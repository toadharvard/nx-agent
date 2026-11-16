"""plugsem - the declared facts of the guest compiler record that a tool of another root reads: the plugin set and its fixed arguments, the semantic flag sets,
the runtime's configure and cflags options (L1 CONTRACT; split out of recipe.py so that a generator tool (toolchain/msvcmodel/records.py) imports these facts
without the whole of recipe.py: its import closure is this file and verify/targets.py only).  recipe.py takes every name from here (one source).
Imports are qualified (`from verify import targets`; the declared import root mounts verify at /imp/verify, and a local run puts ports/fnv-nx on the path)."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

TREE = "ports/fnv-nx"
GUEST_PIN = TREE + "/verify/guest_compiler.json"   # the game's compiler: {"format", "external": {EXT_KEYS}, "cc", "cxx"} (RECIPE.md, "Guest roots")


class BuildRed(Exception):
    pass


def lifted_origin(origin):
    """cxx_origin of a lifted root: {"lifted_by": TOOL.py, "pack": EXTERNAL} (the lifted C++ is made from an external pack) or {"lifted_by": TOOL.py,
    "stage": "gen:<root>:<i>"} (made by a generated step of this root's own recipe, no pack): exactly one of pack and stage."""
    return isinstance(origin, dict) and (set(origin) == {"lifted_by", "pack"} or set(origin) == {"lifted_by", "stage"})




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


def origin_class(origin):
    """"lifted" iff the declared `cxx_origin` ORIGIN is a lifted declaration (lifted_origin: {"lifted_by", "pack"} or {"lifted_by", "stage"}), else "native" (also for no origin): the one
    place the origin's class is read (the class name "lifted" itself is accepted, for the CLI); plugin_args and semantics_set are functions of it."""
    return "lifted" if origin == "lifted" or lifted_origin(origin) else "native"


def semantics_set(origin):
    """The semantic flag set (before the per-language drop of semantics_for) of a root with `cxx_origin` ORIGIN: SEMANTICS_LIFTED for a lifted
    root, SEMANTICS_FLAGS for every other."""
    return semantic_lifted() if origin_class(origin) == "lifted" else semantic_flags()


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
from verify import targets as _targets  # noqa: E402  (the declared targets: verify/targets.json)


def semantic_flags(datamodel=None):
    """The semantic set of every guest root (native, decomp, lifted), evaluated when asked, never at import: its data-model run reads DATAMODEL.json, which a stage that only imports this file
    does not have at the repository path. DATAMODEL: the table's path when the caller holds it elsewhere (the stage's own input); else the record's reference in this checkout."""
    return tuple(_targets.semantic_prefix(_targets.load(), None, datamodel)) + tuple(_sem["common"])


def semantic_lifted(datamodel=None):
    """A root whose cxx_origin is a lifted declaration: the common set plus what the lifter's code is built under."""
    return semantic_flags(datamodel) + tuple(_sem["lifted_extra"])


def __getattr__(name):   # PEP 562: SEMANTICS_FLAGS / SEMANTICS_LIFTED are the sets over this checkout's DATAMODEL, computed on first use
    if name == "SEMANTICS_FLAGS":
        return semantic_flags()
    if name == "SEMANTICS_LIFTED":
        return semantic_lifted()
    raise AttributeError(name)


SDD_DEFINE = "-DFNVNX_SDD=1"   # per root: the plugin fnvnx_sdd needs it (and -fno-devirtualize, which is in the semantic set of a guest root)
SDD_FLAGS = (SDD_DEFINE, "-fno-devirtualize")   # what a NATIVE C++ root of a host target holds itself


def runtime_cflags(pin):
    """The flags the guest runtime (newlib, libstdc++) is built with beyond the semantic set: the `runtime_cflags` of the one guest compiler record.
    They apply to every runtime translation unit: C, C++ and assembly (.S) alike. The runtime build reads them here (guest_flags.py --runtime); no build script spells them."""
    v = pin.get("runtime_cflags") if isinstance(pin, dict) else None
    if not isinstance(v, list) or not all(isinstance(x, str) and x.startswith("-") for x in v):
        raise BuildRed("%s: runtime_cflags must be a list of compiler flags" % GUEST_PIN)
    return list(v)


def runtime_configure(pin):
    """The gcc configure options of the guest compiler build (build.sh of toolchain/msvcmodel): the `runtime_configure` of the one guest compiler record.
    The build reads them here; no build script spells them."""
    v = pin.get("runtime_configure") if isinstance(pin, dict) else None
    if not isinstance(v, list) or not all(isinstance(x, str) and x.startswith("--") for x in v):
        raise BuildRed("%s: runtime_configure must be a list of configure options" % GUEST_PIN)
    cite = pin.get("runtime_configure_cite")
    missing = [o for o in v if not isinstance(cite, dict) or not isinstance(cite.get(o), str) or not cite[o].strip()]
    if missing:
        raise BuildRed("%s: runtime_configure options without a cite in runtime_configure_cite: %s" % (GUEST_PIN, ", ".join(missing)))
    return list(v)


def runtime_configure_patch(pin):
    """The patch of the runtime subdirs' generated configure scripts (rule CFG-LINK): the `runtime_configure_patch` of the one guest compiler record.
    The build reads the script, the subdirs and its two fact sources here; no build script spells them."""
    v = pin.get("runtime_configure_patch") if isinstance(pin, dict) else None
    keys = ("script", "subdirs", "protocol_source", "guest_script", "cite")
    if not isinstance(v, dict) or any(k not in v for k in keys) or not isinstance(v["subdirs"], list) or not all(isinstance(v[k], str) and v[k].strip() for k in keys if k != "subdirs"):
        raise BuildRed("%s: runtime_configure_patch needs %s (cite non-empty)" % (GUEST_PIN, ", ".join(keys)))
    return dict(v)
