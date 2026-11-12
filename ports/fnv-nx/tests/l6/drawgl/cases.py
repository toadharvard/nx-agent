"""cases.py: the cases of tests/l6/drawgl (synthetic hand-assembled shaders, render/shaders/total/sm_hand.py) and their CPU references.  Each case: Case(name, part, tol) + script + c.ref(ix, iy) -> [rgba per render
target] (a value or None = the pixel keeps the clear colour).  Parts: a constants, b textures, c vertex colour + the unwritten-input rule + non-canonical links, d fog, e alpha test, f MRT / sRGB / vertex+index buffers, g fixed function, h user clip planes and point size with a programmable VS, j D3DRS_SHADEMODE FLAT with every pair kind, k SM1 texture-address opcodes reading tN as data (texcoord t0 / texcrd)."""
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
    c = mk("const_float", "a"); vs = c.vs(A("vs_2_0\ndcl_position v0\nmov r0, v0\nmad oPos, r0, c0, c1\nadd r1, c2, c200\nadd oD0, r1, c255\n"))
    ps = c.ps(A("ps_2_0\ndcl v0\nmul r0, v0, c5\nadd oC0, r0, c31\n")); d = c.decl(elems([(FLOAT4, POSITION, 0)])); c.use(vs, ps, d)
    c.consts(0, 0, [2, 2, 2, 1, -0.5, 0.5, 0, 0, 0.2, 0.4, 0.6, 0.8]); c.consts(0, 200, [0.1, 0, 0, 0]); c.consts(0, 255, [0, 0.05, 0, 0]); c.consts(1, 5, [1.0, 0.5, 0.25, 0.75]); c.consts(1, 31, [0, 0.1, 0, 0])
    verts = []
    for p in SCR: x, y = ndc(p); verts.append([fb(x * 0.5 + 0.25), fb(y * 0.5 - 0.25), fb(0.25), fb(1.0)])
    c.run("up", 16, verts); c.ref = full(lambda ix, iy: ((0.2 + 0.1) * 1.0, (0.4 + 0.05) * 0.5 + 0.1, 0.6 * 0.25, 0.8 * 0.75))
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
    cf = lambda sx, sy: ((sx + 2) / 68, (sy + 2) / 68, 0.5, (sx + sy + 4) / 136)   # in [0, 1] at the three vertices: vertex colours are saturated by the vertex shader stage
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
    c = mk("link_vs_subset_of_ps", "c"); vs = c.vs(A("vs_3_0\ndcl_position v0\ndcl_position o0\ndcl_color3 o1\nmov o0, v0\nmov o1, c2\n")); ps = c.ps(A("ps_3_0\ndcl_color2 v0\ndcl_color3 v1\nadd oC0, v0, v1\n"))
    c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0)]))); c.consts(0, 2, [0.6, 0.5, 0.4, 0.3]); c.run("up", 16, vv); c.ref = full(lambda ix, iy: (0.6, 0.5, 0.4, 0.3))
    # two pixel shader objects with one bytecode share one backend shader (memo); releasing one must not take it from the other
    c = mk("shared_bytecode_release", "f"); vs = c.vs(A("vs_2_0\ndcl_position v0\nmov oPos, v0\n")); code = A("ps_2_0\nmov oC0, c0\n"); p1, p2 = c.ps(code), c.ps(code); d1 = c.decl(elems([(FLOAT4, POSITION, 0)]))
    c.clear(); c.use(vs, p1, d1); c.consts(1, 0, [0.9, 0.1, 0.1, 1.0]); c.draw("up", 16, vv); c.use(vs, p2, d1); c.consts(1, 0, [0.1, 0.9, 0.1, 1.0]); c.draw("up", 16, vv)
    c.add("z", 1, p1); c.consts(1, 0, [0.1, 0.1, 0.9, 1.0]); c.draw("up", 16, vv); c.out(); c.ref = full(lambda ix, iy: (0.1, 0.1, 0.9, 1.0))
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
        s, vv2 = tri([("pos", None), ("f4", lambda sx, sy: (0.3, 0.6, 0.9, (sx + 2) / 68)), ("f2", lambda sx, sy: (0, 0))]); c.run("up", s, vv2)
        c.ref = full(lambda ix, iy, fn=fn: (0.3, 0.6, 0.9, (ix + 2) / 68) if PASS[fn]((ix + 2) / 68, 0x66 / 255) else None)
    c = mk("alpha_disabled_ignores_func", "e"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
    c.rs("ALPHATESTENABLE", 0); c.rs("ALPHAFUNC", 1); c.rs("ALPHAREF", 0x66); s, vv2 = tri([("pos", None), ("f4", lambda sx, sy: (0.3, 0.6, 0.9, (sx + 2) / 68)), ("f2", lambda sx, sy: (0, 0))]); c.run("up", s, vv2)
    c.ref = full(lambda ix, iy: (0.3, 0.6, 0.9, (ix + 2) / 68))
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
    # ---------------------------------------------------------------- h: user clip planes and point size with a programmable vertex shader (WineD3D 9.0 shader_glsl_generate_vs_epilogue)
    VS_PC = "vs_2_0\ndcl_position v0\ndcl_color0 v1\nmov oPos, v0\nmov oD0, v1\n"
    VS_PC3 = "vs_3_0\ndcl_position v0\ndcl_color0 v1\ndcl_position o0\ndcl_color0 o1\nmov o0, v0\nmov o1, v1\n"
    HC = (0.3, 0.6, 0.9, 1.0)
    pvs = [[fb(x) for x in (*ndc(p), Z, 1.0)] + [fb(v) for v in HC] for p in SCR]
    def clipcase(nm, vsa, planes, enable, f, clipping=1):
        c = mk(nm, "h"); vs = c.vs(A(vsa)); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0)])))
        c.rs("CLIPPING", clipping); c.rs("CLIPPLANEENABLE", enable)
        for i, p in planes: c.plane(i, p)
        c.run("up", 32, pvs); c.ref = full(lambda ix, iy: HC if f(ix, iy) else None)
    PX, PY = (1.0, 0.0, 0.0, 0.03), (0.0, -1.0, 0.0, 0.03)     # keep ndc x >= -0.03 (ix >= 16, W = 32) / ndc y <= 0.03 (iy >= 16): the plane value is dot(clip position, plane), raw
    kx, ky = (lambda ix, iy: ix >= 16), (lambda ix, iy: iy >= 16)
    clipcase("clip_vs2_plane0_x", VS_PC, [(0, PX)], 1, kx)
    clipcase("clip_vs2_plane1_y_no_inversion", VS_PC, [(1, PY)], 2, ky)
    clipcase("clip_vs2_two_planes", VS_PC, [(0, PX), (1, PY)], 3, lambda ix, iy: kx(ix, iy) and ky(ix, iy))
    clipcase("clip_vs3_plane5", VS_PC3, [(5, PX)], 32, kx)
    clipcase("clip_vs2_plane_not_enabled", VS_PC, [(0, PX), (1, PY)], 0, lambda ix, iy: True)
    clipcase("clip_vs2_enabled_only_plane1", VS_PC, [(0, PX), (1, PY)], 2, ky)
    clipcase("clip_vs2_clipping_off", VS_PC, [(0, PX)], 1, lambda ix, iy: True, clipping=0)
    def ptcase(nm, vsa, size, mn, mx, want, cst=None):
        c = mk(nm, "h"); vs = c.vs(A(vsa)); ps = c.ps(A(PS_COL)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0)])))
        c.rsf("POINTSIZE", size); c.rsf("POINTSIZE_MIN", mn); c.rsf("POINTSIZE_MAX", mx)
        if cst is not None: c.consts(0, 0, [cst, 0, 0, 0])
        pt = [[fb(x) for x in (2 * 16.5 / W - 1, 1 - 2 * 16.5 / H, Z, 1.0)] + [fb(v) for v in HC]]
        c.run_prim(1, 1, 32, pt); h = want / 2
        c.ref = full(lambda ix, iy: HC if abs(ix - 16.5) <= h and abs(iy - 16.5) <= h else None)
    VS_PS = "vs_2_0\ndcl_position v0\ndcl_color0 v1\nmov oPos, v0\nmov oD0, v1\nmov oPts, c0.x\n"
    VS_PS3 = "vs_3_0\ndcl_position v0\ndcl_color0 v1\ndcl_position o0\ndcl_color0 o1\ndcl_psize o2\nmov o0, v0\nmov o1, v1\nmov o2, c0.x\n"
    ptcase("point_vs2_state_size", VS_PC, 4, 1, 64, 4)
    ptcase("point_vs2_state_size_min", VS_PC, 2, 6, 64, 6)
    ptcase("point_vs2_state_size_max", VS_PC, 8, 1, 2, 2)
    ptcase("point_vs2_min_above_max", VS_PC, 3, 5, 2, 2)
    ptcase("point_vs3_state_size", VS_PC3, 6, 1, 64, 6)
    ptcase("point_vs2_oPts", VS_PS, 2, 1, 64, 6, cst=6.0)
    ptcase("point_vs2_oPts_max", VS_PS, 2, 1, 4, 4, cst=6.0)
    ptcase("point_vs2_oPts_min", VS_PS, 8, 4, 64, 4, cst=1.0)
    ptcase("point_vs3_psize_out", VS_PS3, 2, 1, 64, 6, cst=6.0)
    ptcase("point_vs3_psize_out_min_above_max", VS_PS3, 2, 6, 4, 4, cst=6.0)
    # ---------------------------------------------------------------- i: the storage rule of the CONVERT formats (front_fmt.c): PLAIN sampling returns the D3D9 (r, g, b, a), a render target texture of L8 / A8 holds the D3D9 channels
    def pack(bs): bs = list(bs); bs += [0] * (-len(bs) % 4); return [int.from_bytes(bytes(bs[k:k + 4]), "little") for k in range(0, len(bs), 4)]
    PS_BUMP = "ps_2_0\ndcl t0\ndcl_2d s0\ntexld r0, t0, s0\nmul r0, r0, c0\nadd oC0, r0, c1\n"           # o = 0.5 * sampled + 0.5: the signed range [-1, 1] fits the 8-bit target
    DECL3 = lambda c: c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)]))
    UVT = lambda sx, sy: ((sx + 0.5) / 32, (sy + 0.5) / 32)
    sg = lambda v, n: v - (1 << n) if v >> (n - 1) else v
    def upcase(nm, fmt, bpp, texel, ref, bump):
        c = mk(nm, "i"); vs = c.vs(A(VS_PCT)); ps = c.ps(A(PS_BUMP if bump else PS_TEX["2d"])); c.use(vs, ps, DECL3(c))
        if bump: c.consts(1, 0, [0.5] * 8)
        t = c.tex(0, 32, 32, 1, pack(b for iy in range(32) for ix in range(32) for b in texel(ix, iy)), fmt, bpp); c.bind(0, t)
        s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f2", UVT)]); c.run("up", s, vv); c.ref = full(ref)
    h2 = lambda x: 0.5 * x + 0.5
    def l6code(kind, ix, iy): return (ix | iy << 5 | 37 << 10) if kind == 0 else (((iy % 16) & 31) | (((iy % 16) ^ 16) & 31) << 5 | ((iy // 16) * 32 + ix) << 10)
    def l6ref(code): return lambda ix, iy: (h2(max(sg(code(ix, iy) & 31, 5), -15) / 15), h2(max(sg(code(ix, iy) >> 5 & 31, 5), -15) / 15), h2((code(ix, iy) >> 10) / 63), 1.0)
    for kind, nm in ((0, "uv_all_codes"), (1, "l_all_codes")):
        cd = lambda ix, iy, kind=kind: l6code(kind, ix, iy)
        upcase("tex_l6v5u5_" + nm, 61, 2, lambda ix, iy, cd=cd: (cd(ix, iy) & 255, cd(ix, iy) >> 8), l6ref(cd), True)
    UV4, VU4 = (0x80, 0x7F, 0, 1), (1, 0x80, 0x7F, 0)
    xch = {0: lambda ix, iy: (iy * 8 + ix // 4, UV4[ix % 4], VU4[ix % 4]), 1: lambda ix, iy: (UV4[ix % 4], iy * 8 + ix // 4, VU4[ix % 4]), 2: lambda ix, iy: (UV4[ix % 4], VU4[ix % 4], iy * 8 + ix // 4)}
    for k, nm in ((0, "u_all_codes"), (1, "v_all_codes"), (2, "l_all_codes")):
        g3 = xch[k]
        upcase("tex_x8l8v8u8_" + nm, 62, 4, lambda ix, iy, g3=g3: (*g3(ix, iy), 0xAB), lambda ix, iy, g3=g3: (h2(max(sg(g3(ix, iy)[0], 8), -127) / 127), h2(max(sg(g3(ix, iy)[1], 8), -127) / 127), h2(g3(ix, iy)[2] / 255), 1.0), True)
    upcase("tex_l8_upload", 50, 1, lambda ix, iy: ((ix * 8 + iy * 3) & 255,), lambda ix, iy: ((((ix * 8 + iy * 3) & 255) / 255,) * 3) + (1.0,), False)
    upcase("tex_a8_upload", 28, 1, lambda ix, iy: ((ix * 5 + iy * 11) & 255,), lambda ix, iy: (0.0, 0.0, 0.0, ((ix * 5 + iy * 11) & 255) / 255), False)
    CF = lambda sx, sy: (0.3, 0.6, 0.45, 0.7)   # constant: the targets are compared pixel for pixel without a rasterisation model
    CF2 = lambda sx, sy: (0.2, 0.1, 0.1, 0.3)
    def rtcase(nm, fmt, ref, draws, blend=False):
        c = mk(nm, "i"); vs = c.vs(A(VS_PCT)); psc = c.ps(A(PS_COL)); pst = c.ps(A(PS_TEX["2d"])); d = DECL3(c)
        t = c.rttex(fmt); c.rt_to(t); c.use(vs, psc, d); c.clear()
        if draws:
            s, vv = tri([("pos", None), ("f4", CF), ("f2", lambda sx, sy: (0, 0))]); c.draw("up", s, vv)
            if blend:
                c.rs("SRCBLEND", 2); c.rs("DESTBLEND", 7); c.rs("ALPHABLENDENABLE", 1); s, vv = tri([("pos", None), ("f4", CF2), ("f2", lambda sx, sy: (0, 0))]); c.draw("up", s, vv); c.rs("ALPHABLENDENABLE", 0)
        c.rt_main(); c.use(vs, pst, d); c.bind(0, t); s, vv = tri([("pos", None), ("f4", lambda sx, sy: (1, 1, 1, 1)), ("f2", UVT)]); c.draw("up", s, vv); c.out(); c.ref = full(ref)
    rtcase("rt_a8_draw", 28, lambda ix, iy: (0.0, 0.0, 0.0, CF(ix, iy)[3]), True)
    rtcase("rt_a8_clear", 28, lambda ix, iy: (0.0, 0.0, 0.0, 0x80 / 255), False)
    rtcase("rt_l8_draw", 50, lambda ix, iy: (CF(ix, iy)[0],) * 3 + (1.0,), True)
    rtcase("rt_l8_clear", 50, lambda ix, iy: (0x40 / 255,) * 3 + (1.0,), False)
    rtcase("rt_l8_destalpha_is_one", 50, lambda ix, iy: ((CF(ix, iy)[0] + 0.2,) * 3) + (1.0,), True, blend=True)
    # ---------------------------------------------------------------- j: D3DRS_SHADEMODE = FLAT with every pair kind (translated or fixed-function stage on either side): COLOR0 takes the FIRST vertex's value, every other interpolant stays interpolated
    FLAT, GOURAUD = 1, 2
    V3 = [(0.9, 0.2, 0.4, 0.9), (0.1, 0.8, 0.3, 0.7), (0.3, 0.5, 0.95, 0.5)]            # the three vertex colours (r, g, b, a); vertex 0 is the provoking vertex
    V3I = {p: i for i, p in enumerate(SCR)}
    pk = lambda col: (int(col[3] * 255 + 0.5) << 24) | (int(col[0] * 255 + 0.5) << 16) | (int(col[1] * 255 + 0.5) << 8) | int(col[2] * 255 + 0.5)
    tcf = lambda sx, sy: ((sx + 2) / 68, (sy + 2) / 68)                                   # texcoord attribute, affine
    def shaded(mode, ix, iy):
        w1, w2 = (ix + 2) / 68, (iy + 2) / 68
        return V3[0] if mode == FLAT else tuple((1 - w1 - w2) * V3[0][k] + w1 * V3[1][k] + w2 * V3[2][k] for k in range(4))
    VS3_PCT = "vs_3_0\ndcl_position v0\ndcl_color0 v1\ndcl_texcoord0 v2\ndcl_position o0\ndcl_color0 o1\ndcl_texcoord0 o2\nmov o0, v0\nmov o1, v1\nmov o2, v2\n"
    PSI = {"ps11": "ps_1_1\nmov r0, v0\n", "ps2": PS_COL, "ps3": "ps_3_0\ndcl_color0 v0\nmov oC0, v0\n"}
    PSX = {"ps11": "ps_1_1\ntexcoord t0\nmul r0, v0, t0\n", "ps2": "ps_2_0\ndcl v0\ndcl t0\nmul r0, v0, t0\nmov oC0, r0\n",
           "ps3": "ps_3_0\ndcl_color0 v0\ndcl_texcoord0 v1\nmul oC0, v0, v1\n"}
    def shade_case(nm, kind, psk, seq, tex=False):
        """seq: the SHADEMODE of the draws in order, the last one is read back; kind tt (user VS, user PS) | ft (fixed-function VS, user PS) | tf (user VS, fixed-function PS)"""
        c = mk(nm, "j", tol=2); ps = c.ps(A((PSX if tex else PSI)[psk])) if kind != "tf" else 0
        if kind == "ft":
            c.use(0, ps, 0); c.fvf(FVF_XYZ | FVF_DIFFUSE | (FVF_TEX1 if tex else 0)); c.rs("LIGHTING", 0)
            vv = [[fb(x) for x in (*ndc(p), Z)] + [pk(V3[V3I[p]])] + ([fb(v) for v in tcf(*p)] if tex else []) for p in SCR]; stride = 16 + 8 * tex
        else:
            vs = c.vs(A(VS3_PCT if psk == "ps3" and kind == "tt" else VS_PCT)); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
            if kind == "tf": ffp_ps(c)
            stride, vv = tri([("pos", None), ("f4", lambda sx, sy: V3[V3I[(sx, sy)]]), ("f2", tcf)])
        for m in seq[:-1]: c.rs("SHADEMODE", m); c.clear(); c.draw("up", stride, vv)
        c.rs("SHADEMODE", seq[-1]); c.run("up", stride, vv); m = seq[-1]
        c.ref = full(lambda ix, iy: (shaded(m, ix, iy)[0] * tcf(ix, iy)[0], shaded(m, ix, iy)[1] * tcf(ix, iy)[1], 0.0, shaded(m, ix, iy)[3]) if tex else shaded(m, ix, iy))
    for psk in ("ps11", "ps2", "ps3"):
        for kind in ("tt", "ft"):
            shade_case("shade_flat_%s_%s" % (kind, psk), kind, psk, [FLAT])
        shade_case("shade_gouraud_tt_" + psk, "tt", psk, [GOURAUD])
        shade_case("shade_gouraud_then_flat_tt_" + psk, "tt", psk, [GOURAUD, FLAT])
        shade_case("shade_flat_then_gouraud_tt_" + psk, "tt", psk, [FLAT, GOURAUD])
        shade_case("shade_flat_texcoord_tt_" + psk, "tt", psk, [FLAT], tex=True)
        shade_case("shade_flat_texcoord_ft_" + psk, "ft", psk, [FLAT], tex=True)
    shade_case("shade_gouraud_then_flat_ft_ps2", "ft", "ps2", [GOURAUD, FLAT]); shade_case("shade_flat_then_gouraud_ft_ps2", "ft", "ps2", [FLAT, GOURAUD])
    shade_case("shade_flat_tf", "tf", "ps2", [FLAT]); shade_case("shade_gouraud_then_flat_tf", "tf", "ps2", [GOURAUD, FLAT]); shade_case("shade_flat_then_gouraud_tf", "tf", "ps2", [FLAT, GOURAUD])
    # ---------------------------------------------------------------- k: the SM1 texture-address opcodes that read tN as DATA (texcoord t0 of ps_1_1..1_3, texcrd r0, t0 of ps_1_4): the register holds the interpolated texture coordinate of stage 0 clamped to [0, 1], w = 1.  mov_d2 halves it, so both the clamp and w are observable on the 8-bit target
    tc2 = lambda sx, sy: (2 * tcf(sx, sy)[0] - 0.5, 2 * tcf(sx, sy)[1] - 0.5)           # [-0.5, 1.5]: both clamp edges are inside the triangle
    cl = lambda v: min(1.0, max(0.0, v))
    PSK = {"ps11": "ps_1_1\ntexcoord t0\nmov_d2 r0, t0\n", "ps12": "ps_1_2\ntexcoord t0\nmov_d2 r0, t0\n", "ps13": "ps_1_3\ntexcoord t0\nmov_d2 r0, t0\n", "ps14": "ps_1_4\ntexcrd r0, t0\nmov_d2 r0, r0\n"}
    for psk, src in PSK.items():
        for kind in ("tt", "ft"):
            c = mk("texdata_%s_%s" % (kind, psk), "k", tol=2); ps = c.ps(A(src))
            if kind == "ft":
                c.use(0, ps, 0); c.fvf(FVF_XYZ | FVF_DIFFUSE | FVF_TEX1); c.rs("LIGHTING", 0)
                vv = [[fb(x) for x in (*ndc(p), Z)] + [pk(V3[V3I[p]])] + [fb(v) for v in tc2(*p)] for p in SCR]; stride = 24
            else:
                vs = c.vs(A("vs_1_1\ndcl_position v0\ndcl_color0 v1\ndcl_texcoord0 v2\nmov oPos, v0\nmov oD0, v1\nmov oT0, v2\n")); c.use(vs, ps, c.decl(elems([(FLOAT4, POSITION, 0), (FLOAT4, COLOR, 0), (FLOAT2, TEXCOORD, 0)])))
                stride, vv = tri([("pos", None), ("f4", lambda sx, sy: V3[V3I[(sx, sy)]]), ("f2", tc2)])
            c.run("up", stride, vv); c.ref = full(lambda ix, iy: (cl(tc2(ix, iy)[0]) / 2, cl(tc2(ix, iy)[1]) / 2, 0.0, 0.5))
    return out
