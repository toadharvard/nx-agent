#!/usr/bin/env python3
"""Tests for verify/gate_legal.py: the pairing rule on synthetic records, COFF symbol reading, json records. No game data, no private names."""
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import gate_legal as g  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def coff(names):
    """A minimal COFF object: header + one EXTERNAL symbol per name (long names through the string table), no sections' content."""
    strtab, syms = b"", b""
    for n in names:
        b = n.encode()
        if len(b) <= 8:
            nm = b.ljust(8, b"\0")
        else:
            nm = b"\0\0\0\0" + struct.pack("<I", 4 + len(strtab))
            strtab += b + b"\0"
        syms += struct.pack("<8sIhHBB", nm, 0, 1, 0, 2, 0)
    syms += struct.pack("<8sIhHBB", b"undef\0\0\0", 0, 0, 0, 2, 0)      # undefined external: not a definition
    head = struct.pack("<HHIIIHH", 0x14c, 0, 0, 20, len(names) + 1, 0, 0)
    return head + syms + struct.pack("<I", 4 + len(strtab)) + strtab


got = g.coff_externals(coff(["_short", "a_very_long_symbol_name"]))
check("coff: short and long names, undefined excluded", got == {"_short", "a_very_long_symbol_name"}, got)
check("coff: empty / truncated input", g.coff_externals(b"") == set() and g.coff_externals(b"\0" * 20) == set())
cut = g.with_decoration_cut({"_foo@12", "_bar", "baz", "?q@@YAXXZ"})
check("decoration cut", {"foo", "bar", "baz", "_foo@12", "?q@@YAXXZ"} <= cut and "" not in cut, cut)

R = [(0x400000, 0x1000000), (0x10000000, 0x10100000)]
N = {"alpha", "beta"}
check("pair: 0x VA and a name", g.scan_file("a.md", b"x alpha at 0x00410000\n", N, R)[1] == ["a.md:1"])
check("pair: bare 8-digit VA", g.scan_file("a.tsv", b"00410000\tbeta\n", N, R)[1] == ["a.tsv:1"])
check("no pair: VA without a name", g.scan_file("a.md", b"0x00410000 gamma\n", N, R) == (1, []))
check("no pair: name without a VA", g.scan_file("a.md", b"alpha\n", N, R) == (0, []))
check("no pair: VA out of every range", g.scan_file("a.md", b"alpha 0x20000000\n", N, R) == (0, []))
check("no pair: name only as part of a token", g.scan_file("a.md", b"alphabet 0x00410000\n", N, R) == (1, []))
check("pair is per record, not per file", g.scan_file("a.md", b"0x00410000\nalpha\n", N, R) == (1, []))
check("second image range", g.scan_file("a.md", b"beta 0x10000100\n", N, R)[1] == ["a.md:1"])
j = json.dumps({"rows": [{"va": "0x00410000", "n": "alpha"}, {"va": "0x00410004", "n": "gamma"}], "other": ["alpha"]}).encode()
check("json: pair inside one record only", g.scan_file("t.json", j, N, R)[1] == ["t.json:2"] and g.scan_file("t.json", j, N, R)[0] == 2,
      g.scan_file("t.json", j, N, R))
check("json: va and name in sibling containers is no pair", g.scan_file("t.json", json.dumps({"a": {"va": "0x00410000"}, "b": {"n": "alpha"}}).encode(), N, R)[1] == [])
check("bad json falls back to lines", g.scan_file("t.json", b"{ alpha 0x00410000\n", N, R)[1] == ["t.json:1"])
check("layer", g.layer("ports/fnv-nx/verify/x.py") == "ports/fnv-nx" and g.layer("README.md") == "README.md" and g.layer("a/b/c") == "a/b")
print("FAILED: %s" % FAILED if FAILED else "all pass")
sys.exit(1 if FAILED else 0)
