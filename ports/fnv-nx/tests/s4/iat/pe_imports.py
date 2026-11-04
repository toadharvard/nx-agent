#!/usr/bin/env python3
"""pe_imports.py - the import slots of a PE32 image (the player's FalloutNV.exe), one per line.

    python3 -I tests/s4/iat/pe_imports.py FalloutNV.exe > imports.txt

Output: slot VA, DLL name as the import directory spells it, and the name or `#<ordinal>`, tab-separated, for the
normal and the delay-load import directories (delay slots are marked `delay`). Reads only the headers and the
import tables; nothing of the image is copied into the output but those names.
"""
import struct
import sys


def main():
    data = open(sys.argv[1], 'rb').read()
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    assert data[pe:pe + 4] == b'PE\0\0'
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    optsz = struct.unpack_from('<H', data, pe + 20)[0]
    opt = pe + 24
    assert struct.unpack_from('<H', data, opt)[0] == 0x10b, 'PE32 only'
    base = struct.unpack_from('<I', data, opt + 28)[0]
    ndirs = struct.unpack_from('<I', data, opt + 92)[0]
    dirs = [struct.unpack_from('<II', data, opt + 96 + 8 * i) for i in range(ndirs)]
    secs = []
    for i in range(nsec):
        s = pe + 24 + optsz + 40 * i
        vsize, va, rawsz, raw = struct.unpack_from('<IIII', data, s + 8)
        secs.append((va, max(vsize, rawsz), raw))

    def off(rva):
        for va, sz, raw in secs:
            if va <= rva < va + sz:
                return raw + rva - va
        raise ValueError(hex(rva))

    def u32(rva):
        return struct.unpack_from('<I', data, off(rva))[0]

    def cstr(rva):
        o = off(rva)
        return data[o:data.index(b'\0', o)].decode('latin-1')

    def thunks(dll, lookup, iat, tag):
        j = 0
        while True:
            t = u32(lookup + 4 * j)
            if not t:
                break
            what = f'#{t & 0xffff}' if t & 0x80000000 else cstr(t + 2)
            print(f'{base + iat + 4 * j:#010x}\t{dll}\t{what}{tag}')
            j += 1

    rva, size = dirs[1] if ndirs > 1 else (0, 0)
    d = 0
    while rva and d + 20 <= size:
        oft, _, _, name, ft = struct.unpack_from('<IIIII', data, off(rva + d))
        if not (oft or name or ft):
            break
        thunks(cstr(name), oft or ft, ft, '')
        d += 20
    rva, size = dirs[13] if ndirs > 13 else (0, 0)
    d = 0
    while rva and d + 32 <= size:
        attr, name, _, iat, lookup = struct.unpack_from('<IIIII', data, off(rva + d))
        if not name:
            break
        fix = (lambda v: v) if attr & 1 else (lambda v: v - base)
        thunks(cstr(fix(name)), fix(lookup), fix(iat), '\tdelay')
        d += 32


if __name__ == '__main__':
    main()
