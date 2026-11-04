#!/usr/bin/env python3
"""nxd9norm.py - make a d9cap journal conform to RENDER_NATIVE.md 8.3 before both renderers read it.

    nxd9norm.py <in.nxd9> <out.nxd9>

d9cap writes a managed texture's TEX record with 0-byte levels: D3D9 creates the texture empty, and its contents
arrive later as TEXU / TEXC records. 8.3 names 0-byte levels only for render targets. nxd9play accepts them, but
nx_render refuses a TEXC into a cube created that way ("TEXC: face bytes differ from the level's", S16 R3). This
rewrite gives every empty level of a non-render-target TEX its full size in zero texels (cube: six faces). The
contents that follow are unchanged, and both sides read the same normalized file, so the comparison stays fair.
Every other record is copied byte for byte. Prints how many levels were filled.
"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nxd9io

BC = {0x31545844: 8, 0x32545844: 16, 0x33545844: 16, 0x34545844: 16, 0x35545844: 16,   # DXT1-5
      0x31495441: 8, 0x32495441: 16}                                                   # ATI1, ATI2
BPP = {20: 3, 21: 4, 22: 4, 23: 2, 24: 2, 25: 2, 26: 2, 28: 1, 29: 1, 30: 2, 31: 4, 32: 4, 33: 4, 34: 4, 35: 4,
       36: 8, 40: 1, 41: 1, 50: 1, 51: 2, 52: 1, 60: 2, 61: 2, 62: 4, 63: 4, 64: 2, 67: 4, 81: 2, 110: 8,
       111: 2, 112: 4, 113: 8, 114: 4, 115: 8, 116: 16, 117: 2}


def level_bytes(fmt, w, h, level):
    lw, lh = max(1, w >> level), max(1, h >> level)
    if fmt in BC:
        return ((lw + 3) // 4) * ((lh + 3) // 4) * BC[fmt]
    if fmt in BPP:
        return lw * lh * BPP[fmt]
    return None


def norm_tex(p):
    tid, fmt, w, h, lv, fl = struct.unpack_from('<6I', p, 0)
    if fl & 1:            # render target: 0-byte levels are the spec
        return bytes(p), 0
    out, q, filled = bytearray(p[:24]), 24, 0
    faces = 6 if fl & 2 else 1
    for l in range(max(1, lv)):
        if q + 4 > len(p):
            break
        b = struct.unpack_from('<I', p, q)[0]
        data = bytes(p[q + 4:q + 4 + b])
        q += 4 + ((b + 3) & ~3)
        if b == 0:
            n = level_bytes(fmt, w, h, l)
            if n:
                data, b, filled = b'\0' * (n * faces), n * faces, filled + 1
        out += struct.pack('<I', b) + data + b'\0' * ((-b) % 4)
    out += bytes(p[q:])
    return bytes(out), filled


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    w, filled = nxd9io.Writer(sys.argv[2]), 0
    for t, p in nxd9io.records(sys.argv[1]):
        if t == 'TEX ':
            p, f = norm_tex(p)
            filled += f
        w.rec(t, p)
    w.close()
    print(f'nxd9norm: {filled} empty texture levels filled')
    return 0


if __name__ == '__main__':
    sys.exit(main())
