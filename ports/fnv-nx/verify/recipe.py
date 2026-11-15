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
Assumptions: the compiler and ld are deterministic given the flags of unitstage/toolflags (A-env); the sandbox leaves the channels of (ASM-038, ASM-039)
  sandbox.OPEN_CHANNELS open (see gate_determinism.py for which of them two cold builds expose)."""
import argparse
import concurrent.futures
import errno
import hashlib
import json
import os
import pathlib
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cas  # noqa: E402
import elffacts  # noqa: E402
import gate_admit  # noqa: E402
import envcheck  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "link"))
import relocs  # noqa: E402
import undefset  # noqa: E402
from ldcheck import syms_any as ldcheck_syms  # noqa: E402
import ldcheck  # noqa: E402
import runtime  # noqa: E402
import sandbox  # noqa: E402
import toolflags  # noqa: E402
import unitstage  # noqa: E402
sandbox_src = unitstage.sandbox_src   # the one function that composes a unit's sandbox source path (the unit a plugin reports)

FORMAT, OBJ_FORMAT = "fnvnx-recipe/1", "fnvnx-objects/1"
TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
ENV_JSON = TREE + "/verify/env.json"   # the declared environment of the checkout (l1.py checks the same file)

def _plugin_cxx():
    """The host driver that builds the plugins, DERIVED from env.json: the pinned package `g++-N-x86-64-linux-gnu` (exactly one) provides
    `/usr/bin/x86_64-linux-gnu-g++-N`, the GCC of the same major version as the guest compiler (13). Nothing hand-typed in a build.json."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")) as f:
        pk = [m.group(1) for k in json.load(f)["packages"] for m in [re.fullmatch(r"g\+\+-(\d+)-x86-64-linux-gnu", k)] if m]
    if len(pk) != 1:
        raise RuntimeError("env.json must pin exactly one g++-N-x86-64-linux-gnu, has %s" % pk)
    return "x86_64-linux-gnu-g++-" + pk[0]


PLUGIN_CXX = _plugin_cxx()
KEYS = {"format", "compiler", "flags", "include", "units", "generated", "os_layer", "game", "constants", "contract_names", "link", "external",
        "plugins", "variants", "profile", "outputs", "uses", "include_uses", "sources", "error_abi", "empty_handlers", "external_errors", "tests", "run"}
VARIANT_KEYS = {"compiler", "flags", "link", "flags_add", "defines_add"}   # the keys a variant may replace (whole: a sanitizer variant names its own link.libs.system) and its two deltas
VARIANT_DELTAS = {"flags_add", "defines_add"}   # appended to the base flags of the root (RECIPE.md, "Profiles")
PROFILES = ("guest", "host")
PLUGIN_FIXED = {"source": "PLUGIN_SOURCES", "inputs": "plugin_inputs, derived from the source", "args": "PLUGIN_ARGS"}   # what recipe.py fixes for a plugin: a root entry names only the plugin
GUEST_LINK = TREE + "/link/guest.json"   # the guest link profile: {"script": path inside TREE, "libs": {LIBS_KEYS}}, one fact for every guest link (RECIPE.md, Link)
GUEST_PIN = TREE + "/verify/guest_compiler.json"   # the game's compiler: {"format", "external": {EXT_KEYS}, "cc", "cxx"} (RECIPE.md, "Guest roots")
GUEST_TARGETS = ("guest", "host")
# The plugins every product root with native or decomp C++ loads, their only admitted sources and their fixed arguments (RECIPE.md,
# "Plugins"). The arguments are the MSVC semantics (evaluation order of arguments, objects, operators, assignments; enum conversion
# an error as for cl). The other values the plugins accept (ltr, first, cpp17) exist for A/B measurements and are no product.
# The plugin fnvnx_order has no memory-ordering option: every access to shared memory is ordered (x86 TSO), whatever the arguments.
# The set and the LOAD ORDER of compile.sh (fnv-l2-source): ftoi, order, sdd, pi, dispatch, addr, names, layout. A plugin that includes another source
# (layout includes names' .cc) is mounted with it: plugin_inputs() derives the set from the #include closure of the source.
_T = TREE + "/toolchain/"
PLUGIN_SOURCES = {"fnvnx_ftoi": _T + "ftoi/fnvnx_ftoi.cc", "fnvnx_order": _T + "order/fnvnx_order.cc", "fnvnx_sdd": _T + "sdd/fnvnx_sdd.cc",
                  "fnvnx_pi": _T + "pi/fnvnx_pi.cc", "fnvnx_dispatch": _T + "dispatch/fnvnx_dispatch.cc", "fnvnx_addr": _T + "addr/fnvnx_addr.cc",
                  "fnvnx_names": _T + "names/fnvnx_names.cc", "fnvnx_layout": _T + "layout/fnvnx_layout.cc"}
PLUGIN_ARGS = {n: {} for n in PLUGIN_SOURCES}
ANALYSIS_PLUGIN = ("objstub_plugin", TREE + "/verify/objstub_plugin.cc")   # the gate objstub's plugin (RECIPE.md "Plugins"): built for every root with C/C++ units, loaded by no compile stage
PLUGIN_ARGS["fnvnx_ftoi"] = {"mode": "native"}
# fnvnx_ftoi is one plugin with two modes, chosen by the root's cxx_origin (no free choice): native C++ takes the MSVC rewrite, lifted C++ has
# no rewrite and every float -> integer conversion outside an fnvnx_f2i function is an error. Both write their report (gate fpexact) to
# PLUGIN_OUT of the CC stage's own /out; the recipe passes it, a build.json does not declare it.
PLUGIN_OUT = {"fnvnx_ftoi": "/out/unit.f2i.json"}
PLUGIN_ARGS["fnvnx_order"] = {"args": "rtl", "object": "msvc", "operator": "rtl", "assign": "msvc"}


_QINC = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)


def plugin_inputs(source, files, roots, repo="."):
    """The tracked files that the plugin SOURCE includes by `#include "..."`, transitively, each resolved against the directory of the including file
    (a header the plugin stage mounts at its repo path) -> sorted [repo path], SOURCE not in it. Angle includes, and quoted ones that name no tracked file beside the includer, come from the compiler's own
    plugin headers. Rule: the closure lies inside the admitted root that holds SOURCE (a tracked file of a declared root); a quoted include that names a tracked file of another root is ValueError (a plugin reaches another root's files only by a declared `uses`, which a plugin has none of)."""
    held, own = set(files), None
    for r in roots:
        if under(source, r) and (own is None or len(r) > len(own)):
            own = r
    if own is None:
        raise ValueError("plugin source %s lies in no admitted root" % source)
    out, todo = set(), [source]
    while todo:
        f = todo.pop()
        with open(os.path.join(repo, f), encoding="utf-8", errors="surrogateescape") as fh:
            text = fh.read()
        for m in _QINC.findall(text):
            q = posixpath.normpath(posixpath.join(posixpath.dirname(f), m))
            if q not in held:
                continue   # no tracked file beside the includer: found on the compiler's include path (the plugin headers), or missing, which the compile stage reports
            if not under(q, own):
                raise ValueError("%s includes %r: %s lies outside the root %s of the plugin" % (f, m, q, own))
            if q != source and q not in out:
                out.add(q)
                todo.append(q)
    return sorted(out)


def plugin_files(repo="."):
    """Every plugin source of PLUGIN_SOURCES with the files it includes (plugin_inputs), in REPO -> sorted [repo path]."""
    files, roots = tracked(repo), load_admit(repo)
    return sorted(set(PLUGIN_SOURCES.values()) | {i for q in PLUGIN_SOURCES.values() for i in plugin_inputs(q, files, roots, repo)})


def plugin_decls(names, origin, files=(), roots=(), repo="."):
    """[{name, source, inputs, args}] of the root's `plugins` NAMES (each entry {"name": PLUGIN}) for a root whose declared `cxx_origin` is ORIGIN: source and
    arguments come from recipe.py alone (PLUGIN_SOURCES, plugin_args), the inputs are derived from the source (plugin_inputs). A name outside PLUGIN_SOURCES is
    refused by plugin_errors."""
    want = plugin_args(origin)
    return [{"name": p["name"], "source": PLUGIN_SOURCES[p["name"]], "inputs": plugin_inputs(PLUGIN_SOURCES[p["name"]], files, roots, repo), "args": want[p["name"]]} for p in names]


def origin_class(origin):
    """"lifted" iff the declared `cxx_origin` ORIGIN is a lifted declaration (lifted_origin: {"lifted_by", "pack"} or {"lifted_by", "stage"}), else "native" (also for no origin): the one
    place the origin's class is read (the class name "lifted" itself is accepted, for the CLI); plugin_args and semantics_set are functions of it."""
    return "lifted" if origin == "lifted" or lifted_origin(origin) else "native"


def semantics_set(origin):
    """The semantic flag set (before the per-language drop of semantics_for) of a root with `cxx_origin` ORIGIN: SEMANTICS_LIFTED for a lifted
    root, SEMANTICS_FLAGS for every other."""
    return SEMANTICS_LIFTED if origin_class(origin) == "lifted" else SEMANTICS_FLAGS


def plugin_args(origin):
    """{plugin name: its fixed arguments} for a root whose declared `cxx_origin` is ORIGIN ("native", a {"lifted_by", "pack"} dict, or None): the ONE
    place the expected arguments are decided, used by plugin_errors, the gates and the fold of L2. A function of the root's declared fact only, no second
    table: PLUGIN_ARGS, with fnvnx_ftoi's `mode` = the origin's class (lifted or native). No separate table for lifted roots."""
    out = {n: dict(a) for n, a in PLUGIN_ARGS.items()}
    out["fnvnx_ftoi"]["mode"] = origin_class(origin)
    return out


# The semantic flags of a guest root (data model, ABI, target, FP, aliasing, overflow, language model, calling convention): ONE ordered set. Its leading
# run (ABI, architecture, tuning, code model) is generated from the declared targets (targets.json, targets.semantic_prefix); the rest is the guest
# compiler pin's "flags"."semantics" (guest_compiler.json, next to this file). The RECIPE appends it to every guest compile stage, C, C++ and
# assembler alike, minus what the pinned driver itself says does not apply to that language (semantics_for); a guest build.json carries none of them.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "guest_compiler.json")) as _f:
    _sem = json.load(_f)["flags"]["semantics"]
import targets as _targets  # noqa: E402  (the declared targets: verify/targets.json)
SEMANTICS_FLAGS = tuple(_targets.semantic_prefix(_targets.load())) + tuple(_sem["common"])   # every guest root (native, decomp, lifted)
SEMANTICS_LIFTED = SEMANTICS_FLAGS + tuple(_sem["lifted_extra"])   # a root whose cxx_origin is a lifted declaration: the common set plus what the lifter's code is built under
SDD_DEFINE = "-DFNVNX_SDD=1"   # per root: the plugin fnvnx_sdd needs it (and -fno-devirtualize, which is in the semantic set of a guest root)
LANG_X = {"c": "c", "cxx": "c++", "asm": "assembler-with-cpp"}   # the driver's -x language of each kind of unit
# What a guest build.json may carry (per-root classes); every other -f/-m/... token comes from flags.semantics and is refused here. Warnings and
# diagnostics, defines, the language standard, the optimisation level (-O0..-O3, -Os, -Og: not -Ofast, which switches FP semantics), debug info,
# section splitting. -Wa, -Wl, -Wp are driver pass-throughs, not warnings. `--param` with the compiler's own garbage-collector knobs
# (ggc-min-expand, ggc-min-heapsize): they change when the compiler collects its memory, never the code it emits (the gcstress variant).
PER_ROOT = re.compile(r"--param|ggc-min-(?:expand|heapsize)=[0-9]+|-W(?![alp],)[^\s]*|-D[^\s]+|-std=[^\s]+|-O[0-3sg]|-g[^\s]*|-ffunction-sections|-fdata-sections")
# The options of every link, fixed by the rule (RECIPE.md "Link"): the specs of the gcc driver for the ILP32 PIE multilib, no gc-sections, no id.
AUX_FLAGS = ["-fstack-usage"]   # build --stack-usage 1: the CC stage also writes unit.su (documented: one line per function, its frame size and whether it is static); no code generation changes
LD_FIXED = ["--build-id=none", "-pie", "--no-dynamic-linker", "-z", "text", "-z", "now", "-z", "pack-relative-relocs", "-z", "max-page-size=0x1000",
            "-z", "common-page-size=0x1000"]
LD_KIND = {"pie": LD_FIXED, "exe": ["--build-id=none", "-pie", "-z", "text", "-z", "now"], "shared": ["--build-id=none", "-shared", "-z", "text", "-z", "now"]}
LD_TOOLS = (TREE + "/link", TREE + "/contract")   # tool roots of the ldcheck stage: link/ldcheck.py, and gatefile.py at /contract
WERROR_ATTR = "-Werror=attributes"   # required in flags.c and flags.cxx of every guest root
EXT_KEYS = {"name", "repo", "ref", "commit", "tree_sha256", "at"}
SANDBOX_PATHS = ("/src", "/inc", "/in", "/uses", "/out", "/tool", "/objs", "/ld", "/syslib", "/linked", "/contract", "/work", "/proc", "/dev", "/tmp", "/oldroot")
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


def pinned_modules(repo):
    """The python modules pinned in repo's env.json (python.modules), each as (distribution, version, installed_files_sha256, [top-level import
    names], [absolute paths to mount]): the digest is envcheck.wheel_digest and the paths are envcheck.record_tops, both from ONE set, the files the
    wheel's RECORD lists (envcheck.record_files: the package, its sibling *.libs, top-level .so/.pth files, the dist-info), read from the disk. A module
    whose installed files are not exactly its RECORD (a listed file missing or different, an unlisted file under its paths, no RECORD) blocks the
    environment (EnvBlocked). Every pinned module is mounted in the python runtime of the generated stages (all of the paths) and is named, with
    its digest, in their key (Job.generate)."""
    doc = json.load(open(os.path.join(repo, ENV_JSON)))
    out, bad = [], []
    for m, ver in sorted(doc["python"]["modules"].items()):
        try:
            bad += ["env: python module " + x for x in envcheck.record_problems(m)]
        except envcheck.RecordError as e:
            bad.append("env: python module %s" % e)
            continue
        d = envcheck.metadata.distribution(m)
        tops = set()
        for p in envcheck.record_files(m):
            f = pathlib.PurePosixPath(p)
            top = f.parts[0] if f.parts else ""
            if top.endswith((".dist-info", ".data", ".libs")):
                continue
            n = top.split(".")[0] if len(f.parts) == 1 else top
            if n.isidentifier():
                tops.add(n)
        paths = [os.path.realpath(str(d.locate_file(t))) for t in envcheck.record_tops(m)]
        out.append((m, ver, envcheck.wheel_digest(m), sorted(tops), paths))
    if bad:
        raise EnvBlocked(bad)
    return out


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


def norm_entry(r):
    """The one structure of an admit entry. `recipe` is a path (the root's recipe at its `target`), or {"guest": PATH, "host": PATH}: one recipe per
    target of the same units, the entry's `target` selects the base. The dict spelling parses to the string spelling plus one `variants` entry per
    other target, {TARGET: {"target": TARGET, "recipe": PATH}}: a per-target recipe is a variant keyed by its target, the same as the entry's own
    `variants` (a sanitizer variant, a variant with another target). A malformed dict parses to no recipe (gate admit names it)."""
    rc = r.get("recipe")
    if not isinstance(rc, dict):
        return r
    out, tg = dict(r), r.get("target")
    out["recipe"] = rc[tg] if isinstance(rc.get(tg), str) else ""
    vs = dict(r["variants"]) if isinstance(r.get("variants"), dict) else {}
    for k, p in rc.items():
        if k != tg and k in GUEST_TARGETS and isinstance(p, str):
            vs.setdefault(k, {"target": k, "recipe": p})
    if vs:
        out["variants"] = vs
    return out


def tree_of(roots, p):
    """The admitted root path or tree (admit.json `trees`) that holds repo path P, or None: a tool file is admitted iff it is a tracked file
    under a tree of an admitted tool root; membership is over the trees, not over the root paths only (link/ is a tree of the tool root contract). The trees of a
    root are gate_admit.trees: the one function the admit gate resolves them with."""
    return next((t for r in roots for t in gate_admit.trees(roots[r]) if under(p, t)), None)


def tool_file(roots, files, p):
    """P is a tracked file (of FILES, the repo paths) under a tree of an admitted root of class tool."""
    return isinstance(p, str) and p in files and any(roots[r]["class"] == "tool" and any(under(p, t) for t in gate_admit.trees(roots[r])) for r in roots)


def link_file(rec_file, root, mine, roots, files):
    """The repo path of a link file (script, version_script) named REC_FILE, or None: a tracked file of the root (root-relative), else a tracked
    file under a tree of an admitted tool root (repo-relative). One membership definition with the ldcheck tools. A generated output of the
    root (`link_generated`) is not a repo path: the link stage reads it from the root's build view."""
    if rel_ok(rec_file) and rec_file in mine:
        return root + "/" + rec_file
    return rec_file if rel_ok(rec_file) and tool_file(roots, files, rec_file) else None


def link_generated(rec_file, mine, seen):
    """REC_FILE is a generated output of the root (SEEN: the outputs of its generated and run stages, as for `constants`) and not a tracked file:
    the link script or version script a generator writes. The ld stage mounts it from the root's view, so its content is in the stage key."""
    return isinstance(rec_file, str) and rel_ok(rec_file) and rec_file in seen and rec_file not in mine


PROBE_SRC = "int main(void) { return 0; }\n"
GEN_LAUNCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "genlaunch.py")   # the launcher of every generated stage: creates the parent directory of each declared output (verify/genlaunch.py)
PROBE_FLAGS = ["-frandom-seed=guestlink", "-ffile-prefix-map=/probe=.", "-Werror=date-time"]   # the reproducibility flags every gcc stage carries (toolflags)


def parse_driver_link(text, at):
    """The link command of a driver's `-###` output TEXT (its last command line: the link) as the profile's libs: {dirs, names, startfiles, endfiles}, every path
    inside the external mounted at AT, normalised and relative to it (the driver names them through its own bin/..). startfiles: the .o files before the
    library group; endfiles: the .o files after it; dirs: the -L directories inside the external, in order, each once; names: the -l libraries in order of
    appearance (the group's included). A `-L` or `.o` outside the external is not a guest runtime file and is not taken."""
    line = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("COLLECT_GCC_OPTIONS=")][-1]   # each command of -### follows its COLLECT_GCC_OPTIONS line
    toks = [t.strip('"') for t in line.split()]
    rel = lambda t: posixpath.normpath(t)[len(at) + 1:] if posixpath.normpath(t).startswith(at + "/") else None  # noqa: E731
    out = {"dirs": [], "names": [], "startfiles": [], "endfiles": []}
    seen_end = False
    for t in toks:
        if t.startswith("-L") and rel(t[2:]):
            if rel(t[2:]) not in out["dirs"]:
                out["dirs"].append(rel(t[2:]))
        elif t.startswith("-l") and len(t) > 2:
            out["names"].append(t[2:])
        elif t == "--end-group":
            seen_end = True
        elif t.endswith(".o") and rel(t):
            out["endfiles" if seen_end else "startfiles"].append(rel(t))
    return out


def parse_host_link(text, mounts):
    """The link command of a host driver's `-###` output TEXT as {interp, startfiles, endfiles}: `-dynamic-linker`'s argument; the `.o` files that lie
    in one of MOUNTS (the driver's own library search dirs) before the first `-l` library are the start files, those after the last are the end files;
    paths normalised. Nothing else of the command is taken (the objects of the link are the recipe's, the libraries libs.system's)."""
    line = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("COLLECT_GCC_OPTIONS=")][-1]
    toks = [t.strip('"') for t in line.split()]
    ls = [i for i, t in enumerate(toks) if t.startswith("-l") and len(t) > 2]
    inm = lambda t: t.endswith(".o") and t.startswith("/") and any(under(posixpath.normpath(t), m) for m in mounts)  # noqa: E731
    out = {"interp": None, "startfiles": [], "endfiles": []}
    for i, t in enumerate(toks):
        if t == "-dynamic-linker" and i + 1 < len(toks):
            out["interp"] = toks[i + 1]
        elif inm(t):
            out["endfiles" if ls and i > ls[-1] else "startfiles"].append(posixpath.normpath(t))
    return out


def merge_inputs(base, extra, store, ws):
    """BASE inputs plus EXTRA ([{at, tree}]): an extra input that lies inside a base input's tree is copied into a copy of that tree at its relative path
    (two inputs never nest in a sandbox); the others are added. Sorted by `at`."""
    out, n = [dict(i) for i in base], 0
    for e in extra:
        host = max((i for i in out if under(e["at"], i["at"])), key=lambda i: len(i["at"]), default=None)   # the innermost input holding it (or at it)
        if host is None:
            out.append(dict(e))
            continue
        n += 1
        d = os.path.join(ws, "merge%d" % n)
        cas.materialize(host["tree"], store, d)
        sub = os.path.join(d, e["at"][len(host["at"]) + 1:])   # (the input's own root when the paths are equal)
        os.makedirs(sub, exist_ok=True)
        ov = os.path.join(ws, "overlay%d" % n)
        cas.materialize(e["tree"], store, ov)
        for f in os.listdir(ov):
            if os.path.lexists(os.path.join(sub, f)):
                os.unlink(os.path.join(sub, f))
            shutil.copyfile(os.path.join(ov, f), os.path.join(sub, f))
        host["tree"] = cas.ingest(d, store)
    return sorted(out, key=lambda i: i["at"])


def outside_files(text, at):
    """The `.o` files of the driver's link command TEXT that lie outside the runtime external mounted at AT, the program's own object (the driver's
    temporary /tmp/cc*.o) excepted: a start or end file the pinned runtime's driver names from elsewhere. Empty when every one is the runtime's."""
    line = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("COLLECT_GCC_OPTIONS=")][-1]
    toks = [posixpath.normpath(t.strip('"')) for t in line.split() if t.strip('"').endswith(".o") and t.strip('"').startswith("/")]
    return sorted(t for t in toks if not t.startswith(at + "/") and not re.fullmatch(r"/tmp/cc[^/]*\.o", t))


def entry_object(objs, entry):
    """The one-process-start rule on the objects OBJS ({path: defined symbols}) of the runtime's start files: when the link's entry ENTRY is not the
    driver's default `_start`, the object that defines `_start` is the runtime's own process start, which the link does not use (the product defines
    the entry): the paths that define `_start`, sorted. With the default entry there is none."""
    return sorted(p for p, d in objs.items() if "_start" in d) if entry != "_start" else []


def in_multilib(paths, multidir):
    """The paths of PATHS (relative to the runtime external) that do not lie in the multilib directory MULTIDIR the guest semantic flags select
    (`-print-multi-directory`): a directory component run equal to MULTIDIR's components must occur in the path. Empty: every file is of that multilib."""
    want = [c for c in multidir.split("/") if c and c != "."]
    out = []
    for p in paths:
        parts = p.split("/")
        if want and not any(parts[i:i + len(want)] == want for i in range(len(parts))):
            out.append(p)
    return out


def onedef(objs, root_defined):
    """The one-definition rule on the objects OBJS ({path: set of defined global/weak symbols}) of the runtime that the driver names: the paths whose
    defined symbols meet ROOT_DEFINED (the symbols that the admitted guest product roots define), sorted. An object is excluded from the link iff
    it defines a symbol a guest product root defines; no other reason excludes one."""
    return sorted(p for p, d in objs.items() if d & set(root_defined))


def load_guest_link(repo="."):
    """link/guest.json of the checkout, or None when the tree has none."""
    p = os.path.join(repo, GUEST_LINK)
    return json.load(open(p)) if os.path.exists(p) else None


