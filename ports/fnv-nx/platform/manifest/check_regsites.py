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
M_A, M_B, M_S, M_E = 0x401280, 0x4012c0, 0x401300, 0x401540
VA, VB, VD, VP, VS, VE = 0x403304, 0x403324, 0x403344, 0x403384, 0x4033a4, 0x4033c4


def build():
    img, rel, cur = bytearray(0x8000), [], [0x403100]
    rt = [0x405000]

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
    tds = {}

    def alloc(n):
        a = rt[0]
        rt[0] += (n + 3) & ~3
        return a

    def w32(va, v, reloc=False):
        put(va, struct.pack("<I", v))
        if reloc:
            rel.append(va)

    def td_of(name):
        if name not in tds:
            a = alloc(8 + len(name) + 8)
            put(a + 8, (".?AV%s@@" % name).encode() + b"\0")
            tds[name] = a
        return tds[name]

    def klass(name, bases, vts):
        """RTTI of a class: bases = names after the class itself; vts = [(vtable address, offset in the object, slot function addresses)]."""
        chd, bca = alloc(16), alloc(4 * (1 + len(bases)))
        for k, b in enumerate([name] + bases):
            bcd = alloc(24)
            w32(bcd, td_of(b), True)
            w32(bcd + 8, 0)
            w32(bca + 4 * k, bcd, True)
        w32(chd + 8, 1 + len(bases))
        w32(chd + 12, bca, True)
        for v, off, slots in vts:
            col = alloc(20)
            w32(col + 4, off)
            w32(col + 12, td_of(name), True)
            w32(col + 16, chd, True)
            w32(v - 4, col, True)
            for k, f in enumerate(slots):
                w32(v + 4 * k, f, True)

    klass("Q", [], [(0x403204, 0, [0x401090, 0x401150])])  # X is slot 1 of Q: no constructor, no writer of its field
    klass("R", [], [(0x403224, 0, [0x401190])])  # Y is a copy of W also entered through this slot
    data_word = 0x403240  # a relocated code word in data that no vtable owns
    put(data_word, struct.pack("<I", 0x4013f0))
    rel.append(data_word)
    nm_a, nm_d, nm_mi, sw2 = string("FieldName"), string("DerivedName"), string("MIName"), string("Software\\Synth\\Cls")
    cells = {k: 0x403360 + 4 * n for n, k in enumerate("ABDME")}
    method = lambda va: asm(va, pro + [b"\x8b\xf9\x83\xec\x04\x8d\x45\xfc\x50\x68", ("abs", sw2), b"\x68\x01\x00\x00\x80\xff\x15", ("abs", OPEN),
                                      b"\x8b\x4d\xfc"] + z4 + [b"\xff\x77\x08\x51\xff\x15", ("abs", QUERY), b"\x8b\xe5\x5d\xc3"])
    DUMMY = 0x401340
    for va in (M_A, M_B, M_S):
        method(va)
    asm(DUMMY, [b"\xc3"])
    # constructors (return this): A stores "FieldName" at +8, B stores its parameter, D calls A's constructor and stores "DerivedName",
    # (all addresses here are of the synthetic image built by build(), not exe addresses) M has two vtables (offsets 0 and 8) and stores "MIName" at +0x10, E is created and its pointer is stored through an unknown base
    C_A, C_B, C_D, C_M, C_E = 0x401380, 0x4013a0, 0x4013c0, 0x401400, 0x401440
    asm(C_A, pro + [b"\x8b\xc1\xc7\x00", ("abs", VA), b"\xc7\x40\x08", ("abs", nm_a), b"\x5d\xc3"])
    asm(C_B, pro + [b"\x8b\xc1\xc7\x00", ("abs", VB), b"\x8b\x55\x08\x89\x50\x08\x5d\xc2\x04\x00"])
    asm(C_D, pro + [b"\x8b\xf1\xe8", ("rel", C_A), b"\xc7\x06", ("abs", VD), b"\xc7\x46\x08", ("abs", nm_d), b"\x8b\xc6\x5d\xc3"])
    asm(C_M, pro + [b"\x8b\xc1\xc7\x00", ("abs", VP), b"\xc7\x40\x08", ("abs", VS), b"\xc7\x40\x10", ("abs", nm_mi), b"\x5d\xc3"])
    asm(C_E, pro + [b"\x8b\xc1\xc7\x00", ("abs", VE), b"\x5d\xc3"])
    for n, (k, c) in enumerate(zip("ABDM", (C_A, C_B, C_D, C_M))):  # creators: construct, store the pointer into a data cell
        asm(0x401480 + 0x20 * n, pro + [b"\xe8", ("rel", c), b"\xa3", ("abs", cells[k]), b"\x5d\xc3"])
    asm(0x401500, pro + [b"\xe8", ("rel", C_E), b"\x8b\x15", ("abs", cells["E"]), b"\x89\x02\x5d\xc3"])
    method(M_E)
    klass("A", [], [(VA, 0, [M_A])])
    klass("B", [], [(VB, 0, [M_B])])
    klass("D", ["A"], [(VD, 0, [M_A])])
    klass("M", [], [(VP, 0, [DUMMY]), (VS, 8, [M_S])])
    klass("E", [], [(VE, 0, [M_E])])
    # entries of kinds jmp and code word to a parameter wrapper
    asm(0x401580, pro + [b"\x8b\x45\x08\x50\x6a\x00\x6a\x00\x6a\x00\x6a\x00\x50\x68\x02\x00\x00\x80\xff\x15", ("abs", QUERY), b"\x5d\xc2\x04\x00"])
    asm(0x4015c0, [b"\xe9", ("rel", 0x401580)])
    asm(0x4015e0, [b"\xb8", ("abs", 0x401580), b"\xc3"])
    asm(0x4013f0, [b"\xc3"])
    m = pemanifest.Mod.__new__(pemanifest.Mod)
    m.name, m.base, m.img, m.relocs = "synth.exe", BASE, bytes(img), sorted(rel)
    m.code = [(0x401000, 0x800)]
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
    assert len(x) == 1 and x[0][5] == "dynamic" and "no writer of field 0x10 of Q" in x[0][6] and "receiver Q" in x[0][7], show
    y = [r for r in rows if 0x401190 <= r[0] < 0x4011d0]
    assert [(r[1], r[5]) for r in sorted(y)] == [("RegOpenKeyA", "static"), ("RegQueryValueExA", "dynamic")] and \
        "vtable 0x00403224 slot 0" in y[1][7], show
    # class closure: A (constant writer), D (derived: the base constructor's writer and its own), M (second vtable at offset 8),
    # B (unbound writer), E (pointer stored through an unknown base)
    q = lambda lo: sorted({(r[4], r[5]) for r in rows if lo <= r[0] < lo + 0x40 and r[1] == "RegQueryValueExA"})
    assert q(M_A) == [("DerivedName", "static"), ("FieldName", "static")], show  # slot of A and of D: the receivers' value sets
    assert q(M_S) == [("MIName", "static")], show
    b = [r for r in rows if M_B <= r[0] < M_B + 0x40 and r[1] == "RegQueryValueExA"]
    assert len(b) == 1 and b[0][5] == "dynamic" and "stores a value that is not bound" in b[0][6] and "receiver B" in b[0][7], show
    e = [r for r in rows if M_E <= r[0] < M_E + 0x40 and r[1] == "RegQueryValueExA"]
    assert len(e) == 1 and e[0][5] == "dynamic" and "pointer stored through an untyped base" in e[0][6], show
    j = [r for r in rows if 0x401580 <= r[0] < 0x4015c0]
    assert len(j) == 1 and j[0][5] == "dynamic" and "jmp from 0x004015c0" in j[0][7] and "address word in code at 0x004015e1" in j[0][7], show
    assert len([r for r in rows if r[5] == "static"]) == 14, show
    # RTTI: classes, bases, offsets, slots
    rt = regsites.Rtti(m)
    assert rt.name(rt.complete(VS)[0]) == ".?AVM@@" and rt.complete(VS)[1] == 8 and rt.complete(VP)[0] == rt.complete(VS)[0], show
    assert rt.word_kind(0x403204 + 4)[:1] == ("vtable",) and rt.word_kind(0x403240)[0] == "data", show
    assert rt.derived_of(rt.complete(VA)[0]) == sorted({rt.complete(VA)[0], rt.complete(VD)[0]}), show
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
