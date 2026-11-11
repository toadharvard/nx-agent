"""gen.py of tests/l6/ffpdev: the state sweeps (parts a, b) and the end-to-end draws (part c) as case files. Every sweep sets the device state through the model (model.Dev), so the expected module inputs are built from the
same description by python. Families are counted (Dev.family)."""
import math, random
from model import *

SPEC_F = [0.0, 1.0, -1.0, 0.5, 2.0, 1e-3, 100.0, -0.25, 7.5]
def rf(r): return r.choice(SPEC_F) if r.random() < 0.35 else round(r.uniform(-3.0, 3.0), 3)
def fw(x): return f2u(x)
SRC = [0, 1, 2, 3, 0x7FFFFFFF]; MODE = [0, 1, 2, 3, 4, 0xFFFFFFFF]; BLEND = [0, 1, 2, 3, 4, 255, 256, 257, 0xFFFFFFFF]
RS_DOM = {RS["FOGENABLE"]: [0, 1, 2], RS["SPECULARENABLE"]: [0, 1, 5], RS["FOGTABLEMODE"]: MODE, RS["FOGVERTEXMODE"]: MODE, RS["RANGEFOGENABLE"]: [0, 1], RS["CLIPPING"]: [0, 1], RS["LIGHTING"]: [0, 1],
          RS["COLORVERTEX"]: [0, 1], RS["LOCALVIEWER"]: [0, 1], RS["NORMALIZENORMALS"]: [0, 1], RS["DIFFUSEMATERIALSOURCE"]: SRC, RS["SPECULARMATERIALSOURCE"]: SRC, RS["AMBIENTMATERIALSOURCE"]: SRC,
          RS["EMISSIVEMATERIALSOURCE"]: SRC, RS["VERTEXBLEND"]: BLEND, RS["CLIPPLANEENABLE"]: [0, 1, 3, 0x3F, 0xFFFF, 0x2A], RS["POINTSCALEENABLE"]: [0, 1], RS["INDEXEDVERTEXBLENDENABLE"]: [0, 1]}
DECL_TYPES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 16]   # the types the device's caps report (DeclTypes = 0x30f: UBYTE4, UBYTE4N, SHORT2N, SHORT4N, FLOAT16_2/4) + the always supported ones
OPS = list(range(0, 28)) + [0xFFFFFFFF, 0x100]
ARGS = [0, 1, 2, 3, 4, 5, 6, 7, 0x12, 0x22, 0x35, 0x3F, 0x40, 0xFFFFFFFF]
TCI = [s | (g << 16) for g in range(0, 6) for s in (0, 1, 7, 8)] + [3, 5 | (2 << 16), 0xFFFF]
TTFF = [0, 1, 2, 3, 4, 5, 0x100, 0x101, 0x102, 0x103, 0x104, 0x105, 0x7FFFFFFF, 0x1FF]

def rand_rs(r, i):
    if i in FLOAT_RS: return fw(rf(r))
    if i == RS["AMBIENT"]: return r.getrandbits(32)
    return r.choice(RS_DOM[i])
def rand_tss(r, s, t):
    if t in (TSS["COLOROP"], TSS["ALPHAOP"]): return r.choice(OPS if r.random() < 0.5 else [1, 2, 3, 4, 7, 12, 13, 26])
    if t in (TSS["COLORARG0"], TSS["COLORARG1"], TSS["COLORARG2"], TSS["ALPHAARG0"], TSS["ALPHAARG1"], TSS["ALPHAARG2"]): return r.choice(ARGS)
    if t == TSS["TEXCOORDINDEX"]: return r.choice(TCI)
    if t == TSS["TEXTURETRANSFORMFLAGS"]: return r.choice(TTFF)
    if t == TSS["RESULTARG"]: return r.choice([0, 1, 2, 3, 4, 5, 6, 7, 0xFFFFFFFF])
    if t == TSS["CONSTANT"]: return r.getrandbits(32)
    return fw(rf(r))