def guest_link_errors(prof, pin, files, roots):
    """Problems of the guest link profile PROF (load_guest_link): exactly {script}; `script` a tracked file under a tree of an admitted tool root
    (tool_file). The libraries are not stored anywhere: the link derives them (Job.guest_libs, a keyed stage)."""
    if not isinstance(prof, dict) or set(prof) != {"script"}:
        return ["%s must be {script}: the libraries are derived by the link from the pinned runtime's driver" % GUEST_LINK]
    sc = prof["script"]
    full = TREE + "/" + sc if isinstance(sc, str) and rel_ok(sc) else None
    if not (full and tool_file(roots, files, full)):
        return ["%s: script %r is not a tracked file of an admitted tool root (a path inside %s)" % (GUEST_LINK, sc, TREE)]
    return []


def with_guest_link(rec, root, roots, repo):
    """The recipe a guest root is BUILT with: its `link` gets script (by repo path, a tool-tree file) and libs from the guest link profile.
    A host root, a root without link, or a checkout without a profile: REC unchanged."""
    lk, prof = rec.get("link"), load_guest_link(repo)
    if not (isinstance(lk, dict) and prof and entry_of(roots, root).get("target") == "guest"):
        return rec
    return dict(rec, link=dict(lk, script=TREE + "/" + prof["script"], libs={"external": load_pin(repo)["runtime"]["name"], "derive": True}))


def load_admit(repo="."):
    with open(os.path.join(repo, ADMIT)) as f:
        return {r["path"]: norm_entry(r) for r in json.load(f)["roots"]}


def own_recipes(ent):
    """{variant NAME: (target, recipe path)} of the variants of an admit entry that carry a recipe of their own (a per-target recipe)."""
    vs = ent.get("variants") if isinstance(ent, dict) else None
    return {n: (v.get("target"), v["recipe"]) for n, v in vs.items() if isinstance(v, dict) and isinstance(v.get("recipe"), str)} if isinstance(vs, dict) else {}


def targets_of(ent):
    """The targets a product root has a recipe for: its own and those of its per-target recipes."""
    return {ent.get("target")} | {t for t, _ in own_recipes(ent).values()}


