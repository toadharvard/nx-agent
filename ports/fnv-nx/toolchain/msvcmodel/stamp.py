#!/usr/bin/env python3
"""The build key of the game's compiler (toolchain/msvcmodel/build.sh): ONE computation, used by build.sh (which records it in the compiler tree's
MANIFEST.json as compiler.build_key) and by verify/recipe.py (which refuses a pinned compiler tree whose build_key is not the key of the checkout it builds).
The build has two stages and two keys, each over the CONTENT of its own inputs (a tree-relative name for each; a path outside the tree does not enter it), so the
same files give the same keys in any checkout. K1 is stage 1 (the compiler: build.sh, msvcmodel.patch and the records the compiler configure reads); K2, the build key
of the MANIFEST (stamp() returns it), is over the stage-1 TREE (its tree_sha256: stage 1 is stored as its own tree under a K1-named ref, and stage 2 starts from that tree)
and stage 2 (stage2.sh, the runtime patches, cfg_link.py, the plugin files and argv, the plugin stage's own inputs and the records the runtime reads). Each
input is in exactly one stage; the lists are STAGE1_INPUTS / STAGE2_INPUTS, and build.sh and stage2.sh take the keys from here. The inputs are what the two scripts READ:
  - STAGE1_INPUTS and STAGE2_INPUTS (INPUTS is their concatenation), by content;
  - every plugin file the runtime is compiled with (plugin_inputs: the recipe's PLUGIN_SOURCES and PLUGIN_INPUTS, read from the recipe, no second list), by content;
  - the build environment (env_pin): env.json's debs.packages (version and sha256 of every pinned package: the host compiler and the binutils package the build copies in,
    the autotools' host compiler, make, zlib) with the OS and the architecture, in K1; K2 is over the stage-1 tree, which was built in it;
  - the plugin arguments per language (recipe.plugin_args, in the recipe's PLUGIN_SOURCES order, which is the load order) and the recipe's plugin stage (PLUGIN_BUILD_FLAGS,
    unitstage.ENV, verify/unitstage.py), in K2: stage 2 gets its -fplugin flags from plugin_flags() here, never from a caller's file;
  - the VALUES build.sh takes from records, through the same functions it calls (build_values): the declared target (targets.json guest_platform
    triplet/abi/code and console_cpu arch/features, which give the multilib selector, --with-arch and --with-abi), recipe.runtime_configure and
    recipe.runtime_cflags of guest_compiler.json, and recipe.semantics_set(None) (the semantic flag set: targets, the DATAMODEL rows' flags and
    flags.semantics.common). The record's pin fields (external, runtime) are the OUTPUT of this build and do not enter the key.
    python3 -I stamp.py [--override RELPATH=FILE]... [--verify DIR] [--targets FILE] [--stage1-tree SHA256]   -> the 16-hex key K2 on stdout   (defaults: this checkout's)
    python3 -I stamp.py --stages   -> 'K1 K2' (K2 needs --stage1-tree);  --files   -> 'RELPATH PATH SHA256' of every stage-2 input;  --plugin-flags c|cxx --plugins-json FILE   -> the -fplugin flags"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.normpath(os.path.join(HERE, "..", ".."))   # ports/fnv-nx

# the inputs of the compiler build, as paths relative to the tree (ports/fnv-nx): the one list
STAGE1_INPUTS = ("toolchain/msvcmodel/msvcmodel.patch", "toolchain/msvcmodel/build.sh")
STAGE2_INPUTS = ("toolchain/msvcmodel/stage2.sh", "toolchain/gcc/x19-x27.patch", "toolchain/gcc/libstdcxx-nosized.patch", "toolchain/gcc/newlib-guest.patch",
                 "toolchain/gcc/cfg_link.py", "verify/unitstage.py")
INPUTS = STAGE1_INPUTS + STAGE2_INPUTS


def env_pin(verify=None, root=None):
    """The declared build environment, read from verify/env.json (never typed): its OS and architecture and EVERY pinned package (debs.packages: name -> version and
    sha256 of the deb). Rule: both stages run in the environment env.json declares, so what a step reads from it (the host compiler of the GCC and autotools builds,
    make, zlib, the binutils package whose files are copied into the compiler tree and the ld scripts of its emulation) is a function of this pin; the whole pin is in
    the key, so no step's read can be missed. build.sh checks that the installed version of every package it copies from is the pinned one (--expect-package)."""
    e = json.load(open(os.path.join(verify or os.path.join(root or TREE, "verify"), "env.json")))
    return {"os": e["os"], "arch": e["arch"], "packages": {n: [p["version"], p["sha256"]] for n, p in sorted(e["debs"]["packages"].items())}}


def package_version(name, verify=None, root=None):
    """the version env.json pins for package NAME (KeyError: not pinned)."""
    e = json.load(open(os.path.join(verify or os.path.join(root or TREE, "verify"), "env.json")))
    return e["debs"]["packages"][name]["version"]


