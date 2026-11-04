#!/usr/bin/env python3
"""synth.py - S16-compare's synthetic scene set: NXD9 captures we author ourselves (no game content), rendered by both
the reference (nxd9play.exe under Wine) and the candidate (nx_render d3d9). Committed as this generator only.

    synth.py build --asm <s6b_asm> --sdp2dksh <sdp2dksh> --out <dir> [--only a,b]
    synth.py list

Shaders are D3D9 assembly, assembled with tests/s6b's s6b_asm (MojoShader's assembler) and translated with S6b's
sdp2dksh --glsl, so the GLSL embedded in each VSH/PSH record is exactly what the port runs. Each scene covers one
group of D3D9 features the engine uses (README.md, "Synthetic scenes"); every scene has 2 frames, the second one
changing state against the first, so state carried across PRES is exercised too.
"""
import argparse, math, os, struct, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nxd9io

W, H = 320, 240
# D3D9 enums used below (d3d9types.h values)
RS = dict(ZENABLE=7, ZWRITEENABLE=14, ALPHATESTENABLE=15, SRCBLEND=19, DESTBLEND=20, CULLMODE=22, ZFUNC=23,
          ALPHAREF=24, ALPHAFUNC=25, ALPHABLENDENABLE=27, FOGENABLE=28, FOGCOLOR=34, FOGTABLEMODE=35, FOGSTART=36,
          FOGEND=37, STENCILENABLE=52, STENCILFAIL=53, STENCILZFAIL=54, STENCILPASS=55, STENCILFUNC=56,
          STENCILREF=57, STENCILMASK=58, STENCILWRITEMASK=59, FOGVERTEXMODE=140, COLORWRITEENABLE=168, BLENDOP=171,
          SCISSORTESTENABLE=174, SLOPESCALEDEPTHBIAS=175, SRGBWRITEENABLE=194, DEPTHBIAS=195,
          SEPARATEALPHABLENDENABLE=206, SRCBLENDALPHA=207, DESTBLENDALPHA=208)
SS = dict(ADDRESSU=1, ADDRESSV=2, MAGFILTER=5, MINFILTER=6, MIPFILTER=7, MIPMAPLODBIAS=8, SRGBTEXTURE=11)
A8R8G8B8, X8R8G8B8, D24S8, A16B16G16R16F = 21, 22, 75, 113
DXT1, DXT5 = 0x31545844, 0x35545844
POSITION, NORMAL, TEXCOORD, COLOR = 0, 3, 5, 10
FLOAT2, FLOAT3, D3DCOLOR = 1, 2, 4


def fbits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


# ---- shaders (D3D9 assembly; authored here) -------------------------------------------------------------------------
SHADERS = {
    'vs11_rel': """vs_1_1
dcl_position v0
def c21, 0.0, 1.0, 0.0, 1.0
mov oPos, v0
mov a0.x, c4.x
mov oD0, c20[a0.x]
""",
    'vs20_rel': """vs_2_0
dcl_position v0
def c21, 0.0, 1.0, 0.0, 1.0
mov oPos, v0
mova a0.x, c4.x
mov oD0, c20[a0.x]
""",
    'vs20_def': """vs_2_0
dcl_position v0
def c21, 0.0, 1.0, 0.0, 1.0
mov oPos, v0
mov oD0, c21
""",
    'vs20_rel_arr': """vs_2_0
dcl_position v0
def c21, 0.0, 1.0, 0.0, 1.0
mov oPos, v0
mova a0.x, c4.x
add r0, c20[a0.x], c19[a0.x]
mul oD0, r0, c20.x
""",
    'ps20_vcolor': """ps_2_0
dcl v0
mov oC0, v0
""",
    'vs3_ct': """vs_3_0
dcl_position v0
dcl_color v1
dcl_texcoord v2
dcl_position o0
dcl_color o1
dcl_texcoord o2
m4x4 o0, v0, c0
mov o1, v1
mov o2, v2
""",
    'ps3_vcolor': """ps_3_0
dcl_color v0
mov oC0, v0
""",
    'ps3_tex': """ps_3_0
dcl_color v0
dcl_texcoord v1.xy
dcl_2d s0
texld r0, v1, s0
mul oC0, r0, v0
""",
    'ps3_tex2': """ps_3_0
def c1, 0.5, 0.5, 0.5, 1.0
dcl_texcoord v1.xy
dcl_2d s0
dcl_2d s1
texld r0, v1, s0
texld r1, v1, s1
lrp r2, c0.x, r0, r1
mul oC0, r2, c1.w
""",
    'vs2_ct': """vs_2_0
dcl_position v0
dcl_color v1
dcl_texcoord v2
m4x4 r0, v0, c0
mov oPos, r0
mov oD0, v1
mov oT0, v2
mad oFog, r0.z, c4.x, c4.y
""",
    'ps2_tex': """ps_2_0
dcl v0
dcl t0.xy
dcl_2d s0
texld r0, t0, s0
mul r0, r0, v0
mov oC0, r0
""",
    'vs3_lit': """vs_3_0
dcl_position v0
dcl_normal v1
dcl_texcoord v2
dcl_position o0
dcl_texcoord0 o1
dcl_texcoord1 o2
dcl_texcoord2 o3
m4x4 o0, v0, c0
mov o1, v2
m3x3 o2.xyz, v1, c4
mov o2.w, c8.w
m4x4 o3, v0, c4
""",
    'ps3_lit': """ps_3_0
def c4, 0.0, 0.0, 0.0, 1.0
def c5, 16.0, 0.0, 0.0, 0.0
dcl_texcoord0 v0.xy
dcl_texcoord1 v1.xyz
dcl_texcoord2 v2.xyz
dcl_2d s0
nrm r1.xyz, v1
dp3_sat r1.w, r1, c0
mad r2.xyz, c1, r1.w, c2
add r3.xyz, c3, -v2
nrm r4.xyz, r3
add r4.xyz, r4, c0
nrm r5.xyz, r4
dp3_sat r5.w, r1, r5
pow r5.w, r5.w, c5.x
texld r0, v0, s0
mul r0.xyz, r0, r2
mad r0.xyz, c1, r5.w, r0
mov r0.w, c4.w
mov oC0, r0
""",
    'ps3_const': """ps_3_0
mov oC0, c0
""",
}


