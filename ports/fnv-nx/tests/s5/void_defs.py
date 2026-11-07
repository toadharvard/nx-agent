#!/usr/bin/env python3
"""void_defs.py - functions whose native definition returns void while a reference expects a value (S5-boot).

    void_defs.py <link out dir> [--list FILE]

The decomp sometimes defines a function `void C::f()` (the body computes a value and drops it: `{ arr->GetSize(); }`)
while its callers declare `unsigned C2::f()`: natively the callers read whatever x0 holds. FUN_004dcce0 (the display
adapter's mode count) returned 4 for a list of 1 or 2 modes, and renderer init rejected the resolution. The link
resolves references by address, and its ABI classes (abi.tsv) do not include the result's presence.

Reads out/refs.tsv (each code reference with its MSVC decorated name), out/addrmap.tsv (the native definition of
each address) and out/cache/msvc.tsv (decorated -> undecorated names, which give the definition's decorated name by
its class and function name). Prints the count and, with --list, one line per address: address, definition,
one value-returning reference."""
import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot_trace import msvc_void, MEMBER_THIS  # noqa: E402


def itanium_parts(sym):
    """the qualified name's parts of an Itanium symbol: _ZN<len>name...E... or _Z<len>name..."""
    if sym.startswith("_ZN"):
        i, nested = 3, True
    elif sym.startswith("_Z"):
        i, nested = 2, False
    else:
        return None
    parts = []
    while i < len(sym) and sym[i].isdigit():
        j = i
        while j < len(sym) and sym[j].isdigit():
            j += 1
        k = int(sym[i:j])
        parts.append(sym[j:j + k])
        i = j + k
        if not nested:
            break
    return parts or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--list")
    a = ap.parse_args()
    defs = {}
    for line in open(os.path.join(a.out, "addrmap.tsv")):
        f = line.rstrip("\n").split("\t")
        if len(f) > 3 and f[1] in ("F", "X", "C") and f[3]:
            defs.setdefault(f[0], f[3])
    refs = collections.defaultdict(set)
    for line in open(os.path.join(a.out, "refs.tsv")):
        f = line.rstrip("\n").split("\t")
        if len(f) > 6 and f[2] == "code" and f[4] and f[6].startswith("?"):
            refs[f[4]].add(f[6])
    want = {}   # "?fn@Class@" prefix -> address
    for addr, sym in defs.items():
        p = itanium_parts(sym)
        if p and addr in refs:
            want["?" + "@".join(reversed(p)) + "@@"] = addr
    def_dec = {}
    for line in open(os.path.join(a.out, "cache", "msvc.tsv"), errors="replace"):
        d = line.split("\t", 1)[0]
        k = d.find("@@")
        if k > 0:
            key = d[:k + 2]
            if key in want and d not in refs[want[key]]:
                def_dec.setdefault(want[key], d)
    hits = []
    for addr, d in def_dec.items():
        if not msvc_void(d):
            continue
        valued = sorted(r for r in refs[addr] if not r.startswith("??") and not msvc_void(r))   # ctors/dtors
        # and operators have no plain return type
        if valued:
            hits.append((addr, defs[addr], d, valued[0]))
    print(f"definitions with a decorated name: {len(def_dec)} of {len(refs)} referenced addresses; "
          f"void definitions with a value-returning reference: {len(hits)}")
    if a.list:
        with open(a.list, "w") as f:
            for h in sorted(hits):
                f.write("\t".join(h) + "\n")


if __name__ == "__main__":
    main()