def tracked(repo="."):
    out = subprocess.run(["git", "-C", repo, "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    return sorted(p for p in out.decode("utf-8", "surrogateescape").split("\0") if p)


def split_spec(spec):
    """'root' -> (root, ''); 'root@NAME' -> (root, NAME): a (root, variant) pair spelled as one string."""
    root, _, name = spec.partition("@")
    return root, name


ENTRY_VARIANT_KEYS = {"target", "recipe"}


def entry_of(roots, spec):
    """The admit.json entry of a root or of a 'root@NAME' pair: the base entry with the keys the variant's own entry (`variants`: {NAME: {"target"}})
    names replaced. A variant inherits the target of its base unless it declares one; sources stay the base root's."""
    root, name = split_spec(spec)
    ent = dict(roots.get(root, {}))
    own = ent.pop("variants", None)
    if name and isinstance(own, dict) and isinstance(own.get(name), dict):
        ent.update({k: v for k, v in own[name].items() if k in ENTRY_VARIANT_KEYS})
    return ent


def profile_fields(profile, pin):
    """The fields of the PROFILE ("guest" | "host") of a root: {compiler, plugins, external}, generated from the pins and never written in a build.json.
    guest: the game's compiler (the external and drivers of verify/guest_compiler.json, PIN), the full plugin set of PLUGIN_SOURCES in load order
    (plugin_decls takes the arguments from recipe.py alone), the compiler's external and the runtime's external (pin `runtime`).
    host: the distribution compiler pinned by env.json (PLUGIN_CXX's package), no plugin, no external. -> (fields, errors)."""
    if profile == "host":
        return {"compiler": {"cc": PLUGIN_CXX.replace("g++", "gcc"), "cxx": PLUGIN_CXX}, "plugins": [], "external": []}, []
    if profile != "guest":
        return None, ["profile %r is unknown (%s)" % (profile, " | ".join(PROFILES))]
    if not (isinstance(pin, dict) and isinstance(pin.get("external"), dict) and isinstance(pin.get("runtime"), dict)):
        return None, ["profile guest: %s is missing or has no external and runtime" % GUEST_PIN]
    ex = pin["external"]
    return {"compiler": {"external": ex["name"], "cc": pin["cc"], "cxx": pin["cxx"]}, "plugins": [{"name": n} for n in PLUGIN_SOURCES],
            "external": [dict(ex), dict(pin["runtime"])]}, []


COMPILE_KEYS = ("compiler", "flags", "include", "units", "link", "profile", "variants")


def generated_only(rec):
    """A recipe of generated stages only (RECIPE.md "Generated-only roots"): `generated` is a non-empty list and none of the keys that compile or link
    (COMPILE_KEYS) is written. Such a root has no unit (check refuses a translation unit in it), so it needs no compiler and no flags."""
    return isinstance(rec, dict) and bool(rec.get("generated")) and not any(k in rec for k in COMPILE_KEYS)


def fill_generated_only(rec):
    """REC of a generated-only root with the fields every reader expects, empty: no unit is compiled, so the compiler named (the host profile's) is
    never run and no flag is ever passed. Any other recipe: REC unchanged."""
    if not generated_only(rec):
        return rec
    return dict(rec, units="all", include=[], flags={"c": [], "cxx": [], "asm": []}, compiler=profile_fields("host", None)[0]["compiler"])


def expand_profile(rec, pin):
    """REC with its `profile` expanded: the profile's fields (profile_fields) in place of the keys the root does not write. A root that names a
    profile writes none of `compiler` and `plugins` (restating a profile field is red); its `external` lists only the externals the profile does not
    carry, which follow the profile's own in the expanded list. The expanded recipe has no `profile` key, so expansion is idempotent and every stage key is a
    function of the expanded values alone. -> (recipe, errors)."""
    if not isinstance(rec, dict) or "profile" not in rec:
        return fill_generated_only(rec), []
    fields, err = profile_fields(rec["profile"], pin)
    if fields is None:
        return rec, err
    out = {k: v for k, v in rec.items() if k != "profile"}
    err += ["profile %s: %s is the profile's field and is not written in the recipe" % (rec["profile"], k) for k in ("compiler", "plugins") if k in rec]
    mine = rec.get("external") if isinstance(rec.get("external"), list) else []
    names = {e["name"] for e in fields["external"]}
    err += ["profile %s: external %s is the profile's and is not written in the recipe" % (rec["profile"], e["name"])
            for e in mine if isinstance(e, dict) and e.get("name") in names]
    out["compiler"] = fields["compiler"]
    out["plugins"] = fields["plugins"]
    out["external"] = fields["external"] + [e for e in mine if not (isinstance(e, dict) and e.get("name") in names)]
    return out, err


def read_recipe(repo, path):
    """The recipe file PATH of the checkout REPO, its profile expanded (expand_profile with the checkout's guest compiler pin). Errors of the profile are
    reported by check(), which reads the same file through expand_profile; a reader that only needs the values takes the expanded recipe."""
    with open(os.path.join(repo, path)) as f:
        rec = json.load(f)
    return expand_profile(rec, load_pin(repo))[0]


def effective(rec, name):
    """The recipe of variant NAME of REC: REC without `variants`, the keys the variant names replaced whole, and its deltas applied: `flags_add`
    {c, cxx, asm} and `defines_add` ["NAME[=VALUE]"] are appended to the base flags of the languages (defines as -D to c, cxx and asm). '' is the
    implicit variant."""
    if not isinstance(rec, dict):
        return rec
    out = {k: v for k, v in rec.items() if k != "variants"}
    if name:
        v = rec["variants"][name]
        out.update({k: x for k, x in v.items() if k not in VARIANT_DELTAS})
        if VARIANT_DELTAS & set(v) and isinstance(out.get("flags"), dict):
            fl = {k: list(x) if isinstance(x, list) else x for k, x in out["flags"].items()}
            for lang, add in (v.get("flags_add") or {}).items():
                fl[lang] = list(fl.get(lang, [])) + list(add)
            for d in v.get("defines_add") or []:
                for lang in ("c", "cxx", "asm"):
                    fl[lang] = list(fl.get(lang, [])) + ["-D" + d]
            out["flags"] = fl
    return out


def resolve_link_root(roots, d, target):
    """The spec that the link.roots entry D names for an image of TARGET ("guest" | "host"), or (None, why). A link.roots entry names a root, and the
    image links that root's recipe for the image's own target: an explicit `root@variant` must itself have TARGET (else none); a base name is the
    base recipe when the base's target is TARGET, else the one per-target variant of the root whose target is TARGET (admit.json `variants`);
    no such variant, or more than one, is none. Objects of another target are never linked (each linked object's ELF class and machine are checked
    again at the link). -> (spec, "")."""
    r, v = split_spec(d)
    ent = roots.get(r)
    if not ent:
        return None, "%s is not an admitted root" % d
    if v:
        return (d, "") if entry_of(roots, d).get("target") == target else (None, "%s has target %r, the image %r" % (d, entry_of(roots, d).get("target"), target))
    if ent.get("target") == target:
        return d, ""
    own = ent.get("variants") if isinstance(ent.get("variants"), dict) else {}
    hit = sorted(n for n, x in own.items() if isinstance(x, dict) and x.get("target") == target)
    if len(hit) == 1:
        return "%s@%s" % (r, hit[0]), ""
    return None, "%s has target %r and %s variant of target %r for the image" % (d, ent.get("target"), "no" if not hit else "several (%s)" % ", ".join(hit), target)


def link_candidates(roots, root, target, repo):
    """The specs an image ROOT of TARGET may link: every admitted product root with a recipe other than ROOT itself, in the recipe it has for TARGET
    (resolve_link_root), that links nothing itself (an input root has no link). Sorted. A rule over admit.json: a new root is a candidate by being admitted."""
    out = []
    for r, e in sorted(roots.items()):
        if r == root or e.get("class") != "product" or not e.get("recipe"):
            continue
        spec, _ = resolve_link_root(roots, r, target)
        if spec is None:
            continue
        try:
            if load_recipe(repo, roots, spec).get("link") is not None:
                continue
        except (OSError, ValueError, KeyError, AttributeError, RecipeError):
            continue
        out.append(spec)
    return out


def definer_closure(defined, undefined, index):
    """The link members of an image: the definer closure of its undefined symbols. DEFINED, UNDEFINED are the image's own symbol sets; INDEX is
    {spec: {"defined": [{symbol, bind}], "undefined": [symbol]}} of every candidate root (link_candidates). Start from the own undefined set minus the own
    definitions; each symbol goes to the one candidate that defines it (a global definition; a weak one only when no candidate has a global one); that root's
    own undefined symbols join the set; iterate to the fixed point. -> (sorted specs, {symbol: [specs]} of the symbols several candidates define,
    {symbol: [referencers]} of the symbols no candidate defines). The several-definers rule is onedef's: a name is defined by one root. Nothing is listed by name:
    the members follow from the symbols."""
    strong, weak = {}, {}
    for spec, x in index.items():
        for d in x["defined"]:
            (strong if d["bind"] == "global" else weak).setdefault(d["symbol"], set()).add(spec)
    have, members, why, ambiguous, missing = set(defined), set(), {s: ["(own)"] for s in undefined}, {}, {}
    todo = sorted(set(undefined) - have)
    while todo:
        sym = todo.pop()
        if sym in have:
            continue
        who = sorted(strong.get(sym) or weak.get(sym) or ())
        if len(who) > 1:
            ambiguous[sym] = who
            continue
        if not who:
            missing[sym] = sorted(why.get(sym, []))
            continue
        spec = who[0]
        if spec in members:
            continue
        members.add(spec)
        have |= {d["symbol"] for d in index[spec]["defined"]}
        for u in index[spec]["undefined"]:
            if u not in have:
                why.setdefault(u, []).append(spec)
                todo.append(u)
    return sorted(members), ambiguous, missing


def variant_names(rec, ent=None):
    """The variants of a recipe REC, and of the admit entry ENT: its per-target recipes."""
    v = rec.get("variants") if isinstance(rec, dict) else None
    return sorted(set(v if isinstance(v, dict) else []) | set(own_recipes(ent or {})))


def pairs_of(rec, root, ent=None):
    """Every (root, variant) pair spec of a recipe: the implicit one and one per declared variant."""
    return [root] + ["%s@%s" % (root, n) for n in variant_names(rec, ent)]


def load_recipe(repo, roots, spec):
    """The effective recipe of a root or of a 'root@NAME' pair."""
    root, name = split_spec(spec)
    rec = read_recipe(repo, entry_of(roots, spec)["recipe"])
    if name in own_recipes(roots[root]):   # a per-target recipe is a whole recipe, with no variants of its own
        return effective(rec, "")
    return effective(rec, name) if not name or name in variant_names(rec) else rec


def generated_outputs(rec):
    """The declared outputs of the generate and run stages of a recipe."""
    return [o for k in ("generated", "run") for x in (rec.get(k) if isinstance(rec.get(k), list) else []) if isinstance(x, dict)
            and isinstance(x.get("outputs"), list) for o in x["outputs"] if isinstance(o, str)]


def all_units_of(rec, root, files):
    """Sorted root-relative paths of every source of the root: tracked source files + generated outputs with a source name."""
    mine = {f[len(root) + 1:] for f in files if under(f, root)}
    return sorted(u for u in mine | set(generated_outputs(rec)) if is_unit(u))


def tests_of(rec):
    """The declared tests of REC: [(name, [unit, ..])] (entries that are not well formed are left to check())."""
    t = rec.get("tests") if isinstance(rec, dict) else None
    return [(x["name"], list(x["units"])) for x in (t if isinstance(t, list) else []) if isinstance(x, dict) and isinstance(x.get("name"), str)
            and isinstance(x.get("units"), list) and all(isinstance(u, str) for u in x["units"])]


def test_units_of(rec):
    return {u for _, us in tests_of(rec) for u in us}


def units_of(rec, root, files):
    """Sorted root-relative PRODUCT unit paths: every source of the root that is not a unit of a declared test (RECIPE.md, tests)."""
    tu = test_units_of(rec)
    return [u for u in all_units_of(rec, root, files) if u not in tu]


def load_pin(repo="."):
    """verify/guest_compiler.json of the checkout, or None when the tree has none."""
    p = os.path.join(repo, GUEST_PIN)
    return json.load(open(p)) if os.path.exists(p) else None


def runtime_cflags(pin):
    """The flags the guest runtime (newlib, libstdc++) is built with beyond the semantic set: the `runtime_cflags` of the one guest compiler record.
    They apply to every runtime translation unit: C, C++ and assembly (.S) alike. The runtime build reads them here (guest_flags.py --runtime); no build script spells them."""
    v = pin.get("runtime_cflags") if isinstance(pin, dict) else None
    if not isinstance(v, list) or not all(isinstance(x, str) and x.startswith("-") for x in v):
        raise BuildRed("%s: runtime_cflags must be a list of compiler flags" % GUEST_PIN)
    return list(v)


def guest_link_part(rec, root, pin, prof, files, roots):
    """The link of a guest root (part of guest_errors): script and libs are the profile's (link/guest.json), one fact for every guest link."""
    lk = rec.get("link")
    if not isinstance(lk, dict):
        return []
    bad = ["link.%s is declared: script and libs of a guest link come from %s" % (k, GUEST_LINK) for k in ("script", "libs") if k in lk]
    bad += guest_link_errors(prof, pin, files, roots) if prof is not None else ["%s is missing" % GUEST_LINK]
    rt = pin.get("runtime") if isinstance(pin, dict) else None
    if isinstance(rt, dict) and rt.get("name") not in [e.get("name") for e in rec.get("external", []) if isinstance(e, dict)]:
        bad.append("the runtime external %r of %s is not declared in external" % (rt.get("name"), GUEST_PIN))
    return ["guest root %s: %s" % (root, b) for b in bad]


def guest_errors(rec, spec, roots, pin, prof=None, files=()):
    """The admit.json entry of a product root declares "target": "guest" (the game's code: compiled by the game's compiler) or "host".
    A guest root's compiler is the external of verify/guest_compiler.json, exactly (name, commit, tree, mount point, driver paths): the
    distribution's g++-13 is not the compiler of guest code, for C and C++ alike (one compiler key serves both)."""
    root = split_spec(spec)[0]
    t = entry_of(roots, spec).get("target")
    if t not in GUEST_TARGETS:
        return ['admit.json %s: a product root declares "target": "guest" or "host"' % root]
    if t == "host":
        return []
    if not isinstance(pin, dict) or not isinstance(pin.get("external"), dict):
        return ["guest root %s: %s is missing or has no external" % (root, GUEST_PIN)]
    lb = guest_link_part(rec, root, pin, prof, files, roots)
    c = rec.get("compiler")
    if not (isinstance(c, dict) and set(c) == {"external", "cc", "cxx"}):
        return lb + ["guest root %s: compiler must be the external compiler %r (not a distribution driver)" % (root, pin["external"].get("name"))]
    ex = next((e for e in rec.get("external", []) if isinstance(e, dict) and e.get("name") == c["external"]), None)
    bad = [] if ex == pin["external"] else ["external %r is not the pinned one (%s)" % (c["external"], pin["external"])]
    rt = pin.get("runtime")   # the guest's libc/libstdc++ external: when a recipe declares it (by name), it is the pinned one, exactly
    if isinstance(rt, dict):
        bad += ["external %r is not the pinned runtime (%s)" % (e["name"], rt) for e in rec.get("external", []) if isinstance(e, dict) and e.get("name") == rt["name"] and e != rt]
    bad += ["compiler.%s is %r, pinned %r" % (k, c[k], pin.get(k)) for k in ("cc", "cxx") if c[k] != pin.get(k)]
    fl = rec.get("flags") if isinstance(rec.get("flags"), dict) else {}
    # an attribute that neither the compiler nor a loaded plugin registers is otherwise dropped with a warning: a marker a rule relies on vanishes
    bad += ["flags.%s lacks %s" % (k, WERROR_ATTR) for k in ("c", "cxx") if WERROR_ATTR not in (fl.get(k) if isinstance(fl.get(k), list) else [])]
    # every semantic flag comes from the pin's flags.semantics (the recipe appends it to each compile stage), so a guest build.json holds only the
    # per-root classes (PER_ROOT): -mabi, -march, -mtune, -fPIE/-fPIC, -ftls-model, char signedness, -fshort-wchar, -m*outline-atomics, FP, aliasing, ... are refused
    bad += ["flags.%s: %s is not a per-root flag (-W*, -D*, -std=, -O0..3/s/g, -g*, -ffunction-sections, -fdata-sections); the semantic flags come from "
            "flags.semantics of %s" % (k, f, GUEST_PIN) for k in ("c", "cxx", "asm") for f in (fl.get(k) if isinstance(fl.get(k), list) else [])
            if isinstance(f, str) and not PER_ROOT.fullmatch(f)]
    # a flag that downgrades an error to a warning (or silences it) lets a diagnostic a rule relies on vanish
    bad += ["flags.%s: %s downgrades an error (forbidden)" % (k, f) for k in ("c", "cxx", "asm") for f in (fl.get(k) if isinstance(fl.get(k), list) else [])
            if isinstance(f, str) and (f in ("-fpermissive", "-w", "-Wno-error") or f.startswith("-Wno-error="))]
    return lb + ["guest root %s: %s" % (root, b) for b in bad]


LINK_KEYS = {"output", "flags"}
LINK_OPT = {"script", "kind", "version_script", "roots", "libs", "loader", "entry", "tables"}
IMAGE_ENTRY = "__fnvnx_image_entry"   # the symbol the runtime (rt/guest) calls once its start-up is done; the link assigns it the root's declared `link.entry` (--defsym)
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


OBJECTS_OF_KEYS = {"target", "origin", "dependents_of", "section"}
OBJECTS_OF_ORIGINS = ("native", "lifted")


def depends_on(cand, root, roots, repo, seen=()):
    """True iff admitted root CAND reaches ROOT through any input edge (root_edges: `link.roots`, the declared loader, `uses` and `objects_of`, so a root
    that reads the objects of a root that uses ROOT depends on it too), transitively. `objects_of` edges are taken whole (every product root they could select,
    no selector exclusion: that is what keeps this function free of recursion), a superset. Read from the recipes at REPO; a root without a readable recipe has
    no edges."""
    if cand == root:
        return True
    if cand in seen:
        return False
    ent = roots.get(cand)
    try:
        rec = json.load(open(os.path.join(repo, ent["recipe"]))) if ent and ent.get("recipe") else None
    except (OSError, ValueError):
        rec = None
    return any(depends_on(d, root, roots, repo, tuple(seen) + (cand,)) for d in root_edges(rec, cand, roots) if d != cand) if isinstance(rec, dict) else False


def objects_of_roots(rec, root, roots, repo=None):
    """The roots whose objects the generate stages of REC read (`objects_of`): the FACT UNIVERSE of the stage, every admitted product root with a recipe
    except ROOT itself and every root that depends on ROOT (depends_on), the consumers of the fact. Sorted. A rule over the admitted set and the uses graph,
    not a list: a new product root is read by the next build, and no name is excluded.
    A stage with `target` and/or `origin` narrows the universe by the admit.json declaration, never by a name: `target` equals the root's `target`, `origin` is
    the class of its `cxx_origin` (origin_class: "native" = native or undeclared, "lifted" = {lifted_by, pack}). The consumers of ROOT (depends_on:
    `link.roots`, `uses`, the loader, `objects_of`) are never in the universe, so there is no cycle; that needs REPO (the recipes are read from it)."""
    out = set()
    for g in rec.get("generated", []) if isinstance(rec.get("generated"), list) else []:
        oo = g.get("objects_of") if isinstance(g, dict) else None
        if isinstance(oo, dict):
            sel = [k for k in ("target", "origin") if k in oo]
            dep = oo.get("dependents_of")   # the complement of a producer's selection: only the roots that depend on DEP (the checker of what the producer left out)
            out |= {r for r, e in roots.items() if e.get("class") == "product" and e.get("recipe") and r != root
                    and all((e.get("target") == oo["target"]) if k == "target" else (origin_class(e.get("cxx_origin")) == oo["origin"]) for k in sel)
                    and (repo is None or (depends_on(r, dep, roots, repo) if dep else not depends_on(r, root, roots, repo)))}
    return sorted(out)


def fact_sections(root, roots, repo):
    """The sections ROOT's objects must not hold: the `objects_of.section` of every admitted root P with a recipe that ROOT depends on (depends_on: P's fact
    is an input of ROOT, so ROOT is outside P's universe and its objects can contribute nothing to the fact). Derived from the admitted recipes and the uses
    graph at REPO; no recipe names a root or a section for this (rule 2)."""
    out = set()
    for p, e in roots.items():
        if p == root or not e.get("recipe") or not depends_on(root, p, roots, repo):
            continue
        try:
            rec = json.load(open(os.path.join(repo, e["recipe"])))
        except (OSError, ValueError):
            continue
        for g in rec.get("generated", []) if isinstance(rec, dict) and isinstance(rec.get("generated"), list) else []:
            oo = g.get("objects_of") if isinstance(g, dict) else None
            if isinstance(oo, dict) and isinstance(oo.get("section"), str):
                out.add(oo["section"])
    return sorted(out)


def root_edges(rec, root=None, roots=None, repo=None):
    """The roots a recipe depends on besides its own files: the declared loader, `uses` (outputs) and, with ROOTS, the roots `objects_of`
    reads, variants cut off."""
    lk = rec.get("link") if isinstance(rec, dict) else None
    ds = [split_spec(lk["loader"])[0]] if isinstance(lk, dict) and isinstance(lk.get("loader"), str) else []   # the declared loader: its defined names are inputs of the link check
    us = rec.get("uses") if isinstance(rec, dict) else None
    return ds + [u["root"] for u in (us if isinstance(us, list) else []) if isinstance(u, dict) and isinstance(u.get("root"), str)] + (
        objects_of_roots(rec, root, roots, repo) if isinstance(rec, dict) and roots else [])


def generated_trees(roots, repo, files=None):
    """{root: sorted directories a generate stage of its recipe writes into}: `<root>/<dirname of an output>` for every output with a directory part, that holds
    no tracked file (FILES, default: the tracked files of REPO). These are generated trees; no other root includes from them (a consumer takes the declared
    output through `uses`). Read from the admitted recipes at REPO; a root without a readable recipe has none."""
    files = tracked(repo) if files is None else files
    out = {}
    for r, e in roots.items():
        if not e.get("recipe"):
            continue
        try:
            rec = json.load(open(os.path.join(repo, e["recipe"])))
        except (OSError, ValueError):
            continue
        ds = set()
        for g in rec.get("generated", []) if isinstance(rec, dict) and isinstance(rec.get("generated"), list) else []:
            for o in g.get("outputs", []) if isinstance(g, dict) and isinstance(g.get("outputs"), list) else []:
                if isinstance(o, str) and "/" in o:
                    ds.add(r + "/" + os.path.dirname(o))
        ds = {t for t in ds if not any(under(f, t) for f in files)}
        if ds:
            out[r] = sorted(ds)
    return out


def link_roots_cycle(root, roots, repo, seen=()):
    """The cycle through ROOT in the graph of `link.roots` and `uses` between roots, as a list of roots, or None."""
    if root in seen:
        return list(seen[seen.index(root):]) + [root]
    ent = roots.get(root)
    try:
        rec = read_recipe(repo, ent["recipe"]) if ent and ent.get("recipe") else None
    except (OSError, ValueError):
        rec = None
    for d in root_edges(rec, root, roots, repo):
        c = link_roots_cycle(d, roots, repo, tuple(seen) + (root,))
        if c:
            return c
    return None


def source_lines(text):
    """[(ext name, path)] of a `sources` file: one `<ext>:<path>` line per unit, '.' naming the root itself; blank lines skipped. ValueError for a bad line."""
    out = []
    for n, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        m = re.fullmatch(r"([A-Za-z0-9._-]+):(.+)", ln.strip())
        if not m or not rel_ok(m.group(2)):
            raise ValueError("line %d is not <external>:<path inside it>: %r" % (n, ln[:80]))
        out.append((m.group(1), m.group(2)))
    return out


def dir_output(p, gen):
    """P is a directory output: a path with generated outputs below it (the output is the whole directory: its files are exactly those outputs)."""
    return any(o != p and under(o, p) for o in gen)


def include_uses_errors(rec, roots, repo):
    """Problems of `include_uses` ([{root, output}], a subset of `uses`): the directory output NAME of another root, which is mounted at /uses/<root>/<NAME>
    and is one more include directory (after `include`, in list order). It is how a consumer reads the generated headers of another root."""
    iu, us = rec.get("include_uses"), rec.get("uses") or []
    if iu is None:
        return []
    if not (isinstance(iu, list) and all(isinstance(u, dict) and set(u) == {"root", "output"} and all(isinstance(v, str) for v in u.values()) for u in iu)):
        return ['include_uses must be [{"root": admitted root, "output": NAME}]']
    err = []
    for i, u in enumerate(iu):
        if u in iu[:i]:
            err.append("include_uses: %s/%s is listed twice" % (u["root"], u["output"]))
        if u not in us:
            err.append("include_uses: %s/%s is not in `uses`" % (u["root"], u["output"]))
            continue
        try:
            prod = read_recipe(repo, roots[u["root"]]["recipe"])
            p = (prod.get("outputs") or {}).get(u["output"])
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            continue    # uses_errors reports an unreadable or undeclared producer
        if not (isinstance(p, str) and dir_output(p, generated_outputs(prod))):
            err.append("include_uses: output %r of %s is not a directory output (outputs: {NAME: a directory of generated files})" % (u["output"], u["root"]))
        elif any(under(f, u["root"] + "/" + p) for f in tracked(repo)):
            err.append("include_uses: directory output %r of %s holds a tracked file (a directory output is generated files only)" % (u["output"], u["root"]))
    return err


def uses_errors(rec, root, roots, repo):
    """Problems of `outputs` ({name: a generated output of this root}) and `uses` ([{root, output}]: an output another admitted root declares)."""
    err, gen = [], generated_outputs(rec)
    outs = rec.get("outputs")
    if outs is not None and not (isinstance(outs, dict) and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", n) and isinstance(p, str) and (p in gen or dir_output(p, gen))
                                                                 for n, p in outs.items())):
        err.append("outputs must be {NAME: a path in generated[].outputs of this root, or a directory every file of which is one of them}")
    us = rec.get("uses")
    if us is None:
        return err
    if not (isinstance(us, list) and all(isinstance(u, dict) and set(u) == {"root", "output"} and all(isinstance(v, str) for v in u.values()) for u in us)):
        return err + ['uses must be [{"root": admitted root, "output": NAME}]']
    for i, u in enumerate(us):
        r, n = u["root"], u["output"]
        ent = roots.get(r)
        if u in us[:i]:
            err.append("uses: %s/%s is listed twice" % (r, n))
        if r == root:
            err.append("uses: %s is the root itself" % r)
        elif not ent or not ent.get("recipe"):
            err.append("uses: %s is not an admitted root with a recipe" % r)
        else:
            try:
                decl = read_recipe(repo, ent["recipe"]).get("outputs")
            except (OSError, ValueError, AttributeError):
                err.append("uses: the recipe of %s is unreadable" % r)
                continue
            if not (isinstance(decl, dict) and n in decl):
                err.append("uses: root %s declares no output %r (build.json `outputs`)" % (r, n))
    cyc = link_roots_cycle(root, roots, repo)
    if cyc:
        err.append("uses/link.roots: cycle %s" % " -> ".join(cyc))
    return err


def link_errors(lk, root, mine, roots, ext_names, repo, files=(), variant="", seen=()):
    """Problems of the `link` key: {script, output, flags} and optionally `roots` (admitted product roots whose objects are link inputs after
    the root's own, in list order) and `libs` ({external, dirs, names, startfiles, endfiles}: the guest libraries inside an external)."""
    kind = lk.get("kind", "pie") if isinstance(lk, dict) else None
    guest = entry_of(roots, root + "@" + variant if variant else root).get("target") == "guest"   # script and libs of a guest link are the profile's
    if not (isinstance(lk, dict) and LINK_KEYS <= set(lk) <= LINK_KEYS | LINK_OPT and isinstance(lk["flags"], list)
            and all(isinstance(t, str) for t in lk["flags"]) and isinstance(lk["output"], str) and kind in KINDS
            and re.fullmatch(r"[A-Za-z0-9._-]+\.(elf|so|exe)", lk["output"])
            and all(k not in lk or link_file(lk[k], root, mine, roots, files) or link_generated(lk[k], mine, seen) for k in ("script", "version_script"))
            and ("script" in lk or kind != "pie" or guest) and ("version_script" in lk) == (kind == "shared")):
        return ["link must be {output: NAME.elf|.so|.exe, flags: [..]} with `script` (a tracked file of the root, a generated output of the root, or a tracked file of a tree of an admitted tool root; required for kind pie, the default), kind pie|exe|shared, "
                "`version_script` (a tracked file of the root, a generated output of the root, or of a tool tree: required for, and only for, kind shared), optionally roots, libs"]
    err = ["link.flags: %s is in the refusal set R" % t for t in refused(lk["flags"])] + emulation_flags(lk["flags"], kind)
    rent = entry_of(roots, root + "@" + variant if variant else root)
    if rent.get("target") == "guest" and kind == "exe":
        err.append("link.kind exe is a host executable: a guest root links a pie (the ILP32 guest image, emulation aarch64linux32)")
    ent_sym = lk.get("entry")
    if rent.get("target") == "guest":   # the entry of a guest image is declared once, by its root: the symbol the runtime calls, which one object of the image defines
        if not (isinstance(ent_sym, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ent_sym) and ent_sym != IMAGE_ENTRY):
            err.append("link.entry: a guest image declares the symbol its runtime calls as its entry (a C identifier other than %s)" % IMAGE_ENTRY)
    elif "entry" in lk:
        err.append("link.entry is the entry of a guest image; a %s root has none" % (rent.get("target") or "host"))
    tb = lk.get("tables", [])
    if not (isinstance(tb, list) and all(isinstance(t, dict) and set(t) == {"tool", "args", "unit", "section"} and isinstance(t["tool"], str) and t["tool"] in files and t["tool"].endswith(".py")
                                         and isinstance(t["args"], list) and all(isinstance(a, str) for a in t["args"]) and isinstance(t["section"], str)
                                         and re.fullmatch(r"\.[A-Za-z0-9._-]+", t["section"]) and isinstance(t["unit"], str) and rel_ok(t["unit"]) and t["unit"].endswith(CC_EXT + CXX_EXT)
                                         and t["unit"] not in files and root + "/" + t["unit"] not in files for t in tb)
            and len({t["unit"] for t in tb if isinstance(t, dict)}) == len(tb)):
        err.append("link.tables must be a list of {tool: a tracked .py file, args: [str], unit: a new root-relative C or C++ source path, section: \".name\"} with distinct units: "
                   "the tool runs over the objects of this link and writes the unit, which is compiled into the image (the table of a link, never a root of its own)")
    if "roots" in lk:
        err.append("link.roots is derived, not written: the image links the definer closure of its undefined symbols over the admitted product roots (recipe.link_closure); remove it")
    if "loader" in lk:
        ld = lk["loader"]
        lent = roots.get(split_spec(ld)[0]) if isinstance(ld, str) else None
        if kind != "shared":
            err.append("link.loader: only a shared image declares the root of the image that loads it (kind shared)")
        elif not (lent and lent.get("recipe") and lent.get("class") in ("product", "test")):
            err.append("link.loader: %r is not an admitted root with a recipe (product or test)" % (ld,))
        elif split_spec(ld)[0] == root:
            err.append("link.loader: %s is the root itself" % ld)
        elif entry_of(roots, ld).get("target") != rent.get("target"):
            err.append("link.loader: %s has target %r, the loaded image %r" % (ld, entry_of(roots, ld).get("target"), rent.get("target")))
    cyc = link_roots_cycle(root, roots, repo) if isinstance(lk.get("loader"), str) else None
    if cyc and split_spec(lk["loader"])[0] in cyc:
        err.append("link.loader: cycle %s" % " -> ".join(cyc))
    lb = lk.get("libs")
    if guest:
        pass     # guest_errors reports a declared script or libs and checks the profile
    elif isinstance(lb, dict) and set(lb) == {"system"}:
        sysl = lb["system"]
        if not (isinstance(sysl, list) and sysl and all(isinstance(p, str) and p.startswith("/") and posixpath.normpath(p) == p for p in sysl)):
            err.append("link.libs.system must be a non-empty list of absolute normalised paths of the shared libraries of env.json packages")
        elif kind == "pie":
            err.append("link.libs.system: a pie is the guest: its libraries are those of an external")
    elif kind == "exe" and isinstance(lb, dict) and lb.keys() & {"startfiles", "endfiles", "dirs", "names", "external"}:
        err.append("link.libs: an exe's start and end files, dirs and default libraries are the pinned driver's `-###` answer for its target (RECIPE.md Link), "
                   "not a recipe's: only `system` is declared")
    elif "libs" in lk:
        paths = lambda k: isinstance(lb.get(k), list) and all(rel_ok(p) for p in lb[k])  # noqa: E731
        if not (isinstance(lb, dict) and set(lb) == LIBS_KEYS and paths("dirs") and paths("startfiles") and paths("endfiles")
                and isinstance(lb.get("names"), list) and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9._+-]+", n) for n in lb["names"])):
            err.append("link.libs must be {external: NAME, dirs: [paths inside it], names: [lib names], startfiles: [..], endfiles: [..]}")
        elif lb["external"] not in ext_names:
            err.append("link.libs.external %r names no external" % (lb["external"],))
    return err


HEADER_EXTS = (".h", ".hh", ".hpp", ".hxx", ".inc")


def include_dir(roots, root):
    """The include/ dir of the admitted product root ROOT (a repo-relative path): the one place a root's own headers live, as header_dirs and
    every user of a root's headers read it. ROOTS = load_admit(); a root that is not an admitted product root has none (ValueError)."""
    if roots.get(root, {}).get("class") != "product":
        raise ValueError("%s is not an admitted product root" % root)
    return root + "/include"


def header_dirs(rec, root, roots, repo=".", variant=""):
    """The repo include dirs that can supply a header to a product of ROOT's target (at VARIANT: the variant's target): the `include` of REC, the
    `include` of the recipe of every admitted product root that has a recipe for that target (its own or a per-target one; read from REPO; a
    recipe that is not there adds none) and the `include/` dir of each such root."""
    tg = entry_of(roots, root + "@" + variant if variant else root).get("target")
    dirs = set()

    def add(inc):
        dirs.update(d for d in inc if isinstance(d, str) and not d.startswith("/"))
    add(rec.get("include") if isinstance(rec.get("include"), list) else [])
    for p, r in roots.items():
        if r.get("class") == "header":
            dirs.add(p)   # a header root is a home for every target
        if r.get("class") == "product" and tg in targets_of(r):
            dirs.add(include_dir(roots, p))
            for path in [r.get("recipe") if r.get("target") == tg else None] + [q for t, q in own_recipes(r).values() if t == tg]:
                try:
                    with open(os.path.join(repo, path)) as f:
                        add(json.load(f).get("include") or [])
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    pass
    return sorted(dirs)


def include_closure(inc, root, roots, repo, tg, variant_rec=None, files=None):
    """The include path of a unit of ROOT (RECIPE.md "Include"): the recipe's `include` list, then, for every repo directory D of it that lies in another admitted
    product root P with a recipe for the target TG, the repo directories of P's own `include`, transitively and in that order, each once. A header that P's
    headers include is found by every consumer of P's headers (a header names its own dependencies once, in the recipe of the root that ships it); the entries
    of a root's `include` that lie in an external are the root's private toolchain and are not carried. -> [dir]"""
    out, seen = list(inc), set(inc)
    held = [f for f in (files if files is not None else tracked(repo))]
    todo = [d for d in inc if isinstance(d, str) and not d.startswith("/")]
    while todo:
        d = todo.pop(0)
        for p, r in roots.items():
            if p == root or r.get("class") != "product" or tg not in targets_of(r) or not under(d, p):
                continue
            paths = [r.get("recipe") if r.get("target") == tg else None] + [q for t, q in own_recipes(r).values() if t == tg]
            for path in paths:
                if not isinstance(path, str):
                    continue
                try:
                    with open(os.path.join(repo, path)) as f:
                        more = json.load(f).get("include") or []
                except (OSError, ValueError, AttributeError):
                    continue
                for m in more:
                    if isinstance(m, str) and not m.startswith("/") and m not in seen and any(under(f, m) for f in held):   # a directory with no tracked file is a generated tree of P: its headers reach a consumer through `include_uses`, never by this carry
                        seen.add(m)
                        out.append(m)
                        todo.append(m)
    return out


def run_errors(rec, root, files, roots, mine, seen, repo):
    """The `run` stages (RECIPE.md "Run stages"): {name, artifact "ROOT:OUTPUT", args, inputs [ref], debs [env.json package], outputs [path]}."""
    rs, err = rec.get("run", []), []
    if not isinstance(rs, list):
        return ["run is not a list"]
    try:
        pk = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")))["debs"]["packages"]
    except (OSError, KeyError, ValueError):
        pk = {}
    names = set()
    for i, g in enumerate(rs):
        if not (isinstance(g, dict) and {"name", "artifact", "args", "inputs", "debs", "outputs"} <= set(g) <= {"name", "artifact", "args", "inputs", "debs", "outputs", "host_cpu"}):
            err.append("run[%d] needs name, artifact, args, inputs, debs, outputs (and optionally host_cpu)" % i)
            continue
        if "host_cpu" in g and not (g["host_cpu"] == "cpuref" and TREE + "/verify/cpuref.json" in files):
            err.append('run[%d].host_cpu must be "cpuref", with the tracked pin file %s/verify/cpuref.json' % (i, TREE))
        if not (isinstance(g["name"], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", g["name"])) or g["name"] in names:
            err.append("run[%d].name must be a unique plain name" % i)
        names.add(g["name"])
        a = g["artifact"]
        ar, _, ao = a.partition(":") if isinstance(a, str) else ("", "", "")
        if ar not in roots or ar == root or not ao:
            err.append("run[%d].artifact %r must be ROOT:OUTPUT of another admitted root" % (i, a))
        else:
            try:
                lk = load_recipe(repo, roots, ar).get("link")
            except (OSError, ValueError, RecipeError):
                lk = None
            if not (isinstance(lk, dict) and lk.get("output") == ao):
                err.append("run[%d].artifact %s: root %s has no link output %r" % (i, a, ar, ao))
            elif roots[ar].get("target") != "host":
                err.append("run[%d].artifact %s: root %s is not a host product (the sandbox runs x86_64)" % (i, a, ar))
        if not (isinstance(g["args"], list) and all(isinstance(x, str) for x in g["args"])):
            err.append("run[%d].args must be strings" % i)
        if not (isinstance(g["debs"], list) and all(isinstance(d, str) for d in g["debs"])):
            err.append("run[%d].debs must be env.json package names" % i)
        else:
            err += ["run[%d].debs: %s is not declared in env.json debs.packages" % (i, d) for d in g["debs"] if d not in pk]
        ins = g["inputs"]
        if not isinstance(ins, list):
            err.append("run[%d].inputs must be a list of refs" % i)
        else:
            for p in ins:
                if isinstance(p, str):
                    if not (rel_ok(p) and any(f == p or under(f, p) for f in files) and any(under(p, r) for r in roots)):
                        err.append("run[%d].inputs: %s is not a tracked file or directory of an admitted root" % (i, p))
                elif isinstance(p, dict) and set(p) == {"external", "path"}:
                    if p["external"] not in [e.get("name") for e in rec.get("external", []) if isinstance(e, dict)]:
                        err.append("run[%d].inputs: external %r is not declared" % (i, p["external"]))
                elif not (isinstance(p, dict) and set(p) == {"stage", "path"} and all(isinstance(v, str) for v in p.values())):
                    err.append("run[%d].inputs: %r is neither a path, {root, path}, {root, output}, {external, path} nor {stage, path}" % (i, p))
        outs = g["outputs"]
        if not (isinstance(outs, list) and outs and all(isinstance(o, str) and rel_ok(o) for o in outs)):
            err.append("run[%d].outputs must be a non-empty list of root-relative paths" % i)
            continue
        for o in outs:
            if o in mine:
                err.append("run output %s is also a tracked file" % o)
            if o in seen:
                err.append("run output %s is produced twice" % o)
            seen.add(o)
    return err


def check(rec, root, files, roots, pin=None, repo=".", variant=""):
    """Every problem of recipe REC of product root ROOT; roots {path: root entry}; PIN = load_pin(); REPO: where the recipes of the
    roots of `link.roots` are read (their link null, no cycle). -> [str] (empty = valid)."""
    if not isinstance(rec, dict):
        return ["recipe is not a JSON object"]
    err = ["unknown key %r" % k for k in sorted(set(rec) - KEYS)]
    gonly = generated_only(rec)
    if "profile" in rec:
        ent0 = roots.get(root, {})
        want = (entry_of(roots, root + "@" + variant) if variant else ent0).get("target")
        if rec["profile"] != want:
            err.append("profile %r: the admit.json target of %s is %r (a root names the profile of its own target)" % (rec["profile"], root, want))
    rec, perr = expand_profile(rec, pin)
    err += perr
    vs = rec.get("variants")
    if vs is not None:
        if not (isinstance(vs, dict) and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", n) and isinstance(v, dict) and v and set(v) <= VARIANT_KEYS
                                              for n, v in vs.items())):
            return err + ["variants must be {NAME: {compiler, flags and/or link}}: NAME letters, digits, _ . -; a variant replaces only the keys it names (%s)" % sorted(VARIANT_KEYS)]
        for n in sorted(vs):    # every variant is a recipe of its own: all the rules apply to its effective form
            err += ["variant %s: %s" % (n, e) for e in check(effective(rec, n), root, files, roots, pin, repo, n)]
    own = roots.get(root, {}).get("variants")
    if own is not None and not variant:
        names = variant_names(rec) if isinstance(rec, dict) else []
        per = own_recipes(roots[root])
        if not (isinstance(own, dict) and all((n in names or n in per) and isinstance(v, dict) and v and set(v) <= ENTRY_VARIANT_KEYS and v.get("target", "host") in GUEST_TARGETS
                                              and ("recipe" not in v or v.get("target") in GUEST_TARGETS and v["target"] != roots[root].get("target"))
                                              for n, v in own.items())):
            err.append('admit.json %s: variants must be {NAME: {"target": "guest"|"host"}} for variants the recipe declares, or {NAME: {"target": the other target, "recipe": PATH}} for a per-target recipe' % root)
        err += ["admit.json %s: variant %s is declared by the recipe and by admit.json as a per-target recipe" % (root, n)
                for n in sorted(set(per) & set(rec.get("variants") or {} if isinstance(rec, dict) else {}))]
        for n, (t, path) in sorted(per.items()):   # a per-target recipe is a variant: every rule applies to it whole, with its own target
            try:
                with open(os.path.join(repo, path)) as f:
                    prec = json.load(f)   # raw: the recursive check() expands its profile
            except (OSError, ValueError):
                err.append("variant %s: recipe %s is unreadable" % (n, path))
                continue
            if isinstance(prec, dict) and "variants" in prec:
                err.append("variant %s: a per-target recipe declares no variants (its pair is one build)" % n)
            err += ["variant %s: %s" % (n, e) for e in check(prec, root, files, roots, pin, repo, n)]
    err += ["missing key %r" % k for k in ("format", "compiler", "flags", "include", "units") if k not in rec]
    if rec.get("format") != FORMAT:
        err.append("format is not " + FORMAT)
    if rec.get("units") != "all":
        err.append('units must be "all" (no tracked source file can be left out)')
    mine = {f[len(root) + 1:] for f in files if under(f, root)}
    gen = generated_outputs(rec)
    if gonly:   # no compiler and no flags are written: the root compiles nothing
        err += ["generated-only root: %s is a translation unit (a root with units writes compiler, flags, include and units)" % u
                for u in sorted(mine | set(gen)) if u.endswith(CC_EXT + CXX_EXT + ASM_EXT)]
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
    tg = entry_of(roots, root + "@" + variant if variant else root).get("target")
    for d in inc if isinstance(inc, list) else err.append("include must be a list") or []:
        if isinstance(d, str) and any(under(d, a) for a in ats):
            continue
        if not rel_ok(d) or tree_of(roots, d) is None:
            err.append("include %r is not a path inside an admitted root or an external's at" % (d,))
        elif not under(d, root) and not any(under(d, r) and (roots[r].get("class") == "header" or roots[r].get("class") == "product" and tg in targets_of(roots[r])) for r in roots):
            # one home per header: the headers a product root includes lie in a product root of the same target (covered by its gates through
            # the units that read them), never in a doc or tool root or a root of the other target
            err.append("include %r lies in no admitted header root and no product root of target %r (a header dir of a doc or tool root, or of the other target, is no home)"
                       % (d, tg))
        elif not (any(under(f, d) for f in files) or any(under(root + "/" + o, d) for o in gen)):
            err.append("include %r holds no tracked or generated file" % d)
    # a path into a generated tree of another root is no include: the generated files of a root are its declared outputs, and a consumer takes them through `uses`
    for d in inc if isinstance(inc, list) else []:
        for r, g in generated_trees(roots, repo, files).items():
            if r != root and isinstance(d, str) and not under(d, root) and any(under(d, t) for t in g):
                err.append("include %r is inside a generated tree of root %r (%s): take its declared output through `uses`" % (d, r, ", ".join(sorted(t for t in g if under(d, t)))))
    # one name, one file, over the whole target: the include dirs of this recipe, of every admitted product root's recipe of the same target
    # and the `include/` dir of each such root; two files of one include-relative path in them make `#include <that/path>` depend on the
    # order of a -I list (the first wins, silently), or on which root builds: a header is in one home only
    home = {}
    for d in header_dirs(rec, root, roots, repo, variant):
        for f in files:
            if under(f, d) and f.endswith(HEADER_EXTS):
                home.setdefault(f[len(d):].lstrip("/"), set()).add(f)
    err += ["header %r is in %d homes: %s (one home per header)" % (n, len(fs), ", ".join(sorted(fs)))
            for n, fs in sorted(home.items()) if len(fs) > 1]
    seen = set()
    for i, g in enumerate(rec.get("generated", []) if isinstance(rec.get("generated", []), list) else err.append("generated is not a list") or []):
        if not (isinstance(g, dict) and {"tool", "args", "inputs", "outputs"} <= set(g) <= {"tool", "args", "inputs", "outputs", "objects_of"}):
            err.append("generated[%d] needs exactly tool, args, inputs, outputs (and optionally objects_of)" % i)
            continue
        oo = g.get("objects_of")
        if "objects_of" in g and not (isinstance(oo, dict) and set(oo) <= OBJECTS_OF_KEYS
                                      and (oo.get("section") is None or (isinstance(oo["section"], str) and re.fullmatch(r"\.[A-Za-z0-9._-]+", oo["section"]) and "dependents_of" not in oo))
                                      and oo.get("target", "guest") in GUEST_TARGETS and oo.get("origin", "native") in OBJECTS_OF_ORIGINS
                                      and ("dependents_of" not in oo or (oo["dependents_of"] in roots and oo["dependents_of"] != root))):
            err.append('generated[%d].objects_of must be {"target": guest|host, "origin": native|lifted, "dependents_of": another admitted root, "section": ".name"} '
                       '(all optional: the universe is every product root but the consumers of this root; target and origin narrow it by declaration, dependents_of takes only the '
                       'roots that depend on that root, section names the fact section the consumers must not hold; no root is excluded by name)' % i)
        t, ins, outs = g["tool"], g["inputs"], g["outputs"]
        owner = next((r for r in roots if isinstance(t, str) and tree_of({r: roots[r]}, t)), None)
        if not (isinstance(t, str) and t in files and t.endswith(".py") and owner and roots[owner]["class"] in ("tool", "product")):
            err.append("generated[%d].tool %r is not a tracked .py file of an admitted tool or product root" % (i, t))
        if not (isinstance(g["args"], list) and all(isinstance(a, str) for a in g["args"])):
            err.append("generated[%d].args must be strings" % i)
        stg = [p for p in ins if isinstance(p, dict)] if isinstance(ins, list) else []
        ins = [p for p in ins if not isinstance(p, dict)] if isinstance(ins, list) else ins
        for p in stg:   # the declared output of an EARLIER generator of this recipe: {"stage": "<this root>:gen:I", "path": OUTPUT}, mounted at /stage/<OUTPUT>
            m = re.fullmatch(re.escape(root) + r":gen:(\d+)", p.get("stage", "")) if set(p) == {"stage", "path"} and isinstance(p.get("stage"), str) else None
            prev = rec["generated"][int(m.group(1))] if m and int(m.group(1)) < i else None
            if not (prev and isinstance(prev, dict) and isinstance(prev.get("outputs"), list) and p["path"] in prev["outputs"]):
                err.append("generated[%d].inputs: %r is not {stage: %s:gen:I, path: an output of generator I} with I before this one" % (i, p, root))
        if not (isinstance(ins, list) and all(rel_ok(p) and any(f == p or under(f, p) for f in files) for p in ins)):
            err.append("generated[%d].inputs must be tracked files or directories (repo-relative), or stage refs of earlier generators" % i)
        elif any(a != b and under(a, b) for a in ins for b in ins):
            err.append("generated[%d].inputs nest" % i)
        for p in ins if isinstance(ins, list) else []:
            if isinstance(p, str) and not any(under(p, r) for r in roots) and not tree_of(roots, p):
                err.append("generated[%d].inputs: %s lies in no admitted root or tree" % (i, p))
        if not (isinstance(outs, list) and outs and all(rel_ok(o) for o in outs)):
            err.append("generated[%d].outputs must be a non-empty list of root-relative paths" % i)
            continue
        for o in outs:
            if o in mine:
                err.append("generated output %s is also a tracked file" % o)
            if o in seen:
                err.append("generated output %s is produced twice" % o)
            seen.add(o)
    err += run_errors(rec, root, files, roots, mine, seen, repo)
    err += uses_errors(rec, root, roots, repo) + include_uses_errors(rec, roots, repo)
    ea, ident = rec.get("error_abi"), lambda x: isinstance(x, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x)
    if ea is not None and not (isinstance(ea, dict) and ident(ea.get("fail")) and (
            (ea.get("kind") == "noreturn" and set(ea) == {"fail", "kind"}) or (ea.get("kind") == "flag" and set(ea) == {"fail", "kind", "flag"} and ident(ea.get("flag"))))):
        err.append('error_abi must be {"fail": SYM, "kind": "noreturn"} or {"fail": SYM, "kind": "flag", "flag": FIELD}: the root\'s one failure function and how its '
                   'definition signals failure (objstub checks the definition)')
    xe = rec.get("external_errors")
    if xe is not None:
        names_ = {e.get("name") for e in rec.get("external", []) if isinstance(e, dict)}
        if not (isinstance(xe, dict) and xe and all(k in names_ and isinstance(v, dict) and set(v) == {"type", "success"} and ident(v["type"])
                                                    and isinstance(v["success"], int) and not isinstance(v["success"], bool) for k, v in xe.items())):
            err.append('external_errors must be {EXTERNAL: {"type": TYPEDEF, "success": INT}} naming declared externals: the error convention of a library whose units come from '
                       '`sources` (objstub: a V body of such a unit is the library\'s own code unless it returns an error constant)')
        elif not rec.get("sources"):
            err.append("external_errors needs `sources` (the units of the external libraries)")
    eh = rec.get("empty_handlers")
    if eh is not None:
        tb = eh.get("tables") if isinstance(eh, dict) else None
        if not (isinstance(eh, dict) and set(eh) == {"cover", "syntax", "tables"} and isinstance(tb, dict) and tb
                and all(ident(k) and isinstance(v, dict) and set(v) == {"symbol", "field"} and ident(v["symbol"]) and ident(v["field"]) for k, v in tb.items())):
            err.append('empty_handlers must be {"cover": PATH, "syntax": PATH, "tables": {SLOT: {"symbol": TABLE, "field": FIELD}}}: the generated op_cover.tsv and '
                       'op_syntax.tsv and, per slot, the instruction table (an array variable with DWARF) and the field of its entry that holds the handler')
        else:
            for k in ("cover", "syntax"):   # a bare path is a tracked file of ANOTHER admitted root (the generated outputs of the root that produces it); {external, path} is tree-checked by resolve_input
                ref = eh[k]
                if isinstance(ref, dict):
                    if set(ref) != {"external", "path"} or ref.get("external") not in {e.get("name") for e in rec.get("external", []) if isinstance(e, dict)}:
                        err.append("empty_handlers.%s %r must be {external: a declared external, path}" % (k, ref))
                elif not isinstance(ref, str) or owner_root(ref, roots, root) is None or ref not in files:
                    err.append("empty_handlers.%s %r is not a tracked file of an admitted root other than this one (resolve_input)" % (k, ref))
        if ea is None:
            err.append("a root with empty_handlers (table handlers) must declare error_abi")
    sr = rec.get("sources")
    if sr is not None and not (isinstance(sr, str) and sr in generated_outputs(rec)):
        err.append("sources must name a path in generated[].outputs of this root (a file of <external>:<path> lines)")
    allu = set(all_units_of(rec, root, files))
    tl = rec.get("tests", [])
    seen_t, seen_u = set(), set()
    for i, t in enumerate(tl if isinstance(tl, list) else err.append("tests must be a list") or []):
        if not (isinstance(t, dict) and set(t) == {"name", "units"} and isinstance(t["name"], str) and re.fullmatch(r"[A-Za-z0-9_+-]+", t["name"])
                and isinstance(t["units"], list) and t["units"] and all(isinstance(u, str) for u in t["units"])):
            err.append("tests[%d] needs exactly name ([A-Za-z0-9_+-]+) and units (a non-empty list of root-relative units)" % i)
            continue
        if t["name"] in seen_t:
            err.append("test %r is declared twice" % t["name"])
        seen_t.add(t["name"])
        for u in t["units"]:
            if u not in allu:
                err.append("test %r: %r is not a source of the root" % (t["name"], u))
            if u in seen_u:
                err.append("test unit %r is declared by two tests (or twice)" % u)
            seen_u.add(u)
    if seen_t and not rec.get("link"):
        err.append("tests need the root's link (a test is linked with the same script and flags)")
    units = set(units_of(rec, root, files))
    ol = rec.get("os_layer", [])
    for u in ol if isinstance(ol, list) else err.append("os_layer must be a list") or []:
        if u not in units:
            err.append("os_layer entry %r is not a unit (a test unit is no os_layer unit)" % (u,))
    gm = rec.get("game")
    if gm is not None and not (isinstance(gm, str) and rel_ok(gm) and any(u.startswith(gm.strip("/") + "/") for u in units)):
        err.append("game %r is not a root-relative directory holding units" % (gm,))
    for key in ("constants", "contract_names"):
        c = rec.get(key)
        if c is not None and not (isinstance(c, str) and rel_ok(c) and (c in mine or c in seen)):
            err.append("%s %r is neither a tracked file of the root nor a generated output" % (key, c))
    lk = rec.get("link")
    if lk is not None:
        err += link_errors(lk, root, mine, roots, names, repo, files, variant, seen)
        err += ["link: the ldcheck stage needs %s (a tracked file of an admitted tool root)" % p for p in (
            LD_TOOLS[0] + "/ldcheck.py", LD_TOOLS[0] + "/gateids.py", LD_TOOLS[1] + "/gatefile.py")
            if not tool_file(roots, files, p)]   # the one membership function (class tool, under a tree)
    err += guest_errors(rec, root + "@" + variant if variant else root, roots, pin, load_guest_link(repo), files)
    err += plugin_errors(rec, root, files, roots, units, names, variant, repo)
    return err


def lifted_origin(origin):
    """cxx_origin of a lifted root: {"lifted_by": TOOL.py, "pack": EXTERNAL} (the lifted C++ is made from an external pack) or {"lifted_by": TOOL.py,
    "stage": "gen:<root>:<i>"} (made by a generated step of this root's own recipe, no pack): exactly one of pack and stage."""
    return isinstance(origin, dict) and (set(origin) == {"lifted_by", "pack"} or set(origin) == {"lifted_by", "stage"})


def plugin_errors(rec, root, files, roots, units, ext_names, variant="", repo="."):
    """Problems of the plugin declaration of recipe REC of ROOT, and the gate rule: a product root with native or decomp C++ loads both
    plugins of PLUGIN_SOURCES with their fixed arguments; a root whose C++ is all lifted needs none. What is lifted is declared in the
    admit.json entry of the root, "cxx_origin": "native" | {"lifted_by": TOOL.py, "pack": EXTERNAL} | {"lifted_by": TOOL.py, "stage": "gen:ROOT:I"} (exactly
    one of pack and stage; see lifted_origin), required when the root has a C++ unit.
    Consistency of a lifted declaration: no tracked C++ unit in the root, every C++ unit is an output of a `generated` step whose tool is
    `lifted_by` (a tracked .py of an admitted tool root), `pack` names an external of the recipe (declared inputs of every generator); with `stage` the
    units are the outputs of that one generated step (gen:ROOT:I, I its index in `generated`), and its tool is `lifted_by`."""
    err, pl = [], rec.get("plugins", [])
    if not isinstance(pl, list):
        return ["plugins must be a list"]
    loaded = {}
    for i, p in enumerate(pl):
        if not (isinstance(p, dict) and isinstance(p.get("name"), str)):
            err.append('plugins[%d] is {"name": PLUGIN}: recipe.py fixes the rest' % i)
            continue
        n = p["name"]
        err += ["plugin %s: %s are fixed by recipe.py (%s)" % (n, k, PLUGIN_FIXED[k]) if k == "inputs" else "plugin %s: %s is fixed by recipe.py (%s)" % (n, k, PLUGIN_FIXED[k])
                for k in PLUGIN_FIXED if k in p]
        err += ["plugins[%d]: unknown key %r (an entry is {\"name\": PLUGIN})" % (i, k) for k in sorted(set(p) - set(PLUGIN_FIXED) - {"name"})]
        if n in loaded:
            err.append("plugin %s is declared twice" % n)
        elif n not in PLUGIN_SOURCES:
            err.append("plugin %s is not a plugin of recipe.py (PLUGIN_SOURCES: %s)" % (n, ", ".join(PLUGIN_SOURCES)))
        loaded[n] = p
        if n in PLUGIN_SOURCES and PLUGIN_SOURCES[n] in files and os.path.isfile(os.path.join(repo, PLUGIN_SOURCES[n])):
            try:
                plugin_inputs(PLUGIN_SOURCES[n], files, roots, repo)
            except ValueError as e:
                err.append("plugin %s: %s" % (n, e))
    if "plugin_cxx" in rec:
        err.append("plugin_cxx is not declared in a build.json: the plugin compiler is recipe.PLUGIN_CXX (%s), derived from env.json" % PLUGIN_CXX)
    cxx = [u for u in units if u.endswith(CXX_EXT)]
    ent = entry_of(roots, root + "@" + variant if variant else root)
    origin = ent.get("cxx_origin")
    # RULE (orchestrator, 2026-11-12): every guest CC stage, C and C++ alike and whatever the origin of the code, loads the FULL plugin set of
    # PLUGIN_SOURCES with its fixed arguments in the fixed order. No language declarations, no exception list. The arguments are fixed by the
    # origin alone, by the one function plugin_args.
    if ent.get("target") == "guest" and any(u.endswith(CC_EXT + CXX_EXT) for u in units) and not (origin == "native" and cxx):
        for n in sorted(PLUGIN_SOURCES):
            if n not in loaded:
                err.append("guest root %s does not load plugin %s (every guest C and C++ unit is compiled with the full plugin set)" % (root, n))
        if list(loaded) != [n for n in PLUGIN_SOURCES if n in loaded]:
            err.append("plugins must be loaded in the order %s" % list(PLUGIN_SOURCES))
    if not cxx:
        return err + (["admit.json %s: cxx_origin of a root without C++ units" % root] if origin is not None else [])
    if origin == "native" and ent.get("target") != "guest":
        pass   # the plugin set belongs to the TARGET PROFILE, not to the language (RECIPE.md "Plugins"): the host profile has the empty set (the plugins enforce guest-ABI semantics a host object does not have), so nothing is required of it
    elif origin == "native":
        for n in sorted(PLUGIN_SOURCES):
            if n not in loaded:
                err.append("native C++ root %s does not load plugin %s (every native or decomp C++ body is compiled with both)" % (root, n))
        if list(loaded) != [n for n in PLUGIN_SOURCES if n in loaded]:
            err.append("plugins must be loaded in the order %s" % list(PLUGIN_SOURCES))
        fl = rec.get("flags", {}).get("cxx", []) if isinstance(rec.get("flags"), dict) else []
        # fnvnx_sdd needs the define and -fno-devirtualize; a guest root's -fno-devirtualize comes from flags.semantics, its define is per root
        err += ["native C++ root %s: flags.cxx lacks %s" % (root, f) for f in (SDD_DEFINE,) if f not in fl]
    elif lifted_origin(origin):
        tool, pack, stage = origin["lifted_by"], origin.get("pack"), origin.get("stage")
        for n in ("fnvnx_ftoi",):   # lifted C++ does its conversions in helpers: the plugin proves it (and only it is required)
            if n not in loaded:
                err.append("lifted C++ root %s does not load plugin %s (mode lifted: every conversion outside an fnvnx_f2i function is an error)" % (root, n))
        mine = {f[len(root) + 1:] for f in files if under(f, root)}
        owner = next((r for r in roots if isinstance(tool, str) and tree_of({r: roots[r]}, tool)), None)
        if not (isinstance(tool, str) and tool in files and tool.endswith(".py") and owner and roots[owner]["class"] == "tool"):
            err.append("cxx_origin.lifted_by %r is not a tracked .py of an admitted tool root" % (tool,))
        steps = rec.get("generated", []) if isinstance(rec.get("generated"), list) else []
        if "pack" in origin and pack not in ext_names:
            err.append("cxx_origin.pack %r names no external of the recipe" % (pack,))
        if "stage" in origin:
            m = re.fullmatch(re.escape("gen:%s:" % root) + r"(0|[1-9][0-9]*)", stage) if isinstance(stage, str) else None
            if not (m and int(m.group(1)) < len(steps) and isinstance(steps[int(m.group(1))], dict) and steps[int(m.group(1))].get("tool") == tool):
                err.append("cxx_origin.stage %r is not a generated step gen:%s:I of this recipe whose tool is lifted_by" % (stage, root))
                steps = []
            else:
                steps = [steps[int(m.group(1))]]
        else:
            steps = [g for g in steps if isinstance(g, dict) and g.get("tool") == tool]
        made = {o for g in steps if isinstance(g.get("outputs"), list) for o in g["outputs"] if isinstance(o, str)}
        err += ["lifted root %s: C++ unit %s is not an output of a step of %s" % (root, u, tool) for u in cxx if u not in made or u in mine]
    else:
        err.append('admit.json %s: a root with C++ units needs cxx_origin "native" or {"lifted_by": TOOL.py, "pack": EXTERNAL} or {"lifted_by": TOOL.py, "stage": "gen:ROOT:I"} (exactly one of pack and stage)' % root)
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


def publish_dir(tmp, final):
    """The one rule that makes a directory of a shared store visible (every external: the bare fetch, the checkout, the exttree): TMP is a sibling of FINAL
    in the same store, complete and (where a tree hash is pinned) already verified; rename(2) publishes it atomically. A writer that loses the race (FINAL
    exists: ENOTEMPTY or EEXIST) deletes its own copy and uses the published one, which only ever holds a verified tree. No lock, no wait. Any other
    error deletes TMP and is raised. An interrupted writer leaves only a TMP-named directory: FINAL is never partial, and no reader looks at a TMP name."""
    try:
        os.rename(tmp, final)
    except OSError as x:
        shutil.rmtree(tmp, ignore_errors=True)
        if x.errno not in (errno.ENOTEMPTY, errno.EEXIST) or not os.path.isdir(final):
            raise


def fetch_externals(exts, store, extcache, ws):
    """Content-pinned inputs: each external of EXTS fetched by exact commit into the bare cache EXTCACHE/<commit>, checked out ONCE at
    EXTCACHE/<commit>.co, ingested into the CAS STORE; its tree hash must equal tree_sha256 (BuildRed naming the external). -> {name:
    {host, tree, at}}. FACT: the verified pair (commit, tree_sha256) is stored as STORE/external-facts/<commit>-<tree_sha256>.json; a
    pair with a fact, its checkout and its tree in the STORE is not fetched, walked or hashed again (a warm call reads the fact only), and
    a variant of a root adds only its own stage keys. A mismatch stores nothing. Used by a recipe (Job.externals) and by gate_contract.py
    (the exe-derived inputs of the contract gates), one mechanism."""
    out = {}
    url = os.environ.get("ARTIFACTS_URL", ARTIFACTS_URL)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    os.makedirs(ws, exist_ok=True)   # the index files live in WS: every caller may name a fresh sub-path of its scratch directory
    for e in exts:
        stage, bare, co = "external:" + e["name"], os.path.join(extcache, e["commit"]), os.path.join(extcache, e["commit"] + ".co")
        fact = os.path.join(store, "external-facts", "%s-%s.json" % (e["commit"], e["tree_sha256"]))
        if os.path.isfile(fact) and os.path.isdir(co) and os.path.isfile(os.path.join(store, "tree", e["tree_sha256"][:2], e["tree_sha256"] + ".json")):
            out[e["name"]] = {"host": co, "tree": e["tree_sha256"], "at": e["at"]}
            continue
        tmpco = None
        try:
            if not os.path.isdir(bare):
                tmp = "%s.tmp%d-%s" % (bare, os.getpid(), os.urandom(4).hex())
                os.makedirs(tmp)
                for cmd in (["init", "-q", "--bare"], ["fetch", "-q", "--depth", "1", url, e["commit"]]):
                    subprocess.run(["git", "-C", tmp] + cmd, env=env, check=True, capture_output=True)
                publish_dir(tmp, bare)
            dest = co
            if not os.path.isdir(co):
                dest = tmpco = "%s.tmp%d-%s" % (co, os.getpid(), os.urandom(4).hex())
                os.makedirs(dest)
                ienv = dict(env, GIT_INDEX_FILE=os.path.join(ws, "idx-" + e["name"]))
                subprocess.run(["git", "--git-dir", bare, "read-tree", e["commit"]], env=ienv, check=True, capture_output=True)
                subprocess.run(["git", "--git-dir", bare, "--work-tree", dest, "checkout-index", "-a", "-f"], env=ienv, check=True, capture_output=True)
        except (subprocess.CalledProcessError, OSError) as x:
            if tmpco:
                shutil.rmtree(tmpco, ignore_errors=True)
            raise BuildRed({"stage": stage, "exit": getattr(x, "returncode", None),
                            "stderr": (getattr(x, "stderr", b"") or str(x).encode()).decode(errors="replace").splitlines()[-20:]})
        tree = cas.ingest(dest, store)
        if tree != e["tree_sha256"]:
            if tmpco:
                shutil.rmtree(tmpco, ignore_errors=True)
            raise BuildRed({"stage": stage, "exit": None, "stderr": ["tree_sha256 is %s, recipe says %s" % (tree, e["tree_sha256"])]})
        if tmpco:
            publish_dir(tmpco, co)   # a lost race: the published checkout is the same commit and the same verified tree
        os.makedirs(os.path.dirname(fact), exist_ok=True)
        ftmp = "%s.tmp%d-%s" % (fact, os.getpid(), os.urandom(4).hex())
        with open(ftmp, "w") as fh:
            json.dump({"commit": e["commit"], "tree_sha256": tree}, fh, sort_keys=True)
        os.replace(ftmp, fact)
        out[e["name"]] = {"host": co, "tree": tree, "at": e["at"]}
    return out


def _dynsyms(path, defined):
    """Dynamic symbols of ELF PATH: the defined ones, or the STRONG undefined ones (versions cut) -> set. A weak undefined reference (nm type w/v)
    is not a load requirement: the dynamic loader resolves it to null when no object defines it (RTLD_NOW fails only on a strong one), so it is
    not counted (rule CC1PLUS-SYMS: the C++ front end's symbols are weak in every plugin, toolchain/tests/test_cc1.py)."""
    r = subprocess.run(["nm", "-D", "--defined-only" if defined else "--undefined-only", path], capture_output=True, text=True)
    if r.returncode:
        raise BuildRed({"stage": "plugin-frontend", "exit": r.returncode, "stderr": r.stderr.splitlines()[-5:]})
    rows = [l.split() for l in r.stdout.splitlines() if l.split()]
    return {t[-1].split("@")[0] for t in rows if defined or (len(t) >= 2 and t[-2] not in ("w", "v"))}


def plugin_languages(cc, cxx, sos):
    """{kind: [plugin names that front end can load]} for kind "c" (cc1) and "cxx" (cc1plus), in the order of SOS ({name: path of the .so}). A plugin
    loads into a front end iff it has no STRONG undefined reference to a symbol that only the OTHER front end defines: the symbols only cc1plus defines are the dynamic symbols
    of cc1plus not defined by cc1, and the other way round (nm -D of the two compiler programs and of the plugin: a static fact of the ELF files,
    not a name or a run). A plugin that references C++-front-end symbols (cp_global_trees, ...) is not loadable by cc1: there is no C instance of it."""
    prog = {k: subprocess.run([d, "-print-prog-name=" + p], capture_output=True, text=True).stdout.strip() for k, d, p in (("c", cc, "cc1"), ("cxx", cxx, "cc1plus"))}
    defs = {k: _dynsyms(v, True) for k, v in prog.items()}
    only = {"c": defs["c"] - defs["cxx"], "cxx": defs["cxx"] - defs["c"]}   # symbols only that front end defines
    refs = {n: _dynsyms(p, False) for n, p in sos.items()}
    return {k: [n for n in sos if not (refs[n] & only["cxx" if k == "c" else "c"])] for k in ("c", "cxx")}


def predefined(driver, kind, flags):
    """The macros the pinned DRIVER predefines for a unit of KIND under FLAGS (`-dM -E` on an empty input) -> {name: value}: the compiler's own
    statement of the data model (__SIZE_TYPE__, __PTRDIFF_TYPE__, __CHAR_UNSIGNED__, ...), the same macros the unit's preprocessing stage sees."""
    r = subprocess.run([driver, "-x", LANG_X[kind], "-dM", "-E", "-o", "/dev/stdout"] + list(flags) + ["/dev/null"], capture_output=True, text=True,
                       env=dict(os.environ, LC_ALL="C", LANGUAGE="C"))
    if r.returncode:
        raise BuildRed({"stage": "predefined:" + kind, "exit": r.returncode, "stderr": r.stderr.splitlines()[-20:]})
    return {l.split(" ", 2)[1]: (l.split(" ", 2) + [""])[2] for l in r.stdout.splitlines() if l.startswith("#define ")}


def diagnostics(driver, kind, flags, source):
    """The diagnostics (stderr lines) of the pinned DRIVER compiling SOURCE (a file) for a unit of KIND under FLAGS, syntax only: the compiler's own
    answer on a translation unit whose static_asserts state the data model (gate "datamodel").  -> (exit code, [lines])."""
    r = subprocess.run([driver, "-x", LANG_X[kind], "-fsyntax-only"] + list(flags) + [source], capture_output=True, text=True,
                       env=dict(os.environ, LC_ALL="C", LANGUAGE="C"))
    return r.returncode, r.stderr.splitlines()


def semantics_for(driver, kind, flags=None, origin=None):
    """The semantic flags the pinned DRIVER (host path) applies to a unit of KIND ("c", "cxx", "asm"), in their order: FLAGS minus every flag that
    the driver itself says does not apply to that language (its diagnostic "command-line option 'X' is valid for C++/ObjC++ but not for C", read
    from one run on an empty input in the driver's -x language). Nothing is decided by a name here; a flag the driver does not know is an error
    (an unrecognised option makes the run fail: BuildRed), never dropped. FLAGS defaults to the set of the root's ORIGIN (semantics_set: the
    `cxx_origin` declaration of the root; None = native)."""
    flags = semantics_set(origin) if flags is None else flags
    r = subprocess.run([driver, "-x", LANG_X[kind], "-E", "-o", "/dev/null"] + list(flags) + ["/dev/null"], capture_output=True, text=True,
                       env=dict(os.environ, LC_ALL="C", LANGUAGE="C"))
    if r.returncode:
        raise BuildRed({"stage": "semantics:" + kind, "exit": r.returncode, "stderr": r.stderr.splitlines()[-20:]})
    drop = set(re.findall(r"command-line option '([^']+)' is valid for [^\n]* but not for", r.stderr))
    return [f for f in flags if f not in drop]


def unit_flags(rec, roots, spec, driver, kind):
    """The flags a unit of KIND ("c", "cxx", "asm") of root SPEC is compiled with, before the plugin flags of the CC stage: for a guest root the pinned
    semantics (semantics_for the host path DRIVER, the root's cxx_origin) first, then the root's own flags. The one place that composes them: the
    compile stage and the gates that run the compiler on a unit's preprocessed text (objstub) read it, so they see the same TU."""
    ent = entry_of(roots, spec)   # the variant's own target (a host variant of a guest root is a host compile: no guest semantics)
    sem = semantics_for(driver, kind, origin=ent.get("cxx_origin")) if ent.get("target") == "guest" else []
    return sem + rec["flags"][kind]


def driver_doc(stage, drv, flags, mode, tail, inputs):
    """The one constructor of the stage doc of every compiler-driver probe (drv:, mld:): argv = driver + semantic FLAGS + PROBE_FLAGS (the reproducibility
    flags toolflags demands of every gcc stage) + MODE + TAIL, the hermetic environment, one declared input set."""
    return {"format": "fnvnx-stage/1", "stage": stage, "argv": [drv] + list(flags) + PROBE_FLAGS + list(mode) + list(tail), "cwd": "/", "class": "build", "tool": "gcc",
            "env": dict(unitstage.ENV, PATH=os.path.dirname(drv)), "outputs": ["/out"], "inputs": inputs}


class Job:
    def __init__(self, repo, spec, store, jobs, ext):
        root, self.variant = split_spec(spec)
        self.repo, self.root, self.store, self.jobs, self.extcache = repo, root, store, jobs, ext
        self.roots, self.files = load_admit(repo), tracked(repo)
        self.rec = with_guest_link(load_recipe(repo, self.roots, spec), root, self.roots, repo)
        self.keys, self.run, self.hit, self.ext, self.ppkeys, self.built = set(), 0, 0, {}, {}, []
        self.aux = []   # build(aux): AUX_FLAGS of the CC stage
        self.analysis = None   # tree of the objstub plugin's /out (analysis_plugin), built once per root that has C/C++ units
        self.dukeys = {}   # {unit: key of its macro-fact stage (-dU, unitstage.DU_TAIL)}
        self.red, self.blocked, self.failed = [], {}, []   # KEEP GOING: red stages (failure dicts) and {unit: stage it is blocked by}; nothing is masked (see build)
        os.makedirs(os.path.join(store, "rt"), exist_ok=True)

    def stage(self, doc):
        if self.variant:     # the variant name is in every stage key (the implicit variant adds nothing: its keys are those of a root without variants)
            doc["env"] = dict(doc["env"], FNVNX_VARIANT=self.variant)
            tg = entry_of(self.roots, self.root + "@" + self.variant).get("target")   # the effective target is in the key too
            if tg != self.roots[self.root].get("target"):
                doc["env"]["FNVNX_TARGET"] = tg
        v = [] if doc.get("tool") == "run" else toolflags.check_doc(doc)   # a `run` stage executes a declared artifact: its argv[0] is checked by run_one
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

    def named_ext(self, tokens):
        """The names of the externals whose `at` holds an absolute path that a token of TOKENS (argv words, include dirs, input paths) names. RULE: a stage
        is keyed by what it can read, and a stage reads only what is mounted (it is hermetic): PP and a generated stage mount the compiler's external
        plus the externals they name here, not every external of the root. A stage that reads an unnamed external fails (the path is absent), so the
        narrowing cannot drop a real input."""
        paths = {p for t in tokens if isinstance(t, str) for p in re.findall(r"/[^\s=:,;\"']+", t)}
        return {n for n, e in self.ext.items() if any(under(p, e["at"]) for p in paths)}

    def toolrt(self, paths, names=None):
        """Tool runtime (runtime.py rules) of driver PATHS plus every external (NAMES: only those) as declared input. An external compiler is a tree
        mapped HOST@AT: the loader closure of its x86-64 ELF files is resolved as the loader resolves it inside the sandbox."""
        xin = [{"at": e["at"], "tree": e["tree"]} for n, e in self.ext.items() if names is None or n in names]
        tag = hashlib.sha256(repr((paths, xin)).encode()).hexdigest()[:16]
        c = self.rec["compiler"]

        def make():
            if "external" in c:
                x = next((e for e in self.ext.values() if under(paths[0], e["at"])), self.ext[c["external"]])   # the external that holds the driver
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
            return self.plus(runtime.build_tool(self.store, sorted(set(exes)), sorted(set(trees))), names)
        return self.cached_rt("tool-" + tag, make)

    def plus(self, rt, names=None):
        """RT with every external (NAMES: only those) that it does not hold yet as a declared input."""
        have = {i["at"] for i in rt["inputs"]}
        rt["inputs"] = sorted(rt["inputs"] + [{"at": e["at"], "tree": e["tree"]} for n, e in self.ext.items() if e["at"] not in have and (names is None or n in names)], key=lambda i: i["at"])
        return rt

    def ingest_files(self, tmp, name, files, base):
        d = os.path.join(tmp, name)
        os.makedirs(d, exist_ok=True)
        _copy(self.repo, files, base, d)
        return cas.ingest(d, self.store)

    def generated_sources(self, ws):
        """The units a generate stage lists in `sources` (lines `<external>:<path>`): each file inside a declared external (or tracked in the root,
        for '.') is copied into the stage view as unit `<external>/<path>`, so its content is in the unit's keys. A path outside the externals
        and the root is red. -> [unit names]."""
        sr = self.rec.get("sources")
        if not sr:
            return []
        out, mine = [], {f[len(self.root) + 1:] for f in self.files if under(f, self.root)}
        try:
            lines = source_lines(open(os.path.join(ws, "root", sr)).read())
        except (OSError, ValueError) as e:
            raise BuildRed({"stage": "sources:" + self.root, "exit": None, "stderr": ["%s: %s" % (sr, e)]})
        for ext, p in lines:
            if ext == ".":
                if p not in mine:
                    raise BuildRed({"stage": "sources:" + self.root, "exit": None, "stderr": ["%s: %s is no tracked file of the root" % (sr, p)]})
                continue
            if ext not in self.ext:
                raise BuildRed({"stage": "sources:" + self.root, "exit": None, "stderr": ["%s: %s names no external of the recipe" % (sr, ext)]})
            base = os.path.realpath(self.ext[ext]["host"])
            src = os.path.realpath(os.path.join(base, p))
            if not (src.startswith(base + os.sep) and os.path.isfile(src)):
                raise BuildRed({"stage": "sources:" + self.root, "exit": None, "stderr": ["%s: %s is not a file inside external %s" % (sr, p, ext)]})
            unit = "%s/%s" % (ext, p)
            dest = os.path.join(ws, "root", unit)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest)
            if is_unit(unit):
                out.append(unit)
        return sorted(set(out))

    def use_inputs(self, ws):
        """The declared outputs of other roots this root `uses`, as stage inputs {at: /uses/<root>/<name>, tree}: the producer's generate stages
        run (cached by key) and the output file is ingested; the stage key holds its content hash, not a path or the producer's keys."""
        if getattr(self, "_uses", None) is not None:
            return self._uses
        out = []
        for u in self.rec.get("uses", []):
            p = Job(self.repo, u["root"], self.store, self.jobs, self.extcache)
            pws = tempfile.mkdtemp(dir=ws)
            try:
                p.externals(pws)
                p.generate(pws)
                if p.red:   # the output this root uses is not produced
                    raise BuildRed({"stage": "uses:" + u["root"], "exit": None, "stderr": ["producer %s is red: %s" % (u["root"], p.red[0]["stage"])] + list(p.red[0]["stderr"])[-18:]})
                path = os.path.join(pws, "root", p.rec["outputs"][u["output"]])
                out.append({"at": "/uses/%s/%s" % (u["root"], u["output"]), "tree": cas.ingest(path, self.store)})
            finally:
                self.run, self.hit = self.run + p.run, self.hit + p.hit
        self._uses = sorted(out, key=lambda i: i["at"])
        return self._uses

    def objects_input(self, ws, tmp, g):
        """The tree mounted at /objs of a generate stage with `objects_of`: the objects of every admitted product root (implicit variant) the rule
        selects (objects_of_roots), as <root with / as __>/<unit>.o. Each root is built by this recipe machinery (content-keyed, cache hits);
        a red root makes the stage red; the stage key holds the objects' content, not a path."""
        out = os.path.join(tmp, "objs")
        os.makedirs(out, exist_ok=True)
        for d in objects_of_roots({"generated": [g]}, self.root, self.roots, self.repo):
            try:
                r = build(self.repo, d, self.store, os.path.join(ws, "objs-" + d.replace("/", "__")), self.jobs, self.extcache)
            except RecipeError as x:
                raise BuildRed({"stage": "objects_of:" + self.root, "exit": None, "stderr": ["root %s: invalid recipe: %s" % (d, x)][:20]})
            self.keys |= set(r["stage_keys"])
            self.run, self.hit = self.run + r["stages"]["run"], self.hit + r["stages"]["hit"]
            if r["status"] == "red":
                f = r["failure"]
                raise BuildRed({"stage": "objects_of:" + self.root, "exit": f["exit"], "stderr": ["root %s is red: stage %s" % (d, f["stage"])] + f["stderr"][-18:]})
            for o in r["objects"]:
                dest = os.path.join(out, d.replace("/", "__"), o["unit"] + ".o")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(o["object"], dest)
        return cas.ingest(out, self.store)

    def pyrt(self):
        """The Python runtime of every generator and table stage (the interpreter and the pinned modules, a declared input), and `self.pymods`."""
        mods = pinned_modules(self.repo)
        sites = sorted({n for _, _, _, tops, _ in mods for n in tops})
        paths = sorted({p for _, _, _, _, ps in mods for p in ps})
        _, py = self.cached_rt("python-" + hashlib.sha256(repr((sites, paths)).encode()).hexdigest()[:16], lambda: runtime.build(self.store, sys.executable, sites, paths))
        self.pymods = ";".join("%s==%s:%s" % (m, v, d) for m, v, d, _, _ in mods)   # in the key of every generated stage: the interpreter digest (runtime_id) plus the installed files of every pinned module
        return py

    def generate(self, ws):
        if not self.rec.get("generated"):
            return
        py = self.pyrt()
        for i, g in enumerate(self.rec["generated"]):
            names = self.named_ext(list(g["args"]) + list(g["inputs"]))   # a generated stage mounts the externals its stage names (args, inputs), no other
            xin = [{"at": e["at"], "tree": e["tree"]} for n, e in self.ext.items() if n in names]
            try:
                self.generate_one(ws, i, g, py, xin)
            except BuildRed as r:   # a red generator does not stop the others: its outputs are absent, and what needs them is blocked by it
                f = dict(r.args[0])
                self.red.append(f)
                for o in g["outputs"]:
                    self.blocked[o] = f["stage"]

    def run_all(self, ws):
        """The `run` stages of the recipe (RECIPE.md "Run stages"), after the generators; a red one blocks its outputs like a generator."""
        for i, g in enumerate(self.rec.get("run", [])):
            try:
                self.run_one(ws, i, g)
            except BuildRed as r:
                f = dict(r.args[0])
                self.red.append(f)
                for o in g["outputs"]:
                    self.blocked[o] = f["stage"]

    def run_one(self, ws, i, g):
        """Stage `run:<root>:<name>`: executes the artifact (the linked output of an admitted root) in the sandbox. It sees the artifact at /art/<output>,
        the declared inputs at /in/<ident>, each declared deb's installed files at /deb/<name>/<absolute path>, and writes /out. Key = artifact
        content + input contents + deb sha256 (env.json) + argv. A nonzero exit, an undeclared or a missing output is red."""
        aroot, aout = g["artifact"].split(":", 1)
        tmp = tempfile.mkdtemp(dir=ws)
        r = build(self.repo, aroot, self.store, os.path.join(tmp, "art"), self.jobs, self.extcache)
        if r["status"] != "green" or not r["elf"] or os.path.basename(r["elf"]["path"]) != aout:
            raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["artifact %s is not produced (root status %s)" % (g["artifact"], r["status"])]})
        ins = [{"at": "/art", "tree": self._one_file(tmp, "art", r["elf"]["path"], aout)}]
        n = 0
        for ref in g["inputs"]:
            n += 1
            at = "/in/%d" % n
            if isinstance(ref, str):   # a repo-relative tracked file or directory (git ls-files only), like a generator input
                sub = [f for f in self.files if f == ref or under(f, ref)]
                ins.append({"at": at, "tree": cas.ingest(os.path.join(self.repo, ref), self.store) if sub == [ref] else self.ingest_files(tmp, "in%d" % n, sub, ref)})
            else:   # {external, path} or {stage, path}: resolve_input is the one reader
                host, _ = resolve_input(self.root, ref, self.repo, os.path.dirname(self.store), self.rec)
                ins.append({"at": at, "tree": cas.ingest(host, self.store)})
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")) as f:
            pk = json.load(f)["debs"]["packages"]
        for d in g["debs"]:
            if d not in pk:
                raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["deb %s is not declared in env.json debs.packages" % d]})
            ins.append({"at": "/deb/" + d, "tree": self._deb_tree(tmp, d, pk[d])})
        env = {"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "PATH": "/deb",
               "FNVNX_DEBS": ";".join("%s=%s" % (d, pk[d]["sha256"]) for d in sorted(g["debs"]))}   # the deb sha256s are in the key
        argv = ["/art/" + aout] + g["args"]
        interp = elf_interp(r["elf"]["path"])
        if interp:   # started through the file at its PT_INTERP path inside a declared deb (the deb's own symlinks followed; no alias is relied on)
            home = deb_resolve(interp, tmp, g["debs"])
            if not home:
                raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": [
                    "%s has PT_INTERP %s: the declared debs %s do not provide that file (followed through the symlinks they ship)" % (g["artifact"], interp, sorted(g["debs"]))]})
            lp = [os.path.join("/deb", d, "lib", "x86_64-linux-gnu") for d in g["debs"]] + [os.path.join("/deb", d, "usr", "lib", "x86_64-linux-gnu") for d in g["debs"]]
            argv = ["/deb/" + home[0] + home[1], "--library-path", ":".join(lp)] + argv
        if g.get("host_cpu"):   # the reference host's identity is in the key; it must be this host unless the stage is a cache hit
            pin = json.load(open(os.path.join(self.repo, TREE, "verify", "cpuref.json")))
            env["FNVNX_HOST_CPU"] = json.dumps(pin, sort_keys=True)
        doc = {"format": "fnvnx-stage/1", "stage": "run:%s:%s" % (self.root, g["name"]), "class": "build", "tool": "run", "cwd": "/work",
               "argv": argv, "env": env, "inputs": sorted(ins, key=lambda x: x["at"]), "outputs": ["/out"], "ro_root": True}
        if g.get("host_cpu") and sandbox._load(self.store, sandbox.key_of(doc)) is None:
            bad = cpu_mismatch(pin)
            if bad:
                raise BuildRed({"stage": doc["stage"], "exit": None, "stderr": ["host_cpu: this host is not the reference host: " + "; ".join(bad)]})
        res = self.stage(doc)[1]
        got = sorted(x for x, e in cas.listing(res["outputs"]["/out"], self.store) if e["t"] != "d")
        if got != sorted(g["outputs"]):
            raise BuildRed({"stage": doc["stage"], "exit": 0, "stderr": ["outputs %s differ from the declared %s" % (got, sorted(g["outputs"]))]})
        cas.materialize(res["outputs"]["/out"], self.store, os.path.join(tmp, "out"))
        for o in g["outputs"]:
            os.makedirs(os.path.dirname(os.path.join(ws, "root", o)), exist_ok=True)
            shutil.copyfile(os.path.join(tmp, "out", o), os.path.join(ws, "root", o))

    def _one_file(self, tmp, name, path, base):
        d = os.path.join(tmp, "f-" + name)
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(path, os.path.join(d, base))
        os.chmod(os.path.join(d, base), 0o755)
        return cas.ingest(d, self.store)

    def _deb_tree(self, tmp, name, pin):
        """The installed files of the env.json package NAME (dpkg -L: regular files and symlinks) under <tmp>/deb-<name>/<absolute path>. The package
        is the pinned one (envcheck checks version and the deb's sha256 pin: debs.packages.<name>.sha256, which is also in the stage key by `env`)."""
        v = subprocess.run(["dpkg-query", "-W", "-f=${Version}", name], capture_output=True, text=True).stdout
        if v != pin["version"]:
            raise BuildRed({"stage": "deb:" + name, "exit": None, "stderr": ["package %s is installed at %r, env.json pins %s" % (name, v, pin["version"])]})
        dest = os.path.join(tmp, "deb-" + name)
        for p in subprocess.run(["dpkg-query", "-L", name], capture_output=True, text=True).stdout.splitlines():
            if os.path.isfile(p) or os.path.islink(p):
                os.makedirs(os.path.dirname(dest + p), exist_ok=True)
                if os.path.islink(p):   # an absolute target is made relative to the package tree: it must resolve inside /deb/<name>
                    t = os.readlink(p)
                    os.symlink(os.path.relpath(t, os.path.dirname(p)) if t.startswith("/") else t, dest + p)
                else:
                    shutil.copy2(p, dest + p)
        return cas.ingest(dest, self.store)

    def generate_one(self, ws, i, g, py, xin):
        owner = tree_of(self.roots, g["tool"])
        tmp = tempfile.mkdtemp(dir=ws)
        ins = [{"at": "/tool", "tree": self.ingest_files(tmp, "tool", [f for f in self.files if under(f, owner)], owner)},
               {"at": "/launch/genlaunch.py", "tree": cas.ingest(GEN_LAUNCH, self.store)}] + xin + self.use_inputs(ws)
        if "objects_of" in g:
            ins.append({"at": "/objs", "tree": self.objects_input(ws, tmp, g)})
        for p in g["inputs"]:
            if isinstance(p, dict):   # the output of an earlier generator of this recipe: already in the root's view (generate runs in list order), keyed by content
                ins.append({"at": "/stage/" + p["path"], "tree": cas.ingest(os.path.join(ws, "root", p["path"]), self.store)})
                continue
            sub = [f for f in self.files if f == p or under(f, p)]
            ins.append({"at": "/in/" + p, "tree": cas.ingest(os.path.join(self.repo, p), self.store) if sub == [p] else
                        self.ingest_files(tmp, "in%d" % len(ins), sub, p)})
        env = {k: v for k, v in py["env"].items() if k != "PYTHONNOUSERSITE"}
        env.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(py["python"]), "FNVNX_PYMODS": self.pymods})
        doc = {"format": "fnvnx-stage/1", "stage": "gen:%s:%d" % (self.root, i), "class": "build", "tool": "python", "cwd": "/work",
               "argv": [py["python"], "-s", "-B", "/launch/genlaunch.py", "\n".join(g["outputs"]), "/tool/" + g["tool"][len(owner) + 1:]] + g["args"] + (["--section", g["objects_of"]["section"]] if isinstance(g.get("objects_of"), dict) and g["objects_of"].get("section") else []), "env": env,
               "inputs": sorted(py["inputs"] + ins, key=lambda x: x["at"]), "outputs": ["/out"], "ro_root": True}   # a generator writes only /out
        res = self.stage(doc)[1]
        got = sorted(r for r, e in cas.listing(res["outputs"]["/out"], self.store) if e["t"] != "d")
        if got != sorted(g["outputs"]):
            raise BuildRed({"stage": doc["stage"], "exit": 0, "stderr": ["outputs %s differ from the declared %s" % (got, sorted(g["outputs"]))]})
        cas.materialize(res["outputs"]["/out"], self.store, os.path.join(tmp, "out"))
        for o in g["outputs"]:
            os.makedirs(os.path.dirname(os.path.join(ws, "root", o)), exist_ok=True)
            shutil.copyfile(os.path.join(tmp, "out", o), os.path.join(ws, "root", o))

    def table_source(self, ws, groups, t, ti):
        """The source of the link-stage table T (`link.tables[ti]`): the declared tool runs over exactly the objects of this link (GROUPS: [(label, [(unit, .o)])]:
        the root's own and its derived members', mounted at /objs/<label>/<unit>.o) and writes the one file `t["unit"]`, which is then compiled as a unit of the image
        (the root's own flags, plugins and compiler). Stage `table:<root>:<ti>`; the key holds every object's content, the tool and the section name."""
        owner = tree_of(self.roots, t["tool"])
        py = self.pyrt()
        tmp = tempfile.mkdtemp(dir=ws)
        for label, objs in groups:
            for u, p in objs:
                dest = os.path.join(tmp, "objs", label, u + ".o")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(p, dest)
        ins = [{"at": "/tool", "tree": self.ingest_files(tmp, "tool", [f for f in self.files if under(f, owner)], owner)},
               {"at": "/launch/genlaunch.py", "tree": cas.ingest(GEN_LAUNCH, self.store)},
               {"at": "/objs", "tree": cas.ingest(os.path.join(tmp, "objs"), self.store)}]
        env = {k: v for k, v in py["env"].items() if k != "PYTHONNOUSERSITE"}
        env.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(py["python"]), "FNVNX_PYMODS": self.pymods})
        doc = {"format": "fnvnx-stage/1", "stage": "table:%s:%d" % (self.root, ti), "class": "build", "tool": "python", "cwd": "/work",
               "argv": [py["python"], "-s", "-B", "/launch/genlaunch.py", t["unit"], "/tool/" + t["tool"][len(owner) + 1:]] + t["args"] + ["--section", t["section"]],
               "env": env, "inputs": sorted(py["inputs"] + ins, key=lambda x: x["at"]), "outputs": ["/out"], "ro_root": True}
        res = self.stage(doc)[1]
        got = sorted(r for r, e in cas.listing(res["outputs"]["/out"], self.store) if e["t"] != "d")
        if got != [t["unit"]]:
            raise BuildRed({"stage": doc["stage"], "exit": 0, "stderr": ["outputs %s differ from the declared %s" % (got, [t["unit"]])]})
        cas.materialize(res["outputs"]["/out"], self.store, os.path.join(tmp, "out"))
        os.makedirs(os.path.dirname(os.path.join(ws, "root", t["unit"])), exist_ok=True)
        shutil.copyfile(os.path.join(tmp, "out", t["unit"]), os.path.join(ws, "root", t["unit"]))

    def includes(self, ws):
        """unitstage --include specs in recipe order: HOST@/inc/N for a directory of this root or the repo, the sandbox path itself
        for a directory inside an external (a declared input of the tool runtime)."""
        out, n = [], 0
        tg = entry_of(self.roots, self.root + ("@" + self.variant if self.variant else "")).get("target")
        for d in include_closure(self.rec["include"], self.root, self.roots, self.repo, tg, files=self.files):
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
        out += ["/uses/%s/%s" % (u["root"], u["output"]) for u in self.rec.get("include_uses", [])]   # the mounted directory output of another root, an absolute path inside the sandbox
        return out

    def plugin_env(self, ws, drv=1):
        """The common inputs of every plugin stage: the host driver `PLUGIN_CXX` (derived from env.json), the plugin headers of the target compiler
        (`compiler.cxx -print-file-name=plugin`), the tool runtime and the startup files -> dict. A plugin stage's key is f(source content, host
        compiler, flags, headers)."""
        host = "/usr/bin/" + PLUGIN_CXX
        cxx = self.hostpath(self.drivers()[drv])   # the C++ driver (the C driver for a root without C++ units: the same compiler, the same plugin headers)
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
            return self.plus(runtime.build_tool(self.store, sorted(set(exes)), sorted(set(ts + [t for t in trees if not any(under(t, x) for x in ts)]))))   # the plugin headers of the host compiler itself are in its own tree already
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
        return {"host": host, "inc": inc, "rt": rt, "pf": pf, "crt_tree": cas.ingest(crt, self.store)}

    def plugin_build(self, env, ws, n, src, inputs):
        """The stage `plugin:<n>`: the plugin source (and the tracked INPUTS it includes) at their repo paths -> tree of /out (holds <n>.so)."""
        host, inc, rt, pf = env["host"], env["inc"], env["rt"], env["pf"]
        tmp = tempfile.mkdtemp(dir=ws)
        tree = self.ingest_files(tmp, "psrc", [src] + inputs, "")   # at their repo paths: `#include "../names/x.cc"` resolves
        argv = [host, "-shared", "-nostdlib", "-fPIC", "-fno-rtti", "-fno-exceptions", "-O2", "-I" + inc + "/include", "-frandom-seed=" + hashlib.sha256(n.encode()).hexdigest(),
                "-Werror=date-time", "-ffile-prefix-map=/psrc=psrc", "-o", "/out/%s.so" % n, "/plugcrt/crti.o", pf("crtbeginS.o"),
                "/psrc/" + src, pf("crtendS.o"), "/plugcrt/crtn.o"]
        doc = {"format": "fnvnx-stage/1", "stage": "plugin:" + n, "argv": argv, "cwd": "/", "class": "build", "tool": "gcc",
               "env": dict(unitstage.ENV, PATH=os.path.dirname(host)), "outputs": ["/out"],
               "inputs": sorted(rt["inputs"] + [{"at": "/psrc", "tree": tree}, {"at": "/plugcrt", "tree": env["crt_tree"]}], key=lambda i: i["at"])}
        return self.stage(doc)[1]["outputs"]["/out"]

    def plugins(self, ws):
        """Build every plugin of the recipe as a hermetic stage -> [(name, tree of its /out, argv flags)]. The .so is in the store; each CC stage
        declares it as an input."""
        try:
            pl = plugin_decls(self.rec.get("plugins", []), entry_of(self.roots, self.root + "@" + self.variant if self.variant else self.root).get("cxx_origin"),
                              self.files if self.files is not None else tracked(self.repo), self.roots, self.repo)
        except ValueError as e:
            raise BuildRed({"stage": "plugin", "exit": None, "stderr": [str(e)]})
        if not pl:
            return []
        env = self.plugin_env(ws)
        out = []
        for p in pl:
            n = p["name"]
            res = self.plugin_build(env, ws, n, p["source"], p.get("inputs", []))
            flags = ["-fplugin=/plug/%s/%s.so" % (n, n)] + ["-fplugin-arg-%s-%s%s" % (n, k, "=" + v if v else "") for k, v in p["args"].items()] + (
                ["-fplugin-arg-%s-out=%s" % (n, PLUGIN_OUT[n])] if n in PLUGIN_OUT else [])
            out.append((n, res, flags))
        return out

    def analysis_plugin(self, ws, drv=1):
        """The plugin of the gate `objstub` (ANALYSIS_PLUGIN): built by the same stage as every plugin, for the target compiler of the root. It is no
        plugin of the root's compile stages (no root declares it, no CC stage loads it): the gate loads it over the preprocessed text of the units.
        -> tree of its /out."""
        return self.plugin_build(self.plugin_env(ws, drv), ws, ANALYSIS_PLUGIN[0], ANALYSIS_PLUGIN[1], [])

    def compile(self, ws, units):
        rec, drv = self.rec, dict(zip(("c", "cxx"), self.drivers()))
        # KEEP GOING: a unit is built iff every input it declares is produced. A unit that is the output of a red generator, every unit
        # when a `uses` producer is red, and the C++ units when a plugin is red, are blocked by that stage (recorded, never built, never hidden).
        self.blocked.update({u: self.blocked[u] for u in units if u in self.blocked})
        units = [u for u in units if u not in self.blocked]
        kinds = {u: ("c" if u.endswith(CC_EXT) else "cxx" if u.endswith(CXX_EXT) else "asm") for u in units}
        used = sorted({drv["c" if k == "asm" else k] for k in kinds.values()})
        if not used:
            return {}
        cc = self.rec["compiler"].get("external")
        flags = {k: unit_flags(rec, self.roots, self.root + ("@" + self.variant if self.variant else ""), self.hostpath(drv["c" if k == "asm" else k]), k) for k in sorted(set(kinds.values()))}
        mount = ({cc} if cc else set()) | self.named_ext(list(rec["include"]) + [t for fl in flags.values() for t in fl])   # PP mounts what the include path and the flags name
        rtp, _ = self.toolrt(used, mount)
        guest = entry_of(self.roots, self.root + "@" + self.variant if self.variant else self.root).get("target") == "guest"
        pk = ("c", "cxx") if guest else ("cxx",)   # guest: the plugins are loaded by the C and the C++ front end alike
        try:
            self.use_inputs(ws)
        except BuildRed as r:
            self.red.append(dict(r.args[0]))
            self.blocked.update({u: r.args[0]["stage"] for u in units})
            return {}
        guest = entry_of(self.roots, self.root + "@" + self.variant if self.variant else self.root).get("target") == "guest"
        pk = ("c", "cxx") if guest else ("cxx",)   # guest: the plugins are loaded by the C and the C++ front end alike
        try:
            self.built = self.plugins(ws) if any(k in pk for k in kinds.values()) else []
        except BuildRed as r:
            self.red.append(dict(r.args[0]))
            self.blocked.update({u: r.args[0]["stage"] for u, k in kinds.items() if k in pk})
            units = [u for u in units if u not in self.blocked]
            kinds = {u: k for u, k in kinds.items() if u in units}
            self.built = []
        if self.analysis is None and any(k != "asm" for k in kinds.values()):
            try:
                self.analysis = self.analysis_plugin(ws, 1 if "cxx" in kinds.values() else 0)
            except BuildRed as r:   # the gate objstub cannot judge the root: red and named, no unit is blocked by it
                self.red.append(dict(r.args[0]))
        builds = {}
        sos = {}
        for n, t, _ in self.built:   # the plugins each front end can load (plugin_languages): the stage loads exactly those, the gate reads the same fact
            sos[n] = cas._obj(self.store, cas.manifest(t, self.store)["entries"][n + ".so"])
        loadable = plugin_languages(self.hostpath(drv["c"]), self.hostpath(drv["cxx"]), sos) if sos else {"c": [], "cxx": []}
        # RULE: the CC stage (-fpreprocessed, input unit.ii) mounts, and so keys on, only the compiler (its external, if it is one), the
        # declared plugin .so files and unit.ii. Any other external is not mounted there: what is not mounted cannot be read, so the object
        # does not depend on it by construction (a new commit of such an external changes the PP keys, not the CC keys).
        drop = {e["at"] for n, e in self.ext.items() if n != cc}
        for k in sorted(set(kinds.values())):
            fp = os.path.join(ws, "flags-%s.json" % k)
            json.dump({"argv": flags[k]}, open(fp, "w"))   # semantics first, then the root's own
            builds[k] = unitstage.Build(argparse.Namespace(store=self.store, cold=False, cxx=drv["c" if k == "asm" else k], toolrt=rtp, flags_file=fp,
                                                           src_root=os.path.join(ws, "root") + "@/src", include=self.includes(ws), mounted=[i["at"] for i in self.use_inputs(ws)]))
            builds[k].cc_drop = drop | {i["at"] for i in self.use_inputs(ws)}
            builds[k].rt = dict(builds[k].rt, inputs=builds[k].rt["inputs"] + self.use_inputs(ws))
            builds[k].variant = self.variant
            builds[k].cc_flags = list(self.aux)
            builds[k].hint_ns = self.root + ("@" + self.variant if self.variant else "")   # closure hints are per root (unitstage.hint_path)
            if k in pk:   # plugins are loaded by the CC stage only, never by preprocessing (guest roots: C and C++ units; host roots: C++ only)
                ok = loadable[k]
                builds[k].cc_flags = [f for n, _, fl in self.built if n in ok for f in fl] + list(self.aux)
                builds[k].cc_inputs = [("/plug/" + n, t) for n, t, _ in self.built if n in ok]

        def one(u):
            try:
                return u, builds[kinds[u]].unit(u)
            except (sandbox.SbxError, unitstage.UnitError) as e:
                return u, {"status": "cc-failed", "failure": {"stage": getattr(e, "stage", "cc:" + u), "exit": None, "stderr": [str(e)[-300:]]}}
        res, failed = {}, {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.jobs) as ex:
            for u, e in ex.map(one, units):   # every unit is judged, PP and CC alike: a failed unit never stops another
                if e["status"] != "ok":
                    cc = e["status"] == "cc-failed"
                    failed[u] = e.get("failure") or {"stage": "%s:%s" % ("cc" if cc else "pp", u), "exit": e.get("cc_rc" if cc else "pp_rc"),
                                                     "stderr": e["error"].splitlines()}
                    continue
                self.keys |= {e[k] for k in ("pp_key", "du_key", "cc_key") if e.get(k)}
                self.run += (e.get("pp_status") == "run") + (e.get("cc_status") == "run")
                self.hit += (e.get("pp_status") == "hit") + (e.get("cc_status") == "hit")
                res[u] = e["object"]
                if e.get("pp_key"):
                    self.ppkeys[u] = e["pp_key"]
                if e.get("du_key"):
                    self.dukeys[u] = e["du_key"]
        self.failed = [{"unit": u, "stage": failed[u]["stage"], "exit": failed[u]["exit"], "stderr": list(failed[u]["stderr"])[-20:]} for u in units if u in failed]
        return res   # in unit order (UNITS is sorted), so the failures are deterministic

    def link(self, ws, groups, output=None, tag=""):
        """Stage `ld:<root>`: the linker of the compiler with the options the rule fixes (RECIPE.md "Link"). GROUPS: [(label, [(unit, path of
        the .o file)])]: the root's own objects, then those of every `link.roots` root, in list order. Every object is linked whole;
        the guest libraries of `link.libs` are searched (-L, -l) as the gcc driver does for the ILP32 PIE multilib. -> (tree of /out,
        tree of /objs)."""
        lk, cc = self.rec["link"], self.drivers()[0]
        output = output or lk["output"]   # a test is linked to its own output (RECIPE.md, tests); TAG tells the links of one root apart
        pn = subprocess.run([self.hostpath(cc), "-print-prog-name=ld"], capture_output=True, text=True).stdout.strip()
        ld = unitstage.prog(posixpath.normpath(pn))
        # the ld stage reads the compiler's external (the linker is in it, when the compiler is one) and the external of link.libs: it mounts those, no other
        names = ({self.rec["compiler"]["external"]} if "external" in self.rec["compiler"] else set()) | ({lk["libs"]["external"]} if "external" in (lk.get("libs") or {}) else set())
        if "external" in self.rec["compiler"]:
            e = self.ext[self.rec["compiler"]["external"]]
            ld = e["at"] + ld[len(e["host"]):] if ld.startswith(e["host"] + "/") else ld
            rt = self.toolrt([cc], names)[1]
        else:
            rt = self.cached_rt("tool-ld-" + hashlib.sha256(repr((ld, sorted((e["at"], e["tree"]) for n, e in self.ext.items() if n in names))).encode()).hexdigest()[:16], lambda: self.plus(runtime.build_tool(self.store, [ld], []), names))[1]
        od, sd = os.path.join(ws, "objs" + tag), os.path.join(ws, "ld" + tag)
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
                mine = {f[len(self.root) + 1:] for f in self.files if under(f, self.root)}
                src = os.path.join(ws, "root", lk[k]) if link_generated(lk[k], mine, set(generated_outputs(self.rec))) else os.path.join(self.repo, link_file(lk[k], self.root, mine, self.roots, self.files))
                shutil.copyfile(src, os.path.join(sd, os.path.basename(lk[k])))
        lb, pre, post, search, libs, sysin = lk.get("libs"), [], [], [], [], []
        if lb and lb.get("derive"):     # a guest link: the libraries are the pinned driver's answer, derived here (keyed), never stored
            rootdef = set().union(*(undefset.object_symbols(open(p, "rb").read())[0] for _, objs in groups for _, p in objs))
            lb = dict(self.guest_libs(ws, rootdef, open(os.path.join(sd, os.path.basename(lk["script"]))).read())[0], external=lb["external"])
        if lb and "system" in lb:
            # the shared libraries of env.json packages: their content is the input, mounted at /syslib under their own file names
            yd = os.path.join(ws, "syslib")
            os.makedirs(yd)
            bad = envcheck.system_lib_errors(lb["system"], json.load(open(os.path.join(self.repo, ENV_JSON)))["packages"], envcheck.file_owners())
            if bad:
                raise BuildRed({"stage": "ld:" + self.root, "exit": None, "stderr": ["link.libs.system: %s" % b for b in bad]})
            for p in lb["system"]:
                shutil.copyfile(os.path.realpath(p), os.path.join(yd, os.path.basename(p)))
            sysin = [{"at": "/syslib", "tree": cas.ingest(yd, self.store)}]
            search, post = ["-L/syslib"], ["/syslib/" + os.path.basename(p) for p in lb["system"]]
        if kind == "exe":
            # the start and end files and the dynamic linker: the pinned driver's answer for the target, not the recipe's
            hl, mnt = self.host_startfiles(ws, kind)
            bad = envcheck.system_lib_errors(hl["startfiles"] + hl["endfiles"], json.load(open(os.path.join(self.repo, ENV_JSON)))["packages"], envcheck.file_owners())
            if bad or not hl["startfiles"] or not hl["interp"]:
                raise BuildRed({"stage": "drv:" + self.root, "exit": None, "stderr": ["link: the driver's start files %s / interpreter %r: %s" % (hl["startfiles"], hl["interp"], b) for b in bad or ["none derived"]]})
            pre, sysin = hl["startfiles"], sysin + [m for m in mnt if any(under(f, m["at"]) for f in hl["startfiles"] + hl["endfiles"])]
            post = post + hl["endfiles"]
            dl = ["-dynamic-linker", hl["interp"]]
        else:
            dl = []
        if lb and "system" in lb:
            pass
        elif lb:
            at = self.ext[lb["external"]]["at"]
            pre, post = [at + "/" + p for p in lb["startfiles"]], [at + "/" + p for p in lb["endfiles"]]
            search, libs = ["-L" + at + "/" + d for d in lb["dirs"]], ["-l" + n for n in lb["names"]]
        self.linkfacts = {"startend": [self.hostpath(x) for x in pre + post if not x.startswith("/syslib/")],
                          "dirs": [self.hostpath(x[2:]) for x in search if x.startswith("-L") and x != "-L/syslib"], "names": [x[2:] for x in libs],
                          "script": open(os.path.join(sd, os.path.basename(lk["script"]))).read() if "script" in lk else "",
                          "system": list(lb["system"]) if lb and "system" in lb else [], "entry": lk.get("entry")}
        vs = ["--version-script=/ld/" + os.path.basename(lk["version_script"])] if "version_script" in lk else []
        sc = ["-T", "/ld/" + os.path.basename(lk["script"])] if "script" in lk else []
        ds = ["--defsym=%s=%s" % (IMAGE_ENTRY, lk["entry"])] if "entry" in lk else []
        argv = [ld] + LD_KIND[kind] + dl + lk["flags"] + sc + vs + ds + ["--cref", "--no-demangle", "-o", "/out/" + output, "-Map=/out/" + output + ".map"] + pre + search + [
            "--whole-archive"] + args + ["--no-whole-archive"] + (["--start-group"] + libs + ["--end-group"] if libs else []) + post
        objs_tree, ld_tree = cas.ingest(od, self.store), cas.ingest(sd, self.store)
        doc = {"format": "fnvnx-stage/1", "stage": "ld:" + self.root + (":" + tag if tag else ""), "argv": argv, "cwd": "/", "class": "build", "tool": "ld",
               "env": dict(unitstage.ENV, PATH=os.path.dirname(ld)), "outputs": ["/out"],
               "inputs": merge_inputs(rt["inputs"] + [{"at": "/objs", "tree": objs_tree}, {"at": "/ld", "tree": ld_tree}], sysin, self.store, ws)}
        # the reference link (run only to read a failed link's linker-defined names): the same ld, flags, script, inputs, start files and libraries with
        # unresolved symbols ignored; the names its output defines that no input defines are the ones the pinned ld synthesises for this link
        self.ref_doc = dict(doc, stage="ldref:" + self.root, argv=[ld] + LD_KIND[kind] + dl + lk["flags"] + sc + vs + ds + ["--unresolved-symbols=ignore-all", "-o", "/out/ref.elf"]
                            + pre + search + ["--whole-archive"] + args + ["--no-whole-archive"] + (["--start-group"] + libs + ["--end-group"] if libs else []) + post)
        return self.stage(doc)[1]["outputs"]["/out"], objs_tree, [{"at": "/ld", "tree": ld_tree}] + sysin

    def host_startfiles(self, ws, kind):
        """Stage `drv:<root>` for a host exe: the recipe's own pinned C++ driver run with `-###` on the link of a one-line file. It resolves the start
        and end files by searching its own library directories, so those directories' `.o` files (the crt objects; each directory at its real path) are
        declared inputs. -> ({interp, startfiles, endfiles}, [the declared input trees of those files for the link stage])."""
        cxx = self.drivers()[1]
        host = self.hostpath(cxx)
        sd = subprocess.run([host, "-print-search-dirs"], capture_output=True, text=True).stdout
        libs = next(ln for ln in sd.splitlines() if ln.startswith("libraries:")).split("=", 1)[1].split(":")
        crt = lambda d: [f for f in sorted(os.listdir(d)) if f.endswith(".o") and os.path.isfile(os.path.join(d, f))]  # noqa: E731
        dirs = sorted({os.path.realpath(posixpath.normpath(d)) for d in libs if os.path.isdir(d)})
        dirs = [d for d in dirs if crt(d)]    # the directories that hold crt objects
        mnt = []
        for i, d in enumerate(dirs):
            v = os.path.join(ws, "crt%d" % i)
            os.makedirs(v)
            for f in crt(d):
                shutil.copyfile(os.path.join(d, f), os.path.join(v, f))
            mnt.append({"at": d, "tree": cas.ingest(v, self.store)})
        pd = os.path.join(ws, "probe")
        os.makedirs(pd)
        with open(os.path.join(pd, "x.cc"), "w") as f:
            f.write(PROBE_SRC)
        tr = self.toolrt([cxx])[1]
        res = self.driver_stage("drv:" + self.root, cxx, [], ["-pie"] if kind == "exe" else ["-shared"], ["-###", "/probe/x.cc", "-o", "/out/x"],
                                merge_inputs(tr["inputs"] + [{"at": "/probe", "tree": cas.ingest(pd, self.store)}], mnt, self.store, ws))
        text = open(cas._obj(self.store, cas.manifest(res["stderr"], self.store)), errors="replace").read()
        return parse_host_link(text, dirs), mnt

    def driver_stage(self, stage, drv, flags, mode, tail, inputs):
        """The one place a compiler-driver probe stage (drv:, mld:) runs: driver_doc, hermetic. -> the stage result."""
        return self.stage(driver_doc(stage, drv, flags, mode, tail, inputs))[1]

    def link_undefined(self, groups, loader_defs=None):
        """The full static undefined set of the link just attempted (undefset.closure over the symbol tables of its inputs): the objects of the product
        roots (GROUPS), the start and end files, the archive members of the `-l` libraries (pulled by the closure's own rule), the .dynsym of the declared
        system libraries and the declared loader's names, and the names the link script assigns. -> [{symbol, referencers}]."""
        f = self.linkfacts
        rd = lambda p: open(p, "rb").read()  # noqa: E731
        prod = {"%s:%s" % (lab, u): undefset.object_symbols(rd(p)) for lab, objs in groups for u, p in objs}
        rt = {os.path.basename(x): undefset.object_symbols(rd(x)) for x in f["startend"] if os.path.isfile(x)}
        arch = []
        for n in f["names"]:
            ar = next((os.path.join(d, "lib%s.a" % n) for d in f["dirs"] if os.path.isfile(os.path.join(d, "lib%s.a" % n))), None)
            if ar:
                arch += [("lib%s.a(%s)" % (n, m), *undefset.object_symbols(b)) for m, _, b in relocs.ar_members(ar)]
        dyn = set(loader_defs or ())
        for p in f["system"]:
            dyn |= {n for n, _, _, shndx, _ in ldcheck_syms(rd(os.path.realpath(p)), 11) if shndx != 0}
        ld_defs = undefset.script_defined(f["script"]) | self.ld_synthesised(rt, arch) | ({IMAGE_ENTRY} if f.get("entry") else set())
        u = undefset.closure(prod, rt, arch, dyn, ld_defs)
        return [{"symbol": k, "referencers": v} for k, v in u.items()]

    def elf_facts(self, ws, fin, elf_tree, groups):
        """The ELF facts of the linked output (elffacts.py, RECIPE.md "ELF facts of a linked output"): stage `ldfacts:<root>` runs the PINNED readelf of
        env.json (the one beside the linker the compiler names, `-W -S -s`) on the final ELF, and its stdout is cached with the stage; the definer of
        every resolved undefined reference of a product object comes from ld's own cross reference table (`--cref`, in the map of the link stage).
        -> the record (elffacts.record). BuildRed(stage ldfacts:<root>) if the pinned readelf is not a file of a pinned package or of a declared external tree, or a layout does not fit."""
        lk, stage = self.rec["link"], "ldfacts:" + self.root
        name = lk["output"]
        pn = subprocess.run([self.hostpath(self.drivers()[0]), "-print-prog-name=ld"], capture_output=True, text=True).stdout.strip()
        d, b = posixpath.split(posixpath.normpath(unitstage.prog(pn)))
        rd = posixpath.join(d, b[:-2] + "readelf") if b.endswith("ld") else ""
        in_ext = bool(rd) and any(under(rd, e["host"]) or under(rd, e["at"]) for e in self.ext.values())   # a file of a declared external tree is pinned by its commit and tree_sha256
        bad = [] if in_ext else envcheck.system_lib_errors([rd], json.load(open(os.path.join(self.repo, ENV_JSON)))["packages"], envcheck.file_owners()) if rd else ["the linker %r has no readelf beside it" % b]
        if bad:
            raise BuildRed({"stage": stage, "exit": None, "stderr": ["the pinned readelf beside the linker: %s" % x for x in bad]})
        rt = self.cached_rt("tool-readelf-" + hashlib.sha256(repr((rd, sorted((e["at"], e["tree"]) for e in self.ext.values()))).encode()).hexdigest()[:16],
                            lambda: self.plus(runtime.build_tool(self.store, [rd], [])))[1]
        doc = {"format": "fnvnx-stage/1", "stage": stage, "argv": [rd, "-W", "-h", "-S", "-r", "-s", "/linked/" + name], "cwd": "/", "class": "build", "tool": "readelf",
               "env": dict(unitstage.ENV, PATH=os.path.dirname(rd)), "outputs": [],
               "inputs": merge_inputs(rt["inputs"] + [{"at": "/linked", "tree": fin}], [], self.store, ws)}
        res = self.stage(doc)[1]
        text = open(cas._obj(self.store, cas.manifest(res["stdout"], self.store)), errors="replace").read()
        mp = open(cas._obj(self.store, cas.manifest(elf_tree, self.store)["entries"][name + ".map"]), errors="replace").read()
        f = self.linkfacts
        rd_ = lambda p: open(p, "rb").read()  # noqa: E731
        products, defs = {}, {}
        for lab, objs in groups:
            for u, p in objs:
                products["/objs/%s/%s.o" % (lab, u)] = "%s:%s" % (lab, u)
                defs["/objs/%s/%s.o" % (lab, u)] = undefset.object_symbols(rd_(p))[0]
        base = {os.path.basename(x): undefset.object_symbols(rd_(x))[0] for x in f["startend"] if os.path.isfile(x)}
        for n in f["names"]:
            ar = next((os.path.join(dd, "lib%s.a" % n) for dd in f["dirs"] if os.path.isfile(os.path.join(dd, "lib%s.a" % n))), None)
            if ar:
                base.update({"lib%s.a(%s)" % (n, m): undefset.object_symbols(bb)[0] for m, _, bb in relocs.ar_members(ar)})
        for p in f["system"]:
            base[os.path.basename(p)] = {n_ for n_, _, _, shndx, _ in ldcheck_syms(rd_(os.path.realpath(p)), 11) if shndx != 0}

        def defs_of(file):      # ld names an archive member ARCHIVE(MEMBER) with the archive's path, a product object by the path of the link line
            if file in defs:
                return defs[file]
            m = re.fullmatch(r"(.*/)?(lib[^/()]*\.a)\((.*)\)", file)
            return base.get("%s(%s)" % (m[2], m[3])) if m else base.get(posixpath.basename(file))
        try:
            return elffacts.record(name, text, mp, mp, products, defs_of)
        except elffacts.ElfFactsError as e:
            raise BuildRed({"stage": stage, "exit": None, "stderr": [str(e)[-300:]]})

    def guest_libs(self, ws, rootdef, script_text):
        """The guest link's libraries, derived (keyed stages, nothing stored): the driver's `-###` link command read by parse_driver_link, minus the start
        and end files that the one-definition rule (onedef over ROOTDEF, the symbols the product roots define) or the entry rule (entry_object, the ENTRY
        of SCRIPT_TEXT) excludes. Every derived file must lie under the pinned runtime external and in the multilib the guest semantic flags select
        (-print-multi-directory). -> ({dirs, names, startfiles, endfiles}, [excluded objects]); BuildRed otherwise."""
        pin = load_pin(self.repo)
        text, at = self.link_probe(ws)
        raw = parse_driver_link(text, at)
        bad = []
        out = outside_files(text, at)
        if out:
            bad.append("start or end files of the driver's link outside the pinned runtime external: %s" % ", ".join(out))
        stray = in_multilib([x for k in ("startfiles", "endfiles") for x in raw[k]] + raw["dirs"][:2], self.multidir)
        if stray:
            bad.append("derived files outside the multilib %r that flags.semantics selects: %s" % (self.multidir, ", ".join(stray)))
        if bad:
            raise BuildRed({"stage": "drv:" + self.root, "exit": None, "stderr": bad})
        host = self.ext[pin["runtime"]["name"]]["host"]
        objs = {p: {d["symbol"] for d in symbols(os.path.join(host, p), p)[0]} for p in raw["startfiles"] + raw["endfiles"]}
        gone = sorted(set(onedef(objs, rootdef)) | set(entry_object(objs, ldcheck.script_entry(script_text))))
        return {k: ([x for x in v if x not in gone] if k in ("startfiles", "endfiles") else v) for k, v in raw.items()}, gone

    def ld_synthesised(self, rt, arch):
        """The names the pinned ld defines itself for this link: the symbol table of the reference link's output (an empty product, same profile), minus every
        name a start file or library member of the link defines. Read from the linker's output, never listed by hand; a reference link that fails gives
        none (the record's finding names it)."""
        try:
            res = self.stage(self.ref_doc)[1]
        except BuildRed as e:
            self.ref_failure = e.args[0]
            return set()
        d = open(cas._obj(self.store, cas.manifest(res["outputs"]["/out"], self.store)["entries"]["ref.elf"]), "rb").read()
        own = set().union(*(v[0] for v in rt.values()), *(a[1] for a in arch)) if (rt or arch) else set()
        return {n for n, _, _, shndx, typ in ldcheck_syms(d, 2) if shndx != 0 and typ not in (3, 4)} - own

    def link_probe(self, ws):
        """Stage `drv:<root>`: the guest runtime's C++ driver (pin `runtime_driver`, inside the pinned runtime external) run with the guest semantic flags
        (the common set: the profile is one fact for every guest link, native and lifted alike) and `-###` on the link of a one-line C++ file, hermetic (the driver prints the commands it would run; none is run).
        -> (stderr text, the runtime external's mount point): the link command of the multilib the flags select."""
        pin = load_pin(self.repo)
        rt = pin["runtime"]
        self.ext.update(fetch_externals([pin["external"], rt], self.store, self.extcache, ws))
        drv = rt["at"] + "/" + pin["runtime_driver"]
        flags = semantics_for(self.hostpath(self.drivers()[1]), "cxx", SEMANTICS_FLAGS)
        tr = self.toolrt([drv])[1]
        pd = os.path.join(ws, "probe")
        os.makedirs(pd)
        with open(os.path.join(pd, "x.cc"), "w") as f:
            f.write(PROBE_SRC)
        ins = sorted(tr["inputs"] + [{"at": "/probe", "tree": cas.ingest(pd, self.store)}], key=lambda i: i["at"])
        res = self.driver_stage("drv:" + self.root, drv, flags, ["-pie"], ["-###", "/probe/x.cc", "-o", "/out/x.elf"], ins)
        md = self.driver_stage("mld:" + self.root, drv, flags, [], ["-print-multi-directory"], ins)   # the multilib directory the same flags select
        self.multidir = open(cas._obj(self.store, cas.manifest(md["stdout"], self.store)), errors="replace").read().strip()
        return open(cas._obj(self.store, cas.manifest(res["stderr"], self.store)), errors="replace").read(), rt["at"]

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

    def ldcheck(self, ws, elf_tree, objs_tree, extra, loader_defs=None):
        """Stage `ldcheck:<root>`: link/ldcheck.py (python of the python runtime, a pure tool like `generated`) judges the linked ELF from ELF
        symbol tables, writes the crt0 header words and the gate file `ld`. -> tree of /out (the final ELF, ld.json, ld.ids)."""
        lk = self.rec["link"]
        name = lk["output"]
        _, py = self.cached_rt("python", lambda: runtime.build(self.store, sys.executable, []))
        tmp = tempfile.mkdtemp(dir=ws)
        ins = []
        for at, owner in (("/tool", LD_TOOLS[0]), ("/contract", LD_TOOLS[1])):
            ins.append({"at": at, "tree": self.ingest_files(tmp, at[1:], [f for f in self.files if under(f, owner)], owner)})
        if loader_defs is not None:      # the names the declared loader defines, one per line, a declared input at /loader (its content is in the key)
            ld = os.path.join(tmp, "loader")
            os.makedirs(ld)
            with open(os.path.join(ld, "defs.txt"), "w") as f:
                f.write("".join(n + "\n" for n in sorted(loader_defs)))
            ins.append({"at": "/loader", "tree": cas.ingest(ld, self.store)})
        ins += [{"at": "/linked", "tree": elf_tree}, {"at": "/objs", "tree": objs_tree}] + extra
        env = {k: v for k, v in py["env"].items() if k != "PYTHONNOUSERSITE"}
        env.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(py["python"])})
        doc = {"format": "fnvnx-stage/1", "stage": "ldcheck:" + self.root, "class": "build", "tool": "python", "cwd": "/work",
               "argv": [py["python"], "-s", "-B", "/tool/ldcheck.py", "--elf", "/linked/" + name, "--objs", "/objs", "--out", "/out", "--name", name, "--kind", lk.get("kind", "pie")]
               + (["--version-script", "/ld/" + os.path.basename(lk["version_script"])] if "version_script" in lk else [])
               + (["--script", "/ld/" + os.path.basename(lk["script"])] if "script" in lk else [])
               + (["--entry", lk["entry"]] if "entry" in lk else [])
               + ["--lib=/syslib/" + os.path.basename(p) for p in lk.get("libs", {}).get("system", [])]
               + (["--loader-defs", "/loader/defs.txt"] if loader_defs is not None else []),
               "env": env, "inputs": [], "outputs": ["/out"]}
        names = self.named_ext(doc["argv"])   # the externals the stage names (none today: it reads the ELF and the objects), no other
        doc["inputs"] = sorted(py["inputs"] + ins + [{"at": e["at"], "tree": e["tree"]} for n, e in self.ext.items() if n in names], key=lambda x: x["at"])
        return self.stage(doc)[1]["outputs"]["/out"]


