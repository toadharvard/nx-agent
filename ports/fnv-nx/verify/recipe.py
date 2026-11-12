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
ENV_JSON = TREE + "/verify/env.json"   # the declared environment of the checkout (l1.py checks the same file)
KEYS = {"format", "compiler", "flags", "include", "units", "generated", "os_layer", "game", "constants", "contract_names", "link", "external",
        "plugins", "plugin_cxx", "variants", "outputs", "uses", "sources"}
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
PLUGIN_ARGS["fnvnx_ftoi"] = {"mode": "native"}
# fnvnx_ftoi is one plugin with two modes, chosen by the root's cxx_origin (no free choice): native C++ takes the MSVC rewrite, lifted C++ has
# no rewrite and every float -> integer conversion outside an fnvnx_f2i function is an error. Both write their report (gate fpexact) to
# PLUGIN_OUT of the CC stage's own /out; the recipe passes it, a build.json does not declare it.
LIFTED_ARGS = {"fnvnx_ftoi": {"mode": "lifted"}}
PLUGIN_OUT = {"fnvnx_ftoi": "/out/unit.f2i.json"}
PLUGIN_ARGS["fnvnx_order"] = {"args": "rtl", "object": "msvc", "operator": "rtl", "assign": "msvc"}
# The semantic flags of a guest root (data model, ABI, target, FP, aliasing, overflow, language model, calling convention): ONE ordered set, the
# guest compiler pin's "flags"."semantics" (guest_compiler.json, next to this file). The RECIPE appends it to every guest compile stage, C, C++ and
# assembler alike, minus what the pinned driver itself says does not apply to that language (semantics_for); a guest build.json carries none of them.
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "guest_compiler.json")) as _f:
    SEMANTICS_FLAGS = tuple(json.load(_f)["flags"]["semantics"])
SDD_DEFINE = "-DFNVNX_SDD=1"   # per root: the plugin fnvnx_sdd needs it (and -fno-devirtualize, which is in the semantic set of a guest root)
SDD_FLAGS = (SDD_DEFINE, "-fno-devirtualize")   # what a NATIVE C++ root of a host target holds itself
LANG_X = {"c": "c", "cxx": "c++", "asm": "assembler-with-cpp"}   # the driver's -x language of each kind of unit
# What a guest build.json may carry (per-root classes); every other -f/-m/... token comes from flags.semantics and is refused here. Warnings and
# diagnostics, defines, the language standard, the optimisation level (-O0..-O3, -Os, -Og: not -Ofast, which switches FP semantics), debug info,
# section splitting. -Wa, -Wl, -Wp are driver pass-throughs, not warnings.
# The one variant that changes the compiler's own behaviour but no code: a garbage collection after every function (the plugins' GC-rooting stress).
# Its flags are the root's flags plus exactly these parameters, so its objects are the same bytes as the root's (a test asserts that).
GCSTRESS_VARIANT = "gcstress"
GCSTRESS_FLAGS = ["--param", "ggc-min-expand=0", "--param", "ggc-min-heapsize=0"]
PER_ROOT = re.compile(r"-W(?![alp],)[^\s]*|-D[^\s]+|-std=[^\s]+|-O[0-3sg]|-g[^\s]*|-ffunction-sections|-fdata-sections")
# The options of every link, fixed by the rule (RECIPE.md "Link"): the specs of the gcc driver for the ILP32 PIE multilib, no gc-sections, no id.
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


def guest_errors(rec, root, roots, pin, variant="", base=None):
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
    fl = rec.get("flags") if isinstance(rec.get("flags"), dict) else {}
    if variant == GCSTRESS_VARIANT:   # the variant is the root's flags plus GCSTRESS_FLAGS: nothing else may differ; the per-root rules below then see the root's flags
        bf = base.get("flags") if isinstance(base, dict) and isinstance(base.get("flags"), dict) else {}
        bad += ["variant %s: flags.%s must be the root's flags followed by %s" % (variant, k, " ".join(GCSTRESS_FLAGS)) for k in ("c", "cxx", "asm")
                if not (isinstance(fl.get(k), list) and fl[k] == (bf.get(k) if isinstance(bf.get(k), list) else []) + GCSTRESS_FLAGS)]
        fl = {k: (v[:-len(GCSTRESS_FLAGS)] if isinstance(v, list) and v[-len(GCSTRESS_FLAGS):] == GCSTRESS_FLAGS else v) for k, v in fl.items()}
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


