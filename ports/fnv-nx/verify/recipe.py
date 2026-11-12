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
import envcheck  # noqa: E402
import runtime  # noqa: E402
import sandbox  # noqa: E402
import toolflags  # noqa: E402
import unitstage  # noqa: E402

FORMAT, OBJ_FORMAT = "fnvnx-recipe/1", "fnvnx-objects/1"
TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
ENV_JSON = TREE + "/verify/env.json"   # the declared environment of the checkout (l1.py checks the same file)
KEYS = {"format", "compiler", "flags", "include", "units", "generated", "os_layer", "game", "constants", "contract_names", "link", "external",
        "plugins", "plugin_cxx", "unit_flags", "unit_facts"}
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
# Per-unit flags (key `unit_flags`): the only one is the pi plugin's fpfast, derived from the unit's /fp:fast fact in `unit_facts` (RECIPE.md).
FPFAST_FLAG = "-fplugin-arg-fnvnx_pi-fpfast"
FASTMATH_FACT = "/fp:fast"
# Flags of the game's C++ that fix its semantics (compile.sh); fnvnx_sdd needs -DFNVNX_SDD=1 and -fno-devirtualize.
SEMANTICS_FLAGS = ("-mabi=ilp32", "-fms-extensions", "-fshort-wchar", "-frounding-math", "-fsigned-char", "-fno-strict-aliasing", "-fwrapv",
                   "-fno-delete-null-pointer-checks", "-fcheck-new", "-fno-sized-deallocation", "-fno-lifetime-dse", "-ffp-contract=off",
                   "-DFNVNX_SDD=1", "-fno-devirtualize")
WERROR_ATTR = "-Werror=attributes"   # required in flags.c and flags.cxx of every guest root
EXT_KEYS = {"name", "repo", "ref", "commit", "tree_sha256", "at"}
SANDBOX_PATHS = ("/src", "/inc", "/in", "/out", "/tool", "/objs", "/ld", "/work", "/proc", "/dev", "/tmp", "/oldroot")
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


class EnvBlocked(RecipeError):
    """The machine does not match the env.json of the checkout: no stage runs and nothing is stored (a result computed in the wrong
    environment is not a result: a stage that failed for a tool that was not installed yet must not stay cached red)."""


_ENV_OK = set()   # sha256 of the env.json files this process already found matching (an install between processes is seen by the next one)


def env_blocked(repo):
    """Raise EnvBlocked unless this machine matches repo/verify/env.json (envcheck.py check; a missing file is a mismatch, as in l1.py)."""
    p = os.path.join(repo, ENV_JSON)
    if not os.path.exists(p):
        raise EnvBlocked(["env: " + ENV_JSON + ": want a declared environment, have none"])
    raw = open(p, "rb").read()
    h = hashlib.sha256(raw).hexdigest()
    if h in _ENV_OK:
        return
    bad = envcheck.check(json.loads(raw))
    if bad:
        raise EnvBlocked(list(bad))
    _ENV_OK.add(h)


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


def load_recipe(repo, roots, root):
    with open(os.path.join(repo, roots[root]["recipe"])) as f:
        return json.load(f)


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
    bad += ["compiler.%s is %r, pinned %r" % (k, c[k], pin.get(k)) for k in ("cc", "cxx") if c[k] != pin.get(k)]
    fl = rec.get("flags") if isinstance(rec.get("flags"), dict) else {}
    # an attribute that neither the compiler nor a loaded plugin registers is otherwise dropped with a warning: a marker a rule relies on vanishes
    bad += ["flags.%s lacks %s" % (k, WERROR_ATTR) for k in ("c", "cxx") if WERROR_ATTR not in (fl.get(k) if isinstance(fl.get(k), list) else [])]
    return ["guest root %s: %s" % (root, b) for b in bad]


def check(rec, root, files, roots, pin=None):
    """Every problem of recipe REC of product root ROOT; roots {path: root entry}; PIN = load_pin(). -> [str] (empty = valid)."""
    if not isinstance(rec, dict):
        return ["recipe is not a JSON object"]
    err = ["unknown key %r" % k for k in sorted(set(rec) - KEYS)]
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
        if not (isinstance(lk, dict) and set(lk) == {"script", "output", "flags"} and isinstance(lk["flags"], list)
                and all(isinstance(t, str) for t in lk["flags"]) and isinstance(lk["output"], str)
                and re.fullmatch(r"[A-Za-z0-9._-]+\.elf", lk["output"]) and rel_ok(lk["script"]) and lk["script"] in mine):
            err.append("link must be {script: tracked file of the root, output: NAME.elf, flags: [..]}")
        else:
            err += ["link.flags: %s is in the refusal set R" % t for t in refused(lk["flags"])]
    err += guest_errors(rec, root, roots, pin)
    err += plugin_errors(rec, root, files, roots, units, names)
    err += unit_flag_errors(rec, root, roots, units, mine, seen)
    return err


