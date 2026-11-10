"""Record generators: random full states (rand_rec) and the structured sweep (sweep_recs): every value of every
enumeration of the vertex pipeline (valid and out of range) crossed with random well-conditioned bases."""
import math, random
import numpy as np
from vsrec import bits, pack, WORDS, f32

def F(x): return float(np.float32(x))
def Fs(a): return [F(x) for x in np.asarray(a, dtype=np.float64).ravel()]
def rot(r):
    q = np.array([r.gauss(0, 1) for _ in range(4)]); q /= np.linalg.norm(q); w, x, y, z = q
    return np.array([[1 - 2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)], [2*(x*y+z*w), 1 - 2*(x*x+z*z), 2*(y*z-x*w)], [2*(x*z-y*w), 2*(y*z+x*w), 1 - 2*(x*x+y*y)]])
def affine(r, tmax=3.0, sing=0.04):
    m = np.eye(4); s = r.uniform(0.5, 2.0)
    m[:3, :3] = rot(r) * s * (np.array([1, 1, -1]) if r.random() < 0.15 else 1)
    if r.random() < sing: m[:3, :3][r.randrange(3)] = 0.0               # singular -> adjugate branch
    m[3, :3] = [r.uniform(-tmax, tmax) for _ in range(3)]
    return m
def rnd_u32(r): return r.randint(0, 2**32 - 1)
def pick(r, common, rare, p=0.04): return r.choice(rare) if r.random() < p else r.choice(common)

def rand_rs(r):
    rs = [rnd_u32(r) for _ in range(210)]                              # unrelated slots hold junk: must be ignored
    S = lambda i, v: rs.__setitem__(i, v & 0xFFFFFFFF)
    FB = lambda i, x: S(i, bits(x))
    big = [4, 5, 255, 256, 0x7FFFFFFF, 0xFFFFFFFF]
    S(151, pick(r, [0, 1, 2, 3, 255, 256], [4, 7, 100, 257, 0xFFFFFFFF])); S(167, r.choice([0, 1, 1, 7, 0xFFFFFFFF]))
    for i in (143, 137, 141, 29, 142, 28, 48, 157, 136): S(i, pick(r, [0, 1], [2, 0x80000000, 0xFFFFFFFF], 0.05))
    S(140, pick(r, [0, 1, 2, 3], big)); S(35, pick(r, [0, 0, 1, 2, 3], big))
    for i in (145, 146, 147, 148): S(i, pick(r, [0, 1, 2], [3, 5, 0xFFFFFFFF]))
    S(152, r.choice([0, 63, r.randint(0, 63), rnd_u32(r)])); S(139, rnd_u32(r))
    st = r.uniform(0, 5); FB(36, st); FB(37, st + (r.choice([0.0, -1.0]) if r.random() < 0.04 else r.uniform(1, 20))); FB(38, r.uniform(0.01, 0.4))
    FB(170, r.choice([0.0, 1.0, r.random(), r.uniform(-0.5, 1.5)]))
    FB(154, r.uniform(0.5, 8)); FB(155, r.uniform(0, 2)); FB(166, r.uniform(2, 64) if r.random() > 0.05 else r.uniform(-1, 1))
    if r.random() < 0.05: FB(158, 0.0); FB(159, 0.0); FB(160, 0.0)
    else: FB(158, r.uniform(0.1, 1)); FB(159, r.choice([0.0, r.uniform(0, 0.1)])); FB(160, r.choice([0.0, r.uniform(0, 0.02)]))
    return rs

def rand_light(r):
    t = pick(r, [1, 2, 3], [0, 4, 7, 0xFFFFFFFF], 0.06)
    c = lambda: Fs([r.random() for _ in range(3)] + [r.random()])
    th = r.uniform(0.1, 2.5)
    return [t] + [bits(x) for x in c() + c() + c() + Fs([r.uniform(-3, 3), r.uniform(-3, 3), r.uniform(-3, 3)])
            + Fs([r.gauss(0, 1) for _ in range(3)]) + Fs([r.choice([8.0, 40.0, r.uniform(2, 60)]), r.choice([0.0, 1.0, r.uniform(0, 4)]),
            r.choice([1.0, 0.0, r.uniform(0.1, 1)]), r.choice([0.0, r.uniform(0, 0.3)]), r.choice([0.0, r.uniform(0, 0.05)]),
            th, th + r.uniform(0, 1.0)])]

