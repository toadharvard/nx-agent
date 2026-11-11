#!/usr/bin/env python3
"""T-SM-OPS: classes 3, 4 and 5 of the SM totality gate (texld variants, predicate conditions, front-end refusals the table says are legal).
python3 -I tests/l6/sm_fix/test_sm_ops.py [--build DIR] [--only SUBSTR]
Each case translates the program with the glsldk translator, runs the GLSL on llvmpipe (gl_run.c) and compares with values computed by hand
from the documented formula (page named in the comment).  Pixel shaders run on the PS path, vertex shaders feed a pass-through pixel
shader that returns the TEXCOORD0 varying."""
import math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smlib
a = smlib.args(); S = smlib.Suite("T-SM-OPS", a)
cst = smlib.cst
sgn = lambda x: (x > 0) - (x < 0)
BASE = (0.25, 0.5, 0.75, 1.0)
def vs(model, body, o="o1"):
    """vertex shader: position (0,0,0,1) from c20, the result r0 to TEXCOORD0"""
    if model == "vs_3_0": return "vs_3_0\ndcl_position o0\ndcl_texcoord0 o1\n" + body + "mov o0, c20\nmov o1, r0\n"
    if model == "vs_1_1": return "vs_1_1\ndcl_position v0\n" + body + "mov oPos, c20\nmov oT0, r0\n"
    return model + "\n" + body + "mov oPos, c20\nmov oT0, r0\n"
V0 = cst(0, *BASE) + cst(20, 0, 0, 0, 1)

# ---- class 3: texldd / texldp / texldl with cube and volume samplers (declared types: the sampler binding is the register number)
T2 = lambda i, j: smlib.T2(i, j)
CUBE0 = smlib.texcube(0, smlib.TC); VOL0 = smlib.tex3d(0, 2, 2, 2, smlib.T3); TEX0 = smlib.tex2d(0, 4, 4, smlib.T2)
C1 = (0.3, 0.6, 0.2, 0.0)                       # +Y face (index 2) of the cube; (0, 1, 0) of the 2x2x2 volume
for model in ("ps_2_x", "ps_3_0"):
    for ty, tex, want in (("cube", CUBE0, smlib.TC(2)), ("volume", VOL0, smlib.T3(0, 1, 0))):
        # MS texldd page: texldd dst, coord, sampler, ddx, ddy samples with the given gradients (a single-level nearest texture: the sample)
        S.case("texldd.%s.%s" % (model, ty), "%s\ndcl_%s s0\nmov r1, c1\nmov r3, c3\ntexldd r0, r1, s0, r3, r3\nmov oC0, r0\n" % (model, ty),
               cst(1, *C1) + cst(3, 0.01, 0.01, 0.01, 0) + tex, want)
for model in ("ps_2_0", "ps_2_x", "ps_3_0"):
    # MS texldp page: the coordinate is divided by its w component: direction (x, y, z) / w
    S.case("texldp.cube.%s" % model, "%s\ndcl_cube s0\nmov r1, c1\ntexldp r0, r1, s0\nmov oC0, r0\n" % model, cst(1, 0.6, 1.2, 0.4, 2.0) + CUBE0, smlib.TC(2))
# MS texldl page (vs_3_0, ps_3_0): coordinate xyz, lod w
S.case("texldl.vs_3_0.cube", vs("vs_3_0", "dcl_cube s0\nmov r1, c1\ntexldl r0, r1, s0\n"),
       V0 + cst(1, *C1) + CUBE0, smlib.TC(2), ps=False)
S.case("texldl.vs_3_0.volume", vs("vs_3_0", "dcl_volume s0\nmov r1, c1\ntexldl r0, r1, s0\n"), V0 + cst(1, *C1) + VOL0, smlib.T3(0, 1, 0), ps=False)
# ps_3_0 texld with a source modifier on the coordinate (MS texld page: only src1 forbids modifiers)
for nm, mod, c in (("neg", "-r1", (-0.3, -0.6, 0, 0)), ("abs", "r1_abs", (-0.3, 0.6, 0, 0)), ("absneg", "-r1_abs", (-0.3, 0.6, 0, 0))):
    want = smlib.T2(1, 2) if nm != "absneg" else smlib.T2(0, 0)            # -|c| is negative: clamped to texel (0, 0)
    S.case("texld.%s" % nm, "ps_3_0\ndcl_2d s0\nmov r1, c1\ntexld r0, %s, s0\nmov oC0, r0\n" % mod, cst(1, *c) + TEX0, want)
