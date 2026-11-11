"""clib.py: the case DSL of tests/l6/drawgl: a case = a D3D9 call script (t_play.cpp) + an independent python CPU reference of what its shaders compute.
Geometry rule used everywhere: one big triangle with attributes that are AFFINE functions of the window position (sx, sy) of its vertices; D3D9 samples pixel (ix, iy) at (sx, sy) = (ix, iy)
(pixel centres at integer window coordinates; the half pixel lives in the viewport), so the interpolated attribute of pixel (ix, iy) is exactly f(ix, iy) (w = 1: no perspective)."""
import math, struct
W = H = 32
CLEAR = 0x80402010                      # A R G B of the clear colour (the pixel of a discarded fragment)
CLEARF = ((0x40) / 255, 0x20 / 255, 0x10 / 255, 0x80 / 255)   # not used as a float reference: None marks "still the clear colour"
SCR = [(-2.0, -2.0), (66.0, -2.0), (-2.0, 66.0)]
Z = 0.5
# D3DDECLTYPE / D3DDECLUSAGE numbers
FLOAT1, FLOAT2, FLOAT3, FLOAT4, D3DCOLOR = 0, 1, 2, 3, 4
POSITION, NORMAL, TEXCOORD, COLOR = 0, 3, 5, 10
RS = dict(ZENABLE=7, FILLMODE=8, SHADEMODE=9, ALPHATESTENABLE=15, ALPHAREF=24, ALPHAFUNC=25, ALPHABLENDENABLE=27, FOGENABLE=28, FOGCOLOR=34, FOGTABLEMODE=35, FOGSTART=36, FOGEND=37, FOGDENSITY=38,
          LIGHTING=137, CULLMODE=22, FOGVERTEXMODE=140, SRGBWRITEENABLE=194)
SAMP = dict(ADDRESSU=1, ADDRESSV=2, ADDRESSW=3, BORDERCOLOR=4, MAGFILTER=5, MINFILTER=6, MIPFILTER=7, SRGBTEXTURE=11)
D3DFMT_A8R8G8B8 = 21


def fb(x): return struct.unpack("<I", struct.pack("<f", float(x)))[0]
def hx(w): return "%x" % (w & 0xFFFFFFFF)
def ndc(p): return (2 * p[0] / W - 1, 1 - 2 * p[1] / H)
def clamp01(x): return 0.0 if x < 0 else 1.0 if x > 1 else x


class Case:
    def __init__(self, name, part, tol=1, nrt=1):
        self.name, self.part, self.tol, self.nrt, self.ref, self.note = name, part, tol, nrt, None, ""
        self.lines = ["n"]
        self.ids = {"v": 0, "p": 0, "d": 0, "x": 0, "w": 0, "a": 0}
    def add(self, tag, *w): self.lines.append(tag + " " + " ".join(hx(x) for x in w))
    def _id(self, k): self.ids[k] += 1; return self.ids[k]
    def vs(self, bc): i = self._id("v"); self.lines.append("v %x %s" % (i, " ".join(hx(x) for x in bc))); return i
    def ps(self, bc): i = self._id("p"); self.lines.append("p %x %s" % (i, " ".join(hx(x) for x in bc))); return i
    def decl(self, elems):
        i = self._id("d"); self.lines.append("d %x %x %s" % (i, len(elems), " ".join(hx(x) for e in elems for x in e))); return i
    def use(self, vs=0, ps=0, decl=0): self.add("s", vs, ps, decl)
    def fvf(self, f): self.add("f", f)
    def consts(self, stage, idx, vals): self.lines.append("k %x %x %x %s" % (stage, idx, len(vals) // 4, " ".join(hx(fb(x)) for x in vals)))
    def ints(self, stage, idx, vals): self.lines.append("i %x %x %x %s" % (stage, idx, len(vals) // 4, " ".join(hx(x) for x in vals)))
    def bools(self, stage, idx, vals): self.lines.append("b %x %x %x %s" % (stage, idx, len(vals), " ".join(hx(x) for x in vals)))
    def rs(self, name, v): self.add("r", RS[name] if isinstance(name, str) else name, v)
    def rsf(self, name, v): self.rs(name, fb(v))
    def samp(self, stage, name, v): self.add("y", stage, SAMP[name] if isinstance(name, str) else name, v)
    def tss(self, stage, t, v): self.add("t", stage, t, v)
    def tex(self, kind, w, h, d, texels, fmt=D3DFMT_A8R8G8B8, bpp=4):
        """texels: list of dwords (A8R8G8B8) in memory order (cube: six faces)"""
        i = self._id("x"); self.add("x", i, kind, w, h, d, fmt, bpp, *texels); return i
    def bind(self, stage, tid): self.add("e", stage, tid)
    def clear(self): self.add("c", CLEAR)
    def rt(self, idx): self.add("m", idx)
    def out(self): self.add("o", self.nrt)
    def draw(self, mode, stride, verts):
        """verts: three vertices as lists of words. mode up | vb | ib (vb / ib put two unused vertices first: first vertex 2 / base vertex index 2)"""
        flat = [x for v in verts for x in v]
        if mode == "up": self.add("u", stride, 1, *flat)
        else:
            dec = [0] * (2 * stride // 4)
            b = self._id("w"); self.add("w", b, stride, *(dec + flat)); self.add("q", 0, b, 0, stride)
            if mode == "vb": self.add("g", 1, 2)
            else: a = self._id("a"); self.add("a", a, 0, 1, 2); self.add("j", a); self.add("h", 2, 0, 3, 0, 1)
    def run(self, mode="up", stride=0, verts=()):
        self.clear(); self.draw(mode, stride, verts); self.out()


def tri(fields):
    """fields: [(kind, fn)] kind pos | f1 | f2 | f3 | f4 | col (fn(sx, sy) -> floats; col: fn -> 0xAARRGGBB). -> (stride bytes, [words of vertex 0, 1, 2]); `pos` = clip position at depth Z"""
    verts = []
    for p in SCR:
        w = []
        for kind, fn in fields:
            if kind == "pos": x, y = ndc(p); w += [fb(x), fb(y), fb(Z), fb(1.0)]
            elif kind == "col": w.append(fn(*p))
            else: w += [fb(v) for v in fn(*p)]
        verts.append(w)
    return 4 * len(verts[0]), verts


def elems(types):
    """[(type, usage, index)] -> declaration elements at consecutive offsets in stream 0"""
    size = {FLOAT1: 4, FLOAT2: 8, FLOAT3: 12, FLOAT4: 16, D3DCOLOR: 4}; off = 0; out = []
    for t, u, i in types: out.append((0, off, t, 0, u, i)); off += size[t]
    return out


def a8(argb): return ((argb >> 16 & 255) / 255, (argb >> 8 & 255) / 255, (argb & 255) / 255, (argb >> 24 & 255) / 255)
def texel_bytes(argb): return a8(argb)


def srgb_dec(c): return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