def rmat(r):
    k = r.random()
    if k < 0.12: return list(IDENT)
    if k < 0.2: return [0] * 16                                              # singular: adjugate rule
    m = [fw(rf(r) * 0.7) for _ in range(16)]
    if k < 0.3: m[4:8] = m[0:4]                                              # rank deficient
    if k < 0.7: m[3] = m[7] = m[11] = 0; m[15] = fw(1.0)                    # affine
    return m
def rlight(r):
    t = r.choice([1, 2, 3]); col = lambda: [fw(round(r.uniform(0, 1), 3)) for _ in range(4)]
    d = [fw(rf(r)) for _ in range(3)]
    return [t] + col() + col() + col() + [fw(rf(r)) for _ in range(3)] + d + [fw(r.choice([0.0, 5.0, 50.0, 1e30])), fw(r.choice([0.0, 1.0, 2.0])), fw(r.choice([0.0, 1.0, 0.5])), fw(r.choice([0.0, 0.1, 1.0])),
                                                                       fw(r.choice([0.0, 0.01, 1.0])), fw(r.choice([0.3, 0.8, 1.5, 3.0])), fw(r.choice([0.5, 1.2, 2.4, 3.1]))]
def rmaterial(r): return [fw(round(r.uniform(0, 1), 2)) for _ in range(16)] + [fw(r.choice([0.0, 1.0, 8.0, 32.0, 200.0]))]

VALID_FVFS = [0x2 | 0x10 | 0x40 | 0x80 | (2 << 8), 0x8 | 0x10 | 0x40 | (1 << 8), 0x4 | 0x40 | 0x80 | (1 << 8), 0xA | 0x1000 | 0x10 | (3 << 8), 0xE | 0x10 | 0x20 | 0x40 | (4 << 8), 0x2 | (8 << 8),
              0xC | 0x20 | 0x40 | (2 << 8) | (0x1B << 16), 0x6 | 0x8000 | 0x10]
def randomize_all(d, r):
    for i in VS_RS: d.set_rs(i, rand_rs(r, i))
    for s in range(8):
        for t in PS_TSS + [TSS["TEXCOORDINDEX"]]: d.set_tss(s, t, rand_tss(r, s, t))
    for s in range(8): d.set_tex(s, r.choice([0, 1, 1, 2, 3]))
    d.set_rs(RS["TEXTUREFACTOR"], r.getrandbits(32))
    for st in [D3DTS_VIEW, D3DTS_PROJECTION] + [D3DTS_TEXTURE0 + i for i in range(8)] + [D3DTS_WORLD + i for i in range(256)]: d.set_xf(st, rmat(r) if st < D3DTS_WORLD + 4 else IDENT)
    for i in range(6): d.set_clip(i, [fw(rf(r)) for _ in range(4)])
    d.set_mat(rmaterial(r)); d.set_vp(0, 0, 64, 64)
    d.set_fvf(r.choice(VALID_FVFS))

