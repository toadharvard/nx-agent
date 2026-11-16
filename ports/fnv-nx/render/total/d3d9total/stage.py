#!/usr/bin/env python3
"""The stage of the gate "d3d9total" (render/total/d3d9total/build.json, run step `gate`): the D3D9 -> backend translation is total by construction.
It runs inside the recipe's sandbox (verify/RECIPE.md "Tool steps") with only what the recipe mounts: the programs gcc and clang of the declared env.json debs
(python3, gcc-13, clang-18, libc6-dev, libc6-dev-i386), the tracked trees named by `--tree N=PATH` (copied to PATH of a scratch tree), the pinned
externals of the d3d9 and translator roots (`external_of`: --wine, --mojoshader), and the output of the generator of render/shaders/total (--sm-table, --sm-rows).
    python3 -I -B stage.py --out OUT --gcc GCC --clang CLANG --treehash FILE --wine DIR --mojoshader DIR --sm-table F --sm-rows F --work DIR --tree N=PATH ...
The rule is the gate's (render/total/gate_d3d9total.py reads this stage's output through recipe.resolve_input and writes the gate result):
Universe = every (enumeration, enumerator) of the enumerations the translation consumes by switch (SWITCHED of render/d3d9/env/gen_enums.py, enumerators from the
D9_SPEC the generator bridgefacts/d3d9/d3d9_spec.py writes in this stage, plus the vendor FOURCC of gen_enums.vendor_fourcc over the pinned Wine mirror for
D3DFORMAT), ids "enum:E:N", + every row of the SM table (the SM1-3 commands, (stage, opcode form)), ids "sm:K".
Enum rule. Expected (name, value) lists: for E in SWITCHED + INDEXED the spec's enumerators of E in spec order, with the value clang computes for (long long)(name)
in a probe TU over shim/include/d3d9types.h (-m32, the game's ABI); for D3DFORMAT then the vendor FOURCC codes. The header d3d9_enums.gen.h (the generator's own output
here) must hold, by clang's AST, the enum NxD3D<E without "D3D"> with exactly the enumerators NX_<name> = value of that list, in order. The front sources (front/*.c) must
compile with -Werror=switch -Werror=switch-enum (every switch over an NxD3D type covers every enumerator: no default exists to hide one). Sensitivity, per E of SWITCHED:
the header with one more enumerator NX_<E>_GATEPROBE inserted must give a -Werror=switch or -Werror=switch-enum error located in a front .c source and must compile
without the switch flags. A value of E is certified iff the header matches for all enumerations, the front builds and E is sensitive.
SM rule. The pinned MojoShader tree is the translator root's own external; fnvnx_sm_rows.h is put next to fnvnx_sm_check.h. The translator sources (mojoshader.c, the glsl
profile; the profile set is the -DSUPPORT_PROFILE_* of the translator recipe) must compile: that evaluates the _Static_asserts every row expands to. Liveness, per row
line, by mutation of the macro arguments: OP: opcode value + 1, stage = complement of the entry's type mask, NAME unknown; CMP: control + 1, comparison name unknown;
TOKEN: NAME renamed. Each mutated compile must give an error located on that row's line. K is certified iff the rows header follows the generator's layout, the baseline
compiles and every mutation of every line of K is killed.
Every directory an include of the two recipes names under ports/fnv-nx/ must be one of the mounted trees (a recipe include that is not mounted is red: the stage reads
no include that its key does not hold). Nothing is decided from names, text matching of messages, runs or samples."""
import argparse, ast, hashlib, importlib.machinery, importlib.util, json, os, shutil, stat, subprocess, sys

