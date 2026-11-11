#!/usr/bin/env python3
"""Tests for contract/images.py (the image set rule) on a tiny synthetic PE set. No game data."""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.append(os.path.join(HERE, "..", "..", "verify"))
import scratch  # noqa: E402

scratch.install()
import images  # noqa: E402
import modules  # noqa: E402

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


def tree(files):
    d = tempfile.mkdtemp(prefix="img-")
    for n, data in files.items():
        open(os.path.join(d, n), "wb").write(data)
    return d


d = tree({"A.exe": pe(["B.DLL"]), "b.dll": pe(["C.dll", "kernel32.dll"])})
check("A imports B, B imports missing C -> {A, B}", [n for n, _ in images.image_set(d + "/A.exe", [d])] == ["a.exe", "b.dll"])
d2 = tree({"A.exe": pe(["B"], ["D.dll"]), "b.DLL": pe(["A.exe"]), "d.dll": pe(["b"])})
check("delay-load imports count, cycles end, names fold case and the .dll extension",
      [n for n, _ in images.image_set(d2 + "/A.exe", [d2])] == ["a.exe", "b.dll", "d.dll"])
d3, d4 = tree({"A.exe": pe(["x.dll"])}), tree({"X.dll": pe()})
check("a dll in a later directory of the list is found", [n for n, _ in images.image_set(d3 + "/A.exe", [d3, d4])] == ["a.exe", "x.dll"])
d5 = tree({"A.exe": pe(["y.dll"]), "y.dll": pe(), "Y.DLL": pe()})
try:
    images.image_set(d5 + "/A.exe", [d5])
    check("two files differing only in case are an error", False)
except modules.StageError as e:
    check("two files differing only in case are an error", "differ only in case" in str(e), e)
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