def gen_a(seed=1):
    r = random.Random(seed); d = Dev(); randomize_all(d, r)
    d.q("init")
    for ctx in VALID_FVFS[:4]:                                              # one-at-a-time sweeps of every enumerated render state value, under four declarations
        d.set_fvf(ctx)
        for i, dom in sorted(RS_DOM.items()):
            for v in dom: d.set_rs(i, v); d.q("rs_enum")
            d.set_rs(i, r.choice(dom))
    for i in (RS["AMBIENT"], RS["TEXTUREFACTOR"]):
        for v in (0, 0xFFFFFFFF, 0x80402010, 0x01FE7F80): d.set_rs(i, v); d.q("rs_color")
    for i in sorted(FLOAT_RS):
        for v in (0.0, 1.0, -1.0, 1e30, -0.0, float("inf"), float("nan"), 3.5): d.set_rs(i, fw(v)); d.q("rs_float")
        d.set_rs(i, fw(rf(r)))
    for s in range(8):                                                      # every stage state over its domain
        for t in PS_TSS + [TSS["TEXCOORDINDEX"]]:
            if t in (TSS["COLOROP"], TSS["ALPHAOP"]): dom = OPS
            elif t in (TSS["COLORARG0"], TSS["COLORARG1"], TSS["COLORARG2"], TSS["ALPHAARG0"], TSS["ALPHAARG1"], TSS["ALPHAARG2"]): dom = ARGS
            elif t == TSS["TEXCOORDINDEX"]: dom = TCI
            elif t == TSS["TEXTURETRANSFORMFLAGS"]: dom = TTFF
            elif t == TSS["RESULTARG"]: dom = [0, 1, 4, 5, 6, 7, 0xFFFFFFFF]
            elif t == TSS["CONSTANT"]: dom = [0, 0xFFFFFFFF, 0x12345678]
            else: dom = [fw(0.0), fw(1.0), fw(-0.5), fw(float("nan"))]
            if s > 1 and t in (TSS["COLOROP"], TSS["ALPHAOP"]): dom = dom[::3]
            for v in dom: d.set_tss(s, t, v); d.q("tss_%s" % ("op" if t in (1, 4) else "arg" if t in (2, 3, 5, 6, 26, 27) else "tci" if t == 11 else "ttff" if t == 24 else "misc"))
            d.set_tss(s, t, rand_tss(r, s, t))
    for s in range(8):                                                      # texture kinds x cascade end
        d.set_tss(s, TSS["COLOROP"], 4)
    for end in range(9):
        for kinds in range(6):
            for s in range(8): d.set_tss(s, TSS["COLOROP"], 1 if s >= end else r.choice([2, 4, 7, 0, 99])); d.set_tex(s, r.choice([0, 1, 2, 3]))
            d.q("cascade")
    for n in range(0, 13):                                                  # lights: counts, types, sparse indices, enable order, redefinition
        for rep in range(8):
            for i in list(d.enabled): d.enable(i, False)
            idxs = r.sample([0, 1, 2, 3, 5, 8, 9, 10, 31, 100, 4000, 65535, 7, 12, 99], n)
            for i in idxs: d.set_light(i, rlight(r)); d.enable(i, True)
            for i in r.sample(sorted(d.lights), min(3, len(d.lights))): d.set_light(i, rlight(r))
            for i in r.sample(sorted(d.lights), min(2, len(d.lights))): d.enable(i, r.random() < 0.5)
            d.set_rs(RS["LIGHTING"], r.choice([0, 1])); d.q("lights")
    for _ in range(4):
        for vb in BLEND:
            for ix in (0, 1):                                               # vertex blending x declarations with weights / indices
                d.set_rs(RS["VERTEXBLEND"], vb); d.set_rs(RS["INDEXEDVERTEXBLENDENABLE"], ix); d.set_fvf(r.choice(VALID_FVFS + [0xE | 0x1000 | 0x10, 0x6 | 0x1000, 0xA | 0x8000, 0x8 | 0x8000 | 0x10])); d.q("blend")
    for _ in range(300):                                                    # texgen / texture matrices / projected
        for s in range(8): d.set_tss(s, TSS["TEXCOORDINDEX"], r.choice(TCI)); d.set_tss(s, TSS["TEXTURETRANSFORMFLAGS"], r.choice(TTFF))
        if r.random() < 0.3: d.set_fvf(r.choice(VALID_FVFS))
        d.q("texgen")
    for k in range(120):                                                    # matrices: general, affine, singular, identity
        for st in (D3DTS_VIEW, D3DTS_PROJECTION, D3DTS_WORLD, D3DTS_TEXTURE0 + r.randrange(8), D3DTS_WORLD + 1 + r.randrange(255)): d.set_xf(st, rmat(r))
        d.q("matrix")
    for k in range(60):
        d.set_mat(rmaterial(r)); d.set_vp(r.randrange(0, 20), r.randrange(0, 20), r.randrange(0, 44), r.randrange(0, 44))
        for i in range(6): d.set_clip(i, [fw(rf(r)) for _ in range(4)])
        d.q("material_viewport_clip")
    for k in range(700):                                                    # random full states
        randomize_all(d, r)
        for s in range(8): d.set_tss(s, TSS["COLOROP"], r.choice([2, 4, 7, 12, 13, 26, 24, 1]))
        d.q("random")
    for k in range(400):                                                    # declarations: random element lists (first wins, methods, streams, usages)
        els, off = [], 0
        for _ in range(r.randrange(0, 12)):
            t = r.choice(DECL_TYPES); u = r.choice([0, 1, 2, 3, 4, 5, 5, 5, 6, 7, 9, 10, 10, 11, 0, 3]); i = r.choice([0, 0, 0, 1, 2, 7, 8]) if u in (0, 3, 5, 10) else r.choice([0, 0, 1])
            els.append((r.choice([0, 0, 0, 1, 2]), off, t, r.choice([0, 0, 0, 0, 1, 2]), u, i)); off += BYTES[t]
        d.set_decl(els); d.set_rs(RS["VERTEXBLEND"], r.choice(BLEND)); d.set_rs(RS["INDEXEDVERTEXBLENDENABLE"], r.choice([0, 1]))
        for s in range(8): d.set_tss(s, TSS["TEXCOORDINDEX"], r.choice(TCI))
        d.q("decl")
    return d