D3D9_REC, TRANSLATOR_REC = "render/d3d9/build.json", "render/shaders/translator/build.json"
PORT_PFX = "ports/fnv-nx/"
STRICT = ["-Wall", "-Wextra", "-Werror", "-Werror=switch", "-Werror=switch-enum"]
PLAIN = ["-Wall", "-Wextra", "-Werror", "-Wno-switch", "-Wno-switch-enum"]
SM_SOURCES = ["mojoshader.c", os.path.join("profiles", "mojoshader_profile_glsl.c")]
ROWS = "fnvnx_sm_rows.h"
# the wrappers of the mutated row lines: kind -> (wrapper definitions, {row macro: wrapper name})
MUTATIONS = {
    "value": ("#define FNVNX_GATE_OP(id, stage, value, NAME) FNVNX_SM_OP(id, stage, (value) + 1, NAME)\n"
              "#define FNVNX_GATE_CMP(id, NAME, cmp, ctl) FNVNX_SM_CMP(id, NAME, cmp, (ctl) + 1)\n"),
    "stage": "#define FNVNX_GATE_OP(id, stage, value, NAME) FNVNX_SM_OP(id, (~FNVNX_OPTYPE_##NAME), value, NAME)\n",
    "name": ("#define FNVNX_GATE_OP(id, stage, value, NAME) FNVNX_SM_OP(id, stage, value, FNVNX_GATE_NOSUCH_##NAME)\n"
             "#define FNVNX_GATE_CMP(id, NAME, cmp, ctl) FNVNX_SM_CMP(id, NAME, FNVNX_GATE_NOSUCH_##cmp, ctl)\n"),
}
WRAP = {"OP": "FNVNX_GATE_OP", "CMP": "FNVNX_GATE_CMP"}
TOKEN_WRAP = "#define FNVNX_GATE_TOKEN(id, stage, NAME, value) FNVNX_SM_TOKEN(id, stage, FNVNX_GATE_NOSUCH_##NAME, value)\n"


class Red(Exception):
    pass


def run(argv, **kw):
    return subprocess.run(argv, capture_output=True, text=True, **kw)


def read(p):
    with open(p) as f:
        return f.read()


def write(p, text):
    with open(p, "w") as f:
        f.write(text)


def copy_tree(src, dst):
    """copy a directory tree (files with their mode, symlinks as links), names in sorted order"""
    os.makedirs(dst)
    os.chmod(dst, 0o755)
    for name in sorted(os.listdir(src)):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        m = os.lstat(s).st_mode
        if stat.S_ISLNK(m):
            os.symlink(os.readlink(s), d)
        elif stat.S_ISDIR(m):
            copy_tree(s, d)
        else:
            shutil.copyfile(s, d)
            os.chmod(d, stat.S_IMODE(m) | 0o200)


ARGS = None
GCC = CLANG = ""
INCLUDES = TR_INCLUDES = []
SM_CC_BASE = []


def tree_hash(path):
    spec = importlib.util.spec_from_file_location("treehash", ARGS.treehash, loader=importlib.machinery.SourceFileLoader("treehash", ARGS.treehash))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    try:
        return m.tree_hash(path)
    except m.CasError as e:
        raise Red(str(e))


def mount_external(src_dir, dest):
    """the pinned external of the owner root (mounted by the recipe at src_dir, its tree is the owner's pin), copied to a writable place; (tree_sha256 of the copy)"""
    copy_tree(src_dir, dest)
    return tree_hash(dest)


def recipe_includes(inp, path):
    return [i[len(PORT_PFX):] for i in json.load(open(os.path.join(inp, path)))["include"] if i.startswith(PORT_PFX)]


def overlay(src, dst):
    """the files of directory SRC (repo-relative paths under ports/fnv-nx/) placed under DST, mode kept; a path two closures both hold is one file"""
    for d, dirs, files in os.walk(src):
        for f in sorted(files):
            s = os.path.join(d, f)
            rel = os.path.relpath(s, src)
            if not rel.startswith(PORT_PFX):
                raise Red("include closure file outside %s: %s" % (PORT_PFX, rel))
            t = os.path.join(dst, rel[len(PORT_PFX):])
            os.makedirs(os.path.dirname(t), exist_ok=True)
            if not os.path.exists(t):
                shutil.copyfile(s, t)
                os.chmod(t, stat.S_IMODE(os.lstat(s).st_mode) | 0o200)


