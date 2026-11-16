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
EXTERNAL inputs (key "external", RECIPE.md): fetched only by exact commit from the artifacts repository (recipe.artifacts_url) into a bare cache (--ext DIR, shared by
  builds that must not share stores), checked out, ingested; the CAS tree hash must equal tree_sha256 (else the build is red naming
  the external). The tree is a declared input at "at" of EVERY stage (the tool runtime lists it), so stage keys cover its hash. A tree
  is the content-pinned INPUT, not a build output: sharing the fetch between two cold builds shares nothing that a build computes.
  compiler {"external": N, cc, cxx}: the tree is declared to runtime.py tool as HOST@AT (loader closure resolved as inside the sandbox, RECIPE.md).
Outputs in --out: objects.json ("fnvnx-objects/1": root, objects[{unit,object,sha256,os_layer}], elf, constants, defined [{symbol,bind,type,object}] and undefined [symbol] (ELF .symtab of the objects, struct), stage_keys, stages
  {run,hit}, status green|red, failure {stage,exit,stderr tail 20} / failed_units when red), obj/<unit>.o, <name>.elf, constants.tsv.
Assumptions: the compiler and ld are deterministic given the flags of unitstage/toolflags (A-env); the sandbox leaves the channels of (ASM-038, ASM-039)
  sandbox.OPEN_CHANNELS open (see gate_determinism.py for which of them two cold builds expose)."""
import argparse
import atexit
import concurrent.futures
import errno
import hashlib
import importlib.util
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
import artifacts  # noqa: E402
import cas  # noqa: E402
import elffacts  # noqa: E402
import gate_admit  # noqa: E402
import pathflow  # noqa: E402
import envcheck  # noqa: E402
import hostprofile  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "link"))
import relocs  # noqa: E402
import undefset  # noqa: E402
from ldcheck import syms_any as ldcheck_syms  # noqa: E402
import ldcheck  # noqa: E402
import runtime  # noqa: E402
import sandbox  # noqa: E402
import toolflags  # noqa: E402
import unitstage  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # ports/fnv-nx: the parent of the package `verify` (a stage mounts it at /imp)
from verify import plugsem  # noqa: E402
from verify import targets as _targets  # noqa: E402  (the declared targets: verify/targets.json)  (the guest compiler record's facts, one source)
BuildRed = plugsem.BuildRed
lifted_origin = plugsem.lifted_origin
PLUGIN_SOURCES = plugsem.PLUGIN_SOURCES
PLUGIN_ARGS = plugsem.PLUGIN_ARGS
PLUGIN_OUT = plugsem.PLUGIN_OUT
origin_class = plugsem.origin_class
semantics_set = plugsem.semantics_set
plugin_args = plugsem.plugin_args


def __getattr__(name):   # PEP 562: the semantic sets are computed on first use (plugsem.semantic_flags), never at import
    if name in ("SEMANTICS_FLAGS", "SEMANTICS_LIFTED"):
        return getattr(plugsem, name)
    raise AttributeError(name)


SDD_DEFINE = plugsem.SDD_DEFINE
SDD_FLAGS = plugsem.SDD_FLAGS
ANALYSIS_PLUGIN = plugsem.ANALYSIS_PLUGIN
runtime_cflags = plugsem.runtime_cflags
runtime_configure = plugsem.runtime_configure
runtime_configure_patch = plugsem.runtime_configure_patch
sandbox_src = unitstage.sandbox_src   # the one function that composes a unit's sandbox source path (the unit a plugin reports)

FORMAT, OBJ_FORMAT = "fnvnx-recipe/1", "fnvnx-objects/1"
TREE = plugsem.TREE
ADMIT = TREE + "/verify/admit.json"
ENV_JSON = TREE + "/verify/env.json"   # the declared environment of the checkout (l1.py checks the same file)


ARTIFACTS = artifacts.pinned()   # the pinned repository of env.json (artifacts.py, the one source); a test fixture may assign a toy repository (the same seam as deb_pins); no other code does
ARTIFACTS_FILE = artifacts.FILE


def ext_dir(cache):
    """THE external cache of a verdict/run cache CACHE (bare fetches CACHE/external/<commit>, stage-ext records, artifacts.url): the sibling of CACHE/store.
    Fetch, build, exttree, resolve_input and every gate name it through this one function."""
    return os.path.join(cache, "external")


def ext_bare(extc, commit):
    """The bare fetch of the external at COMMIT in the external cache EXTC."""
    return os.path.join(extc, commit)


def ext_checkout(extc, commit):
    """The checkout of the external fetched at COMMIT in the external cache EXTC (fetch_externals writes it, every reader names it through this function)."""
    return os.path.join(extc, commit + ".co")


def ext_of_store(store):
    """The external cache that belongs to the store STORE (CACHE/store -> CACHE/external)."""
    return ext_dir(os.path.dirname(os.path.abspath(store)))


def artifacts_url(extcache):
    """Where the externals of EXTCACHE come from: the record `EXTCACHE/artifacts.url` that the caller of a run wrote (set_artifacts: l1.py --artifacts,
    recipe.py --artifacts), else the pinned repository of env.json. Never the process environment: a URL is an argument of the run, kept beside the
    cache it fills, so a gate subprocess and a test read the same one."""
    return artifacts.recorded(extcache) or ARTIFACTS


def set_artifacts(extcache, url):
    artifacts.record(extcache, url)


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
GUEST_PIN = plugsem.GUEST_PIN
GUEST_TARGETS = ("guest", "host")


_QINC = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)


def plugin_inputs(source, files, roots, repo=".", tree=None):
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
        with open(os.path.join(tree, f[len(TREE) + 1:]) if tree else os.path.join(repo, f), encoding="utf-8", errors="surrogateescape") as fh:   # TREE is a directory of its own on the disk
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


def plugin_files(repo=".", files=None, roots=None, tree=None):
    """Every plugin source of PLUGIN_SOURCES with the files it includes (plugin_inputs), in REPO -> sorted [repo path]. FILES and ROOTS default to the tracked
    files and the admitted roots of REPO (a checkout that is no git repository gives its own file list and its root, as stamp.py does)."""
    files, roots = tracked(repo) if files is None else files, load_admit(repo) if roots is None else roots
    return sorted(set(PLUGIN_SOURCES.values()) | {i for q in PLUGIN_SOURCES.values() for i in plugin_inputs(q, files, roots, repo, tree)})


def plugin_decls(names, origin, files=(), roots=(), repo="."):
    """[{name, source, inputs, args}] of the root's `plugins` NAMES (each entry {"name": PLUGIN}) for a root whose declared `cxx_origin` is ORIGIN: source and
    arguments come from recipe.py alone (PLUGIN_SOURCES, plugin_args), the inputs are derived from the source (plugin_inputs). A name outside PLUGIN_SOURCES is
    refused by plugin_errors."""
    want = plugin_args(origin)
    return [{"name": p["name"], "source": PLUGIN_SOURCES[p["name"]], "inputs": plugin_inputs(PLUGIN_SOURCES[p["name"]], files, roots, repo), "args": want[p["name"]]} for p in names]


def plugin_flags(name, args, so, out=None):
    """The compiler arguments that load plugin NAME from the file SO with its fixed ARGS (and its -out file OUT, when it has one): the ONE place they are spelled,
    used by the CC stage (Job.plugins: SO is the stage's mount) and by the gates that run the compiler on a unit's preprocessed text (root_plugin_flags)."""
    return ["-fplugin=" + so] + ["-fplugin-arg-%s-%s%s" % (name, k, "=" + v if v else "") for k, v in args.items()] + (["-fplugin-arg-%s-out=%s" % (name, out)] if out else [])


def root_plugin_flags(built_plugins, origin, outdir):
    """The flags that load, in load order, every plugin the build of a root used (objects.json `plugins` [{name, path}]: the .so files of its plugin stages) for a
    root with `cxx_origin` ORIGIN: what the CC stage loads, with the plugins' -out files in the directory OUTDIR. A gate that compiles a unit of the root outside the
    stage must see the same dialect (the attributes the root's plugins register), so it takes these and no list of its own."""
    want = plugin_args(origin)
    return [f for p in built_plugins for f in plugin_flags(p["name"], want[p["name"]], p["path"], os.path.join(outdir, os.path.basename(PLUGIN_OUT[p["name"]])) if p["name"] in PLUGIN_OUT else None)]


LANG_X = {"c": "c", "cxx": "c++", "asm": "assembler-with-cpp"}   # the driver's -x language of each kind of unit
# What a guest build.json may carry (per-root classes); every other -f/-m/... token comes from flags.semantics and is refused here. Warnings and
# diagnostics, defines, the language standard, the optimisation level (-O0..-O3, -Os, -Og: not -Ofast, which switches FP semantics), debug info,
# section splitting. -Wa, -Wl, -Wp are driver pass-throughs, not warnings. `--param` with the compiler's own garbage-collector knobs
# (ggc-min-expand, ggc-min-heapsize): they change when the compiler collects its memory, never the code it emits (the gcstress variant).
PER_ROOT = re.compile(r"--param|ggc-min-(?:expand|heapsize)=[0-9]+|-W(?![alp],)[^\s]*|-D[^\s]+|-std=[^\s]+|-O[0-3sg]|-g[^\s]*|-ffunction-sections|-fdata-sections|-ftime-report")
# The options of every link, fixed by the rule (RECIPE.md "Link"): the specs of the gcc driver for the ILP32 PIE multilib, no gc-sections, no id.
AUX_FLAGS = ["-fstack-usage"]   # build --stack-usage 1: the CC stage also writes unit.su (documented: one line per function, its frame size and whether it is static); no code generation changes
LD_FIXED = ["--build-id=none", "-pie", "--no-dynamic-linker", "-z", "text", "-z", "now", "-z", "pack-relative-relocs", "-z", "max-page-size=0x1000",
            "-z", "common-page-size=0x1000"]
LD_KIND = {"pie": LD_FIXED, "exe": ["--build-id=none", "-pie", "-z", "text", "-z", "now"], "shared": ["--build-id=none", "-shared", "-z", "text", "-z", "now"]}
LD_TOOLS = (TREE + "/link", TREE + "/contract")   # tool roots of the ldcheck stage: link/ldcheck.py, and gatefile.py at /contract
WERROR_ATTR = "-Werror=attributes"   # required in flags.c and flags.cxx of every guest root
EXT_KEYS = {"name", "repo", "ref", "commit", "tree_sha256", "at"}
STAGE_EXT_KEYS = {"name", "root", "output", "at"}   # an external that is the declared output of another root's stages (the compiler stage): built, never fetched


def is_stage_ext(e):
    """E is an external produced by a root's stages (STAGE_EXT_KEYS), not a fetched commit."""
    return isinstance(e, dict) and set(e) == STAGE_EXT_KEYS
SANDBOX_PATHS = ("/src", "/inc", "/in", "/uses", "/out", "/tool", "/objs", "/ld", "/syslib", "/linked", "/arch", "/imp", "/contract", "/work", "/proc", "/dev", "/tmp", "/oldroot")
MOUNT_PREFIXES = SANDBOX_PATHS + ("/ext", "/launch", "/deb", "/plug", "/probe", "/exec")   # every absolute path a stage can open: the stage paths above, the externals ("at": /ext/<name>) and the launcher (/launch/genlaunch.py), the debs of a run stage (/deb) and its tool program (/exec), the plugin objects (/plug) and the driver probe (/probe); gate_abspath.py reads this one constant
def require_mounted(path):
    """PATH when it is under MOUNT_PREFIXES (the stage's own mount namespace), else ValueError: the one guard of a launcher that runs a tool by path (gate_abspath.py accepts a dynamic load only through it)."""
    if not any(path == m or path.startswith(m + "/") for m in MOUNT_PREFIXES):
        raise ValueError("%s is not under a mount prefix %s" % (path, MOUNT_PREFIXES))
    return path


STYPE = {0: "NOTYPE", 1: "OBJECT", 2: "FUNC", 5: "COMMON", 6: "TLS", 10: "IFUNC"}
SBIND = {1: "GLOBAL", 2: "WEAK", 10: "UNIQUE"}
CC_EXT, CXX_EXT, ASM_EXT = (".c",), (".cc", ".cpp", ".cxx"), (".s", ".S")
LONG_R = ("unresolved-symbols", "allow-shlib-undefined", "noinhibit-exec", "defsym", "wrap", "allow-multiple-definition",
          "warn-unresolved-symbols")


class RecipeError(Exception):
    def __init__(self, errors):
        Exception.__init__(self, "; ".join(errors))
        self.errors = list(errors)


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
    libraries; endfiles: the .o files after the last `-l` library (the none-elf driver prints no `--end-group`); dirs: the -L directories inside the external, in order, each once; names: the -l libraries in order of
    appearance (the group's included). A `-L` or `.o` outside the external is not a guest runtime file and is not taken."""
    line = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("COLLECT_GCC_OPTIONS=")][-1]   # each command of -### follows its COLLECT_GCC_OPTIONS line
    toks = [t.strip('"') for t in line.split()]
    rel = lambda t: posixpath.normpath(t)[len(at) + 1:] if posixpath.normpath(t).startswith(at + "/") else None  # noqa: E731
    out = {"dirs": [], "names": [], "startfiles": [], "endfiles": []}
    last_l = max((i for i, t in enumerate(toks) if t.startswith("-l") and len(t) > 2), default=-1)
    for i, t in enumerate(toks):
        if t.startswith("-L") and rel(t[2:]):
            if rel(t[2:]) not in out["dirs"]:
                out["dirs"].append(rel(t[2:]))
        elif t.startswith("-l") and len(t) > 2:
            out["names"].append(t[2:])
        elif t.endswith(".o") and rel(t):
            out["endfiles" if i > last_l else "startfiles"].append(rel(t))
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
        pv = tempfile.mkdtemp(dir=ws)   # private: merge_inputs runs more than once in one workspace (a name built from ws and a counter collides across calls)
        d = os.path.join(pv, "merged")
        cas.materialize(host["tree"], store, d)
        sub = os.path.join(d, e["at"][len(host["at"]) + 1:])   # (the input's own root when the paths are equal)
        os.makedirs(sub, exist_ok=True)
        ov = os.path.join(pv, "overlay")
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
    return dict(rec, link=dict(lk, script=TREE + "/" + prof["script"], libs={"external": load_pin(repo)["external"]["name"], "derive": True}))


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
    guest: the game's compiler (the external of verify/guest_compiler.json, PIN, and its drivers derived from the one triplet of targets.json), the full plugin set of PLUGIN_SOURCES in load order
    (plugin_decls takes the arguments from recipe.py alone), the compiler's external (it holds the runtime too: one tree).
    host: the distribution compiler pinned by env.json (PLUGIN_CXX's package), no plugin, no external. -> (fields, errors)."""
    if profile == "host":
        return {"compiler": {"cc": PLUGIN_CXX.replace("g++", "gcc"), "cxx": PLUGIN_CXX}, "plugins": [], "external": []}, []
    if profile != "guest":
        return None, ["profile %r is unknown (%s)" % (profile, " | ".join(PROFILES))]
    if not (isinstance(pin, dict) and isinstance(pin.get("external"), dict)):
        return None, ["profile guest: %s is missing or has no external" % GUEST_PIN]
    ex = pin["external"]
    cc, cxx = pin_drivers()
    return {"compiler": {"external": ex["name"], "cc": cc, "cxx": cxx}, "plugins": [{"name": n} for n in PLUGIN_SOURCES],
            "external": [dict(ex)]}, []


UNIT_KEYS = ("flags", "include", "units", "link", "profile", "variants")   # the keys of a root that compiles units: a root with none of them has no unit (`compiler` and `plugins` stay allowed: a run step's {"plugin": NAME} input builds against the root's compiler)


def generated_only(rec):
    """A recipe of generated stages only (RECIPE.md "Generated-only roots"): `generated` or `run` is a non-empty list and none of the keys that compile or link
    (UNIT_KEYS) is written; `compiler` and `plugins` may be (the compiler stage: its `run` steps take the root's plugins as inputs). Such a root has no unit (check refuses
    a translation unit in it), so it needs no flags; with no `compiler` of its own it needs none either."""
    return isinstance(rec, dict) and bool(rec.get("generated") or rec.get("run")) and not any(k in rec for k in UNIT_KEYS)


def fill_generated_only(rec):
    """REC of a generated-only root with the fields every reader expects, empty: no unit is compiled, so the compiler named (the host profile's) is
    never run and no flag is ever passed. Any other recipe: REC unchanged."""
    if not generated_only(rec):
        return rec
    return dict(rec, units="all", include=[], flags={"c": [], "cxx": [], "asm": []}, compiler=rec.get("compiler") or profile_fields("host", None)[0]["compiler"])


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


def expand_recipe(rec, pin, repo, files=None):
    """THE ONE EXPANSION POINT of a recipe: REC with its profile (expand_profile) and its foreach steps (expand_foreach) expanded -> (recipe, profile errors, foreach errors). With FILES
    (the tracked files) a foreach table that is not tracked is a foreach error. A malformed foreach is a foreach error and the recipe comes back without its foreach steps (every rule
    then judges the rest). Expansion is a projection, expand(expand(r)) == expand(r): the expanded form is its own expansion. Only this function and nothing else calls expand_profile or
    expand_foreach (`tests/test_expand_once.py` checks the call sites by ast); read_recipe and check take their recipe from here."""
    rec, perr = expand_profile(rec, pin)
    ferr = []
    try:
        if files is not None:
            ferr += ["run foreach table %s is not a tracked file" % t for t in (g["foreach"]["table"] for g in (rec.get("run") if isinstance(rec.get("run"), list) else [])
                                                                                if isinstance(g, dict) and isinstance(g.get("foreach"), dict) and isinstance(g["foreach"].get("table"), str)) if t not in files]
        rec = expand_foreach(rec, repo)
    except ValueError as e:
        ferr.append(str(e))
        rec = dict(rec, run=[g for g in (rec.get("run") if isinstance(rec.get("run"), list) else []) if not (isinstance(g, dict) and "foreach" in g)])
    return rec, perr, ferr


def read_recipe(repo, path):
    """The recipe file PATH of the checkout REPO, expanded (expand_recipe with the checkout's guest compiler pin). Errors of the profile are reported by check(), which takes its
    recipe from the same expand_recipe; a reader that only needs the values takes the expanded recipe. ValueError on a malformed foreach."""
    with open(os.path.join(repo, path)) as f:
        rec = json.load(f)
    rec, _perr, ferr = expand_recipe(rec, load_pin(repo), repo)
    if ferr:
        raise ValueError(ferr[0])
    return rec


FOREACH_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
FOREACH_KEY = re.compile(r"[A-Za-z0-9_.-]+")
FOREACH_TOKEN = re.compile(r"\{\{|\}\}|\{([A-Za-z][A-Za-z0-9_]*)\}")


def foreach_rows(table, key, text):
    """The rows of a foreach table (RECIPE.md "foreach"): TSV, a header line of column names, then one row per line, each with exactly the header's fields. KEY is a column
    whose values are unique plain names (FOREACH_KEY). -> [{column: field}]; ValueError for any malformed, empty or duplicate row."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        raise ValueError("foreach %s: the table is empty (it needs a header line)" % table)
    head = lines[0].split("\t")
    if not all(FOREACH_NAME.fullmatch(c) for c in head) or len(set(head)) != len(head):
        raise ValueError("foreach %s: the header must be unique column names [A-Za-z][A-Za-z0-9_]*, tab separated" % table)
    if key not in head:
        raise ValueError("foreach %s: the key column %r is not in the header %s" % (table, key, head))
    rows, seen = [], set()
    for n, ln in enumerate(lines[1:], 2):
        f = ln.split("\t")
        if len(f) != len(head):
            raise ValueError("foreach %s: line %d has %d fields, the header has %d" % (table, n, len(f), len(head)))
        r = dict(zip(head, f))
        if not FOREACH_KEY.fullmatch(r[key]):
            raise ValueError("foreach %s: line %d: key %r is not a plain name (letters, digits, _ . -)" % (table, n, r[key]))
        if r[key] in seen:
            raise ValueError("foreach %s: line %d: key %r twice" % (table, n, r[key]))
        seen.add(r[key])
        rows.append(r)
    if not rows:
        raise ValueError("foreach %s: the table has no row" % table)
    return rows


def foreach_subst(text, row, where):
    """TEXT with every {COLUMN} replaced by the row's field ({{ and }} are literal braces); a name that is no column, or a lone brace, is ValueError."""
    def one(m):
        if m.group(0) in ("{{", "}}"):
            return m.group(0)[0]
        if m.group(1) not in row:
            raise ValueError("%s: {%s} is no column of the table (%s)" % (where, m.group(1), sorted(row)))
        return row[m.group(1)]
    out = FOREACH_TOKEN.sub(one, text)
    rest = FOREACH_TOKEN.sub("", text)
    if "{" in rest or "}" in rest:
        raise ValueError("%s: a lone brace in %r (write {COLUMN}, {{ or }})" % (where, text))
    return out


def foreach_spec_rows(g, repo):
    """(rows, key) of the foreach of step G: `{table, key}` (a tracked TSV file) or `{range: N}` (N rows with the columns i = 0..N-1 and n = N, key i)."""
    fe, name = g["foreach"], g.get("name")
    if isinstance(fe, dict) and set(fe) == {"range"}:
        n = fe["range"]
        if not (isinstance(n, int) and not isinstance(n, bool) and n >= 1):
            raise ValueError("run %r: foreach range must be an integer >= 1 (an empty fan-out is red)" % name)
        return [{"i": str(i), "n": str(n)} for i in range(n)], "i"
    if not (isinstance(fe, dict) and set(fe) == {"table", "key"} and isinstance(fe["table"], str) and isinstance(fe["key"], str) and rel_ok(fe["table"])):
        raise ValueError("run %r: foreach must be {table: tracked file path, key: column} or {range: N}" % name)
    if repo is None:
        raise ValueError("run %r: foreach needs the checkout to read its table" % name)
    try:
        with open(os.path.join(repo, fe["table"]), encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError("run %r: foreach table %s cannot be read: %s" % (name, fe["table"], e))
    return foreach_rows(fe["table"], fe["key"], text), fe["key"]


def expand_foreach(rec, repo):
    """REC with every `run` step that has `foreach: {table, key}` or `foreach: {range: N}` replaced by one step per row (a row of a tracked TSV file of the checkout REPO,
    or i = 0..N-1 with n = N): the step is the template with {COLUMN} replaced in its `args`, `outputs`, `dirs` and `env` values, named NAME.<key field>. The table is read
    here and is no stage input, so a changed row changes only the steps whose expanded text changed, and the key of a step is a function of its own row, tool and inputs.
    A consumer's input {"stage": "ROOT:run:NAME", "path": TEMPLATE, "rows": "all"} (TEMPLATE = an output or dir of the foreach step as written) becomes one stage ref per row in
    row order, and the arg "@rows" becomes the mount paths /in/K of those inputs in that order. This is the one place a foreach is expanded (called by expand_recipe only), so every other reader sees plain static steps.
    Expansion is a projection: the marker step of a build-time foreach (`foreach` carrying `row`) and the row steps are their own expansion. ValueError on a malformed form, table or duplicate output."""
    rs = rec.get("run")
    if not isinstance(rs, list) or not any(isinstance(g, dict) and ("foreach" in g or any(isinstance(r, dict) and r.get("rows") == "all" for r in (g.get("inputs") if isinstance(g.get("inputs"), list) else []))) for g in rs):
        return rec
    out, outputs, fan = [], {}, {}   # fan: {NAME: (template step, rows, key)}
    for g in rs:
        if not (isinstance(g, dict) and "foreach" in g) or isinstance(g["foreach"].get("table") if isinstance(g["foreach"], dict) else None, dict):
            continue
        rows, key = foreach_spec_rows(g, repo)
        fan[g.get("name")] = (g, rows, key)
    for g in rs:
        if not isinstance(g, dict):
            out.append(g)
            continue
        if "foreach" not in g:
            out.append(foreach_consume(g, fan))
            continue
        if isinstance(g["foreach"], dict) and "row" in g["foreach"] and isinstance(g["foreach"].get("table"), dict):   # the marker step foreach_dynamic returned: expanded already, kept as it is
            out.append(g)
            continue
        if isinstance(g["foreach"], dict) and isinstance(g["foreach"].get("table"), dict):
            out.append(foreach_dynamic(g, rec))
            for o in step_outputs(out[-1]):
                outputs[o] = g.get("name")
            continue
        name, (_, rows, key) = g.get("name"), fan[g.get("name")]
        for row in rows:
            where = "run %r row %s" % (name, row[key])
            st = foreach_row_step(g, row, key)
            for o in step_outputs(st):
                if o in outputs:
                    raise ValueError("%s: output %s is also an output of %s (an output of a foreach step must name the row: {%s})" % (where, o, outputs[o], key))
                outputs[o] = st["name"]
            out.append(foreach_consume(st, fan))
    return dict(rec, run=out)


def foreach_row_step(g, row, key):
    """The step of one row of foreach step G: G without `foreach`, named NAME.<key field>, {COLUMN} replaced in args, outputs, dirs and env values."""
    where = "run %r row %s" % (g.get("name"), row[key])
    st = {k: v for k, v in g.items() if k != "foreach"}
    st["name"] = "%s.%s" % (g.get("name"), row[key])
    for k in ("args", "outputs", "dirs"):
        if isinstance(st.get(k), list):
            st[k] = [foreach_subst(x, row, where) if isinstance(x, str) else x for x in st[k]]
    if isinstance(st.get("env"), dict):
        st["env"] = {k: foreach_subst(v, row, where) if isinstance(v, str) else v for k, v in st["env"].items()}
    return st


def foreach_dynamic(g, rec):
    """The marker step of a foreach whose table is produced at build time: `foreach: {table: {stage, path} | {external, path}, key, into: DIR}`. Its rows are not known
    until the table exists, so no row step is made here: the step stays ONE step that declares the directory DIR (`dirs: [DIR]`, no `outputs`); the row template (the step as
    written) is kept under foreach.row, and Job.run_foreach runs one step per row of the table at build time, each writing under DIR. ValueError on a malformed form."""
    fe, name = g["foreach"], g.get("name")
    t = fe.get("table")
    if not (set(fe) == {"table", "key", "into"} and isinstance(fe["key"], str) and isinstance(fe["into"], str) and rel_ok(fe["into"])
            and isinstance(t, dict) and set(t) in ({"stage", "path"}, {"external", "path"}) and all(isinstance(v, str) for v in t.values())):
        raise ValueError("run %r: a build-time foreach is {table: {stage, path} | {external, path}, key: column, into: directory}" % name)
    if "external" in t and t["external"] not in [e.get("name") for e in rec.get("external", []) if isinstance(e, dict)]:
        raise ValueError("run %r: foreach table external %r is not declared" % (name, t["external"]))
    outs = g.get("outputs")
    if not (isinstance(outs, list) and outs and all(isinstance(o, str) and under(o, fe["into"]) and ("{" + fe["key"] + "}") in o for o in outs)):
        raise ValueError("run %r: the outputs of a build-time foreach step lie under `into` %s and name the row ({%s})" % (name, fe["into"], fe["key"]))
    if g.get("dirs"):
        raise ValueError("run %r: a build-time foreach step has `into`, not `dirs`" % name)
    row = {k: v for k, v in g.items() if k != "foreach"}
    return dict({k: v for k, v in g.items() if k != "outputs"}, outputs=[], dirs=[fe["into"]], foreach=dict(fe, row=row))


def foreach_consume(g, fan):
    """Step G with its `"rows": "all"` inputs expanded (see expand_foreach). A rows input may carry `"as": NAME`; the arg `@rows:NAME` is then that group's mounts in row
    order, and the bare `@rows` is the group of the step's only rows input. Two groups need names (unique); an `@rows[:NAME]` with no such input is red."""
    ins, nm0 = g.get("inputs"), g.get("name")
    is_rows = lambda r: isinstance(r, dict) and r.get("rows") == "all"   # noqa: E731
    args = g.get("args") if isinstance(g.get("args"), list) else []
    if not isinstance(ins, list) or not any(is_rows(r) for r in ins):
        if any(isinstance(a, str) and (a == "@rows" or a.startswith("@rows:")) for a in args):
            raise ValueError("run %r: an @rows arg needs an input {stage, path, rows: all}" % nm0)
        return g
    new, groups = [], {}   # groups: {name or None: [mounts]}
    nrows = sum(1 for r in ins if is_rows(r))
    for r in ins:
        if not is_rows(r):
            new.append(r)
            continue
        if set(r) not in ({"stage", "path", "rows"}, {"stage", "path", "rows", "as"}) or not all(isinstance(r[k], str) for k in set(r) - {"rows"}):
            raise ValueError("run %r: a rows input is exactly {stage: ROOT:run:NAME, path: TEMPLATE, rows: \"all\"[, as: NAME]}" % nm0)
        label = r.get("as")
        if label is None and nrows > 1:
            raise ValueError("run %r: with several rows inputs each needs `as`" % nm0)
        if label is not None and not FOREACH_NAME.fullmatch(label):
            raise ValueError("run %r: rows input name %r is no plain name" % (nm0, label))
        if label in groups:
            raise ValueError("run %r: rows input name %r twice" % (nm0, label))
        _, _, step = r["stage"].partition(":")
        kind, _, nm = step.partition(":")
        if kind != "run" or nm not in fan:
            raise ValueError("run %r: rows input %s names no foreach step of this recipe" % (nm0, r["stage"]))
        tmpl, rows, key = fan[nm]
        if r["path"] not in step_outputs(tmpl):
            raise ValueError("run %r: rows input path %r is no output of foreach step %s as written (%s)" % (nm0, r["path"], nm, step_outputs(tmpl)))
        groups[label] = []
        for row in rows:
            groups[label].append("/in/%d" % (len(new) + 1))
            new.append({"stage": "%s:run:%s.%s" % (r["stage"].partition(":")[0], nm, row[key]), "path": foreach_subst(r["path"], row, "run %r row %s" % (nm, row[key]))})
    out = []
    for a in args:
        if isinstance(a, str) and (a == "@rows" or a.startswith("@rows:")):
            label = None if a == "@rows" else a[len("@rows:"):]
            if label not in groups:
                raise ValueError("run %r: %s names no rows input%s" % (nm0, a, "" if label else " (a bare @rows needs exactly one rows input without `as`)"))
            out += groups[label]
        else:
            out.append(a)
    return dict(g, inputs=new, args=out)


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


def missing_recipes(roots, files):
    """[str] one error per recipe file that an admit row names (gate_admit.recipe_paths: `recipe` string, {target: path} map, variants) and that is no git-tracked file of
    FILES: a row without its build.json in the same tree would stop every build with a bare "No such file"."""
    return ["admit.json: root %s names recipe %s, which is not a tracked file of the tree (the recipe enters with the row, in the same commit)" % (p, rp)
            for p, r in sorted(roots.items()) for rp in gate_admit.recipe_paths(r) if isinstance(rp, str) and rp not in files]


def import_trees(roots, owner):
    """{declared import root: [its trees]} of the admitted root OWNER (admit.json `imports`): the roots whose Python modules a tool of OWNER may import."""
    return {r: gate_admit.trees(roots[r]) for r in (roots[owner].get("imports", []) if owner in roots and isinstance(roots[owner].get("imports", []), list) else []) if r in roots}


def declared_import_uses(owner, imports, files, repo="."):
    """{declared import root} that some module of the declaring root OWNER imports from: every tracked .py file of OWNER, its absolute imports (`ast`, any depth) taken by the
    one rule (gate_admit.import_roots: a name that is, or lies under, the module prefix of a declared root; `from a import b` also tries `a.b`). IMPORTS: {root: [its trees]}
    (import_trees). A declaration is unused iff its root is not in this set: per declaring root, never per consuming recipe (the stage of one tool mounts only its own closure)."""
    import ast
    pre = gate_admit.import_roots(list(imports), TREE)

    def home(name):
        return next((r for p, r in pre.items() if name == p or name.startswith(p + ".")), None)
    out = set()
    for f in files:
        if not (f.endswith(".py") and under(f, owner)):
            continue
        try:
            tree = ast.parse(open(os.path.join(repo, f)).read())
        except (OSError, SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else (
                [n.module] + [n.module + "." + a.name for a in n.names] if isinstance(n, ast.ImportFrom) and not n.level and n.module else [])
            out |= {r for r in map(home, names) if r}
    return out


def import_prefixes(roots, owner):
    """{module prefix: root} of the roots OWNER declares (gate_admit.import_roots over the declared roots that are admitted), and OWNER's own root: a module is
    one file under one module path whoever imports it (a root's tool and another root's tool that imports it both spell `contract.x`), so the stage mounts the
    owner's closure at /imp/<its path> as well as at /tool, and a sibling of a root's own file is imported qualified or relative like any declared root's."""
    return gate_admit.import_roots(list(import_trees(roots, owner)) + [owner], TREE)


def import_roots_of(roots, owner):
    """The trees whose modules the stage mounts under /imp: the declared import roots and the owner's own root."""
    return [x for ts in import_trees(roots, owner).values() for x in ts] + [owner]


def import_mount(tool, owner, files, inputs, roots, repo="."):
    """([bad], [files of the declared import roots in the import closure of TOOL]): what the generate stage of TOOL mounts under /imp (repo-relative layout) next to its
    own root's /tool: the .py closure plus the data files those modules read by a path of their own location (decided by pathflow). The key covers exactly these files: an
    edit to any other file of an import root leaves the stage's key unchanged."""
    it = import_trees(roots, owner)
    clo = []
    bad = foreign_imports(tool, owner, files, inputs, repo, clo, import_roots_of(roots, owner), import_prefixes(roots, owner))
    mods = sorted(f for f in set(clo) if any(under(f, t) for t in import_roots_of(roots, owner)) and f != tool)
    data = set()
    for f in mods:   # the data a module reads by a path of its own location, resolved by pathflow inside its root's tree (a directory: every tracked file under it)
        try:
            ins = []
            pathflow.escapes_of(f, open(os.path.join(repo, f)).read(), tree_of(roots, f), ins)
        except (OSError, SyntaxError, ValueError):
            continue
        data |= {g for g in files if any(g == p or under(g, p) for p in ins)}
    return bad, sorted(set(mods) | data)


def foreign_imports(tool, owner, files, inputs, repo=".", closure=None, imports=(), prefixes=None):
    """[module] a generator tool imports that its stage cannot read: the stage mounts the tool's own root and the generator's declared inputs, plus the Python
    standard library and the pinned modules of env.json (envcheck.PY_MODULES). Every import of the tool, found by `ast` (any depth, relative imports included), and,
    transitively, of every module it resolves to, must be one of those; a module of another root that no declared input carries is foreign (the stage would die with
    ModuleNotFoundError at run time, so the recipe is red before any run). INPUTS: the generator's input paths (repo-relative strings). CLOSURE: a list that receives
    the tool's import closure (the repo paths of the files the walk read), for the file-escape rule.
    IMPORTS: the trees (repo paths) of the roots the tool's root declares in admit.json `imports`: a module found there is no foreign import; the stage mounts exactly the
    closure files of those trees (import_mount), never the whole tree. PREFIXES: {module prefix: root} (gate_admit.import_roots, the one rule): a module of a declared root
    is imported only by its qualified name (`render.total.x`); the bare name of such a module is foreign, with the qualified name as the hint. A file that lies in a
    declared root imports its siblings qualified or relative (the mount has /imp on the path, no per-file directory)."""
    import ast
    std, pinned = set(sys.stdlib_module_names) | {"__future__"}, set(envcheck.PY_MODULES.values())
    mine = [f for f in files if under(f, owner) and f.endswith(".py")]
    ins = [i for i in inputs if isinstance(i, str)]
    theirs = [f for f in files if f.endswith(".py") and any(under(f, t) for t in imports if t != owner)]   # the modules of the declared import roots
    prefixes = prefixes or {}
    theirs_set = set(theirs) | {f for f in mine if owner in imports}   # qualified names also reach the owner's own root (import_prefixes)
    ns = gate_admit.import_namespaces(prefixes)
    by_path = {}

    def qualified(name):   # the tracked file of a module spelled by the one rule (prefix + path), inside its declared root
        for pre, rt in prefixes.items():
            if name == pre or name.startswith(pre + "."):
                parts = name.split(".")
                for suf in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
                    c = TREE + "/" + suf
                    if c in theirs_set:
                        return c
        return None

    def pkg_dir(name):   # a directory of a declared root that holds modules, named by the qualified rule: an implicit (PEP 420) namespace package, `from <it> import <module>`
        d = TREE + "/" + name.replace(".", "/") + "/"
        return any(name == pre or name.startswith(pre + ".") for pre in prefixes) and any(c.startswith(d) for c in theirs_set)

    def find(name, rel):    # the tracked file of module NAME (dotted): in the tool's own root, or carried by a declared input
        parts = name.split(".")
        hit = qualified(name)
        if hit:
            return hit
        if name in ns or any(name == pre or name.startswith(pre + ".") for pre in prefixes):
            return None
        if rel in theirs:   # a file of a declared root: its siblings are qualified or relative; the owner's own files are not on the path of the mount
            return None
        for cand in (mine + [f for f in files if f.endswith(".py") and any(f == i or under(f, i) for i in ins)]):
            for suf in ("/".join(parts) + ".py", "/".join(parts) + "/__init__.py"):
                if cand == suf or cand.endswith("/" + suf):
                    return cand
        return None
    bad, seen, todo = [], set(), [tool]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        if closure is not None:
            closure.append(f)
        try:
            tree = ast.parse(open(os.path.join(repo, f)).read())
        except (OSError, SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            names = []
            if isinstance(n, ast.ImportFrom) and n.level:
                # Python's own resolution of `from <.. module> import names`: a relative import names a module beside the importing file, inside its root
                base = posixpath.normpath(posixpath.join(posixpath.dirname(f), *([".."] * (n.level - 1))))
                if n.module:
                    mp = base + "/" + n.module.replace(".", "/")
                    hit = [c for c in (mp + ".py", mp + "/__init__.py") if c in files]
                    if not hit:
                        bad.append("." * n.level + n.module)
                        continue
                    todo.append(hit[0])
                    if hit[0].endswith("/__init__.py"):    # a name is a submodule only where the package holds the file; else it is an attribute of the module
                        todo += [mp + "/" + a.name + ".py" for a in n.names if (mp + "/" + a.name + ".py") in files]
                else:
                    for a in n.names:
                        hit = [c for c in (base + "/" + a.name + ".py", base + "/" + a.name + "/__init__.py") if c in files]
                        if hit:
                            todo.append(hit[0])
                        else:
                            bad.append("." * n.level + a.name)
                continue
            if isinstance(n, ast.Import):
                names = [(a.name, 0) for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                names = [(n.module or "", 0)]
            if isinstance(n, ast.ImportFrom) and n.module:   # `from a.b import c`: c may be a submodule (a qualified module of a declared root)
                names += [(n.module + "." + a.name, 0) for a in n.names if qualified(n.module + "." + a.name)]
            for name, level in names:
                top = name.split(".")[0]
                if top in std or top in pinned or not top or name in ns or pkg_dir(name) or (name in prefixes and not qualified(name)):
                    continue
                got = find(name, f) or find(top, f)
                if got is None:
                    hint = [pre for pre in prefixes for c in theirs if c.endswith("/" + "/".join(name.split(".")) + ".py") and under(c, prefixes[pre])]
                    bad.append(name + (" (qualified: %s)" % ", ".join("%s.%s" % (h, name) for h in sorted(set(hint))) if hint else ""))
                else:
                    todo.append(got)
    return sorted(set(bad))


def ar_bytes(members):
    """The bytes of a GNU `ar` archive of MEMBERS [(name, object bytes)], names at most 15 characters: the symbol index ('/') of the members' global and weak
    definitions (COMMON included), then the members. Deterministic (date, uid, gid 0, mode 644), so an archive is a pure function of its members: the linker reads
    it as it reads any archive (a member is pulled only for a symbol that is still undefined)."""
    mem = [(n + "/", d, sorted(undefset.object_symbols(d)[0])) for n, d in members]
    names = [x for _, _, ds in mem for x in ds]
    symsz = 4 + 4 * len(names) + sum(len(x) + 1 for x in names)
    off = 8 + 60 + symsz + (symsz & 1)
    offs = []
    for n, d, ds in mem:
        offs += [off] * len(ds)
        off += 60 + len(d) + (len(d) & 1)
    sym = struct.pack(">I", len(names)) + b"".join(struct.pack(">I", o) for o in offs) + b"".join(x.encode() + b"\0" for x in names)

    def hdr(name, size):
        return ("%-16s%-12s%-6s%-6s%-8s%-10d`\n" % (name, "0", "0", "0", "644", size)).encode()
    out = [b"!<arch>\n", hdr("/", len(sym)), sym, b"\n" if len(sym) & 1 else b""]
    for n, d, _ in mem:
        out += [hdr(n, len(d)), d, b"\n" if len(d) & 1 else b""]
    return b"".join(out)


def empty_object(cls, machine):
    """An ELF relocatable with no symbols and no code (class CLS 1 or 2, e_machine MACHINE, little-endian): the one input the linker needs to link a script alone."""
    names = b"\0.shstrtab\0"
    eh, sh = (52, 40) if cls == 1 else (64, 64)
    off = eh + len(names)
    off += -off % 8
    if cls == 1:
        head = struct.pack("<16sHHIIIIIHHHHHH", b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8), 1, machine, 1, 0, 0, off, 0, eh, 0, 0, sh, 2, 1)
        sect = struct.pack("<IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0) + struct.pack("<IIIIIIIIII", 1, 3, 0, 0, eh, len(names), 0, 0, 1, 0)
    else:
        head = struct.pack("<16sHHIQQQIHHHHHH", b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8), 1, machine, 1, 0, 0, off, 0, eh, 0, 0, sh, 2, 1)
        sect = bytes(64) + struct.pack("<IIQQQQIIQQ", 1, 3, 0, 0, eh, len(names), 0, 0, 1, 0)
    return head + names + bytes(off - eh - len(names)) + sect


SCRIPT_REF = re.compile(r"undefined symbol `([^']+)' referenced in expression")


def script_refs_loop(run):
    """The symbols a link script references that no input defines: the undefined symbols the pinned ld itself reports for the script linked alone (nothing else
    is input). RUN(defined) -> the stderr lines of that link with every name in DEFINED given a value (`--defsym`), or None when it succeeds. ld stops at the first
    undefined name of an expression, so the link repeats with each reported name defined until a pass reports no new one: every pass adds a name, so it ends. What
    the script's own ASSERTs say about the placeholder values, or any other script error, is the real link's to report. -> sorted names."""
    seen = []
    while True:
        err = run(list(seen))
        new = sorted({m for ln in (err or []) for m in SCRIPT_REF.findall(ln)} - set(seen))
        if not new:
            return sorted(seen)
        seen += new


def variant_names(rec, ent=None):
    """The variants of a recipe REC, and of the admit entry ENT: its per-target recipes."""
    v = rec.get("variants") if isinstance(rec, dict) else None
    return sorted(set(v if isinstance(v, dict) else []) | set(own_recipes(ent or {})))


def pairs_of(rec, root, ent=None):
    """Every (root, variant) pair spec of a recipe: the implicit one and one per declared variant."""
    return [root] + ["%s@%s" % (root, n) for n in variant_names(rec, ent)]


BASE_ONLY = ("generated", "run", "uses", "outputs")   # the stages and their outputs: host work whatever the target, so they live once, in the base recipe
INHERITED = ("units", "include")                      # taken from the base unless the per-target recipe really differs


def per_target_errors(prec, base):
    """The problems of the RAW per-target recipe PREC against the base recipe BASE (RECIPE.md "One recipe per target"): a key of BASE_ONLY is red, a `units`
    or `include` equal to the base's is a redundant copy, an `external` name that the base declares differently is two facts for one name."""
    err = ["%r is a key of the base recipe only (generated and run stages run on the host whatever the target: they live once)" % k for k in BASE_ONLY if k in prec]
    err += ["%r equals the base recipe's: a redundant copy (leave it out, the target inherits it)" % k for k in INHERITED if k in prec and prec[k] == base.get(k)]
    bx = {e.get("name"): e for e in base.get("external", []) if isinstance(e, dict)} if isinstance(base.get("external"), list) else {}
    err += ["external %r differs from the base recipe's entry of that name" % e.get("name") for e in (prec.get("external") if isinstance(prec.get("external"), list) else [])
            if isinstance(e, dict) and e.get("name") in bx and bx[e["name"]] != e]
    return err


def inherit(prec, base):
    """The effective per-target recipe: PREC with the BASE_ONLY keys, an absent `units` or `include`, and the base externals it does not name taken from BASE
    (both expanded, no variants)."""
    out = dict(prec)
    for k in BASE_ONLY + INHERITED:
        if k in base and k not in out:
            out[k] = base[k]
    if isinstance(base.get("external"), list):
        have = {e.get("name") for e in out.get("external", []) if isinstance(e, dict)}
        out["external"] = list(out.get("external") or []) + [e for e in base["external"] if not (isinstance(e, dict) and e.get("name") in have)]
    return out


def load_recipe(repo, roots, spec):
    """The effective recipe of a root or of a 'root@NAME' pair."""
    root, name = split_spec(spec)
    rec = read_recipe(repo, entry_of(roots, spec)["recipe"])
    if name in own_recipes(roots[root]):   # a per-target recipe has no variants of its own; the stages and the shared fields come from the base
        return inherit(effective(rec, ""), effective(read_recipe(repo, roots[root]["recipe"]), ""))
    return effective(rec, name) if not name or name in variant_names(rec) else rec


def step_outputs(x):
    """The declared outputs of one generate or run step X: its files (`outputs`) and its directories (`dirs`, generate steps; RECIPE.md "Directory
    outputs"), as the root-relative names a consumer reads them by."""
    return [o for k in ("outputs", "dirs") for o in (x.get(k) if isinstance(x.get(k), list) else []) if isinstance(o, str)]


def generated_outputs(rec):
    """The declared outputs (files and directories) of the generate and run stages of a recipe."""
    return [o for k in ("generated", "run") for x in (rec.get(k) if isinstance(rec.get(k), list) else []) if isinstance(x, dict) for o in step_outputs(x)]


def run_input_sources(rec, root, files):
    """Root-relative C/C++/asm source files of ROOT that are declared inputs of one of its `run` steps (a path input that is the file, or a directory holding it):
    the step's input, mounted at /in/N with its content in the step's key, not a recipe unit (RECIPE.md "Run stages")."""
    ins = [p for g in (rec.get("run") if isinstance(rec.get("run"), list) else []) if isinstance(g, dict) and isinstance(g.get("inputs"), list)
           for p in g["inputs"] if isinstance(p, str)]
    return {f[len(root) + 1:] for f in files if under(f, root) and is_unit(f) and any(f == p or under(f, p) for p in ins)}


def own_sources(root, files, roots, repo="."):
    """{root-relative path}: the tracked files that are sources of ROOT and of no other stage. A file under a root admitted inside ROOT (nested admitted roots, e.g. verify/probes
    in verify) belongs to the nearest admitted root, as tree_of resolves it; a plugin source and the files it includes (plugin_files) are inputs of the plugin stage of the
    compiler build, never translation units of the root that holds them. One definition: the unit list, check() and the build read it."""
    nested = [r for r in roots if r != root and under(r, root)]
    plug = set()
    for q in PLUGIN_SOURCES.values():
        if q in files and os.path.isfile(os.path.join(repo, q)):
            try:
                plug |= {q} | set(plugin_inputs(q, files, roots, repo))
            except ValueError:
                pass   # reported by plugin_errors
    return {f[len(root) + 1:] for f in files if under(f, root) and not any(under(f, r) for r in nested) and f not in plug}


def all_units_of(rec, root, files, roots, repo="."):
    """Sorted root-relative paths of every source of the root (own_sources): tracked source files + generated outputs with a source name, minus the sources that are declared
    inputs of a `run` step of the root (those are the step's inputs, never units)."""
    mine = own_sources(root, files, roots, repo) - run_input_sources(rec, root, files)
    return sorted(u for u in mine | set(generated_outputs(rec)) if is_unit(u))


def tests_of(rec):
    """The declared tests of REC: [(name, [unit, ..])] (entries that are not well formed are left to check())."""
    t = rec.get("tests") if isinstance(rec, dict) else None
    return [(x["name"], list(x["units"])) for x in (t if isinstance(t, list) else []) if isinstance(x, dict) and isinstance(x.get("name"), str)
            and isinstance(x.get("units"), list) and all(isinstance(u, str) for u in x["units"])]


def test_units_of(rec):
    return {u for _, us in tests_of(rec) for u in us}


def units_of(rec, root, files, roots, repo="."):
    """Sorted root-relative PRODUCT unit paths: every source of the root that is not a unit of a declared test (RECIPE.md, tests)."""
    tu = test_units_of(rec)
    return [u for u in all_units_of(rec, root, files, roots, repo) if u not in tu]


def pin_drivers():
    """(cc, cxx) driver paths of the guest compiler tree: derived from the one triplet of targets.json (targets.drivers), never written in the pin."""
    return _targets.drivers(_targets.load())


def load_pin(repo="."):
    """verify/guest_compiler.json of the checkout, or None when the tree has none."""
    p = os.path.join(repo, GUEST_PIN)
    return json.load(open(p)) if os.path.exists(p) else None


def guest_link_part(rec, root, pin, prof, files, roots):
    """The link of a guest root (part of guest_errors): script and libs are the profile's (link/guest.json), one fact for every guest link."""
    lk = rec.get("link")
    if not isinstance(lk, dict):
        return []
    bad = ["link.%s is declared: script and libs of a guest link come from %s" % (k, GUEST_LINK) for k in ("script", "libs") if k in lk]
    bad += guest_link_errors(prof, pin, files, roots) if prof is not None else ["%s is missing" % GUEST_LINK]
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
    bad += ["compiler.%s is %r, derived from the triplet %r" % (k, c[k], d) for k, d in zip(("cc", "cxx"), pin_drivers()) if c[k] != d]
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
    """[(ext name, path)] of a `sources` file: one `<ext>:<path>` line per unit, '.' naming the root itself, an admitted ROOT path naming a tracked file of that
    root (root_source); blank lines skipped. ValueError for a bad line."""
    out = []
    for n, ln in enumerate(text.splitlines(), 1):
        if not ln.strip():
            continue
        m = re.fullmatch(r"([A-Za-z0-9._/-]+):(.+)", ln.strip())
        if not m or not rel_ok(m.group(2)):
            raise ValueError("line %d is not <external or root>:<path inside it>: %r" % (n, ln[:80]))
        out.append((m.group(1), m.group(2)))
    return out


def root_source(roots, files, uses, root, other, p):
    """The `sources` line `OTHER:P` where OTHER is an admitted root: -> (repo path, unit name). The file is a git-tracked file under the root OTHER (never a directory
    or a glob; P is a normalized relative path, so it cannot leave OTHER), and OTHER is a root of ROOT's `uses` (the stage graph shows the edge and the
    uses cycle check applies). ValueError with the reason otherwise. The unit is `<OTHER with / as __>/<P>`: the file is copied into the unit view as an
    external's file is, so its content is in the unit's keys; it stays owned by OTHER (onedef and objstub judge each product separately)."""
    if other not in roots:
        raise ValueError("%s is not an admitted root" % other)
    if other == root:
        raise ValueError("%s is the root itself (use `.`)" % other)
    if not rel_ok(p):
        raise ValueError("%s is not a relative path inside %s" % (p, other))
    full = other + "/" + p
    if full not in files:
        raise ValueError("%s is not a git-tracked file of %s" % (p, other))
    if other not in {u.get("root") for u in uses if isinstance(u, dict)}:
        raise ValueError("%s is not in `uses` of %s (list it as {root, output})" % (other, root))
    return full, other.replace("/", "__") + "/" + p


def dir_output(p, gen):
    """P is a directory output: a path with generated outputs below it (the output is the whole directory: its files are exactly those outputs)."""
    return any(o != p and under(o, p) for o in gen)


def foreign_search_dirs(dirs, ext_hosts):
    """The directories DIRS of a compiler's own search list (its answers, unitstage.gcc_set) that lie outside every external tree EXT_HOSTS (host paths), by their
    real path too: the headers a guest compile reads are the pinned runtime's own, never a distribution directory of the machine."""
    under_ext = lambda p: any(under(p, e) for e in ext_hosts)  # noqa: E731
    return sorted({t for t in dirs if not under_ext(t) or not under_ext(os.path.realpath(t))})


HEADER_OUTPUT = "INCLUDE"   # the one implicit output of a header root: its own directory (a srcdir, keyed by its tree hash)


def root_outputs(roots, r, repo):
    """-> ({NAME: root-relative path}, generated outputs) that admitted root R declares for `uses`: the `outputs` of its recipe, or for a header root (class header,
    no recipe, no units) the one implicit output INCLUDE = its own directory (".": nothing generated, every tracked file below the root). The ONE accessor of
    what a `uses` entry names: the check, the include homes, the include_uses rule and the stage input all read it, so a header root is reached one way only.
    KeyError for a root that is not admitted, OSError/ValueError for an unreadable recipe."""
    ent = roots[r]
    if ent.get("class") == "header":
        return {HEADER_OUTPUT: "."}, set()
    prod = read_recipe(repo, ent["recipe"])
    return prod.get("outputs") or {}, generated_outputs(prod)


def include_home(d, rec, roots, repo, files):
    """D (a repo directory) lies in a directory output of tracked files (a `srcdir`) of a root that REC `uses` (a header root's INCLUDE is its own directory)."""
    for u in rec.get("uses") or []:
        if not (isinstance(u, dict) and isinstance(roots.get(u.get("root")), dict)):
            continue
        try:
            outs, gen = root_outputs(roots, u["root"], repo)
            p = outs.get(u["output"])
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            continue
        if isinstance(p, str) and output_kind(p, u["root"], gen, files) == "srcdir" and under(d, u["root"] if p == "." else u["root"] + "/" + p):
            return True
    return False


def output_kind(p, root, gen, files):
    """What the declared output path P (root-relative) of ROOT is: "file" (a generated output), "gendir" (a directory whose files are generated outputs, none
    tracked), "srcdir" (a directory of tracked files of the root, nothing generated below it: its content is its tree hash), "mixed" (generated and tracked
    files below one directory: red), or None."""
    if p in gen:
        return "file"
    full = root if p == "." else root + "/" + p
    gdir = dir_output(p, gen)
    tdir = any(under(f, full) for f in files)
    if gdir and tdir:
        return "mixed"
    return "gendir" if gdir else "srcdir" if tdir else None


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
            outs, gen = root_outputs(roots, u["root"], repo)
            p = outs.get(u["output"])
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            continue    # uses_errors reports an unreadable or undeclared producer
        if not (isinstance(p, str) and output_kind(p, u["root"], gen, tracked(repo)) in ("gendir", "srcdir")):
            err.append("include_uses: output %r of %s is not a directory output (outputs: {NAME: a directory of generated files, or of tracked files})" % (u["output"], u["root"]))
    return err


def uses_errors(rec, root, roots, repo, files=None):
    """Problems of `outputs` ({name: a generated output of this root, a directory of generated files, or a directory of tracked files}) and `uses`
    ([{root, output}]: an output another admitted root declares)."""
    err, gen = [], generated_outputs(rec)
    outs = rec.get("outputs")
    if outs is not None:
        held = tracked(repo) if files is None else files
        if not (isinstance(outs, dict) and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", n) and isinstance(p, str) and rel_ok(p)
                                               and output_kind(p, root, gen, held) in ("file", "gendir", "srcdir") for n, p in outs.items())):
            err.append("outputs must be {NAME: a path in generated[].outputs of this root, a directory every file of which is one of them, or a directory of tracked files of this root "
                       "with no generated output below it (never both in one directory)}")
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
        elif not ent or not (ent.get("recipe") or ent.get("class") == "header"):
            err.append("uses: %s is not an admitted root with a recipe or a header root" % r)
        else:
            try:
                decl = root_outputs(roots, r, repo)[0]
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
        if r.get("class") == "header" and any(isinstance(u, dict) and u.get("root") == p and u.get("output") == HEADER_OUTPUT for u in rec.get("uses") or []):
            dirs.add(p)   # a header root is a home only for a recipe that `uses` its INCLUDE
        if r.get("class") == "product" and tg in targets_of(r):
            dirs.add(include_dir(roots, p))
            for path in [r.get("recipe") if r.get("target") == tg else None] + [q for t, q in own_recipes(r).values() if t == tg]:
                try:
                    with open(os.path.join(repo, path)) as f:
                        add(json.load(f).get("include") or [])
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    pass
    return sorted(dirs)


def deb_mirror():
    """The deb mirror pin {commit, ...} of verify/env.json (debs.mirror: the private fnv-nx-build branch tools/verdict-debs); {} when unreadable."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")) as f:
            return json.load(f)["debs"]["mirror"]
    except (OSError, KeyError, ValueError):
        return {}


def deb_pins():
    """The declared debs {name: {version, sha256, ...}} of verify/env.json (the one record of the declared system's packages); {} when unreadable."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")) as f:
            return json.load(f)["debs"]["packages"]
    except (OSError, KeyError, ValueError):
        return {}


def deb_profiles():
    """The named top lists of verify/env.json `debs.profiles` ({name: [top package names]}): one fact in one file; {} when unreadable."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "env.json")) as f:
            return json.load(f)["debs"].get("profiles", {})
    except (OSError, KeyError, ValueError):
        return {}


def deb_closure(tops):
    """Sorted package names of TOPS and everything their Pre-Depends/Depends need on the declared machine (envcheck.dep_closure, the rule that defines the
    declared package set: what `dpkg -i` of the pins needs to be complete). A top that is not installed there is not in the result; the build refuses it."""
    import envcheck
    have = envcheck.dep_closure(list(tops))
    miss = sorted(set(tops) - set(have))
    if miss:
        raise ValueError("debs.closure: %s is not installed on the declared machine" % ", ".join(miss))
    return sorted(have)


DEBS_CLOSURE_ERR = 'debs is a list of env.json package names, {"closure": [top package names]} or {"profile": NAME} (NAME in env.json debs.profiles: the tops of a named closure; the dependency closure of the programs the step runs, resolved by the recipe)'


def step_debs(g, closure=True):
    """The debs of run step G as a sorted list of names: the written list, or for {"closure": [TOPS]} the Depends closure of TOPS (deb_closure), so a package that a
    dependency adds is written in no build.json; plus the machine's root layout package (envcheck.RUN_LAYOUT_DEB, base-files: the usr-merge links /lib /lib64 /bin /sbin
    that every PT_INTERP and every program path resolves through), which is a fact of the declared machine and in every run step: a recipe that lists it is red (as a
    duplicated declaration).
    ValueError on a form that is neither; CLOSURE False takes the tops themselves (the check does not read the machine)."""
    import envcheck
    d = g.get("debs")
    if isinstance(d, list) and all(isinstance(x, str) for x in d):
        got = list(d)
        if envcheck.RUN_LAYOUT_DEB in got:
            raise ValueError("debs: %s is the machine's root layout and in every run step: do not list it" % envcheck.RUN_LAYOUT_DEB)
    elif isinstance(d, dict) and set(d) == {"closure"} and isinstance(d["closure"], list) and d["closure"] and all(isinstance(x, str) for x in d["closure"]):
        got = deb_closure(d["closure"]) if closure else list(d["closure"])
    elif isinstance(d, dict) and set(d) == {"profile"} and isinstance(d["profile"], str):
        tops = deb_profiles().get(d["profile"])
        if not (isinstance(tops, list) and tops and all(isinstance(x, str) for x in tops)):
            raise ValueError("debs.profile: %r is not a profile of env.json debs.profiles (%s)" % (d["profile"], ", ".join(sorted(deb_profiles())) or "none"))
        got = deb_closure(tops) if closure else list(tops)
    else:
        raise ValueError(DEBS_CLOSURE_ERR)
    return sorted(set(got) | {envcheck.RUN_LAYOUT_DEB})


def step_inputs(rec, g):
    """The inputs of run step G with {"plugins": "root"} expanded to one {"plugin": NAME} per plugin the root declares, in declaration order (the order is written once, in `plugins`)."""
    out = []
    for p in g.get("inputs", []):
        if isinstance(p, dict) and p == {"plugins": "root"}:
            out += [{"plugin": x["name"]} for x in (rec.get("plugins") if isinstance(rec.get("plugins"), list) else []) if isinstance(x, dict) and isinstance(x.get("name"), str)]
        else:
            out.append(p)
    return out


def expand_run(rec, closure=True):
    """REC with the `run` steps' forms expanded (step_inputs, step_debs): the one reader every consumer of the steps uses after the form check."""
    rs = rec.get("run")
    if not isinstance(rs, list):
        return rec
    out = []
    for g in rs:
        if isinstance(g, dict) and isinstance(g.get("inputs"), list):
            g = dict(g, inputs=step_inputs(rec, g))
        if isinstance(g, dict):
            try:
                g = dict(g, debs=step_debs(g, closure))
            except ValueError:
                if closure:
                    raise
        out.append(g)   # (a malformed form is run_errors' report; the build refuses a closure that cannot be resolved)
    return dict(rec, run=out)


def run_errors(rec, root, files, roots, mine, seen, repo):
    """The `run` stages (RECIPE.md "Run stages"): {name, artifact "ROOT:OUTPUT", args, inputs [ref], debs [env.json package], outputs [path]}."""
    rs, err = rec.get("run", []), []
    if not isinstance(rs, list):
        return ["run is not a list"]
    for i, g in enumerate(rs):
        if isinstance(g, dict) and isinstance(g.get("inputs"), list) and {"plugins": "root"} in g["inputs"]:
            if g["inputs"].count({"plugins": "root"}) > 1:
                err.append('run[%d].inputs: {"plugins": "root"} is written once' % i)
            if not any(isinstance(x, dict) for x in (rec.get("plugins") if isinstance(rec.get("plugins"), list) else [])):
                err.append('run[%d].inputs: {"plugins": "root"} needs the root to declare `plugins`' % i)
    raw = rs
    rs = expand_run(rec, closure=False).get("run", [])
    pk = deb_pins()
    names = set()
    for i, g in enumerate(rs):
        tool = isinstance(g, dict) and "exec" in g   # a tool step: runs a program of the declared debs in their root layout (RECIPE.md "Tool steps")
        base = {"name", "args", "inputs", "debs", "outputs"} | ({"exec"} if tool else {"artifact"})
        if not (isinstance(g, dict) and base <= set(g) <= base | {"host_cpu"} | ({"dirs", "scratch", "timeout_s", "env", "foreach"} if tool else set())):
            err.append("run[%d] needs name, args, inputs, debs, outputs and exactly one of artifact | exec (and optionally host_cpu; a tool step also dirs, scratch, env)" % i)
            continue
        if "host_cpu" in g and not (g["host_cpu"] == "cpuref" and TREE + "/verify/cpuref.json" in files):
            err.append('run[%d].host_cpu must be "cpuref", with the tracked pin file %s/verify/cpuref.json' % (i, TREE))
        if not (isinstance(g["name"], str) and re.fullmatch(r"[A-Za-z0-9_.-]+", g["name"])) or g["name"] in names:
            err.append("run[%d].name must be a unique plain name" % i)
        names.add(g["name"])
        a = g["exec"] if tool else g["artifact"]
        ar, _, ao = a.partition(":") if isinstance(a, str) and not tool else ("", "", "")
        if tool:
            sc = g.get("scratch", [])
            xl = a.get("libs", []) if isinstance(a, dict) else []   # the declared lib dirs of a file form: [{external, path} | {root, output}], mounted at /exec/lib/<i>
            a = {k: v for k, v in a.items() if k != "libs"} if isinstance(a, dict) else a
            ex_ext = isinstance(a, dict) and set(a) == {"external", "path"} and all(isinstance(v, str) for v in a.values())
            ex_root = isinstance(a, dict) and set(a) == {"root", "output"} and all(isinstance(v, str) for v in a.values())
            ex_of = is_external_of(a)
            ex_py = a == {"python": True}   # the pinned python runtime of the generated stages (pyrt): the interpreter and env.json python.modules
            if ex_py and raw[i].get("debs"):   # as written: the machine's layout deb is added to every step
                err.append("run[%d].exec python: the step has no debs (the interpreter's runtime is its root)" % i)
            if ex_of:
                err += external_of_errors("run[%d].exec" % i, a, root, rec, roots, repo)
            if xl and not (ex_ext or ex_root or ex_of):
                err.append("run[%d].exec: `libs` belongs to an {external, path} or {root, output} exec" % i)
            for lb in xl if isinstance(xl, list) else [xl]:
                okx = isinstance(lb, dict) and set(lb) == {"external", "path"} and lb["external"] in {x.get("name") for x in rec.get("external", []) if isinstance(x, dict)} and isinstance(lb["path"], str) and rel_ok(lb["path"])
                oku = isinstance(lb, dict) and set(lb) == {"root", "output"} and any(isinstance(u, dict) and u.get("root") == lb["root"] and u.get("output") == lb["output"] for u in rec.get("uses", []))
                if not (okx or oku):
                    err.append("run[%d].exec.libs: %r must be an {external, path} of this recipe or a {root, output} it `uses` (a directory)" % (i, lb))
            if ex_ext:   # a file of a pinned external, mounted at /exec/<name> (RECIPE.md "A tool that is not a deb binary")
                if a["external"] not in {x.get("name") for x in rec.get("external", []) if isinstance(x, dict)} or not rel_ok(a["path"]):
                    err.append("run[%d].exec %r must name an external of this recipe and a path inside its tree" % (i, a))
            elif ex_of or ex_py:
                pass   # checked above (the one external_of rule; the python runtime)
            elif ex_root:   # the output of a built root this root `uses`: the same resolution as the uses mount
                if not any(isinstance(u, dict) and u.get("root") == a["root"] and u.get("output") == a["output"] for u in rec.get("uses", [])):
                    err.append("run[%d].exec %r must be a root and output this recipe `uses`" % (i, a))
            elif not (isinstance(a, str) and a.startswith("/") and posixpath.normpath(a) == a):
                err.append("run[%d].exec %r must be an absolute path of a program in the declared debs, {external, path} or {root, output}" % (i, a))
            if "timeout_s" in g:
                err.append("run[%d].timeout_s is nondeterministic (a time limit decides red or green): bound the work with args" % i)
            if "env" in g and not (isinstance(g["env"], dict) and all(isinstance(k, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", k) and isinstance(v, str) and k not in ("PATH", "TZ", "LC_ALL", "SOURCE_DATE_EPOCH", "FNVNX_DEBS", "JOBS") for k, v in g["env"].items())):
                err.append("run[%d].env must map plain names to constant strings (not PATH, TZ, LC_ALL, SOURCE_DATE_EPOCH, FNVNX_DEBS, JOBS)" % i)
            elif isinstance(g.get("env"), dict) and not all(isinstance(v, str) and (not v.startswith("/") or any(under(v, p) for p in (sc if isinstance(sc, list) else []))) for v in g["env"].values()):
                err.append("run[%d].env: a path value must lie under a scratch directory of the step" % i)
            if not (isinstance(sc, list) and all(isinstance(p, str) and p.startswith("/") and posixpath.normpath(p) == p and p != "/"
                                                 and not any(under(p, r) or under(r, p) for r in ("/in", "/out", "/proc", "/dev", "/tmp", "/usr", "/etc", "/var", "/bin", "/lib", "/sbin"))
                                                 for p in sc)):
                err.append("run[%d].scratch must be absolute paths of fresh directories (not /in, /out, /proc, /dev, /tmp or a system directory)" % i)
            dd = g.get("dirs", [])
            if not (isinstance(dd, list) and all(rel_ok(d) for d in dd)):
                err.append("run[%d].dirs must be root-relative directory paths" % i)
        elif ar not in roots or ar == root or not ao:
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
        try:
            err += ["run[%d].debs: %s is not declared in env.json debs.packages" % (i, d) for d in step_debs(raw[i], closure=False) if d not in pk]
        except ValueError as e:
            err.append("run[%d].%s" % (i, e))
        ins = g["inputs"]
        if not isinstance(ins, list):
            err.append("run[%d].inputs must be a list of refs" % i)
        else:
            for p in ins:
                if isinstance(p, str):
                    if not (rel_ok(p) and any(f == p or under(f, p) for f in files) and tree_of(roots, p)):
                        err.append("run[%d].inputs: %s is not a tracked file or directory of an admitted root" % (i, p))
                elif isinstance(p, dict) and set(p) == {"external", "path"}:
                    if p["external"] not in [e.get("name") for e in rec.get("external", []) if isinstance(e, dict)]:
                        err.append("run[%d].inputs: external %r is not declared" % (i, p["external"]))
                elif is_external_of(p):
                    err += external_of_errors("run[%d].inputs" % i, p, root, rec, roots, repo)
                elif is_include_of(p):
                    err += include_of_errors("run[%d].inputs" % i, p, root, rec, roots, repo)
                elif isinstance(p, dict) and set(p) == {"plugin"}:   # the built plugin of this root (its .so), mounted like a stage input
                    if not (tool and p["plugin"] in [x.get("name") for x in rec.get("plugins", []) if isinstance(x, dict)]):
                        err.append("run[%d].inputs: plugin %r is not declared in this root's `plugins` (or the step is no tool step)" % (i, p["plugin"]))
                elif not (isinstance(p, dict) and set(p) == {"stage", "path"} and all(isinstance(v, str) for v in p.values())):
                    err.append("run[%d].inputs: %r is neither a path, {external, path} nor {stage, path}" % (i, p))
        outs = g["outputs"]
        if not (isinstance(outs, list) and (outs or g.get("dirs")) and all(isinstance(o, str) and rel_ok(o) for o in outs)):
            err.append("run[%d].outputs must be a non-empty list of root-relative paths (a tool step may have only dirs)" % i)
            continue
        for o in outs + (g.get("dirs", []) if isinstance(g.get("dirs"), list) else []):
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
    written_compiler = rec.get("compiler")   # before expand_profile fills one in
    if "profile" in rec:
        ent0 = roots.get(root, {})
        want = (entry_of(roots, root + "@" + variant) if variant else ent0).get("target")
        if rec["profile"] != want:
            err.append("profile %r: the admit.json target of %s is %r (a root names the profile of its own target)" % (rec["profile"], root, want))
    rec, perr, ferr = expand_recipe(rec, pin, repo, files)   # a foreach step becomes one static step per row of its tracked table: every rule below judges the expanded steps
    err += perr + ferr
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
            base = effective(rec, "") if isinstance(rec, dict) else {}
            err += ["variant %s: %s" % (n, e) for e in (per_target_errors(prec, base) if isinstance(prec, dict) else [])]
            err += ["variant %s: %s" % (n, e) for e in check(inherit(prec, base) if isinstance(prec, dict) else prec, root, files, roots, pin, repo, n)]
    err += ["missing key %r" % k for k in ("format", "compiler", "flags", "include", "units") if k not in rec]
    if rec.get("format") != FORMAT:
        err.append("format is not " + FORMAT)
    if rec.get("units") != "all":
        err.append('units must be "all" (no tracked source file can be left out)')
    err += missing_recipes(roots, files)
    mine = own_sources(root, files, roots, repo)
    gen = generated_outputs(rec)
    if gonly:   # `compiler` and `plugins` of a root with no unit are used only by its run steps: a plugin is a step's {"plugin": NAME} input, the compiler builds the plugins
        # (or is a step's {"external": NAME} input); an unused one is red, as gen:unused-import is
        rins = [p for g in (expand_run(rec, closure=False).get("run") if isinstance(rec.get("run"), list) else []) if isinstance(g, dict) and isinstance(g.get("inputs"), list) for p in g["inputs"] if isinstance(p, dict)]
        pl = [x.get("name") for x in (rec.get("plugins") if isinstance(rec.get("plugins"), list) else []) if isinstance(x, dict)]
        err += ["gonly:unused-plugin:%s (a root with no unit declares a plugin only when a run step takes it as its {\"plugin\": NAME} input)" % n for n in pl if {"plugin": n} not in rins]
        cp = written_compiler
        if cp is not None and not (pl or any(set(p) == {"external"} and isinstance(cp, dict) and p["external"] in (cp.get("external"), cp.get("name")) for p in rins)):
            err.append("gonly:unused-compiler (a root with no unit names a compiler only to build its plugins or as a run step's {\"external\": NAME} input)")
    if gonly:   # no compiler and no flags are written: the root compiles nothing; a source that is no run step's declared input is neither a unit nor an input: red
        err += ["generated-only root: %s is a translation unit (a root with units writes compiler, flags, include and units); a source file is a declared input of a `run` step or a unit" % u
                for u in sorted((mine - run_input_sources(rec, root, files)) | set(gen)) if u.endswith(CC_EXT + CXX_EXT + ASM_EXT)]
    comp, exts, ats = rec.get("compiler"), rec.get("external", []), []
    if not isinstance(exts, list):
        err.append("external must be a list")
        exts = []
    for i, e in enumerate(exts):
        if not (isinstance(e, dict) and set(e) in (EXT_KEYS, STAGE_EXT_KEYS) and all(isinstance(v, str) for v in e.values())):
            err.append("external[%d] needs exactly %s (a fetched commit) or %s (a root's output); strings" % (i, ", ".join(sorted(EXT_KEYS)), ", ".join(sorted(STAGE_EXT_KEYS))))
            continue
        at = e["at"]
        if is_stage_ext(e):
            fetched = ((e["root"] in roots and e["root"] != root, "root must be another admitted product root"),
                       (re.fullmatch("[A-Za-z0-9_]+", e["output"]), "bad output name"))
        else:
            fetched = ((e["repo"] == "fnv-nx-build", "repo must be fnv-nx-build"), (re.fullmatch("[0-9a-f]{40}", e["commit"]), "commit is not 40 hex"),
                       (re.fullmatch("[0-9a-f]{64}", e["tree_sha256"]), "tree_sha256 is not 64 hex"))
        for ok, why in fetched + ((re.fullmatch("[A-Za-z0-9._-]+", e["name"]), "bad name"),
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
        elif not under(d, root) and not include_home(d, rec, roots, repo, files):
            # one home per header, and the way to it is declared: an include outside the unit's own root lies in a directory output
            # (`outputs: {INCLUDE: dir}`) of another admitted root that this root `uses`, never merely in some admitted root
            err.append("include %r lies outside root %s and in no directory output of a root that this root `uses` (the owner declares `outputs: {INCLUDE: dir}`, this root `uses: [{root, output}]`)"
                       % (d, root))
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
    seen, used = set(), {}   # USED: owner root -> declared import roots the import closures of this recipe's generators reach
    for i, g in enumerate(rec.get("generated", []) if isinstance(rec.get("generated", []), list) else err.append("generated is not a list") or []):
        if not (isinstance(g, dict) and {"tool", "args", "inputs", "outputs"} <= set(g) <= {"tool", "args", "inputs", "outputs", "objects_of", "dirs"}):
            err.append("generated[%d] needs exactly tool, args, inputs, outputs (and optionally objects_of, dirs)" % i)
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
        if isinstance(t, str) and t in files and t.endswith(".py") and owner:
            clo = []
            used.setdefault(owner, set())
            err += ["gen:foreign-import:%s:%s (generated[%d]: the stage mounts the tool's own root and the declared inputs; the module lies in another root and no input carries it)" % (t, m, i)
                    for m in foreign_imports(t, owner, files, g["inputs"] if isinstance(g["inputs"], list) else [], repo, clo,
                                             import_roots_of(roots, owner), import_prefixes(roots, owner))]
            # the file-escape rule (pathflow.escapes, the one implementation the abspath gate also runs): a read built from __file__, __path__ or sys.argv[0] that leaves the
            # tree the stage mounts the file from, or that cannot be decided, is red; a resolved file inside the mounted tree and a read through require_mounted are green
            for cf in sorted(set(clo)):
                ot = tree_of(roots, cf)
                if ot is None:
                    continue

                try:
                    src = open(os.path.join(repo, cf)).read()
                    esc = pathflow.escapes_of(cf, src, ot)
                except (OSError, SyntaxError, ValueError):
                    continue
                err += ["gen:file-escape:%s:gen:%d:%s:%d:%s (the stage mounts only the tool's tree and the declared inputs: take data of another root through a declared `uses` input, passed as an argument or read through require_mounted)" % (root, i, cf, ln, d)
                        for ln, d in esc]
        stg = [p for p in ins if isinstance(p, dict)] if isinstance(ins, list) else []
        ins = [p for p in ins if not isinstance(p, dict)] if isinstance(ins, list) else ins
        for p in [x for x in stg if is_external_of(x)]:
            err += external_of_errors("generated[%d].inputs" % i, p, root, rec, roots, repo)
        stg = [x for x in stg if not is_external_of(x)]
        for p in [x for x in stg if is_include_of(x)]:
            err += include_of_errors("generated[%d].inputs" % i, p, root, rec, roots, repo)
        stg = [x for x in stg if not is_include_of(x)]
        for p in stg:   # the declared output of an EARLIER generator of this recipe: {"stage": "<this root>:gen:I", "path": OUTPUT}, mounted at /stage/<OUTPUT>
            m = re.fullmatch(re.escape(root) + r":gen:(\d+)", p.get("stage", "")) if set(p) == {"stage", "path"} and isinstance(p.get("stage"), str) else None
            prev = rec["generated"][int(m.group(1))] if m and int(m.group(1)) < i else None
            if not (prev and isinstance(prev, dict) and p["path"] in step_outputs(prev)):
                err.append("generated[%d].inputs: %r is not {stage: %s:gen:I, path: an output of generator I} with I before this one" % (i, p, root))
        if not (isinstance(ins, list) and all(rel_ok(p) and any(f == p or under(f, p) for f in files) for p in ins)):
            err.append("generated[%d].inputs must be tracked files or directories (repo-relative), or stage refs of earlier generators" % i)
        elif any(a != b and under(a, b) for a in ins for b in ins):
            err.append("generated[%d].inputs nest" % i)
        for p in ins if isinstance(ins, list) else []:
            if isinstance(p, str) and not any(under(p, r) for r in roots) and not tree_of(roots, p):
                err.append("generated[%d].inputs: %s lies in no admitted root or tree" % (i, p))
        dirs = g.get("dirs", [])
        if not (isinstance(dirs, list) and all(rel_ok(d) for d in dirs)):
            err.append("generated[%d].dirs must be a list of root-relative directory paths" % i)
            continue
        if not (isinstance(outs, list) and (outs or dirs) and all(rel_ok(o) for o in outs)):
            err.append("generated[%d].outputs must be a non-empty list of root-relative paths (or empty with dirs)" % i)
            continue
        for d in dirs:   # a directory output is its own subtree: no file output and no other directory output lies in it or holds it, and no tracked file is in it
            if any(under(o, d) or under(d, o) for o in outs) or any(x != d and (under(x, d) or under(d, x)) for x in dirs) or any(under(f[len(root) + 1:], d) for f in files if under(f, root)):
                err.append("generated[%d].dirs: %s overlaps another output or a tracked file" % (i, d))
        for o in outs + dirs:
            if o in mine:
                err.append("generated output %s is also a tracked file" % o)
            if o in seen:
                err.append("generated output %s is produced twice" % o)
            seen.add(o)
    err += ["gen:unused-import:%s:%s (admit.json `imports` of %s declares a root that no module of %s imports from: remove the declaration)" % (o, ir, o, o)
            for o in sorted(used) for ir in sorted(set(import_trees(roots, o)) - declared_import_uses(o, import_trees(roots, o), files, repo))]
    err += run_errors(rec, root, files, roots, mine, seen, repo)
    err += uses_errors(rec, root, roots, repo, files) + include_uses_errors(rec, roots, repo)
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
    allu = set(all_units_of(rec, root, files, roots, repo))
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
    units = set(units_of(rec, root, files, roots, repo))
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


def plugin_errors(rec, root, files, roots, units, ext_names, variant="", repo="."):
    """Problems of the plugin declaration of recipe REC of ROOT, and the gate rule: a product root with native or decomp C++ loads both
    plugins of PLUGIN_SOURCES with their fixed arguments; a root whose C++ is all lifted needs none. What is lifted is declared in the
    admit.json entry of the root, "cxx_origin": "native" | {"lifted_by": TOOL.py, "pack": EXTERNAL} | {"lifted_by": TOOL.py, "stage": "gen:ROOT:I"} (exactly
    one of pack and stage; see lifted_origin), required when the root has a C++ unit.
    Consistency of a lifted declaration: no tracked C++ unit in the root, every C++ unit is an output of a `generated` step whose tool is
    `lifted_by` (a tracked .py of an admitted tool root), `pack` names an external of the recipe or an admitted root in its `uses` (the provenance is the output of a
    generated step of `lifted_by`, never a name: the units are checked against every such step of this recipe, whichever kind `pack` is); with `stage` the
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
    # RULE (L1, 2026-11-16): a GCC plugin is bound to the exact compiler that loads it, and every plugin is built against the guest compiler's plugin headers.
    # So only a root whose expanded compiler is the guest pin's loads plugins; any other compiler (the host profile, or one named by hand) declaring `plugins` is red.
    pin = load_pin(repo)
    gc = (pin or {}).get("external", {}).get("name") if isinstance(pin, dict) else None
    cc_ = rec.get("compiler") if isinstance(rec.get("compiler"), dict) else {}
    guest_cc = cc_.get("external") == gc
    if pl and not guest_cc:
        return err + ["plugins are bound to the guest compiler; root %s's compiler is %s" % (root, cc_.get("external") or "/".join(str(cc_.get(k)) for k in ("cc", "cxx")))]
    # RULE (RECIPE.md "Plugins"): the plugin set and the SDD flags are a function of the target profile, whatever the language or the origin of the code:
    # guest = the full set of PLUGIN_SOURCES with its fixed arguments in the fixed order, host = the empty set. A guest root that misses one is red, and so is a host root that loads one.
    guest = ent.get("target") == "guest"
    if guest and any(u.endswith(CC_EXT + CXX_EXT) for u in units):
        for n in sorted(PLUGIN_SOURCES):
            if n not in loaded:
                err.append("guest root %s does not load plugin %s (every guest C and C++ unit is compiled with the full plugin set)" % (root, n))
        if list(loaded) != [n for n in PLUGIN_SOURCES if n in loaded]:
            err.append("plugins must be loaded in the order %s" % list(PLUGIN_SOURCES))
    elif not guest and loaded and any(u.endswith(CC_EXT + CXX_EXT) for u in units):   # a host root loads a plugin only where its compile stage compiles a unit with it (a generated-only root's plugins are its run steps' inputs)
        err.append("host root %s loads plugin(s) %s (the host profile has the empty plugin set: the plugins enforce guest-ABI semantics)" % (root, ", ".join(sorted(loaded))))
    if not cxx:
        return err + (["admit.json %s: cxx_origin of a root without C++ units" % root] if origin is not None else [])
    if origin == "native":
        fl = rec.get("flags", {}).get("cxx", []) if isinstance(rec.get("flags"), dict) else []
        # fnvnx_sdd needs the define; a guest root's -fno-devirtualize comes from flags.semantics, its define is per root; a root of another compiler loads no plugin and needs none
        err += ["native C++ root %s: flags.cxx lacks %s" % (root, f) for f in ((SDD_DEFINE,) if guest and guest_cc else ()) if f not in fl]
    elif lifted_origin(origin):
        tool, pack, stage = origin["lifted_by"], origin.get("pack"), origin.get("stage")
        for n in (("fnvnx_ftoi",) if guest_cc else ()):   # lifted C++ does its conversions in helpers: the plugin proves it (and only it is required)
            if n not in loaded:
                err.append("lifted C++ root %s does not load plugin %s (mode lifted: every conversion outside an fnvnx_f2i function is an error)" % (root, n))
        mine = own_sources(root, files, roots, repo)
        owner = next((r for r in roots if isinstance(tool, str) and tree_of({r: roots[r]}, tool)), None)
        if not (isinstance(tool, str) and tool in files and tool.endswith(".py") and owner and roots[owner]["class"] == "tool"):
            err.append("cxx_origin.lifted_by %r is not a tracked .py of an admitted tool root" % (tool,))
        steps = rec.get("generated", []) if isinstance(rec.get("generated"), list) else []
        uses_roots = {u.get("root") for u in rec.get("uses", []) if isinstance(u, dict)}
        if "pack" in origin and pack not in ext_names and not (pack in uses_roots and pack in roots):
            err.append("cxx_origin.pack %r names neither an external of the recipe nor an admitted root in its `uses`" % (pack,))
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


# Relocation types that are a call (a branch to a function) per ELF e_machine: AArch64 (183) R_AARCH64_JUMP26 282, CALL26 283; x86-64 (62) R_X86_64_PLT32 4.
# The same sets as elffacts.CALL_TYPES (readelf names); every other relocation (data, ADRP/ADD, ABS32/64, GOT) takes the address of its target.
CALL_RELOCS = {183: frozenset((282, 283)), 62: frozenset((4,))}


def call_refs(path):
    """The names of the UNDEFINED global and weak symbols that object PATH (ELF32 or ELF64 little-endian, REL or RELA) reaches by a CALL relocation
    (CALL_RELOCS of its e_machine; a machine without an entry has none). A symbol whose address is only taken (an export table or vtable entry, a
    function pointer) is not here: taking an address performs no call. -> set of names."""
    d = open(path, "rb").read()
    if d[:4] != b"\x7fELF" or d[4] not in (1, 2) or d[5] != 1:
        raise ValueError("%s: not an ELF32/ELF64 little-endian object" % path)
    w64 = d[4] == 2
    machine, = struct.unpack_from("<H", d, 0x12)
    calls = CALL_RELOCS.get(machine, frozenset())
    shoff, = struct.unpack_from("<Q" if w64 else "<I", d, 0x28 if w64 else 0x20)
    esz, num = struct.unpack_from("<HH", d, 0x3a if w64 else 0x2e)
    secs = [struct.unpack_from("<IIQQQQIIQQ" if w64 else "<IIIIIIIIII", d, shoff + i * esz) for i in range(num)]
    out = set()
    for s in secs:
        if s[1] not in (4, 9):                                   # SHT_RELA, SHT_REL
            continue
        symsec = secs[s[6]]
        strtab = secs[symsec[6]]
        names = d[strtab[4]:strtab[4] + strtab[5]]
        syms = d[symsec[4]:symsec[4] + symsec[5]]
        ssz = 24 if w64 else 16
        rsz = (24 if s[1] == 4 else 16) if w64 else (12 if s[1] == 4 else 8)
        for o in range(s[4], s[4] + s[5], rsz):
            info, = struct.unpack_from("<Q" if w64 else "<I", d, o + (8 if w64 else 4))
            typ, k = (info & 0xffffffff, info >> 32) if w64 else (info & 0xff, info >> 8)
            if typ not in calls or not k:
                continue
            if w64:
                nm, sinfo, _, shndx = struct.unpack_from("<IBBH", syms, k * ssz)
            else:
                nm, _, _, sinfo, _, shndx = struct.unpack_from("<IIIBBH", syms, k * ssz)
            if shndx == 0 and sinfo >> 4 in SBIND and nm:
                out.add(names[nm:names.index(b"\0", nm)].decode())
    return out


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


def ensure_bare(extcache, commit):
    """The bare fetch EXTCACHE/<commit> of the artifacts repository at exactly COMMIT (shallow, atomic): the one place a pinned commit is fetched, by externals and by the deb mirror."""
    bare = os.path.join(extcache, commit)
    if not os.path.isdir(bare):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        tmp = "%s.tmp%d-%s" % (bare, os.getpid(), os.urandom(4).hex())
        os.makedirs(tmp)
        for cmd in (["init", "-q", "--bare"], ["fetch", "-q", "--depth", "1", artifacts_url(extcache), commit]):
            subprocess.run(["git", "-C", tmp] + cmd, env=env, check=True, capture_output=True)
        publish_dir(tmp, bare)
    return bare


def fetch_externals(exts, store, extcache, ws):
    """Content-pinned inputs: each external of EXTS fetched by exact commit into the bare cache EXTCACHE/<commit>, checked out ONCE at
    EXTCACHE/<commit>.co, ingested into the CAS STORE; its tree hash must equal tree_sha256 (BuildRed naming the external). -> {name:
    {host, tree, at}}. FACT: the verified pair (commit, tree_sha256) is stored as STORE/external-facts/<commit>-<tree_sha256>.json; a
    pair with a fact, its checkout and its tree in the STORE is not fetched, walked or hashed again (a warm call reads the fact only), and
    a variant of a root adds only its own stage keys. A mismatch stores nothing. Used by a recipe (Job.externals) and by gate_contract.py
    (the exe-derived inputs of the contract gates), one mechanism."""
    out = {}
    url = artifacts_url(extcache)
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    os.makedirs(ws, exist_ok=True)   # the index files live in WS: every caller may name a fresh sub-path of its scratch directory
    for e in exts:
        stage, bare, co = "external:" + e["name"], ext_bare(extcache, e["commit"]), ext_checkout(extcache, e["commit"])
        fact = os.path.join(store, "external-facts", "%s-%s.json" % (e["commit"], e["tree_sha256"]))
        if os.path.isfile(fact) and os.path.isdir(co) and os.path.isfile(os.path.join(store, "tree", e["tree_sha256"][:2], e["tree_sha256"] + ".json")):
            out[e["name"]] = {"host": co, "tree": e["tree_sha256"], "at": e["at"]}
            continue
        tmpco = None
        try:
            ensure_bare(extcache, e["commit"])
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
            shutil.rmtree(tmpco)   # the checkout was only the thing to hash: the published one is the verified tree placed as hard links of the store's objects (one copy of the bytes)
            cas.materialize(tree, store, tmpco)
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


def shard_of(key, n):
    """The shard of a unit whose first-stage key is KEY (hex): int(KEY, 16) mod N. The first-stage key is the PP stage key on the whole trees
    (unitstage.Build.first_key), a function of the unit and the declared inputs, so every shard build of one checkout assigns the same shard."""
    return int(key, 16) % n


def shard_spec(text):
    """`i/N` -> (i, N); N any integer >= 1, 0 <= i < N, else RecipeError (no cap: shard_partition asserts the partition)."""
    m = re.fullmatch(r"(0|[1-9][0-9]*)/([1-9][0-9]*)", text or "")
    if not m or not int(m.group(1)) < int(m.group(2)):
        raise RecipeError(["--shard %r: want i/N with N >= 1 and 0 <= i < N" % text])
    return int(m.group(1)), int(m.group(2))


def shard_partition(keys, n):
    """KEYS: {unit: first-stage key} -> N lists of units (sorted), shard i holding the units with shard_of == i. Asserts what the rule says: the
    lists are pairwise disjoint and together exactly the unit set; a violation is a recipe defect, loud."""
    parts = [sorted(u for u, k in keys.items() if shard_of(k, n) == i) for i in range(n)]
    flat = [u for p in parts for u in p]
    if len(flat) != len(set(flat)) or set(flat) != set(keys):
        raise RecipeError(["shard partition is not a partition of the %d units: %d listed, %d distinct" % (len(keys), len(flat), len(set(flat)))])
    return parts


STORE_KINDS = ("obj", "tree", "stage-docs")   # content-named files of a store, copied before the key records that name them


def _store_files(root, kind):
    base = os.path.join(root, kind)
    for d in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        for n in sorted(os.listdir(os.path.join(base, d))):
            if not n.startswith(".tmp-"):
                yield n, os.path.join(base, d, n)


def _tree_closure(store, h, seen):
    """The tree manifests and objects that tree H names in STORE -> [(kind, file name)]: a file's object, a directory's manifest and what it names, recursively."""
    if h in seen:
        return []
    seen.add(h)
    m = cas.manifest(h, store)
    out = [("tree", h + ".json")]
    if not cas._is_dir_manifest(m):   # a single-file tree: the manifest is the file entry itself
        return out + [("obj", m["h"] + (".x" if m["x"] else ""))]
    for e in m["entries"].values():
        out += _tree_closure(store, e["h"], seen) if e["t"] == "d" else [("obj", e["h"] + (".x" if e["x"] else ""))]
    return out


def store_export(store, key, dst):
    """Copy the cache entry of stage KEY of STORE into the store directory DST, under its key: the key's record, its stage document and every tree and object the
    record names (stdout, stderr, each output). DST is then a store that `store_import` (store-merge) takes in; nothing else of STORE is copied. A key with no
    record, or a record naming a tree or object the store lacks, is RecipeError. The timing sidecar is a measurement and is not part of the entry."""
    res = sandbox._load(store, key)
    if res is None:
        raise RecipeError(["store-export: no record for key %s in %s" % (key, store)])
    files, seen, miss = [], set(), []
    try:
        for h in [res["stdout"], res["stderr"]] + [res["outputs"][o] for o in sorted(res["outputs"])]:
            files += _tree_closure(store, h, seen)
    except (cas.CasError, KeyError) as e:
        raise RecipeError(["store-export: key %s names a tree the store lacks: %s" % (key, e)])
    for kind, n in sorted(set(files)):
        p = os.path.join(store, kind, n[:2], n)
        if not os.path.isfile(p):
            miss.append("%s/%s" % (kind, n))
    if miss:
        raise RecipeError(["store-export: key %s names a file the store lacks: %s" % (key, m) for m in miss[:20]])
    for kind, n in sorted(set(files)):
        cas.publish_file(os.path.join(dst, kind, n[:2], n), src=os.path.join(store, kind, n[:2], n))
    sd = os.path.join(store, "stage-docs", key[:2], key + ".json")
    if os.path.isfile(sd):
        cas.publish_file(os.path.join(dst, "stage-docs", key[:2], key + ".json"), src=sd)
    cas.publish_file(os.path.join(dst, "keys", key, "result.json"), src=os.path.join(store, "keys", key, "result.json"))
    return len(set(files)) + 2


def store_import(src, store, key):
    """Take the entry of KEY from the store directory SRC (a `store_export`) into STORE: SRC must hold exactly the closure of KEY's record (the record, and every tree
    and object it names, each object checked against its own sha256 name), else RecipeError and STORE is unchanged; then it is store_merge (a name present in both
    must hold the same bytes)."""
    rp = os.path.join(src, "keys", key, "result.json")
    if not os.path.isfile(rp):
        raise RecipeError(["store-import: %s holds no record of key %s" % (src, key)])
    if sorted(os.listdir(os.path.join(src, "keys"))) != [key]:
        raise RecipeError(["store-import: %s holds more than the entry of key %s" % (src, key)])
    with tempfile.TemporaryDirectory() as td:   # the closure check runs on a copy of nothing: the export is read where it is
        try:
            res = json.load(open(rp))
            closure = set()
            seen = set()
            for h in [res["stdout"], res["stderr"]] + [res["outputs"][o] for o in sorted(res["outputs"])]:
                closure |= set(_tree_closure(src, h, seen))
        except (OSError, ValueError, KeyError, cas.CasError) as e:
            raise RecipeError(["store-import: the entry of key %s is incomplete: %s" % (key, e)])
        have = {(k, n) for k in ("obj", "tree") for n, _ in _store_files(src, k)}
        if have != closure:
            raise RecipeError(["store-import: %s differs from the closure of key %s: %s" % (src, key, sorted(have ^ closure)[:5])])
    return store_merge(src, store)


def store_merge(src, dst):
    """Import the objects, trees, stage documents and key records of store SRC into store DST by content (the one import path). A name present in both
    must hold the same bytes (a key's result record included), else RecipeError `store-merge: key <k> differs` and DST is unchanged (the check is
    complete before the first copy). Objects are checked against their own name (sha256) on the way in. Files go in as the build writes them
    (cas.publish_file, mode and mtime of the store), the key records last, so a key in DST always has its objects. -> {kind: files copied}."""
    if not os.path.isdir(src):
        raise RecipeError(["store-merge: %s is not a store directory" % src])
    todo, bad = [], []
    for kind in STORE_KINDS:
        for n, p in _store_files(src, kind):
            q = os.path.join(dst, kind, n[:2], n)
            data = open(p, "rb").read()
            if kind == "obj" and hashlib.sha256(data).hexdigest() != n.partition(".")[0]:
                bad.append("store-merge: obj %s: content does not match its name" % n)
            elif os.path.exists(q):
                if open(q, "rb").read() != data:
                    bad.append("store-merge: %s %s differs" % (kind, n))
            else:
                todo.append((kind, p, q, os.stat(p).st_mode & 0o777))
    kd = os.path.join(src, "keys")
    for k in sorted(os.listdir(kd)) if os.path.isdir(kd) else []:
        p = os.path.join(kd, k, "result.json")
        if not os.path.isfile(p):
            continue
        q = os.path.join(dst, "keys", k, "result.json")
        if os.path.exists(q):
            if open(p, "rb").read() != open(q, "rb").read():
                bad.append("store-merge: key %s differs" % k)
        else:
            todo.append(("keys", p, q, None))
    if bad:
        raise RecipeError(bad[:20])
    tms = [(os.path.join(src, f), os.path.join(dst, f)) for f in sorted(os.listdir(src)) if f.endswith(".time") and os.path.isfile(os.path.join(src, f))]
    n = {k: 0 for k in STORE_KINDS + ("keys",)}
    for kind, p, q, mode in sorted(todo, key=lambda t: (t[0] == "keys", t[2])):
        cas.publish_file(q, src=p)
        if mode is not None:
            os.chmod(q, mode)
            os.utime(q, (0, 0))
        n[kind] += 1
    for p, q in tms:       # the timing sidecars (sandbox.write_time): measurements, never compared; whichever the destination holds is kept, else the source's is taken
        if not os.path.exists(q):
            cas.publish_file(q, src=p)
    return n


TIME_ROW = re.compile(r"^\s*(.+?)\s*:\s+(\d+\.\d+) \(\s*\d+%\)\s+(\d+\.\d+) \(\s*\d+%\)\s+(\d+\.\d+) \(\s*\d+%\)")


def timereport_passes(text):
    """{pass name: {usr, sys, wall}} of the stderr TEXT of a `-ftime-report` compile (GCC's table: `name : usr (pct) sys (pct) wall (pct) ggc (pct)`; the total row is
    `TOTAL`); a pass of the same name in a later table of the text adds up"""
    out = {}
    for ln in text.splitlines():
        m = TIME_ROW.match(ln)
        if m:
            d = out.setdefault(m[1], {"usr": 0.0, "sys": 0.0, "wall": 0.0})
            for k, v in zip(("usr", "sys", "wall"), m.groups()[1:]):
                d[k] = round(d[k] + float(v), 3)
    return out


def timing_report(store, keys):
    """The timing of the stages KEYS (a build's stage_keys) from the sidecars (sandbox.read_time): {kinds: {kind: {n, timed, cpu_s, wall_s}}, units: {unit: {cpu_s,
    wall_s}} (the stages named `kind:unit`), passes: {pass: {usr, sys, wall}} (the TIMEREPORT of every stage whose argv holds -ftime-report: its stderr), untimed: n}.
    A stage with no sidecar was only ever a hit in this store (or came from a store merged without it): it counts in n, not in timed."""
    kinds, units, passes, untimed = {}, {}, {}, 0
    for k in sorted(keys):
        dp = os.path.join(store, "stage-docs", k[:2], k + ".json")
        if not os.path.isfile(dp):
            untimed += 1
            continue
        doc = json.load(open(dp))
        kind, _, unit = doc["stage"].partition(":")
        d = kinds.setdefault(kind, {"n": 0, "timed": 0, "cpu_s": 0.0, "wall_s": 0.0})
        d["n"] += 1
        t = sandbox.read_time(store, k)
        if t is None:
            untimed += 1
        else:
            d["timed"] += 1
            d["cpu_s"] = round(d["cpu_s"] + t["cpu_s"], 3)
            d["wall_s"] = round(d["wall_s"] + t["wall_s"], 3)
            u = units.setdefault(unit or kind, {"cpu_s": 0.0, "wall_s": 0.0})
            u["cpu_s"] = round(u["cpu_s"] + t["cpu_s"], 3)
            u["wall_s"] = round(u["wall_s"] + t["wall_s"], 3)
        if "-ftime-report" in doc.get("argv", []):
            res = sandbox._load(store, k)
            if res:
                text = open(cas._obj(store, cas.manifest(res["stderr"], store)), errors="replace").read()
                for name, v in timereport_passes(text).items():
                    p = passes.setdefault(name, {"usr": 0.0, "sys": 0.0, "wall": 0.0})
                    for x in p:
                        p[x] = round(p[x] + v[x], 3)
    return {"kinds": kinds, "units": units, "passes": passes, "untimed": untimed}


class Job:
    stats = {"run_one": 0, "artifact_builds": 0}   # evaluations of `run` steps and builds of artifact roots in this process: what the regression tests count (never a stub of run_one)

    def __init__(self, repo, spec, store, jobs, ext):
        root, self.variant = split_spec(spec)
        self.repo, self.root, self.store, self.jobs, self.extcache = repo, root, store, jobs, ext
        self.roots, self.files = load_admit(repo), tracked(repo)
        self.rec = with_guest_link(load_recipe(repo, self.roots, spec), root, self.roots, repo)
        self.keys, self.run, self.hit, self.ext, self.ppkeys, self.built = set(), 0, 0, {}, {}, []
        self.arts = {}   # {artifact root: its build result}: a root is built once per Job, whatever number of steps run its artifact
        self.shard, self.shard_units = None, None   # (i, N) of a shard build (build(shard=...)); the units it compiled
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
        """Fetch by exact commit into the shared bare cache, check out, ingest, require tree_sha256 (BuildRed naming the external). An external that
        is another root's output (is_stage_ext: the compiler stage) is built by that root's stages (stage_external)."""
        exts = self.rec.get("external", [])
        self.ext.update(fetch_externals([e for e in exts if not is_stage_ext(e)], self.store, self.extcache, ws))
        for e in exts:
            if is_stage_ext(e):
                self.ext[e["name"]] = self.stage_external(ws, e)

    def include_of_tree(self, ref):
        """-> the CAS tree of an {include_of: ROOT} input: every tracked file under a repository include directory of ROOT's recipe, at its repo-relative path (the compile stage
        reads the same directories). The validation is include_of_errors."""
        errs = include_of_errors("%s: input" % self.root, ref, self.root, self.rec, self.roots, self.repo)
        if errs:
            raise BuildRed({"stage": "include_of:%s" % ref.get("include_of", "?"), "exit": None, "stderr": errs})
        dirs = include_of_dirs(ref["include_of"], self.roots, self.repo)
        tmp = tempfile.mkdtemp(prefix="incof-")
        try:
            _copy(self.repo, sorted({f for f in self.files if any(under(f, d) for d in dirs)}), "", tmp)
            return cas.ingest(tmp, self.store)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def external_of_host(self, ws, ref):
        """-> host path of an {external_of, name, path} input: the owner's pinned external is fetched into the shared cache by its exact commit (checked against its tree_sha256, as
        Job.externals does for this root's own), then read by resolve_input, the one reader. The validation is external_of_errors, inside resolve_input."""
        errs = external_of_errors("%s: input" % self.root, ref, self.root, self.rec, self.roots, self.repo)
        if errs:
            raise BuildRed({"stage": "external_of:%s" % ref.get("external_of", "?"), "exit": None, "stderr": errs})
        e = next(x for x in load_recipe(self.repo, self.roots, ref["external_of"]).get("external", []) if x.get("name") == ref["name"])
        fetch_externals([e], self.store, self.extcache, ws)
        try:
            return resolve_input(self.root, ref, self.repo, os.path.dirname(self.store), self.rec, self.extcache)[0]
        except ValueError as x:
            raise BuildRed({"stage": "external_of:%s" % ref["external_of"], "exit": None, "stderr": [str(x)]})

    def stage_external(self, ws, e):
        """The output E["output"] of root E["root"], built by that root's generated and run stages (cached by key: a hit costs the lookups, a plugin
        change re-runs the plugin stage only, an input of an earlier stage re-runs everything after it), ingested, and materialised once at
        EXTCACHE/stage-<tree> for the mount. -> {host, tree, at, stage_key}; stage_key = sha256 of the producer's sorted stage keys: the key of
        every PP and CC stage of this root holds it (FNVNX_COMPILER_KEY), beside the tree hash that its mount already holds."""
        p = Job(self.repo, e["root"], self.store, self.jobs, self.extcache)
        pws = tempfile.mkdtemp(dir=ws)
        try:
            _copy(self.repo, [f for f in p.files if under(f, e["root"])], e["root"], os.path.join(pws, "root"))
            p.externals(pws)
            p.generate(pws)
            p.run_all(pws)
        finally:
            self.run, self.hit = self.run + p.run, self.hit + p.hit
        self.keys |= p.keys
        if p.red:
            raise BuildRed({"stage": "external:" + e["name"], "exit": None, "stderr": ["producer %s is red: %s" % (e["root"], p.red[0]["stage"])] + list(p.red[0]["stderr"])[-18:]})
        rel = (p.rec.get("outputs") or {}).get(e["output"])
        path = os.path.join(pws, "root", rel) if isinstance(rel, str) else ""
        if not os.path.isdir(path):
            raise BuildRed({"stage": "external:" + e["name"], "exit": None, "stderr": ["root %s declares no directory output %r" % (e["root"], e["output"])]})
        tree = cas.ingest(path, self.store)
        host = os.path.join(self.extcache, "stage-" + tree)
        if not os.path.isdir(host):
            tmp = "%s.tmp%d-%s" % (host, os.getpid(), os.urandom(4).hex())
            os.makedirs(self.extcache, exist_ok=True)
            cas.materialize(tree, self.store, tmp)   # hard links of the store's objects: the mount is the ingested tree itself, not a second copy
            publish_dir(tmp, host)
        os.makedirs(os.path.join(self.extcache, "stage-ext"), exist_ok=True)   # the pointer a gate reads (host_compilers): name -> the tree this build used
        ptr = os.path.join(self.extcache, "stage-ext", e["name"] + ".json")
        with open(ptr + ".tmp%d" % os.getpid(), "w") as fh:
            json.dump({"host": host, "tree": tree}, fh, sort_keys=True)
        os.replace(ptr + ".tmp%d" % os.getpid(), ptr)
        return {"host": host, "tree": tree, "at": e["at"], "stage_key": hashlib.sha256(json.dumps(sorted(p.keys)).encode()).hexdigest()}

    def compiler_key(self):
        """The stage key of the compiler's producer root (stage_external), "" when the compiler is a distribution package (host profile)."""
        c = self.rec["compiler"]
        return self.ext[c["external"]].get("stage_key", "") if "external" in c else ""

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

    def pp_subtrees(self, tokens, skip=()):
        """The declared inputs of a PP stage that the TOKENS (the include directories and the flags) name inside an external other than SKIP: the subtree each named path
        denotes ({at: the path, tree: the content hash of that subtree or file}), a path nested in another named path folded into it, a path that is the whole external
        the whole external. RULE (a stage is keyed by exactly what its sandbox can read): `-I /ext/x/inc` mounts that directory, not the external `x`; what is not mounted
        is unreadable, so a PP that needed more fails loudly and never builds stale."""
        paths = sorted({p.rstrip("/") for t in tokens if isinstance(t, str) for p in re.findall(r"/[^\s=:,;\"']+", t)})
        out = []
        for n, e in sorted(self.ext.items()):
            if n in skip:
                continue
            kept = []
            for p in (q for q in paths if under(q, e["at"])):
                if not any(p == k or under(p, k) for k in kept):
                    kept.append(p)
            for p in kept:
                host = e["host"] + p[len(e["at"]):]
                if p == e["at"]:
                    out.append({"at": p, "tree": e["tree"]})
                elif os.path.exists(host):
                    out.append({"at": p, "tree": cas.ingest(host, self.store)})
        return sorted(out, key=lambda i: i["at"])

    def pp_uses(self, tokens):
        """The `uses` outputs a PP stage mounts: the `include_uses` ones (an include directory) and any the TOKENS name (a flag path such as -include); the other
        `uses` outputs are read by generated stages, link and tests, not by preprocessing, so they are not in a PP key."""
        iu = {"/uses/%s/%s" % (u["root"], u["output"]) for u in self.rec.get("include_uses", [])}
        named = {p for t in tokens if isinstance(t, str) for p in re.findall(r"/[^\s=:,;\"']+", t)}
        return [i for i in self.use_inputs_cached if i["at"] in iu or any(under(p, i["at"]) for p in named)]

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
                if entry_of(self.roots, self.root + "@" + self.variant if self.variant else self.root).get("target") == "guest":
                    bad = foreign_search_dirs(ts, [e["host"] for e in self.ext.values()])
                    if bad:   # a guest compile reads the pinned runtime's headers only: a search directory of the machine is no input of it
                        raise BuildRed({"stage": "toolrt", "exit": None, "stderr": ["guest compiler %s searches %s outside the externals of the recipe: the runtime headers of a guest "
                                                                                       "are the pinned external's own (verify/guest_compiler.json)" % (c["cxx"], ", ".join(bad))]})
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
            if ext not in self.ext and ext in self.roots:   # a tracked file of another admitted root of `uses`
                try:
                    full, unit = root_source(self.roots, set(self.files), self.rec.get("uses", []), self.root, ext, p)
                except ValueError as e:
                    raise BuildRed({"stage": "sources:" + self.root, "exit": None, "stderr": ["%s: %s" % (sr, e)]})
                dest = os.path.join(ws, "root", unit)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(os.path.join(self.repo, full), dest)
                if is_unit(unit):
                    out.append(unit)
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
            if self.roots[u["root"]].get("class") == "header":   # a header root has no recipe and no stage: INCLUDE is its tracked files, keyed by their tree hash
                pws = tempfile.mkdtemp(dir=ws)
                _copy(self.repo, [f for f in self.files if under(f, u["root"])], u["root"], os.path.join(pws, "root"))
                out.append({"at": "/uses/%s/%s" % (u["root"], u["output"]), "tree": cas.ingest(os.path.join(pws, "root"), self.store)})
                continue
            p = Job(self.repo, u["root"], self.store, self.jobs, self.extcache)
            pws = tempfile.mkdtemp(dir=ws)
            rel = (p.rec.get("outputs") or {}).get(u["output"])
            if isinstance(rel, str) and output_kind(rel, u["root"], generated_outputs(p.rec), p.files) == "srcdir":
                # a directory of tracked files: its content is the output, no stage of the producer runs (a header-only root has nothing to build)
                _copy(self.repo, [f for f in p.files if under(f, u["root"] + "/" + rel)], u["root"], os.path.join(pws, "root"))
                out.append({"at": "/uses/%s/%s" % (u["root"], u["output"]), "tree": cas.ingest(os.path.join(pws, "root", rel), self.store)})
                continue
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
                for o in step_outputs(g):
                    self.blocked[o] = f["stage"]

    def step_closure(self, steps, name):
        """The steps that produce NAME's inputs, transitively, and NAME itself, in recipe order: an input that is a stage ref of this root names its producer
        (`ROOT:run:STEP`), or is an output (file or directory) of one step; a step outside the closure cannot change what NAME reads."""
        by_name = {g["name"]: g for g in steps}
        if name not in by_name:
            raise ValueError("%s: root declares no run step %r" % (self.root, name))
        need, todo = set(), [name]
        while todo:
            n = todo.pop()
            if n in need:
                continue
            need.add(n)
            fe = by_name[n].get("foreach")
            for ref in by_name[n]["inputs"] + ([fe["table"]] if isinstance(fe, dict) and isinstance(fe.get("table"), dict) else []):
                if not (isinstance(ref, dict) and "stage" in ref and ref["stage"].partition(":")[0] == self.root):
                    continue
                kind, _, key = ref["stage"].partition(":")[2].partition(":")
                todo += [key] if kind == "run" and key in by_name else [g["name"] for g in steps if ref["path"] in g["outputs"] or any(under(ref["path"], d) for d in g.get("dirs", []))]
        return [g for g in steps if g["name"] in need]

    def run_all(self, ws, only=None):
        """The `run` stages of the recipe (RECIPE.md "Run stages"), after the generators; a red one blocks its outputs like a generator. ONLY = the name of one step: that
        step and the steps its inputs come from run, no other."""
        try:
            steps = expand_run(self.rec).get("run", [])
        except ValueError as e:
            self.red.append({"stage": "run:%s" % self.root, "exit": None, "stderr": [str(e)]})
            for g in self.rec.get("run", []):
                for o in step_outputs(g):
                    self.blocked[o] = "run:%s" % self.root
            return
        if only is not None:
            steps = self.step_closure(steps, only)
        for i, g in enumerate(steps):
            try:
                if "foreach" in g:
                    self.run_foreach(ws, i, g)
                else:
                    self.run_one(ws, i, g)
            except BuildRed as r:
                f = dict(r.args[0])
                self.red.append(f)
                for o in step_outputs(g):
                    self.blocked[o] = f["stage"]

    def run_foreach(self, ws, i, g):
        """A build-time foreach step (foreach_dynamic): read its table (an earlier step's output of this root, another root's stage output, or an external), make one step per
        row and run each as an ordinary step; each writes under the step's directory `into`. The table is no input of the rows: a row step's key is a function of its own
        row, tool and inputs, so an unchanged row is a hit whatever the other rows are. An empty table, a bad row, an output outside `into` or one two rows share is red."""
        fe, nm = g["foreach"], "run:%s:%s" % (self.root, g["name"])
        t = fe["table"]
        if "stage" in t and t["stage"].partition(":")[0] == self.root:
            if t["path"] in self.blocked:
                raise BuildRed({"stage": nm, "exit": None, "stderr": ["table %s is not produced: %s is red" % (t["path"], self.blocked[t["path"]])]})
            host = os.path.join(ws, "root", t["path"])
        else:
            host = resolve_input(self.root, t, self.repo, os.path.dirname(self.store), self.rec, self.extcache)[0]
        try:
            with open(host, encoding="utf-8") as f:
                rows = foreach_rows(t["path"], fe["key"], f.read())
            steps, seen = [], {}
            for row in rows:
                st = foreach_row_step(fe["row"], row, fe["key"])
                for o in step_outputs(st):
                    if not under(o, fe["into"]) or not rel_ok(o):
                        raise ValueError("row %s: output %s is outside %s" % (row[fe["key"]], o, fe["into"]))
                    if o in seen:
                        raise ValueError("row %s: output %s is also an output of %s" % (row[fe["key"]], o, seen[o]))
                    seen[o] = st["name"]
                steps.append(st)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise BuildRed({"stage": nm, "exit": None, "stderr": ["foreach table %s: %s" % (t["path"], e)]})
        red = []
        for st in expand_run(dict(self.rec, run=steps))["run"]:   # the forms and the debs closure of a step, as every other step
            try:
                self.run_one(ws, i, st)
            except BuildRed as r:
                red.append(r)
                self.red.append(dict(r.args[0]))
        if red:
            raise BuildRed({"stage": nm, "exit": None, "stderr": ["%d of %d rows are red, first: %s" % (len(red), len(steps), red[0].args[0]["stage"])]})
        os.makedirs(os.path.join(ws, "root", fe["into"]), exist_ok=True)

    def run_one(self, ws, i, g):
        """Stage `run:<root>:<name>`: executes the artifact (the linked output of an admitted root) in the sandbox. It sees the artifact at /art/<output>,
        the declared inputs at /in/<ident>, each declared deb's installed files at /deb/<name>/<absolute path>, and writes /out. Key = artifact
        content + input contents + deb sha256 (env.json) + argv. A nonzero exit, an undeclared or a missing output is red."""
        Job.stats["run_one"] += 1
        tmp = tempfile.mkdtemp(dir=ws)
        tool = "exec" in g
        if tool:
            ins, rootfs = self._rootfs(tmp, g)
        else:
            aroot, aout = g["artifact"].split(":", 1)
            if aroot not in self.arts:
                Job.stats["artifact_builds"] += 1
                self.arts[aroot] = build(self.repo, aroot, self.store, tempfile.mkdtemp(dir=ws), self.jobs, self.extcache)
            r = self.arts[aroot]
            if r["status"] != "green" or not r["elf"] or os.path.basename(r["elf"]["path"]) != aout:
                raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["artifact %s is not produced (root status %s)" % (g["artifact"], r["status"])] + ([] if not r.get("failure") else ["its failure: stage %s: %s" % (r["failure"]["stage"], " | ".join(r["failure"].get("stderr") or [])[:600])])})
            ins = [{"at": "/art", "tree": self._one_file(tmp, "art", r["elf"]["path"], aout)}]
        n = 0
        for ref in g["inputs"]:
            n += 1
            at = "/in/%d" % n
            if isinstance(ref, dict) and "stage" in ref and ref["stage"].partition(":")[0] == self.root:   # an output (file or directory) of an earlier step of this root, as a generator's stage ref
                if ref["path"] in self.blocked:
                    raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["input %s is not produced: %s is red" % (ref["path"], self.blocked[ref["path"]])]})
                ins.append({"at": at, "tree": cas.ingest(os.path.join(ws, "root", ref["path"]), self.store)})
                continue
            if isinstance(ref, dict) and set(ref) == {"plugin"}:   # the .so built by this root's plugin stage (against the root's compiler external): a stage input, so in the key
                if not self.built:
                    try:
                        self.built = self.plugins(ws)
                    except BuildRed as r:
                        raise BuildRed(dict(r.args[0], stage="run:%s:%s" % (self.root, g["name"]) + " <- " + r.args[0]["stage"]))
                tree = next((t for nm, t, _ in self.built if nm == ref["plugin"]), None)
                if tree is None:
                    raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["plugin %s is not built" % ref["plugin"]]})
                ins.append({"at": at, "tree": tree})
                continue
            if isinstance(ref, str):   # a repo-relative tracked file or directory (git ls-files only), like a generator input
                sub = [f for f in self.files if f == ref or under(f, ref)]
                ins.append({"at": at, "tree": cas.ingest(os.path.join(self.repo, ref), self.store) if sub == [ref] else self.ingest_files(tmp, "in%d" % n, sub, ref)})
            elif is_external_of(ref):   # an external of another root: fetched by its pin, then the same reader
                ins.append({"at": at, "tree": cas.ingest(self.external_of_host(ws, ref), self.store)})
            elif is_include_of(ref):   # the include closure of another root: its tracked trees, repo-relative
                ins.append({"at": at, "tree": self.include_of_tree(ref)})
            else:   # {external, path} or {stage, path}: resolve_input is the one reader
                host, _ = resolve_input(self.root, ref, self.repo, os.path.dirname(self.store), self.rec, self.extcache)
                ins.append({"at": at, "tree": cas.ingest(host, self.store)})
        pk = deb_pins()
        if not tool:
            for d in g["debs"]:
                if d not in pk:
                    raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["deb %s is not declared in env.json debs.packages" % d]})
                ins.append({"at": "/deb/" + d, "tree": self._deb_tree(tmp, d, pk[d])})
        env = {"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "PATH": "/usr/bin:/bin" if tool else "/deb",
               "FNVNX_DEBS": ";".join("%s=%s" % (d, pk[d]["sha256"]) for d in sorted(g["debs"]))}   # the deb sha256s are in the key
        env.update(g.get("env", {}))   # constant strings (check); the sandbox has no network namespace route, so nothing is fetched
        xs = g["exec"] if tool else None
        pyargv = None
        if xs == {"python": True}:   # the one python runtime of generate_one (pyrt): its inputs, env and PATH; the step's own inputs/scratch/stage refs as any tool step
            py = self.pyrt()
            ins += py["inputs"]
            env.update({k: v for k, v in py["env"].items() if k != "PYTHONNOUSERSITE"})
            env.update({"COLUMNS": "80", "PATH": os.path.dirname(py["python"]), "FNVNX_PYMODS": self.pymods})
            env.update(g.get("env", {}))
            pyargv = [py["python"], "-s", "-B"] + g["args"]
            xs = None
        elif isinstance(xs, dict):   # {external, path} | {root, output}: the tool file is a stage input (in the key by content), mounted executable at /exec/<name>
            if "external_of" in xs:
                host = self.external_of_host(ws, {k: v for k, v in xs.items() if k != "libs"})
                base = os.path.basename(xs["path"])
            elif "external" in xs:
                host, _ = resolve_input(self.root, {"external": xs["external"], "path": xs["path"]}, self.repo, os.path.dirname(self.store), self.rec, self.extcache)
                base = os.path.basename(xs["path"])
            else:
                at = "/uses/%s/%s" % (xs["root"], xs["output"])
                host = os.path.join(tmp, "xroot")
                cas.materialize(next(t["tree"] for t in self.use_inputs(ws) if t["at"] == at), self.store, host)
                base = xs["output"]
            ldirs = []
            for k, lb in enumerate(xs.get("libs", [])):
                if "external" in lb:
                    ld_, _ = resolve_input(self.root, lb, self.repo, os.path.dirname(self.store), self.rec, self.extcache)
                else:
                    ld_ = os.path.join(tmp, "xlib%d" % k)
                    cas.materialize(next(t["tree"] for t in self.use_inputs(ws) if t["at"] == "/uses/%s/%s" % (lb["root"], lb["output"])), self.store, ld_)
                if not os.path.isdir(ld_):
                    raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["exec.libs[%d] %r is not a directory" % (k, lb)]})
                ldirs.append(ld_)
                ins.append({"at": "/exec/lib/%d" % k, "tree": cas.ingest(ld_, self.store)})
            if ldirs:
                env["LD_LIBRARY_PATH"] = ":".join("/exec/lib/%d" % k for k in range(len(ldirs)))
            bad = exec_unresolved(host, tmp, g["debs"], ldirs)
            if bad:
                raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["exec-unresolved:%s: %s needs it and the declared debs do not provide it" % (b, base) for b in bad]})
            ins.append({"at": "/exec", "tree": self._one_file(tmp, "exec", host, base)})
            xs = "/exec/" + base
        argv = pyargv if pyargv else [xs] + g["args"] if tool else ["/art/" + aout] + g["args"]
        interp = None if tool else elf_interp(r["elf"]["path"])
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
        if tool:   # the declared debs' root layout: top-level symlinks are links, scratch is a tmpfs; the job count and the time limit are outside the key
            doc.update(links=rootfs, scratch=list(g.get("scratch", [])), unkeyed={"env": {"JOBS": str(self.jobs)}, "timeout": 7200})
            if not doc["links"]:
                del doc["links"]
            if not doc["scratch"]:
                del doc["scratch"]
        if g.get("host_cpu") and sandbox._load(self.store, sandbox.key_of(doc)) is None:
            bad = cpu_mismatch(pin)
            if bad:
                raise BuildRed({"stage": doc["stage"], "exit": None, "stderr": ["host_cpu: this host is not the reference host: " + "; ".join(bad)]})
        res = self.stage(doc)[1]
        got = sorted(x for x, e in cas.listing(res["outputs"]["/out"], self.store) if e["t"] != "d")
        dirs = g.get("dirs", [])
        stray = [r for r in got if r not in g["outputs"] and not any(under(r, d) for d in dirs)]
        absent = [o for o in g["outputs"] if o not in got] + [d + "/" for d in dirs if not any(under(r, d) for r in got)]
        if stray or absent:
            raise BuildRed({"stage": doc["stage"], "exit": 0, "stderr": ["outputs differ from the declared %s: unexpected %s, missing %s" % (sorted(g["outputs"] + dirs), stray[:10], absent[:10])]})
        cas.materialize(res["outputs"]["/out"], self.store, os.path.join(tmp, "out"))
        for o in g["outputs"]:
            os.makedirs(os.path.dirname(os.path.join(ws, "root", o)), exist_ok=True)
            shutil.copyfile(os.path.join(tmp, "out", o), os.path.join(ws, "root", o))
        for d in dirs:
            os.makedirs(os.path.dirname(os.path.join(ws, "root", d)), exist_ok=True)
            shutil.copytree(os.path.join(tmp, "out", d), os.path.join(ws, "root", d), symlinks=True)

    def _rootfs(self, tmp, g):
        """The root layout of the declared debs of a tool step: the union of their installed files (_deb_tree), each top-level directory an input mounted at
        its own path, each top-level symlink (base-files' /bin, /lib, /lib64, /sbin) a sandbox link. Two debs that ship one file are red. The trees are in
        the key by content (not by the package version). -> ([inputs], [links])."""
        pk = deb_pins()
        for d in g["debs"]:
            if d not in pk:
                raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["deb %s is not declared in env.json debs.packages" % d]})
        mkey = (os.path.realpath(self.store), tuple(sorted((d, pk[d]["sha256"]) for d in g["debs"])))   # the layout is a function of the pinned deb contents (their sha256) alone
        if mkey in ROOTFS_MEMO and all(os.path.isdir(x) for x in ROOTFS_MEMO[mkey][2].values()):
            for d, home in ROOTFS_MEMO[mkey][2].items():
                if not os.path.lexists(os.path.join(tmp, "deb-" + d)):
                    os.symlink(home, os.path.join(tmp, "deb-" + d))   # exec_unresolved and deb_resolve read the unpacked trees under tmp
            return list(ROOTFS_MEMO[mkey][0]), list(ROOTFS_MEMO[mkey][1])   # copies: the caller appends its own inputs
        union, owner = os.path.join(tmp, "rootfs"), {}
        os.makedirs(union)
        for d in sorted(g["debs"]):
            self._deb_tree(tmp, d, pk[d])
            base = os.path.join(tmp, "deb-" + d)
            for dp, dn, fn in os.walk(base, followlinks=False):
                rel = os.path.relpath(dp, base)
                for name in dn + fn:
                    src, p = os.path.join(dp, name), os.path.normpath(os.path.join(rel, name))
                    if os.path.isdir(src) and not os.path.islink(src):
                        os.makedirs(os.path.join(union, p), exist_ok=True)
                        continue
                    if p in owner:
                        if open(src, "rb").read() == open(os.path.join(union, p), "rb").read() if not os.path.islink(src) and not os.path.islink(os.path.join(union, p)) else (
                                os.path.islink(src) and os.path.islink(os.path.join(union, p)) and os.readlink(src) == os.readlink(os.path.join(union, p))):
                            continue   # the same file shipped twice is one file
                        raise BuildRed({"stage": "run:%s:%s" % (self.root, g["name"]), "exit": None, "stderr": ["debs %s and %s ship /%s with different content" % (owner[p], d, p)]})
                    owner[p] = d
                    if os.path.islink(src):
                        os.symlink(os.readlink(src), os.path.join(union, p))
                    else:
                        shutil.copy2(src, os.path.join(union, p), follow_symlinks=False)
        ins, links = [], []
        for name in sorted(os.listdir(union)):
            p = os.path.join(union, name)
            if "/" + name in ("/proc", "/dev", "/tmp", "/in", "/out", "/work"):
                continue   # the sandbox's own directories (and base-files' empty ones)
            if os.path.islink(p):
                links.append({"at": "/" + name, "to": os.readlink(p)})
            elif os.path.isdir(p):
                ins.append({"at": "/" + name, "tree": cas.ingest(p, self.store)})
        homes = {}
        for d in sorted(g["debs"]):   # the unpacked trees outlive this step: one per deb content per process, linked into each later step's tmp
            home = os.path.join(rootfs_home(), "%s-%s" % (d, pk[d]["sha256"]))
            if not os.path.isdir(home):
                shutil.move(os.path.join(tmp, "deb-" + d), home)
                os.symlink(home, os.path.join(tmp, "deb-" + d))
            homes[d] = home
        ROOTFS_MEMO[mkey] = (list(ins), list(links), homes)
        return ins, links

    def _one_file(self, tmp, name, path, base):
        d = os.path.join(tmp, "f-" + name)
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(path, os.path.join(d, base))
        os.chmod(os.path.join(d, base), 0o755)
        return cas.ingest(d, self.store)

    def _deb_tree(self, tmp, name, pin):
        """The files of the env.json package NAME under <tmp>/deb-<name>/<absolute path>: the data members of the pinned deb itself (the mirror's debs/<file> at the pinned
        mirror commit, checked against debs.packages.<name>.sha256), unpacked by `dpkg-deb -x`, with the symlink targets the archive holds. Nothing is read from the host's
        installed files, alternatives or maintainer scripts: the tree is a function of the pin alone. An absolute link target is made relative to the package tree."""
        mir = deb_mirror()
        try:
            bare = ensure_bare(self.extcache, mir["commit"])
            blob = subprocess.run(["git", "--git-dir", bare, "show", "%s:debs/%s" % (mir["commit"], pin["file"])], check=True, capture_output=True).stdout
        except (KeyError, subprocess.CalledProcessError, OSError) as x:
            raise BuildRed({"stage": "deb:" + name, "exit": None, "stderr": ["the deb %s of the mirror pin cannot be fetched: %s" % (pin.get("file"), (getattr(x, "stderr", b"") or str(x).encode()).decode(errors="replace")[-300:])]})
        if hashlib.sha256(blob).hexdigest() != pin["sha256"]:
            raise BuildRed({"stage": "deb:" + name, "exit": None, "stderr": ["the deb %s of the mirror has sha256 %s, env.json pins %s" % (pin["file"], hashlib.sha256(blob).hexdigest(), pin["sha256"])]})
        dest = os.path.join(tmp, "deb-" + name)
        debf = os.path.join(tmp, "pkg-" + name + ".deb")
        with open(debf, "wb") as f:
            f.write(blob)
        os.makedirs(dest)
        r = subprocess.run(["dpkg-deb", "-x", debf, dest], capture_output=True, text=True)
        os.unlink(debf)
        if r.returncode:
            raise BuildRed({"stage": "deb:" + name, "exit": r.returncode, "stderr": r.stderr.splitlines()[-20:]})
        for dp, dn, fn in os.walk(dest):
            for n in dn + fn:
                q = os.path.join(dp, n)
                if os.path.islink(q):
                    t = os.readlink(q)
                    if t.startswith("/"):
                        os.unlink(q)
                        os.symlink(os.path.relpath(t, os.path.dirname(q[len(dest):]) or "/"), q)
        return cas.ingest(dest, self.store)

    def mounts_file(self, tmp):
        """MOUNT_PREFIXES as /launch/mounts.json: the one list, read by genlaunch.py's require_mounted (a stage input, so in the key)."""
        p = os.path.join(tmp, "mounts.json")
        with open(p, "w") as f:
            json.dump(list(MOUNT_PREFIXES), f)
        return cas.ingest(p, self.store)

    def generate_one(self, ws, i, g, py, xin):
        owner = tree_of(self.roots, g["tool"])
        tmp = tempfile.mkdtemp(dir=ws)
        ins = [{"at": "/tool", "tree": self.ingest_files(tmp, "tool", [f for f in self.files if under(f, owner)], owner)},
               {"at": "/launch/genlaunch.py", "tree": cas.ingest(GEN_LAUNCH, self.store)},
               {"at": "/launch/mounts.json", "tree": self.mounts_file(tmp)}] + xin + self.use_inputs(ws)
        imp = import_mount(g["tool"], owner, set(self.files), [p for p in g["inputs"] if isinstance(p, str)], self.roots, self.repo)[1]
        if imp:   # the import closure of the tool in the declared import roots, repo-relative, and nothing else of those roots
            ins.append({"at": "/imp", "tree": self.ingest_files(tmp, "imp", imp, TREE)})   # at /imp/<path relative to the project tree>: the module path of gate_admit.import_roots
        if "objects_of" in g:
            ins.append({"at": "/objs", "tree": self.objects_input(ws, tmp, g)})
        for p in g["inputs"]:
            if is_external_of(p):   # a pinned external of another root, mounted at /in/external_of/<owner as a__b>/<name>/<path>: the content hash is in the key
                host = self.external_of_host(ws, p)
                at = "/in/external_of/%s/%s/%s" % (p["external_of"].replace("/", "__"), p["name"], p["path"])
                ins.append({"at": at, "tree": cas.ingest(host, self.store)})
                continue
            if is_include_of(p):   # the include closure of another root, mounted at /in/include_of/<owner as a__b>/ with repo-relative paths: the content hash is in the key
                ins.append({"at": "/in/include_of/%s" % p["include_of"].replace("/", "__"), "tree": self.include_of_tree(p)})
                continue
            if isinstance(p, dict):   # the output of an earlier generator of this recipe: already in the root's view (generate runs in list order), keyed by content
                if p["path"] in self.blocked:   # that generator is red: this stage has no input, it is blocked by it (never run on a missing file)
                    raise BuildRed({"stage": "gen:%s:%d" % (self.root, i), "exit": None, "stderr": ["input %s is not produced: %s is red" % (p["path"], self.blocked[p["path"]])]})
                ins.append({"at": "/stage/" + p["path"], "tree": cas.ingest(os.path.join(ws, "root", p["path"]), self.store)})
                continue
            sub = [f for f in self.files if f == p or under(f, p)]
            ins.append({"at": "/in/" + p, "tree": cas.ingest(os.path.join(self.repo, p), self.store) if sub == [p] else
                        self.ingest_files(tmp, "in%d" % len(ins), sub, p)})
        env = {k: v for k, v in py["env"].items() if k != "PYTHONNOUSERSITE"}
        env.update({"TZ": "UTC", "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0", "COLUMNS": "80", "PATH": os.path.dirname(py["python"]), "FNVNX_PYMODS": self.pymods})
        if imp:   # the directories of the mounted modules (sorted): a module of a declared import root imports its siblings
            env["PYTHONPATH"] = "/imp"   # the one entry: a module of a declared root is imported by its qualified name (namespace packages, no __init__.py invented)
        doc = {"format": "fnvnx-stage/1", "stage": "gen:%s:%d" % (self.root, i), "class": "build", "tool": "python", "cwd": "/work",
               "argv": [py["python"], "-s", "-B", "/launch/genlaunch.py", "\n".join(g["outputs"] + ["dir:" + d for d in g.get("dirs", [])]), "/tool/" + g["tool"][len(owner) + 1:]] + g["args"] + (["--section", g["objects_of"]["section"]] if isinstance(g.get("objects_of"), dict) and g["objects_of"].get("section") else []), "env": env,
               "inputs": sorted(py["inputs"] + ins, key=lambda x: x["at"]), "outputs": ["/out"], "ro_root": True}   # a generator writes only /out
        res = self.stage(doc)[1]
        got = sorted(r for r, e in cas.listing(res["outputs"]["/out"], self.store) if e["t"] != "d")
        dirs = g.get("dirs", [])
        stray = [r for r in got if r not in g["outputs"] and not any(under(r, d) for d in dirs)]
        absent = [o for o in g["outputs"] if o not in got] + [d + "/" for d in dirs if not any(under(r, d) for r in got)]
        if stray or absent:   # the files are exactly the declared ones plus any file inside a declared directory, each directory non-empty
            raise BuildRed({"stage": doc["stage"], "exit": 0, "stderr": ["outputs differ from the declared %s: unexpected %s, missing %s" % (sorted(g["outputs"] + dirs), stray[:10], absent[:10])]})
        cas.materialize(res["outputs"]["/out"], self.store, os.path.join(tmp, "out"))
        for o in g["outputs"]:
            os.makedirs(os.path.dirname(os.path.join(ws, "root", o)), exist_ok=True)
            shutil.copyfile(os.path.join(tmp, "out", o), os.path.join(ws, "root", o))
        for d in dirs:
            os.makedirs(os.path.dirname(os.path.join(ws, "root", d)), exist_ok=True)
            shutil.copytree(os.path.join(tmp, "out", d), os.path.join(ws, "root", d))

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
        out += ["/uses/%s/%s" % (u["root"], u["output"]) for u in self.rec.get("include_uses", [])]   # the mounted directory output of another root, an absolute path inside the sandbox
        return out

    def plugin_env(self, ws):
        """The common inputs of every plugin stage: the host driver `PLUGIN_CXX` (derived from env.json), the plugin headers of the GUEST compiler (the external of
        verify/guest_compiler.json, `cxx -print-file-name=plugin`, whatever root asks: a plugin is bound to the exact compiler that loads it), the tool runtime and the
        startup files -> dict. A plugin stage's key is f(source content, host compiler, flags, headers); the consuming root's driver is not in it."""
        host = os.path.join(hostprofile.HOST["bindir"], PLUGIN_CXX)
        pin = (load_pin(self.repo) or {}).get("external")
        if not isinstance(pin, dict):
            raise BuildRed({"stage": "plugin", "exit": None, "stderr": ["%s is missing or has no external" % GUEST_PIN]})
        if pin["name"] not in self.ext:
            self.ext.update(fetch_externals([pin], self.store, self.extcache, ws))
        cxx = self.ext[pin["name"]]["host"] + "/" + pin_drivers()[1]
        pdir = subprocess.run([cxx, "-print-file-name=plugin"], capture_output=True, text=True).stdout.strip()
        if not os.path.isfile(os.path.join(pdir, "include", "gcc-plugin.h")):
            raise BuildRed({"stage": "plugin", "exit": None, "stderr": ["no plugin headers of %s in %s (the guest compiler's tree)" % (cxx, pdir)]})
        inc, trees = pdir, [pdir]
        for e in self.ext.values():   # headers inside an external compiler: its tree is a declared input at `at` already
            if under(pdir, e["host"]):
                inc, trees = e["at"] + pdir[len(e["host"]):], []

        pnames = {n for n, e in self.ext.items() if under(cxx, e["host"]) or under(pdir, e["host"])}   # the externals the plugin stage reads: the one holding its compiler, no other

        def make():
            exes, ts = unitstage.gcc_set(host)
            # a native driver prints a bare `as` / `ld` (it finds them on PATH): the binutils of env.json are /usr/bin/as and /usr/bin/ld;
            # linking a shared object also needs collect2 and ld, which gcc_set (compile only) does not list
            q = lambda n: subprocess.run([host, "-print-prog-name=" + n], capture_output=True, text=True).stdout.strip()  # noqa: E731
            exes = [e if e.startswith("/") else "/usr/bin/" + e for e in exes + [q("collect2"), q("ld")]]
            return self.plus(runtime.build_tool(self.store, sorted(set(exes)), sorted(set(ts + [t for t in trees if not any(under(t, x) for x in ts)]))), pnames)   # the plugin headers of the host compiler itself are in its own tree already
        rt = self.cached_rt("tool-plugin-" + hashlib.sha256(repr((host, pdir, sorted((e["at"], e["tree"]) for n, e in self.ext.items() if n in pnames))).encode()).hexdigest()[:16], make)[1]
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
            out.append((n, res, plugin_flags(n, p["args"], "/plug/%s/%s.so" % (n, n), PLUGIN_OUT.get(n))))
        return out

    def analysis_plugin(self, ws):
        """The plugin of the gate `objstub` (ANALYSIS_PLUGIN): built by the same stage as every plugin, against the guest compiler's headers. It is no
        plugin of the root's compile stages (no root declares it, no CC stage loads it): the gate loads it over the preprocessed text of the units.
        -> tree of its /out."""
        return self.plugin_build(self.plugin_env(ws), ws, ANALYSIS_PLUGIN[0], ANALYSIS_PLUGIN[1], [])

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
        named = list(rec["include"]) + [t for fl in flags.values() for t in fl]
        rtp, _ = self.toolrt(used, {cc} if cc else set())   # PP mounts the compiler's external whole; any other external only by the subtree the include path and the flags name (pp_subtrees)
        self.pp_sub = self.pp_subtrees(named, {cc} if cc else set())
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
        if self.analysis is None and guest and any(k != "asm" for k in kinds.values()):   # the analysis plugin is bound to the guest compiler: a host root has none
            try:
                self.analysis = self.analysis_plugin(ws)
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
        self.use_inputs_cached = self.use_inputs(ws)
        ppin = self.pp_sub + self.pp_uses(named)   # what PP mounts besides the compiler: external subtrees and `uses` outputs named by an include or a flag
        for k in sorted(set(kinds.values())):
            fp = os.path.join(ws, "flags-%s.json" % k)
            json.dump({"argv": flags[k]}, open(fp, "w"))   # semantics first, then the root's own
            builds[k] = unitstage.Build(argparse.Namespace(store=self.store, cold=False, cxx=drv["c" if k == "asm" else k], toolrt=rtp, flags_file=fp,
                                                           src_root=os.path.join(ws, "root") + "@/src", include=self.includes(ws), mounted=[i["at"] for i in ppin]))
            builds[k].cc_drop = drop | {i["at"] for i in ppin}
            builds[k].rt = dict(builds[k].rt, inputs=sorted(builds[k].rt["inputs"] + ppin, key=lambda i: i["at"]))
            builds[k].variant = self.variant
            builds[k].compiler_key = self.compiler_key()
            builds[k].cc_flags = list(self.aux)
            builds[k].hint_ns = self.root + ("@" + self.variant if self.variant else "")   # closure hints are per root (unitstage.hint_path)
            if k in pk:   # plugins are loaded by the CC stage only, never by preprocessing (guest roots: C and C++ units; host roots: C++ only)
                ok = loadable[k]
                builds[k].cc_flags = [f for n, _, fl in self.built if n in ok for f in fl] + list(self.aux)
                builds[k].cc_inputs = [("/plug/" + n, t) for n, t, _ in self.built if n in ok]

        if self.shard:   # the shard rule (shard_of): this build compiles the units of its shard only; the partition is asserted from the unit list
            ukeys = {u: builds[kinds[u]].first_key(u) for u in units}
            parts = shard_partition(ukeys, self.shard[1])
            units = parts[self.shard[0]]
            self.shard_units = list(units)

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

    def ld_tool(self):
        """(path of the pinned ld, its tool runtime) for this root's link stages: the linker of the compiler, with the compiler's external and that of `link.libs` mounted."""
        lk, cc = self.rec["link"], self.drivers()[0]
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
        return ld, rt

    def script_refs(self, ws):
        """The symbols the link script itself references (`ASSERT(DEFINED(x))`, assignments from x) that no input defines: what the pinned ld reports for the script linked
        alone, never read from the script text (script_refs_loop). One stage per pass, keyed by the script, the flags and the ld. [] for a link without a script."""
        lk = self.rec["link"]
        if "script" not in lk:
            return []
        ld, rt = self.ld_tool()
        mine = {f[len(self.root) + 1:] for f in self.files if under(f, self.root)}
        src = os.path.join(ws, "root", lk["script"]) if link_generated(lk["script"], mine, set(generated_outputs(self.rec))) else os.path.join(self.repo, link_file(lk["script"], self.root, mine, self.roots, self.files))
        sd = os.path.join(tempfile.mkdtemp(dir=ws), "ldscript")
        os.makedirs(sd)
        shutil.copyfile(src, os.path.join(sd, os.path.basename(lk["script"])))
        with open(os.path.join(sd, "empty.o"), "wb") as f:   # the one input ld needs: an empty object of the link's target (no symbols of its own)
            f.write(empty_object(*TARGETS[emulation_of(lk["flags"])]))
        tree = cas.ingest(sd, self.store)
        kind = lk.get("kind", "pie")

        def run(defined):
            argv = [ld] + LD_KIND[kind] + lk["flags"] + ["-T", "/ld/" + os.path.basename(lk["script"]), "--unresolved-symbols=report-all", "-o", "/out/x.elf"] + [
                "--defsym=%s=0" % n for n in defined] + ["/ld/empty.o"]
            doc = {"format": "fnvnx-stage/1", "stage": "ldscript:%s:%d" % (self.root, len(defined)), "argv": argv, "cwd": "/", "class": "build", "tool": "ld",
                   "env": dict(unitstage.ENV, PATH=os.path.dirname(ld)), "outputs": ["/out"], "inputs": merge_inputs(rt["inputs"] + [{"at": "/ld", "tree": tree}], [], self.store, ws)}
            try:
                self.stage(doc)
            except BuildRed as e:
                return e.args[0]["stderr"] or ["(no stderr)"]
            return None
        return script_refs_loop(run)

    def link(self, ws, groups, output=None, tag="", offer=None, refs=()):
        """Stage `ld:<root>`: the linker of the compiler with the options the rule fixes (RECIPE.md "Link"). GROUPS: [(label, [(unit, path of
        the .o file)])]: the root's own objects, then those of every `link.roots` root, in list order. Every object is linked whole;
        the guest libraries of `link.libs` are searched (-L, -l) as the gcc driver does for the ILP32 PIE multilib. OFFER: [(label, [(unit, path)])]
        makes this the selecting link: each offered root is an archive of its objects inside a group, REFS are `-u` references, unresolved symbols are
        ignored, and `self.offered` = {label: {member: unit}}; the objects ld pulled are then in the map. -> (tree of /out,
        tree of /objs, the inputs the checks of this link read)."""
        lk = self.rec["link"]
        output = output or lk["output"]   # a test is linked to its own output (RECIPE.md, tests); TAG tells the links of one root apart
        ld, rt = self.ld_tool()
        pv = tempfile.mkdtemp(dir=ws)   # a private scratch of this invocation: a stage is built once in its own directory, never in a name another invocation of the same ws can take
        od, sd = os.path.join(pv, "objs"), os.path.join(pv, "ld")
        args = []
        for label, objs in groups:
            for u, p in objs:
                dest = os.path.join(od, label, u + ".o")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(p, dest)
                args.append("/objs/%s/%s.o" % (label, u))
        os.makedirs(sd)
        arch_args, arch_in = [], []
        if offer is not None:
            ad = os.path.join(pv, "arch")
            os.makedirs(ad)
            self.offered = {}
            for label, objs in offer:
                names = {"%05d.o" % i: u for i, (u, _) in enumerate(objs)}
                with open(os.path.join(ad, label + ".a"), "wb") as f:
                    f.write(ar_bytes([("%05d.o" % i, open(p, "rb").read()) for i, (_, p) in enumerate(objs)]))
                self.offered[label] = names
                arch_args.append("/arch/%s.a" % label)
            arch_in = [{"at": "/arch", "tree": cas.ingest(ad, self.store)}]
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
            yd = os.path.join(pv, "syslib")
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
        sel = (["--unresolved-symbols=ignore-all"] if offer is not None else []) + [x for r in refs for x in ("-u", r)]
        argv = [ld] + LD_KIND[kind] + dl + lk["flags"] + sc + vs + ds + sel + ["--cref", "--no-demangle", "-o", "/out/" + output, "-Map=/out/" + output + ".map"] + pre + search + [
            "--whole-archive"] + args + ["--no-whole-archive"] + (["--start-group"] + arch_args + ["--end-group"] if arch_args else []) + (["--start-group"] + libs + ["--end-group"] if libs else []) + post
        objs_tree, ld_tree = cas.ingest(od, self.store), cas.ingest(sd, self.store)
        doc = {"format": "fnvnx-stage/1", "stage": "ld:" + self.root + (":" + tag if tag else ""), "argv": argv, "cwd": "/", "class": "build", "tool": "ld",
               "env": dict(unitstage.ENV, PATH=os.path.dirname(ld)), "outputs": ["/out"],
               "inputs": merge_inputs(rt["inputs"] + [{"at": "/objs", "tree": objs_tree}, {"at": "/ld", "tree": ld_tree}] + arch_in, sysin, self.store, ws)}
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
        pv = tempfile.mkdtemp(dir=ws)
        for i, d in enumerate(dirs):
            v = os.path.join(pv, "crt%d" % i)
            os.makedirs(v)
            for f in crt(d):
                shutil.copyfile(os.path.join(d, f), os.path.join(v, f))
            mnt.append({"at": d, "tree": cas.ingest(v, self.store)})
        pd = os.path.join(pv, "probe")
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
        -> the record (elffacts.record). BuildRed(stage ldfacts:<root>) if the pinned readelf is not a file of a pinned package, or a layout does not fit."""
        lk, stage = self.rec["link"], "ldfacts:" + self.root
        name = lk["output"]
        pn = subprocess.run([self.hostpath(self.drivers()[0]), "-print-prog-name=ld"], capture_output=True, text=True).stdout.strip()
        d, b = posixpath.split(posixpath.normpath(unitstage.prog(pn)))
        rd = posixpath.join(d, b[:-2] + "readelf") if b.endswith("ld") else ""
        # pinned by content when the linker lies in the compiler's declared external (commit + tree_sha256: the compiler's own binutils, mounted like its ld);
        # else a file of a package env.json pins
        ce = self.ext.get(self.rec["compiler"].get("external")) if "external" in self.rec["compiler"] else None
        if ce is not None and rd.startswith(ce["host"] + "/"):
            rd = ce["at"] + rd[len(ce["host"]):]
            rt = self.toolrt([self.drivers()[0]], {self.rec["compiler"]["external"]})[1]
        else:
            bad = envcheck.system_lib_errors([rd], json.load(open(os.path.join(self.repo, ENV_JSON)))["packages"], envcheck.file_owners()) if rd else ["the linker %r has no readelf beside it" % b]
            if bad:
                raise BuildRed({"stage": stage, "exit": None, "stderr": ["the pinned readelf beside the linker: %s" % x for x in bad]})
            rt = self.cached_rt("tool-readelf-" + hashlib.sha256(repr(rd).encode()).hexdigest()[:16],    # a package's readelf reads no external
                                lambda: runtime.build_tool(self.store, [rd], []))[1]
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
        host = self.ext[pin["external"]["name"]]["host"]
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

    def synthesised(self, ws, kind, objs, script=None):
        """Stage `ldsynth:<root>:<kind>`: the pinned ld links the root's OWN objects OBJS [(unit, path)] with the options of KIND (LD_KIND: guest pie plus the guest script SCRIPT, a repo path;
        host shared or exe) and `--unresolved-symbols=ignore-all --no-gc-sections`, nothing else. The synthesised names are those the output defines (GLOBAL or LOCAL) that no input
        object and no script assignment defines (synth_set): ld's own answer to the relocations (GOT, TLS, PLT) the root really carries, never a probe and never a list. One stage per
        root and kind, keyed by the objects, ld and the options. Needs self.rec["link"] = {"flags", "kind"} (ld_tool). -> set of names. A failed link is BuildRed."""
        lk = self.rec["link"]
        ld, rt = self.ld_tool()
        sd = os.path.join(tempfile.mkdtemp(dir=ws), "ldsynth")
        os.makedirs(os.path.join(sd, "objs"))
        args = []
        for k, (u, path) in enumerate(objs):
            shutil.copyfile(path, os.path.join(sd, "objs", "%05d.o" % k))
            args.append("/ld/objs/%05d.o" % k)
        text = ""
        if script:
            text = open(os.path.join(self.repo, script)).read()
            with open(os.path.join(sd, "link.ld"), "w") as f:
                f.write(text)
        argv = [ld] + LD_KIND[kind] + lk["flags"] + (["-T", "/ld/link.ld"] if script else []) + ["--unresolved-symbols=ignore-all", "--no-gc-sections", "-o", "/out/x.elf"] + args
        doc = {"format": "fnvnx-stage/1", "stage": "ldsynth:%s:%s" % (self.root, kind), "argv": argv, "cwd": "/", "class": "build", "tool": "ld",
               "env": dict(unitstage.ENV, PATH=os.path.dirname(ld)), "outputs": ["/out"], "inputs": merge_inputs(rt["inputs"] + [{"at": "/ld", "tree": cas.ingest(sd, self.store)}], [], self.store, ws)}
        res = self.stage(doc)[1]
        d = open(cas._obj(self.store, cas.manifest(res["outputs"]["/out"], self.store)["entries"]["x.elf"]), "rb").read()
        return synth_set(d, [open(path, "rb").read() for _, path in objs], text)

    def link_probe(self, ws):
        """Stage `drv:<root>`: the guest runtime's C++ driver (the cxx driver of the triplet, inside the pinned compiler tree that holds the runtime) run with the guest semantic flags
        (the common set: the profile is one fact for every guest link, native and lifted alike) and `-###` on the link of a one-line C++ file, hermetic (the driver prints the commands it would run; none is run).
        -> (stderr text, the runtime external's mount point): the link command of the multilib the flags select."""
        pin = load_pin(self.repo)
        rt = pin["external"]
        if rt["name"] not in self.ext:
            self.externals(ws)
        drv = rt["at"] + "/" + pin_drivers()[1]
        flags = semantics_for(self.hostpath(self.drivers()[1]), "cxx", plugsem.semantic_flags())
        tr = self.toolrt([drv])[1]
        pd = os.path.join(tempfile.mkdtemp(dir=ws), "probe")
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


def synth_set(out, objs, script_text=""):
    """The linker-synthesised names of one link: the symbols (GLOBAL, WEAK or LOCAL; not section or file symbols) that the linked ELF bytes OUT define, minus those some input object
    (OBJS: ELF relocatable bytes) defines and those the link script assigns."""
    own = set(undefset.script_defined(script_text)) if script_text else set()
    for o in objs:
        own |= {n for n, _, _, shndx, typ in ldcheck_syms(o, 2) if shndx != 0 and typ not in (3, 4) and n}
    return {n for n, _, _, shndx, typ in ldcheck_syms(out, 2) if shndx != 0 and typ not in (3, 4) and n} - own


def synth_names(repo, root, cache, kinds, script=None, objs=()):
    """The linker-synthesised names of ROOT for each link kind in KINDS (Job.synthesised, one cached stage per root and kind): the pinned ld of ROOT's compiler on ROOT's own objects OBJS
    [(unit, path)] and the kind's fixed options. A guest kind is `pie` (emulation aarch64linux32, link/guest.json's SCRIPT); a host kind is `shared` or `exe` (elf_x86_64, no script).
    -> {kind: set of names}. BuildRed/RecipeError as the stage."""
    store = os.path.join(cache, "store")
    j = Job(repo, root, store, 1, ext_dir(cache))
    ws = tempfile.mkdtemp(prefix="synth-")
    try:
        j.externals(ws)
        out = {}
        for k in kinds:
            emu = next(e for e in KIND_EMU[k] if TARGETS[e][1] == (183 if entry_of(j.roots, root).get("target") == "guest" else 62))
            j.rec = dict(j.rec, link={"output": "x.elf", "flags": ["-m", emu], "kind": k})
            out[k] = j.synthesised(ws, k, list(objs), script)
        return out
    finally:
        shutil.rmtree(ws, ignore_errors=True)


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


def exttree(e, cache, extc=None):
    """Host directory of external E (a recipe `external` entry) checked out by its exact commit from the bare fetch the build made in
    CACHE/external, into CACHE/exttree/<commit> (shared, atomic). For gates that read what a build linked in."""
    extc = extc or ext_dir(cache)   # the one accessor, unless the build names its external cache (--ext): the readers go where the writer wrote
    if is_stage_ext(e):   # a root's output: the directory the build materialised (stage_external)
        with open(os.path.join(extc, "stage-ext", e["name"] + ".json")) as fh:
            return json.load(fh)["host"]
    dest = os.path.join(cache, "exttree", e["commit"])
    if not os.path.isdir(dest):
        tmp = "%s.tmp%d-%s" % (dest, os.getpid(), os.urandom(4).hex())
        os.makedirs(tmp)
        env = dict(os.environ, GIT_INDEX_FILE=tmp + ".idx")
        bare = os.path.join(extc, e["commit"])
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


EXTERNAL_OF_KEYS = {"external_of", "name", "path"}


def is_external_of(ref):
    """REF is the input form {"external_of": ROOT, "name": EXTERNAL, "path": SUB}: a path inside a pinned external that ANOTHER admitted root declares (RECIPE.md "An external of another root")."""
    return isinstance(ref, dict) and set(ref) == EXTERNAL_OF_KEYS and all(isinstance(v, str) for v in ref.values())


def is_include_of(ref):
    """REF is the input form {"include_of": ROOT}: the tracked trees of ROOT's include closure (RECIPE.md "The include closure of a root")."""
    return isinstance(ref, dict) and set(ref) == {"include_of"} and isinstance(ref["include_of"], str)


def include_of_dirs(owner, roots, repo="."):
    """[repo dir]: the include closure of admitted root OWNER as its compile stage resolves it: the directories of its recipe's `include` that lie in the repository
    (a directory inside an external is a declared input of the compiler, not a tracked tree), sorted. ValueError if the recipe cannot be read."""
    try:
        orec = load_recipe(repo, roots, owner)
    except (OSError, ValueError, RecipeError) as e:
        raise ValueError("the recipe of %s cannot be read: %s" % (owner, str(e)[:200]))
    inc = orec.get("include") if isinstance(orec, dict) else None
    if not isinstance(inc, list):
        raise ValueError("the recipe of %s has no `include` list" % owner)
    ats = [e["at"] for e in (orec.get("external") or []) if isinstance(e, dict) and isinstance(e.get("at"), str)]
    return sorted({d for d in inc if isinstance(d, str) and not any(under(d, a) for a in ats)})


def include_of_errors(where, ref, root, rec, roots, repo="."):
    """[str]: the ONE rule of the `include_of` form (check, every step kind, and the mount). Red: a ref of another shape; ROOT is this root; not an admitted root; neither a root this
    recipe `uses` nor a declared import of this root (the dependency is declared); its recipe cannot be read or holds no repository include directory."""
    if not is_include_of(ref):
        return ["%s: %r is not {include_of: ROOT}" % (where, ref)]
    owner, me = ref["include_of"], split_spec(root)[0]
    if owner == me:
        return ["%s: include_of names this root itself: its include dirs are its own sources" % where]
    if owner not in roots:
        return ["%s: include_of %s is not an admitted root" % (where, owner)]
    declared = {u.get("root") for u in (rec.get("uses") or []) if isinstance(u, dict)} | set(roots[me].get("imports") or [] if isinstance(roots.get(me, {}).get("imports", []), list) else [])
    if owner not in declared:
        return ["%s: include_of %s is not a root this recipe `uses` nor a declared import of %s (declare the dependency)" % (where, owner, me)]
    try:
        dirs = include_of_dirs(owner, roots, repo)
    except ValueError as e:
        return ["%s: %s" % (where, e)]
    return [] if dirs else ["%s: the recipe of %s has no include directory inside the repository" % (where, owner)]


def external_of_errors(where, ref, root, rec, roots, repo="."):
    """[str]: the ONE rule of the `external_of` form, called by check (every step kind), by resolve_input (key and mount). The form names the owner root OWNER, the
    external NAME of OWNER's own recipe and a path inside its tree. Red: a ref of another shape; OWNER is this root (that is the {external, path} form), is not an admitted root
    or has no recipe; OWNER is neither a root this recipe `uses` nor a declared import (admit.json `imports`) of ROOT (the dependency is declared, never reached by name alone);
    OWNER's recipe declares no external NAME; the external is a root's built output (a stage external: take it through `uses`); the path is absolute or leaves the tree.
    The pin is OWNER's (commit and tree_sha256): the mount is checked against it, so a changed pin is a changed tree in the key."""
    if not is_external_of(ref):
        return ["%s: %r is not {external_of: ROOT, name: EXTERNAL, path: SUB} (strings)" % (where, ref)]
    owner, name, sub = ref["external_of"], ref["name"], ref["path"]
    me = split_spec(root)[0]
    if owner == me:
        return ["%s: external_of names this root itself: use {external, path}" % where]
    if owner not in roots:
        return ["%s: external_of %s is not an admitted root" % (where, owner)]
    declared = {u.get("root") for u in (rec.get("uses") or []) if isinstance(u, dict)} | set(roots[me].get("imports") or [] if isinstance(roots.get(me, {}).get("imports", []), list) else [])
    if owner not in declared:
        return ["%s: external_of %s is not a root this recipe `uses` nor a declared import of %s (declare the dependency)" % (where, owner, me)]
    try:
        orec = load_recipe(repo, roots, owner)
    except (OSError, ValueError, RecipeError) as e:
        return ["%s: the recipe of %s cannot be read: %s" % (where, owner, str(e)[:200])]
    e = next((x for x in (orec.get("external") or []) if isinstance(x, dict) and x.get("name") == name), None) if isinstance(orec, dict) else None
    if e is None:
        return ["%s: the recipe of %s declares no external %r" % (where, owner, name)]
    if is_stage_ext(e):
        return ["%s: external %r of %s is a root's built output: take it through `uses`" % (where, name, owner)]
    if not sub or os.path.isabs(sub) or ".." in sub.split("/"):
        return ["%s: external_of path %r is absolute or leaves the tree" % (where, sub)]
    return []


def resolve_input(root, ref, repo=".", cache=None, rec=None, extc=None):
    """THE reader of a path named by an input file of a gate (imports.in.json, registry.in.json, ...); no gate opens one itself.
    REF is a bare string (a tracked file or directory of ROOT) or {"external": NAME, "path": P}: NAME must be an `external` of ROOT's recipe,
    its tree is mounted from the pinned commit and checked against its tree_sha256. Red (ValueError) for: an undeclared external, a path that
    is absolute or leaves the tree (.. or a symlink), a missing path, an untracked path, a tree_sha256 mismatch.
    -> (host path, ident); IDENT is "tree_sha256:path" of an external, "root/path" of a tracked file: the gate key takes it."""
    if isinstance(ref, str):
        base, p, kind = os.path.join(repo, root), ref, None
    elif isinstance(ref, dict) and set(ref) == {"external", "path"} and all(isinstance(v, str) for v in ref.values()):
        kind, p = ref["external"], ref["path"]
        if rec is None:
            rec = load_recipe(repo, load_admit(repo), root)
        e = next((x for x in rec.get("external", []) if x["name"] == kind), None)
        if e is None:
            raise ValueError("%s: input %r names the external %r, which the root's recipe does not declare" % (root, ref, kind))
        if cache is None:
            raise ValueError("%s: input %r needs the gate cache to mount the external" % (root, ref))
        base = exttree(e, cache, extc)
        if is_stage_ext(e):   # a root's output: its tree hash is what the build ingested (stage_external)
            with open(os.path.join(extc or ext_dir(cache), "stage-ext", e["name"] + ".json")) as fh:
                etree = json.load(fh)["tree"]
        else:
            etree = e["tree_sha256"]
            if (cache, e["commit"]) not in _EXT_CHECKED:
                if cas.ingest(base, os.path.join(cache, "store")) != etree:
                    raise ValueError("external %r: tree_sha256 differs from the checkout" % kind)
                _EXT_CHECKED.add((cache, e["commit"]))
    elif isinstance(ref, dict) and set(ref) == {"stage", "path"} and all(isinstance(v, str) for v in ref.values()):
        return stage_output(root, ref, repo, cache)
    elif is_external_of(ref):   # an external another root declares: the same reader, on the owner's recipe and pin, after the one validator
        roots = load_admit(repo)
        errs = external_of_errors("%s: input" % root, ref, root, rec if rec is not None else load_recipe(repo, roots, root), roots, repo)
        if errs:
            raise ValueError(errs[0])
        return resolve_input(ref["external_of"], {"external": ref["name"], "path": ref["path"]}, repo, cache, load_recipe(repo, roots, ref["external_of"]), extc)
    else:
        raise ValueError("%s: input %r is neither a path, {external, path}, {external_of, name, path} nor {stage, path}" % (root, ref))
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
    return full, "%s:%s" % (etree, p)


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


def elf_dyn(path):
    """(PT_INTERP or None, [DT_NEEDED names], [DT_RPATH/DT_RUNPATH entries]) of an ELF64 file, read from its program headers and dynamic section (not ldd:
    nothing is executed); None when the file is not an ELF64."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"\x7fELF" or d[4] != 2:
        return None
    off, esz, n = struct.unpack_from("<Q", d, 32)[0], struct.unpack_from("<H", d, 54)[0], struct.unpack_from("<H", d, 56)[0]
    ph = [struct.unpack_from("<IIQQQQ", d, off + i * esz) for i in range(n)]
    cstr = lambda o: d[o:d.index(b"\0", o)].decode()   # noqa: E731
    interp = next((cstr(po) for t, _, po, _, _, _ in ph if t == 3), None)
    dyn = next((x for x in ph if x[0] == 2), None)
    if dyn is None:
        return interp, [], []
    ents = [struct.unpack_from("<qQ", d, dyn[2] + k) for k in range(0, dyn[5], 16)]
    strtab = next((v for t, v in ents if t == 5), None)
    if strtab is None:
        return interp, [], []
    fo = next((po + strtab - va for t, _, po, va, _, fsz in ph if t == 1 and va <= strtab < va + fsz), None)
    if fo is None:
        return interp, [], []
    return interp, [cstr(fo + v) for t, v in ents if t == 1], [x for t, v in ents if t in (15, 29) for x in cstr(fo + v).split(":") if x]


def exec_unresolved(path, tmp, debs, libdirs=()):
    """The DT_NEEDED names and the interpreter of the ELF at PATH that the step does not provide: the root layout of DEBS (deb_resolve over <tmp>/deb-<name>: the loader's default
    directories and the file's absolute RPATH/RUNPATH entries) or a declared lib dir (LIBDIRS: host directories mounted for the step, searched by file name; a library found
    there is read in turn, transitively). An entry that is not absolute ($ORIGIN) finds nothing, because the step mounts one file. A tool that is not an ELF64 (a script) has
    nothing to resolve. -> sorted names, each "interp:<path>" or the library name."""
    dyn = elf_dyn(path)
    if dyn is None:
        return []
    interp, need, rp = dyn
    defaults = ["/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu", "/lib64", "/usr/lib64", "/lib", "/usr/lib"]
    bad = ["interp:" + interp] if interp and not deb_resolve(interp, tmp, debs) else []
    seen, todo = set(), [(need, rp)]
    while todo:
        names, rpaths = todo.pop()
        for n in names:
            if n in seen:
                continue
            seen.add(n)
            dirs = [x for x in rpaths if x.startswith("/")] + defaults
            if any(deb_resolve(posixpath.join(x, n), tmp, debs) for x in dirs):
                continue
            f = next((os.path.join(d, n) for d in libdirs if os.path.isfile(os.path.join(d, n))), None)
            sub = elf_dyn(f) if f else None
            if f is None or sub is None:
                bad.append(n)
            else:
                todo.append((sub[1], sub[2]))
    return sorted(bad)


ROOTFS_MEMO = {}   # per process: {(store, ((deb, sha256), ...)): (inputs, links, {deb: unpacked tree})}
_ROOTFS_HOME = []


def rootfs_home():
    """The process's directory of unpacked deb trees (removed at exit)."""
    if not _ROOTFS_HOME:
        _ROOTFS_HOME.append(tempfile.mkdtemp(prefix="rootfs-"))
        atexit.register(shutil.rmtree, _ROOTFS_HOME[0], True)
    return _ROOTFS_HOME[0]


STAGE_MEMO = {}   # per process: {(store, checkout, repo_signature, root, step, path, step declaration): result of stage_output}; the stage's own key decides a hit, this only skips re-deriving it


def repo_signature(repo):
    """The content of the tracked tree of REPO as the build reads it: the index entries (mode, blob hash, path) and the sha256 of the bytes of every tracked file that differs from the index."""
    h = hashlib.sha256(subprocess.run(["git", "-C", repo, "ls-files", "-s", "-z", "--", TREE], capture_output=True, check=True).stdout)
    for f in subprocess.run(["git", "-C", repo, "ls-files", "-m", "-z", "--", TREE], capture_output=True, check=True).stdout.split(b"\0"):
        if f:
            with open(os.path.join(repo, f.decode("utf-8", "surrogateescape")), "rb") as fh:
                h.update(f + b"\0" + hashlib.sha256(fh.read()).digest())
    return h.hexdigest()


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
    memo_key = (os.path.realpath(store), os.path.realpath(repo), repo_signature(repo), sroot, name, ref["path"], json.dumps(ent, sort_keys=True))
    if memo_key in STAGE_MEMO and os.path.exists(STAGE_MEMO[memo_key][0]):
        return STAGE_MEMO[memo_key]
    j = Job(repo, sroot, store, 1, ext_dir(cache))
    ws = tempfile.mkdtemp(prefix="stageref-")
    try:
        j.externals(ws)
        j.generate(ws)
        j.run_all(ws, only=key if kind == "run" else None)
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
    STAGE_MEMO[memo_key] = (dest, "stage:%s:%s:%s:%s" % (sroot, name, ref["path"], h))
    return STAGE_MEMO[memo_key]


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


def build(repo, root, store, out, jobs=4, ext=None, aux=None, link=True, shard=None):
    """Build product ROOT of the checkout REPO into STORE; write OUT/objects.json and the files it names. EXT: bare cache dir of
    externals (default: the sibling of STORE, ext_of_store). SHARD: (i, N), build only the units of shard i (shard_of); root-level stages (tests, link, tables) do not run
    in a shard build, its objects.json says `shard`. -> the objects dict; raises RecipeError for an invalid recipe."""
    env_blocked(repo)   # BEFORE any stage: nothing below runs or stores on a machine that is not the declared one
    spec = root
    j = Job(repo, spec, store, jobs, ext or ext_of_store(store))
    j.shard = shard
    if shard:
        link = False
    j.aux = list(aux or [])   # AUX_FLAGS the caller asked for: extra outputs of the CC stage (in its key), never a change of the code generated
    root = j.root
    with open(os.path.join(repo, j.roots[root]["recipe"])) as f:
        full = json.load(f)
    if j.variant and j.variant not in variant_names(full, j.roots[root]):
        raise RecipeError(["root %s has no variant %r" % (root, j.variant)])
    errs = check(full, root, j.files, j.roots, load_pin(repo), repo)
    if errs:
        raise RecipeError(errs)
    units = units_of(j.rec, root, j.files, j.roots, repo)
    res = {"format": OBJ_FORMAT, "root": spec, "objects": [], "tests": [], "elf": None, "link_roots": [], "constants": None, "contract_names": None, "defined": [], "undefined": [], "plugins": [],
           "link_archives": [], "stage_keys": [], "status": "green"}
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
            objs = j.compile(ws, units + ([] if shard else sorted(test_units_of(j.rec))))
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
              loader_defs = None    # set once the link's loader is read; the red path below reads it whichever stage failed
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
              # The members of the image are chosen by the pinned ld: every candidate root is offered as an archive of its objects, so a root contributes exactly
              # the objects that define a symbol still undefined, transitively (archive semantics). The seed is the own objects' undefined symbols, the symbols the link
              # script references (`-u`, from the ld itself) and the declared entry. The set is ld's own map member list.
              cands = list(built_in)
              offered = [("%02d-%s" % (n + 1, d.replace("/", "__")), [(o["unit"], o["object"]) for o in built_in[d]["objects"]]) for n, d in enumerate(cands)]
              refs = sorted(set(j.script_refs(ws)) | ({lk["entry"]} if "entry" in lk else set()))

              def select(own):      # {label: {unit}} the objects ld pulls from the offered archives
                  out_tree = j.link(ws, own, "select.elf", "select", offer=offered, refs=refs)[0]
                  text = open(cas._obj(store, cas.manifest(out_tree, store)["entries"]["select.elf.map"]), errors="replace").read()
                  chosen = {}
                  for ar, mem in map_members(text):
                      m = re.fullmatch(r"/arch/(.+)\.a", ar)
                      if m and m.group(1) in j.offered:
                          chosen.setdefault(m.group(1), set()).add(j.offered[m.group(1)][mem])
                  return chosen
              members = select(groups)
              for label, d in zip((o[0] for o in offered), cands):
                  if label not in members:
                      continue
                  r = built_in[d]
                  kept = []     # the input root's objects stay beside this root's result (its own build directory is scratch)
                  for o in r["objects"]:
                      if o["unit"] not in members[label]:
                          continue
                      dest = os.path.join(out, "link_roots", d.replace("/", "__"), o["unit"] + ".o")
                      os.makedirs(os.path.dirname(dest), exist_ok=True)
                      shutil.copyfile(o["object"], dest)
                      kept.append({"unit": o["unit"], "object": os.path.abspath(dest), "sha256": o["sha256"]})
                  res["link_roots"].append({"root": d, "sha256": objects_digest(r), "objects": kept})
                  groups.append((label, [(o["unit"], o["object"]) for o in kept]))
                  defs[label] = [x for x in r["defined"] if x["object"] in members[label]]
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
                  d_ = symbols(dest, t["unit"])[0]
                  res["defined"] = sorted(res["defined"] + d_, key=lambda d: (d["symbol"], d["object"]))
                  again = select([(groups[0][0], groups[0][1] + [(t["unit"], os.path.abspath(dest))])])    # the own objects with the table's, the archives offered anew
                  if again != members:      # the table's own references pull in objects the table was not made over: its tool must see every object of the link
                      raise BuildRed({"stage": "table:%s:%d" % (root, ti), "exit": None, "stderr": ["the table object %s changes the objects ld pulls: %s differ from the %s the table was made over" % (
                          t["unit"], sorted(k for k in set(again) | set(members) if again.get(k) != members.get(k)), sorted(members))]})
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
    if shard:
        res["shard"] = "%d/%d" % shard
        res["shard_units"] = j.shard_units
    res.update(stage_keys=sorted(j.keys), stages={"run": j.run, "hit": j.hit}, units=all_units, pp_keys=dict(sorted(j.ppkeys.items())), du_keys=dict(sorted(j.dukeys.items())))
    with open(os.path.join(out, "objects.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    return res


def need_store(opt):
    """The --store of a store command: a required argument (no default from the environment)."""
    if not opt.get("--store"):
        raise RecipeError(["missing --store DIR"])
    return opt["--store"]


def norm_root(repo, spec):
    """The one normalization of a --root argument ROOT[@VARIANT]: an absolute path inside the checkout REPO becomes the relative root id; a path outside it is
    RecipeError "root must be inside the checkout: PATH"; a spec that is not an admitted root with a recipe is RecipeError (never an uncaught KeyError)."""
    if not isinstance(spec, str) or not spec:
        raise RecipeError(["missing --root ROOT"])
    path, sep, var = spec.partition("@")
    if os.path.isabs(path):
        top = os.path.realpath(repo)
        rel = os.path.relpath(os.path.realpath(path), top)
        if rel == ".." or rel.startswith("../") or rel == ".":
            raise RecipeError(["root must be inside the checkout: %s" % path])
        path = rel
    path = os.path.normpath(path)
    ent = load_admit(repo).get(path)
    if not (isinstance(ent, dict) and ent.get("recipe")):
        raise RecipeError(["root %s is not an admitted root with a recipe" % path])
    return path + sep + var


def declared_externals(repo, prefix=""):
    """[(ref, commit, tree_sha256)] sorted: the fetched externals (not a root's stage outputs) that the recipes of the admitted roots declare, whose `ref` starts
    with PREFIX. A rule over the admitted set and the recipes (the variants carry no externals of their own), no list; the same ref pinned two ways by two
    recipes is RecipeError (a ref has one pin). What a consumer of the declared content (the boot machine's game directory) is allowed to read."""
    out, bad = {}, []
    roots = load_admit(repo)
    for r in sorted(roots):
        if not roots[r].get("recipe"):
            continue
        with open(os.path.join(repo, roots[r]["recipe"])) as fh:
            rec = json.load(fh)
        for e in rec.get("external", []) if isinstance(rec, dict) else []:
            if is_stage_ext(e) or not str(e.get("ref", "")).startswith(prefix):
                continue
            pin = (e["commit"], e["tree_sha256"])
            if out.setdefault(e["ref"], pin) != pin:
                bad.append("external %s is pinned two ways (%s and %s)" % (e["ref"], out[e["ref"]], pin))
    if bad:
        raise RecipeError(bad)
    return sorted((ref, c, t) for ref, (c, t) in out.items())


def externals_cmd(repo, opt):
    """recipe externals --ref-prefix P [--ref R] [--fetch --store S --ext E --ws W --artifacts URL]: the declared externals with the ref prefix P, one `ref TAB commit TAB
    tree_sha256` line each; with --fetch, each is fetched by its commit and checked against its tree_sha256 by fetch_externals (the one mechanism of every
    external: BuildRed on a mismatch), and the line is `ref TAB checkout dir`. A branch head is never fetched."""
    rows = declared_externals(repo, opt.get("--ref-prefix", ""))
    if "--ref" in opt:   # exactly one ref (a prefix names Voices1 and Voices10 alike): its declared row, or an error
        rows = [r for r in rows if r[0] == opt["--ref"]]
        if not rows:
            raise RecipeError(["external %s is declared by no recipe" % opt["--ref"]])
    if "--fetch" not in opt:
        return ["%s\t%s\t%s" % r for r in rows]
    store, ext = need_store(opt), opt.get("--ext") or ext_of_store(need_store(opt))
    if "--artifacts" in opt:
        set_artifacts(ext, opt["--artifacts"])
    exts = [{"name": ref.replace("/", "_"), "commit": c, "tree_sha256": t, "at": "/ext/" + ref.replace("/", "_")} for ref, c, t in rows]
    with tempfile.TemporaryDirectory() as ws:
        got = fetch_externals(exts, store, ext, ws)
    return ["%s\t%s" % (ref, got[ref.replace("/", "_")]["host"]) for ref, _, _ in rows]


def main(argv):
    opt = dict(zip(argv[1::2], argv[2::2]))
    if argv[:1] == ["externals"]:   # a flag without a value (--fetch) is read before the pairs are
        opt = dict(zip([a for a in argv[1:] if a != "--fetch"][0::2], [a for a in argv[1:] if a != "--fetch"][1::2]), **({"--fetch": "1"} if "--fetch" in argv else {}))
    try:
        repo = "."
        if "--root" in opt:
            opt["--root"] = norm_root(repo, opt["--root"])
        if argv[:1] == ["store-merge"]:   # recipe store-merge SRC [--store DST]: --store DST is required
            dst = need_store(dict(zip(argv[2::2], argv[3::2])))
            print("store-merge: " + " ".join("%s %d" % kv for kv in sorted(store_merge(argv[1], dst).items())))
            return 0
        if argv[:1] in (["store-export"], ["store-import"]):   # recipe store-export KEY DIR [--store S] / store-import DIR KEY [--store S]: one cache entry under its key
            st = need_store(dict(zip(argv[3::2], argv[4::2])))
            if argv[0] == "store-export":
                print("store-export: %d files" % store_export(st, argv[1], argv[2]))
            else:
                print("store-import: " + " ".join("%s %d" % kv for kv in sorted(store_import(argv[1], st, argv[2]).items())))
            return 0
        if argv[:1] == ["timing"]:     # recipe timing --root R[@VARIANT] --store S [--ext E]: builds (every stage a hit when it was built before) and sums the sidecars
            o = dict(zip(argv[1::2], argv[2::2]), **{"--root": opt["--root"]})
            with tempfile.TemporaryDirectory() as td:
                res = build(repo, o["--root"], need_store(o), os.path.join(td, "out"), int(o.get("--jobs", 4)), o.get("--ext"))
            if res["status"] != "green":
                print(json.dumps(res["failure"], indent=1))
                return 2
            print(json.dumps(timing_report(o["--store"], res["stage_keys"]), indent=1, sort_keys=True))
            return 0
        if argv[:1] == ["externals"]:
            print("\n".join(externals_cmd(repo, opt)))
            return 0
        if argv[:1] == ["check"]:
            env_blocked(repo)   # the declared environment is judged here by the same function as before a build (the dependency closure of env.json included)
            roots = load_admit(repo)
            root, _ = split_spec(opt["--root"])   # the one place the spec is split: nothing below sees root@NAME
            rec = load_recipe(repo, roots, opt["--root"]) if _ else json.load(open(os.path.join(repo, roots[root]["recipe"])))   # a base is checked whole (raw: check() expands its profile): with its variants
            errs = check(rec, root, tracked(repo), roots, load_pin(repo), repo, _)
            print("\n".join(errs) or "recipe valid")
            return 2 if errs else 0
        unknown = set(opt) - {"--root", "--store", "--out", "--jobs", "--ext", "--stack-usage", "--shard", "--artifacts"}
        if "--artifacts" not in opt:
            raise RecipeError(["missing --artifacts URL (the artifacts repository of the externals)"])
        set_artifacts(opt.get("--ext") or ext_of_store(opt["--store"]), opt["--artifacts"])
        if unknown or opt.get("--stack-usage", "1") != "1":
            raise RecipeError(["unknown or bad option: %s" % ", ".join(sorted(unknown) or ["--stack-usage"])])
        res = build(repo, opt["--root"], opt["--store"], opt["--out"], int(opt.get("--jobs", 4)), opt.get("--ext"), AUX_FLAGS if "--stack-usage" in opt else None, shard=shard_spec(opt["--shard"]) if "--shard" in opt else None)
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
