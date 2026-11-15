#!/usr/bin/env python3
"""Gate "d3d9total" (fnvnx-gate/1, VERDICT class 7): the D3D9 -> backend translation is total by construction.
    GATE_OUT=<dir> [GATE_CACHE=<dir>] [ARTIFACTS_URL=<git url>] python3 -I ports/fnv-nx/render/total/gate_d3d9total.py
Run in the checkout root (verify/verdict.py does). Writes GATE_OUT/d3d9total.json; exit 0 iff green, 2 red.
Universe = every (enumeration, enumerator) of the enumerations the translation consumes by switch (SWITCHED of
render/d3d9/front/gen_enums.py, enumerators from the interface table that bridgefacts/d3d9/d3d9_spec.py generates plus VENDOR_FOURCC for D3DFORMAT), ids "enum:E:N",
+ every row of the table the total root generates (render/shaders/total, sm_table.json; the SM1-3 commands, (stage, opcode form)), ids "sm:K".

Declared inputs (nothing else is read): the tracked files (git ls-files) of INPUTS below (the d3d9 and translator recipes and the d3d9 root's include lists), copied into GATE_OUT/d3d9total.work;
the external "mojoshader" of the translator root's recipe (render/shaders/translator/build.json: fnv-nx-build commit + tree_sha256, fetched by exact commit from
ARTIFACTS_URL, default the fnv-nx-build repository, into GATE_CACHE/external/<commit> or the work dir; its tree hash, the hash of
verify/cas.py, must equal tree_sha256); the programs /usr/bin/gcc, /usr/bin/clang, git of verify/env.json (absolute paths, compile only:
nothing is linked or run) with the C headers of the pinned /usr/include tree and clang's resource headers.

Enum rule. Expected (name, value) lists: for E in SWITCHED + INDEXED the spec's enumerators of E in spec order, with the value clang
computes for (long long)(name) in a probe TU over shim/include/d3d9types.h (-m32, the game's ABI); for D3DFORMAT then the VENDOR_FOURCC
codes s as "D3DFMT_"+s.strip() = MAKEFOURCC of its four characters (the shim's macro). The committed header d3d9_enums.gen.h must hold,
by clang's AST, the enum NxD3D<E without "D3D"> with exactly the enumerators NX_<name> = value of that list, in order. The front sources
(front/*.c) must compile with -Werror=switch -Werror=switch-enum (every switch over an NxD3D type covers every enumerator: no default
exists to hide one). Sensitivity, per E of SWITCHED: the header with one more enumerator NX_<E>_GATEPROBE (first value >= 0x40000000 not
used by E) inserted at the end of the AST range of NxD3D<E> must give a -Werror=switch or -Werror=switch-enum error located in a front .c source (so a switch of the
front over E exists) and must compile without the switch flags (so that is the only error). A value of E is certified iff the header
matches for all enumerations, the front builds and E is sensitive.

SM rule. The external is the translator root's own pinned MojoShader tree (the patched mirror; the pin is read from the recipe, nothing
is applied or restated here); fnvnx_sm_rows.h (the total root's generate stage output, run here over the same inputs and the pinned mirrors) is put next to fnvnx_sm_check.h.
The translator sources (mojoshader.c, the glsl profile; the profile set is the -DSUPPORT_PROFILE_* of the translator recipe) must compile: that evaluates
the _Static_asserts every row expands to. The row of command K is located by the generator's layout (one comment line, then per K in
sorted order its FNVNX_SM_OP or FNVNX_SM_TOKEN line, then its FNVNX_SM_CMP line if it has a comparison); each line is checked to start
with that macro and id. Liveness, per row line, by mutation of the macro arguments (the row text stays, the leading macro name is renamed
to a wrapper of a -include file): OP: opcode value + 1, stage = complement of the entry's type mask, NAME unknown; CMP: control + 1,
comparison name unknown. Each mutated compile must give an error located (caret or macro-expansion location) on that row's line. TOKEN:
NAME renamed must make mojoshader.c fail (all other rows unchanged, the baseline compiles, so the token is consumed). K is certified iff
the rows header is generated, the baseline compiles and every mutation of every line of K is killed.
Nothing is decided from names, text matching of messages, runs or samples. Result extras: "parts"."""
import ast, hashlib, json, os, shutil, stat, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, "..", ".."))
REPO = os.path.normpath(os.path.join(PORT, "..", ".."))
PORT_REL = os.path.relpath(PORT, REPO)
D3D9_REC, TRANSLATOR_REC = "render/d3d9/build.json", "render/shaders/translator/build.json"
TOTAL_DIR = "render/shaders/total"            # the SM table root: its recipe names the generate stage whose outputs are the table and the rows header
TOTAL_REC = TOTAL_DIR + "/build.json"
PORT_PFX = "ports/fnv-nx/"