def rand_rec(r, sing_view=True):
    pT = r.random() < 0.1
    d = [int(pT), pick(r, [0, 1, 2, 3, 4], [5, 9, 0xFFFFFFFF]), r.choice([0, 0, 1, 2]), int(r.random() < 0.8), int(r.random() < 0.3),
         int(r.random() < 0.7), int(r.random() < 0.6), int(r.random() < 0.5), int(r.random() < 0.5), rnd_u32(r)]
    d += [pick(r, [0, 1, 2, 3, 4, 2, 2], [5, 9, 0xFFFFFFFF]) for _ in range(8)] + [rnd_u32(r), rnd_u32(r)]
    view = affine(r, 2.0, 0.0); view[3, 2] += r.uniform(5, 12)
    if sing_view and r.random() < 0.03:       # singular view: exact zero row (its inverse does not exist -> adjugate rule); no normals then,
        view[:3, :3][r.randrange(3)] = 0.0   # because W0*V would be singular only up to rounding (ill-conditioned for ANY inverse)
        d[3] = 0; d[8] = 0
    pj = np.zeros((4, 4)); pj[0, 0] = r.uniform(0.5, 2); pj[1, 1] = r.uniform(0.5, 2); pj[2, 2] = r.uniform(1, 2); pj[2, 3] = 1; pj[3, 2] = r.uniform(-2, 0)
    pal = [affine(r, 1.0, 0.0) for _ in range(16)]
    w0 = pal[0]
    lights = []
    for _ in range(16): lights += rand_light(r)
    tm = []
    for s in range(8): tm += Fs(np.eye(4) if r.random() < 0.3 else np.array([[r.uniform(-1, 1) for _ in range(4)] for _ in range(4)]))
    vw, vh = r.choice([640, 1280, 1920, r.randint(100, 2000)]), r.choice([480, 720, 1080, r.randint(100, 2000)])
    if r.random() < 0.03: vw = 0
    if r.random() < 0.03: vh = 0
    if pT: pos = [r.uniform(0, max(vw, 1)), r.uniform(0, max(vh, 1)), r.random(), r.choice([0.0, r.uniform(0.1, 2)] if r.random() < 0.1 else [r.uniform(0.1, 2)])]
    else: pos = [r.uniform(-2, 2), r.uniform(-2, 2), r.uniform(-2, 2), r.random()]
    bidx = d[2]
    bi = [(r.choice([r.randint(0, 15), r.randint(0, 255), 300, -2, 1.5])) for _ in range(4)]
    if bidx == 2: bi = [r.choice([k / 255.0 for k in (0, 1, 7, 15, 200, 255)]) for _ in range(4)]
    mat = Fs([r.random() for _ in range(16)] + [r.choice([0.0, r.uniform(0, 40)])] + [0, 0, 0])
    rec = dict(decl=d, rs=rand_rs(r), tcIndex=[pick(r, [0, 1, 2, 3, 4, 5, 6, 7, s], [8, 20, 0xFFFF, 0xFFFFFFFF], 0.05) | pick(r, [0, 0, 0x10000, 0x20000, 0x30000, 0x40000], [0x50000, 0xFFFF0000, 0x70000], 0.05) for s in range(8)],
        ttff=[pick(r, [0, 0, 1, 2, 3, 4, 256, 257, 258, 259, 260], [5, 255, 0xFFFF, 0xFFFFFFFF, 0x1FF], 0.08) for _ in range(8)],
        view=Fs(view), proj=Fs(pj), world0=Fs(w0), texm=tm, clip=Fs([r.gauss(0, 1) for _ in range(24)]), mat=mat,
        vp=[float(r.randint(0, 50)), float(r.randint(0, 50)), float(vw), float(vh)], nLights=[r.choice([0, 1, 2, 3, 8, 8, 9, 12, 16, r.randint(0, 16)])], lights=lights,
        posfix=Fs([-1.0 / max(vw, 1), 1.0 / max(vh, 1), r.choice([1.0, -1.0]), 0.0]),
        pos=Fs(pos), pos1=Fs([r.uniform(-2, 2) for _ in range(3)] + [1]), n1=Fs([r.gauss(0, 1) for _ in range(3)] + [0]),
        bw=Fs([r.random() * 0.4 for _ in range(4)]), bi=Fs(bi), n=Fs([r.gauss(0, 0.8) for _ in range(3)] + [0]),
        c0=Fs([r.random() for _ in range(4)]), c1=Fs([r.random() for _ in range(4)]), psize=Fs([r.uniform(0.5, 6), 0, 0, 0]),
        tex=Fs([r.uniform(-2, 2) for _ in range(32)]), palette=[F(x) for m in pal for x in m.ravel()])
    rec["world0"] = Fs(pal[0])
    return rec

