#!/usr/bin/env python3
"""recipe: build a product root from its recipe `build.json` ("fnvnx-recipe/1", contract in RECIPE.md) on the stage machinery:
every step is a sandbox stage (sandbox.py) keyed by its declared inputs, cached in the store (cas.py).
    python3 -I recipe.py build --root ports/fnv-nx/X --store S --out DIR [--jobs N]     (cwd = checkout root; exit 0 green, 2 red)
    python3 -I recipe.py check --root ports/fnv-nx/X                                    validate only
Steps: (1) generated stages (python runtime, runtime.py), (2) one unit stage per source: .c/.cc/.cpp/.cxx = unitstage.Build (PP on
name-complete views, then CC on the preprocessed text), .s/.S = unitstage.Build too (.S: PP then CC; .s: one stage on its own file), (3) the link stage
(`ld` of the compiler, objects + script as inputs). Tools (compilers, ld) are runtime.py `tool` runtimes: declared inputs, no host /usr.
RULE units: every tracked *.c *.cc *.cpp *.cxx *.s *.S under the root, and every generated output with such a name, is a unit.
RULE refusal set R: an option of GNU ld's grammar that lets a link accept missing or substituted symbols. Matched by OPTION SYNTAX:
  strip one or two leading dashes, cut at `=`; a name that is a prefix (getopt_long abbreviation) of a refused long name is refused (a
  single-dash name needs >= 2 chars); `-z undefs|muldefs` as `-z X` or `-zX`; `-Wl,a,b` and `-Xlinker a` are split into the options
  they pass; `@file` is refused (cannot be analysed). An ambiguous abbreviation is refused too (ld would reject it anyway).
EXTERNAL inputs (key "external", RECIPE.md): fetched only by exact commit from ARTIFACTS_URL into a bare cache (--ext DIR, shared by
  builds that must not share stores), checked out, ingested; the CAS tree hash must equal tree_sha256 (else the build is red naming
  the external). The tree is a declared input at "at" of EVERY stage (the tool runtime lists it), so stage keys cover its hash. A tree
  is the content-pinned INPUT, not a build output: sharing the fetch between two cold builds shares nothing that a build computes.
  compiler {"external": N, cc, cxx}: the tree is declared to runtime.py tool as HOST@AT (loader closure resolved as inside the sandbox, RECIPE.md).
Outputs in --out: objects.json ("fnvnx-objects/1": root, objects[{unit,object,sha256,os_layer}], elf, constants, defined [{symbol,bind,type,object}] and undefined [symbol] (ELF .symtab of the objects, struct), stage_keys, stages
  {run,hit}, status green|red, failure {stage,exit,stderr tail 20} / failed_units when red), obj/<unit>.o, <name>.elf, constants.tsv.
Assumptions: the compiler and ld are deterministic given the flags of unitstage/toolflags (A-env); the sandbox leaves the channels of
  sandbox.OPEN_CHANNELS open (see gate_determinism.py for which of them two cold builds expose)."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "link"))
import relocs  # noqa: E402
import ldcheck  # noqa: E402
import runtime  # noqa: E402
import sandbox  # noqa: E402
import toolflags  # noqa: E402
import unitstage  # noqa: E402

FORMAT, OBJ_FORMAT = "fnvnx-recipe/1", "fnvnx-objects/1"
TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
KEYS = {"format", "compiler", "flags", "include", "units", "generated", "os_layer", "game", "constants", "contract_names", "link", "external",
        "plugins", "plugin_cxx", "variants"}
VARIANT_KEYS = {"compiler", "flags"}   # the keys a variant may replace
PLUGIN_KEYS = {"name", "source", "args"}   # and optionally "inputs": [tracked files the source includes]
GUEST_PIN = TREE + "/verify/guest_compiler.json"   # the game's compiler: {"format", "external": {EXT_KEYS}, "cc", "cxx"} (RECIPE.md, "Guest roots")
GUEST_TARGETS = ("guest", "host")
# The plugins every product root with native or decomp C++ loads, their only admitted sources and their fixed arguments (RECIPE.md,
# "Plugins"). The arguments are the MSVC semantics (evaluation order of arguments, objects, operators, assignments; enum conversion
# an error as for cl). The other values the plugins accept (ltr, first, cpp17) exist for A/B measurements and are no product.
# The plugin fnvnx_order has no memory-ordering option: every access to shared memory is ordered (x86 TSO), whatever the arguments.
# The set and the LOAD ORDER of compile.sh (fnv-l2-source): ftoi, order, sdd, pi, dispatch, addr, names, layout. A plugin that includes another source
# (layout includes names' .cc) lists it in PLUGIN_INPUTS; the stage mounts the sources at their repo paths.
_T = TREE + "/toolchain/"
PLUGIN_SOURCES = {"fnvnx_ftoi": _T + "ftoi/fnvnx_ftoi.cc", "fnvnx_order": _T + "order/fnvnx_order.cc", "fnvnx_sdd": _T + "sdd/fnvnx_sdd.cc",
                  "fnvnx_pi": _T + "pi/fnvnx_pi.cc", "fnvnx_dispatch": _T + "dispatch/fnvnx_dispatch.cc", "fnvnx_addr": _T + "addr/fnvnx_addr.cc",
                  "fnvnx_names": _T + "names/fnvnx_names.cc", "fnvnx_layout": _T + "layout/fnvnx_layout.cc"}
PLUGIN_INPUTS = {"fnvnx_layout": [_T + "names/fnvnx_names.cc"]}
PLUGIN_ARGS = {n: {} for n in PLUGIN_SOURCES}
PLUGIN_ARGS["fnvnx_order"] = {"args": "rtl", "object": "msvc", "operator": "rtl", "assign": "msvc"}
# Flags of the game's C++ that fix its semantics (compile.sh); fnvnx_sdd needs -DFNVNX_SDD=1 and -fno-devirtualize; fnvnx_addr needs no .cold sections: -fno-reorder-blocks-and-partition.
SEMANTICS_FLAGS = ("-mabi=ilp32", "-fms-extensions", "-fshort-wchar", "-frounding-math", "-fsigned-char", "-fno-strict-aliasing", "-fwrapv",
                   "-fno-delete-null-pointer-checks", "-fcheck-new", "-fno-sized-deallocation", "-fno-lifetime-dse", "-ffp-contract=off",
                   "-DFNVNX_SDD=1", "-fno-devirtualize", "-fno-reorder-blocks-and-partition")
# The options of every link, fixed by the rule (RECIPE.md "Link"): the specs of the gcc driver for the ILP32 PIE multilib, no gc-sections, no id.
LD_FIXED = ["--build-id=none", "-pie", "--no-dynamic-linker", "-z", "text", "-z", "now", "-z", "pack-relative-relocs", "-z", "max-page-size=0x1000",
            "-z", "common-page-size=0x1000"]
LD_KIND = {"pie": LD_FIXED, "exe": ["--build-id=none", "-pie", "-z", "text", "-z", "now"], "shared": ["--build-id=none", "-shared", "-z", "text", "-z", "now"]}
LD_TOOLS = (TREE + "/link", TREE + "/contract")   # tool roots of the ldcheck stage: link/ldcheck.py, and gatefile.py at /contract
EXT_KEYS = {"name", "repo", "ref", "commit", "tree_sha256", "at"}
SANDBOX_PATHS = ("/src", "/inc", "/in", "/out", "/tool", "/objs", "/ld", "/syslib", "/linked", "/contract", "/work", "/proc", "/dev", "/tmp", "/oldroot")
STYPE = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 5: "COMMON", 6: "TLS", 10: "IFUNC"}
SBIND = {1: "GLOBAL", 2: "WEAK", 10: "UNIQUE"}
ARTIFACTS_URL = "https://github.com/toadharvard/fnv-nx-build"
CC_EXT, CXX_EXT, ASM_EXT = (".c",), (".cc", ".cpp", ".cxx"), (".s", ".S")
LONG_R = ("unresolved-symbols", "allow-shlib-undefined", "noinhibit-exec", "defsym", "wrap", "allow-multiple-definition",
          "warn-unresolved-symbols")


class RecipeError(Exception):
    def __init__(self, errors):
        Exception.__init__(self, "; ".join(errors))
        self.errors = list(errors)


class BuildRed(Exception):
    pass


def refused(tokens):
    """Options of the refusal set R in TOKENS (see the module docstring for the grammar)."""
    toks, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("-Wl,"):
            toks += t[4:].split(",")
        elif t == "-Xlinker" and i + 1 < len(tokens):
            i += 1
            toks.append(tokens[i])
        else:
            toks.append(t)
        i += 1
    bad = []
    for i, t in enumerate(toks):
        name = t.lstrip("-").split("=", 1)[0] if t.startswith("-") else ""
        long_ = t.startswith("--") or len(name) >= 2
        hit = t.startswith("@") or (name and long_ and any(r.startswith(name) for r in LONG_R)) or (
            t == "-z" and toks[i + 1:i + 2] in (["undefs"], ["muldefs"])) or t in ("-zundefs", "-zmuldefs")
        if hit:
            bad.append(t)
    return bad


def is_unit(p):
    return p.endswith(CC_EXT + CXX_EXT + ASM_EXT)


def rel_ok(p):
    return isinstance(p, str) and p != "" and not p.startswith("/") and posixpath.normpath(p) == p and p != ".." \
        and not p.startswith("../")


def under(p, d):
    return p == d or p.startswith(d + "/")


def load_admit(repo="."):
    with open(os.path.join(repo, ADMIT)) as f:
        return {r["path"]: r for r in json.load(f)["roots"]}


def tracked(repo="."):
    out = subprocess.run(["git", "-C", repo, "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    return sorted(p for p in out.decode("utf-8", "surrogateescape").split("\0") if p)


def split_spec(spec):
    """'root' -> (root, ''); 'root@NAME' -> (root, NAME): a (root, variant) pair spelled as one string."""
    root, _, name = spec.partition("@")
    return root, name


def effective(rec, name):
    """The recipe of variant NAME of REC: REC without `variants`, the keys the variant names replaced whole. '' is the implicit variant."""
    if not isinstance(rec, dict):
        return rec
    out = {k: v for k, v in rec.items() if k != "variants"}
    if name:
        out.update(rec["variants"][name])
    return out


def variant_names(rec):
    v = rec.get("variants") if isinstance(rec, dict) else None
    return sorted(v) if isinstance(v, dict) else []


def pairs_of(rec, root):
    """Every (root, variant) pair spec of a recipe: the implicit one and one per declared variant."""
    return [root] + ["%s@%s" % (root, n) for n in variant_names(rec)]


def load_recipe(repo, roots, spec):
    """The effective recipe of a root or of a 'root@NAME' pair."""
    root, name = split_spec(spec)
    with open(os.path.join(repo, roots[root]["recipe"])) as f:
        rec = json.load(f)
    return effective(rec, name) if not name or name in variant_names(rec) else rec


def generated_outputs(rec):
    g = rec.get("generated")
    return [o for x in (g if isinstance(g, list) else []) if isinstance(x, dict) and isinstance(x.get("outputs"), list)
            for o in x["outputs"] if isinstance(o, str)]


def units_of(rec, root, files):
    """Sorted root-relative unit paths: tracked source files + generated outputs with a source name."""
    mine = {f[len(root) + 1:] for f in files if under(f, root)}
    return sorted(u for u in mine | set(generated_outputs(rec)) if is_unit(u))


def load_pin(repo="."):
    """verify/guest_compiler.json of the checkout, or None when the tree has none."""
    p = os.path.join(repo, GUEST_PIN)
    return json.load(open(p)) if os.path.exists(p) else None


def guest_errors(rec, root, roots, pin):
    """The admit.json entry of a product root declares "target": "guest" (the game's code: compiled by the game's compiler) or "host".
    A guest root's compiler is the external of verify/guest_compiler.json, exactly (name, commit, tree, mount point, driver paths): the
    distribution's g++-13 is not the compiler of guest code, for C and C++ alike (one compiler key serves both)."""
    t = roots.get(root, {}).get("target")
    if t not in GUEST_TARGETS:
        return ['admit.json %s: a product root declares "target": "guest" or "host"' % root]
    if t == "host":
        return []
    if not isinstance(pin, dict) or not isinstance(pin.get("external"), dict):
        return ["guest root %s: %s is missing or has no external" % (root, GUEST_PIN)]
    c = rec.get("compiler")
    if not (isinstance(c, dict) and set(c) == {"external", "cc", "cxx"}):
        return ["guest root %s: compiler must be the external compiler %r (not a distribution driver)" % (root, pin["external"].get("name"))]
    ex = next((e for e in rec.get("external", []) if isinstance(e, dict) and e.get("name") == c["external"]), None)
    bad = [] if ex == pin["external"] else ["external %r is not the pinned one (%s)" % (c["external"], pin["external"])]
    rt = pin.get("runtime")   # the guest's libc/libstdc++ external: when a recipe declares it (by name), it is the pinned one, exactly
    if isinstance(rt, dict):
        bad += ["external %r is not the pinned runtime (%s)" % (e["name"], rt) for e in rec.get("external", []) if isinstance(e, dict) and e.get("name") == rt["name"] and e != rt]
    bad += ["compiler.%s is %r, pinned %r" % (k, c[k], pin.get(k)) for k in ("cc", "cxx") if c[k] != pin.get(k)]
    return ["guest root %s: %s" % (root, b) for b in bad]