def recipe_includes(path):
    """the port-relative include directories of a root's own recipe (its `include` list; /ext entries are externals, not inputs)"""
    return [i[len(PORT_PFX):] for i in json.load(open(os.path.join(PORT, path)))["include"] if i.startswith(PORT_PFX)]


# the inputs are the roots' own records: the d3d9 root's recipe and its include lists, the translator root's recipe (its mojoshader external)
INCLUDES = recipe_includes(D3D9_REC)
TR_GEN = {os.path.dirname(os.path.dirname(TRANSLATOR_REC) + "/" + o) for g in json.load(open(os.path.join(PORT, TRANSLATOR_REC))).get("generated", []) for o in g["outputs"]}   # build outputs: no tracked files
TR_INCLUDES = [i for i in recipe_includes(TRANSLATOR_REC) if i not in TR_GEN]   # the translator's own tracked include dirs (the patched MojoShader includes fnvnx_dk_layout.h from them)
INPUTS = ["render/d3d9/front", "bridgefacts/d3d9", D3D9_REC, TRANSLATOR_REC,
          TOTAL_DIR, "contract/treehash.py"] + INCLUDES + [i for i in TR_INCLUDES if i not in INCLUDES]
GCC, CLANG, GIT = "/usr/bin/gcc", "/usr/bin/clang", "/usr/bin/git"
ARTIFACTS_URL = "https://github.com/toadharvard/fnv-nx-build"
STRICT = ["-Wall", "-Wextra", "-Werror", "-Werror=switch", "-Werror=switch-enum"]
PLAIN = ["-Wall", "-Wextra", "-Werror", "-Wno-switch", "-Wno-switch-enum"]
SM_CC_BASE = [GCC, "-fsyntax-only", "-Wall", "-fmax-errors=0", "-fdiagnostics-format=json"]
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


def git_env(extra=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    env.update(extra or {})
    return env


def read(p):
    with open(p) as f:
        return f.read()


def write(p, text):
    with open(p, "w") as f:
        f.write(text)


def copy_tree(src, dst):
    """copy a directory tree (files with their mode, symlinks as links), names in sorted order"""
    os.makedirs(dst)
    for name in sorted(os.listdir(src)):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        m = os.lstat(s).st_mode
        if stat.S_ISLNK(m):
            os.symlink(os.readlink(s), d)
        elif stat.S_ISDIR(m):
            copy_tree(s, d)
        else:
            shutil.copyfile(s, d)
            shutil.copymode(s, d)


# ---------------------------------------------------------------- tree hash (the one home: contract/treehash.py; a test cross-checks it with cas)
sys.path.append(os.path.join(PORT, "contract"))
import treehash  # noqa: E402


def tree_hash(path):
    try:
        return treehash.tree_hash(path)
    except treehash.CasError as e:
        raise Red(str(e))


# ---------------------------------------------------------------- declared inputs
def materialize(dest):
    """copy the tracked files of INPUTS into DEST (paths relative to the port); a declared input without files is red"""
    r = run([GIT, "ls-files", "-z", "--"] + [PORT_REL + "/" + p for p in INPUTS], cwd=REPO, env=git_env())
    if r.returncode:
        raise Red("git ls-files: " + r.stderr[-200:])
    files = sorted(f for f in r.stdout.split("\0") if f)
    rel = [os.path.relpath(f, PORT_REL) for f in files]
    missing = [p for p in INPUTS if not any(x == p or x.startswith(p + "/") for x in rel)]
    if missing:
        raise Red("declared inputs without tracked files: %s" % missing)
    for f, x in zip(files, rel):
        d = os.path.join(dest, x)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, f), d)
        shutil.copymode(os.path.join(REPO, f), d)
    return rel


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