def loader_defined(repo, loader, store, jobs, extcache, ws, stage):
    """The names the declared loader root LOADER defines: the global and weak definitions of its objects, built objects-only through the stage cache. -> (the set, the build results: their stage keys and counts belong to the
    caller's); raises BuildRed(stage STAGE) when one of them is invalid or red."""
    ent = load_admit(repo)[split_spec(loader)[0]]
    try:
        rec = read_recipe(repo, ent["recipe"])
    except (OSError, ValueError) as x:
        raise BuildRed({"stage": stage, "exit": None, "stderr": ["loader root %s: unreadable recipe: %s" % (loader, x)]})
    names, rs = set(), []
    for n, d in enumerate([loader]):
        try:
            r = build(repo, d, store, os.path.join(ws, "%d" % n), jobs, extcache, link=False)
        except RecipeError as x:
            raise BuildRed({"stage": stage, "exit": None, "stderr": ["loader root %s: invalid recipe: %s" % (d, x)][:20]})
        if r["status"] == "red":
            f = r["failure"]
            raise BuildRed({"stage": stage, "exit": f["exit"], "stderr": ["loader root %s is red: stage %s" % (d, f["stage"])] + f["stderr"][-18:]})
        names |= {x["symbol"] for x in r["defined"]}
        rs.append(r)
    return names, rs