LINK_KEYS = {"output", "flags"}
LINK_OPT = {"script", "kind", "version_script", "roots", "libs"}
LIBS_KEYS = {"external", "dirs", "names", "startfiles", "endfiles"}
KINDS = ("pie", "exe", "shared")
# The emulation names a link may carry, and the target each one is: (ELF class, e_machine). `pie` is the ILP32 guest; `exe` and `shared` are host images.
TARGETS = {"aarch64linux32": (1, 183), "aarch64linux": (2, 183), "elf_x86_64": (2, 62)}
KIND_EMU = {"pie": ("aarch64linux32",), "exe": ("aarch64linux", "elf_x86_64"), "shared": ("aarch64linux", "elf_x86_64")}


def emulation_flags(toks, kind="pie"):
    """Problems of the link flags: the options of a link are fixed by the rule (RECIPE.md "Link"), the recipe names the emulation only:
    `-m <emulation>` (or `-m<emulation>`) exactly once, and `-EL`, `-X`. The emulation is the target of the link (TARGETS); a `pie` is the ILP32 guest."""
    err, prev, emu = [], None, []
    for t in toks:
        if prev == "-m":
            emu.append(t)
            prev = None
        elif t == "-m":
            prev = t
        elif t.startswith("-m") and len(t) > 2 and t[2] != "-":
            emu.append(t[2:])
        elif t not in ("-EL", "-X"):
            err.append("link.flags: %s is not an emulation option (-m <emulation>, -EL, -X); the other options are fixed by the rule" % t)
    if prev:
        err.append("link.flags: -m has no emulation name")
    if len(emu) != 1 or emu[0] not in KIND_EMU[kind]:
        err.append("link.flags must name one emulation of a %s link %s exactly once, has %s" % (kind, KIND_EMU[kind], emu))
    return err


def link_roots_cycle(root, roots, repo, seen=()):
    """The cycle through ROOT in the `link.roots` graph, as a list of roots, or None."""
    if root in seen:
        return list(seen[seen.index(root):]) + [root]
    ent = roots.get(root)
    try:
        rec = json.load(open(os.path.join(repo, ent["recipe"]))) if ent and ent.get("recipe") else None
    except (OSError, ValueError):
        rec = None
    lk = rec.get("link") if isinstance(rec, dict) else None
    for d in (lk.get("roots", []) if isinstance(lk, dict) and isinstance(lk.get("roots"), list) else []):
        if isinstance(d, str):
            c = link_roots_cycle(split_spec(d)[0], roots, repo, tuple(seen) + (root,))
            if c:
                return c
    return None