def unit_flag_errors(rec, root, roots, units, mine, gen):
    """Shape of `unit_flags` ({game C++ unit: [FPFAST_FLAG]}) and `unit_facts` (a tracked file or generated output: units.json, {"units": [{"source",
    "flags"}]}). The values are checked against the facts by fpfast_errors at build time (the file may be a generated output). A native guest root
    that declares `game` must declare `unit_facts`: its units' /fp:fast facts are then compared, so no unit can lose or gain the flag silently."""
    uf, fa, gm = rec.get("unit_flags"), rec.get("unit_facts"), rec.get("game")
    err = []
    if fa is not None and not (isinstance(fa, str) and rel_ok(fa) and (fa in mine or fa in gen)):
        err.append("unit_facts %r is neither a tracked file of the root nor a generated output" % (fa,))
    if uf is not None:
        if not (isinstance(uf, dict) and all(isinstance(u, str) and isinstance(v, list) and v and all(isinstance(f, str) for f in v) for u, v in uf.items())):
            err.append("unit_flags must be {unit: [flag, ...]} (non-empty lists)")
        else:
            pre = gm.strip("/") + "/" if isinstance(gm, str) else None
            for u, v in sorted(uf.items()):
                if not (u in units and u.endswith(CXX_EXT) and pre and u.startswith(pre)):
                    err.append("unit_flags: %r is not a C++ unit under game" % u)
                err += ["unit_flags[%s]: %r is not a unit flag (only %s)" % (u, f, FPFAST_FLAG) for f in v if f != FPFAST_FLAG]
            if fa is None:
                err.append("unit_flags without unit_facts: a per-unit flag is judged against the unit's fact")
            if uf and "fnvnx_pi" not in [x.get("name") for x in rec.get("plugins", []) if isinstance(x, dict)]:
                err.append("unit_flags need the plugin fnvnx_pi, which interprets %s" % FPFAST_FLAG)
    ent = roots.get(root, {})
    if ent.get("target") == "guest" and ent.get("cxx_origin") == "native" and gm is not None and fa is None:
        err.append("a native guest root with `game` needs unit_facts (the units' /fp:fast facts)")
    return err


