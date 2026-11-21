#!/usr/bin/env python3
"""Selftest of verify/pathflow.py (no build): a module-level path constant read through a parameter default is a read of the file, in every default form
(positional default, keyword-only default, lambda default) and through a closure. Each in-tree read is reported in INSIDE (so the stage mounts it); a default
that leaves the owner tree is an escape. Run: python3 -I <this file>."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import pathflow  # noqa: E402

OWNER, ME = "ports/fnv-nx/verify", "ports/fnv-nx/verify/t.py"
HEAD = "import os\nHERE = os.path.dirname(os.path.abspath(__file__))\nT = os.path.join(HERE, 'targets.json')\nOUT = os.path.join(HERE, '..', '..', 'x.json')\n"
CASES = [
    ("positional default", "def load(path=T):\n    return open(path).read()\n", ["ports/fnv-nx/verify/targets.json"], 0),
    ("keyword-only default", "def load(*, path=T):\n    return open(path).read()\n", ["ports/fnv-nx/verify/targets.json"], 0),
    ("lambda default", "f = lambda p=T: open(p).read()\n", ["ports/fnv-nx/verify/targets.json"], 0),
    ("closure", "def outer():\n    q = T\n    def inner():\n        return open(q).read()\n    return inner\n", ["ports/fnv-nx/verify/targets.json"], 0),
    ("default leaving the owner tree", "def load(path=OUT):\n    return open(path).read()\n", [], 1),
    ("an argument without default is the caller's key", "def load(path):\n    return open(path).read()\n", [], 0),
]
fails = 0
for name, body, want_in, want_esc in CASES:
    inside = []
    esc = pathflow.escapes_of(ME, HEAD + body, OWNER, inside)
    ok = inside == want_in and len(esc) == want_esc
    print("%s %s" % ("PASS" if ok else "FAIL", name), "" if ok else (inside, esc))
    fails += 0 if ok else 1
# the trees the stage mounts (a list): a read in a sibling tree of a declared root is inside, a read outside every mounted tree is an escape
SIB = "import os\nHERE = os.path.dirname(os.path.abspath(__file__))\nD = os.path.join(HERE, '..', 'msvcmodel', 'DATAMODEL.json')\nopen(D).read()\n"
for name, owners, want_in, want_esc in (
        ("a read in a sibling tree: escape for the module's own tree alone", "ports/fnv-nx/verify", [], 1),
        ("a read in a sibling tree: inside when that tree is mounted", ["ports/fnv-nx/verify", "ports/fnv-nx/msvcmodel"], ["ports/fnv-nx/msvcmodel/DATAMODEL.json"], 0),
        ("a read in a tree that is not mounted: escape", ["ports/fnv-nx/verify", "ports/fnv-nx/other"], [], 1)):
    inside = []
    esc = pathflow.escapes_of(ME, SIB, owners, inside)
    ok = inside == want_in and len(esc) == want_esc
    print("%s %s" % ("PASS" if ok else "FAIL", name), "" if ok else (inside, esc))
    fails += 0 if ok else 1
sys.exit(1 if fails else 0)