S.case("texldp.neg", "ps_3_0\ndcl_2d s0\nmov r1, c1\ntexldp r0, -r1, s0\nmov oC0, r0\n", cst(1, 0.3, 0.6, 0, 0.5) + TEX0, smlib.T2(2, 3))   # (-.3 / -.5, -.6 / -.5) = (.6, 1.2)
S.case("texldb.neg", "ps_3_0\ndcl_2d s0\nmov r1, c1\ntexldb r0, -r1, s0\nmov oC0, r0\n", cst(1, -0.3, -0.6, 0, 0) + TEX0, smlib.T2(1, 2))

# ---- class 4: the predicate as the condition of if / callnz / breakp: one component by a replicate swizzle, optional NOT.
#  setp_gt p0, r1, r2 with c1 = (2, 1, 3, 0), c2 = (1, 2, 1, 1): p0 = (true, false, true, false)
P0 = (True, False, True, False)
SETP = "mov r1, c1\nmov r2, c2\nsetp_gt p0, r1, r2\n"
PREG = cst(1, 2, 1, 3, 0) + cst(2, 1, 2, 1, 1) + cst(10, 0.1, 0.2, 0.3, 0.4) + cst(11, 0.9, 0.8, 0.7, 0.6) + cst(12, 0.0625, 0.0625, 0.0625, 0.0625)
for model in ("ps_2_x", "ps_3_0"):
    for p in ("p0.x", "p0.y", "p0.z", "p0.w", "!p0.y", "!p0.w", "!p0.x", "!p0.z"):
        cond = P0["xyzw".index(p[-1])] != p.startswith("!")
        want = (0.9, 0.8, 0.7, 0.6) if cond else (0.1, 0.2, 0.3, 0.4)
        S.case("if.%s.%s" % (model, p), "%s\n%smov r0, c10\nif %s\nmov r0, c11\nendif\nmov oC0, r0\n" % (model, SETP, p), PREG, want)
        S.case("callnz.%s.%s" % (model, p), "%s\n%smov r0, c10\ncallnz l0, %s\nmov oC0, r0\nret\nlabel l0\nmov r0, c11\nret\n" % (model, SETP, p), PREG, want)
        # breakp leaves the loop after the first iteration when the condition holds (4 iterations: 4 * c12 otherwise)
        n = 1 if cond else 4
        S.case("breakp.%s.%s" % (model, p), "%s\ndefi i0, 4, 0, 1, 0\n%smov r0, c13\nrep i0\nadd r0, r0, c12\nbreakp %s\nendrep\nmov oC0, r0\n" % (model, SETP, p),
               PREG + cst(13, 0, 0, 0, 0), tuple(n * 0.0625 for _ in range(4)))
def vsflow(model, body, subs):
    """vertex shader whose main program ends with `ret` and is followed by the subroutines `subs`"""
    hdr = "dcl_position o0\ndcl_texcoord0 o1\n" if model == "vs_3_0" else ""
    out = "mov o0, c20\nmov o1, r0\n" if model == "vs_3_0" else "mov oPos, c20\nmov oT0, r0\n"
    return model + "\n" + hdr + body + out + "ret\n" + subs
for model in ("vs_2_x", "vs_3_0"):
    for p in ("p0.x", "!p0.y", "p0.w"):
        cond = P0["xyzw".index(p[-1])] != p.startswith("!")
        want = (0.9, 0.8, 0.7, 0.6) if cond else (0.1, 0.2, 0.3, 0.4)
        S.case("callnz.%s.%s" % (model, p), vsflow(model, SETP + "mov r0, c10\ncallnz l0, %s\n" % p, "label l0\nmov r0, c11\nret\n"), V0 + PREG, want, ps=False)

# ---- class 5
# sgn (MS sgn page: dest = -1, 0, +1 by the sign of src0; the two extra operands of the 3-source form are scratch registers whose
# contents after the instruction are undefined: the translator never writes them).  vs_3_0 has both forms (vkd3d: 1 source).
SV = cst(1, -2.0, 0.0, 3.0, -0.5)
want = tuple(float(sgn(x)) for x in (-2.0, 0.0, 3.0, -0.5))
S.case("sgn.vs_3_0.1src", vs("vs_3_0", "mov r0, c0\nsgn r0, c1\n"), V0 + SV, want, ps=False)
S.case("sgn.vs_3_0.3src", vs("vs_3_0", "mov r0, c0\nsgn r0, c1, r1, r2\n"), V0 + SV, want, ps=False)
S.case("sgn.vs_2_x.3src", vs("vs_2_x", "mov r0, c0\nsgn r0, c1, r1, r2\n"), V0 + SV, want, ps=False)
S.case("sgn.vs_3_0.mask", vs("vs_3_0", "mov r0, c0\nsgn r0.yw, c1\n"), V0 + SV, (0.25, 0.0, 0.75, -1.0), ps=False)
# sgn r0, c0[aL] with aL = 1 (one iteration of loop aL, i0): reads c1; the relative token follows the source token
S.case("sgn.vs_3_0.rel", "vs_3_0\ndcl_position o0\ndcl_texcoord0 o1\ndefi i0, 1, 1, 1, 0\nmov r0, c0\nloop aL, i0\nsgn r0, c0[aL]\nendloop\nmov o0, c20\nmov o1, r0\n",
       V0 + SV, want, ps=False)