def emulation_of(flags):
    return next(t[2:] if t != "-m" else flags[i + 1] for i, t in enumerate(flags) if t == "-m" or (t.startswith("-m") and len(t) > 2))


def section_names(path):
    """The section names of ELF32 or ELF64 little-endian object PATH (from the section header string table)."""
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF" or d[4] not in (1, 2) or d[5] != 1:
        raise ValueError("%s: not an ELF32/ELF64 little-endian object" % path)
    w64 = d[4] == 2
    shoff, = struct.unpack_from("<Q" if w64 else "<I", d, 0x28 if w64 else 0x20)
    esz, num, stri = struct.unpack_from("<HHH", d, 0x3a if w64 else 0x2e)
    secs = [struct.unpack_from("<IIQQQQIIQQ" if w64 else "<IIIIIIIIII", d, shoff + i * esz) for i in range(num)]
    st = secs[stri]
    strtab = d[st[4]:st[4] + st[5]]
    return [strtab[x[0]:strtab.index(b"\0", x[0])].decode() for x in secs]


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
    keep["analysis_plugin"] = (res.get("analysis_plugin") or {}).get("sha256")
    keep["link_archives"] = [{k: m[k] for k in ("archive", "member", "sha256")} for m in res.get("link_archives", [])]
    keep["elf"] = res["elf"] and res["elf"]["sha256"]
    for k in ("constants", "contract_names"):
        keep[k] = res[k] and hashlib.sha256(open(res[k], "rb").read()).hexdigest()
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()


