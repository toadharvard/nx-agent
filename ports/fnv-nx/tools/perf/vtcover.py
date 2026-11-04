#!/usr/bin/env python3
"""vtcover.py - native coverage of a class's vtables (stream S15-threads, PERF.md 6.9).

    vtcover.py <FalloutNV.exe> <link out/addrmap.tsv> <ClassName> [...]

Finds each class's vftables in the player's exe through MSVC RTTI (type descriptor ".?AV<name>@@" -> complete object
locators -> the vftable after the pointer to each), reads the slots (pointers into .text) and looks each one up in the
link's address map (fnvnx-addrmap: kind F/C/V/X/G = native code). Prints one line per vftable and one per slot.
Output is derived from the player's exe: local only, never committed. Needs pefile.
"""
import struct
import sys

import pefile
exe, amap = sys.argv[1], sys.argv[2]
pe = pefile.PE(exe, fast_load=True); base = pe.OPTIONAL_HEADER.ImageBase
data = open(exe, 'rb').read()
secs = [(s.VirtualAddress + base, s.Misc_VirtualSize, s.PointerToRawData, s.Name.rstrip(b'\0').decode()) for s in pe.sections]
def off(va):
    for v, n, r, _ in secs:
        if v <= va < v + n: return r + va - v
def sec(va):
    for v, n, r, nm in secs:
        if v <= va < v + n: return nm
def fo2va(fo):
    for v, n, r, _ in secs:
        if r <= fo < r + n: return v + fo - r
kinds = {}
for l in open(amap):
    if l.startswith('#'): continue
    f = l.rstrip('\n').split('\t')
    kinds[int(f[0], 16)] = (f[1], f[3])
for name in sys.argv[3:]:
    s = data.find(b'.?AV' + name.encode() + b'@@\0')
    td = fo2va(s - 8)
    tdb = struct.pack('<I', td)
    # COLs: sig 0, then offset, cdOffset, pTD
    i = 0; vts = []
    while True:
        i = data.find(tdb, i + 1)
        if i < 0: break
        col_fo = i - 12
        sig, o, cd = struct.unpack_from('<III', data, col_fo)
        if sig != 0: continue
        col = fo2va(col_fo); cb = struct.pack('<I', col)
        j = 0
        while True:
            j = data.find(cb, j + 1)
            if j < 0: break
            vt = fo2va(j + 4)
            if sec(vt) in ('.rdata',): vts.append((o, vt))
    for o, vt in vts:
        slots = []
        p = off(vt)
        while True:
            a = struct.unpack_from('<I', data, p)[0]
            if sec(a) != '.text' or (slots and kinds.get(vt + 4 * len(slots)) is not None and len(slots) > 0 and False): break
            slots.append(a); p += 4
            if len(slots) > 80: break
        res = [(k, a, kinds.get(a, ('-', ''))) for k, a in enumerate(slots)]
        bad = [r for r in res if r[2][0] not in ('F', 'C', 'V', 'X', 'G')]
        print('%s vftable %08x (offset %d): %d slots, %d without native code' % (name, vt, o, len(slots), len(bad)))
        for k, a, (kd, nat) in res:
            print('   slot %2d %08x %s %s' % (k, a, kd, nat[:70]))