# ---- structured sweep: axis = (label, setter(rec, value), values); each value x NB random bases
BIG = [0, 1, 2, 3, 4, 5, 255, 256, 257, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF]
def rs_axis(name, idx, vals): return (name, lambda r, v: r["rs"].__setitem__(idx, v), vals)
AXES = [rs_axis("VERTEXBLEND", 151, BIG + [100, 254]), rs_axis("INDEXED", 167, [0, 1, 2, 0xFFFFFFFF]),
        rs_axis("NORMALIZE", 143, [0, 1, 2, 0xFFFFFFFF]), rs_axis("LIGHTING", 137, [0, 1, 2, 0xFFFFFFFF]),
        rs_axis("COLORVERTEX", 141, [0, 1, 2, 0xFFFFFFFF]), rs_axis("SPECULAR", 29, [0, 1, 2, 0xFFFFFFFF]),
        rs_axis("LOCALVIEWER", 142, [0, 1, 2, 0xFFFFFFFF]), rs_axis("FOGENABLE", 28, [0, 1, 2, 0xFFFFFFFF]),
        rs_axis("FOGVERTEXMODE", 140, BIG), rs_axis("FOGTABLEMODE", 35, BIG), rs_axis("RANGEFOG", 48, [0, 1, 2, 0xFFFFFFFF]),
        rs_axis("POINTSCALE", 157, [0, 1, 2, 0xFFFFFFFF]), rs_axis("CLIPPING", 136, [0, 1, 2, 0xFFFFFFFF])] + \
       [rs_axis(n, i, BIG) for n, i in (("DIFFUSESRC", 145), ("SPECULARSRC", 146), ("AMBIENTSRC", 147), ("EMISSIVESRC", 148))] + \
       [("CLIPPLANEENABLE", lambda r, v: r["rs"].__setitem__(152, v), [0, 1, 2, 4, 8, 16, 32, 63, 64, 0xFFFFFFFF, 0xAAAAAAAA])] + \
       [("TCI_gen%d" % s, lambda r, v, s=s: r["tcIndex"].__setitem__(s, v), [h | l for h in (0, 0x10000, 0x20000, 0x30000, 0x40000, 0x50000, 0x80000, 0xFFFF0000) for l in (0, 3, 7, 8, 0xFFFF)]) for s in (0, 3, 7)] + \
       [("TTFF%d" % s, lambda r, v, s=s: r["ttff"].__setitem__(s, v), [p | c for p in (0, 256, 512) for c in (0, 1, 2, 3, 4, 5, 255)] + [0xFFFFFFFF]) for s in (0, 5)] + \
       [("light_type%d" % k, lambda r, v, k=k: r["lights"].__setitem__(26 * k, v), [0, 1, 2, 3, 4, 5, 0x80000000, 0xFFFFFFFF]) for k in (0, 2, 7, 8)] + \
       [("nLights", lambda r, v: r.__setitem__("nLights", [v]), [0, 1, 7, 8, 9, 15, 16]),
        ("decl.nBlendW", lambda r, v: r["decl"].__setitem__(1, v), [0, 1, 2, 3, 4, 5, 6, 0xFFFFFFFF]),
        ("decl.blendIdx", lambda r, v: r["decl"].__setitem__(2, v), [0, 1, 2]),
        ("decl.nTex0", lambda r, v: r["decl"].__setitem__(10, v), [0, 1, 2, 3, 4, 5, 0xFFFFFFFF]),
        ("decl.nTex1", lambda r, v: r["decl"].__setitem__(11, v), [0, 1, 2, 3, 4, 5])] + \
       [("decl.has%d" % i, lambda r, v, i=i: r["decl"].__setitem__(i, v), [0, 1, 2]) for i in (0, 3, 4, 5, 6, 7, 8)]
BIDX = [0, 1, 2, 3, 7, 15, 16, 31, 255, 256, 300, -1, 1.5, 0.5, 2.5]

def sweep_recs(r, nb=12):
    """-> list of (label, rec). Each axis value is applied to nb independent random bases (same bases for all values)."""
    out = []
    for name, setter, vals in AXES:
        bases = [random.Random(r.randint(0, 2**31)) for _ in range(nb)]
        for v in vals:
            for b in bases:
                rec = rand_rec(random.Random(b.randint(0, 2**31)), False); setter(rec, v); out.append(("%s=%s" % (name, hex(v) if isinstance(v, int) else v), rec))
    for v in BIDX:                                    # matrix palette indices incl. clamp/round, UBYTE4 and D3DCOLOR formats
        for fmt in (1, 2):
            for vb in (1, 2, 3, 256):
                for _ in range(3):
                    rec = rand_rec(r); rec["decl"][0] = 0; rec["decl"][2] = fmt; rec["decl"][1] = 3; rec["rs"][151] = vb; rec["rs"][167] = 1
                    rec["bi"] = [F(v / 255.0 if fmt == 2 else v)] * 4; out.append(("bidx=%s fmt%d vb%d" % (v, fmt, vb), rec))
    return out
