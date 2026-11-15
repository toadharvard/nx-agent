#!/usr/bin/env python3
"""The build key of the game's compiler (toolchain/msvcmodel/build.sh): ONE computation, used by build.sh (which records it in the compiler tree's
MANIFEST.json as compiler.build_key) and by verify/recipe.py (which refuses a pinned compiler tree whose build_key is not the key of the checkout it builds).
The key is a function of the CONTENT of the inputs of the compiler build alone: msvcmodel.patch, build.sh and the three guest patches, each under its
tree-relative name (a path outside the tree does not enter it), so the same files give the same key in any checkout.
    python3 -I stamp.py [--patch FILE] [--build FILE] [--patch-dir DIR]    -> the 16-hex key on stdout   (the defaults are this checkout's files)"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TREE = os.path.normpath(os.path.join(HERE, "..", ".."))   # ports/fnv-nx

# the inputs of the compiler build, as paths relative to the tree (ports/fnv-nx): the one list
INPUTS = ("toolchain/msvcmodel/msvcmodel.patch", "toolchain/msvcmodel/build.sh",
          "toolchain/gcc/x19-x27.patch", "toolchain/gcc/libstdcxx-nosized.patch", "toolchain/gcc/newlib-guest.patch")


def stamp(root=None, patch=None, build=None, patch_dir=None):
    """-> the 16-hex key over the inputs under ROOT (a tree: ports/fnv-nx of a checkout); PATCH, BUILD and PATCH_DIR name other files for the first,
    second and the three last inputs (build.sh's MSVC_PATCH and PATCH_DIR overrides)."""
    root = root or TREE
    paths = [patch or os.path.join(root, INPUTS[0]), build or os.path.join(root, INPUTS[1])]
    paths += [os.path.join(patch_dir, os.path.basename(i)) if patch_dir else os.path.join(root, i) for i in INPUTS[2:]]
    lines = "".join("%s  %s\n" % (hashlib.sha256(open(p, "rb").read()).hexdigest(), n) for p, n in zip(paths, INPUTS))
    return hashlib.sha256(lines.encode()).hexdigest()[:16]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch")
    ap.add_argument("--build")
    ap.add_argument("--patch-dir")
    a = ap.parse_args()
    print(stamp(patch=a.patch, build=a.build, patch_dir=a.patch_dir))