def link_errors(lk, root, mine, roots, ext_names, repo):
    """Problems of the `link` key: {script, output, flags} and optionally `roots` (admitted product roots whose objects are link inputs after
    the root's own, in list order) and `libs` ({external, dirs, names, startfiles, endfiles}: the guest libraries inside an external)."""
    kind = lk.get("kind", "pie") if isinstance(lk, dict) else None
    if not (isinstance(lk, dict) and LINK_KEYS <= set(lk) <= LINK_KEYS | LINK_OPT and isinstance(lk["flags"], list)
            and all(isinstance(t, str) for t in lk["flags"]) and isinstance(lk["output"], str) and kind in KINDS
            and re.fullmatch(r"[A-Za-z0-9._-]+\.(elf|so|exe)", lk["output"])
            and all(k not in lk or (rel_ok(lk[k]) and lk[k] in mine) for k in ("script", "version_script"))
            and ("script" in lk or kind != "pie") and ("version_script" in lk) == (kind == "shared")):
        return ["link must be {output: NAME.elf|.so|.exe, flags: [..]} with `script` (a tracked file of the root; required for kind pie, the default), kind pie|exe|shared, "
                "`version_script` (a tracked file of the root: required for, and only for, kind shared), optionally roots, libs"]
    err = ["link.flags: %s is in the refusal set R" % t for t in refused(lk["flags"])] + emulation_flags(lk["flags"], kind)
    deps = lk.get("roots", [])
    if not (isinstance(deps, list) and all(isinstance(d, str) for d in deps)):
        err.append("link.roots must be a list of repo paths of admitted product roots")
        deps = []
    for i, d in enumerate(deps):
        if d in deps[:i]:
            err.append("link.roots: %s is listed twice" % d)
        dr, dv = split_spec(d)
        ent = roots.get(dr)
        if dr == root:
            err.append("link.roots: %s is the root itself" % d)
        elif ent and ent.get("class") != "product":
            err.append("link.roots: %s is a %s root: a root reached through link.roots runs at its target, so it is a product root (a tool root's outputs run at build time only)" % (d, ent.get("class")))
        elif not ent:
            err.append("link.roots: %s is not an admitted product root" % d)
        else:
            try:
                drec = json.load(open(os.path.join(repo, ent["recipe"])))
                dl = drec.get("link")
            except (OSError, ValueError, KeyError, AttributeError):
                err.append("link.roots: the recipe of %s is unreadable" % d)
                continue
            if dv and dv not in variant_names(drec):
                err.append("link.roots: %s has no variant %r" % (dr, dv))
            if dl is not None:
                err.append("link.roots: %s has a link of its own (an input root links nothing)" % d)
    cyc = link_roots_cycle(root, roots, repo) if deps else None
    if cyc:
        err.append("link.roots: cycle %s" % " -> ".join(cyc))
    lb = lk.get("libs")
    if isinstance(lb, dict) and set(lb) == {"system"}:
        sysl = lb["system"]
        if not (isinstance(sysl, list) and sysl and all(isinstance(p, str) and p.startswith("/") and posixpath.normpath(p) == p for p in sysl)):
            err.append("link.libs.system must be a non-empty list of absolute normalised paths of the shared libraries of env.json packages")
        elif kind == "pie":
            err.append("link.libs.system: a pie is the guest: its libraries are those of an external")
    elif "libs" in lk:
        paths = lambda k: isinstance(lb.get(k), list) and all(rel_ok(p) for p in lb[k])  # noqa: E731
        if not (isinstance(lb, dict) and set(lb) == LIBS_KEYS and paths("dirs") and paths("startfiles") and paths("endfiles")
                and isinstance(lb.get("names"), list) and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9._+-]+", n) for n in lb["names"])):
            err.append("link.libs must be {external: NAME, dirs: [paths inside it], names: [lib names], startfiles: [..], endfiles: [..]}")
        elif lb["external"] not in ext_names:
            err.append("link.libs.external %r names no external" % (lb["external"],))
    return err


def check(rec, root, files, roots, pin=None, repo="."):
    """Every problem of recipe REC of product root ROOT; roots {path: root entry}; PIN = load_pin(); REPO: where the recipes of the
    roots of `link.roots` are read (their link null, no cycle). -> [str] (empty = valid)."""
    if not isinstance(rec, dict):
        return ["recipe is not a JSON object"]
    err = ["unknown key %r" % k for k in sorted(set(rec) - KEYS)]
    vs = rec.get("variants")
    if vs is not None:
        if not (isinstance(vs, dict) and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", n) and isinstance(v, dict) and v and set(v) <= VARIANT_KEYS
                                              for n, v in vs.items())):
            return err + ["variants must be {NAME: {compiler and/or flags}}: NAME letters, digits, _ . -; a variant replaces only the keys it names (%s)" % sorted(VARIANT_KEYS)]
        for n in sorted(vs):    # every variant is a recipe of its own: all the rules apply to its effective form
            err += ["variant %s: %s" % (n, e) for e in check(effective(rec, n), root, files, roots, pin, repo)]
    err += ["missing key %r" % k for k in ("format", "compiler", "flags", "include", "units") if k not in rec]
    if rec.get("format") != FORMAT:
        err.append("format is not " + FORMAT)
    if rec.get("units") != "all":
        err.append('units must be "all" (no tracked source file can be left out)')
    mine = {f[len(root) + 1:] for f in files if under(f, root)}
    gen = generated_outputs(rec)
    comp, exts, ats = rec.get("compiler"), rec.get("external", []), []
    if not isinstance(exts, list):
        err.append("external must be a list")
        exts = []
    for i, e in enumerate(exts):
        if not (isinstance(e, dict) and set(e) == EXT_KEYS and all(isinstance(v, str) for v in e.values())):
            err.append("external[%d] needs exactly %s (strings)" % (i, ", ".join(sorted(EXT_KEYS))))
            continue
        at = e["at"]
        for ok, why in ((e["repo"] == "fnv-nx-build", "repo must be fnv-nx-build"), (re.fullmatch("[0-9a-f]{40}", e["commit"]), "commit is not 40 hex"),
                        (re.fullmatch("[0-9a-f]{64}", e["tree_sha256"]), "tree_sha256 is not 64 hex"), (re.fullmatch("[A-Za-z0-9._-]+", e["name"]), "bad name"),
                        (at.startswith("/") and at != "/" and posixpath.normpath(at) == at, "at is not an absolute normalised path"),
                        (not any(under(at, p) or under(p, at) for p in SANDBOX_PATHS), "at overlaps a path the stages use itself %s" % (SANDBOX_PATHS,)),
                        (not any(under(at, a) or under(a, at) for a in ats), "at overlaps another external's at"),
                        (e["name"] not in [x["name"] for x in exts[:i] if isinstance(x, dict) and "name" in x], "duplicate name")):
            if not ok:
                err.append("external %r: %s" % (e["name"], why))
        ats.append(at)
    names = {e["name"] for e in exts if isinstance(e, dict) and isinstance(e.get("name"), str)}
    word = lambda v: isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9._+-]+", v)  # noqa: E731
    if isinstance(comp, dict) and set(comp) == {"external", "cc", "cxx"}:
        if comp["external"] not in names:
            err.append("compiler.external %r names no external" % (comp["external"],))
        if not (rel_ok(comp["cc"]) and rel_ok(comp["cxx"])):
            err.append("compiler cc/cxx of an external must be relative paths inside it")
    elif not (isinstance(comp, dict) and set(comp) == {"cc", "cxx"} and word(comp["cc"]) and word(comp["cxx"])):
        err.append('compiler must be {"cc": NAME, "cxx": NAME} (drivers under /usr/bin) or {"external": N, "cc": PATH, "cxx": PATH}')
    fl = rec.get("flags")
    if not (isinstance(fl, dict) and set(fl) == {"c", "cxx", "asm"} and all(isinstance(v, list) and all(isinstance(t, str) for t in v)
                                                                             for v in fl.values())):
        err.append('flags must be {"c": [..], "cxx": [..], "asm": [..]} of strings')
        fl = {}
    for k, toks in fl.items():
        err += ["flags.%s: %s is in the refusal set R" % (k, t) for t in refused(toks)]
        err += ["flags.%s: %s changes a stage's job (use include / the unit's own name)" % (k, t) for t in toks if t in (
            "-o", "-c", "-E", "-S", "-x") or t.startswith(("-I", "-M"))]
        err += ["flags.%s: %s loads or configures a plugin (use plugins)" % (k, t) for t in toks if t.startswith("-fplugin")]
    inc = rec.get("include")
    for d in inc if isinstance(inc, list) else err.append("include must be a list") or []:
        if isinstance(d, str) and any(under(d, a) for a in ats):
            continue
        if not rel_ok(d) or not any(under(d, r) for r in roots):
            err.append("include %r is not a path inside an admitted root or an external's at" % (d,))
        elif not (any(under(f, d) for f in files) or any(under(root + "/" + o, d) for o in gen)):
            err.append("include %r holds no tracked or generated file" % d)
    seen = set()
    for i, g in enumerate(rec.get("generated", []) if isinstance(rec.get("generated", []), list) else err.append("generated is not a list") or []):
        if not (isinstance(g, dict) and set(g) == {"tool", "args", "inputs", "outputs"}):
            err.append("generated[%d] needs exactly tool, args, inputs, outputs" % i)
            continue
        t, ins, outs = g["tool"], g["inputs"], g["outputs"]
        owner = next((r for r in roots if isinstance(t, str) and under(t, r)), None)
        if not (isinstance(t, str) and t in files and t.endswith(".py") and owner and roots[owner]["class"] in ("tool", "product")):
            err.append("generated[%d].tool %r is not a tracked .py file of an admitted tool or product root" % (i, t))
        if not (isinstance(g["args"], list) and all(isinstance(a, str) for a in g["args"])):
            err.append("generated[%d].args must be strings" % i)
        if not (isinstance(ins, list) and all(rel_ok(p) and any(f == p or under(f, p) for f in files) for p in ins)):
            err.append("generated[%d].inputs must be tracked files or directories (repo-relative)" % i)
        elif any(a != b and under(a, b) for a in ins for b in ins):
            err.append("generated[%d].inputs nest" % i)
        if not (isinstance(outs, list) and outs and all(rel_ok(o) for o in outs)):
            err.append("generated[%d].outputs must be a non-empty list of root-relative paths" % i)
            continue
        for o in outs:
            if o in mine:
                err.append("generated output %s is also a tracked file" % o)
            if o in seen:
                err.append("generated output %s is produced twice" % o)
            seen.add(o)
    units = set(units_of(rec, root, files))
    ol = rec.get("os_layer", [])
    for u in ol if isinstance(ol, list) else err.append("os_layer must be a list") or []:
        if u not in units:
            err.append("os_layer entry %r is not a unit" % (u,))
    gm = rec.get("game")
    if gm is not None and not (isinstance(gm, str) and rel_ok(gm) and any(u.startswith(gm.strip("/") + "/") for u in units)):
        err.append("game %r is not a root-relative directory holding units" % (gm,))
    for key in ("constants", "contract_names"):
        c = rec.get(key)
        if c is not None and not (isinstance(c, str) and rel_ok(c) and (c in mine or c in seen)):
            err.append("%s %r is neither a tracked file of the root nor a generated output" % (key, c))
    lk = rec.get("link")
    if lk is not None:
        err += link_errors(lk, root, mine, roots, names, repo)
        err += ["link: the ldcheck stage needs %s (a tracked file of an admitted tool root)" % p for p in (
            LD_TOOLS[0] + "/ldcheck.py", LD_TOOLS[0] + "/gateids.py", LD_TOOLS[1] + "/gatefile.py")
            if p not in files or not any(under(p, r) for r in roots)]
    err += guest_errors(rec, root, roots, pin)
    err += plugin_errors(rec, root, files, roots, units, names)
    return err


