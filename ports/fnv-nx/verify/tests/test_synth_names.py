#!/usr/bin/env python3
"""recipe.synth_names: a root with no objects (a header-only root, hostabi) has no names synthesised by ld for any kind: the answer is the empty set per kind, with no link run (ld
refuses a link with no input, and a root without objects has no relocations to answer). Pure: the early answer needs no compiler, store or sandbox.
Run: python3 -I <this file>."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


for kinds in (("pie",), ("shared", "exe"), ()):
    r = recipe.synth_names("/nonexistent-repo", "ports/fnv-nx/hdr", "/nonexistent-cache", kinds, None, ())
    check("a header-only root, kinds %r: the empty set per kind" % (kinds,), r == {k: set() for k in kinds}, r)
r = recipe.synth_names("/nonexistent-repo", "ports/fnv-nx/hdr", "/nonexistent-cache", ("pie",), "ports/fnv-nx/link/guest.ld", [])
check("with a script and an empty object list: still the empty set", r == {"pie": set()}, r)
sys.exit(1 if fails else 0)