# predicated: p0 = (c2 > c3) = (true, false, false, false); the first sgn runs, the second does not
S.case("sgn.vs_3_0.pred", vs("vs_3_0", "mov r1, c2\nmov r2, c3\nsetp_gt p0, r1, r2\nmov r0, c0\n(p0.x) sgn r0, c1\n(!p0.x) sgn r0.x, c2\n"),
       V0 + SV + cst(2, 5, 5, 5, 5) + cst(3, 1, 9, 9, 9), want, ps=False)
# matrix write masks: m4x4 / m3x4 with .xyz (MS: .xyzw or .xyz): component i = src0 . row i, w untouched
SRC4, ROWS = (1.0, 2.0, 3.0, 4.0), [(1.0, 0.0, 0.5, 1.0), (0.0, 2.0, 1.0, 0.25), (3.0, 1.0, 0.0, 0.5), (9.0, 9.0, 9.0, 9.0)]
MAT = cst(1, *SRC4) + "".join(cst(5 + i, *r) for i, r in enumerate(ROWS))
dotn = lambda a, b, n: sum(x * y for x, y in zip(a[:n], b[:n]))
for model in ("vs_1_1", "vs_2_0", "vs_3_0"):
    S.case("m4x4.xyz.%s" % model, vs(model, "mov r0, c0\nm4x4 r0.xyz, c1, c5\n"), V0 + MAT, tuple(dotn(SRC4, r, 4) for r in ROWS[:3]) + (BASE[3],), ps=False)
    S.case("m3x4.xyz.%s" % model, vs(model, "mov r0, c0\nm3x4 r0.xyz, c1, c5\n"), V0 + MAT, tuple(dotn(SRC4, r, 3) for r in ROWS[:3]) + (BASE[3],), ps=False)
    S.case("m4x4.full.%s" % model, vs(model, "m4x4 r0, c1, c5\n"), V0 + MAT, tuple(dotn(SRC4, r, 4) for r in ROWS), ps=False)
# _sat on frc / sincos in a pixel shader (the table's sat_except_vs); sincos .xyz: (cos, sin, 0): z undefined by MS, written 0
X = 3.0
for model in ("ps_2_0", "ps_2_x", "ps_3_0"):
    tail = "" if model == "ps_3_0" else ", c5, c6"
    S.case("sincos_sat.%s" % model, "%s\nmov r0, c0\nmov r1, c1\nsincos_sat r0.xy, r1.x%s\nmov oC0, r0\n" % (model, tail), cst(0, *BASE) + cst(1, X, 0, 0, 0) + cst(5, 1, 1, 1, 1) + cst(6, 2, 2, 2, 2),
           (min(max(math.cos(X), 0.0), 1.0), math.sin(X), BASE[2], BASE[3]))
    S.case("sincos.xyz.%s" % model, "%s\nmov r0, c0\nmov r1, c1\nsincos r0.xyz, r1.x%s\nmov oC0, r0\n" % (model, tail), cst(0, *BASE) + cst(1, X, 0, 0, 0) + cst(5, 1, 1, 1, 1) + cst(6, 2, 2, 2, 2),
           (math.cos(X), math.sin(X), 0.0, BASE[3]))
    S.case("frc_sat.%s" % model, "%s\nmov r0, c0\nmov r1, c1\nfrc_sat r0.xy, r1\nmov oC0, r0\n" % model, cst(0, *BASE) + cst(1, 1.75, -0.25, 0, 0), (0.75, 0.75, BASE[2], BASE[3]))