def plugin_errors(rec, root, files, roots, units, ext_names):
    """Problems of the plugin declaration of recipe REC of ROOT, and the gate rule: a product root with native or decomp C++ loads both
    plugins of PLUGIN_SOURCES with their fixed arguments; a root whose C++ is all lifted needs none. What is lifted is declared in the
    admit.json entry of the root, "cxx_origin": "native" | {"lifted_by": TOOL.py, "pack": EXTERNAL}, required when the root has a C++ unit.
    Consistency of a lifted declaration: no tracked C++ unit in the root, every C++ unit is an output of a `generated` step whose tool is
    `lifted_by` (a tracked .py of an admitted tool root), `pack` names an external of the recipe (declared inputs of every generator)."""
    err, pl = [], rec.get("plugins", [])
    if not isinstance(pl, list):
        return ["plugins must be a list"]
    loaded = {}
    for i, p in enumerate(pl):
        if not (isinstance(p, dict) and set(p) - {"inputs"} == PLUGIN_KEYS and isinstance(p["name"], str) and isinstance(p["source"], str)
                and isinstance(p["args"], dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in p["args"].items())):
            err.append("plugins[%d] needs exactly name, source, args (name and source strings, args {string: string})" % i)
            continue
        n, src = p["name"], p["source"]
        owner = next((r for r in roots if under(src, r)), None)
        if not re.fullmatch("[a-z][a-z0-9_]*", n):
            err.append("plugins[%d].name %r is not [a-z][a-z0-9_]*" % (i, n))
        elif n in loaded:
            err.append("plugin %s is declared twice" % n)
        elif posixpath.basename(src) != n + ".cc":
            err.append("plugin %s: source %s must be named %s.cc (the plugin's name is its file's)" % (n, src, n))
        if not (rel_ok(src) and src in files and owner and roots[owner]["class"] == "tool"):
            err.append("plugin %s: source %r is not a tracked file of an admitted tool root" % (n, src))
        elif n in PLUGIN_SOURCES and src != PLUGIN_SOURCES[n]:
            err.append("plugin %s: source must be %s" % (n, PLUGIN_SOURCES[n]))
        ins = p.get("inputs", [])
        if not (isinstance(ins, list) and all(isinstance(x, str) and rel_ok(x) and x in files for x in ins)):
            err.append("plugin %s: inputs must be tracked files (repo-relative)" % n)
        elif n in PLUGIN_SOURCES and ins != PLUGIN_INPUTS.get(n, []):
            err.append("plugin %s: inputs must be %s" % (n, PLUGIN_INPUTS.get(n, [])))
        if not all(re.fullmatch("[a-z][a-z0-9-]*", k) for k in p["args"]):
            err.append("plugin %s: argument names must be [a-z][a-z0-9-]*" % n)
        loaded[n] = p
    px = rec.get("plugin_cxx")
    if pl and not (isinstance(px, str) and re.fullmatch(r"[A-Za-z0-9._+-]+", px)):
        err.append("plugins need plugin_cxx: the driver under /usr/bin (env.json) that builds them, of the same GCC version as compiler.cxx")
    elif not pl and px is not None:
        err.append("plugin_cxx without plugins")
    cxx = [u for u in units if u.endswith(CXX_EXT)]
    ent = roots.get(root, {})
    origin = ent.get("cxx_origin")
    if not cxx:
        return err + (["admit.json %s: cxx_origin of a root without C++ units" % root] if origin is not None else [])
    if origin == "native":
        for n in sorted(PLUGIN_SOURCES):
            if n not in loaded:
                err.append("native C++ root %s does not load plugin %s (every native or decomp C++ body is compiled with both)" % (root, n))
            elif loaded[n].get("args") != PLUGIN_ARGS[n]:
                err.append("plugin %s: arguments must be %s, are %s" % (n, PLUGIN_ARGS[n], loaded[n].get("args")))
        if list(loaded) != [n for n in PLUGIN_SOURCES if n in loaded]:
            err.append("plugins must be loaded in the order %s" % list(PLUGIN_SOURCES))
        fl = rec.get("flags", {}).get("cxx", []) if isinstance(rec.get("flags"), dict) else []
        err += ["native C++ root %s: flags.cxx lacks the semantics flag %s" % (root, f) for f in SEMANTICS_FLAGS if f not in fl]
    elif isinstance(origin, dict) and set(origin) == {"lifted_by", "pack"}:
        tool, pack = origin["lifted_by"], origin["pack"]
        mine = {f[len(root) + 1:] for f in files if under(f, root)}
        owner = next((r for r in roots if isinstance(tool, str) and under(tool, r)), None)
        if not (isinstance(tool, str) and tool in files and tool.endswith(".py") and owner and roots[owner]["class"] == "tool"):
            err.append("cxx_origin.lifted_by %r is not a tracked .py of an admitted tool root" % (tool,))
        if pack not in ext_names:
            err.append("cxx_origin.pack %r names no external of the recipe" % (pack,))
        made = {o for g in rec.get("generated", []) if isinstance(g, dict) and g.get("tool") == tool and isinstance(g.get("outputs"), list)
                for o in g["outputs"] if isinstance(o, str)}
        err += ["lifted root %s: C++ unit %s is not an output of a step of %s" % (root, u, tool) for u in cxx if u not in made or u in mine]
    else:
        err.append('admit.json %s: a root with C++ units needs cxx_origin "native" or {"lifted_by": TOOL.py, "pack": EXTERNAL}' % root)
    return err


def _copy(repo, files, base, dest):
    for f in files:
        d = os.path.join(dest, f[len(base) + 1:] if base else f)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copyfile(os.path.join(repo, f), d)


def tail(store, res):
    p = cas._obj(store, cas.manifest(res["stderr"], store))
    return open(p, errors="replace").read().splitlines()[-20:]


def symbols(path, unit):
    """(defined [{symbol,bind,type,object}], undefined {symbol}) of the global and weak symbols of ELF32 or ELF64 little-endian
    object PATH (.symtab; ELF32 is the ILP32 ABI, ELF64 the LP64 one: the layouts of Elf32_/Elf64_ Shdr and Sym)."""
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF" or d[4] not in (1, 2) or d[5] != 1:
        raise ValueError("%s: not an ELF32/ELF64 little-endian object" % path)
    w64 = d[4] == 2
    shoff, = struct.unpack_from("<Q" if w64 else "<I", d, 0x28 if w64 else 0x20)
    esz, num = struct.unpack_from("<HH", d, 0x3a if w64 else 0x2e)
    secs = [struct.unpack_from("<IIQQQQIIQQ" if w64 else "<IIIIIIIIII", d, shoff + i * esz) for i in range(num)]   # name type flags addr off size link info align entsize
    defined, undefined = [], set()
    for s in (x for x in secs if x[1] == 2):
        st = secs[s[6]]
        strtab = d[st[4]:st[4] + st[5]]
        for t in struct.iter_unpack("<IBBHQQ" if w64 else "<IIIBBH", d[s[4]:s[4] + s[5]]):
            name, info, shndx = (t[0], t[1], t[3]) if w64 else (t[0], t[3], t[5])
            n = strtab[name:strtab.index(b"\0", name)].decode()
            if info >> 4 in SBIND and n:
                if shndx == 0:
                    undefined.add(n)
                else:
                    defined.append({"symbol": n, "bind": SBIND[info >> 4], "type": STYPE.get(info & 15, str(info & 15)), "object": unit})
    return defined, undefined


