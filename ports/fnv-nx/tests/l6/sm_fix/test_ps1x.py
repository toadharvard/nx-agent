#!/usr/bin/env python3
"""T-SM-PS1X: ps_1_x texture instructions (classes 1 and 2 of the SM totality gate).  python3 -I tests/l6/sm_fix/test_ps1x.py [--build DIR]
Every case translates a ps_1_x program with the glsldk translator, runs the GLSL on llvmpipe with real textures bound at the three numbering
blocks of fnvnx_dk_layout.h (2D at n, CUBE at 8+n, VOLUME at 16+n) and the stage type words of the PS driver block (words 80..87:
0 = 2D, 1 = CUBE, 2 = VOLUME), and compares with values computed by hand from the documented formulas (MS D3D9 asm reference pages named in
each case).  ps_1_x has no dcl: the sampler type is the type of the texture bound at draw time, so it is DATA (class 1)."""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smlib
a = smlib.args(); S = smlib.Suite("T-SM-PS1X", a)
STY = 80                                              # driver block word of stage 0's type
def tytab(*types): return "".join("drvu %d %d\n" % (STY + n, t) for n, t in enumerate(types))
def allt(first):
    """the three numbering blocks for stages first..3 (all four stages with first = 0)"""
    r = ""
    for n in range(first, 4): r += smlib.tex2d(n, 4, 4, smlib.T2)
    for n in range(4): r += smlib.texcube(8 + n, smlib.TC) + smlib.tex3d(16 + n, 2, 2, 2, smlib.T3)
    return r
ALL, ALLN = allt(0), allt(1)                          # ALLN: stage 0's 2D texture is the case's own (SRC)
def t2(u, v):
    cl = lambda x, n: min(max(int(math.floor(x * n)), 0), n - 1)
    return smlib.T2(cl(u, 4), cl(v, 4))
def t3(u, v, w):
    cl = lambda x: min(max(int(math.floor(x * 2)), 0), 1)
    return smlib.T3(cl(u), cl(v), cl(w))
def tcube(x, y, z):
    ax = (abs(x), abs(y), abs(z)); m = ax.index(max(ax)); d = (x, y, z)[m]
    return smlib.TC(2 * m + (0 if d >= 0 else 1))
def smp(ty, c):
    return (t2(c[0], c[1]), tcube(*c[:3]), t3(*c[:3]))[ty]
def tc(n, *v): return smlib.cst(240 + n, *v)

# ---- class 1: the sampler dimensionality is data (tex, texld, texbem, texm3x2tex, texm3x3tex)
C0 = (0.3, 0.6, 0.2, 1.0)
for ty, nm in enumerate(("2d", "cube", "volume")):
    S.case("tex.s0.%s" % nm, "ps_1_1\ntex t0\nmov r0, t0\n", tc(0, *C0) + tytab(ty, 0, 0, 0) + ALL, smp(ty, C0))
    S.case("tex.s1.%s" % nm, "ps_1_1\ntex t1\nmov r0, t1\n", tc(1, *C0) + tytab(0, ty, 0, 0) + ALL, smp(ty, C0))
    S.case("texld14.s1.%s" % nm, "ps_1_4\ntexld r1, t1\nmov r0, r1\n", tc(1, *C0) + tytab(0, ty, 0, 0) + ALL, smp(ty, C0))
# texld ps_1_4 projective: _dw divides by w (MS texld ps_1_4: the 2D coordinate (x/w, y/w); the third coordinate of a 3D type likewise z/w)
PW = (0.6, 1.2, 0.4, 2.0)
S.case("texld14.dw.2d", "ps_1_4\ntexld r0, t0.xyw_dw\n", tc(0, *PW) + tytab(0) + ALL, t2(0.3, 0.6))
# texbem (ps_1_1): u' = u + M00*r + M10*g, v' = v + M01*r + M11*g with the stage's D3DTSS_BUMPENVMAT (driver block texbem[2n], words 16 + 8n)
BUMP = "drvf 24 0.5\ndrvf 25 0.25\ndrvf 26 0.125\ndrvf 27 0.0625\n"          # stage 1: m00 m01 m10 m11
SRC = smlib.tex2d(0, 1, 1, lambda i, j: (0.3, 0.6, 0.8, 0.45))
u1, v1 = 0.45 + 0.5 * 0.3 + 0.125 * 0.6, 0.35 + 0.25 * 0.3 + 0.0625 * 0.6
S.case("texbem.2d", "ps_1_1\ntex t0\ntexbem t1, t0\nmov r0, t1\n", tc(0, 0.5, 0.5, 0, 1) + tc(1, 0.45, 0.35, 0, 1) + BUMP + tytab(0, 0) + SRC + smlib.tex2d(1, 4, 4, smlib.T2) + smlib.tex2d(2, 4, 4, smlib.T2), t2(u1, v1))
# texbeml: the sample times L = src.b * D3DTSS_BUMPENVLSCALE + D3DTSS_BUMPENVLOFFSET (driver block texbem[2n + 1] = (lscale, loffset), words 28, 29)
LUM = 0.8 * 0.5 + 0.125
S.case("texbeml.2d", "ps_1_1\ntex t0\ntexbeml t1, t0\nmov r0, t1\n", tc(0, 0.5, 0.5, 0, 1) + tc(1, 0.45, 0.35, 0, 1) + BUMP + "drvf 28 0.5\ndrvf 29 0.125\n" + tytab(0, 0) + SRC + smlib.tex2d(1, 4, 4, smlib.T2),
       tuple(x * LUM for x in t2(u1, v1)))
