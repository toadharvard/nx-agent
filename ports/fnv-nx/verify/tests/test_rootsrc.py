#!/usr/bin/env python3
"""Tests for recipe.root_source (a `sources` line naming a tracked file of another admitted root). Plain script: exit 0 on pass. The builds that show the unit's
key follows the file's content are in test_uses.py (they need the pinned toolchain)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)))
    fails += 0 if ok else 1


R, O = "ports/fnv-nx/host", "ports/fnv-nx/lift/rt"
roots = {R: {"class": "product"}, O: {"class": "product"}}
files = {R + "/a.c", O + "/rcptab.c", O + "/sub/x.c", O + "/inc/h.h"}
uses = [{"root": O, "output": "GEN"}]


def red(other, p, u=uses, rt=roots, fl=files):
    try:
        recipe.root_source(rt, fl, u, R, other, p)
        return None
    except ValueError as e:
        return str(e)


check("green: a tracked file of an admitted root in uses", recipe.root_source(roots, files, uses, R, O, "rcptab.c") == (O + "/rcptab.c", "ports__fnv-nx__lift__rt/rcptab.c"))
check("green: a file in a subdirectory", recipe.root_source(roots, files, uses, R, O, "sub/x.c")[1] == "ports__fnv-nx__lift__rt/sub/x.c")
check("red: the root is not admitted", "not an admitted root" in (red("ports/fnv-nx/ghost", "rcptab.c") or ""), red("ports/fnv-nx/ghost", "rcptab.c"))
check("red: the file is not tracked", "not a git-tracked file" in (red(O, "nope.c") or ""), red(O, "nope.c"))
check("red: a directory is not a file", "not a git-tracked file" in (red(O, "sub") or ""), red(O, "sub"))
check("red: a glob is not a file", "not a git-tracked file" in (red(O, "*.c") or ""), red(O, "*.c"))
check("red: the path leaves the root (..)", "not a relative path" in (red(O, "../host/a.c") or ""), red(O, "../host/a.c"))
check("red: an absolute path", "not a relative path" in (red(O, "/etc/passwd") or ""), red(O, "/etc/passwd"))
check("red: the root is not in uses", "not in `uses`" in (red(O, "rcptab.c", u=[]) or ""), red(O, "rcptab.c", u=[]))
check("red: the root itself", "the root itself" in (red(R, "a.c") or ""), red(R, "a.c"))
check("source_lines accepts a root path as the first part", recipe.source_lines("%s:rcptab.c\n" % O) == [(O, "rcptab.c")])
try:
    recipe.source_lines("%s:../x.c\n" % O)
    check("source_lines refuses ..", False)
except ValueError:
    check("source_lines refuses ..", True)
sys.exit(1 if fails else 0)