def fetch_externals(exts, store, extcache, ws):
    """Content-pinned inputs: each external of EXTS fetched by exact commit into the bare cache EXTCACHE/<commit>, checked out at
    WS/ext/<name>, ingested into the CAS STORE; its tree hash must equal tree_sha256 (BuildRed naming the external). -> {name: {host, tree, at}}.
    Used by a recipe (Job.externals) and by gate_contract.py (the exe-derived inputs of the contract gates), one mechanism."""
    out = {}
    url = os.environ.get("ARTIFACTS_URL", ARTIFACTS_URL)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    for e in exts:
        stage, bare, dest = "external:" + e["name"], os.path.join(extcache, e["commit"]), os.path.join(ws, "ext", e["name"])
        try:
            if not os.path.isdir(bare):
                tmp = "%s.tmp%d-%s" % (bare, os.getpid(), os.urandom(4).hex())
                os.makedirs(tmp)
                for cmd in (["init", "-q", "--bare"], ["fetch", "-q", "--depth", "1", url, e["commit"]]):
                    subprocess.run(["git", "-C", tmp] + cmd, env=env, check=True, capture_output=True)
                os.replace(tmp, bare)
            os.makedirs(dest)
            ienv = dict(env, GIT_INDEX_FILE=os.path.join(ws, "idx-" + e["name"]))
            subprocess.run(["git", "--git-dir", bare, "read-tree", e["commit"]], env=ienv, check=True, capture_output=True)
            subprocess.run(["git", "--git-dir", bare, "--work-tree", dest, "checkout-index", "-a", "-f"], env=ienv, check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError) as x:
            raise BuildRed({"stage": stage, "exit": getattr(x, "returncode", None),
                            "stderr": (getattr(x, "stderr", b"") or str(x).encode()).decode(errors="replace").splitlines()[-20:]})
        tree = cas.ingest(dest, store)
        if tree != e["tree_sha256"]:
            raise BuildRed({"stage": stage, "exit": None, "stderr": ["tree_sha256 is %s, recipe says %s" % (tree, e["tree_sha256"])]})
        out[e["name"]] = {"host": dest, "tree": tree, "at": e["at"]}
    return out