def build_values(root=None, verify=None, targets=None, recipe=None):
    """-> (stage-1 values, stage-2 values) {name: value}: what build.sh and stage2.sh read from the records, computed by the functions build.sh itself calls (RECIPE: the loaded verify/recipe.py module, or
    the one under VERIFY, default ROOT/verify; TARGETS: build.sh's targets.json, default VERIFY/targets.json)."""
    verify = verify or os.path.join(root or TREE, "verify")
    if recipe is None:
        sys.path.insert(0, verify)
        import recipe
    t = json.load(open(targets or os.path.join(verify, "targets.json")))
    pin = json.load(open(os.path.join(verify, "guest_compiler.json")))
    v1 = {"triplet": t["guest_platform"]["triplet"], "abi": t["guest_platform"]["abi"], "code": t["guest_platform"]["code"],
          "cpu_arch": t["console_cpu"]["arch"], "cpu_features": t["console_cpu"]["features"], "runtime_configure": recipe.runtime_configure(pin),   # stage 1: the compiler's configure
          "env": env_pin(verify)}
    v2 = {"runtime_configure_patch": recipe.runtime_configure_patch(pin), "runtime_cflags": recipe.runtime_cflags(pin),
          "semantics": list(recipe.semantics_set(None)),   # stage 2: what only the target libraries' builds read
          "env": env_pin(verify),   # the host programs of stage 2 (the autotools' host compiler, make, patch) are the pinned ones
          "plugins": {"order": list(recipe.PLUGIN_SOURCES), "args": recipe.plugin_args("native"), "out": sorted(recipe.PLUGIN_OUT),   # the runtime is native C++ compiled with the native arguments
                      "argv": {l: plugin_argv(l, recipe) for l in LANGS}, "build": {"flags": list(recipe.PLUGIN_BUILD_FLAGS), "env": dict(unitstage_env(verify))}}}
    return v1, v2


LANGS = ("c", "cxx")


def plugin_argv(lang, recipe):
    """[-fplugin-arg-<plugin>-<key>[=value]] of every plugin in the recipe's PLUGIN_SOURCES order (= the load order), for target libraries of language LANG: the
    arguments recipe.plugin_args("native") fixes, nothing else (PLUGIN_OUT is the report path of a recipe stage's /out; a runtime build has none). The load order
    and the arguments are the same for C and C++ today; they are listed per language so that a language rule in the recipe would enter the key."""
    want = recipe.plugin_args("native")
    return ["-fplugin-arg-%s-%s%s" % (n, k, "=" + v if v else "") for n in recipe.PLUGIN_SOURCES for k, v in want[n].items()]


def unitstage_env(verify):
    """unitstage.ENV, read from verify/unitstage.py by value (the environment every recipe stage of a plugin runs in)."""
    sys.path.insert(0, verify)
    import unitstage
    return unitstage.ENV


def plugin_flags(lang, plugins_json, recipe=None):
    """-> the -fplugin flags (one string) of every target-library compile of language LANG: `-fplugin=<so path>` in the load order, then each plugin's argv. PLUGINS_JSON is
    the recipe's objects.json of the plugin stages ({"plugins": [{name, sha256, stage_key, path}]}: path is the .so the caller extracted from the plugin stage's /out); every
    plugin of PLUGIN_SOURCES must be in it, and no other. The argv is plugin_argv, the one source."""
    import hashlib as _h
    if recipe is None:
        sys.path.insert(0, os.path.join(TREE, "verify"))
        import recipe
    objs = {p["name"]: p for p in json.load(open(plugins_json))["plugins"]}
    if set(objs) != set(recipe.PLUGIN_SOURCES):
        raise SystemExit("stamp.py: the plugin list %s is not the recipe's PLUGIN_SOURCES %s" % (sorted(objs), sorted(recipe.PLUGIN_SOURCES)))
    flags = []
    for n in recipe.PLUGIN_SOURCES:
        p = objs[n]
        if _h.sha256(open(p["path"], "rb").read()).hexdigest() != p["sha256"]:
            raise SystemExit("stamp.py: %s is not the plugin whose sha256 the list records" % p["path"])
        flags.append("-fplugin=%s" % p["path"])
    return " ".join(flags + plugin_argv(lang, recipe))


def plugin_inputs(root=None, recipe=None):
    """-> [tree-relative path]: every file the plugins are built from, read from the recipe's own declaration (PLUGIN_SOURCES and PLUGIN_INPUTS, the one list;
    the runtime is compiled with all of the plugins, so a change to any of these is another plugin ABI in the pinned runtime)."""
    if recipe is None:
        sys.path.insert(0, os.path.join(root or TREE, "verify"))
        import recipe
    files = set(recipe.PLUGIN_SOURCES.values())
    for v in recipe.PLUGIN_INPUTS.values():
        files.update(v)
    return sorted(os.path.relpath(f, recipe.TREE) for f in files)