def gen_b(seed=2):
    """all FVF combinations (position field x LASTBETA x normal x psize x diffuse x specular x texture count; the texture size codes random), each with random vertex blend / texcoord index states"""
    r = random.Random(seed); d = Dev(); randomize_all(d, r); n = 0
    for pos in [0, 2, 4, 0x4002] + [(0x6, 0), (0x8, 0), (0xA, 0), (0xC, 0), (0xE, 0)]:
        lbs = [0] if isinstance(pos, int) else [0, 0x1000, 0x8000, 0x9000]
        for lb in lbs:
            p = pos if isinstance(pos, int) else pos[0]
            for flags in range(16):
                for nt in range(9):
                    fvf = p | lb | (0x10 if flags & 1 else 0) | (0x20 if flags & 2 else 0) | (0x40 if flags & 4 else 0) | (0x80 if flags & 8 else 0) | (nt << 8) | (r.getrandbits(16) << 16 if nt else 0)
                    if not fvf_valid(fvf): continue
                    d.set_fvf(fvf)
                    d.set_rs(RS["VERTEXBLEND"], r.choice(BLEND)); d.set_rs(RS["INDEXEDVERTEXBLENDENABLE"], r.choice([0, 1]))
                    for s in range(8):
                        d.set_tss(s, TSS["TEXCOORDINDEX"], r.choice(TCI if r.random() < 0.5 else [s, s, (s + 1) % 8, 0, 7])); d.set_tss(s, TSS["COLOROP"], r.choice([1, 2, 4, 4, 4, 4]))
                        d.set_tex(s, r.choice([0, 1, 1, 1, 2, 3]))
                    d.q("fvf"); n += 1
    return d


# ---- part c: triangles through the draw path
def rot(th, tx, ty, tz=0.0, sc=1.0):
    c, s = math.cos(th) * sc, math.sin(th) * sc
    return [fw(x) for x in (c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, tx, ty, tz, 1)]