def build_inp(inp):
    """the mounted trees (--tree N=PATH: /in/N copied to inp/PATH) and the include closures of the d3d9 and translator roots (--include-of DIR: the recipe's own {"include_of": ROOT}
    input, mounted by recipe.py); the -I directories are the two recipes' own `include` lists, so nothing restates them here"""
    global INCLUDES, TR_INCLUDES
    for t in ARGS.tree:
        n, rel = t.split("=", 1)
        s, d = "/in/" + n, os.path.join(inp, rel)
        if os.path.isdir(s):
            copy_tree(s, d)
        else:
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(s, d)
            shutil.copymode(s, d)
    for src in ARGS.include_of:
        overlay(src, inp)
    INCLUDES = recipe_includes(inp, D3D9_REC)
    gen = {os.path.dirname(os.path.dirname(TRANSLATOR_REC) + "/" + o) for g in json.load(open(os.path.join(inp, TRANSLATOR_REC))).get("generated", []) for o in g["outputs"]}
    TR_INCLUDES = [i for i in recipe_includes(inp, TRANSLATOR_REC) if i not in gen]


def ast_decls(argv):
    """the top-level JSON objects clang prints for -ast-dump=json with a filter"""
    r = run(argv)
    if r.returncode:
        raise Red("clang: " + r.stderr[-300:])
    dec, t, i, out = json.JSONDecoder(), r.stdout, 0, []
    while True:
        while i < len(t) and t[i].isspace():
            i += 1
        if i >= len(t):
            return out
        o, i = dec.raw_decode(t, i)
        out.append(o)


def enum_values(decl):
    """[(enumerator, int value)] of a clang EnumDecl"""
    res = []
    for c in decl.get("inner", []):
        if c.get("kind") != "EnumConstantDecl":
            continue
        vals = [x["value"] for x in c.get("inner", []) if x.get("kind") == "ConstantExpr" and "value" in x]
        if len(vals) != 1:
            raise Red("enumerator %s has no computed value" % c.get("name"))
        res.append((c["name"], int(vals[0])))
    return res


def module_lists(path, names):
    """the module-level list literals NAMES of a Python source (ast.literal_eval; nothing is executed)"""
    got = {}
    for st in ast.parse(read(path)).body:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name) and st.targets[0].id in names:
            got[st.targets[0].id] = ast.literal_eval(st.value)
    if sorted(got) != sorted(names) or not all(isinstance(v, list) and all(isinstance(s, str) for s in v) for v in got.values()):
        raise Red("%s: module-level string lists %s not found" % (path, names))
    return got


def diags(stderr):
    """the gcc JSON diagnostics of a run (one array per translation unit)"""
    dec, t, i, out = json.JSONDecoder(), stderr, 0, []
    while True:
        while i < len(t) and t[i].isspace():
            i += 1
        if i >= len(t):
            return out
        if t[i] != "[":
            j = t.find("\n", i)
            i = len(t) if j < 0 else j + 1     # a line that is not JSON (driver text): skipped, the exit status decides
            continue
        o, i = dec.raw_decode(t, i)
        out += o


def locs(d):
    """every (file, line) of a diagnostic: its locations (caret, start, finish) and those of its children (macro expansion notes)"""
    res = []
    for x in [d] + d.get("children", []):
        for loc in x.get("locations", []):
            for k in ("caret", "start", "finish"):
                if k in loc:
                    res.append((os.path.normpath(loc[k].get("file", "")), loc[k].get("line")))
    return res


# ---------------------------------------------------------------- enum part
def front_build(front, incs, flags):
    """compile every front source syntax-only with gcc JSON diagnostics -> (ok, errors [(diag, src)])"""
    ok, errs = True, []
    for f in [x for x in sorted(os.listdir(front)) if x.endswith(".c")]:
        r = run([GCC, "-std=c11", "-fsyntax-only", "-fdiagnostics-format=json", *flags, *[x for i in incs for x in ("-I", i)], "-I", front, os.path.join(front, f)])
        if r.returncode:
            ok = False
        errs += [(d, f) for d in diags(r.stderr) if d.get("kind") == "error"]
    return ok, errs