def stage2_files(root=None, overrides=None):
    """-> {tree-relative name: path}: every file stage 2 applies or runs (STAGE2_INPUTS), at ROOT or at the path OVERRIDES names for that tree-relative name (build.sh's
    MSVC_PATCH / PATCH_DIR / stage2.sh overrides: one generic mapping, no per-name rule). stage2.sh asks for a file by its tree-relative name through --files; a name
    that is not listed here is a refusal, so stage2.sh applies nothing this key does not cover."""
    root = root or TREE
    ov = overrides or {}
    bad = sorted(set(ov) - set(INPUTS))
    if bad:
        raise SystemExit("stamp.py: --override names %s, which is no input of the build" % bad)
    return {i: ov.get(i, os.path.join(root, i)) for i in STAGE2_INPUTS}


def stages(root=None, overrides=None, verify=None, targets=None, recipe=None, stage1_tree=None):
    """-> (K1, K2), 16 hex digits each, over the inputs under ROOT (a tree: ports/fnv-nx of a checkout); OVERRIDES {tree-relative input name: file} replaces files of the
    checkout (build.sh's MSVC_PATCH and PATCH_DIR); VERIFY, TARGETS and RECIPE as in build_values. K1 covers stage 1: msvcmodel.patch, build.sh, the records the
    compiler configure reads and env_pin. K2 covers stage 2 over STAGE1_TREE (the tree_sha256 of the stage-1 tree: stage 1 is stored as its own tree and stage 2
    starts from it; None gives K2 = None, K1 alone is wanted) and everything stage 2 reads (see the module docstring)."""
    root = root or TREE
    ov = overrides or {}
    h = lambda p, n: "%s  %s\n" % (hashlib.sha256(open(p, "rb").read()).hexdigest(), n)
    v1, v2 = build_values(root, verify, targets, recipe)
    lines1 = "".join(h(ov.get(n, os.path.join(root, n)), n) for n in STAGE1_INPUTS) + "values  " + json.dumps(v1, sort_keys=True, separators=(",", ":")) + "\n"
    k1 = hashlib.sha256(lines1.encode()).hexdigest()[:16]
    if stage1_tree is None:
        return k1, None
    lines2 = "stage1  %s\n" % stage1_tree + "".join(h(p, n) for n, p in sorted(stage2_files(root, ov).items()))
    lines2 += "".join(h(os.path.join(root, n), n) for n in plugin_inputs(root, recipe))
    lines2 += "values  " + json.dumps(v2, sort_keys=True, separators=(",", ":")) + "\n"
    return k1, hashlib.sha256(lines2.encode()).hexdigest()[:16]


def stamp(root=None, overrides=None, verify=None, targets=None, recipe=None, stage1_tree=None):
    """-> K2, the build key of the whole compiler tree (MANIFEST compiler.build_key); see stages()."""
    return stages(root, overrides, verify, targets, recipe, stage1_tree)[1]


def patch_sha256(root=None, patch=None):
    """the full sha256 of msvcmodel.patch: the MANIFEST.json field `compiler.msvcmodel_patch_sha256`, the one input of the key that names the data model; the key
    covers it, so a tree with another patch has another key (this is the readable form of that fact)."""
    return hashlib.sha256(open(patch or os.path.join(root or TREE, INPUTS[0]), "rb").read()).hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--override", action="append", default=[], metavar="RELPATH=FILE", help="use FILE for the input RELPATH (build.sh's MSVC_PATCH, PATCH_DIR)")
    ap.add_argument("--verify")
    ap.add_argument("--targets")
    ap.add_argument("--stage1-tree", help="the tree_sha256 of the stage-1 tree (K2 is over it)")
    ap.add_argument("--stages", action="store_true", help="print 'K1 K2' instead of K2 (K2 needs --stage1-tree)")
    ap.add_argument("--k1", action="store_true", help="print K1 only")
    ap.add_argument("--files", action="store_true", help="print 'RELPATH PATH' of every stage-2 input (stage2.sh applies exactly these)")
    ap.add_argument("--plugin-flags", choices=LANGS, help="print the -fplugin flags of that language (needs --plugins-json)")
    ap.add_argument("--plugins-json")
    ap.add_argument("--expect-package", metavar="NAME", help="print the version env.json pins for package NAME")
    a = ap.parse_args()
    ov = dict(o.split("=", 1) for o in a.override)
    if a.files:
        for n, p in stage2_files(overrides=ov).items():
            print(n, p)
    elif a.plugin_flags:
        print(plugin_flags(a.plugin_flags, a.plugins_json))
    elif a.expect_package:
        print(package_version(a.expect_package, a.verify))
    else:
        k1, k2 = stages(overrides=ov, verify=a.verify, targets=a.targets, stage1_tree=a.stage1_tree)
        if a.k1:
            print(k1)
        else:
            if k2 is None:
                raise SystemExit("stamp.py: K2 needs --stage1-tree")
            print("%s %s" % (k1, k2) if a.stages else k2)
