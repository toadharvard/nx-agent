#!/usr/bin/env python3
"""check_shim.py - shim/include does not depend on a platform file.

shim/include is the shared header directory of every root (platform, render, loader). A header there must be resolvable without
platform/: every `#include "X"` or `#include <X>` of a file under shim/include either names a file of shim/include, of
toolchain/include (the shared host ABI), or of no tracked directory of ours at all (a system or external header). It fails when X
names a file that exists under platform/ (by its path suffix) and not under shim/include or toolchain/include, or when the path of
the include contains `platform/`.
Usage: check_shim.py [--root ports/fnv-nx]   exit 1 with one line per violation."""
import os
import re
import sys

INC = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"]+)[>"]', re.M)


def tracked(root, sub):
    out = []
    for d, _, fs in os.walk(os.path.join(root, sub)):
        for f in fs:
            out.append(os.path.relpath(os.path.join(d, f), root))
    return out


def check(root):
    bad = []
    shared = [os.path.join("shim", "include", x) for x in ()]
    plat = tracked(root, "platform")
    for rel in tracked(root, os.path.join("shim", "include")):
        try:
            text = open(os.path.join(root, rel), errors="replace").read()
        except OSError:
            continue
        for x in INC.findall(text):
            if "platform/" in x:
                bad.append("%s: includes %s (a platform path)" % (rel, x))
                continue
            here = os.path.normpath(os.path.join(os.path.dirname(rel), x))
            if os.path.isfile(os.path.join(root, here)):
                continue
            if os.path.isfile(os.path.join(root, "shim", "include", x)) or os.path.isfile(os.path.join(root, "toolchain", "include", x)):
                continue
            if any(p == os.path.join("platform", x) or p.endswith("/" + x) for p in plat if not x.startswith("..")):
                bad.append("%s: includes %s, a file of platform/" % (rel, x))
    return bad


def main(a):
    root = a[a.index("--root") + 1] if "--root" in a else "ports/fnv-nx"
    bad = check(root)
    for b in bad:
        print(b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