def vendor_fourcc(inp, work, cache, specp):
    """the vendor FOURCC list of the enum generator: ITS rule (gen_enums.vendor_fourcc over the pinned Wine mirror, the d3d9 root's external wine-d3d9-formats); (list, mirror tree_sha256)"""
    tree = os.path.join(work, "ext", "wine-d3d9-formats")
    h = mount_external(ARGS.wine, tree)
    env = os.path.join(inp, "render", "d3d9", "env")
    sys.path.insert(0, env)
    try:
        import gen_enums
        return gen_enums.vendor_fourcc(tree, specp), h
    finally:
        sys.path.remove(env)


def enum_part(inp, work, cache):
    front, backend = os.path.join(inp, "render", "d3d9", "front"), [os.path.join(inp, i) for i in INCLUDES]
    gen = module_lists(os.path.join(inp, "render", "d3d9", "env", "gen_enums.py"), ["SWITCHED", "INDEXED"])
    os.makedirs(work)
    # the interface table is a build output (bridgefacts D9_SPEC): the gate runs the same generator on the same tree
    specp = os.path.join(work, "d3d9_spec.json")
    r = run([sys.executable, "-E", "-s", "-B", os.path.join(inp, "bridgefacts", "d3d9", "d3d9_spec.py"), specp, os.path.join(work, "d9bridge_rows.tsv"), os.path.join(inp, "shim", "include")])
    if r.returncode:
        raise Red("d3d9_spec.py failed: " + (r.stderr or r.stdout or "")[-400:].replace("\n", " | "))
    gen["VENDOR_FOURCC"], wine_hash = vendor_fourcc(inp, work, cache, specp)
    spec = json.load(open(specp))["enums"]
    names = gen["SWITCHED"] + gen["INDEXED"]
    want_names = {e: list(spec[e]) for e in names}
    want_names["D3DFORMAT"] = want_names["D3DFORMAT"] + ["D3DFMT_" + s.strip() for s in gen["VENDOR_FOURCC"]]
    # the header is a build output (render/d3d9/build.json generated stage): the gate runs the same generator on the same tree
    gdir = os.path.join(work, "gen")
    os.makedirs(gdir)
    hpath = os.path.join(gdir, "d3d9_enums.gen.h")
    if run([sys.executable, "-I", os.path.join(inp, "render", "d3d9", "env", "gen_enums.py"), hpath, specp, os.path.join(work, "ext", "wine-d3d9-formats")]).returncode:
        raise Red("gen_enums.py failed")
    inc = ["-m32", "-ffreestanding", "-I", gdir, "-I", os.path.join(inp, "shim", "include")]
    ginc = [gdir, os.path.join(inp, "shim", "include")]
    M32 = ["-m32", "-ffreestanding"]
    src = ['#include "d3d9types.h"']
    for e in names:
        exprs = ["(long long)(%s)" % n for n in spec[e]]
        if e == "D3DFORMAT":
            exprs += ["(long long)(MAKEFOURCC(%s))" % ", ".join("'%s'" % c for c in s) for s in gen["VENDOR_FOURCC"]]
        src.append("enum FnvnxSpec_%s : long long { %s };" % (e, ", ".join("FnvnxSpec_%s_%d = %s" % (e, i, x) for i, x in enumerate(exprs))))
    write(os.path.join(work, "spec.cpp"), "\n".join(src) + "\n")
    spec_ast = {d["name"]: enum_values(d) for d in ast_decls(
        [CLANG, "-x", "c++", "-m32", "-std=c++17", "-w", "-fsyntax-only", "-I", os.path.join(inp, "shim", "include"),
         "-Xclang", "-ast-dump=json", "-Xclang", "-ast-dump-filter=FnvnxSpec_", os.path.join(work, "spec.cpp")])
        if d.get("kind") == "EnumDecl"}
    want = {}
    for e in names:
        vals = spec_ast.get("FnvnxSpec_" + e, [])
        if [n for n, _ in vals] != ["FnvnxSpec_%s_%d" % (e, i) for i in range(len(want_names[e]))]:
            raise Red("spec probe: enumeration %s not computed" % e)
        want[e] = [("NX_" + n, v) for n, (_, v) in zip(want_names[e], vals)]
    hdecl = {d["name"]: d for d in ast_decls(
        [CLANG, "-x", "c", "-std=c11", "-w", "-fsyntax-only", "-Xclang", "-ast-dump=json", "-Xclang", "-ast-dump-filter=NxD3D", *inc, hpath])
        if d.get("kind") == "EnumDecl" and "name" in d}
    # names and order from the AST; values evaluated by the front's compiler in C: one _Static_assert per enumerator, one line each
    bad = {e for e in names if "NxD3D" + e[3:] not in hdecl or
           [c.get("name") for c in hdecl["NxD3D" + e[3:]].get("inner", []) if c.get("kind") == "EnumConstantDecl"] != [n for n, _ in want[e]]}
    vsrc, vline = ['#include "d3d9_enums.gen.h"'], {}
    for e in names:
        for n, v in want[e]:
            vsrc.append('_Static_assert((long long)(%s) == %dLL, "%s");' % (n, v, n))
            vline[len(vsrc)] = e
    vc = os.path.join(work, "values.c")
    write(vc, "\n".join(vsrc) + "\n")
    r = run([GCC, "-std=c11", "-fsyntax-only", "-w", "-fmax-errors=0", "-fdiagnostics-format=json", *inc, "-I", front, vc])
    verr = [d for d in diags(r.stderr) if d.get("kind") == "error"]
    bad |= {vline.get(ln, "?") for d in verr for f, ln in locs(d) if f == os.path.normpath(vc)}
    if r.returncode and not verr:
        bad.add("?")
    header_bad = sorted(bad)
    built, berrs = front_build(front, backend + ginc, STRICT + M32)
    htext = open(hpath, "rb").read()
    sens = {}
    for e in gen["SWITCHED"]:
        d = hdecl.get("NxD3D" + e[3:])
        if d is None:
            sens[e] = {"located_in_front": False, "compiles_without": False}
            continue
        used = {v for _, v in want[e]}
        extra = next(v for v in range(0x40000000, 0x40000000 + len(used) + 1) if v not in used)
        off = d["range"]["end"]["offset"]
        if htext[off:off + 1] != b"}":
            raise Red("NxD3D%s: AST range does not end at '}'" % e[3:])
        pdir = os.path.join(work, "probe_" + e)
        copy_tree(front, pdir)
        pgen = os.path.join(pdir, "gen")
        os.makedirs(pgen)
        pinc = [pgen, os.path.join(inp, "shim", "include")]
        with open(os.path.join(pgen, "d3d9_enums.gen.h"), "wb") as f:
            f.write(htext[:off] + b"NX_%s_GATEPROBE = %d, " % (e.encode(), extra) + htext[off:])
        _, perrs = front_build(pdir, backend + pinc, STRICT + M32)
        here = [(dg, f) for dg, f in perrs if dg.get("option") in ("-Werror=switch", "-Werror=switch-enum") and
                any(fl == os.path.normpath(os.path.join(pdir, f)) for fl, _ in locs(dg))]
        sens[e] = {"located_in_front": bool(here), "compiles_without": front_build(pdir, backend + pinc, PLAIN + M32)[0]}
    universe, uncert = [], []
    for e in gen["SWITCHED"]:
        good = not header_bad and built and sens[e]["located_in_front"] and sens[e]["compiles_without"]
        for n in want_names[e]:
            universe.append("enum:%s:%s" % (e, n))
            if not good:
                uncert.append("enum:%s:%s" % (e, n))
    info = {"header_mismatch": header_bad, "front_builds": built, "front_errors": [d.get("message", "") for d, _ in berrs][:5],
            "sensitivity": sens, "wine_formats_tree_sha256": wine_hash}
    return universe, uncert, info