for model in ("vs_2_0", "vs_3_0"):
    tail = "" if model == "vs_3_0" else ", c5, c6"
    S.case("sincos.xyz.%s" % model, vs(model, "mov r0, c0\nsincos r0.xyz, c1.x%s\n" % tail), V0 + cst(1, X, 0, 0, 0) + cst(5, 1, 1, 1, 1) + cst(6, 2, 2, 2, 2), (math.cos(X), math.sin(X), 0.0, BASE[3]), ps=False)
# vs_1_1 frc: any write mask (the table's x_y_xy_vs1 and the full mask)
FR = cst(1, 2.75, 1.25, -0.5, 3.0)
S.case("frc.x.vs_1_1", vs("vs_1_1", "mov r0, c0\nfrc r0.x, c1\n"), V0 + FR, (0.75, BASE[1], BASE[2], BASE[3]), ps=False)
S.case("frc.all.vs_1_1", vs("vs_1_1", "frc r0, c1\n"), V0 + FR, (0.75, 0.25, 0.5, 0.0), ps=False)
# label numbers above 15 (sm3 allows l0..l2047) and a call to a subroutine defined later than its caller (call-depth 4)
for model, tail in (("vs_3_0", None), ("ps_3_0", None)):
    for lab in (17, 2047):
        body = "mov r0, c10\ncall l%d\n" % lab
        if model == "ps_3_0": asm = "ps_3_0\n" + body + "mov oC0, r0\nret\nlabel l%d\nmov r0, c11\nret\n" % lab
        else: asm = "vs_3_0\ndcl_position o0\ndcl_texcoord0 o1\n" + body + "mov o0, c20\nmov o1, r0\nret\nlabel l%d\nmov r0, c11\nret\n" % lab
        S.case("label.l%d.%s" % (lab, model), asm, V0 + PREG, (0.9, 0.8, 0.7, 0.6), ps=(model == "ps_3_0"))
CH = "mov r0, c0\ncall l0\n"
LAB = "".join("label l%d\nadd r0, r0, c1\n%sret\n" % (k, "call l%d\n" % (k + 1) if k < 3 else "") for k in range(4))
want = tuple(BASE[i] + 4 * (0.5, 0.25, 0.125, 0.0625)[i] for i in range(4))
CC = cst(1, 0.5, 0.25, 0.125, 0.0625)
for model in ("ps_2_x", "ps_3_0"):
    S.case("calldepth4.%s" % model, model + "\n" + CH + "mov oC0, r0\nret\n" + LAB, V0 + CC, want)
for model in ("vs_2_x", "vs_3_0"):
    hdr = "dcl_position o0\ndcl_texcoord0 o1\n" if model == "vs_3_0" else ""
    out = "mov o0, c20\nmov o1, r0\n" if model == "vs_3_0" else "mov oPos, c20\nmov oT0, r0\n"
    S.case("calldepth4.%s" % model, model + "\n" + hdr + CH + out + "ret\n" + LAB, V0 + CC, want, ps=False)
# ---- relative constant addressing needs no CTAB: c[a0 + n] / c[aL + n] read c[0..N) of the stage, N = 256 (vs) / 224 (ps), 0 outside
RC = "".join(cst(n, n * 1.0, 100.0 + n, 0.0, 0.0) for n in (10, 11, 12, 222, 223, 224, 225))
S.case("relc.vs_2_0.a0", vs("vs_2_0", "mov r1, c1\nmova a0.x, r1.x\nmov r0, c[a0.x + 10]\n"), V0 + RC + cst(1, 2.0, 0, 0, 0), (12.0, 112.0, 0.0, 0.0), ps=False)
S.case("relc.vs.limit256", vs("vs_3_0", "mov r1, c1\nmova a0.x, r1.x\nmov r0, c[a0.x + 10]\n"), V0 + RC + cst(255, 7, 8, 9, 10) + cst(1, 245.0, 0, 0, 0), (7.0, 8.0, 9.0, 10.0), ps=False)
S.case("relc.vs.beyond256", vs("vs_3_0", "mov r1, c1\nmova a0.x, r1.x\nmov r0, c[a0.x + 10]\n"), V0 + RC + cst(1, 246.0, 0, 0, 0), (0.0, 0.0, 0.0, 0.0), ps=False)
S.case("relc.ps_3_0.limit224", "ps_3_0\ndefi i0, 3, 0, 1, 0\nmov r0, c0\nloop aL, i0\nadd r0, r0, c[aL + 222]\nendloop\nmov oC0, r0\n", cst(0, 0, 0, 0, 0) + RC,
       (222.0 + 223.0, 322.0 + 323.0, 0.0, 0.0))                   # aL = 0, 1, 2: c222 + c223 + (c224 is outside the ps file: 0)
S.done()