def root_edges(rec):
    """The roots a recipe depends on besides its own files: `link.roots` (objects) and `uses` (outputs), variants cut off."""
    lk = rec.get("link") if isinstance(rec, dict) else None
    ds = [split_spec(d)[0] for d in (lk.get("roots", []) if isinstance(lk, dict) and isinstance(lk.get("roots"), list) else []) if isinstance(d, str)]
    us = rec.get("uses") if isinstance(rec, dict) else None
    return ds + [u["root"] for u in (us if isinstance(us, list) else []) if isinstance(u, dict) and isinstance(u.get("root"), str)]


def link_roots_cycle(root, roots, repo, seen=()):
    """The cycle through ROOT in the graph of `link.roots` and `uses` between roots, as a list of roots, or None."""
    if root in seen:
        return list(seen[seen.index(root):]) + [root]
    ent = roots.get(root)
    try:
        rec = json.load(open(os.path.join(repo, ent["recipe"]))) if ent and ent.get("recipe") else None
    except (OSError, ValueError):
        rec = None
    for d in root_edges(rec):
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


def uses_errors(rec, root, roots, repo):
    """Problems of `outputs` ({name: a generated output of this root}) and `uses` ([{root, output}]: an output another admitted root declares)."""
    err, gen = [], generated_outputs(rec)
    outs = rec.get("outputs")
    if outs is not None and not (isinstance(outs, dict) and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", n) and isinstance(p, str) and p in gen
                                                                 for n, p in outs.items())):
        err.append("outputs must be {NAME: a path in generated[].outputs of this root}")
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
                decl = json.load(open(os.path.join(repo, ent["recipe"]))).get("outputs")
            except (OSError, ValueError, AttributeError):
                err.append("uses: the recipe of %s is unreadable" % r)
                continue
            if not (isinstance(decl, dict) and n in decl):
                err.append("uses: root %s declares no output %r (build.json `outputs`)" % (r, n))
    cyc = link_roots_cycle(root, roots, repo)
    if cyc:
        err.append("uses/link.roots: cycle %s" % " -> ".join(cyc))
    return err


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
    if cyc and any(d in cyc for d in [split_spec(x)[0] for x in deps]):
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


def check(rec, root, files, roots, pin=None, repo=".", variant="", base=None):
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
            err += ["variant %s: %s" % (n, e) for e in check(effective(rec, n), root, files, roots, pin, repo, n, rec)]
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
        for p in ins if isinstance(ins, list) else []:
            if isinstance(p, str) and not under(p, root) and not any(under(p, r) and roots[r]["class"] == "tool" for r in roots):
                err.append("generated[%d].inputs: %s is a file of another root: take it through `uses` (the producer's declared output), not by path" % (i, p))
        if not (isinstance(outs, list) and outs and all(rel_ok(o) for o in outs)):
            err.append("generated[%d].outputs must be a non-empty list of root-relative paths" % i)
            continue
        for o in outs:
            if o in mine:
                err.append("generated output %s is also a tracked file" % o)
            if o in seen:
                err.append("generated output %s is produced twice" % o)
            seen.add(o)
    err += uses_errors(rec, root, roots, repo)
    sr = rec.get("sources")
    if sr is not None and not (isinstance(sr, str) and sr in generated_outputs(rec)):
        err.append("sources must name a path in generated[].outputs of this root (a file of <external>:<path> lines)")
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
    err += guest_errors(rec, root, roots, pin, variant, base)
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
    # RULE (orchestrator, 2026-11-12): every guest CC stage, C and C++ alike and whatever the origin of the code, loads the FULL plugin set of
    # PLUGIN_SOURCES with its fixed arguments in the fixed order. No language declarations, no exception list. The arguments are fixed by the
    # origin alone: a lifted root takes LIFTED_ARGS where it names a plugin (fnvnx_ftoi mode lifted), every other root PLUGIN_ARGS.
    if ent.get("target") == "guest" and any(u.endswith(CC_EXT + CXX_EXT) for u in units) and not (origin == "native" and cxx):
        want = dict(PLUGIN_ARGS, **LIFTED_ARGS) if isinstance(origin, dict) and set(origin) == {"lifted_by", "pack"} else PLUGIN_ARGS
        for n in sorted(PLUGIN_SOURCES):
            if n not in loaded:
                err.append("guest root %s does not load plugin %s (every guest C and C++ unit is compiled with the full plugin set)" % (root, n))
            elif loaded[n].get("args") != want[n]:
                err.append("plugin %s: arguments must be %s, are %s" % (n, want[n], loaded[n].get("args")))
        if list(loaded) != [n for n in PLUGIN_SOURCES if n in loaded]:
            err.append("plugins must be loaded in the order %s" % list(PLUGIN_SOURCES))
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
        # fnvnx_sdd needs the define and -fno-devirtualize; a guest root's -fno-devirtualize comes from flags.semantics, its define is per root
        err += ["native C++ root %s: flags.cxx lacks %s" % (root, f) for f in (SDD_FLAGS if ent.get("target") != "guest" else (SDD_DEFINE,)) if f not in fl]
    elif isinstance(origin, dict) and set(origin) == {"lifted_by", "pack"}:
        tool, pack = origin["lifted_by"], origin["pack"]
        for n, a in LIFTED_ARGS.items():   # lifted C++ does its conversions in helpers: the plugin proves it (and only it is required)
            if n not in loaded:
                err.append("lifted C++ root %s does not load plugin %s (mode lifted: every conversion outside an fnvnx_f2i function is an error)" % (root, n))
            elif loaded[n].get("args") != a:
                err.append("plugin %s: arguments of a lifted root must be %s, are %s" % (n, a, loaded[n].get("args")))
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
                os.replace(tmp, bare)
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
            try:
                os.rename(tmpco, co)
            except OSError:      # another process made it first: the same commit, the same tree
                shutil.rmtree(tmpco, ignore_errors=True)
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