# ---------------------------------------------------------------- SM part
def row_layout(ops):
    """[(line number, kind, K)] of fnvnx_sm_rows.h by the generator's layout"""
    out, ln = [], 1
    for k in sorted(ops):
        ln += 1
        out.append((ln, "TOKEN" if ops[k]["opnum"] >= 0xFFF0 else "OP", k))
        if out[-1][1] == "OP" and "cmp" in ops[k]:
            ln += 1
            out.append((ln, "CMP", k))
    return out


def sm_incs(inp, tree):
    """-I list of the translator recipe's include list: /ext/mojoshader[/sub] is the fetched tree, a tracked directory is the gate's copy of it"""
    out = []
    for i in json.load(open(os.path.join(inp, TRANSLATOR_REC)))["include"]:
        if i == "/ext/mojoshader" or i.startswith("/ext/mojoshader/"):
            out.append(tree + i[len("/ext/mojoshader"):])
        elif i.startswith(PORT_PFX):
            out.append(os.path.join(inp, i[len(PORT_PFX):]))
    return out


def sm_compile(tree, defs, extra=(), incs=()):
    """-> (ok, error diagnostics) of the translator sources"""
    ok, errs = True, []
    for s in SM_SOURCES:
        r = run(SM_CC_BASE + defs + list(extra) + ["-I", tree, "-I", os.path.join(tree, "profiles")] + [x for i in incs for x in ("-I", i)] + [os.path.join(tree, s)])
        ok = ok and r.returncode == 0
        errs += [d for d in diags(r.stderr) if d.get("kind") == "error"]
    return ok, errs


