"""cases.py: the cases of tests/l6/drawgl (synthetic hand-assembled shaders, render/shaders/total/sm_hand.py) and their CPU references.  Each case: Case(name, part, tol) + script + c.ref(ix, iy) -> [rgba per render
target] (a value or None = the pixel keeps the clear colour).  Parts: a constants, b textures, c vertex colour + the unwritten-input rule + non-canonical links, d fog, e alpha test, f MRT / sRGB / vertex+index buffers, g fixed function."""
import math
from clib import *

VS_PCT = "vs_2_0\ndcl_position v0\ndcl_color0 v1\ndcl_texcoord0 v2\nmov oPos, v0\nmov oD0, v1\nmov oT0, v2\n"   # position, colour, texcoord pass-through
PS_COL = "ps_2_0\ndcl v0\nmov oC0, v0\n"
PS_TEX = {"2d": "ps_2_0\ndcl t0\ndcl_2d s0\ntexld r0, t0, s0\nmov oC0, r0\n", "cube": "ps_2_0\ndcl t0\ndcl_cube s0\ntexld r0, t0, s0\nmov oC0, r0\n", "vol": "ps_2_0\ndcl t0\ndcl_volume s0\ntexld r0, t0, s0\nmov oC0, r0\n"}
FVF_XYZ, FVF_DIFFUSE, FVF_TEX1 = 0x2, 0x40, 0x100
CASES = []