# texm3x2tex (ps_1_1): u = dot(t1.xyz, t0.rgb), v = dot(t2.xyz, t0.rgb) -> sampler 2 of its type
N0 = (0.3, 0.6, 0.8)
rowa, rowb = (0.5, 0.25, 0.5), (0.4, 0.5, 0.25)
uu = sum(x * y for x, y in zip(rowa, N0)); vv = sum(x * y for x, y in zip(rowb, N0))
for ty, nm in enumerate(("2d", "cube", "volume")):
    # the 2D / 3D types take the first two / three components; texm3x2tex is a 2D-coordinate op: a third component is 0 (MS texm3x2tex page: u, v)
    want = (t2(uu, vv), tcube(uu, vv, 0.0), t3(uu, vv, 0.0))[ty]
    S.case("texm3x2tex.%s" % nm, "ps_1_1\ntex t0\ntexm3x2pad t1, t0\ntexm3x2tex t2, t0\nmov r0, t2\n",
           tc(0, *N0, 1) + tc(1, *rowa, 0) + tc(2, *rowb, 0) + tytab(0, 0, ty, 0) + SRC + ALLN, want)
# ---- texm3x3tex / texm3x3spec / texm3x3vspec (ps_1_1)
row0, row1, row2 = (0.5, 0.25, 0.5), (0.4, 0.5, 0.25), (0.1, 0.2, 0.9)
dot = lambda p, q: sum(x * y for x, y in zip(p, q))
Nv = (dot(row0, N0), dot(row1, N0), dot(row2, N0))
for ty, nm in enumerate(("2d", "cube", "volume")):
    pre = "ps_1_1\ntex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\n"
    spec = tc(0, *N0, 1) + tc(1, *row0, 0.3) + tc(2, *row1, 0.4) + tc(3, *row2, 0.5) + tytab(0, 0, 0, ty) + SRC + ALLN
    S.case("texm3x3tex.%s" % nm, pre + "texm3x3tex t3, t0\nmov r0, t3\n", spec, smp(ty, Nv))
    # texm3x3spec (MS texm3x3spec page): R = 2 * (N.E) / (N.N) * N - E with E = c0.xyz, sampled with the stage's type
    E = (0.2, 0.9, 0.4); R = tuple(2 * dot(Nv, E) / dot(Nv, Nv) * n - e for n, e in zip(Nv, E))
    S.case("texm3x3spec.%s" % nm, pre + "texm3x3spec t3, t0, c0\nmov r0, t3\n", spec + smlib.cst(0, *E, 0), smp(ty, R))
    # texm3x3vspec: E = (t1.w, t2.w, t3.w)
    E = (0.3, 0.4, 0.5); R = tuple(2 * dot(Nv, E) / dot(Nv, Nv) * n - e for n, e in zip(Nv, E))
    S.case("texm3x3vspec.%s" % nm, pre + "texm3x3vspec t3, t0\nmov r0, t3\n", spec, smp(ty, R))
S.case("texm3x3", "ps_1_2\ntex t0\ntexm3x3pad t1, t0\ntexm3x3pad t2, t0\ntexm3x3 t3, t0\nmov r0, t3\n", tc(0, *N0, 1) + tc(1, *row0, 0) + tc(2, *row1, 0) + tc(3, *row2, 0) + SRC, (*Nv, 1.0))