def assemble(asm_tool, sdp_tool, work):
    """{name: (bytecode, glsl)} for every shader above"""
    out = {}
    gl = os.path.join(work, 'glsl')
    os.makedirs(gl, exist_ok=True)
    blobs = []
    for name, src in SHADERS.items():
        a, b = os.path.join(work, name + '.asm'), os.path.join(work, name + '.bin')
        open(a, 'w').write(src)
        r = subprocess.run([asm_tool, a, b], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f'synth: s6b_asm {name}: {r.stderr.strip()}')
        blobs.append(b)
    r = subprocess.run([sdp_tool, '-o', gl, '--glsl', '--no-dksh', '--blob'] + blobs, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'synth: sdp2dksh: {r.stderr.strip()} {r.stdout.strip()}')
    for name in SHADERS:
        bc = open(os.path.join(work, name + '.bin'), 'rb').read()
        h = nxd9io.fnv1a64(bc)
        out[name] = (bc, open(os.path.join(gl, f'{h:016x}.glsl')).read())
    return out


# ---- textures ---------------------------------------------------------------------------------------------------------
def checker(w, h, a, b, cell):
    px = bytearray()
    for y in range(h):
        for x in range(w):
            c = a if ((x // cell) + (y // cell)) % 2 == 0 else b
            px += bytes((c[2], c[1], c[0], c[3]))   # A8R8G8B8 in memory: B, G, R, A
    return bytes(px)


def gradient(w, h):
    px = bytearray()
    for y in range(h):
        for x in range(w):
            r, g = x * 255 // max(1, w - 1), y * 255 // max(1, h - 1)
            bl = int(127.5 + 127.5 * math.sin(x * 0.4) * math.cos(y * 0.3))
            px += bytes((bl, g, r, (x ^ y) & 0xFF))
    return bytes(px)


def downsample(px, w, h):
    """box filter of an 8-bit 4-channel image to half size (at least 1)"""
    nw, nh = max(1, w // 2), max(1, h // 2)
    o = bytearray()
    for y in range(nh):
        for x in range(nw):
            for c in range(4):
                s = 0
                for dy in (0, 1):
                    for dx in (0, 1):
                        sx, sy = min(w - 1, 2 * x + dx), min(h - 1, 2 * y + dy)
                        s += px[(sy * w + sx) * 4 + c]
                o.append((s + 2) // 4)
    return bytes(o), nw, nh


def mips(px, w, h, levels):
    out = [px]
    for _ in range(levels - 1):
        px, w, h = downsample(px, w, h)
        out.append(px)
    return out


def rgb565(r, g, b):
    return (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)


def dxt1(px, w, h):
    """a plain DXT1 encoder (endpoints = per-block min/max luma colours, nearest of the four palette entries)"""
    out = bytearray()
    for by in range(0, max(1, h), 4):
        for bx in range(0, max(1, w), 4):
            blk = []
            for y in range(4):
                for x in range(4):
                    sx, sy = min(w - 1, bx + x), min(h - 1, by + y)
                    i = (sy * w + sx) * 4
                    blk.append((px[i + 2], px[i + 1], px[i]))
            lum = [r * 3 + g * 6 + b for r, g, b in blk]
            hi, lo = blk[lum.index(max(lum))], blk[lum.index(min(lum))]
            c0, c1 = rgb565(*hi), rgb565(*lo)
            if c0 < c1: c0, c1, hi, lo = c1, c0, lo, hi
            if c0 == c1:
                out += struct.pack('<HHI', c0, c1, 0)
                continue
            exp = lambda c: ((c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31)
            e0, e1 = exp(c0), exp(c1)
            pal = [e0, e1, tuple((2 * a + b) // 3 for a, b in zip(e0, e1)), tuple((a + 2 * b) // 3 for a, b in zip(e0, e1))]
            idx = 0
            for k, c in enumerate(blk):
                d = [sum((a - b) ** 2 for a, b in zip(c, p)) for p in pal]
                idx |= d.index(min(d)) << (2 * k)
            out += struct.pack('<HHI', c0, c1, idx)
    return bytes(out)


# ---- geometry -----------------------------------------------------------------------------------------------------
def persp(fovy, aspect, zn, zf):
    """D3D left-handed perspective, as rows for m4x4 (dst.i = dot(c[i], v))"""
    ys = 1.0 / math.tan(fovy / 2)
    xs = ys / aspect
    return [(xs, 0, 0, 0), (0, ys, 0, 0), (0, 0, zf / (zf - zn), -zn * zf / (zf - zn)), (0, 0, 1, 0)]


def mul(a, b):
    return [tuple(sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)) for i in range(4)]


def rot_y(t):
    c, s = math.cos(t), math.sin(t)
    return [(c, 0, s, 0), (0, 1, 0, 0), (-s, 0, c, 0), (0, 0, 0, 1)]


def rot_x(t):
    c, s = math.cos(t), math.sin(t)
    return [(1, 0, 0, 0), (0, c, -s, 0), (0, s, c, 0), (0, 0, 0, 1)]


def trans(x, y, z):
    return [(1, 0, 0, x), (0, 1, 0, y), (0, 0, 1, z), (0, 0, 0, 1)]


IDENT = [(1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1)]


def vct(x, y, z, argb, u, v):  # FLOAT3 position, D3DCOLOR, FLOAT2 uv: 24 bytes
    return struct.pack('<3fI2f', x, y, z, argb, u, v)


def quad_ct(x0, y0, x1, y1, z, argb, u0=0, v0=0, u1=1, v1=1):
    return [vct(x0, y0, z, argb, u0, v1), vct(x0, y1, z, argb, u0, v0), vct(x1, y1, z, argb, u1, v0),
            vct(x1, y0, z, argb, u1, v1)]


CT_DECL = [(0, 0, FLOAT3, 0, POSITION, 0), (0, 12, D3DCOLOR, 0, COLOR, 0), (0, 16, FLOAT2, 0, TEXCOORD, 0)]
LIT_DECL = [(0, 0, FLOAT3, 0, POSITION, 0), (0, 12, FLOAT3, 0, NORMAL, 0), (0, 24, FLOAT2, 0, TEXCOORD, 0)]


def cube_lit():
    """24 vertices (FLOAT3 pos, FLOAT3 normal, FLOAT2 uv) and 36 indices of a unit cube, clockwise front faces"""
    v, idx = [], []
    faces = [((1, 0, 0), (0, 0, 1), (0, 1, 0)), ((-1, 0, 0), (0, 0, -1), (0, 1, 0)), ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
             ((0, -1, 0), (1, 0, 0), (0, 0, -1)), ((0, 0, 1), (-1, 0, 0), (0, 1, 0)), ((0, 0, -1), (1, 0, 0), (0, 1, 0))]
    for n, t, b in faces:
        base = len(v)
        for su, sv in ((-1, -1), (-1, 1), (1, 1), (1, -1)):
            p = tuple(n[k] + su * t[k] + sv * b[k] for k in range(3))
            v.append(struct.pack('<8f', *p, *n, (su + 1) / 2, (1 - sv) / 2))
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return b''.join(v), idx


# ---- scenes -------------------------------------------------------------------------------------------------------
class Scene:
    def __init__(self, path, sh):
        self.w = nxd9io.Writer(path)
        self.sh, self.ids = sh, {}
        self.w.u32('DEV ', W, H, A8R8G8B8, D24S8)

    def shader(self, name):
        if name not in self.ids:
            sid = len(self.ids) + 1
            bc, gl = self.sh[name]
            h = nxd9io.fnv1a64(bc)
            g = gl.encode()
            p = struct.pack('<4I', sid, h & 0xFFFFFFFF, h >> 32, len(bc)) + bc + b'\0' * ((-len(bc)) % 4)
            self.w.rec('VSH ' if name.startswith('vs') else 'PSH ', p + struct.pack('<I', len(g)) + g)
            self.ids[name] = sid
        return self.ids[name]

    def use(self, vs, ps):
        self.w.u32('SVS ', self.shader(vs))
        self.w.u32('SPS ', self.shader(ps))

    def rs(self, **kv):
        for k, v in kv.items(): self.w.u32('RS  ', RS[k], v)

    def ss(self, s, **kv):
        for k, v in kv.items(): self.w.u32('SS  ', s, SS[k], v)

    def cf(self, kind, start, vecs):
        self.w.rec('VSCF' if kind == 'vs' else 'PSCF',
                   struct.pack('<2I', start, len(vecs)) + b''.join(struct.pack('<4f', *v) for v in vecs))

    def tex(self, tid, fmt, w, h, levels_data, flags=0):
        p = struct.pack('<6I', tid, fmt, w, h, max(1, len(levels_data)), flags)
        for l in levels_data:
            p += struct.pack('<I', len(l)) + l + b'\0' * ((-len(l)) % 4)
        self.w.rec('TEX ', p)

    def decl(self, did, elems):
        self.w.rec('DECL', struct.pack('<2I', did, len(elems)) + b''.join(struct.pack('<HHBBBB', *e) for e in elems))

    def vb(self, bid, data):
        self.w.u32('VB  ', bid, len(data))
        self.w.rec('VBU ', struct.pack('<3I', bid, 0, len(data)) + data)

    def ib(self, bid, idx):
        data = struct.pack('<%dH' % len(idx), *idx)
        self.w.u32('IB  ', bid, len(data), 101)
        self.w.rec('IBU ', struct.pack('<3I', bid, 0, len(data)) + data)

    def clear(self, flags, color, z=1.0, stencil=0, rects=()):
        self.w.rec('CLR ', struct.pack('<2IfII', flags, color, z, stencil, len(rects)) +
                   b''.join(struct.pack('<4i', *r) for r in rects))

    def drwi(self, base, nverts, start, prims):
        self.w.rec('DRWI', struct.pack('<IiIIII', 4, base, 0, nverts, start, prims))

    def present(self):
        self.w.rec('PRES')

    def close(self):
        self.w.close()


def quad_ib(n):
    idx = []
    for q in range(n):
        b = 4 * q
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return idx


def common_ct(s, vs='vs3_ct', ps='ps3_vcolor'):
    s.decl(1, CT_DECL)
    s.w.u32('SDCL', 1)
    s.use(vs, ps)
    s.cf('vs', 0, IDENT)
    s.rs(CULLMODE=1, ZENABLE=0, ZWRITEENABLE=0)
    s.w.u32('SIDX', 1)


def sc_vcolor(s):
    """vertex colours, overlapping quads, depth test and write, cull modes, a rect clear, a scissor"""
    q = []
    q += quad_ct(-0.9, -0.9, 0.3, 0.6, 0.5, 0xFFFF2020)
    q += quad_ct(-0.4, -0.5, 0.8, 0.9, 0.3, 0xFF20FF20)
    q += quad_ct(-0.2, -0.8, 0.9, 0.1, 0.7, 0xFF2040FF)          # behind the others
    # a quad with four different colours: gouraud interpolation
    q += [vct(-1, -1, 0.1, 0xFFFF0000, 0, 0), vct(-1, -0.6, 0.1, 0xFF00FF00, 0, 0), vct(-0.6, -0.6, 0.1, 0xFF0000FF, 0, 0),
          vct(-0.6, -1, 0.1, 0xFFFFFFFF, 0, 0)]
    s.vb(1, b''.join(q)); s.ib(1, quad_ib(4))
    common_ct(s)
    s.w.u32('STRM', 0, 1, 0, 24)
    s.rs(ZENABLE=1, ZWRITEENABLE=1, ZFUNC=4)
    s.clear(7, 0xFF303040, 1.0, 0)
    s.drwi(0, 16, 0, 8)
    s.clear(1, 0xFFFFFF00, rects=[(300, 0, 320, 20)])
    s.present()
    s.clear(7, 0xFF000000, 1.0, 0)
    s.w.rec('SCIS', struct.pack('<4i', 40, 30, 280, 210)); s.rs(SCISSORTESTENABLE=1)
    s.rs(ZFUNC=7)                                                 # GREATEREQUAL: the farthest quad wins now
    s.clear(2, 0, 0.0, 0)
    s.drwi(0, 16, 0, 8)
    s.rs(SCISSORTESTENABLE=0)
    s.present()


def sc_texture(s):
    """A8R8G8B8 and DXT1 textures with mips: point, linear, trilinear, wrap, clamp, mirror, LOD bias"""
    g = gradient(64, 64)
    s.tex(1, A8R8G8B8, 64, 64, mips(g, 64, 64, 7))
    c = checker(64, 64, (240, 200, 40, 255), (30, 60, 200, 255), 8)
    s.tex(2, DXT1, 64, 64, [dxt1(m, 64 >> i, 64 >> i) for i, m in enumerate(mips(c, 64, 64, 7))])
    q = []
    o = 0.3 / 64     # point-sampled quads: no pixel centre falls exactly on a texel edge (README, "Synthetic scenes")
    q += quad_ct(-1, 0, 0, 1, 0.5, 0xFFFFFFFF, o, o, 1 + o, 1 + o)
    q += quad_ct(0, 0, 1, 1, 0.5, 0xFFFFFFFF, -1 + o, -1 + o, 2 + o, 2 + o)      # repeats: address modes
    q += quad_ct(-1, -1, 0, 0, 0.5, 0xFFFFFFFF, 0, 0, 8, 8)      # minified: mips
    q += quad_ct(0, -1, 1, 0, 0.5, 0xFFC0FFC0, 0.2, 0.2, 0.4, 0.4)  # magnified, tinted
    s.vb(1, b''.join(q)); s.ib(1, quad_ib(4))
    common_ct(s, ps='ps3_tex')
    s.w.u32('STRM', 0, 1, 0, 24)
    s.clear(1, 0xFF101010)
    s.w.u32('STEX', 0, 1)
    s.ss(0, MAGFILTER=1, MINFILTER=1, MIPFILTER=0, ADDRESSU=1, ADDRESSV=1)
    s.drwi(0, 4, 0, 2)
    s.ss(0, ADDRESSU=2, ADDRESSV=3)
    s.drwi(4, 4, 0, 2)
    s.w.u32('STEX', 0, 2)
    s.ss(0, MAGFILTER=2, MINFILTER=2, MIPFILTER=2, ADDRESSU=1, ADDRESSV=1)
    s.drwi(8, 4, 0, 2)
    s.drwi(12, 4, 0, 2)
    s.present()
    s.clear(1, 0xFF101010)
    s.w.u32('STEX', 0, 1)
    s.ss(0, MAGFILTER=2, MINFILTER=2, MIPFILTER=1, MIPMAPLODBIAS=fbits(1.0))
    for b in (0, 4, 8, 12): s.drwi(b, 4, 0, 2)
    s.present()


def sc_blend(s):
    """alpha blending (src alpha, additive, multiply, separate alpha), alpha test, colour write mask"""
    q = []
    q += quad_ct(-0.9, -0.9, 0.5, 0.5, 0.5, 0xFFC02020)
    q += quad_ct(-0.5, -0.5, 0.9, 0.9, 0.5, 0x8020C020)
    q += quad_ct(-0.7, 0.1, 0.7, 0.7, 0.5, 0x402060FF)
    q += quad_ct(0.0, -0.95, 0.95, 0.0, 0.5, 0xC0FFFFFF)
    s.vb(1, b''.join(q)); s.ib(1, quad_ib(4))
    common_ct(s)
    s.w.u32('STRM', 0, 1, 0, 24)
    s.clear(1, 0x80404040)
    s.drwi(0, 4, 0, 2)
    s.rs(ALPHABLENDENABLE=1, SRCBLEND=5, DESTBLEND=6)               # SRCALPHA, INVSRCALPHA
    s.drwi(4, 4, 0, 2)
    s.rs(SRCBLEND=2, DESTBLEND=2)                                   # additive
    s.drwi(8, 4, 0, 2)
    s.rs(SRCBLEND=9, DESTBLEND=1)                                   # DESTCOLOR, ZERO: multiply
    s.drwi(12, 4, 0, 2)
    s.present()
    s.clear(1, 0x80404040)
    s.rs(ALPHABLENDENABLE=0, ALPHATESTENABLE=1, ALPHAFUNC=5, ALPHAREF=0x60)
    for b in (0, 4, 8, 12): s.drwi(b, 4, 0, 2)                      # alpha 0x40 quad is discarded
    s.rs(ALPHATESTENABLE=0, ALPHABLENDENABLE=1, SRCBLEND=5, DESTBLEND=6, SEPARATEALPHABLENDENABLE=1,
         SRCBLENDALPHA=2, DESTBLENDALPHA=1, COLORWRITEENABLE=0x5)  # red and blue only
    s.drwi(4, 4, 0, 2)
    s.rs(COLORWRITEENABLE=0xF, SEPARATEALPHABLENDENABLE=0, ALPHABLENDENABLE=0)
    s.present()


def sc_stencil(s):
    """stencil write and test (REPLACE, INCR, EQUAL, NOTEQUAL), depth bias"""
    q = []
    q += quad_ct(-0.6, -0.6, 0.6, 0.6, 0.5, 0xFF808080)
    q += quad_ct(-1, -1, 1, 1, 0.6, 0xFFFF4000)
    q += quad_ct(-1, -0.2, 1, 0.2, 0.5, 0xFF00C0FF)
    s.vb(1, b''.join(q)); s.ib(1, quad_ib(3))
    common_ct(s)
    s.w.u32('STRM', 0, 1, 0, 24)
    s.clear(7, 0xFF000000, 1.0, 0)
    s.rs(STENCILENABLE=1, STENCILFUNC=8, STENCILPASS=3, STENCILREF=1, STENCILMASK=0xFF, STENCILWRITEMASK=0xFF)
    s.drwi(0, 4, 0, 2)                                              # writes 1 in the centre
    s.rs(STENCILFUNC=3, STENCILPASS=1)                              # EQUAL 1
    s.drwi(4, 4, 0, 2)
    s.rs(STENCILFUNC=6)                                             # NOTEQUAL 1
    s.drwi(8, 4, 0, 2)
    s.present()
    s.clear(7, 0xFF000000, 1.0, 0)
    s.rs(STENCILFUNC=8, STENCILPASS=7, STENCILREF=0)                # INCR everywhere twice in the band
    s.drwi(8, 4, 0, 2); s.drwi(8, 4, 0, 2); s.drwi(0, 4, 0, 2)
    s.rs(STENCILFUNC=3, STENCILREF=2, STENCILPASS=1)
    s.drwi(4, 4, 0, 2)
    s.rs(STENCILENABLE=0, ZENABLE=1, ZWRITEENABLE=1, ZFUNC=4)
    s.drwi(4, 4, 0, 2)
    s.rs(DEPTHBIAS=fbits(-0.2))                                     # pulls the 0.6 quad in front of the 0.5 band
    s.drwi(0, 4, 0, 2)
    s.rs(DEPTHBIAS=0)
    s.present()


def sc_rtt(s):
    """render to a texture (SURF of a render-target texture, SRT, its own viewport) and sample it on the back buffer"""
    s.tex(5, A8R8G8B8, 128, 128, [], flags=1)
    s.w.u32('SURF', 7, 1, A8R8G8B8, 128, 128, 5)
    q = []
    q += quad_ct(-1, -1, 1, 1, 0.5, 0xFFFFFFFF)
    q += quad_ct(-0.5, -0.5, 0.5, 0.5, 0.5, 0xFF20A0FF)
    q += quad_ct(-0.9, -0.9, 0.2, 0.2, 0.5, 0xFFFFFFFF, 0, 0, 1, 1)
    q += quad_ct(0.1, 0.1, 0.9, 0.9, 0.5, 0xFFFFA0A0, 0, 0, 1, 1)
    s.vb(1, b''.join(q)); s.ib(1, quad_ib(4))
    common_ct(s)
    s.w.u32('STRM', 0, 1, 0, 24)
    s.w.u32('SDS ', 0)
    s.w.u32('SRT ', 0, 7)
    s.clear(1, 0xFF400000)
    s.drwi(4, 4, 0, 2)
    s.w.u32('SRT ', 0, 0)
    s.w.u32('SDS ', 0xFFFFFFFF)
    s.clear(3, 0xFF202020)
    s.use('vs3_ct', 'ps3_tex')
    s.w.u32('STEX', 0, 5)
    s.ss(0, MAGFILTER=2, MINFILTER=2, MIPFILTER=0, ADDRESSU=3, ADDRESSV=3)
    s.drwi(8, 4, 0, 2)
    s.ss(0, MAGFILTER=1, MINFILTER=1)
    s.drwi(12, 4, 0, 2)
    s.present()
    s.w.u32('SRT ', 0, 7)
    s.w.rec('VIEW', struct.pack('<4I2f', 32, 32, 64, 64, 0.0, 1.0))
    s.use('vs3_ct', 'ps3_vcolor')
    s.w.u32('STEX', 0, 0)
    s.clear(1, 0xFF004000)
    s.drwi(0, 4, 0, 2)
    s.w.u32('SRT ', 0, 0)
    s.clear(1, 0xFF202020)
    s.use('vs3_ct', 'ps3_tex')
    s.w.u32('STEX', 0, 5)
    s.drwi(8, 4, 0, 2)
    s.present()


def sc_lit(s):
    """a lit, textured, perspective cube with depth: vs/ps 3.0 math (m4x4, m3x3, nrm, dp3_sat, pow, lrp-free)"""
    vtx, idx = cube_lit()
    s.vb(2, vtx); s.ib(2, idx)
    s.decl(2, LIT_DECL); s.w.u32('SDCL', 2)
    c = checker(32, 32, (220, 220, 220, 255), (90, 140, 90, 255), 4)
    s.tex(3, A8R8G8B8, 32, 32, mips(c, 32, 32, 6))
    s.use('vs3_lit', 'ps3_lit')
    s.w.u32('STRM', 0, 2, 0, 32); s.w.u32('SIDX', 2)
    s.rs(ZENABLE=1, ZWRITEENABLE=1, ZFUNC=4, CULLMODE=3)
    s.w.u32('STEX', 0, 3)
    s.ss(0, MAGFILTER=2, MINFILTER=2, MIPFILTER=2)
    proj = persp(math.radians(60), W / H, 0.5, 20)
    L = (0.4, 0.7, -0.6); n = math.sqrt(sum(x * x for x in L)); L = tuple(x / n for x in L)
    s.cf('ps', 0, [L + (0,), (0.9, 0.85, 0.7, 1), (0.15, 0.15, 0.2, 1), (0, 0, -4, 1)])
    for f, (ay, ax) in enumerate(((0.6, 0.4), (1.3, -0.5))):
        world = mul(trans(0, 0, 4), mul(rot_y(ay), rot_x(ax)))
        s.cf('vs', 0, mul(proj, world))
        s.cf('vs', 4, world)
        s.cf('vs', 8, [(0, 0, 0, 1)])
        s.clear(3, 0xFF203040, 1.0)
        s.drwi(0, 24, 0, 12)
        s.present()


def sc_sm2fog(s):
    """vs_2_0/ps_2_0 with vertex fog (FOGENABLE, FOGCOLOR; table mode NONE) and two textures with lrp (ps_3_0)"""
    g = gradient(32, 32)
    s.tex(1, A8R8G8B8, 32, 32, mips(g, 32, 32, 6))
    c = checker(32, 32, (255, 255, 255, 255), (0, 0, 0, 255), 4)
    s.tex(2, A8R8G8B8, 32, 32, mips(c, 32, 32, 6))
    q = []
    for i in range(5):
        z = 0.1 + 0.2 * i
        x0 = -1 + 0.4 * i
        q += quad_ct(x0, -1, x0 + 0.4, 1, z, 0xFFFFFFFF, 0.3 / 32, 0.3 / 32, 1 + 0.3 / 32, 1 + 0.3 / 32)
    s.vb(1, b''.join(q)); s.ib(1, quad_ib(5))
    common_ct(s, vs='vs2_ct', ps='ps2_tex')
    s.cf('vs', 4, [(-1.0, 1.0, 0.0, 0.0)])           # oFog = 1 - z: fog grows with depth
    s.w.u32('STRM', 0, 1, 0, 24)
    s.w.u32('STEX', 0, 1)
    s.ss(0, MAGFILTER=2, MINFILTER=2, MIPFILTER=2)
    s.rs(FOGENABLE=1, FOGCOLOR=0xFFC08040, FOGTABLEMODE=0, FOGVERTEXMODE=0)
    s.clear(1, 0xFF000000)
    for i in range(5): s.drwi(4 * i, 4, 0, 2)
    s.present()
    s.rs(FOGENABLE=0)
    s.use('vs3_ct', 'ps3_tex2')
    s.w.u32('STEX', 1, 2)
    s.ss(1, MAGFILTER=1, MINFILTER=1, MIPFILTER=0)
    s.clear(1, 0xFF000000)
    for i in range(5):
        s.cf('ps', 0, [(i / 4.0, 0, 0, 0)])
        s.drwi(4 * i, 4, 0, 2)
    s.present()


def sc_srgb(s):
    """sRGB: SRGBTEXTURE sampling and SRGBWRITEENABLE blending, against the same draws in linear"""
    g = gradient(32, 32)
    s.tex(1, A8R8G8B8, 32, 32, [g])
    q = quad_ct(-1, 0, 1, 1, 0.5, 0xFFFFFFFF) + quad_ct(-1, -1, 1, 0, 0.5, 0xFFFFFFFF)
    s.vb(1, b''.join(q)); s.ib(1, quad_ib(2))
    common_ct(s, ps='ps3_tex')
    s.w.u32('STRM', 0, 1, 0, 24)
    s.w.u32('STEX', 0, 1)
    s.ss(0, MAGFILTER=2, MINFILTER=2, MIPFILTER=0)
    s.clear(1, 0xFF000000)
    s.drwi(0, 4, 0, 2)
    s.ss(0, SRGBTEXTURE=1)
    s.drwi(4, 4, 0, 2)
    s.present()
    s.clear(1, 0xFF000000)
    s.rs(SRGBWRITEENABLE=1)
    s.drwi(0, 4, 0, 2)
    s.ss(0, SRGBTEXTURE=0)
    s.rs(ALPHABLENDENABLE=1, SRCBLEND=5, DESTBLEND=6)
    s.drwi(4, 4, 0, 2)
    s.rs(SRGBWRITEENABLE=0, ALPHABLENDENABLE=0)
    s.present()


def sc_reldef(s):
    """relative constant reads (a0) that land on def'd and app-set registers, vs_1_1 and vs_2_0 (S16 perm finding)"""
    # quads 1-3: c[20 + a0.x] (c21 def'd green in the shader and set red by the app; c22 app-set blue), quad 3 c21
    # directly, quad 4 (c[20 + a0] + c[19 + a0]) * c20.x. Wine and nx_render agree: def'd registers win for relative reads
    s.decl(2, [(0, 0, 2, 0, 0, 0)])                      # FLOAT3 POSITION only
    q = b''
    for k in range(4):
        x0 = -1 + 0.5 * k
        for (x, y) in ((x0, 1), (x0 + 0.5, 1), (x0 + 0.5, -1), (x0, -1)):
            q += struct.pack('<3f', x, y, 0.5)
    s.vb(2, q); s.ib(2, quad_ib(4))
    s.w.u32('SDCL', 2); s.w.u32('STRM', 0, 2, 0, 12); s.w.u32('SIDX', 2)
    s.rs(CULLMODE=1, ZENABLE=0, ZWRITEENABLE=0)
    s.cf('vs', 20, [(0.5, 0.5, 0.5, 1), (1, 0, 0, 1), (0, 0, 1, 1)])
    for frame, a0 in enumerate((1.0, 2.0)):
        s.clear(1, 0xFF202020)
        s.cf('vs', 4, [(a0, 0, 0, 0)])
        for k, vs in enumerate(('vs11_rel', 'vs20_rel', 'vs20_def', 'vs20_rel_arr')):
            s.use(vs, 'ps20_vcolor')
            s.drwi(4 * k, 4, 0, 2)
        s.present()


SCENES = {'vcolor': sc_vcolor, 'texture': sc_texture, 'blend': sc_blend, 'stencil': sc_stencil, 'rtt': sc_rtt,
          'lit': sc_lit, 'sm2fog': sc_sm2fog, 'srgb': sc_srgb, 'reldef': sc_reldef}


def build(asm_tool, sdp_tool, out, only=None):
    os.makedirs(out, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        sh = assemble(asm_tool, sdp_tool, work)
    names = [n for n in SCENES if not only or n in only]
    for n in names:
        s = Scene(os.path.join(out, n + '.nxd9'), sh)
        SCENES[n](s)
        s.close()
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build', 'list'])
    ap.add_argument('--asm'); ap.add_argument('--sdp2dksh'); ap.add_argument('--out'); ap.add_argument('--only')
    a = ap.parse_args()
    if a.cmd == 'list':
        for n, f in SCENES.items(): print(f'{n}\t{f.__doc__}')
        return 0
    if not (a.asm and a.sdp2dksh and a.out): ap.error('build needs --asm, --sdp2dksh and --out')
    names = build(a.asm, a.sdp2dksh, a.out, a.only.split(',') if a.only else None)
    print('synth:', ' '.join(names))
    return 0


if __name__ == '__main__':
    sys.exit(main())