def sm_part(inp, work, cache):
    try:
        ops = json.load(open(ARGS.sm_table))["opcodes"]
        rows_text = read(ARGS.sm_rows)
    except (ValueError, OSError, KeyError) as x:
        return [], [], {"error": "render/shaders/total generator output: " + str(x)[:300], "killed": {}}
    universe = ["sm:" + k for k in sorted(ops)]
    info = {"baseline_compiles": False, "layout": False, "killed": {}, "error": ""}
    os.makedirs(work)
    try:
        exts = [x for x in json.load(open(os.path.join(inp, TRANSLATOR_REC)))["external"] if x.get("name") == "mojoshader"]
        if len(exts) != 1 or exts[0].get("repo") != "fnv-nx-build":
            raise Red("%s: exactly one external mojoshader of fnv-nx-build is required" % TRANSLATOR_REC)
        defs = [f for f in json.load(open(os.path.join(inp, TRANSLATOR_REC)))["flags"]["c"] if f.startswith("-DSUPPORT_PROFILE_")]   # the profile set of the translator recipe
        tree = os.path.join(work, "ext", "mojoshader")
        info["tree_sha256"] = mount_external(ARGS.mojoshader, tree)
        info["commit"] = exts[0]["commit"]
        rows = os.path.join(tree, ROWS)
        write(rows, rows_text)
        write(os.path.join(tree, "mojoshader_version.h"), '#define MOJOSHADER_VERSION -1\n#define MOJOSHADER_CHANGESET "gate"\n')  # cmake-generated
        incs = sm_incs(inp, tree)
        base_ok, base_errs = sm_compile(tree, defs, (), incs)
        info["baseline_compiles"] = base_ok
        if not base_ok:
            raise Red("patched translator does not compile: " + "; ".join(d.get("message", "") for d in base_errs[:3]))
        lines = rows_text.split("\n")
        layout = row_layout(ops)
        info["layout"] = len(lines) == len(layout) + 2 and lines[-1] == "" and all(
            lines[ln - 1].startswith('FNVNX_SM_%s("%s", ' % (kind, k)) for ln, kind, k in layout)
        if not info["layout"]:
            raise Red("fnvnx_sm_rows.h does not follow the generator's layout")
        need = {}          # line -> set of mutation kinds that must be killed there
        for ln, kind, k in layout:
            need[ln] = ["value", "stage", "name"] if kind == "OP" else ["value", "name"] if kind == "CMP" else ["token"]
        killed = {ln: [] for ln, _, _ in layout}
        mutated = "\n".join(WRAP[kind] + lines[ln - 1][len("FNVNX_SM_" + kind):] if kind in WRAP else lines[ln - 1]
                            for ln, kind, _ in [(1, "", "")] + layout) + "\n"
        for m in sorted(MUTATIONS):
            inc = os.path.join(work, "mut_%s.h" % m)
            write(inc, MUTATIONS[m])
            write(rows, mutated)
            _, errs = sm_compile(tree, defs, ["-include", inc], incs)
            hit = {line for d in errs for f, line in locs(d) if f == os.path.normpath(rows)}
            for ln in sorted(hit):
                if ln in killed and m in need[ln]:
                    killed[ln].append(m)
        inc = os.path.join(work, "mut_token.h")
        write(inc, TOKEN_WRAP)
        for ln, kind, _ in layout:
            if kind == "TOKEN":
                write(rows, "\n".join("FNVNX_GATE_TOKEN" + l[len("FNVNX_SM_TOKEN"):] if i == ln else l
                                      for i, l in enumerate(lines, 1)))
                if not sm_compile(tree, defs, ["-include", inc], incs)[0]:
                    killed[ln].append("token")
        write(rows, rows_text)
        ok_rows = {k for k in ops}
        for ln, kind, k in layout:
            if sorted(killed[ln]) != sorted(need[ln]):
                ok_rows.discard(k)
                info["killed"]["%s:%d" % (kind, ln)] = {"row": k, "killed": sorted(killed[ln]), "need": sorted(need[ln])}
    except Red as x:
        info["error"] = str(x)
        ok_rows = set()
    return universe, [u for u in universe if u[3:] not in ok_rows], info