class Job:
    def __init__(self, repo, spec, store, jobs, ext):
        root, self.variant = split_spec(spec)
        self.repo, self.root, self.store, self.jobs, self.extcache = repo, root, store, jobs, ext
        self.roots, self.files = load_admit(repo), tracked(repo)
        self.rec = load_recipe(repo, self.roots, spec)
        self.keys, self.run, self.hit, self.ext, self.ppkeys, self.built = set(), 0, 0, {}, {}, []
        os.makedirs(os.path.join(store, "rt"), exist_ok=True)

    def stage(self, doc):
        if self.variant:     # the variant name is in every stage key (the implicit variant adds nothing: its keys are those of a root without variants)
            doc["env"] = dict(doc["env"], FNVNX_VARIANT=self.variant)
        v = toolflags.check_doc(doc)
        if v:
            raise BuildRed({"stage": doc["stage"], "exit": None, "stderr": ["toolflags: %s %s" % (r, d) for r, d, _ in v][:20]})
        try:
            key, res, st, _ = unitstage.run_stage(self.store, doc, False)
        except (sandbox.SbxError, unitstage.UnitError) as e:
            raise BuildRed({"stage": doc["stage"], "exit": None, "stderr": [str(e)[-300:]]})
        self.keys.add(key)
        self.run, self.hit = self.run + (st == "run"), self.hit + (st == "hit")
        if res["rc"]:
            raise BuildRed({"stage": doc["stage"], "exit": res["rc"], "stderr": tail(self.store, res)})
        return key, res

    def cached_rt(self, name, make):
        p = os.path.join(self.store, "rt", name + ".json")
        if not os.path.exists(p):
            tmp = "%s.tmp-%d-%s" % (p, os.getpid(), os.urandom(4).hex())   # builds may share a store: the temp name is private
            with open(tmp, "w") as f:
                json.dump(make(), f, sort_keys=True)
            os.replace(tmp, p)
        return p, json.load(open(p))

    def externals(self, ws):
        """Fetch by exact commit into the shared bare cache, check out, ingest, require tree_sha256 (BuildRed naming the external)."""
        self.ext.update(fetch_externals(self.rec.get("external", []), self.store, self.extcache, ws))

    def drivers(self):
        c = self.rec["compiler"]
        if "external" in c:
            e = self.ext[c["external"]]
            return e["at"] + "/" + c["cc"], e["at"] + "/" + c["cxx"]
        return "/usr/bin/" + c["cc"], "/usr/bin/" + c["cxx"]

    def hostpath(self, p):
        for e in self.ext.values():
            if under(p, e["at"]):
                return e["host"] + p[len(e["at"]):]
        return p

    def toolrt(self, paths):
        """Tool runtime (runtime.py rules) of driver PATHS plus every external as declared input. An external compiler is a tree
        mapped HOST@AT: the loader closure of its x86-64 ELF files is resolved as the loader resolves it inside the sandbox."""
        xin = [{"at": e["at"], "tree": e["tree"]} for e in self.ext.values()]
        tag = hashlib.sha256(repr((paths, xin)).encode()).hexdigest()[:16]
        c = self.rec["compiler"]

        def make():
            if "external" in c:
                x = self.ext[c["external"]]
                exes, trees = paths, ["%s@%s" % (x["host"], x["at"])]
                # What the external compiler itself names outside its tree (its own answers, as gcc_set asks a distribution compiler): the
                # assembler and linker it is configured with, and the header directories of its search list, symlink targets included
                # (the distribution's cross headers and binutils, pinned by env.json). They are declared inputs at their own paths.
                host = self.hostpath(paths[0])
                xs, ts = unitstage.gcc_set(host)
                q = lambda n: subprocess.run([host, "-print-prog-name=" + n], capture_output=True, text=True).stdout.strip()  # noqa: E731
                out = lambda p: not any(under(p, e["host"]) for e in self.ext.values())  # noqa: E731
                q = lambda n, _q=q: unitstage.prog(_q(n))  # noqa: E731
                exes += [p for p in xs[1:] + [q("ld")] if os.path.isabs(p) and out(p)]
                trees += sorted({r for t in ts for r in (t, os.path.realpath(t)) if out(r) and os.path.isdir(r)})
            else:
                exes, trees = [], []
                for p in paths:
                    xs, ts = unitstage.gcc_set(p)
                    exes, trees = exes + xs, trees + ts
            return self.plus(runtime.build_tool(self.store, sorted(set(exes)), sorted(set(trees))))
        return self.cached_rt("tool-" + tag, make)

    def plus(self, rt):
        """RT with every external that it does not hold yet as a declared input."""
        have = {i["at"] for i in rt["inputs"]}
        rt["inputs"] = sorted(rt["inputs"] + [{"at": e["at"], "tree": e["tree"]} for e in self.ext.values() if e["at"] not in have], key=lambda i: i["at"])
        return rt

    def ingest_files(self, tmp, name, files, base):
        d = os.path.join(tmp, name)
        os.makedirs(d, exist_ok=True)
        _copy(self.repo, files, base, d)
        return cas.ingest(d, self.store)

    def generate(self, ws):
        if not self.rec.get("generated"):
            return
        _, py = self.cached_rt("python", lambda: runtime.build(self.store, sys.executable, []))
        xin = [{"at": e["at"], "tree": e["tree"]} for e in self.ext.values()]
        for i, g in enumerate(self.rec["generated"]):
            owner = next(r for r in self.roots if under(g["tool"], r))
            tmp = tempfile.mkdtemp(dir=ws)
            ins = [{"at": "/tool", "tree": self.ingest_files(tmp, "tool", [f for f in self.files if under(f, owner)], owner)}] + xin
            for p in g["inputs"]:
                sub = [f for f in self.files if f == p or under(f, p)]
                ins.append({"at": "/in/" + p, "tree": cas.ingest(os.path.join(self.repo, p), self.store) if sub == [p] else
                            self.ingest_files(tmp, "in%d" % len(ins), sub, p)})
            env = {k: v for k, v in py["env"].items() if k != "PYTHONNOUSERSITE"}
            env.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(py["python"])})
            doc = {"format": "fnvnx-stage/1", "stage": "gen:%s:%d" % (self.root, i), "class": "build", "tool": "python", "cwd": "/work",
                   "argv": [py["python"], "-s", "-B", "/tool/" + g["tool"][len(owner) + 1:]] + g["args"], "env": env,
                   "inputs": sorted(py["inputs"] + ins, key=lambda x: x["at"]), "outputs": ["/out"]}
            res = self.stage(doc)[1]
            got = sorted(r for r, e in cas.listing(res["outputs"]["/out"], self.store) if e["t"] != "d")
            if got != sorted(g["outputs"]):
                raise BuildRed({"stage": doc["stage"], "exit": 0, "stderr": ["outputs %s differ from the declared %s" % (got, sorted(g["outputs"]))]})
            cas.materialize(res["outputs"]["/out"], self.store, os.path.join(tmp, "out"))
            for o in g["outputs"]:
                os.makedirs(os.path.dirname(os.path.join(ws, "root", o)), exist_ok=True)
                shutil.copyfile(os.path.join(tmp, "out", o), os.path.join(ws, "root", o))

    def includes(self, ws):
        """unitstage --include specs in recipe order: HOST@/inc/N for a directory of this root or the repo, the sandbox path itself
        for a directory inside an external (a declared input of the tool runtime)."""
        out, n = [], 0
        for d in self.rec["include"]:
            if any(under(d, e["at"]) for e in self.ext.values()):
                out.append(d)
                continue
            if under(d, self.root):
                host = os.path.join(ws, "root", d[len(self.root) + 1:])
            else:
                _copy(self.repo, [f for f in self.files if under(f, d)], "", os.path.join(ws, "inc"))
                host = os.path.join(ws, "inc", d)
            out.append("%s@/inc/%d" % (host, n))
            n += 1
        return out

    def plugins(self, ws):
        """Build every plugin of the recipe as a hermetic stage -> [(name, tree of its /out, argv flags)]. The stage runs the host driver
        `plugin_cxx` (env.json) on the plugin source and the plugin headers of the target compiler (`compiler.cxx -print-file-name=plugin`),
        so its key is f(source content, host compiler, flags, headers). The .so is in the store; each CC stage declares it as an input."""
        pl = self.rec.get("plugins", [])
        if not pl:
            return []
        host = "/usr/bin/" + self.rec["plugin_cxx"]
        cxx = self.hostpath(self.drivers()[1])
        pdir = subprocess.run([cxx, "-print-file-name=plugin"], capture_output=True, text=True).stdout.strip()
        if not os.path.isfile(os.path.join(pdir, "include", "gcc-plugin.h")):
            raise BuildRed({"stage": "plugin", "exit": None, "stderr": ["no plugin headers of %s in %s (gcc-N-plugin-dev of env.json)" % (cxx, pdir)]})
        inc, trees = pdir, [pdir]
        for e in self.ext.values():   # headers inside an external compiler: its tree is a declared input at `at` already
            if under(pdir, e["host"]):
                inc, trees = e["at"] + pdir[len(e["host"]):], []

        def make():
            exes, ts = unitstage.gcc_set(host)
            # a native driver prints a bare `as` / `ld` (it finds them on PATH): the binutils of env.json are /usr/bin/as and /usr/bin/ld;
            # linking a shared object also needs collect2 and ld, which gcc_set (compile only) does not list
            q = lambda n: subprocess.run([host, "-print-prog-name=" + n], capture_output=True, text=True).stdout.strip()  # noqa: E731
            exes = [e if e.startswith("/") else "/usr/bin/" + e for e in exes + [q("collect2"), q("ld")]]
            return self.plus(runtime.build_tool(self.store, sorted(set(exes)), sorted(set(ts + trees))))
        rt = self.cached_rt("tool-plugin-" + hashlib.sha256(repr((host, pdir, sorted((e["at"], e["tree"]) for e in self.ext.values()))).encode()).hexdigest()[:16], make)[1]
        # A plugin is linked against nothing (its symbols come from the compiler that loads it): -nostdlib, with the startup files the
        # driver would pass (crti/crtbeginS ... crtendS/crtn: __dso_handle, .init/.fini). crtbeginS/crtendS are in the driver's own
        # tree (a declared input); crti/crtn are libc6-dev's files, copied by name into a tree of their own.
        crt = tempfile.mkdtemp(dir=ws)
        pf = lambda n: subprocess.run([host, "-print-file-name=" + n], capture_output=True, text=True).stdout.strip()  # noqa: E731
        for n in ("crti.o", "crtn.o"):
            if not os.path.isabs(pf(n)):
                raise BuildRed({"stage": "plugin", "exit": None, "stderr": ["%s: no %s (libc6-dev of env.json)" % (host, n)]})
            shutil.copyfile(pf(n), os.path.join(crt, n))
        crt_tree = cas.ingest(crt, self.store)
        out = []
        for p in pl:
            n, src = p["name"], p["source"]
            tmp = tempfile.mkdtemp(dir=ws)
            tree = self.ingest_files(tmp, "psrc", [src] + p.get("inputs", []), "")   # at their repo paths: `#include "../names/x.cc"` resolves
            argv = [host, "-shared", "-nostdlib", "-fPIC", "-fno-rtti", "-fno-exceptions", "-O2", "-I" + inc + "/include", "-frandom-seed=" + hashlib.sha256(n.encode()).hexdigest(),
                    "-Werror=date-time", "-ffile-prefix-map=/psrc=psrc", "-o", "/out/%s.so" % n, "/plugcrt/crti.o", pf("crtbeginS.o"),
                    "/psrc/" + src, pf("crtendS.o"), "/plugcrt/crtn.o"]
            doc = {"format": "fnvnx-stage/1", "stage": "plugin:" + n, "argv": argv, "cwd": "/", "class": "build", "tool": "gcc",
                   "env": dict(unitstage.ENV, PATH=os.path.dirname(host)), "outputs": ["/out"],
                   "inputs": sorted(rt["inputs"] + [{"at": "/psrc", "tree": tree}, {"at": "/plugcrt", "tree": crt_tree}], key=lambda i: i["at"])}
            res = self.stage(doc)[1]["outputs"]["/out"]
            flags = ["-fplugin=/plug/%s/%s.so" % (n, n)] + ["-fplugin-arg-%s-%s%s" % (n, k, "=" + v if v else "") for k, v in p["args"].items()]
            out.append((n, res, flags))
        return out

    def compile(self, ws, units):
        rec, drv = self.rec, dict(zip(("c", "cxx"), self.drivers()))
        kinds = {u: ("c" if u.endswith(CC_EXT) else "cxx" if u.endswith(CXX_EXT) else "asm") for u in units}
        used = sorted({drv["c" if k == "asm" else k] for k in kinds.values()})
        if not used:
            return {}
        rtp, _ = self.toolrt(used)
        self.built = self.plugins(ws) if any(k == "cxx" for k in kinds.values()) else []
        builds = {}
        # RULE: the CC stage (-fpreprocessed, input unit.ii) mounts, and so keys on, only the compiler (its external, if it is one), the
        # declared plugin .so files and unit.ii. Any other external is not mounted there: what is not mounted cannot be read, so the object
        # does not depend on it by construction (a new commit of such an external changes the PP keys, not the CC keys).
        cc = self.rec["compiler"].get("external")
        drop = {e["at"] for n, e in self.ext.items() if n != cc}
        for k in sorted(set(kinds.values())):
            fp = os.path.join(ws, "flags-%s.json" % k)
            json.dump({"argv": rec["flags"][k]}, open(fp, "w"))
            builds[k] = unitstage.Build(argparse.Namespace(store=self.store, cold=False, cxx=drv["c" if k == "asm" else k], toolrt=rtp, flags_file=fp,
                                                           src_root=os.path.join(ws, "root") + "@/src", include=self.includes(ws)))
            builds[k].cc_drop = drop
            builds[k].variant = self.variant
            if k == "cxx":   # plugins are loaded by the C++ front end of the CC stage only (not by cc1, not by preprocessing)
                builds[k].cc_flags = [f for _, _, fl in self.built for f in fl]
                builds[k].cc_inputs = [("/plug/" + n, t) for n, t, _ in self.built]

        def one(u):
            try:
                return u, builds[kinds[u]].unit(u)
            except (sandbox.SbxError, unitstage.UnitError) as e:
                return u, {"status": "cc-failed", "failure": {"stage": "cc:" + u, "exit": None, "stderr": [str(e)[-300:]]}}
        res, failed = {}, {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs) as ex:
            for u, e in ex.map(one, units):
                if e["status"] != "ok":
                    cc = e["status"] == "cc-failed"
                    failed[u] = e.get("failure") or {"stage": "%s:%s" % ("cc" if cc else "pp", u), "exit": e.get("cc_rc" if cc else "pp_rc"),
                                                     "stderr": e["error"].splitlines()}
                    continue
                self.keys |= {e[k] for k in ("pp_key", "cc_key") if e.get(k)}
                self.run += (e.get("pp_status") == "run") + (e.get("cc_status") == "run")
                self.hit += (e.get("pp_status") == "hit") + (e.get("cc_status") == "hit")
                res[u] = e["object"]
                if e.get("pp_key"):
                    self.ppkeys[u] = e["pp_key"]
        if failed:
            raise BuildRed(dict(failed[sorted(failed)[0]], units=sorted(failed), done=sorted(res)))
        return res

    def link(self, ws, groups):
        """Stage `ld:<root>`: the linker of the compiler with the options the rule fixes (RECIPE.md "Link"). GROUPS: [(label, [(unit, path of
        the .o file)])]: the root's own objects, then those of every `link.roots` root, in list order. Every object is linked whole;
        the guest libraries of `link.libs` are searched (-L, -l) as the gcc driver does for the ILP32 PIE multilib. -> (tree of /out,
        tree of /objs)."""
        lk, cc = self.rec["link"], self.drivers()[0]
        pn = subprocess.run([self.hostpath(cc), "-print-prog-name=ld"], capture_output=True, text=True).stdout.strip()
        ld = unitstage.prog(posixpath.normpath(pn))
        if "external" in self.rec["compiler"]:
            e = self.ext[self.rec["compiler"]["external"]]
            ld = e["at"] + ld[len(e["host"]):] if ld.startswith(e["host"] + "/") else ld
            rt = self.toolrt([cc])[1]
        else:
            rt = self.cached_rt("tool-ld-" + hashlib.sha256(repr((ld, sorted((e["at"], e["tree"]) for e in self.ext.values()))).encode()).hexdigest()[:16], lambda: self.plus(runtime.build_tool(self.store, [ld], [])))[1]
        od, sd = os.path.join(ws, "objs"), os.path.join(ws, "ld")
        args = []
        for label, objs in groups:
            for u, p in objs:
                dest = os.path.join(od, label, u + ".o")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(p, dest)
                args.append("/objs/%s/%s.o" % (label, u))
        os.makedirs(sd)
        kind = lk.get("kind", "pie")
        for k in ("script", "version_script"):
            if k in lk:
                shutil.copyfile(os.path.join(self.repo, self.root, lk[k]), os.path.join(sd, os.path.basename(lk[k])))
        lb, pre, post, search, libs, sysin = lk.get("libs"), [], [], [], [], []
        if lb and "system" in lb:
            # the shared libraries of env.json packages: their content is the input, mounted at /syslib under their own file names
            yd = os.path.join(ws, "syslib")
            os.makedirs(yd)
            for p in lb["system"]:
                if not os.path.isfile(p):
                    raise BuildRed({"stage": "ld:" + self.root, "exit": None, "stderr": ["link.libs.system: %s is not a file of this machine (env.json packages)" % p]})
                shutil.copyfile(os.path.realpath(p), os.path.join(yd, os.path.basename(p)))
            sysin = [{"at": "/syslib", "tree": cas.ingest(yd, self.store)}]
            search, post = ["-L/syslib"], ["/syslib/" + os.path.basename(p) for p in lb["system"]]
        elif lb:
            at = self.ext[lb["external"]]["at"]
            pre, post = [at + "/" + p for p in lb["startfiles"]], [at + "/" + p for p in lb["endfiles"]]
            search, libs = ["-L" + at + "/" + d for d in lb["dirs"]], ["-l" + n for n in lb["names"]]
        vs = ["--version-script=/ld/" + os.path.basename(lk["version_script"])] if "version_script" in lk else []
        sc = ["-T", "/ld/" + os.path.basename(lk["script"])] if "script" in lk else []
        argv = [ld] + LD_KIND[kind] + lk["flags"] + sc + vs + ["-o", "/out/" + lk["output"], "-Map=/out/" + lk["output"] + ".map"] + pre + search + [
            "--whole-archive"] + args + ["--no-whole-archive"] + (["--start-group"] + libs + ["--end-group"] if libs else []) + post
        objs_tree, ld_tree = cas.ingest(od, self.store), cas.ingest(sd, self.store)
        doc = {"format": "fnvnx-stage/1", "stage": "ld:" + self.root, "argv": argv, "cwd": "/", "class": "build", "tool": "ld",
               "env": dict(unitstage.ENV, PATH=os.path.dirname(ld)), "outputs": ["/out"],
               "inputs": sorted(rt["inputs"] + [{"at": "/objs", "tree": objs_tree}, {"at": "/ld", "tree": ld_tree}] + sysin, key=lambda i: i["at"])}
        return self.stage(doc)[1]["outputs"]["/out"], objs_tree, [{"at": "/ld", "tree": ld_tree}] + sysin

    def archive_members(self, ent, name, out):
        """The archive members the linker pulled (from its map), each with the member's bytes read from the archive (a file of an external or of an
        env.json package), kept beside the result at out/archive_members/<archive>/<member>: the objects of the link facts, judged like every other
        object. -> [{archive, archive_sha256, member, object, sha256}]."""
        text = open(cas._obj(self.store, ent[name + ".map"]), errors="replace").read()
        res, cache = [], {}
        for ar, mem in map_members(text):
            host = self.hostpath(ar)
            if host not in cache:
                cache[host] = ({n: b for n, _, b in relocs.ar_members(host)}, hashlib.sha256(open(host, "rb").read()).hexdigest())
            members, ash = cache[host]
            dest = os.path.join(out, "archive_members", ar.strip("/").replace("/", "__"), mem)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(members[mem])
            res.append({"archive": ar, "archive_sha256": ash, "member": mem, "object": os.path.abspath(dest), "sha256": hashlib.sha256(members[mem]).hexdigest()})
        return res

    def ldcheck(self, ws, elf_tree, objs_tree, extra):
        """Stage `ldcheck:<root>`: link/ldcheck.py (python of the python runtime, a pure tool like `generated`) judges the linked ELF from ELF
        symbol tables, writes the crt0 header words and the gate file `ld`. -> tree of /out (the final ELF, ld.json, ld.ids)."""
        lk = self.rec["link"]
        name = lk["output"]
        _, py = self.cached_rt("python", lambda: runtime.build(self.store, sys.executable, []))
        tmp = tempfile.mkdtemp(dir=ws)
        ins = []
        for at, owner in (("/tool", LD_TOOLS[0]), ("/contract", LD_TOOLS[1])):
            ins.append({"at": at, "tree": self.ingest_files(tmp, at[1:], [f for f in self.files if under(f, owner)], owner)})
        ins += [{"at": "/linked", "tree": elf_tree}, {"at": "/objs", "tree": objs_tree}] + extra + [{"at": e["at"], "tree": e["tree"]} for e in self.ext.values()]
        env = {k: v for k, v in py["env"].items() if k != "PYTHONNOUSERSITE"}
        env.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(py["python"])})
        doc = {"format": "fnvnx-stage/1", "stage": "ldcheck:" + self.root, "class": "build", "tool": "python", "cwd": "/work",
               "argv": [py["python"], "-s", "-B", "/tool/ldcheck.py", "--elf", "/linked/" + name, "--objs", "/objs", "--out", "/out", "--name", name, "--kind", lk.get("kind", "pie")]
               + (["--version-script", "/ld/" + os.path.basename(lk["version_script"])] if "version_script" in lk else [])
               + ["--lib=/syslib/" + os.path.basename(p) for p in lk.get("libs", {}).get("system", [])],
               "env": env, "inputs": sorted(py["inputs"] + ins, key=lambda x: x["at"]), "outputs": ["/out"]}
        return self.stage(doc)[1]["outputs"]["/out"]