def semantics_for(driver, kind, flags=SEMANTICS_FLAGS):
    """The semantic flags the pinned DRIVER (host path) applies to a unit of KIND ("c", "cxx", "asm"), in their order: FLAGS minus every flag that
    the driver itself says does not apply to that language (its diagnostic "command-line option 'X' is valid for C++/ObjC++ but not for C", read
    from one run on an empty input in the driver's -x language). Nothing is decided by a name here; a flag the driver does not know is an error
    (an unrecognised option makes the run fail: BuildRed), never dropped."""
    r = subprocess.run([driver, "-x", LANG_X[kind], "-E", "-o", "/dev/null"] + list(flags) + ["/dev/null"], capture_output=True, text=True,
                       env=dict(os.environ, LC_ALL="C", LANGUAGE="C"))
    if r.returncode:
        raise BuildRed({"stage": "semantics:" + kind, "exit": r.returncode, "stderr": r.stderr.splitlines()[-20:]})
    drop = set(re.findall(r"command-line option '([^']+)' is valid for [^\n]* but not for", r.stderr))
    return [f for f in flags if f not in drop]


class Job:
    def __init__(self, repo, spec, store, jobs, ext):
        root, self.variant = split_spec(spec)
        self.repo, self.root, self.store, self.jobs, self.extcache = repo, root, store, jobs, ext
        self.roots, self.files = load_admit(repo), tracked(repo)
        self.rec = load_recipe(repo, self.roots, spec)
        self.keys, self.run, self.hit, self.ext, self.ppkeys, self.built = set(), 0, 0, {}, {}, []
        self.dukeys = {}   # {unit: key of its macro-fact stage (-dU, unitstage.DU_TAIL)}
        self.red, self.blocked, self.failed = [], {}, []   # KEEP GOING: red stages (failure dicts) and {unit: stage it is blocked by}; nothing is masked (see build)
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

    def generate(self, ws):
        if not self.rec.get("generated"):
            return
        _, py = self.cached_rt("python", lambda: runtime.build(self.store, sys.executable, []))
        xin = [{"at": e["at"], "tree": e["tree"]} for e in self.ext.values()]
        for i, g in enumerate(self.rec["generated"]):
            try:
                self.generate_one(ws, i, g, py, xin)
            except BuildRed as r:   # a red generator does not stop the others: its outputs are absent, and what needs them is blocked by it
                f = dict(r.args[0])
                self.red.append(f)
                for o in g["outputs"]:
                    self.blocked[o] = f["stage"]

    def generate_one(self, ws, i, g, py, xin):
        owner = next(r for r in self.roots if under(g["tool"], r))
        tmp = tempfile.mkdtemp(dir=ws)
        ins = [{"at": "/tool", "tree": self.ingest_files(tmp, "tool", [f for f in self.files if under(f, owner)], owner)}] + xin + self.use_inputs(ws)
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
            flags = ["-fplugin=/plug/%s/%s.so" % (n, n)] + ["-fplugin-arg-%s-%s%s" % (n, k, "=" + v if v else "") for k, v in p["args"].items()] + (
                ["-fplugin-arg-%s-out=%s" % (n, PLUGIN_OUT[n])] if n in PLUGIN_OUT else [])
            out.append((n, res, flags))
        return out

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
        rtp, _ = self.toolrt(used)
        guest = self.roots.get(self.root, {}).get("target") == "guest"
        pk = ("c", "cxx") if guest else ("cxx",)   # guest: the plugins are loaded by the C and the C++ front end alike
        try:
            self.use_inputs(ws)
        except BuildRed as r:
            self.red.append(dict(r.args[0]))
            self.blocked.update({u: r.args[0]["stage"] for u in units})
            return {}
        try:
            self.built = self.plugins(ws) if any(k in pk for k in kinds.values()) else []
        except BuildRed as r:
            self.red.append(dict(r.args[0]))
            self.blocked.update({u: r.args[0]["stage"] for u, k in kinds.items() if k in pk})
            units = [u for u in units if u not in self.blocked]
            kinds = {u: k for u, k in kinds.items() if u in units}
            self.built = []
        builds = {}
        sos = {}
        for n, t, _ in self.built:   # the plugins each front end can load (plugin_languages): the stage loads exactly those, the gate reads the same fact
            sos[n] = cas._obj(self.store, cas.manifest(t, self.store)["entries"][n + ".so"])
        loadable = plugin_languages(self.hostpath(drv["c"]), self.hostpath(drv["cxx"]), sos) if sos else {"c": [], "cxx": []}
        # RULE: the CC stage (-fpreprocessed, input unit.ii) mounts, and so keys on, only the compiler (its external, if it is one), the
        # declared plugin .so files and unit.ii. Any other external is not mounted there: what is not mounted cannot be read, so the object
        # does not depend on it by construction (a new commit of such an external changes the PP keys, not the CC keys).
        cc = self.rec["compiler"].get("external")
        drop = {e["at"] for n, e in self.ext.items() if n != cc}
        for k in sorted(set(kinds.values())):
            fp = os.path.join(ws, "flags-%s.json" % k)
            sem = semantics_for(self.hostpath(drv["c" if k == "asm" else k]), k) if guest else []   # the recipe's flags: semantics first, then the root's own
            json.dump({"argv": sem + rec["flags"][k]}, open(fp, "w"))
            builds[k] = unitstage.Build(argparse.Namespace(store=self.store, cold=False, cxx=drv["c" if k == "asm" else k], toolrt=rtp, flags_file=fp,
                                                           src_root=os.path.join(ws, "root") + "@/src", include=self.includes(ws)))
            builds[k].cc_drop = drop | {i["at"] for i in self.use_inputs(ws)}
            builds[k].rt = dict(builds[k].rt, inputs=builds[k].rt["inputs"] + self.use_inputs(ws))
            builds[k].variant = self.variant
            if k in pk:   # plugins are loaded by the CC stage only, never by preprocessing (guest roots: C and C++ units; host roots: C++ only)
                ok = loadable[k]
                builds[k].cc_flags = [f for n, _, fl in self.built if n in ok for f in fl]
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
        try:
            os.rename(tmp, dest)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
    return dest


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