def exttree(e, cache):
    """Host directory of external E (a recipe `external` entry) checked out by its exact commit from the bare fetch the build made in
    CACHE/external, into CACHE/exttree/<commit> (shared, atomic). For gates that read what a build linked in."""
    dest = os.path.join(cache, "exttree", e["commit"])
    if not os.path.isdir(dest):
        tmp = "%s.tmp%d-%s" % (dest, os.getpid(), os.urandom(4).hex())
        os.makedirs(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp + ".idx")
        bare = os.path.join(cache, "external", e["commit"])
        subprocess.run(["git", "--git-dir", bare, "read-tree", e["commit"]], env=env, check=True, capture_output=True)
        subprocess.run(["git", "--git-dir", bare, "--work-tree", tmp, "checkout-index", "-a", "-f"], env=env, check=True, capture_output=True)
        os.unlink(tmp + ".idx")
        publish_dir(tmp, dest)
    return dest


_EXT_CHECKED = set()


def owner_root(path, roots, root):
    """The admitted root other than ROOT that holds the repository path PATH (the longest admitted prefix), or None."""
    own = [r for r in roots if r != root and path.startswith(r + "/")]
    return max(own, key=len) if own else None


def resolve_foreign(root, ref, repo=".", cache=None, rec=None):
    """resolve_input for the fact file REF of ROOT's recipe: a repository path is read through resolve_input as a file of its owning root (owner_root), an
    {external, path} through ROOT's own externals. -> what resolve_input returns: (host path, ident)."""
    if isinstance(ref, str):
        own = owner_root(ref, load_admit(repo), root)
        if own is None:
            raise ValueError("%s: %r is not a file of an admitted root other than this one" % (root, ref))
        return resolve_input(own, ref[len(own) + 1:], repo, cache)
    return resolve_input(root, ref, repo, cache, rec)


