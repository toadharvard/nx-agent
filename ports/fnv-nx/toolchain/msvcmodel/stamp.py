#!/usr/bin/env python3
"""The build key of the game's compiler (toolchain/msvcmodel/build.sh): ONE computation, used by build.sh (which records it in the compiler tree's
MANIFEST.json as compiler.build_key) and by verify/recipe.py (which refuses a pinned compiler tree whose build_key is not the key of the checkout it builds).
The build has two stages and two keys, each over the CONTENT of its own inputs (a tree-relative name for each; a path outside the tree does not enter it), so the
same files give the same keys in any checkout. K1 is stage 1 (the compiler: build.sh, msvcmodel.patch and the records the compiler configure reads); K2, the build key
of the MANIFEST (stamp() returns it), is over K1 and stage 2 (stage2.sh, the runtime patches, cfg_link.py, the plugin files and the records the runtime reads). Each
input is in exactly one stage; the lists are STAGE1_INPUTS / STAGE2_INPUTS, and build.sh and stage2.sh take the keys from here. The inputs are what the two scripts READ:
  - STAGE1_INPUTS and STAGE2_INPUTS (INPUTS is their concatenation), by content;
  - every plugin file the runtime is compiled with (plugin_inputs: the recipe's PLUGIN_SOURCES and their include closure, recipe.plugin_inputs, no second list), by content;
  - the VALUES build.sh takes from records, through the same functions it calls (build_values): the declared target (targets.json guest_platform
    triplet/abi/code and console_cpu arch/features, which give the multilib selector, --with-arch and --with-abi), recipe.runtime_configure and
    recipe.runtime_cflags of guest_compiler.json, and recipe.semantics_set(None) (the semantic flag set: targets, the DATAMODEL rows' flags and
    flags.semantics.common). The record's pin fields (external, runtime) are the OUTPUT of this build and do not enter the key.
    python3 -I stamp.py [--patch FILE] [--build FILE] [--patch-dir DIR] [--verify DIR] [--targets FILE]   -> the 16-hex key on stdout   (defaults: this checkout's)"""
import argparse
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.normpath(os.path.join(HERE, "..", ".."))   # ports/fnv-nx

# the inputs of the compiler build, as paths relative to the tree (ports/fnv-nx): the one list
STAGE1_INPUTS = ("toolchain/msvcmodel/msvcmodel.patch", "toolchain/msvcmodel/build.sh")
STAGE2_INPUTS = ("toolchain/msvcmodel/stage2.sh", "toolchain/gcc/x19-x27.patch", "toolchain/gcc/libstdcxx-nosized.patch", "toolchain/gcc/newlib-guest.patch",
                 "toolchain/gcc/cfg_link.py")
INPUTS = STAGE1_INPUTS + STAGE2_INPUTS


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
          "cpu_arch": t["console_cpu"]["arch"], "cpu_features": t["console_cpu"]["features"], "runtime_configure": recipe.runtime_configure(pin)}   # stage 1: the compiler's configure
    v2 = {"runtime_configure_patch": recipe.runtime_configure_patch(pin), "runtime_cflags": recipe.runtime_cflags(pin),
          "semantics": list(recipe.semantics_set(None))}   # stage 2: what only the target libraries' builds read
    return v1, v2


