#!/usr/bin/env python3
# Synthetic NXD9 captures for the d9 families (stream render-lit, RENDER_NATIVE.md §4.5): each frame draws one
# (vertex, pixel) program pair of the player's shader package on a full-screen 16 x 16 vertex grid with random
# constants, vertex attributes and textures, into a float render target. d9_parity.py then renders the file with
# the translated programs and with the hand-written families and compares every draw.
#
#   python3 -I render/verify/shaders/d9_synth.py --sdp shaderpackage012.sdp --index <sdp2dksh dir>/index.tsv \
#       [--seeds 4] -o synth.nxd9 [VS PS ...]        (default pairs below)
#
# Real captures leave draws out (depth-rejected or off-screen) and cover a few constant sets; this drives every
# input the families read across their ranges: light counts 0..1+NPT (also fractional), translucent and opaque
# materials, the toggles, fog near/far/power, unit and non-unit frames. The output derives from the player's
# package (the bytecode), so it stays local, like the package.
import argparse, math, random, struct

PAIRS = [('SLS2022.vso', 'SLS2031.pso'), ('SLS2022.vso', 'SLS2033.pso'), ('SLS2025.vso', 'SLS2034.pso'),
         ('SLS2025.vso', 'SLS2036.pso'), ('SLS2020.vso', 'SLS2029.pso'), ('SLS2020.vso', 'SLS2030.pso'),
         ('SLS2050.vso', 'SLS2057.pso'), ('SLS1017.vso', 'SLS1030.pso')]
W, H, G = 128, 96, 16


def tag(s):
    return struct.unpack('<I', s.encode())[0]


class Out:
    def __init__(self):
        self.b = bytearray(b'NXD9\0\0\0\1')

    def rec(self, t, payload):
        self.b += struct.pack('<II', tag(t), len(payload)) + payload + b'\0' * (-len(payload) % 4)


def entries(path):
    d = open(path, 'rb').read()
    n = struct.unpack_from('<I', d, 4)[0]
    at, out = 12, {}
    for _ in range(n):
        name = d[at:at + 256].split(b'\0')[0].decode()
        sz = struct.unpack_from('<I', d, at + 256)[0]
        out.setdefault(name, d[at + 260:at + 260 + sz])
        at += 260 + sz
    return out