def emulation_of(flags):
    return next(t[2:] if t != "-m" else flags[i + 1] for i, t in enumerate(flags) if t == "-m" or (t.startswith("-m") and len(t) > 2))


def object_target(path):
    """(ELF class, e_machine) of an object file, from its header."""
    with open(path, "rb") as f:
        d = f.read(20)
    return d[4], int.from_bytes(d[18:20], "little")


def defined_mismatch(label, defined, objs):
    """Findings where the `defined` list of an objects.json (the global and weak symbols its build recorded) is not what the objects themselves
    define: OBJS [(unit, path)] are read again by link/ldcheck.py's own ELF reader (a second implementation of the symbol table walk). -> [str]."""
    want = {(d["object"], d["symbol"], d["bind"]) for d in defined}
    got = set()
    for u, p in objs:
        with open(p, "rb") as f:
            for n, bind, _, shndx, _ in ldcheck.syms_any(f.read(), 2):
                if bind in SBIND and shndx != 0:
                    got.add((u, n, SBIND[bind]))
    return ["%s: objects.json says %s defined by %s, the object does not" % (label, n, u) for u, n, _ in sorted(want - got)[:10]] + [
        "%s: object %s defines %s, objects.json does not list it" % (label, u, n) for u, n, _ in sorted(got - want)[:10]]


def map_members(text):
    """[(archive path, member name)] of the "Archive member included to satisfy reference by file (symbol)" section of a GNU ld map, in link order:
    the members the linker pulled out of archives (a long entry carries its reference on the next line)."""
    out, on = [], False
    for ln in text.splitlines():
        if ln.startswith("Archive member included"):
            on = True
        elif on and ln.startswith(("Discarded input", "Memory Configuration", "Allocating common")):
            break
        elif on and ln and not ln[0].isspace():
            m = re.match(r"(\S+?)\(([^()\s]+)\)(?:\s|$)", ln)
            if m:
                out.append((m.group(1), m.group(2)))
    return out


def objects_digest(res):
    """sha256 of the CONTENT of an objects.json dict: its fields without the machine paths (object, plugin, ELF, constants files: their
    sha256 stands for them) and without the cache counters, so two builds of the same sources give the same digest."""
    keep = {k: res.get(k) for k in ("format", "root", "status", "defined", "undefined", "units", "stage_keys", "pp_keys", "failure")}
    keep["link_roots"] = [{"root": x["root"], "sha256": x["sha256"]} for x in res["link_roots"]]
    keep["objects"] = [{k: o[k] for k in ("unit", "sha256", "os_layer")} for o in res["objects"]]
    keep["plugins"] = [{k: p[k] for k in ("name", "sha256")} for p in res["plugins"]]
    keep["link_archives"] = [{k: m[k] for k in ("archive", "member", "sha256")} for m in res.get("link_archives", [])]
    keep["elf"] = res["elf"] and res["elf"]["sha256"]
    for k in ("constants", "contract_names"):
        keep[k] = res[k] and hashlib.sha256(open(res[k], "rb").read()).hexdigest()
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()