def build(asm_fn):
    out = []
    def mk(name, part, tol=1, nrt=1): c = Case(name, part, tol, nrt); out.append(c); return c
    def A(text):
        import struct
        b = asm_fn(text); return list(struct.unpack("<%dI" % (len(b) // 4), b))
    return out, mk, A


def pos_fields(extra=()): return [("pos", None)] + list(extra)
def aff(a, b, c): return lambda sx, sy: a * sx + b * sy + c


def make(asm_fn):
    out, mk, A = build(asm_fn)
    full = lambda f: (lambda ix, iy: [f(ix, iy)])
    # ---------------------------------------------------------------- a: constants (float, int, bool)
    c = mk("const_float", "a"); vs = c.vs(A("vs_2_0\ndcl_position v0\nmov r0, v0\nmad oPos, r0, c0, c1\nadd oD0, c2, c200\n"))
    ps = c.ps(A("ps_2_0\ndcl v0\nmul r0, v0, c5\nadd oC0, r0, c31\n")); d = c.decl(elems([(FLOAT4, POSITION, 0)])); c.use(vs, ps, d)
    c.consts(0, 0, [2, 2, 2, 1, -0.5, 0.5, 0, 0, 0.2, 0.4, 0.6, 0.8]); c.consts(0, 200, [0.1, 0, 0, 0]); c.consts(1, 5, [1.0, 0.5, 0.25, 0.75]); c.consts(1, 31, [0, 0.1, 0, 0])
    verts = []
    for p in SCR: x, y = ndc(p); verts.append([fb(x * 0.5 + 0.25), fb(y * 0.5 - 0.25), fb(0.25), fb(1.0)])
    c.run("up", 16, verts); c.ref = full(lambda ix, iy: ((0.2 + 0.1) * 1.0, 0.4 * 0.5 + 0.1, 0.6 * 0.25, 0.8 * 0.75))
    c = mk("const_float_c223", "a"); vs = c.vs(A("vs_3_0\ndcl_position v0\ndcl_position o0\nmov o0, v0\n")); ps = c.ps(A("ps_3_0\nmov oC0, c223\n"))
    c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)]))); c.consts(1, 223, [0.3, 0.6, 0.9, 0.5]); c.consts(1, 222, [0.9, 0.9, 0.9, 0.9]); s, vv = tri([("pos", None)]); c.run("up", s, vv)
    c.ref = full(lambda ix, iy: (0.3, 0.6, 0.9, 0.5))
    c = mk("const_int_ps3", "a"); vs = c.vs(A("vs_3_0\ndcl_position v0\ndcl_position o0\nmov o0, v0\n"))
    ps = c.ps(A("ps_3_0\nmov r0, c0\nrep i15\nadd r0, r0, c1\nendrep\nmov oC0, r0\n")); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)])))
    c.consts(1, 0, [0.05, 0.1, 0.15, 0.2]); c.consts(1, 1, [0.1, 0.05, 0.02, 0.01]); c.ints(1, 15, [4, 0, 0, 0]); c.ints(1, 0, [9, 0, 0, 0]); s, vv = tri([("pos", None)]); c.run("up", s, vv)
    c.ref = full(lambda ix, iy: (0.05 + 4 * 0.1, 0.1 + 4 * 0.05, 0.15 + 4 * 0.02, 0.2 + 4 * 0.01))
    c = mk("const_int_vs", "a"); vs = c.vs(A("vs_2_0\ndcl_position v0\nmov oPos, v0\nmov r0, c3\nrep i15\nadd r0, r0, c4\nendrep\nmov oD0, r0\n")); ps = c.ps(A(PS_COL))
    c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)]))); c.consts(0, 3, [0.1, 0.1, 0.0, 0.2, 0.05, 0.1, 0.15, 0.0]); c.ints(0, 15, [3, 0, 0, 0]); s, vv = tri([("pos", None)]); c.run("up", s, vv)
    c.ref = full(lambda ix, iy: (0.1 + 3 * 0.05, 0.1 + 3 * 0.1, 3 * 0.15, 0.2))
    for b0, b15 in ((5, 0), (0, 7)):
        c = mk("const_bool_%d_%d" % (b0, b15), "a")
        vs = c.vs(A("vs_2_0\ndcl_position v0\nmov oPos, v0\nif b0\nmov oD0, c0\nelse\nmov oD0, c1\nendif\n")); ps = c.ps(A("ps_2_x\ndcl v0\nif b15\nmov oC0, v0\nelse\nmul oC0, v0, c0\nendif\n"))
        c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)]))); c.consts(0, 0, [0.8, 0.6, 0.4, 0.9, 0.2, 0.3, 0.5, 0.7]); c.consts(1, 0, [0.5, 0.25, 1.0, 0.5]); c.bools(0, 0, [b0]); c.bools(1, 15, [b15])
        s, vv = tri([("pos", None)]); c.run("up", s, vv)
        def f(ix, iy, b0=b0, b15=b15):
            v = (0.8, 0.6, 0.4, 0.9) if b0 else (0.2, 0.3, 0.5, 0.7); m = (0.5, 0.25, 1.0, 0.5)
            return v if b15 else tuple(v[k] * m[k] for k in range(4))
        c.ref = full(f)
    # ---------------------------------------------------------------- b: textures
    T4 = [0xFF000000 | 0 for _ in range(16)]
    for j in range(4):
        for i in range(4): T4[j * 4 + i] = ((255 - 10 * i - 20 * j) << 24) | ((30 + 70 * i) << 16) | ((30 + 70 * j) << 8) | (((i + j) % 2) * 200 + 30)
    def t4(i, j): return a8(T4[j * 4 + i])
    def addr(mode, i, n):
        if mode == 1: return i % n
        if mode == 3: return min(max(i, 0), n - 1)
        if mode == 2: k = i % (2 * n); return k if k < n else 2 * n - 1 - k
        return i if 0 <= i < n else None
    BORDER = 0xFFC08040
    for nm, mu, mv in (("wrap_clamp", 1, 3), ("clamp_wrap", 3, 1), ("mirror_mirror", 2, 2), ("border_border", 4, 4)):
        c = mk("tex2d_point_" + nm, "b"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_TEX["2d"])); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
        t = c.tex(0, 4, 4, 1, T4); c.bind(0, t); c.samp(0, "ADDRESSU", mu); c.samp(0, "ADDRESSV", mv); c.samp(0, "BORDERCOLOR", BORDER)
        s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f2", lambda sx, sy: ((sx + 0.5) / 16, (sy + 0.5) / 16))]); c.run("up", s, vv)
        def f(ix, iy, mu=mu, mv=mv):
            i, j = addr(mu, math.floor((ix + 0.5) / 4), 4), addr(mv, math.floor((iy + 0.5) / 4), 4)
            return a8(BORDER) if i is None or j is None else t4(i, j)
        c.ref = full(f)
    c = mk("tex2d_linear_clamp", "b", tol=3); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_TEX["2d"])); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
    t = c.tex(0, 4, 4, 1, T4); c.bind(0, t); c.samp(0, "MAGFILTER", 2); c.samp(0, "MINFILTER", 2); c.samp(0, "ADDRESSU", 3); c.samp(0, "ADDRESSV", 3); c.note = "bilinear: llvmpipe 8-bit weights, tolerance 3/255"
    s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f2", lambda sx, sy: ((sx + 0.5) / 32, (sy + 0.5) / 32))]); c.run("up", s, vv)
    def lin(ix, iy):
        x, y = (ix + 0.5) / 8 - 0.5, (iy + 0.5) / 8 - 0.5; i0, j0 = math.floor(x), math.floor(y); fx, fy = x - i0, y - j0; r = [0, 0, 0, 0]
        for dj, wy in ((0, 1 - fy), (1, fy)):
            for di, wx in ((0, 1 - fx), (1, fx)):
                t = t4(addr(3, i0 + di, 4), addr(3, j0 + dj, 4))
                for k in range(4): r[k] += t[k] * wx * wy
        return tuple(r)
    c.ref = full(lin)
    # cube: six faces of 2 x 2 texels
    CUBE = [((((f * 40 + 20 + i * 90) & 255) << 16) | (((f * 30 + j * 70 + 30) & 255) << 8) | (255 - f * 30 - i * 20 - j * 10)) | 0xFF000000 for f in range(6) for j in range(2) for i in range(2)]
    def cube_ref(d):
        x, y, z = d; ax, ay, az = abs(x), abs(y), abs(z)
        if ax >= ay and ax >= az: f, sc, tc, ma = (0 if x > 0 else 1), (-z if x > 0 else z), -y, ax
        elif ay >= az: f, sc, tc, ma = (2 if y > 0 else 3), x, (z if y > 0 else -z), ay
        else: f, sc, tc, ma = (4 if z > 0 else 5), (x if z > 0 else -x), -y, az
        i, j = math.floor((sc / ma + 1) / 2 * 2), math.floor((tc / ma + 1) / 2 * 2); return a8(CUBE[f * 4 + j * 2 + i])
    for face in range(6):
        axis, sgn = face // 2, (1 if face % 2 == 0 else -1)
        def dirf(sx, sy, axis=axis, sgn=sgn):
            a, b = (sx - 15.5) / 20, (sy - 15.5) / 20; v = [0, 0, 0]; v[axis] = sgn; v[(axis + 1) % 3] = a; v[(axis + 2) % 3] = b; return tuple(v)
        c = mk("tex_cube_face%d" % face, "b"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_TEX["cube"])); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT3, TEXCOORD, 0)])))
        t = c.tex(1, 2, 2, 1, CUBE); c.bind(0, t); s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f3", dirf)]); c.run("up", s, vv)
        c.ref = full(lambda ix, iy, dirf=dirf: cube_ref(dirf(ix, iy)))
    VOL = [0xFF000000 | ((40 + 60 * z) << 16) | ((50 + 100 * y) << 8) | (20 + 120 * x + 30 * z) for z in range(2) for y in range(2) for x in range(2)]
    c = mk("tex_volume_point", "b"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_TEX["vol"])); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT3, TEXCOORD, 0)])))
    t = c.tex(2, 2, 2, 2, VOL); c.bind(0, t); vf = lambda sx, sy: ((sx + 0.5) / 32, (sy + 0.5) / 32, (sx + sy + 0.5) / 64)
    s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f3", vf)]); c.run("up", s, vv)
    c.ref = full(lambda ix, iy: a8(VOL[math.floor(vf(ix, iy)[2] * 2) * 4 + math.floor(vf(ix, iy)[1] * 2) * 2 + math.floor(vf(ix, iy)[0] * 2)]))
    c = mk("tex_srgb_read", "b", tol=1); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_TEX["2d"])); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
    t = c.tex(0, 4, 4, 1, T4); c.bind(0, t); c.samp(0, "SRGBTEXTURE", 1); c.samp(0, "ADDRESSU", 3); c.samp(0, "ADDRESSV", 3)
    s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f2", lambda sx, sy: ((sx + 0.5) / 32, (sy + 0.5) / 32))]); c.run("up", s, vv)
    def sr(ix, iy):
        t = t4(min(ix // 8, 3), min(iy // 8, 3)); return (srgb_dec(t[0]), srgb_dec(t[1]), srgb_dec(t[2]), t[3])
    c.ref = full(sr)
    # two textures at stages 0 and 2 (stage 1 empty): the sampler slot is the sampler register
    c = mk("tex_slots_s0_s2", "b"); vs = c.vs(A(VS_PCT)); ps = c.ps(A("ps_2_0\ndcl t0\ndcl_2d s0\ndcl_2d s2\ntexld r0, t0, s0\ntexld r1, t0, s2\nmul r1, r1, c0\nadd oC0, r0, r1\n"))
    c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)]))); ta = c.tex(0, 4, 4, 1, T4); tb = c.tex(0, 2, 2, 1, [0xFF200000, 0xFF002000, 0xFF000020, 0xFF202020])
    c.bind(0, ta); c.bind(2, tb); c.consts(1, 0, [0.5, 0.5, 0.5, 0.0]); c.samp(0, "ADDRESSU", 3); c.samp(0, "ADDRESSV", 3); c.samp(2, "ADDRESSU", 3); c.samp(2, "ADDRESSV", 3)
    s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f2", lambda sx, sy: ((sx + 0.5) / 32, (sy + 0.5) / 32))]); c.run("up", s, vv)
    TB = [0xFF200000, 0xFF002000, 0xFF000020, 0xFF202020]
    c.ref = full(lambda ix, iy: tuple(t4(min(ix // 8, 3), min(iy // 8, 3))[k] + a8(TB[(iy // 16) * 2 + ix // 16])[k] * (0.5, 0.5, 0.5, 0.0)[k] for k in range(4)))
    for kind, nm in ((0, "2d"), (1, "cube"), (2, "vol")):   # ps_1_1 `tex t0`: the type word of the driver block selects the sampler dimension
        c = mk("ps11_tex_" + nm, "b"); vs = c.vs(A(VS_PCT)); ps = c.ps(A("ps_1_1\ntex t0\nmov r0, t0\n")); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT3, TEXCOORD, 0)])))
        if kind == 0: t = c.tex(0, 4, 4, 1, T4); tf = lambda ix, iy: t4(min(ix // 8, 3), min(iy // 8, 3)); cf = lambda sx, sy: ((sx + 0.5) / 32, (sy + 0.5) / 32, 0)
        elif kind == 1:
            t = c.tex(1, 2, 2, 1, CUBE); dirf = lambda sx, sy: (1, (sy - 15.5) / 20, (sx - 15.5) / 20); cf = dirf; tf = lambda ix, iy: cube_ref(dirf(ix, iy))
        else: t = c.tex(2, 2, 2, 2, VOL); cf = vf; tf = lambda ix, iy: a8(VOL[math.floor(vf(ix, iy)[2] * 2) * 4 + math.floor(vf(ix, iy)[1] * 2) * 2 + math.floor(vf(ix, iy)[0] * 2)])
        c.bind(0, t); c.samp(0, "ADDRESSU", 3); c.samp(0, "ADDRESSV", 3); s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f3", cf)]); c.run("up", s, vv); c.ref = full(tf)
    # ---------------------------------------------------------------- c: vertex colour, the unwritten-input rule, non-canonical links
    cf = lambda sx, sy: (sx / 32, sy / 32, 0.5, (sx + sy) / 64)
    c = mk("vcolor_float4", "c"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
    s, vv = tri([("pos", None), ("f4", cf), ("f2", lambda sx, sy: (0, 0))]); c.run("up", s, vv); c.ref = full(lambda ix, iy: cf(ix, iy))
    c = mk("vcolor_d3dcolor", "c"); vs = c.vs(A("vs_2_0\ndcl_position v0\ndcl_color0 v1\nmov oPos, v0\nmov oD0, v1\n")); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (D3DCOLOR, COLOR, 0)])))
    s, vv = tri([("pos", None), ("col", lambda sx, sy: 0x80336699)]); c.run("up", s, vv); c.ref = full(lambda ix, iy: (0x33 / 255, 0x66 / 255, 0x99 / 255, 0x80 / 255))
    for mode in ("vb", "ib"):
        c = mk("vcolor_float4_" + mode, "f"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
        s, vv = tri([("pos", None), ("f4", cf), ("f2", lambda sx, sy: (0, 0))]); c.run(mode, s, vv); c.ref = full(lambda ix, iy: cf(ix, iy))
    s, vv = tri([("pos", None)])
    for nm, vsa, psa, want in (
            ("unwritten_color0_ps20", "vs_1_1\nmov oPos, v0\n", "ps_2_0\ndcl v0\nmov oC0, v0\n", (1, 1, 1, 1)),
            ("unwritten_color0_ps30", "vs_3_0\ndcl_position v0\ndcl_position o0\nmov o0, v0\n", "ps_3_0\ndcl_color0 v0\nmov oC0, v0\n", (0, 0, 0, 0)),
            ("unwritten_color0_ps11", "vs_1_1\nmov oPos, v0\n", "ps_1_1\nmov r0, v0\n", (1, 1, 1, 1)),
            ("unwritten_color1_ps20", "vs_2_0\ndcl_position v0\nmov oPos, v0\nmov oD0, c0\n", "ps_2_0\ndcl v1\nmov oC0, v1\n", (0, 0, 0, 0)),
            ("unwritten_texcoord0_ps20", "vs_2_0\ndcl_position v0\nmov oPos, v0\n", "ps_2_0\ndcl t0\nmov oC0, t0\n", (0, 0, 0, 0)),
            ("unwritten_texcoord5_ps30", "vs_3_0\ndcl_position v0\ndcl_position o0\nmov o0, v0\n", "ps_3_0\ndcl_texcoord5 v0\nmov oC0, v0\n", (0, 0, 0, 0)),
            ("partial_texcoord_xy", "vs_2_0\ndcl_position v0\nmov oPos, v0\nmov oT0.xy, c0\n", "ps_2_0\ndcl t0\nmov oC0, t0\n", (0.3, 0.6, 0, 0))):
        c = mk(nm, "c"); vs = c.vs(A(vsa)); ps = c.ps(A(psa)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)]))); c.consts(0, 0, [0.3, 0.6, 0.9, 0.2]); c.run("up", 16, vv); c.ref = full(lambda ix, iy, w=want: w)
    c = mk("written_color0_unaffected", "c"); vs = c.vs(A("vs_2_0\ndcl_position v0\nmov oPos, v0\nmov oD0, c0\n")); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)])))
    c.consts(0, 0, [0.3, 0.6, 0.9, 0.2]); c.run("up", 16, vv); c.ref = full(lambda ix, iy: (0.3, 0.6, 0.9, 0.2))
    # non-canonical pairs (COLOR2 / COLOR3): both stages are translated with the link set
    for nm, psa, f in (("link_color3", "ps_3_0\ndcl_color3 v0\nmov oC0, v0\n", lambda: (0.6, 0.5, 0.4, 0.3)), ("link_color2_color3", "ps_3_0\ndcl_color2 v0\ndcl_color3 v1\nadd oC0, v0, v1\n", lambda: (0.7, 0.7, 0.7, 0.7))):
        c = mk(nm, "c"); vs = c.vs(A("vs_3_0\ndcl_position v0\ndcl_position o0\ndcl_color2 o1\ndcl_color3 o2\nmov o0, v0\nmov o1, c1\nmov o2, c2\n")); ps = c.ps(A(psa))
        c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)]))); c.consts(0, 1, [0.1, 0.2, 0.3, 0.4, 0.6, 0.5, 0.4, 0.3]); c.run("up", 16, vv); c.ref = full(lambda ix, iy, f=f: f())
    # ---------------------------------------------------------------- d: fog (ps_2_0 with a vs_1_1 without oFog)
    FC = 0x00FF8000; fc = a8(FC); base = (0.8, 0.4, 0.2, 0.6)
    def fogc(f): return tuple(fc[k] + (base[k] - fc[k]) * f for k in range(3)) + (base[3],)
    def fogcase(nm, vsa, psa, setup, f, vfog=None):
        c = mk(nm, "d"); vs = c.vs(A(vsa)); ps = c.ps(A(psa)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)]))); c.consts(1, 0, list(base)); c.consts(0, 0, [0.25, 0, 0, 0]); setup(c)
        c.run("up", 16, vv); c.ref = full(lambda ix, iy: f)
    V11, P20 = "vs_1_1\nmov oPos, v0\n", "ps_2_0\nmov oC0, c0\n"
    fogcase("fog_off", V11, P20, lambda c: c.rs("FOGENABLE", 0), base)
    def tab(mode, **kw):
        def s(c):
            c.rs("FOGENABLE", 1); c.rs("FOGCOLOR", FC); c.rs("FOGTABLEMODE", mode)
            for k, v in kw.items(): c.rsf(k, v)
        return s
    fogcase("fog_table_linear", V11, P20, tab(3, FOGSTART=0.2, FOGEND=0.8), fogc((0.8 - Z) / 0.6))
    fogcase("fog_table_exp", V11, P20, tab(1, FOGDENSITY=1.0), fogc(math.exp(-1.0 * Z)))
    fogcase("fog_table_exp2", V11, P20, tab(2, FOGDENSITY=1.2), fogc(math.exp(-(1.2 * Z) ** 2)))
    fogcase("fog_vertex_unwritten_oFog", V11, P20, lambda c: (c.rs("FOGENABLE", 1), c.rs("FOGCOLOR", FC), c.rs("FOGTABLEMODE", 0), c.rs("FOGVERTEXMODE", 3)), fogc(0.0))
    fogcase("fog_vertex_written_oFog", "vs_1_1\nmov oPos, v0\nmov oFog, c0.x\n", P20, lambda c: (c.rs("FOGENABLE", 1), c.rs("FOGCOLOR", FC), c.rs("FOGTABLEMODE", 0), c.rs("FOGVERTEXMODE", 3)), fogc(0.25))
    fogcase("fog_ps30_has_none", "vs_3_0\ndcl_position v0\ndcl_position o0\nmov o0, v0\n", "ps_3_0\nmov oC0, c0\n", tab(3, FOGSTART=0.2, FOGEND=0.8), base)
    # ---------------------------------------------------------------- e: alpha test
    PASS = {1: lambda a, r: False, 2: lambda a, r: a < r, 3: lambda a, r: a == r, 4: lambda a, r: a <= r, 5: lambda a, r: a > r, 6: lambda a, r: a != r, 7: lambda a, r: a >= r, 8: lambda a, r: True}
    for fn, nm in ((5, "greater"), (4, "lessequal"), (1, "never"), (8, "always"), (6, "notequal"), (2, "less"), (7, "greaterequal"), (3, "equal")):
        c = mk("alpha_" + nm, "e"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
        c.rs("ALPHATESTENABLE", 1); c.rs("ALPHAFUNC", fn); c.rs("ALPHAREF", 0x66)
        s, vv2 = tri([("pos", None), ("f4", lambda sx, sy: (0.3, 0.6, 0.9, sx / 32)), ("f2", lambda sx, sy: (0, 0))]); c.run("up", s, vv2)
        c.ref = full(lambda ix, iy, fn=fn: (0.3, 0.6, 0.9, ix / 32) if PASS[fn](ix / 32, 0x66 / 255) else None)
    c = mk("alpha_disabled_ignores_func", "e"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
    c.rs("ALPHATESTENABLE", 0); c.rs("ALPHAFUNC", 1); c.rs("ALPHAREF", 0x66); s, vv2 = tri([("pos", None), ("f4", lambda sx, sy: (0.3, 0.6, 0.9, sx / 32)), ("f2", lambda sx, sy: (0, 0))]); c.run("up", s, vv2)
    c.ref = full(lambda ix, iy: (0.3, 0.6, 0.9, ix / 32))
    # ---------------------------------------------------------------- f: MRT
    c = mk("mrt_two_targets", "f", nrt=2); vs = c.vs(A("vs_2_0\ndcl_position v0\nmov oPos, v0\n")); ps = c.ps(A("ps_2_0\nmov oC0, c0\nmov oC1, c1\n")); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)])))
    c.rt(1); c.consts(1, 0, [0.2, 0.4, 0.6, 0.8, 0.9, 0.7, 0.5, 0.3]); c.run("up", 16, vv); c.ref = lambda ix, iy: [(0.2, 0.4, 0.6, 0.8), (0.9, 0.7, 0.5, 0.3)]
    # ---------------------------------------------------------------- g: fixed function stage with a programmable stage
    ffcol = 0xC0204060
    c = mk("ffp_vs_user_ps_color", "g"); ps = c.ps(A(PS_COL)); c.use(0, ps, 0); c.fvf(FVF_XYZ | FVF_DIFFUSE); c.rs("LIGHTING", 0)
    verts3 = []
    for p in SCR: x, y = ndc(p); verts3.append([fb(x), fb(y), fb(Z), ffcol])
    c.run("up", 16, verts3); c.ref = full(lambda ix, iy: a8(ffcol))
    c = mk("ffp_vs_user_ps_texture", "g"); ps = c.ps(A(PS_TEX["2d"])); c.use(0, ps, 0); c.fvf(FVF_XYZ | FVF_TEX1); c.rs("LIGHTING", 0)
    t = c.tex(0, 4, 4, 1, T4); c.bind(0, t); verts3 = []
    for p in SCR: x, y = ndc(p); verts3.append([fb(x), fb(y), fb(Z), fb((p[0] + 0.5) / 16), fb((p[1] + 0.5) / 16)])
    c.run("up", 20, verts3); c.ref = full(lambda ix, iy: t4(math.floor((ix + 0.5) / 4) % 4, math.floor((iy + 0.5) / 4) % 4))
    def ffp_ps(c):
        c.tss(0, 1, 2); c.tss(0, 2, 0); c.tss(0, 4, 2); c.tss(0, 5, 0); c.tss(1, 1, 1); c.tss(1, 4, 1)   # stage 0: SELECTARG1 DIFFUSE (colour and alpha); stage 1 disabled
    c = mk("user_vs_ffp_ps_diffuse", "g"); vs = c.vs(A(VS_PCT)); c.use(vs, 0, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)]))); ffp_ps(c)
    s, vv2 = tri([("pos", None), ("f4", cf), ("f2", lambda sx, sy: (0, 0))]); c.run("up", s, vv2); c.ref = full(lambda ix, iy: cf(ix, iy))
    c = mk("user_vs_ffp_ps_modulate", "g", tol=2); vs = c.vs(A(VS_PCT)); c.use(vs, 0, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
    t = c.tex(0, 4, 4, 1, T4); c.bind(0, t); c.tss(0, 1, 4); c.tss(0, 2, 2); c.tss(0, 3, 0); c.tss(0, 4, 4); c.tss(0, 5, 2); c.tss(0, 6, 0); c.tss(1, 1, 1); c.tss(1, 4, 1); c.samp(0, "ADDRESSU", 3); c.samp(0, "ADDRESSV", 3)
    s, vv2 = tri([("pos", None), ("f4", cf), ("f2", lambda sx, sy: ((sx + 0.5) / 32, (sy + 0.5) / 32))]); c.run("up", s, vv2)
    c.ref = full(lambda ix, iy: tuple(t4(min(ix // 8, 3), min(iy // 8, 3))[k] * cf(ix, iy)[k] for k in range(4)))
    return out
