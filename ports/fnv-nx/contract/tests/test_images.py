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
check("A imports B, B imports missing C -> B is declared, not an image: {A}", [n for n, _ in images.image_set(d + "/A.exe", [d])] == ["a.exe"])
check("... the declared B is reached but not walked", [n for n, _ in images.classify(d + "/A.exe", [d])[0]] == ["b.dll"]
      and images.classify(d + "/A.exe", [d])[1] == {"b.dll": "declared", "c.dll": "platform", "kernel32.dll": "platform"}, images.classify(d + "/A.exe", [d])[1])
check("C and kernel32 bridged -> B is an image again", [n for n, _ in images.image_set(d + "/A.exe", [d], {"c.dll", "kernel32.dll"})] == ["a.exe", "b.dll"])
dB = tree({"A.exe": pe(["B.DLL", "kernel32.dll"]), "b.dll": pe(["C.dll"]), "c.dll": pe(["kernel32.dll"])})
kB = images.classify(dB + "/A.exe", [dB], {"b.dll", "kernel32.dll"})
check("a bridge dll with a found file is not an image and is not walked (c.dll is an image on its own, as a file of the directory, not through b)",
      [n for n, _ in images.image_set(dB + "/A.exe", [dB], {"b.dll", "kernel32.dll"})] == ["a.exe", "c.dll"]
      and kB[1]["b.dll"] == "bridge" and [n for n, _ in kB[0]] == ["c.dll"], kB[1])
check("without the bridge row the same chain is images",
      [n for n, _ in images.image_set(dB + "/A.exe", [dB], {"kernel32.dll"})] == ["a.exe", "b.dll", "c.dll"])
check("an image whose dependency is a declared dll is itself declared (closure, no named list)",
      [n for n, _ in images.image_set(dB + "/A.exe", [dB], set())] == ["a.exe"]
      and images.classify(dB + "/A.exe", [dB], set())[1]["c.dll"] == "declared")
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
import dllkind  # noqa: E402
check("images_of: a cycle of images stays, a dll with an unresolved import and its dependents go",
      dllkind.images_of({"a.dll": ["b.dll"], "b.dll": ["a.dll", "k.dll"], "c.dll": ["a.dll"], "d.dll": ["x.dll"], "e.dll": ["d.dll"]}, {"k.dll"}) == {"a.dll", "b.dll", "c.dll"})
check("images_of: the root's own name and bridge names satisfy an import", dllkind.images_of({"a.dll": ["exe.exe", "k.dll"]}, {"exe.exe", "k.dll"}) == {"a.dll"})
dM = tree({"x.dll": pe(["kernel32.dll"]), "y.dll": pe(["nowhere.dll"]), "z.dll": pe(["y.dll"])})
mods = modules.scan([dM], {"kernel32.dll"})
img = dllkind.images_of({n: [images.iat.norm_dll(x) for x, _ in m.imports] for n, m in mods.items()}, {"kernel32.dll"})
check("modules uses the same function: x is an image, y and its dependent z are declared", img == {"x.dll"}, img)
check("modules.closure treats a declared dll as satisfied, not as a missing module",
      [m.name for m in modules.closure(modules.PE(pe(["x.dll", "y.dll"]), "e"), {"kernel32.dll"}, {"x.dll": mods["x.dll"]}, {"y.dll", "z.dll"})] == ["x.dll"])
import types  # noqa: E402


def fake(name, size):
    return types.SimpleNamespace(name=name, base=None, relocs=0, pe=types.SimpleNamespace(size_of_image=size, base=0x10000000, check_relocations=lambda: 0))


exe = types.SimpleNamespace(base=0x400000, size_of_image=0x1000000)
a = [fake("binkw32.dll", 0x68000), fake("libvorbis.dll", 0x133000), fake("libvorbisfile.dll", 0x1e000)]
modules.assign_bases(exe, a)
before = {m.name: m.base for m in a}
b = [fake("binkw32.dll", 0x68000), fake("libvorbis.dll", 0x133000), fake("libvorbisfile.dll", 0x1e000), fake("ssce5432.dll", 0x39000)]
modules.assign_bases(exe, b)
check("adding an image whose name sorts last moves no base", {m.name: m.base for m in b if m.name in before} == before)
c = [fake("atimgpud.dll", 0x29000)] + [fake(m.name, m.pe.size_of_image) for m in a]
modules.assign_bases(exe, c)
check("adding an image whose name sorts first moves the later ones (bases are a function of the name order)", c[1].base != before["binkw32.dll"])
import declareddlls  # noqa: E402
sd = tempfile.mkdtemp(prefix="specs-")
for n in ("msvcp110.spec", "comdlg32.spec"):
    open(os.path.join(sd, n), "w").write("@ stdcall X()\n")
check("declareddlls: one row per spec file, sorted, name + .dll", [r[0] for r in declareddlls.derive(sd)] == ["comdlg32.dll", "msvcp110.dll"])
open(os.path.join(sd, "readme.txt"), "w").write("x")
try:
    declareddlls.derive(sd)
    check("declareddlls: a non-spec file is an error", False)
except ValueError:
    check("declareddlls: a non-spec file is an error", True)
print("FAILED: %d" % len(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