def host_compilers(rec, cache):
    """Host paths of the recipe's (cc, cxx): /usr/bin/NAME, or the paths inside the external compiler tree, checked out (by exact commit,
    from the bare fetch the build made in CACHE/external) into CACHE/exttree/<commit>. For gates that run the compiler on the host
    (objstub's terminator plugin); the build itself runs it in the sandbox."""
    c = rec["compiler"]
    if "external" not in c:
        return "/usr/bin/" + c["cc"], "/usr/bin/" + c["cxx"]
    e = next(x for x in rec["external"] if x["name"] == c["external"])
    dest = os.path.join(cache, "exttree", e["commit"])
    if not os.path.isdir(dest):
        tmp = "%s.tmp%d-%s" % (dest, os.getpid(), os.urandom(4).hex())
        os.makedirs(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp + ".idx")
        bare = os.path.join(cache, "external", e["commit"])
        subprocess.run(["git", "--git-dir", bare, "read-tree", e["commit"]], env=env, check=True, capture_output=True)
        subprocess.run(["git", "--git-dir", bare, "--work-tree", tmp, "checkout-index", "-a", "-f"], env=env, check=True, capture_output=True)
        os.unlink(tmp + ".idx")
        try:
            os.rename(tmp, dest)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
    return os.path.join(dest, c["cc"]), os.path.join(dest, c["cxx"])


def build(repo, root, store, out, jobs=4, ext=None):
    """Build product ROOT of the checkout REPO into STORE; write OUT/objects.json and the files it names. EXT: bare cache dir of
    externals (default STORE/external). -> the objects dict; raises RecipeError for an invalid recipe."""
    spec = root
    j = Job(repo, spec, store, jobs, ext or os.path.join(store, "external"))
    root = j.root
    with open(os.path.join(repo, j.roots[root]["recipe"])) as f:
        full = json.load(f)
    if j.variant and j.variant not in variant_names(full):
        raise RecipeError(["root %s has no variant %r" % (root, j.variant)])
    errs = check(full, root, j.files, j.roots, load_pin(repo), repo)
    if errs:
        raise RecipeError(errs)
    units = units_of(j.rec, root, j.files)
    res = {"format": OBJ_FORMAT, "root": spec, "objects": [], "elf": None, "link_roots": [], "constants": None, "contract_names": None, "defined": [], "undefined": [], "plugins": [],
           "link_archives": [], "stage_keys": [], "status": "green"}
    ws = tempfile.mkdtemp(prefix="recipe-")
    os.makedirs(out, exist_ok=True)
    try:
        _copy(repo, [f for f in j.files if under(f, root)], root, os.path.join(ws, "root"))
        j.externals(ws)
        j.generate(ws)
        objs, und = j.compile(ws, units), set()
        for n, t, _ in j.built:
            ent = cas.manifest(t, store)["entries"][n + ".so"]
            dest = os.path.join(out, "plugins", n + ".so")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(cas._obj(store, ent), dest)
            res["plugins"].append({"name": n, "sha256": ent["h"], "path": os.path.abspath(dest)})
        for u in units:
            dest = os.path.join(out, "obj", u + ".o")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            ent = cas.manifest(objs[u], store)["entries"]["unit.o"]
            shutil.copyfile(cas._obj(store, ent), dest)
            d, x = symbols(dest, u)
            res["defined"] += d
            und |= x
            res["objects"].append({"unit": u, "object": os.path.abspath(dest), "sha256": ent["h"], "os_layer": u in j.rec.get("os_layer", [])})
        res.update(defined=sorted(res["defined"], key=lambda d: (d["symbol"], d["object"])), undefined=sorted(und))
        if j.rec.get("link"):
            lk = j.rec["link"]
            name = lk["output"]
            groups = [("00-own", [(u, o["object"]) for u, o in zip(units, res["objects"])])]
            res["link_roots"], defs = [], {}
            for n, d in enumerate(lk.get("roots", [])):
                try:
                    r = build(repo, d, store, os.path.join(ws, "in", d.replace("/", "__")), jobs, j.extcache)
                except RecipeError as x:
                    raise BuildRed({"stage": "ld:" + root, "exit": None, "stderr": ["input root %s: invalid recipe: %s" % (d, x)][:20]})
                j.keys |= set(r["stage_keys"])
                j.run, j.hit = j.run + r["stages"]["run"], j.hit + r["stages"]["hit"]
                if r["status"] == "red":
                    f = r["failure"]
                    raise BuildRed({"stage": "ld:" + root, "exit": f["exit"], "stderr": ["input root %s is red: stage %s" % (d, f["stage"])] + f["stderr"][-18:]})
                kept = []     # the input root's objects stay beside this root's result (its own build directory is scratch)
                for o in r["objects"]:
                    dest = os.path.join(out, "link_roots", d.replace("/", "__"), o["unit"] + ".o")
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.copyfile(o["object"], dest)
                    kept.append({"unit": o["unit"], "object": os.path.abspath(dest), "sha256": o["sha256"]})
                res["link_roots"].append({"root": d, "sha256": objects_digest(r), "objects": kept})
                groups.append(("%02d-%s" % (n + 1, d.replace("/", "__")), [(o["unit"], o["object"]) for o in kept]))
                defs[groups[-1][0]] = r["defined"]
            for lab, objs in groups:
                bad = defined_mismatch(lab, res["defined"] if lab == "00-own" else defs[lab], objs)
                if bad:
                    raise BuildRed({"stage": "ld:" + root, "exit": None, "stderr": bad})
            for lab, objs in groups:      # every linked object has the target of the link (ELF class, e_machine), read from its header
                bad = [u for u, p in objs if object_target(p) != TARGETS[emulation_of(lk["flags"])]]
                if bad:
                    raise BuildRed({"stage": "ld:" + root, "exit": None, "stderr": ["%s: object %s has target (class, e_machine) %s, the link %s is %s" % (
                        lab, u, object_target(dict(objs)[u]), emulation_of(lk["flags"]), TARGETS[emulation_of(lk["flags"])]) for u in bad[:20]]})
            elf_tree, objs_tree, extra = j.link(ws, groups)
            res["link_archives"] = j.archive_members(cas.manifest(elf_tree, store)["entries"], name, out)
            fin = j.ldcheck(ws, elf_tree, objs_tree, extra)
            ent = cas.manifest(fin, store)["entries"]
            shutil.copyfile(cas._obj(store, ent[name]), os.path.join(out, name))
            res["elf"] = {"path": os.path.abspath(os.path.join(out, name)), "sha256": ent[name]["h"]}
            res["ld_gate"] = {}
            for f in ("ld.json", "ld.ids"):
                shutil.copyfile(cas._obj(store, ent[f]), os.path.join(out, f))
                res["ld_gate"][f] = {"path": os.path.abspath(os.path.join(out, f)), "sha256": ent[f]["h"]}
        for key in ("constants", "contract_names"):
            if j.rec.get(key):
                shutil.copyfile(os.path.join(ws, "root", j.rec[key]), os.path.join(out, key + ".tsv"))
                res[key] = os.path.abspath(os.path.join(out, key + ".tsv"))
    except BuildRed as r:
        res.update(status="red", failure=r.args[0], failed_units=r.args[0].get("units", []))
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    res.update(stage_keys=sorted(j.keys), stages={"run": j.run, "hit": j.hit}, units=units, pp_keys=dict(sorted(j.ppkeys.items())))
    with open(os.path.join(out, "objects.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    return res


def main(argv):
    opt = dict(zip(argv[1::2], argv[2::2]))
    try:
        repo = "."
        if argv[:1] == ["check"]:
            roots = load_admit(repo)
            errs = check(load_recipe(repo, roots, opt["--root"]), opt["--root"], tracked(repo), roots, load_pin(repo), repo)
            print("\n".join(errs) or "recipe valid")
            return 2 if errs else 0
        res = build(repo, opt["--root"], opt["--store"], opt["--out"], int(opt.get("--jobs", 4)), opt.get("--ext"))
        print("%s: %d objects, stages run %d hit %d" % (res["status"], len(res["objects"]), res["stages"]["run"], res["stages"]["hit"]))
        if res["status"] == "red":
            print(json.dumps(res["failure"], indent=1))
        return 0 if res["status"] == "green" else 2
    except RecipeError as e:
        print("\n".join(e.errors), file=sys.stderr)
        return 2
    except (KeyError, OSError, ValueError, struct.error, cas.CasError, sandbox.SbxError, runtime.RtError, unitstage.UnitError) as e:
        print("recipe: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
