#!/usr/bin/env python3
"""Tests for contract/loadlib.py: the image-set fixpoint over resolved LoadLibrary names, dynsites agreement, the rows file.
Synthetic PE files and a synthetic derivation (the derivation itself is argflow's, tested in test_gpa_names.py). No game data."""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import argflow  # noqa: E402
import dllkind  # noqa: E402
import gpa_names  # noqa: E402
import loadlib  # noqa: E402

FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:300]))
    if not cond:
        FAILED.append(name)


def pe(imports=(), delays=()):
    """A PE32 i386 image: one 4 KiB section at rva 0x1000 holding the import / delay-load directories and the names."""
    sec = bytearray(0x1000)
    pos = 0x500                                   # strings and the empty thunk (rva 0x1400 holds the zero thunk word)

    def put(b):
        nonlocal pos
        r = pos
        sec[pos:pos + len(b)] = b
        pos += len(b)
        return 0x1000 + r

    idesc = bytearray()
    for n in imports:
        idesc += struct.pack("<IIIII", 0x1400, 0, 0, put(n.encode() + b"\0"), 0x1400)
    idesc += bytes(20)
    ddesc = bytearray()
    for n in delays:
        ddesc += struct.pack("<8I", 1, put(n.encode() + b"\0"), 0, 0, 0, 0, 0, 0)
    ddesc += bytes(32)
    sec[0:len(idesc)] = idesc
    sec[0x200:0x200 + len(ddesc)] = ddesc
    h = bytearray(0x200)
    h[0:2] = b"MZ"
    struct.pack_into("<I", h, 0x3c, 0x80)
    h[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", h, 0x84, 0x14c, 1, 0, 0, 0, 224, 0x2102)
    o = 0x98
    struct.pack_into("<H", h, o, 0x10b)
    struct.pack_into("<I", h, o + 28, 0x10000000)
    struct.pack_into("<II", h, o + 32, 0x1000, 0x200)
    struct.pack_into("<II", h, o + 56, 0x2000, 0x200)
    struct.pack_into("<I", h, o + 92, 16)
    struct.pack_into("<II", h, o + 96 + 8 * 1, 0x1000, len(idesc))
    struct.pack_into("<II", h, o + 96 + 8 * 13, 0x1200, len(ddesc))
    struct.pack_into("<8sIIIIIIHHI", h, o + 224, b".data", 0x1000, 0x1000, 0x1000, 0x200, 0, 0, 0, 0, 0xc0000040)
    return bytes(h) + bytes(sec)


d = tempfile.mkdtemp(prefix="ll-")
for n, imps in {"A.exe": ["kernel32.dll"], "b.dll": ["kernel32.dll"], "c.dll": ["kernel32.dll"], "z.dll": ["kernel32.dll"]}.items():
    with open(os.path.join(d, n), "wb") as fh:
        fh.write(pe(imps))
BR = {"kernel32.dll"}
asked = []


def rows(names, status="closed"):
    return [(0x1000, [0x1000], "LoadLibraryA", status, names, [])]


def fake(table):
    def derive_image(name, path, rg):
        asked.append(name)
        return table[name]
    return derive_image


has = lambda n: "r" if n in ("a.exe", "b.dll", "c.dll") else None
members, per = loadlib.fixpoint(d + "/A.exe", [d], BR, has, fake({"a.exe": rows(["b.dll"]), "b.dll": rows(["c.dll"]), "c.dll": rows(["kernel32.dll"])}))
check("a.exe loads b, b loads c: the fixpoint reaches c; z (loaded by nobody) is no image", [n for n, _ in members] == ["a.exe", "b.dll", "c.dll"], members)
check("every member is derived once", sorted(asked) == ["a.exe", "b.dll", "c.dll"], asked)
asked.clear()
members, per = loadlib.fixpoint(d + "/A.exe", [d], BR, has, fake({"a.exe": rows([]), "b.dll": rows(["c.dll"]), "c.dll": rows([])}))
check("no resolved name: only the exe; the loads of a file outside the set are never derived", [n for n, _ in members] == ["a.exe"] and asked == ["a.exe"], (members, asked))
asked.clear()
members, per = loadlib.fixpoint(d + "/A.exe", [d], BR, has, fake({"a.exe": rows(["z.dll"])}))
check("a member without facts (z) has no row: unknown, listed by the gate", [n for n, _ in members] == ["a.exe", "z.dll"] and sorted(per) == ["a.exe"], (members, per))
check("an open site contributes no name", [n for n, _ in loadlib.fixpoint(d + "/A.exe", [d], BR, has, fake({"a.exe": rows([], "open")}))[0]] == ["a.exe"])

per = {"x.dll": [(0x10, [0xe], "LoadLibraryA", "closed", ["a.dll"], []), (0x20, [0x1e], "LoadLibraryW", "open", [], ["1e name argument is imm 5"])]}
text = loadlib.tsv(per)
check("loadlib.tsv round trip through dllkind.read_loads", dllkind.read_loads(text) == {"x.dll": ["a.dll"]}, text)
dyn = "module\tsite\tapi\toperand\nx.dll\t0x10\tLoadLibraryA\ta.dll\nx.dll\t0x20\tLoadLibraryW\tdynamic\ny.dll\t0x30\tLoadLibraryA\tdynamic\n"
check("dynsites agreement: the same sites (rows of modules without a derivation are not compared)", loadlib.agree(per, dyn) == [])
check("a site only in dynsites and one only here are listed",
      loadlib.agree(per, dyn.replace("0x20", "0x24")) == [("x.dll", 0x20, "only here"), ("x.dll", 0x24, "only in dynsites")])
check("targets: closed names per module", loadlib.targets(per) == {"x.dll": ["a.dll"]})

tf = os.path.join(d, "images.tsv")
open(tf, "w").write("name\tpe\tranges\na.exe\t/p/a.exe\t/r/a\nb.dll\t/p/b.dll\t-\n")
check("images.tsv: specs of the members with facts, names of all members", gpa_names.specs_of(tf) == ["a.exe=/p/a.exe,/r/a"] and gpa_names.members_of(tf) == ["a.exe", "b.dll"])

rg = argflow.read_ranges("# base=0x01720000: absolute VAs\n01721000\t1721000-1721042\n")
sh = argflow.rebased(rg, 0x400000, "d.dll")
check("ranges at the assigned base are read at the preferred base (one coordinate system with the image and dynsites)",
      dict(sh) == {0x401000: [(0x401000, 0x401042)]} and sh.base == 0x400000, dict(sh))
check("ranges at the image's own base stay", argflow.rebased(argflow.read_ranges("# base=0x00400000: x\n00401000\t401000-401004\n"), 0x400000, "e.exe") == {0x401000: [(0x401000, 0x401004)]})
check("ranges without a header stay", argflow.rebased(argflow.read_ranges("00401000\t401000-401004\n"), 0x400000, "e.exe") == {0x401000: [(0x401000, 0x401004)]})
try:
    argflow.rebased(argflow.read_ranges("# base=0x02000000: x\n00001000\t1000-1004\n"), 0x400000, "e.dll")
    check("ranges that shift below zero are refused", False)
except ValueError:
    check("ranges that shift below zero are refused", True)

if FAILED:
    print("FAILED: %d" % len(FAILED))
    sys.exit(1)
print("all passed")