def resolve_input(root, ref, repo=".", cache=None, rec=None):
    """THE reader of a path named by an input file of a gate (imports.in.json, registry.in.json, ...); no gate opens one itself.
    REF is a bare string (a tracked file or directory of ROOT), {"root": R, "path": P} (a tracked file or directory of the admitted TOOL root R: the
    inputs a declaration derives from live in a root below its consumers, RECIPE.md "Outputs and uses") or {"root": R, "output": NAME} (the declared output NAME of root R, which ROOT's recipe `uses`: the stage index is not part of the contract) or {"external": NAME, "path": P}: NAME must be an `external` of ROOT's recipe,
    its tree is mounted from the pinned commit and checked against its tree_sha256. Red (ValueError) for: an undeclared external, a path that
    is absolute or leaves the tree (.. or a symlink), a missing path, an untracked path, a tree_sha256 mismatch.
    -> (host path, ident); IDENT is "tree_sha256:path" of an external, "root/path" of a tracked file: the gate key takes it."""
    if isinstance(ref, str):
        base, p, kind = os.path.join(repo, root), ref, None
    elif isinstance(ref, dict) and set(ref) == {"root", "path"} and all(isinstance(v, str) for v in ref.values()):
        roots = load_admit(repo)
        if ref["root"] not in roots or roots[ref["root"]].get("class") != "tool":
            raise ValueError("%s: input %r names %r, which is not an admitted tool root" % (root, ref, ref["root"]))
        root, base, p, kind = ref["root"], os.path.join(repo, ref["root"]), ref["path"], None
    elif isinstance(ref, dict) and set(ref) == {"root", "output"} and all(isinstance(v, str) for v in ref.values()):
        roots = load_admit(repo)
        if rec is None:
            rec = load_recipe(repo, roots, root)
        if {"root": ref["root"], "output": ref["output"]} not in (rec.get("uses") or []):
            raise ValueError("%s: input %r is an output of another root that the recipe does not `uses`" % (root, ref))
        prod = load_recipe(repo, roots, ref["root"])
        path = (prod.get("outputs") or {}).get(ref["output"])
        if path is None:
            raise ValueError("%s: root %s declares no output %r" % (root, ref["root"], ref["output"]))
        idx = next((i for i, g in enumerate(prod.get("generated", [])) if path in g["outputs"]), None)
        if idx is None:
            raise ValueError("%s: output %r of %s is made by no generate stage" % (root, ref["output"], ref["root"]))
        return stage_output(root, {"stage": "%s:gen:%d" % (ref["root"], idx), "path": path}, repo, cache)
    elif isinstance(ref, dict) and set(ref) == {"external", "path"} and all(isinstance(v, str) for v in ref.values()):
        kind, p = ref["external"], ref["path"]
        if rec is None:
            rec = load_recipe(repo, load_admit(repo), root)
        e = next((x for x in rec.get("external", []) if x["name"] == kind), None)
        if e is None:
            raise ValueError("%s: input %r names the external %r, which the root's recipe does not declare" % (root, ref, kind))
        if cache is None:
            raise ValueError("%s: input %r needs the gate cache to mount the external" % (root, ref))
        base = exttree(e, cache)
        if (cache, e["commit"]) not in _EXT_CHECKED:
            if cas.ingest(base, os.path.join(cache, "store")) != e["tree_sha256"]:
                raise ValueError("external %r: tree_sha256 differs from the checkout" % kind)
            _EXT_CHECKED.add((cache, e["commit"]))
    elif isinstance(ref, dict) and set(ref) == {"stage", "path"} and all(isinstance(v, str) for v in ref.values()):
        return stage_output(root, ref, repo, cache)
    else:
        raise ValueError("%s: input %r is neither a path, {root, path}, {root, output}, {external, path} nor {stage, path}" % (root, ref))
    if not p or os.path.isabs(p) or ".." in p.split("/"):
        raise ValueError("%s: input path %r is absolute or leaves the tree" % (root, p))
    full = os.path.join(base, p)
    rf, rb = os.path.realpath(full), os.path.realpath(base)
    if rf != rb and not rf.startswith(rb + os.sep):     # the base itself ("." = the whole tree) is inside it
        raise ValueError("%s: input path %r leaves the tree (symlink)" % (root, p))
    if not os.path.exists(full):
        raise ValueError("%s: input path %r does not exist" % (root, p))
    if kind is None:
        rel = posixpath.normpath(posixpath.join(root, p))
        if not subprocess.run(["git", "-C", repo, "ls-files", "--error-unmatch", "--", rel], capture_output=True).returncode == 0:
            raise ValueError("%s: input path %r is not tracked" % (root, p))
        if os.path.isdir(full):
            return _tracked_dir(repo, rel, cache)
        return full, rel
    return full, "%s:%s" % (e["tree_sha256"], p)


def _tracked_dir(repo, rel, cache):
    """A tracked directory REF: a materialised copy in CACHE holding exactly `git ls-files` under REL (an untracked file inside is not in the copy,
    so it is never read), written from the blobs. -> (copy path, ident); the ident carries sha256 over (path, blob sha) of those files, so editing
    a tracked file inside changes it. The one rule of the reader; the generator-input mount calls resolve_input too."""
    if cache is None:
        raise ValueError("input directory %r needs the gate cache to materialise its tracked files" % rel)
    out = subprocess.run(["git", "-C", repo, "ls-files", "-s", "-z", "--", rel], capture_output=True, check=True).stdout.decode()
    files = []
    for ent in filter(None, out.split("\0")):
        meta, _, path = ent.partition("\t")
        mode, sha, _stage = meta.split()
        if mode == "120000":
            raise ValueError("input directory %r holds the symlink %r" % (rel, path))
        files.append((path, sha, mode))
    h = hashlib.sha256()
    for path, sha, _ in files:
        h.update(path.encode() + b"\0" + sha.encode() + b"\n")
    d = h.hexdigest()
    dest = os.path.join(cache, "dirs", d, rel)
    if not os.path.isdir(dest):
        tmp = os.path.join(cache, "dirs", d + ".tmp%d" % os.getpid())
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, rel))
        for path, sha, mode in files:
            q = os.path.join(tmp, path)
            os.makedirs(os.path.dirname(q), exist_ok=True)
            with open(q, "wb") as fh:
                fh.write(subprocess.run(["git", "-C", repo, "cat-file", "blob", sha], capture_output=True, check=True).stdout)
            if mode == "100755":
                os.chmod(q, 0o755)
        try:
            os.rename(tmp, os.path.join(cache, "dirs", d))
        except OSError:   # another process materialised the same content first
            shutil.rmtree(tmp, ignore_errors=True)
    return dest, "%s@%s" % (rel, d)


CPU_FIELDS = {"vendor_id": "vendor_id", "family": "cpu family", "model": "model", "stepping": "stepping", "microcode": "microcode", "model_name": "model name"}


def cpu_mismatch(pin, text=None):
    """Differences between the pin {vendor_id, family, model, stepping, microcode, model_name} (the /proc/cpuinfo fields, ints as numbers) and every
    processor of /proc/cpuinfo (TEXT: the cpuinfo text to judge, default this host's). A pin value null means the field is ABSENT from the processor's
    lines (a VM has no microcode line); it is not a wildcard: a present field is a mismatch."""
    procs, cur = [], {}
    for line in (open("/proc/cpuinfo").read() if text is None else text).splitlines() + [""]:
        k, _, v = line.partition(":")
        if k.strip() == "" and cur:
            procs.append(cur)
            cur = {}
        elif k.strip():
            cur[k.strip()] = v.strip()
    bad = []
    for i, p in enumerate(procs):
        for f, name in CPU_FIELDS.items():
            have = p.get(name)
            want = pin.get(f)
            if isinstance(want, int):
                try:
                    have = int(have, 0)
                except (TypeError, ValueError):
                    pass
            if have != want:
                bad.append("processor %d %s is %r, reference %r" % (i, name, have, want))
    return bad[:6] if procs else ["/proc/cpuinfo lists no processor"]


