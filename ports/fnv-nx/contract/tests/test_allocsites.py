#!/usr/bin/env python3
"""Tests for contract/allocsites.py: the size and parameter helpers on argflow results. No game data."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import allocsites  # noqa: E402
import argflow  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got %r want %r" % (got, want)))
    if not ok:
        FAILED.append(name)


def res(leaves=(), breaks=()):
    r = argflow.Result()
    for k, v in leaves:
        r.add(k, v, ())
    for b in breaks:
        r.brk(*b)
    return r


check("one immediate is a constant size", allocsites.size_of(res([("imm", 64)])), 64)
check("two immediates are open", allocsites.size_of(res([("imm", 64), ("imm", 8)])), None)
check("a break makes the size open", allocsites.size_of(res([("imm", 64)], [(1, "x")])), None)
check("a pointer leaf is open", allocsites.size_of(res([("ret", 5)])), None)
check("parameter 0 of the function only", allocsites.param_of(res([("param", (16, 0))]), 16), 0)
check("a parameter of another function is not it", allocsites.param_of(res([("param", (32, 0))]), 16), None)
check("a parameter plus an immediate is not a clean parameter", allocsites.param_of(res([("param", (16, 0)), ("imm", 4)]), 16), None)
check("allocator imports: HeapAlloc size is argument 2, VirtualAlloc 1", (allocsites.ALLOCATORS["HeapAlloc"], allocsites.ALLOCATORS["VirtualAlloc"]), (2, 1))
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