def fpfast_errors(rec, facts_text):
    """The per-unit rule: for every C++ unit under `game`, FPFAST_FLAG is in unit_flags iff the unit's entry in unit_facts (a "source" equal to the
    root-relative unit path) lists FASTMATH_FACT; a unit with no entry has no such fact (compile.sh: no entry, no flag). A fact for a source that is
    not a C++ unit under game is an error. -> [str]. Assumption: unit_facts is produced from the exe/decomp facts by the generator, not by hand."""
    try:
        ent = json.loads(facts_text)["units"]
        fast = {e["source"] for e in ent if FASTMATH_FACT in (e.get("flags") or [])}
    except (ValueError, KeyError, TypeError, AttributeError) as x:
        return ["unit_facts is not units.json ({\"units\": [{\"source\", \"flags\"}]}): %s" % str(x)[:100]]
    gm = rec.get("game")
    if not isinstance(gm, str):
        return []
    pre = gm.strip("/") + "/"
    uf = rec.get("unit_flags", {})
    err = ["unit %s: /fp:fast fact without %s" % (u, FPFAST_FLAG) for u in sorted(fast) if FPFAST_FLAG not in uf.get(u, [])]
    err += ["unit %s: %s without a /fp:fast fact" % (u, FPFAST_FLAG) for u in sorted(uf) if FPFAST_FLAG in uf[u] and u not in fast]
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
    def __init__(self, repo, root, store, jobs, ext):
        self.repo, self.root, self.store, self.jobs, self.extcache = repo, root, store, jobs, ext
        self.roots, self.files = load_admit(repo), tracked(repo)
        self.rec = load_recipe(repo, self.roots, root)
        self.keys, self.run, self.hit, self.ext, self.ppkeys, self.built = set(), 0, 0, {}, {}, []
        os.makedirs(os.path.join(store, "rt"), exist_ok=True)

    def stage(self, doc):
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
            builds[k].unit_cc_flags = rec.get("unit_flags", {}) if k == "cxx" else {}   # the pi plugin's fpfast, per unit (CC stage argv, so in its key)
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
            for u, e in ex.map(one, units):   # every unit is judged, PP and CC alike: a failed unit never stops another
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
        if failed:   # in unit order (UNITS is sorted), so the first failure and the list are deterministic
            fl = [{"unit": u, "stage": failed[u]["stage"], "exit": failed[u]["exit"], "stderr": list(failed[u]["stderr"])[-20:]} for u in units if u in failed]
            raise BuildRed(dict(fl[0], failed_units=fl, done=sorted(res)))
        return res

    def link(self, ws, objs):
        lk, cc = self.rec["link"], self.drivers()[0]
        pn = subprocess.run([self.hostpath(cc), "-print-prog-name=ld"], capture_output=True, text=True).stdout.strip()
        ld = posixpath.normpath(pn)
        if "external" in self.rec["compiler"]:
            e = self.ext[self.rec["compiler"]["external"]]
            ld = e["at"] + ld[len(e["host"]):] if ld.startswith(e["host"] + "/") else ld
            rt = self.toolrt([cc])[1]
        else:
            rt = self.cached_rt("tool-ld-" + hashlib.sha256(repr((ld, sorted((e["at"], e["tree"]) for e in self.ext.values()))).encode()).hexdigest()[:16], lambda: self.plus(runtime.build_tool(self.store, [ld], [])))[1]
        od, sd = os.path.join(ws, "objs"), os.path.join(ws, "ld")
        for u, o in objs.items():
            os.makedirs(os.path.dirname(os.path.join(od, u + ".o")), exist_ok=True)
            shutil.copyfile(cas._obj(self.store, cas.manifest(o, self.store)["entries"]["unit.o"]), os.path.join(od, u + ".o"))
        os.makedirs(sd)
        shutil.copyfile(os.path.join(self.repo, self.root, lk["script"]), os.path.join(sd, os.path.basename(lk["script"])))
        argv = [ld, "--build-id=none", "-T", "/ld/" + os.path.basename(lk["script"])] + lk["flags"] + ["-o", "/out/" + lk["output"]] + [
            "/objs/%s.o" % u for u in sorted(objs)]
        doc = {"format": "fnvnx-stage/1", "stage": "ld:" + self.root, "argv": argv, "cwd": "/", "class": "build", "tool": "ld",
               "env": dict(unitstage.ENV, PATH=os.path.dirname(ld)), "outputs": ["/out"],
               "inputs": sorted(rt["inputs"] + [{"at": "/objs", "tree": cas.ingest(od, self.store)},
                                                {"at": "/ld", "tree": cas.ingest(sd, self.store)}], key=lambda i: i["at"])}
        return self.stage(doc)[1]["outputs"]["/out"]


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
    env_blocked(repo)   # BEFORE any stage: nothing below runs or stores on a machine that is not the declared one
    j = Job(repo, root, store, jobs, ext or os.path.join(store, "external"))
    errs = check(j.rec, root, j.files, j.roots, load_pin(repo))
    if errs:
        raise RecipeError(errs)
    units = units_of(j.rec, root, j.files)
    res = {"format": OBJ_FORMAT, "root": root, "objects": [], "elf": None, "constants": None, "contract_names": None, "defined": [], "undefined": [], "plugins": [],
           "stage_keys": [], "status": "green"}
    ws = tempfile.mkdtemp(prefix="recipe-")
    os.makedirs(out, exist_ok=True)
    try:
        _copy(repo, [f for f in j.files if under(f, root)], root, os.path.join(ws, "root"))
        j.externals(ws)
        j.generate(ws)
        if j.rec.get("unit_facts"):
            fe = fpfast_errors(j.rec, open(os.path.join(ws, "root", j.rec["unit_facts"])).read())
            if fe:
                raise BuildRed({"stage": "unit_facts", "exit": None, "stderr": fe[:20]})
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
            name = j.rec["link"]["output"]
            ent = cas.manifest(j.link(ws, objs), store)["entries"][name]
            shutil.copyfile(cas._obj(store, ent), os.path.join(out, name))
            res["elf"] = {"path": os.path.abspath(os.path.join(out, name)), "sha256": ent["h"]}
        for key in ("constants", "contract_names"):
            if j.rec.get(key):
                shutil.copyfile(os.path.join(ws, "root", j.rec[key]), os.path.join(out, key + ".tsv"))
                res[key] = os.path.abspath(os.path.join(out, key + ".tsv"))
    except BuildRed as r:
        fl = r.args[0].get("failed_units", [])
        res.update(status="red", failure={k: v for k, v in r.args[0].items() if k in ("stage", "exit", "stderr", "done")}, failed_units=fl)
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
            errs = check(load_recipe(repo, roots, opt["--root"]), opt["--root"], tracked(repo), roots, load_pin(repo))
            print("\n".join(errs) or "recipe valid")
            return 2 if errs else 0
        res = build(repo, opt["--root"], opt["--store"], opt["--out"], int(opt.get("--jobs", 4)), opt.get("--ext"))
        print("%s: %d objects, stages run %d hit %d" % (res["status"], len(res["objects"]), res["stages"]["run"], res["stages"]["hit"]))
        if res["status"] == "red":
            print(json.dumps(res["failure"], indent=1))
        return 0 if res["status"] == "green" else 2
    except EnvBlocked as e:
        print("\n".join(["blocked: env"] + e.errors))
        return 4
    except RecipeError as e:
        print("\n".join(e.errors), file=sys.stderr)
        return 2
    except (KeyError, OSError, ValueError, struct.error, cas.CasError, sandbox.SbxError, runtime.RtError, unitstage.UnitError) as e:
        print("recipe: error: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