def main():
    """The stage ends in a result file with reasons whatever happens: an exception of any part is a red result that names it, never an exit without a file."""
    global ARGS, GCC, CLANG, SM_CC_BASE
    ap = argparse.ArgumentParser()
    for a in ("out", "gcc", "clang", "treehash", "wine", "mojoshader", "sm-table", "sm-rows", "work"):
        ap.add_argument("--" + a, required=True)
    ap.add_argument("--tree", action="append", required=True)
    ap.add_argument("--include-of", action="append", required=True)
    ARGS = ap.parse_args()
    GCC, CLANG = ARGS.gcc, ARGS.clang
    SM_CC_BASE = [GCC, "-fsyntax-only", "-Wall", "-fmax-errors=0", "-fdiagnostics-format=json"]
    try:
        return evaluate()
    except Exception:   # noqa: BLE001  the total rule: every failure is a finding
        import traceback
        write(ARGS.out, json.dumps({
            "format": "fnvnx-gate/1", "name": "d3d9total", "status": "red", "universe": 0, "certified": 0, "uncertified": [],
            "detail": ("stage crashed: " + " | ".join(traceback.format_exc().strip().splitlines()[-6:]))[:900]}, sort_keys=True, indent=1) + "\n")
        return 0


def evaluate():
    work = ARGS.work
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    inp = os.path.join(work, "in")
    universe, uncert, err = [], [], ""
    try:
        build_inp(inp)
        eu, ec, einfo = enum_part(inp, os.path.join(work, "enum"), None)
    except Red as x:
        err, eu, ec, einfo = str(x), [], [], {}
    su, sc, sinfo = sm_part(inp, os.path.join(work, "sm"), None) if not err else ([], [], {})
    universe, uncert = eu + su, ec + sc
    parts = {"enums": {"universe": len(eu), "certified": len(eu) - len(ec), "info": einfo},
             "sm": {"universe": len(su), "certified": len(su) - len(sc), "info": sinfo}}
    green = not err and universe and not uncert
    res = {"format": "fnvnx-gate/1", "name": "d3d9total", "status": "green" if green else "red", "universe": len(universe),
           "certified": len(universe) - len(uncert), "uncertified": sorted(uncert)[:50],
           "detail": "enum values %d of %d certified (header mismatch %s, front builds %s, sensitive %d of %d enumerations); "
                     "SM commands %d of %d certified (external mojoshader %s, rows header generated %s, translator compiles %s, "
                     "mutations not killed %d)%s"
                     % (len(eu) - len(ec), len(eu), einfo.get("header_mismatch"), einfo.get("front_builds"),
                        sum(1 for s in einfo.get("sensitivity", {}).values() if s["located_in_front"] and s["compiles_without"]),
                        len(einfo.get("sensitivity", {})), len(su) - len(sc), len(su), sinfo.get("tree_sha256"),
                        sinfo.get("rows_header_equals_generated"), sinfo.get("baseline_compiles"), len(sinfo.get("killed", {})),
                        ("; error: " + (err or sinfo.get("error", "")))[:400] if (err or sinfo.get("error")) else ""),
           "parts": parts}
    write(ARGS.out, json.dumps(res, sort_keys=True, indent=1) + "\n")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