def build(repo, root, store, out, jobs=4, ext=None):
    """Build product ROOT of the checkout REPO into STORE; write OUT/objects.json and the files it names. EXT: bare cache dir of
    externals (default STORE/external). -> the objects dict; raises RecipeError for an invalid recipe."""
    env_blocked(repo)   # BEFORE any stage: nothing below runs or stores on a machine that is not the declared one
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
                units = sorted(set(units) | set(j.generated_sources(ws)))
            except BuildRed as r:   # the list of externally sourced units is itself not produced
                j.red.append(dict(r.args[0]))
            objs = j.compile(ws, units)
        except BuildRed as r:   # an external is not available: no stage of the root has its inputs
            j.red.append(dict(r.args[0]))
            j.blocked.update({u: r.args[0]["stage"] for u in units})
        all_units, units = units, [u for u in units if u in objs]
        res["objects"], res["failed_units"] = [], []
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
            row = {"unit": u, "object": os.path.abspath(dest), "sha256": ent["h"], "os_layer": u in j.rec.get("os_layer", [])}
            rep = cas.manifest(objs[u], store)["entries"].get("unit.f2i.json")   # the fnvnx_ftoi report of the unit, when the plugin is loaded
            if rep:
                fd = os.path.join(out, "obj", u + ".f2i.json")
                shutil.copyfile(cas._obj(store, rep), fd)
                row["f2i"] = os.path.abspath(fd)
            res["objects"].append(row)
        res.update(defined=sorted(res["defined"], key=lambda d: (d["symbol"], d["object"])), undefined=sorted(und))
        if j.rec.get("link") and (j.red or j.blocked or j.failed):
            j.blocked[j.rec["link"]["output"]] = (j.red or j.failed)[0]["stage"]
        elif j.rec.get("link"):
          try:
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
          except BuildRed as r:
            j.red.append(dict(r.args[0]))
            res["failed_units"].append({"unit": j.rec["link"]["output"], "stage": r.args[0]["stage"], "exit": r.args[0].get("exit"), "stderr": list(r.args[0]["stderr"])[-20:]})
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
            errs = check(load_recipe(repo, roots, opt["--root"]), opt["--root"], tracked(repo), roots, load_pin(repo), repo)
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
