#!/usr/bin/env python3
"""Tests for contract/callcensus.py: the bisect interval stabber and the forward closure on synthetic facts. No game data."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import callcensus  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def fn(calls=(), tails=(), data=()):
    return dict(calls=[[0, c] for c in calls], tails=[[0, t] for t in tails], codeptrs=[], jt=[], vrefs=[], data=[list(d) for d in data])


facts = {"fn": {"16": fn(calls=[32]), "32": fn(tails=[48]), "48": fn(), "64": fn(), "80": fn(data=[(0, 4096)])},
         "vft": {}, "ptrs": [[4096, 64], [4100, 80]]}
check("closure follows calls and tail jumps", callcensus.closure(facts, 16) == {16, 32, 48})
check("the entry inside a function starts at the function containing it", callcensus.closure(facts, 20) == {16, 32, 48})
facts["fn"]["48"] = fn(data=[(0, 4096)])
check("a pointer table a reached function references adds every code pointer of its contiguous run", callcensus.closure(facts, 16) == {16, 32, 48, 64, 80})
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