def unit(v):
    l = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / l for x in v]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pairs', nargs='*')
    ap.add_argument('--sdp', required=True)
    ap.add_argument('--index', required=True)
    ap.add_argument('--seeds', type=int, default=4)
    ap.add_argument('-o', required=True)
    a = ap.parse_args()
    pairs = list(zip(a.pairs[0::2], a.pairs[1::2])) or PAIRS
    prog = entries(a.sdp)
    pkg = a.sdp.split('/')[-1].split('.')[0]
    hashes = {}
    for line in open(a.index):
        p = line.rstrip('\n').split('\t')
        if len(p) > 2 and (p[1] == pkg or p[1].endswith(pkg) or pkg in p[1]):
            hashes.setdefault(p[2], int(p[0], 16))
    o = Out()
    o.rec('DEV ', struct.pack('<4I', W, H, 21, 0))
    o.rec('TEX ', struct.pack('<6I', 1, 113, W, H, 1, 1))              # A16B16G16R16F render target
    o.rec('SURF', struct.pack('<6I', 1, 1, 113, W, H, 1))
    o.rec('SRT ', struct.pack('<2I', 0, 1))
    o.rec('SDS ', struct.pack('<I', 0))
    for rs, v in ((7, 0), (22, 1), (27, 0), (15, 0), (28, 0), (168, 15)):  # no depth, no cull, no blend/alpha test/fog
        o.rec('RS  ', struct.pack('<2I', rs, v))
    # vertex grid: position, normal, tangent, binormal (a random frame per vertex), uv, colour
    decl = [(0, 2, 0, 0), (12, 2, 3, 0), (24, 2, 6, 0), (36, 2, 7, 0), (48, 1, 5, 0), (56, 4, 10, 0)]
    o.rec('DECL', struct.pack('<2I', 1, len(decl)) + b''.join(struct.pack('<HHBBBB', 0, off, t, 0, u, i) for off, t, u, i in decl))
    ib = b''.join(struct.pack('<3H', *t) for y in range(G - 1) for x in range(G - 1)
                  for t in ((y * G + x, y * G + x + 1, (y + 1) * G + x), (y * G + x + 1, (y + 1) * G + x + 1, (y + 1) * G + x)))
    ib += struct.pack('<3H', 0, 0, 0)   # a degenerate triangle: draw 0 of each frame, so draw 1 has a target to diff
    o.rec('IB  ', struct.pack('<3I', 1, len(ib), 101))
    o.rec('IBU ', struct.pack('<3I', 1, 0, len(ib)) + ib)
    o.rec('SIDX', struct.pack('<I', 1))
    sid = 0
    tid = 10
    for vsn, psn in pairs:
        vs, ps = prog[vsn], prog[psn]
        sid += 1
        for t, name, bc in (('VSH ', vsn, vs), ('PSH ', psn, ps)):
            h = hashes[name]
            o.rec(t, struct.pack('<4I', sid, h & 0xFFFFFFFF, h >> 32, len(bc)) + bc + b'\0' * (-len(bc) % 4) + struct.pack('<I', 0))
        for seed in range(a.seeds):
            r = random.Random(hash((vsn, psn, seed)) & 0xFFFFFFFF)
            vb = bytearray()
            for y in range(G):
                for x in range(G):
                    px, py = -1.05 + 2.1 * x / (G - 1), -1.05 + 2.1 * y / (G - 1)
                    n = unit([r.uniform(-0.5, 0.5), r.uniform(-0.5, 0.5), 1.0])
                    t = unit([1.0, r.uniform(-0.3, 0.3), r.uniform(-0.3, 0.3)])
                    b = unit([t[1] * n[2] - t[2] * n[1], t[2] * n[0] - t[0] * n[2], t[0] * n[1] - t[1] * n[0]])
                    if seed % 2:   # non-unit frames on odd seeds
                        t = [c * 1.3 for c in t]; b = [c * 0.8 for c in b]
                    vb += struct.pack('<3f', px, py, r.uniform(0.1, 0.9)) + struct.pack('<9f', *(n + t + b))
                    vb += struct.pack('<2f', x / (G - 1) * 2, y / (G - 1) * 2) + bytes(r.getrandbits(8) for _ in range(4))
            o.rec('VB  ', struct.pack('<2I', sid * 100 + seed, len(vb)))
            o.rec('VBU ', struct.pack('<3I', sid * 100 + seed, 0, len(vb)) + bytes(vb))
            o.rec('STRM', struct.pack('<4I', 0, sid * 100 + seed, 0, 60))
            o.rec('SDCL', struct.pack('<I', 1))
            # textures: 2D 16 x 16 on samplers 0-3 (wrap, linear), a 8 x 8 cube on 1 for the env map
            for s in range(4):
                tid += 1
                cube = s == 1 and b'ObjToCubeSpace' in vs
                lv = bytes(r.getrandbits(8) for _ in range((6 if cube else 1) * (8 * 8 if cube else 16 * 16) * 4))
                dim = 8 if cube else 16
                o.rec('TEX ', struct.pack('<6I', tid, 21, dim, dim, 1, 2 if cube else 0) + struct.pack('<I', len(lv)) + lv)
                o.rec('STEX', struct.pack('<2I', s, tid))
                for st, v in ((5, 2), (6, 2), (7, 0)):   # MAG/MIN linear, MIP none
                    o.rec('SS  ', struct.pack('<3I', s, st, v))
            o.rec('SVS ', struct.pack('<I', sid))
            o.rec('SPS ', struct.pack('<I', sid))
            vc = {i: [0.0] * 4 for i in range(32)}
            vc[0], vc[1], vc[2], vc[3] = [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]
            vc[14] = [r.uniform(0.3, 2.0), r.uniform(0.5, 2.0), r.choice([0.6, 1.0, 2.0]), 0]
            vc[15] = [r.random(), r.random(), r.random(), 0]
            vc[16] = [r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(1, 3), 0]
            npt = 5
            lights = r.choice([0.0, 1.0, 2.0, 2.5, 3.0, 4.0, 6.0])
            vc[17] = [0, 0, lights, 0]
            vc[25] = unit([r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(0.2, 1)]) + [r.uniform(0, 1.5)]
            pts = [[r.uniform(-1.2, 1.2), r.uniform(-1.2, 1.2), r.uniform(0.2, 1.5), r.uniform(0.6, 3.0)] for _ in range(npt)]
            for i in range(npt):
                vc[26 + i] = pts[i]
            m = [unit([r.uniform(-1, 1) for _ in range(3)]) for _ in range(3)]
            vc[8], vc[9], vc[10] = m[0] + [r.uniform(-1, 1)], m[1] + [r.uniform(-1, 1)], m[2] + [r.uniform(-1, 1)]
            pc = {i: [0.0] * 4 for i in range(32)}
            pc[1] = [r.uniform(0, 0.3), r.uniform(0, 0.3), r.uniform(0, 0.3), r.choice([1.0, 0.6])]
            pc[2] = [0, 0, 0, lights]
            for i in range(1 + npt):
                pc[3 + i] = [r.random(), r.random(), r.random(), 0]
            for i in range(npt):
                pc[19 + i] = pts[i]
            pc[27] = [r.choice([0.0, 1.0]), r.choice([0.0, 1.0]), r.uniform(1, 40), r.uniform(0, 0.8)]
            if b'ObjToCubeSpace' in vs:
                pc[27] = [r.choice([0.0, 1.0]), 0.0, r.uniform(0, 2), r.choice([0.0, 0.5, 1.0])]
            o.rec('VSCF', struct.pack('<2I', 0, 32) + b''.join(struct.pack('<4f', *vc[i]) for i in range(32)))
            o.rec('PSCF', struct.pack('<2I', 0, 32) + b''.join(struct.pack('<4f', *pc[i]) for i in range(32)))
            o.rec('CLR ', struct.pack('<IIfII', 1, 0xFF102030, 1.0, 0, 0))
            o.rec('DRWI', struct.pack('<IiIIII', 4, 0, 0, G * G, 6 * (G - 1) * (G - 1), 1))
            o.rec('DRWI', struct.pack('<IiIIII', 4, 0, 0, G * G, 0, 2 * (G - 1) * (G - 1)))
            o.rec('PRES', b'')
    open(a.o, 'wb').write(o.b)
    print('%s: %d pairs x %d seeds = %d frames' % (a.o, len(pairs), a.seeds, len(pairs) * a.seeds))


if __name__ == '__main__':
    main()
