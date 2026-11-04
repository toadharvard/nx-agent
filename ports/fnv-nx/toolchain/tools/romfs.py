#!/usr/bin/env python3
"""romfs.py - a Switch RomFS image with files in its root directory (what fnv.nro's asset section needs: the shader
pack). Byte-identical to switch-tools build_romfs (romfs.c, 2275606) for the same directory; tests/nx1 checks that.

    romfs.py --build <out.romfs> <file>...      (each file goes to the root under its base name)
    romfs.py --list <image.romfs>

Layout (romfs.c): a 0x50-byte header of ten u64; the file partition at 0x200, files in name order, each at a 0x10
boundary; then, 4-aligned, the directory hash table, the directory table (the root, 0x18 bytes), the file hash table
and the file table (0x20 + name rounded up to 4 per file). Hash: parent ^ 123456789, then for each name byte
h = ror(h, 5) ^ byte; bucket = h % count, count from romfs_get_hash_table_count.
"""
from __future__ import annotations

import os
import struct
import sys

EMPTY = 0xFFFFFFFF
PARTITION = 0x200
HEADER = 0x50


def align(x: int, a: int) -> int:
    return (x + a - 1) & ~(a - 1)


def path_hash(parent: int, name: bytes) -> int:
    h = parent ^ 123456789
    for b in name:
        h = ((h >> 5) | (h << 27)) & 0xFFFFFFFF
        h ^= b
    return h


def table_count(n: int) -> int:
    if n < 3:
        return 3
    if n < 19:
        return n | 1
    c = n
    while any(c % p == 0 for p in (2, 3, 5, 7, 11, 13, 17)):
        c += 1
    return c


def build(files: dict[str, bytes]) -> bytes:
    names = sorted(files)
    for n in names:
        if "/" in n or not n:
            raise ValueError(f"root-only romfs: bad name {n!r}")
    # file partition
    offs, size = {}, 0
    for n in names:
        size = align(size, 0x10)
        offs[n] = size
        size += len(files[n])
    part = bytearray(size)
    for n in names:
        part[offs[n]:offs[n] + len(files[n])] = files[n]
    # file table
    fcount = table_count(len(names))
    fhash = [EMPTY] * fcount
    ftab = bytearray()
    entry = {}
    for n in names:
        entry[n] = len(ftab)
        ftab += bytes(0x20 + align(len(n.encode()), 4))
    for i, n in enumerate(names):
        nb = n.encode()
        sib = entry[names[i + 1]] if i + 1 < len(names) else EMPTY
        h = path_hash(0, nb) % fcount
        struct.pack_into("<IIQQII", ftab, entry[n], 0, sib, offs[n], len(files[n]), fhash[h], len(nb))
        ftab[entry[n] + 0x20:entry[n] + 0x20 + len(nb)] = nb
        fhash[h] = entry[n]
    # the root directory
    dcount = table_count(1)
    dhash = [EMPTY] * dcount
    dtab = bytearray(0x18)
    h = path_hash(0, b"") % dcount
    struct.pack_into("<IIIIII", dtab, 0, 0, EMPTY, EMPTY, entry[names[0]] if names else EMPTY, dhash[h], 0)
    dhash[h] = 0
    dh_off = align(PARTITION + len(part), 4)
    d_off = dh_off + 4 * dcount
    fh_off = d_off + len(dtab)
    f_off = fh_off + 4 * fcount
    hdr = struct.pack("<10Q", HEADER, dh_off, 4 * dcount, d_off, len(dtab), fh_off, 4 * fcount, f_off, len(ftab),
                      PARTITION)
    out = bytearray(f_off + len(ftab))
    out[0:HEADER] = hdr
    out[PARTITION:PARTITION + len(part)] = part
    out[dh_off:dh_off + 4 * dcount] = struct.pack(f"<{dcount}I", *dhash)
    out[d_off:d_off + len(dtab)] = dtab
    out[fh_off:fh_off + 4 * fcount] = struct.pack(f"<{fcount}I", *fhash)
    out[f_off:f_off + len(ftab)] = ftab
    return bytes(out)


def read(img: bytes) -> dict[str, bytes]:
    """Files of the root directory (independent of build(): follows the root's file chain)."""
    (hs, dh_off, dh_sz, d_off, d_sz, fh_off, fh_sz, f_off, f_sz, part) = struct.unpack_from("<10Q", img, 0)
    if hs != HEADER or f_off + f_sz > len(img):
        raise ValueError("not a RomFS image")
    out = {}
    e = struct.unpack_from("<I", img, d_off + 12)[0]   # root's first file
    seen = 0
    while e != EMPTY and seen < 100000:
        _parent, sib, off, size, _h, nlen = struct.unpack_from("<IIQQII", img, f_off + e)
        name = img[f_off + e + 0x20:f_off + e + 0x20 + nlen].decode()
        out[name] = img[part + off:part + off + size]
        e, seen = sib, seen + 1
    return out


def main(argv):
    if len(argv) >= 2 and argv[0] == "--build":
        files = {os.path.basename(p): open(p, "rb").read() for p in argv[2:]}
        open(argv[1], "wb").write(build(files))
        return 0
    if len(argv) == 2 and argv[0] == "--list":
        for n, b in read(open(argv[1], "rb").read()).items():
            print(f"{n}\t{len(b)}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
