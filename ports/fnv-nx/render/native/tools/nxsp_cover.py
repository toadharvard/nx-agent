#!/usr/bin/env python3
"""Coverage of a translated.nxsp (nxsp_pack.py) for real captures: every vertex / pixel program an NXD9 capture creates
(VSH / PSH records, keyed by fnvnx_shader_hash) must be in the pack with a precompiled DKSH, and every draw's program
pair must be covered (NX1 check 9 on the scenes: no runtime compile).

    python3 -I render/native/tools/nxsp_cover.py <translated.nxsp> <capture.nxd9> [...]

Prints, per capture: programs created, programs without DKSH in the pack, draws, draws whose VS or PS lacks DKSH.
Exit 1 when anything lacks DKSH. Captures and packs are game-derived: local data only."""
import struct, sys, zlib


def pack_rows(path):
    d = open(path, 'rb').read()
    magic, ver, n, idx, _, _ = struct.unpack_from('<4sIIII12s', d, 0)
    if magic != b'NXSP' or ver != 1:
        sys.exit('%s: not an NXSP v1 pack' % path)
    dksh = set()
    for i in range(n):
        name, fam, var, st, off, size, crc = struct.unpack_from('<48s24s16sIIII', d, idx + 104 * i)
        if fam.rstrip(b'\0') == b'translated' and size and d[off:off + 4] == b'DKSH' and zlib.crc32(d[off:off + size]) == crc:
            dksh.add(int(name.rstrip(b'\0'), 16))
    return dksh


def capture(path, dksh):
    d = open(path, 'rb').read()
    if d[:8] != b'NXD9\0\0\0\1':
        sys.exit('%s: not an NXD9 capture' % path)
    at, shaders, vs, ps = 8, {}, 0, 0
    draws = bad_draws = 0
    missing = set()
    while at + 8 <= len(d):
        tag, ln = struct.unpack_from('<4sI', d, at)
        p = at + 8
        at += 8 + ((ln + 3) & ~3)
        if tag in (b'VSH ', b'PSH '):
            i, lo, hi = struct.unpack_from('<3I', d, p)
            shaders[(tag, i)] = hi << 32 | lo
        elif tag == b'SVS ':
            vs = struct.unpack_from('<I', d, p)[0]
        elif tag == b'SPS ':
            ps = struct.unpack_from('<I', d, p)[0]
        elif tag in (b'DRAW', b'DRWI'):
            draws += 1
            hv, hp = shaders.get((b'VSH ', vs)), shaders.get((b'PSH ', ps))
            if hv is None or hp is None or hv not in dksh or hp not in dksh:
                bad_draws += 1
    progs = set(shaders.values())
    missing = sorted(h for h in progs if h not in dksh)
    return len(progs), missing, draws, bad_draws


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    dksh = pack_rows(sys.argv[1])
    rc = 0
    for c in sys.argv[2:]:
        n, miss, draws, bad = capture(c, dksh)
        print('%s: programs %d, without DKSH %d; draws %d, draws needing a compile %d' % (c.split('/')[-1], n, len(miss),
                                                                                       draws, bad))
        for h in miss[:10]:
            print('  missing %016x' % h)
        rc |= 1 if miss or bad else 0
    return rc


if __name__ == '__main__':
    sys.exit(main())