def gen_c(seed=3, count=84):
    r = random.Random(seed); d = Dev(); d.set_mat([fw(0.5)] * 4 + [fw(0.7)] * 4 + [fw(0.3)] * 4 + [fw(0.0)] * 4 + [fw(10.0)])
    base = dict(RS)
    for i in VS_RS: d.set_rs(i, 0)
    for s in range(8):
        for t in PS_TSS + [TSS["TEXCOORDINDEX"]]: d.set_tss(s, t, 0)
        d.set_tss(s, TSS["COLOROP"], 1); d.set_tss(s, TSS["ALPHAOP"], 1); d.set_tss(s, TSS["TEXCOORDINDEX"], s); d.set_tss(s, TSS["COLORARG1"], 2); d.set_tss(s, TSS["COLORARG2"], 1); d.set_tss(s, TSS["ALPHAARG1"], 2); d.set_tss(s, TSS["ALPHAARG2"], 1)
        d.set_tex(s, 0)
    for rsn, v in (("ZENABLE", 0), ("SHADEMODE", 2), ("CULLMODE", 1), ("ALPHATESTENABLE", 0), ("FOGCOLOR", 0x00808040), ("TEXTUREFACTOR", 0x80a0c0e0), ("AMBIENT", 0x00303030), ("CLIPPING", 1),
                   ("LOCALVIEWER", 1), ("DIFFUSEMATERIALSOURCE", 1), ("SPECULARMATERIALSOURCE", 2), ("POINTSIZE", fw(1.0)), ("POINTSIZE_MIN", fw(1.0)), ("POINTSIZE_MAX", fw(64.0)), ("FOGSTART", fw(0.0)),
                   ("FOGEND", fw(1.0)), ("FOGDENSITY", fw(1.0)), ("POINTSCALE_A", fw(1.0))): d.set_rs(RS[rsn], v)
    for st in [D3DTS_VIEW, D3DTS_PROJECTION] + [D3DTS_TEXTURE0 + i for i in range(8)] + [D3DTS_WORLD + i for i in range(256)]: d.set_xf(st, IDENT)
    for i in range(6): d.set_clip(i, [0, 0, 0, 0])
    d.set_vp(0, 0, 64, 64)
    for s in range(8): d.color_tex(s, r.getrandbits(32))
    for k in range(count):
        d.set_rs(RS["SHADEMODE"], 1 if k % 11 == 5 else 2)
        d.set_xf(D3DTS_VIEW, rot(r.uniform(-0.5, 0.5), r.uniform(-0.15, 0.15), r.uniform(-0.15, 0.15))); d.set_xf(D3DTS_WORLD, rot(r.uniform(-0.4, 0.4), r.uniform(-0.1, 0.1), r.uniform(-0.1, 0.1), 0.0, 1.0))
        d.set_xf(D3DTS_PROJECTION, IDENT)
        nb = r.choice([0, 0, 0, 1, 2, 3]) if k % 5 == 3 else 0
        fvf = [0x2, 0x6, 0x8, 0xA][nb] | (0x10 if r.random() < 0.8 else 0) | (0x40 if r.random() < 0.7 else 0) | (0x80 if r.random() < 0.5 else 0) | (r.choice([0, 1, 2, 3]) << 8)
        if nb and not (fvf & 0x10): fvf |= 0x10
        d.set_fvf(fvf)                                                       # texture size codes 0 = 2 floats (FLOAT2) for every set
        d.set_rs(RS["VERTEXBLEND"], nb)
        for i in range(1, nb + 1): d.set_xf(D3DTS_WORLD + i, rot(r.uniform(-0.4, 0.4), r.uniform(-0.1, 0.1), r.uniform(-0.1, 0.1)))
        # lighting
        lit = r.random() < 0.7; d.set_rs(RS["LIGHTING"], 1 if lit else 0); d.set_rs(RS["COLORVERTEX"], r.choice([0, 1])); d.set_rs(RS["NORMALIZENORMALS"], r.choice([0, 1]))
        d.set_rs(RS["SPECULARENABLE"], r.choice([0, 1])); d.set_rs(RS["LOCALVIEWER"], r.choice([0, 1]))
        for nm in ("DIFFUSE", "SPECULAR", "AMBIENT", "EMISSIVE"): d.set_rs(RS[nm + "MATERIALSOURCE"], r.choice([0, 1, 2]))
        d.set_mat(rmaterial(r))
        for i in list(d.enabled): d.enable(i, False)
        for j in range(r.choice([0, 1, 2, 3]) if lit else 0):
            li = rlight(r); li[0] = r.choice([1, 2, 3]); li[19] = fw(r.choice([10.0, 100.0])); li[20] = fw(r.choice([0.0, 1.0, 2.0])); li[21] = fw(r.choice([1.0, 0.5])); li[22] = fw(r.choice([0.0, 0.2])); li[23] = fw(0.0)
            li[13:16] = [fw(r.uniform(-1, 1)), fw(r.uniform(-1, 1)), fw(r.uniform(-1, 1))]; li[16:19] = [fw(r.uniform(-1, 1)), fw(r.uniform(-1, 1)), fw(r.uniform(0.2, 1))]
            d.set_light(j, li); d.enable(j, True)
        # fog
        fog = r.random() < 0.35; d.set_rs(RS["FOGENABLE"], 1 if fog else 0); d.set_rs(RS["FOGVERTEXMODE"], r.choice([0, 1, 2, 3])); d.set_rs(RS["RANGEFOGENABLE"], r.choice([0, 1]))
        d.set_rs(RS["FOGSTART"], fw(r.choice([0.0, -0.2]))); d.set_rs(RS["FOGEND"], fw(r.choice([1.0, 0.5, 3.0]))); d.set_rs(RS["FOGDENSITY"], fw(r.choice([0.3, 1.0, 2.0])))
        # texture stages 0-2: 1x1 constant textures, a random cascade
        nst = r.choice([0, 1, 2, 3, 3]); ops = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 21, 24, 25, 26, 17]
        for s in range(8):
            if s < nst:
                d.set_tss(s, TSS["COLOROP"], r.choice(ops)); d.set_tss(s, TSS["ALPHAOP"], r.choice(ops + [1, 0]))
                for t in (TSS["COLORARG0"], TSS["COLORARG1"], TSS["COLORARG2"], TSS["ALPHAARG0"], TSS["ALPHAARG1"], TSS["ALPHAARG2"]): d.set_tss(s, t, r.choice([0, 1, 2, 3, 4, 5, 6, 0x12, 0x22, 0x20, 0x13]))
                d.set_tss(s, TSS["RESULTARG"], r.choice([1, 1, 1, 5])); d.set_tss(s, TSS["CONSTANT"], r.getrandbits(32))
                d.set_tss(s, TSS["TEXCOORDINDEX"], r.choice([s, s, 1 << 16, 2 << 16, 3 << 16, 4 << 16])); d.set_tss(s, TSS["TEXTURETRANSFORMFLAGS"], r.choice([0, 0, 2, 3, 0x102, 0x103]))
                d.set_tss(s, TSS["BUMPENVMAT00"], fw(0.1)); d.set_tss(s, TSS["BUMPENVMAT11"], fw(0.1))
                d.set_tex(s, r.choice([0, 1, 1, 1])); d.color_tex(s, r.getrandbits(32))
                d.set_xf(D3DTS_TEXTURE0 + s, rot(r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(-1, 1)))
            else: d.set_tss(s, TSS["COLOROP"], 1); d.set_tex(s, 0); d.set_tss(s, TSS["TEXTURETRANSFORMFLAGS"], 0)
        if nst == 0: d.set_tss(0, TSS["COLOROP"], r.choice([1, 2, 4])); d.set_tss(0, TSS["COLORARG1"], r.choice([0, 3, 4])); d.set_tss(0, TSS["COLORARG2"], r.choice([0, 1, 4]))
        d.set_rs(RS["TEXTUREFACTOR"], r.getrandbits(32))
        pos = fvf & 0xE; nw = nb; ntex = (fvf >> 8) & 15; verts = [fvf]
        pts = [(-0.5, -0.5), (0.5, -0.4), (-0.1, 0.55)]
        for (px, py) in pts:
            v = [fw(px + r.uniform(-0.08, 0.08)), fw(py + r.uniform(-0.08, 0.08)), fw(r.uniform(0.2, 0.7))]
            if nb:
                ws = [r.uniform(0, 1.0 / nb) for _ in range(nb)]; v += [fw(x) for x in ws]
            if fvf & 0x10: n = [r.uniform(-1, 1), r.uniform(-1, 1), r.uniform(0.2, 1)]; l = math.sqrt(sum(x * x for x in n)); v += [fw(x / l) for x in n]
            if fvf & 0x40: v.append(r.getrandbits(32))
            if fvf & 0x80: v.append(r.getrandbits(32))
            for s in range(ntex): v += [fw(r.uniform(0, 1)), fw(r.uniform(0, 1))]
            verts += v
        d.q("draw", "W", verts)
    return d