def enum_part(inp, work):
    front, backend = os.path.join(inp, "render", "d3d9", "front"), [os.path.join(inp, i) for i in INCLUDES]
    gen = module_lists(os.path.join(front, "gen_enums.py"), ["SWITCHED", "INDEXED", "VENDOR_FOURCC"])
    os.makedirs(work)
    # the interface table is a build output (bridgefacts D9_SPEC): the gate runs the same generator on the same tree
    specp = os.path.join(work, "d3d9_spec.json")
    if run([sys.executable, "-I", os.path.join(inp, "bridgefacts", "d3d9", "d3d9_spec.py"), specp, os.path.join(work, "d9bridge_rows.tsv"), os.path.join(inp, "shim", "include")]).returncode:
        raise Red("d3d9_spec.py failed")
    spec = json.load(open(specp))["enums"]
    names = gen["SWITCHED"] + gen["INDEXED"]
    want_names = {e: list(spec[e]) for e in names}
    want_names["D3DFORMAT"] = want_names["D3DFORMAT"] + ["D3DFMT_" + s.strip() for s in gen["VENDOR_FOURCC"]]
    # the header is a build output (render/d3d9/build.json generated stage): the gate runs the same generator on the same tree
    gdir = os.path.join(work, "gen")
    os.makedirs(gdir)
    hpath = os.path.join(gdir, "d3d9_enums.gen.h")
    if run([sys.executable, "-I", os.path.join(front, "gen_enums.py"), hpath, specp]).returncode:
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
            "sensitivity": sens}
    return universe, uncert, info


# ---------------------------------------------------------------- SM part
def fetch_external(e, cache, dest, scratch):
    """the recipe form: bare EXTCACHE/<commit> fetched by exact commit, checked out by read-tree + checkout-index"""
    bare = os.path.join(cache, e["commit"])
    url = os.environ.get("ARTIFACTS_URL", ARTIFACTS_URL)
    if not os.path.isdir(bare):
        tmp = bare + ".tmp"
        if os.path.isdir(tmp):
            shutil.rmtree(tmp)
        os.makedirs(tmp)
        for cmd in (["init", "-q", "--bare"], ["fetch", "-q", "--depth", "1", url, e["commit"]]):
            r = run([GIT, "-C", tmp] + cmd, env=git_env())
            if r.returncode:
                raise Red("external %s: git %s: %s" % (e["name"], cmd[0], r.stderr[-200:]))
        os.replace(tmp, bare)
    os.makedirs(dest)
    ienv = git_env({"GIT_INDEX_FILE": os.path.join(scratch, "idx-" + e["name"])})
    for cmd in (["--git-dir", bare, "read-tree", e["commit"]], ["--git-dir", bare, "--work-tree", dest, "checkout-index", "-a", "-f"]):
        r = run([GIT] + cmd, env=ienv)
        if r.returncode:
            raise Red("external %s: git %s: %s" % (e["name"], cmd[2], r.stderr[-200:]))
    h = tree_hash(dest)
    if h != e["tree_sha256"]:
        raise Red("external %s: tree_sha256 is %s, the recipe says %s" % (e["name"], h, e["tree_sha256"]))
    return h


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


def total_outputs(inp, work, cache):
    """run the total root's generate stage (the recipe's own record: tool, args and externals of TOTAL_REC) over the copied inputs and the pinned
    mirrors -> {output file name: path}. The table and the rows header exist only as this stage's outputs."""
    rec = json.load(open(os.path.join(inp, TOTAL_REC)))
    (st,) = [g for g in rec["generated"] if g["tool"].endswith("/sm_table.py")]
    exts = {e["at"]: e for e in rec["external"]}
    gen = os.path.join(work, "gen")
    os.makedirs(gen)
    args = []
    for a in st["args"]:
        if a.startswith("/ext/"):
            if a not in exts:
                raise Red("%s: the generate stage names %s, which is no external of the root" % (TOTAL_REC, a))
            tree = os.path.join(work, "ext" + a[4:].replace("/", "_"))
            fetch_external(exts[a], cache, tree, work)
            a = tree
        elif a.startswith("/in/" + PORT_PFX):
            a = os.path.join(inp, a[len("/in/" + PORT_PFX):])
        elif a == "/out/gen":
            a = gen
        args.append(a)
    r = run([sys.executable, "-I", os.path.join(inp, st["tool"][len(PORT_PFX):])] + args)
    if r.returncode:
        raise Red("%s: the generate stage is red: %s" % (TOTAL_REC, (r.stdout + r.stderr)[-300:]))
    return {os.path.basename(o): os.path.join(work, o) for o in st["outputs"]}