def deb_resolve(path, tmp, debs, limit=40):
    """(deb, real path) of the file the absolute PATH names in the root made by the union of the installed files of DEBS (<tmp>/deb-<name>/<path>,
    from _deb_tree), following every symlink component the debs ship, such as base-files' /lib64 -> usr/lib64; None when a component is missing, a
    link loop or the final path is not a file. The union is the stage's whole root: no alias and no hand list of usr-merged names is relied on."""
    def find(p):
        return [d for d in debs if os.path.lexists(os.path.join(tmp, "deb-" + d + p))]
    parts, cur = [x for x in path.split("/") if x], "/"
    for _ in range(limit * 8):
        if not parts:
            break
        nxt = os.path.join(cur, parts[0])
        link = [d for d in find(nxt) if os.path.islink(os.path.join(tmp, "deb-" + d + nxt))]
        if link:
            limit -= 1
            if limit < 0:
                return None
            t = os.readlink(os.path.join(tmp, "deb-" + link[0] + nxt))
            full = os.path.normpath(os.path.join(cur, t))
            parts, cur = [x for x in full.split("/") if x] + parts[1:], "/"
            continue
        if not find(nxt):
            return None
        cur, parts = nxt, parts[1:]
    home = [d for d in find(cur) if os.path.isfile(os.path.join(tmp, "deb-" + d + cur))]
    return (home[0], cur) if home else None

def elf_interp(path):
    """The PT_INTERP path of an ELF64 file, or None (read from the program headers)."""
    with open(path, "rb") as f:
        h = f.read(64)
        if h[:4] != b"\x7fELF" or h[4] != 2:
            return None
        off, esz, n = struct.unpack_from("<Q", h, 32)[0], struct.unpack_from("<H", h, 54)[0], struct.unpack_from("<H", h, 56)[0]
        for i in range(n):
            f.seek(off + i * esz)
            t, _, po, _, _, fsz = struct.unpack("<IIQQQQ", f.read(40))
            if t == 3:
                f.seek(po)
                return f.read(fsz).rstrip(b"\0").decode()
    return None


def stage_output(root, ref, repo, cache):
    """{"stage": "ROOT:gen:I" | "ROOT:run:NAME", "path": P}: the declared output P of a generate (I = index in `generated`) or run stage of an admitted
    root, produced by that stage (cached by its key in CACHE/store) and written to CACHE/stageout/<sha256>/P. Red (ValueError) for an unknown root or
    stage, an output the stage does not declare, a red stage. -> (host path, "stage:ROOT:NAME:P:sha256")."""
    if cache is None:
        raise ValueError("%s: input %r needs the gate cache" % (root, ref))
    sroot, _, name = ref["stage"].partition(":")
    roots = load_admit(repo)
    if sroot not in roots:
        raise ValueError("%s: stage ref %r names no admitted root" % (root, ref["stage"]))
    rec = load_recipe(repo, roots, sroot)
    kind, _, key = name.partition(":")
    lst = rec.get("generated" if kind == "gen" else "run", []) if kind in ("gen", "run") else []
    ent = next((g for i, g in enumerate(lst) if (str(i) if kind == "gen" else g.get("name")) == key), None)
    if ent is None:
        raise ValueError("%s: root %s declares no stage %r" % (root, sroot, name))
    if ref["path"] not in ent["outputs"]:
        raise ValueError("%s: stage %s of %s does not declare the output %r (declared: %s)" % (root, name, sroot, ref["path"], ent["outputs"]))
    store = os.path.join(cache, "store")
    j = Job(repo, sroot, store, 1, os.path.join(store, "external"))
    ws = tempfile.mkdtemp(prefix="stageref-")
    try:
        j.externals(ws)
        j.generate(ws)
        j.run_all(ws)
        if j.red:
            raise ValueError("%s: stage %s of %s is red: %s" % (root, name, sroot, j.red[0]["stage"] + ": " + " ".join(j.red[0]["stderr"][-3:])))
        data = open(os.path.join(ws, "root", ref["path"]), "rb").read()
    except BuildRed as r:
        raise ValueError("%s: stage %s of %s is red: %s" % (root, name, sroot, r.args[0]["stage"]))
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    h = hashlib.sha256(data).hexdigest()
    dest = os.path.join(cache, "stageout", h, ref["path"])
    cas.publish_file(dest, data)
    return dest, "stage:%s:%s:%s:%s" % (sroot, name, ref["path"], h)


def host_compilers(rec, cache):
    """Host paths of the recipe's (cc, cxx): /usr/bin/NAME, or the paths inside the external compiler tree, checked out (by exact commit,
    from the bare fetch the build made in CACHE/external) into CACHE/exttree/<commit>. For gates that run the compiler on the host
    (objstub's terminator plugin); the build itself runs it in the sandbox."""
    c = rec["compiler"]
    if "external" not in c:
        return "/usr/bin/" + c["cc"], "/usr/bin/" + c["cxx"]
    e = next(x for x in rec["external"] if x["name"] == c["external"])
    dest = exttree(e, cache)
    return os.path.join(dest, c["cc"]), os.path.join(dest, c["cxx"])


def build(repo, root, store, out, jobs=4, ext=None, aux=None, link=True):
    """Build product ROOT of the checkout REPO into STORE; write OUT/objects.json and the files it names. EXT: bare cache dir of
    externals (default STORE/external). -> the objects dict; raises RecipeError for an invalid recipe."""
    env_blocked(repo)   # BEFORE any stage: nothing below runs or stores on a machine that is not the declared one
    spec = root
    j = Job(repo, spec, store, jobs, ext or os.path.join(store, "external"))
    j.aux = list(aux or [])   # AUX_FLAGS the caller asked for: extra outputs of the CC stage (in its key), never a change of the code generated
    root = j.root
    with open(os.path.join(repo, j.roots[root]["recipe"])) as f:
        full = json.load(f)
    if j.variant and j.variant not in variant_names(full, j.roots[root]):
        raise RecipeError(["root %s has no variant %r" % (root, j.variant)])
    errs = check(full, root, j.files, j.roots, load_pin(repo), repo)
    if errs:
        raise RecipeError(errs)
    units = units_of(j.rec, root, j.files)
    res = {"format": OBJ_FORMAT, "root": spec, "objects": [], "tests": [], "elf": None, "link_roots": [], "constants": None, "contract_names": None, "defined": [], "undefined": [], "plugins": [],
           "link_archives": [], "stage_keys": [], "outputs": {}, "status": "green"}
    ws = tempfile.mkdtemp(prefix="recipe-")
    os.makedirs(out, exist_ok=True)
    all_units = units
    try:
        _copy(repo, [f for f in j.files if under(f, root)], root, os.path.join(ws, "root"))
        # KEEP GOING (RECIPE.md "Keep going"): every unit whose declared inputs are produced is built; a red stage is recorded with its
        # diagnostics, what needs its output is blocked by it and not run; the root is red if anything is red or blocked.
        und, objs = set(), {}
        try:
            j.externals(ws)
            try:
                j.generate(ws)
                j.run_all(ws)
                units = sorted(set(units) | set(j.generated_sources(ws)))
            except BuildRed as r:   # the list of externally sourced units is itself not produced
                j.red.append(dict(r.args[0]))
            objs = j.compile(ws, units + sorted(test_units_of(j.rec)))
        except BuildRed as r:   # an external is not available: no stage of the root has its inputs
            j.red.append(dict(r.args[0]))
            j.blocked.update({u: r.args[0]["stage"] for u in units})
        built_objs = dict(objs)   # unit -> object tree; `objs` is rebound by the link loops below
        all_units, units = units, [u for u in units if u in objs]
        res["objects"], res["failed_units"] = [], []
        for n, t, _ in j.built:
            ent = cas.manifest(t, store)["entries"][n + ".so"]
            dest = os.path.join(out, "plugins", n + ".so")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(cas._obj(store, ent), dest)
            res["plugins"].append({"name": n, "sha256": ent["h"], "path": os.path.abspath(dest)})
        res["outputs"] = {}   # the root's declared outputs (`outputs: {NAME: path}`), each a file of `out` with its sha256: a consumer compares the hash, never the path
        for nm, rel in sorted((j.rec.get("outputs") or {}).items()):
            src = os.path.join(ws, "root", rel)
            if os.path.isdir(src):    # a directory output: its sha256 is over the sorted (relative path, content) of its files
                dest = os.path.join(out, "outputs", rel)
                shutil.copytree(src, dest, dirs_exist_ok=True)
                h = hashlib.sha256()
                for dp, _dn, fn in sorted(os.walk(dest)):
                    for f in sorted(fn):
                        fp = os.path.join(dp, f)
                        h.update(("%s\0%s\0" % (os.path.relpath(fp, dest), hashlib.sha256(open(fp, "rb").read()).hexdigest())).encode())
                res["outputs"][nm] = {"path": os.path.abspath(dest), "sha256": h.hexdigest()}
            elif os.path.isfile(src):
                dest = os.path.join(out, "outputs", rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(src, dest)
                with open(dest, "rb") as fh:
                    res["outputs"][nm] = {"path": os.path.abspath(dest), "sha256": hashlib.sha256(fh.read()).hexdigest()}
        if j.analysis:   # the plugin of the gate objstub (a file of the store, kept beside the root's own plugins)
            ent = cas.manifest(j.analysis, store)["entries"][ANALYSIS_PLUGIN[0] + ".so"]
            dest = os.path.join(out, "plugins", ANALYSIS_PLUGIN[0] + ".so")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(cas._obj(store, ent), dest)
            res["analysis_plugin"] = {"name": ANALYSIS_PLUGIN[0], "sha256": ent["h"], "path": os.path.abspath(dest)}
        for u in units:
            dest = os.path.join(out, "obj", u + ".o")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            ent = cas.manifest(objs[u], store)["entries"]["unit.o"]
            shutil.copyfile(cas._obj(store, ent), dest)
            d, x = symbols(dest, u)
            res["defined"] += d
            und |= x
            row = {"unit": u, "object": os.path.abspath(dest), "sha256": ent["h"], "os_layer": u in j.rec.get("os_layer", [])}
            rep = cas.manifest(objs[u], store)["entries"].get("unit.f2i.json")   # the fnvnx_ftoi report of the unit, when the plugin is loaded
            if rep:
                fd = os.path.join(out, "obj", u + ".f2i.json")
                shutil.copyfile(cas._obj(store, rep), fd)
                row["f2i"] = os.path.abspath(fd)
            su = cas.manifest(objs[u], store)["entries"].get("unit.su")   # the compiler's stack-usage report, when built with --stack-usage 1
            if su:
                sd = os.path.join(out, "obj", u + ".su")
                shutil.copyfile(cas._obj(store, su), sd)
                row["su"] = os.path.abspath(sd)
            res["objects"].append(row)
            bad = sorted(set(section_names(dest)) & set(fact_sections(split_spec(root)[0], j.roots, j.repo)))
            if bad:
                raise BuildRed({"stage": "forbid:" + root, "exit": None, "stderr": ["object %s has the forbidden section(s) %s (a fact section of a root this root depends on: its universe is the other roots, recipe.fact_sections)" % (u, bad)]})
        res.update(defined=sorted(res["defined"], key=lambda d: (d["symbol"], d["object"])), undefined=sorted(und))
        if j.rec.get("link") and link and (j.red or j.blocked or j.failed):
            j.blocked[j.rec["link"]["output"]] = (j.red or j.failed)[0]["stage"]
        elif j.rec.get("link") and link:
          try:
              lk = j.rec["link"]
              name = lk["output"]
              groups = [("00-own", [(u, o["object"]) for u, o in zip(units, res["objects"])])]
              res["link_roots"], defs = [], {}
              built_in = {}     # every candidate root, built objects-only through the stage cache: the symbol index of the definer closure
              for d in link_candidates(j.roots, root, entry_of(j.roots, spec).get("target"), repo):
                  try:
                      r = build(repo, d, store, os.path.join(ws, "in", d.replace("/", "__")), jobs, j.extcache, link=False)   # its objects only: its own link (if any) is not built here
                  except RecipeError as x:
                      raise BuildRed({"stage": "ld:" + root, "exit": None, "stderr": ["input root %s: invalid recipe: %s" % (d, x)][:20]})
                  j.keys |= set(r["stage_keys"])
                  j.run, j.hit = j.run + r["stages"]["run"], j.hit + r["stages"]["hit"]
                  if r["status"] == "red":
                      f = r["failure"]
                      raise BuildRed({"stage": "ld:" + root, "exit": f["exit"], "stderr": ["input root %s is red: stage %s" % (d, f["stage"])] + f["stderr"][-18:]})
                  built_in[d] = r
              members, ambiguous, _missing = definer_closure({x["symbol"] for x in res["defined"]}, und, {d: {"defined": r["defined"], "undefined": r["undefined"]} for d, r in built_in.items()})
              if ambiguous:     # a name defined by several admitted roots: onedef's rule, found here at the closure
                  raise BuildRed({"stage": "ld:" + root, "exit": None, "stderr": ["%s is defined by several admitted roots (%s): one definition per name" % (sym, ", ".join(w)) for sym, w in sorted(ambiguous.items())][:20]})
              for n, d in enumerate(members):
                  r = built_in[d]
                  kept = []     # the input root's objects stay beside this root's result (its own build directory is scratch)
                  for o in r["objects"]:
                      dest = os.path.join(out, "link_roots", d.replace("/", "__"), o["unit"] + ".o")
                      os.makedirs(os.path.dirname(dest), exist_ok=True)
                      shutil.copyfile(o["object"], dest)
                      kept.append({"unit": o["unit"], "object": os.path.abspath(dest), "sha256": o["sha256"]})
                  res["link_roots"].append({"root": d, "sha256": objects_digest(r), "objects": kept})
                  groups.append(("%02d-%s" % (n + 1, d.replace("/", "__")), [(o["unit"], o["object"]) for o in kept]))
                  defs[groups[-1][0]] = r["defined"]
              for ti, t in enumerate(lk.get("tables", [])):    # link-stage tables: the tool runs over exactly the objects of this link, its source is one more unit of the image
                  j.table_source(ws, groups, t, ti)
                  tobj = j.compile(ws, [t["unit"]]).get(t["unit"])
                  if tobj is None:
                      f = (j.failed or [{"stage": "table:%s:%d" % (root, ti), "stderr": []}])[-1]
                      raise BuildRed({"stage": "table:%s:%d" % (root, ti), "exit": None, "stderr": ["the table unit %s did not compile: stage %s" % (t["unit"], f["stage"])] + list(f["stderr"])[-18:]})
                  dest = os.path.join(out, "obj", t["unit"] + ".o")
                  os.makedirs(os.path.dirname(dest), exist_ok=True)
                  ent_ = cas.manifest(tobj, store)["entries"]["unit.o"]
                  shutil.copyfile(cas._obj(store, ent_), dest)
                  if t["section"] in section_names(dest):     # the fixed point: the table's own object holds no entry of the fact it is made from, so one pass is sound
                      raise BuildRed({"stage": "table:%s:%d" % (root, ti), "exit": None, "stderr": ["the table object %s holds the fact section %s: its own entries would be missing from itself" % (t["unit"], t["section"])]})
                  d_, x_ = symbols(dest, t["unit"])
                  res["defined"] = sorted(res["defined"] + d_, key=lambda d: (d["symbol"], d["object"]))
                  und |= x_
                  again, amb2, _m2 = definer_closure({x["symbol"] for x in res["defined"]}, und, {d: {"defined": r["defined"], "undefined": r["undefined"]} for d, r in built_in.items()})
                  if again != members or amb2:      # the table's own references pull in a root the table was not made over: its tool must see every object of the link
                      raise BuildRed({"stage": "table:%s:%d" % (root, ti), "exit": None, "stderr": ["the table object %s needs the roots %s, the link it was made over has %s" % (t["unit"], sorted(set(again) ^ set(members)), members)]})
                  row = {"unit": t["unit"], "object": os.path.abspath(dest), "sha256": ent_["h"], "os_layer": False}
                  res["objects"].append(row)
                  units.append(t["unit"])
                  built_objs[t["unit"]] = tobj
                  groups[0] = (groups[0][0], groups[0][1] + [(t["unit"], row["object"])])
              loader_defs = None
              if lk.get("loader"):      # the declared loader root: the names its objects (and its link.roots') define may stay undefined in this shared image
                  loader_defs, lrs = loader_defined(repo, lk["loader"], store, jobs, j.extcache, os.path.join(ws, "in", "loader"), "ld:" + root)
                  for r in lrs:
                      j.keys |= set(r["stage_keys"])
                      j.run, j.hit = j.run + r["stages"]["run"], j.hit + r["stages"]["hit"]
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
              for tname, tunits in tests_of(j.rec):   # a test: the root's WHOLE product unit set + its own units, same script and flags (RECIPE.md, tests)
                  tout, tobjs, tdef = "test-%s.elf" % tname, [], []
                  for u in tunits:
                      if u not in built_objs:
                          raise BuildRed({"stage": "ld:%s:test-%s" % (root, tname), "exit": None, "stderr": ["test unit %s was not built" % u]})
                      dest = os.path.join(out, "obj", u + ".o")
                      os.makedirs(os.path.dirname(dest), exist_ok=True)
                      ent_ = cas.manifest(built_objs[u], store)["entries"]["unit.o"]
                      shutil.copyfile(cas._obj(store, ent_), dest)
                      tdef += symbols(dest, u)[0]
                      tobjs.append({"unit": u, "object": os.path.abspath(dest), "sha256": ent_["h"]})
                  tgroups = [(groups[0][0], groups[0][1] + [(o["unit"], o["object"]) for o in tobjs])] + groups[1:]
                  tent = cas.manifest(j.link(ws, tgroups, tout, "test-" + tname)[0], store)["entries"][tout]
                  shutil.copyfile(cas._obj(store, tent), os.path.join(out, tout))
                  res["tests"].append({"name": tname, "units": list(tunits), "objects": tobjs, "linked": sorted(u for _, ol_ in tgroups for u, _ in ol_), "defined": sorted(
                      tdef, key=lambda d: (d["symbol"], d["object"])), "elf": {"path": os.path.abspath(os.path.join(out, tout)), "sha256": tent["h"]}})
              res["link_archives"] = j.archive_members(cas.manifest(elf_tree, store)["entries"], name, out)
              fin = j.ldcheck(ws, elf_tree, objs_tree, extra, loader_defs)
              ent = cas.manifest(fin, store)["entries"]
              shutil.copyfile(cas._obj(store, ent[name]), os.path.join(out, name))
              if lk.get("kind") == "exe":
                  os.chmod(os.path.join(out, name), 0o755)    # an executable image has the exec bit
              res["elf"] = {"path": os.path.abspath(os.path.join(out, name)), "sha256": ent[name]["h"]}
              res["ld_gate"] = {}
              for f in ("ld.json", "ld.ids"):
                  shutil.copyfile(cas._obj(store, ent[f]), os.path.join(out, f))
                  res["ld_gate"][f] = {"path": os.path.abspath(os.path.join(out, f)), "sha256": ent[f]["h"]}
              facts = j.elf_facts(ws, fin, elf_tree, groups)     # the link record: sections, defined symbols, definers (consumers never run readelf or nm)
              fp = os.path.join(out, "elffacts.json")
              with open(fp, "w") as fh:
                  json.dump(facts, fh, indent=1, sort_keys=True)
                  fh.write("\n")
              res["elf_facts"] = {"path": os.path.abspath(fp), "sha256": hashlib.sha256(open(fp, "rb").read()).hexdigest()}
          except BuildRed as r:
            j.red.append(dict(r.args[0]))
            fu = {"unit": j.rec["link"]["output"], "stage": r.args[0]["stage"], "exit": r.args[0].get("exit"), "stderr": list(r.args[0]["stderr"])[-20:]}
            if r.args[0]["stage"].startswith("ld:") and getattr(j, "linkfacts", None):
                # the link record carries the full static undefined set (never ld's text: its status decides, its stderr is an attachment)
                try:
                    und = j.link_undefined(groups, loader_defs)
                    fu["undefined"] = und
                    if getattr(j, "ref_failure", None):
                        fu["findings"] = ["reference link (names ld defines itself) failed at %s: %s" % (j.ref_failure["stage"], " | ".join(j.ref_failure["stderr"])[:300])]
                    if not und and r.args[0].get("exit"):
                        fu["findings"] = ["static closure disagrees with ld: ld exit %s with an empty static undefined set (the failure is not an undefined reference the symbol tables show)" % r.args[0]["exit"]]
                except (OSError, ValueError, struct.error) as e:
                    fu["findings"] = ["static undefined set not computed: %s" % e]
                res["link_undefined"] = fu.get("undefined")
            res["failed_units"].append(fu)
        for key in ("constants", "contract_names"):
            if j.rec.get(key):
                shutil.copyfile(os.path.join(ws, "root", j.rec[key]), os.path.join(out, key + ".tsv"))
                res[key] = os.path.abspath(os.path.join(out, key + ".tsv"))
    except BuildRed as r:   # a stage of the root's own that is not keep-going (a constants file ...): red, nothing else known
        j.red.append(dict(r.args[0]))
    except (sandbox.SbxError, OSError, cas.CasError, runtime.RtError, unitstage.UnitError) as e:
        # a store or sandbox failure (EMLINK, ENOSPC, EXDEV ...) is a red build naming its stage: objects.json is always written
        res.update(status="red", failed_units=[], failure={"stage": getattr(e, "stage", "store"), "exit": None, "stderr": [str(e)[-300:]]})
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    if j.red or j.failed or j.blocked:
        first = (j.red or j.failed)[0] if (j.red or j.failed) else None
        if first is None:   # blocked only: cannot happen (a block names a red stage); kept red and loud
            first = {"stage": "blocked", "exit": None, "stderr": ["units blocked without a red stage"]}
        res["failed_units"] = (res.get("failed_units") or []) + [f for f in j.failed if f not in res.get("failed_units", [])]
        res["blocked"] = [{"unit": u, "blocked_by": b} for u, b in sorted(j.blocked.items())]
        res.update(status="red", failure={k: v for k, v in first.items() if k in ("stage", "exit", "stderr")}, red_stages=[r["stage"] for r in j.red])
    res.update(stage_keys=sorted(j.keys), stages={"run": j.run, "hit": j.hit}, units=all_units, pp_keys=dict(sorted(j.ppkeys.items())), du_keys=dict(sorted(j.dukeys.items())))
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
            root, _ = split_spec(opt["--root"])   # the one place the spec is split: nothing below sees root@NAME
            rec = load_recipe(repo, roots, opt["--root"]) if _ else json.load(open(os.path.join(repo, roots[root]["recipe"])))   # a base is checked whole (raw: check() expands its profile): with its variants
            errs = check(rec, root, tracked(repo), roots, load_pin(repo), repo, _)
            print("\n".join(errs) or "recipe valid")
            return 2 if errs else 0
        unknown = set(opt) - {"--root", "--store", "--out", "--jobs", "--ext", "--stack-usage"}
        if unknown or opt.get("--stack-usage", "1") != "1":
            raise RecipeError(["unknown or bad option: %s" % ", ".join(sorted(unknown) or ["--stack-usage"])])
        res = build(repo, opt["--root"], opt["--store"], opt["--out"], int(opt.get("--jobs", 4)), opt.get("--ext"), AUX_FLAGS if "--stack-usage" in opt else None)
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
