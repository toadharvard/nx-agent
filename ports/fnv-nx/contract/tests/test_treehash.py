#!/usr/bin/env python3
"""treehash: the one home of the tree-hash rule. cas.py re-exports it (identity), the hash of a fixture tree is the value the pre-split cas.py
computed (pinned), it equals ingest's hash, and imagerec reads it from here (not from verify/)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'verify'))
import cas
import imagerec
import treehash

PINNED = "0835a6860c2e6dbc1594a47f0a466680d789a8c931b496d8f016b1c632637255"   # tree_hash of the fixture below, as verify/cas.py computed it before the rule moved here
OK = []


def check(name, cond):
    if not cond:
        print("FAIL", name)
        sys.exit(1)
    OK.append(name)


def fixture(root):
    os.makedirs(os.path.join(root, "d", "e"))
    os.makedirs(os.path.join(root, "empty"))
    with open(os.path.join(root, "a.txt"), "wb") as f:
        f.write(b"alpha\n")
    with open(os.path.join(root, "d", "run.sh"), "wb") as f:
        f.write(b"#!/bin/sh\n")
    os.chmod(os.path.join(root, "d", "run.sh"), 0o755)
    with open(os.path.join(root, "d", "e", "z.bin"), "wb") as f:
        f.write(bytes(range(256)) * 5000)
    os.symlink("../a.txt", os.path.join(root, "d", "ln"))
    os.makedirs(os.path.join(root, "__pycache__"))
    with open(os.path.join(root, "__pycache__", "x.pyc"), "wb") as f:
        f.write(b"x")


for n in ("FORMAT", "CasError", "canon", "tree_hash"):
    check("cas.%s is treehash.%s" % (n, n), getattr(cas, n) is getattr(treehash, n))
td = tempfile.mkdtemp(prefix="treehash-")
fx = os.path.join(td, "fx")
os.makedirs(fx)
fixture(fx)
h = treehash.tree_hash(fx)
check("the hash of the fixture tree is the pinned one", h == PINNED)
store = os.path.join(td, "store")
os.makedirs(store)
check("tree_hash equals what ingest stores", cas.ingest(fx, store) == h)
check("skip leaves the named entries out", treehash.tree_hash(fx, skip=("__pycache__",)) != h)
check("a single file hashes as its entry", treehash.tree_hash(os.path.join(fx, "a.txt")) == cas.tree_hash(os.path.join(fx, "a.txt")))
check("imagerec uses treehash", imagerec._tree_hash(fx) == h)
check("treehash imports only the standard library", set(l.split()[1] for l in open(treehash.__file__) if l.startswith("import ")) == {"hashlib", "json", "os", "stat"})
print("treehash: %d checks passed" % len(OK))