# ---- class 2: texreg2ar / texreg2gb / texreg2rgb / texdp3 / texdp3tex (MS pages of the same names)
SRC4 = smlib.tex2d(0, 1, 1, lambda i, j: (0.3, 0.6, 0.8, 0.45))          # t0 = (r, g, b, a) after `tex t0`
T1 = smlib.tex2d(1, 4, 4, smlib.T2) + smlib.tex2d(2, 4, 4, smlib.T2)
S.case("texreg2ar", "ps_1_1\ntex t0\ntexreg2ar t1, t0\nmov r0, t1\n", tytab(0, 0) + SRC4 + T1, t2(0.45, 0.3))      # (u, v) = (a, r)
S.case("texreg2gb", "ps_1_2\ntex t0\ntexreg2gb t1, t0\nmov r0, t1\n", tytab(0, 0) + SRC4 + T1, t2(0.6, 0.8))       # (u, v) = (g, b)
for ty, nm in enumerate(("2d", "cube", "volume")):
    ALL1 = SRC4 + smlib.tex2d(1, 4, 4, smlib.T2) + smlib.texcube(9, smlib.TC) + smlib.tex3d(17, 2, 2, 2, smlib.T3)
    S.case("texreg2rgb.%s" % nm, "ps_1_2\ntex t0\ntexreg2rgb t1, t0\nmov r0, t1\n", tytab(0, ty) + ALL1, smp(ty, (0.3, 0.6, 0.8)))      # (u, v, w) = (r, g, b)
D3 = dot((0.5, 0.25, 0.5), (0.3, 0.6, 0.8))                              # t1's coordinates . t0.rgb
S.case("texdp3", "ps_1_2\ntex t0\ntexdp3 t1, t0\nmov r0, t1\n", tc(1, 0.5, 0.25, 0.5, 0) + tytab(0, 0) + SRC4, (D3, D3, D3, D3))
S.case("texdp3tex", "ps_1_2\ntex t0\ntexdp3tex t1, t0\nmov r0, t1\n", tc(1, 0.5, 0.25, 0.5, 0) + tytab(0, 0) + SRC4 + T1, t2(D3, 0.0))   # sampled at (dot, 0)
# ---- bem (ps_1_4): dst.r = src0.r + M00*src1.r + M10*src1.g ; dst.g = src0.g + M01*src1.r + M11*src1.g, stage = the destination register number
BEMC = smlib.cst(0, 0.1, 0.2, 0.3, 0.4) + smlib.cst(1, 0.5, 0.25, 0.0, 0.0) + smlib.cst(2, 0.9, 0.8, 0.7, 0.6)
S.case("bem", "ps_1_4\nmov r0, c0\nmov r2, c1\nmov r1, c2\nbem r1.xy, r0, r2\nmov r0, r1\n", BEMC + BUMP,
       (0.1 + 0.5 * 0.5 + 0.125 * 0.25, 0.2 + 0.25 * 0.5 + 0.0625 * 0.25, 0.7, 0.6))
# ---- texdepth (ps_1_4): depth = r5.r / r5.g, 1.0 when r5.g == 0 (MS texdepth page)
OUTC = smlib.cst(1, 0.25, 0.5, 0.75, 1.0)
for nm, z, w, d in (("ratio", 0.3, 0.6, 0.5), ("w0", 0.3, 0.0, 1.0), ("zero", 0.0, 0.6, 0.0)):
    S.case("texdepth." + nm, "ps_1_4\nmov r5, c0\ntexdepth r5\nmov r0, c1\n", smlib.cst(0, z, w, 0, 0) + OUTC, (0.25, 0.5, 0.75, 1.0), depth=d)
# ---- texm3x2depth (ps_1_3): z = dot(t1, t0.rgb), w = dot(t2, t0.rgb), depth = z / w, 1.0 when w == 0
for nm, r2, d in (("ratio", (1.0, 0.5, 0.25), dot((0.5, 0.25, 0.5), N0) / dot((1.0, 0.5, 0.25), N0)), ("w0", (0.0, 0.0, 0.0), 1.0)):
    S.case("texm3x2depth." + nm, "ps_1_3\ntex t0\ntexm3x2pad t1, t0\ntexm3x2depth t2, t0\nmov r0, c1\n",
           tc(0, *N0, 1) + tc(1, 0.5, 0.25, 0.5, 0) + tc(2, *r2, 0) + tytab(0) + SRC + OUTC, (0.25, 0.5, 0.75, 1.0), depth=d)
S.done()