def sm_part(inp, work, cache):
    info = {"baseline_compiles": False, "layout": False, "killed": {}, "error": ""}
    os.makedirs(work)
    ops, universe = {}, []
    try:
        gen = total_outputs(inp, os.path.join(work, "total"), cache)
        ops = json.load(open(gen["sm_table.json"]))["opcodes"]
        universe = ["sm:" + k for k in sorted(ops)]
        exts = [x for x in json.load(open(os.path.join(inp, TRANSLATOR_REC)))["external"] if x.get("name") == "mojoshader"]
        if len(exts) != 1 or exts[0].get("repo") != "fnv-nx-build":
            raise Red("%s: exactly one external mojoshader of fnv-nx-build is required" % TRANSLATOR_REC)
        defs = [f for f in json.load(open(os.path.join(inp, TRANSLATOR_REC)))["flags"]["c"] if f.startswith("-DSUPPORT_PROFILE_")]   # the profile set of the translator recipe
        tree = os.path.join(work, "ext", "mojoshader")
        info["tree_sha256"] = fetch_external(exts[0], cache, tree, work)
        info["commit"] = exts[0]["commit"]
        rows_text = read(gen["fnvnx_sm_rows.h"])
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
    out = os.environ["GATE_OUT"]
    work = os.path.join(out, "d3d9total.work")
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work)
    cache = os.path.join(os.environ["GATE_CACHE"], "external") if os.environ.get("GATE_CACHE") else os.path.join(work, "external")
    os.makedirs(cache, exist_ok=True)
    inp = os.path.join(work, "in")
    parts, universe, uncert, err = {}, [], [], ""
    try:
        materialize(inp)
        eu, ec, einfo = enum_part(inp, os.path.join(work, "enum"))
    except Red as x:
        err, eu, ec, einfo = str(x), [], [], {}
    su, sc, sinfo = sm_part(inp, os.path.join(work, "sm"), cache) if not err else ([], [], {})
    universe, uncert = eu + su, ec + sc
    parts = {"enums": {"universe": len(eu), "certified": len(eu) - len(ec), "info": einfo},
             "sm": {"universe": len(su), "certified": len(su) - len(sc), "info": sinfo}}
    green = not err and not sinfo.get("error") and universe and not uncert
    res = {"format": "fnvnx-gate/1", "name": "d3d9total", "status": "green" if green else "red", "universe": len(universe),
           "certified": len(universe) - len(uncert), "uncertified": sorted(uncert)[:50],
           "detail": "enum values %d of %d certified (header mismatch %s, front builds %s, sensitive %d of %d enumerations); "
                     "SM commands %d of %d certified (external mojoshader %s, rows header from the generate stage, translator compiles %s, "
                     "mutations not killed %d)%s"
                     % (len(eu) - len(ec), len(eu), einfo.get("header_mismatch"), einfo.get("front_builds"),
                        sum(1 for s in einfo.get("sensitivity", {}).values() if s["located_in_front"] and s["compiles_without"]),
                        len(einfo.get("sensitivity", {})), len(su) - len(sc), len(su), sinfo.get("tree_sha256"),
                        sinfo.get("baseline_compiles"), len(sinfo.get("killed", {})),
                        ("; error: " + (err or sinfo.get("error", "")))[:400] if (err or sinfo.get("error")) else ""),
           "parts": parts}
    write(os.path.join(out, "d3d9total.json"), json.dumps(res, sort_keys=True, indent=1) + "\n")
    shutil.rmtree(work)
    return 0 if green else 2


if __name__ == "__main__":
    sys.exit(main())
