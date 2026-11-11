"""Independent oracle for the D3D9 fixed-function pixel pipeline, written from the D3DTEXTUREOP documentation
formulas as a TABLE (op -> argument slots, blend-factor source, per-channel formula) in float64. Shares no code with
ffp_ps_ref.c. Open points follow WineD3D (see render/d3d9/ffp/README.md). Case = list of 186 words (ffp_test_common.h)."""
import struct

def f32(u): return struct.unpack("<f", struct.pack("<I", u))[0]
def sat(x): return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
def d3dcolor(c): return [((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0, ((c >> 24) & 255) / 255.0]

# name, slots read (0/1/2 = Arg0/Arg1/Arg2), blend factor source (D diffuse, T texture, F tfactor, C current), formula
# formula(x0, x1, x2, a1w, b): x_i = channel value of Arg_i, a1w = alpha of Arg1, b = blend factor alpha
TABLE = {
    1: ("DISABLE", (), None, None),
    2: ("SELECTARG1", (1,), None, lambda x0, x1, x2, a, b: x1),
    3: ("SELECTARG2", (2,), None, lambda x0, x1, x2, a, b: x2),
    4: ("MODULATE", (1, 2), None, lambda x0, x1, x2, a, b: x1 * x2),
    5: ("MODULATE2X", (1, 2), None, lambda x0, x1, x2, a, b: x1 * x2 * 2),
    6: ("MODULATE4X", (1, 2), None, lambda x0, x1, x2, a, b: x1 * x2 * 4),
    7: ("ADD", (1, 2), None, lambda x0, x1, x2, a, b: x1 + x2),
    8: ("ADDSIGNED", (1, 2), None, lambda x0, x1, x2, a, b: x1 + x2 - 0.5),
    9: ("ADDSIGNED2X", (1, 2), None, lambda x0, x1, x2, a, b: (x1 + x2 - 0.5) * 2),
    10: ("SUBTRACT", (1, 2), None, lambda x0, x1, x2, a, b: x1 - x2),
    11: ("ADDSMOOTH", (1, 2), None, lambda x0, x1, x2, a, b: x1 + x2 - x1 * x2),
    12: ("BLENDDIFFUSEALPHA", (1, 2), "D", lambda x0, x1, x2, a, b: b * x1 + (1 - b) * x2),
    13: ("BLENDTEXTUREALPHA", (1, 2), "T", lambda x0, x1, x2, a, b: b * x1 + (1 - b) * x2),
    14: ("BLENDFACTORALPHA", (1, 2), "F", lambda x0, x1, x2, a, b: b * x1 + (1 - b) * x2),
    15: ("BLENDTEXTUREALPHAPM", (1, 2), "T", lambda x0, x1, x2, a, b: x1 + x2 * (1 - b)),
    16: ("BLENDCURRENTALPHA", (1, 2), "C", lambda x0, x1, x2, a, b: b * x1 + (1 - b) * x2),
    17: ("PREMODULATE", (1,), None, lambda x0, x1, x2, a, b: x1),
    18: ("MODULATEALPHA_ADDCOLOR", (1, 2), None, lambda x0, x1, x2, a, b: x1 + a * x2),
    19: ("MODULATECOLOR_ADDALPHA", (1, 2), None, lambda x0, x1, x2, a, b: x1 * x2 + a),
    20: ("MODULATEINVALPHA_ADDCOLOR", (1, 2), None, lambda x0, x1, x2, a, b: (1 - a) * x2 + x1),
    21: ("MODULATEINVCOLOR_ADDALPHA", (1, 2), None, lambda x0, x1, x2, a, b: (1 - x1) * x2 + a),
    22: ("BUMPENVMAP", (1, 2), None, None),
    23: ("BUMPENVMAPLUMINANCE", (1, 2), None, None),
    24: ("DOTPRODUCT3", (1, 2), None, None),            # cross-channel, handled in code
    25: ("MULTIPLYADD", (0, 1, 2), None, lambda x0, x1, x2, a, b: x0 + x1 * x2),
    26: ("LERP", (0, 1, 2), None, lambda x0, x1, x2, a, b: (x1 - x2) * x0 + x2),
}
SLOTS = {k: v[1] for k, v in TABLE.items()}

def nop(v): return v if 1 <= v <= 26 else 0
def slots(op): return SLOTS.get(op, ())

def run(w, sample):
    """w: 186 words; sample(stage, type, (x, y, z)) -> rgba. Returns rgba before fog / alpha test."""
    g = w[144:]
    tf, spec_en = d3dcolor(g[0]), g[1]
    diffuse = [f32(u) for u in g[2:6]]; spec = [f32(u) for u in g[6:10]]
    tcs = [[f32(u) for u in g[10 + 4 * s:14 + 4 * s]] for s in range(8)]
    cur, tmp = diffuse[:], [0.0] * 4
    prev_bump, pm_c, pm_a, prev_tex = 0, False, False, [0.0] * 4
    for s in range(8):
        p = w[18 * s:18 * s + 18]
        cop = nop(p[0])
        if cop == 1: break
        ttype = p[3] if 1 <= p[3] <= 3 else 0
        cargs, aargs = list(p[4:7]), list(p[8:11])        # arg0, arg1, arg2
        def invalid(op, args):                              # no bound texture and a read slot selects TEXTURE
            return ttype == 0 and any((args[i] & 15) == 2 for i in slots(op))
        if invalid(cop, cargs): cop, cargs = 2, [0, 1, 0]
        if cop == 24: aop, aargs = 24, cargs
        else:
            aop = nop(p[1])
            if invalid(aop, aargs): aop, aargs = 2, [0, 1, 0]
        # texture
        tex = [0.0] * 4
        if ttype:
            cnt, proj = p[7] & 255, bool(p[7] & 256) and ttype != 2
            div = tcs[s][cnt - 1] if 1 <= cnt <= 4 else tcs[s][3]
            c = tcs[s][:3]
            if proj and div != 0.0: c = [v / div for v in c]
            if prev_bump:
                q = w[18 * (s - 1):18 * s]
                m = [f32(q[12]), f32(q[13]), f32(q[14]), f32(q[15])]
                c = [c[0] + prev_tex[0] * m[0] + prev_tex[1] * m[2], c[1] + prev_tex[0] * m[1] + prev_tex[1] * m[3], c[2]]
            tex = list(sample(s, ttype, tuple(c)))
        if prev_bump == 2:
            q = w[18 * (s - 1):18 * s]
            k = sat(prev_tex[2] * f32(q[16]) + f32(q[17]))
            tex = [v * k for v in tex]
        if pm_c: cur[:3] = [cur[i] * tex[i] for i in range(3)]
        if pm_a: cur[3] *= tex[3]
        cst = d3dcolor(p[11])
        src = {0: diffuse, 1: cur, 2: tex, 3: tf, 4: spec, 5: tmp, 6: cst}
        def arg(a):
            v = list(src.get(a & 15, [0.0] * 4))
            if a & 16: v = [1 - x for x in v]
            if a & 32: v = [v[3]] * 4
            return v
        def evaluate(op, args):
            if op in (0, 1, 22, 23): return None
            A = [arg(a) for a in args]
            if op == 24:
                d = 4 * sum((A[1][i] - 0.5) * (A[2][i] - 0.5) for i in range(3))
                return [d] * 4
            _, _, fsrc, fn = TABLE[op]
            b = {"D": diffuse, "T": tex, "F": tf, "C": cur}[fsrc][3] if fsrc else 0.0
            return [fn(A[0][ch], A[1][ch], A[2][ch], A[1][3], b) for ch in range(4)]
        rc, ra = evaluate(cop, cargs), evaluate(aop, aargs)
        bump = cop in (22, 23)
        dst = tmp if p[2] == 5 else cur
        if rc is not None and not bump:
            for i in range(3): dst[i] = sat(rc[i])
        wrote_a = ra is not None and not bump
        if wrote_a: dst[3] = sat(ra[3])
        pm_c, pm_a = cop == 17, wrote_a and aop == 17
        prev_bump = 1 if cop == 22 else 2 if cop == 23 else 0
        prev_tex = tex
    return [sat(cur[i] + (spec[i] if spec_en and i < 3 else 0.0)) for i in range(4)]

def texel(stage, ttype, c):                                 # same function as ffp_test_common.h texel()
    out = []
    for k in range(4):
        x = 0.37 * stage + 0.23 * ttype + 0.11 * k + 0.53 * c[0] + 0.29 * c[1] + (0.0 if ttype == 1 else 0.17 * c[2])
        u = 4 * x - 1.5
        out.append(0.5 + 0.5 * u / (1 + abs(u)))
    return out