def plugin_inputs(root=None, recipe=None):
    """-> [tree-relative path]: every file the plugins are built from, read from the recipe's own declaration (PLUGIN_SOURCES and their include closure;
    the runtime is compiled with all of the plugins, so a change to any of these is another plugin ABI in the pinned runtime)."""
    if recipe is None:
        sys.path.insert(0, os.path.join(root or TREE, "verify"))
        import recipe
    root = root or TREE
    roots = recipe.load_admit(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(recipe.__file__))))))   # the recipe's own admitted roots (which root holds a plugin)
    held = sorted(recipe.TREE + "/" + os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/") for d, _, ns in os.walk(root) for n in ns)
    files = set(recipe.PLUGIN_SOURCES.values())
    with tempfile.TemporaryDirectory(prefix="stamp-") as repo:   # the includes are read at their repo paths: the tree under its repo name
        os.makedirs(os.path.join(repo, os.path.dirname(recipe.TREE)))
        os.symlink(os.path.abspath(root), os.path.join(repo, recipe.TREE))
        for q in list(files):
            files.update(recipe.plugin_inputs(q, held, roots, repo))
    return sorted(os.path.relpath(f, recipe.TREE) for f in files)


def stages(root=None, patch=None, build=None, patch_dir=None, verify=None, targets=None, recipe=None, stage2=None):
    """-> (K1, K2), 16 hex digits each, over the inputs under ROOT (a tree: ports/fnv-nx of a checkout); PATCH, BUILD and STAGE2 name other files for
    msvcmodel.patch, build.sh and stage2.sh, PATCH_DIR another directory for the files of toolchain/gcc (build.sh's MSVC_PATCH and PATCH_DIR overrides);
    VERIFY, TARGETS and RECIPE as in build_values. K2 is over K1, so a changed stage-1 input changes both and a stage-2 input only K2."""
    root = root or TREE
    h = lambda p, n: "%s  %s\n" % (hashlib.sha256(open(p, "rb").read()).hexdigest(), n)
    v1, v2 = build_values(root, verify, targets, recipe)
    p1 = [patch or os.path.join(root, STAGE1_INPUTS[0]), build or os.path.join(root, STAGE1_INPUTS[1])]
    lines1 = "".join(h(p, n) for p, n in zip(p1, STAGE1_INPUTS)) + "values  " + json.dumps(v1, sort_keys=True, separators=(",", ":")) + "\n"
    k1 = hashlib.sha256(lines1.encode()).hexdigest()[:16]
    paths = []
    for i in STAGE2_INPUTS:
        if i == "toolchain/msvcmodel/stage2.sh" and stage2:
            paths.append(stage2)
        elif patch_dir and i.startswith("toolchain/gcc/"):
            paths.append(os.path.join(patch_dir, os.path.basename(i)))
        else:
            paths.append(os.path.join(root, i))
    lines2 = "stage1  %s\n" % k1 + "".join(h(p, n) for p, n in zip(paths, STAGE2_INPUTS))
    lines2 += "".join(h(os.path.join(root, n), n) for n in plugin_inputs(root, recipe))
    lines2 += "values  " + json.dumps(v2, sort_keys=True, separators=(",", ":")) + "\n"
    return k1, hashlib.sha256(lines2.encode()).hexdigest()[:16]


def stamp(root=None, patch=None, build=None, patch_dir=None, verify=None, targets=None, recipe=None, stage2=None):
    """-> K2, the build key of the whole compiler tree (MANIFEST compiler.build_key); see stages()."""
    return stages(root, patch, build, patch_dir, verify, targets, recipe, stage2)[1]


def patch_sha256(root=None, patch=None):
    """the full sha256 of msvcmodel.patch: the MANIFEST.json field `compiler.msvcmodel_patch_sha256`, the one input of the key that names the data model; the key
    covers it, so a tree with another patch has another key (this is the readable form of that fact)."""
    return hashlib.sha256(open(patch or os.path.join(root or TREE, INPUTS[0]), "rb").read()).hexdigest()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch")
    ap.add_argument("--build")
    ap.add_argument("--patch-dir")
    ap.add_argument("--verify")
    ap.add_argument("--targets")
    ap.add_argument("--stage2")
    ap.add_argument("--stages", action="store_true", help="print 'K1 K2' (build.sh) instead of K2")
    a = ap.parse_args()
    k1, k2 = stages(patch=a.patch, build=a.build, patch_dir=a.patch_dir, verify=a.verify, targets=a.targets, stage2=a.stage2)
    print("%s %s" % (k1, k2) if a.stages else k2)
