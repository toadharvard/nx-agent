#!/usr/bin/env python3
"""Selftest of regsites.py on a synthetic PE image built here from byte snippets (no game bytes, no private input).

The image holds, at 16-aligned entries:
  F1  open HKLM\\Software\\Synth\\Game into [ebp-4], read value "Installed Path" through the handle reloaded into ecx (register flow)
  W   wrapper: open HKCU\\Control Panel\\X, read the value named by its parameter [ebp+8]
  C   calls W("N1") and W("N2")                                        (wrapper propagation from callers)
  D   value name read from an object field, import pointer held in esi (dynamic, reason kept; the pointer load is no extra row)
  X   method (vtable 0x403200 slot 1) whose value name is a field of `this`          (stops: field rule, entry through a vtable)
  Y   copy of W, called directly by C2 and also a vtable word (0x403220 slot 0)       (stops: the direct callers do not cover it)
  T   frameless thunk `jmp [RegSetValueExA]` after D                   (reported, never dropped)
Usage: check_regsites.py [specs dir]   (with a Wine specs dir it also checks the ROLES table against the exports)
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pemanifest, regsites

BASE = 0x400000
OPEN, QUERY, SETV, SAVE = 0x403000, 0x403004, 0x403008, 0x40300c


def build():
    img, rel, cur = bytearray(0x4000), [], [0x403100]

    def put(va, b):
        img[va - BASE:va - BASE + len(b)] = b

    def string(s):
        a = cur[0]
        put(a, s.encode() + b"\0")
        cur[0] += (len(s) + 4) & ~3
        return a

    def asm(va, parts):
        b = bytearray()
        for p in parts:
            if isinstance(p, tuple):
                if p[0] == "abs":  # relocated 32-bit address
                    rel.append(va + len(b))
                    b += struct.pack("<I", p[1])
                else:  # rel32 to a target
                    b += struct.pack("<i", p[1] - (va + len(b) + 4))
            else:
                b += bytes(p)
        put(va, bytes(b))
        put(va + len(b), b"\xcc" * ((-(va + len(b))) % 16 or 16))

    sw, val, n1, n2, cp = (string(x) for x in ("Software\\Synth\\Game", "Installed Path", "N1", "N2", "Control Panel\\X"))
    pro = [b"\x55\x8b\xec"]
    z4 = [b"\x6a\x00"] * 4
    open_out = [b"\x83\xec\x04\x8d\x45\xfc\x50"]  # sub esp,4; lea eax,[ebp-4]; push eax
    asm(0x401000, pro + open_out + [b"\x68", ("abs", sw), b"\x68\x02\x00\x00\x80", b"\xff\x15", ("abs", OPEN), b"\x8b\x4d\xfc"] + z4 +
        [b"\x68", ("abs", val), b"\x51\xff\x15", ("abs", QUERY), b"\x8b\xe5\x5d\xc3"])
    asm(0x401040, pro + open_out + [b"\x68", ("abs", cp), b"\x68\x01\x00\x00\x80", b"\xff\x15", ("abs", OPEN), b"\x8b\x4d\xfc"] + z4 +
        [b"\x8b\x45\x08\x50\x51\xff\x15", ("abs", QUERY), b"\x8b\xe5\x5d\xc2\x04\x00"])
    asm(0x401090, pro + [b"\x68", ("abs", n1), b"\xe8", ("rel", 0x401040), b"\x83\xc4\x04", b"\x68", ("abs", n2), b"\xe8",
                         ("rel", 0x401040), b"\x83\xc4\x04\x5d\xc3"])
    asm(0x4010d0, pro + [b"\x8b\x45\x08\x8b\x48\x08", b"\x8b\x35", ("abs", QUERY)] + z4 + [b"\x51\x68\x02\x00\x00\x80\xff\xd6\x5d\xc3"])
    asm(0x401110, [b"\xff\x25", ("abs", SETV)])
    asm(0x401150, pro + [b"\x8b\x41\x10\x8b\x35", ("abs", QUERY)] + z4 + [b"\x50\x68\x02\x00\x00\x80\xff\xd6\x5d\xc3"])
    asm(0x401190, pro + open_out + [b"\x68", ("abs", cp), b"\x68\x01\x00\x00\x80", b"\xff\x15", ("abs", OPEN), b"\x8b\x4d\xfc"] + z4 +
        [b"\x8b\x45\x08\x50\x51\xff\x15", ("abs", QUERY), b"\x8b\xe5\x5d\xc2\x04\x00"])
    asm(0x4011d0, pro + [b"\x68", ("abs", n1), b"\xe8", ("rel", 0x401190), b"\x83\xc4\x04\x5d\xc3"])
    for va, w in ((0x403200, 0x401090), (0x403204, 0x401150), (0x403220, 0x401190)):  # vtables: relocated words of code addresses
        put(va, struct.pack("<I", w))
        rel.append(va)
    m = pemanifest.Mod.__new__(pemanifest.Mod)
    m.name, m.base, m.img, m.relocs = "synth.exe", BASE, bytes(img), sorted(rel)
    m.code = [(0x401000, 0x400)]
    m.imports = [("advapi32.dll", "RegOpenKeyA", OPEN), ("advapi32.dll", "RegQueryValueExA", QUERY), ("advapi32.dll", "RegSetValueExA", SETV)]
    return m


def main():
    m = build()
    rows = regsites.regsites(m)
    show = "\n".join(repr(r) for r in rows)
    # row = (site, api, root, subkey, value, status, reason, via)
    q1 = [r for r in rows if r[1] == "RegQueryValueExA" and r[0] < 0x401040]
    assert len(q1) == 1 and q1[0][2:6] == ("HKEY_LOCAL_MACHINE", "Software\\Synth\\Game", "Installed Path", "static"), show
    o1 = [r for r in rows if r[1] == "RegOpenKeyA" and r[0] < 0x401040]
    assert len(o1) == 1 and o1[0][2:4] == ("HKEY_LOCAL_MACHINE", "Software\\Synth\\Game") and o1[0][5] == "static", show
    w = sorted(r for r in rows if 0x401040 <= r[0] < 0x401090 and r[1] == "RegQueryValueExA")
    assert [(r[4], r[5]) for r in w] == [("N1", "static"), ("N2", "static")], show
    assert all(r[2:4] == ("HKEY_CURRENT_USER", "Control Panel\\X") and r[7].startswith("via 0x004010") for r in w), show
    d = [r for r in rows if 0x4010d0 <= r[0] < 0x401110]
    assert len(d) == 1 and d[0][1] == "RegQueryValueExA" and d[0][2] == "HKEY_LOCAL_MACHINE" and d[0][5] == "dynamic" \
        and "field 0x8 of parameter 0" in d[0][6], show
    t = [r for r in rows if 0x401110 <= r[0] < 0x401150]
    assert len(t) == 1 and t[0][1] == "RegSetValueExA" and t[0][5] == "dynamic" and t[0][6].startswith("indirect"), show
    x = [r for r in rows if 0x401150 <= r[0] < 0x401190]
    assert len(x) == 1 and x[0][5] == "dynamic" and "field 0x10 of this" in x[0][6] and "vtable 0x00403200 slot 1" in x[0][7] \
        and "receivers" in x[0][7], show
    y = [r for r in rows if 0x401190 <= r[0] < 0x4011d0]
    assert [(r[1], r[5]) for r in sorted(y)] == [("RegOpenKeyA", "static"), ("RegQueryValueExA", "dynamic")] and \
        "vtable 0x00403220 slot 0" in y[1][7], show
    assert len(rows) == 1 + 1 + 1 + 2 + 1 + 1 + 1 + 2, show  # F1 open+query, W open, W query x2, D, T, X, Y open+query
    m.imports.append(("advapi32.dll", "RegSaveKeyA", SAVE))
    assert regsites.gate_domain({"synth.exe": m}) == [("synth.exe", "advapi32.dll", "RegSaveKeyA")]
    print("regsites selftest ok: %d rows" % len(rows))
    if len(sys.argv) > 1:
        e = regsites.check_roles(sys.argv[1])
        print("\n".join(e) or "roles ok")
        return 1 if e else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
