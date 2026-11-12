#!/usr/bin/env python3
"""test_dynsites_open.py - a slot reference in code bytes that no run of the function facts covers is an OPEN row, never dropped.
Run: python3 -I test_dynsites_open.py"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pemanifest, resolve_dynsites


class FF:
    ok = True
    runs = {0x1000: [(0x1000, 0x1010)]}

    def entries_of(self, a):
        return [0x1000] if 0x1000 <= a < 0x1010 else []

    def why(self, a):
        return "inside no run of the function facts"


rows = pemanifest.uncovered_refs(FF(), "LoadLibraryA", [0x1002, 0x2006, 0x2000])
assert rows == [("LoadLibraryA", 0x2000, "unresolved: inside no run of the function facts"),
                ("LoadLibraryA", 0x2006, "unresolved: inside no run of the function facts")], rows
assert pemanifest.uncovered_refs(FF(), "LoadLibraryA", [0x1002]) == []
res = resolve_dynsites.read_unresolved("module\tva\tapi\treason\nm.dll\t0x2000\tLoadLibraryA\t%s\n" % rows[0][2])
c = resolve_dynsites.counts(res)
assert (c["dyn"], c["open"], c["unreached"], c["resolved"]) == (1, 1, 0, 0), c
print("test_dynsites_open ok")
