#!/usr/bin/env python3
"""xc_missing.py - the initializers of the player's exe that the guest has no native for.

    python3 -I tests/s5/xc_missing.py <FalloutNV.exe> <link out/addrmap.tsv> [--thunks]

Reads the exe's __xc table (the span S10's IMAGE mode finds, default 0x00fdf4a8..0x010113c0 for 1.4.0.525), keeps
the slots inside the initializer group .text$yc (0x00f34eb0..0x00fc7a70, docs/STARTUP.md) and lists the addresses
without an F row in the address map. S10's runner counts these as missing and skips them, so the objects they build
are never constructed. Each is classified by its first bytes. A `mov ecx, imm32; jmp rel32` thunk is printed with
its object and target, and with --thunks as the `before-winmain <native> <object>` line of tests/s5/known_faults.txt
when the target has a native. Prints nothing derived from the decomp beyond the address map's names.
"""
import argparse
import collections
import struct
import sys

GROUP = (0x00F34EB0, 0x00FC7A70)
XC = (0x00FDF4A8, 0x010113C0)


def sections(d):
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    n = struct.unpack_from("<H", d, pe + 6)[0]
    opt = struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
    out = []
    for i in range(n):
        name, vs, va, rs, ra = struct.unpack_from("<8sIIII", d, pe + 24 + opt + 40 * i)
        out.append((va + base, max(vs, rs), ra))
    return out


def reader(d):
    secs = sections(d)

    def rd(a, size):
        for va, sz, ra in secs:
            if va <= a < va + sz:
                return d[ra + a - va:ra + a - va + size]
        return b""
    return rd


def shape(b):
    if len(b) >= 10 and b[0] == 0xB9 and b[5] == 0xE9:
        return "thunk"
    if len(b) >= 6 and b[0] == 0x68 and b[5] == 0xE8:
        return "push imm32; call"
    if b[:3] == b"\x55\x8b\xec":
        return "frame (push ebp; mov ebp, esp)"
    if b[:1] == b"\xd9" or b[:1] == b"\xdd":
        return "x87 load/store"
    if b[:1] == b"\xa1":
        return "mov eax, [imm32]"
    return "other"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("exe")
    ap.add_argument("addrmap")
    ap.add_argument("--thunks", action="store_true", help="print before-winmain lines for the thunks")
    a = ap.parse_args(argv)
    d = open(a.exe, "rb").read()
    rd = reader(d)
    amap = {}
    for line in open(a.addrmap, errors="replace"):
        f = line.rstrip("\n").split("\t")
        if len(f) > 3 and not f[0].startswith("#"):
            amap[int(f[0], 16)] = (f[1], f[3])
    raw = rd(XC[0], XC[1] - XC[0])
    slots = [struct.unpack_from("<I", raw, i)[0] for i in range(0, len(raw) - 3, 4)]
    group = [s for s in slots if GROUP[0] <= s < GROUP[1]]
    missing = sorted({s for s in group if amap.get(s, ("",))[0] != "F"})
    kinds = collections.Counter(shape(rd(s, 16)) for s in missing)
    print(f"xc slots {len(slots)}, in the initializer group {len(group)}, without a native {len(missing)}")
    for k, n in kinds.most_common():
        print(f"  {n:5d}  {k}")
    for s in missing:
        b = rd(s, 16)
        if shape(b) != "thunk":
            continue
        obj = struct.unpack_from("<I", b, 1)[0]
        tgt = (s + 10 + struct.unpack_from("<i", b, 6)[0]) & 0xFFFFFFFF
        kind, native = amap.get(tgt, ("", ""))
        print(f"thunk\t{s:#010x}\tobject {obj:#010x}\tjmp {tgt:#010x}\t{native or 'no native'}")
        if a.thunks and native and kind in ("F", "C"):
            print(f"before-winmain {native} {obj:#010x}      # {s:#010x}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
