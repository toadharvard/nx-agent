"""model.py of tests/l6/ffpdev: the python oracle. A device-state model that emits D3D9 calls as case-file lines (cases_*.txt) and, per evaluation, the INPUT words of the fixed-function state modules built from the same
description (exp_*.txt), plus the expected program inputs / texture stages / varyings by the rules written in render/d3d9/dev/ffp_sets.cpp (V1, V2, S1, S2), here re-derived from the GLSL programs' reading of their inputs.
Nothing in this file looks at product code except `glsl_locations` (the `layout (location = n) in` lines of ffp.vert.glsl, cross-checked against the location table below)."""
import re, struct

# ---- D3D9 numbers (d3d9types.h)
RS = dict(ZENABLE=7, SHADEMODE=9, CULLMODE=22, ALPHATESTENABLE=15, FOGENABLE=28, SPECULARENABLE=29, FOGCOLOR=34, FOGTABLEMODE=35, FOGSTART=36, FOGEND=37, FOGDENSITY=38, RANGEFOGENABLE=48, TEXTUREFACTOR=60,
          CLIPPING=136, LIGHTING=137, AMBIENT=139, FOGVERTEXMODE=140, COLORVERTEX=141, LOCALVIEWER=142, NORMALIZENORMALS=143, DIFFUSEMATERIALSOURCE=145, SPECULARMATERIALSOURCE=146,
          AMBIENTMATERIALSOURCE=147, EMISSIVEMATERIALSOURCE=148, VERTEXBLEND=151, CLIPPLANEENABLE=152, POINTSIZE=154, POINTSIZE_MIN=155, POINTSCALEENABLE=157, POINTSCALE_A=158, POINTSCALE_B=159,
          POINTSCALE_C=160, POINTSIZE_MAX=166, INDEXEDVERTEXBLENDENABLE=167, TWEENFACTOR=170)
VS_RS = [RS[n] for n in ("FOGENABLE SPECULARENABLE FOGTABLEMODE FOGSTART FOGEND FOGDENSITY RANGEFOGENABLE CLIPPING LIGHTING AMBIENT FOGVERTEXMODE COLORVERTEX LOCALVIEWER NORMALIZENORMALS DIFFUSEMATERIALSOURCE "
                         "SPECULARMATERIALSOURCE AMBIENTMATERIALSOURCE EMISSIVEMATERIALSOURCE VERTEXBLEND CLIPPLANEENABLE POINTSIZE POINTSIZE_MIN POINTSCALEENABLE POINTSCALE_A POINTSCALE_B POINTSCALE_C "
                         "POINTSIZE_MAX INDEXEDVERTEXBLENDENABLE TWEENFACTOR").split()]
FLOAT_RS = {RS[n] for n in "FOGSTART FOGEND FOGDENSITY POINTSIZE POINTSIZE_MIN POINTSCALE_A POINTSCALE_B POINTSCALE_C POINTSIZE_MAX TWEENFACTOR".split()}
TSS = dict(COLOROP=1, COLORARG1=2, COLORARG2=3, ALPHAOP=4, ALPHAARG1=5, ALPHAARG2=6, BUMPENVMAT00=7, BUMPENVMAT01=8, BUMPENVMAT10=9, BUMPENVMAT11=10, TEXCOORDINDEX=11, BUMPENVLSCALE=22, BUMPENVLOFFSET=23,
           TEXTURETRANSFORMFLAGS=24, COLORARG0=26, ALPHAARG0=27, RESULTARG=28, CONSTANT=32)
PS_TSS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 22, 23, 24, 26, 27, 28, 32]          # the order of the I line (t_ffp.cpp oracle_ps kT)
D3DTS_VIEW, D3DTS_PROJECTION, D3DTS_TEXTURE0, D3DTS_WORLD = 2, 3, 16, 256
USAGE = dict(POSITION=0, BLENDWEIGHT=1, BLENDINDICES=2, NORMAL=3, PSIZE=4, TEXCOORD=5, TANGENT=6, BINORMAL=7, TESSFACTOR=8, POSITIONT=9, COLOR=10, FOG=11, DEPTH=12, SAMPLE=13)
def PAIR(u, i): return u * 16 + i
COMPS = [1, 2, 3, 4, 4, 4, 2, 4, 4, 2, 4, 2, 4, 3, 3, 2, 4]                    # D3DDECLTYPE_FLOAT1 .. FLOAT16_4: components the fixed-function pipeline reads
BYTES = [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8]
D3DCOLOR_T, UBYTE4_T = 4, 5

def f2u(x): return struct.unpack("<I", struct.pack("<f", x))[0]
def u2f(u): return struct.unpack("<f", struct.pack("<I", u))[0]
def fnv64(b):
    h = 1469598103934665603
    for c in b: h = ((h ^ c) * 1099511628211) & (2**64 - 1)
    return h
IDENT = [f2u(1.0) if i % 5 == 0 else 0 for i in range(16)]


# ---- FVF -> what the fixed-function program is told (the grammar and layout rules of res_fvf.cpp's header, restated)
def fvf_valid(fvf):
    pos = fvf & 0x400E
    return (pos == 0 or pos == 0x4002 or 2 <= pos <= 0xE) and not (fvf & 1) and not (fvf & 0x6000) and ((fvf >> 8) & 15) <= 8 and fvf != 0

def fvf_elements(fvf):
    """[(type, usage, index)] in stream order"""
    pos = fvf & 0x400E; ntex = (fvf >> 8) & 15; el = []
    blend = pos != 0x4002 and (pos & 0xE) > 4 and pos != 0
    idx = blend and (pos == 0xE or (fvf & (0x1000 | 0x8000)))
    nbeta = 1 + ((pos & 0xE) - 6) // 2 if blend else 0; nw = nbeta - (1 if idx else 0)
    b2c = pos == 8 and (fvf & 0x8000)
    if pos: el.append((3 if (pos in (4, 0x4002)) else 2, USAGE["POSITIONT"] if pos == 4 else USAGE["POSITION"], 0))
    if blend and nw > 0: el.append((D3DCOLOR_T if b2c else nw - 1, USAGE["BLENDWEIGHT"], 0))
    if idx: el.append((UBYTE4_T if ((fvf & 0x1000) or b2c) else D3DCOLOR_T if (fvf & 0x8000) else 0, USAGE["BLENDINDICES"], 0))
    if fvf & 0x10: el.append((2, USAGE["NORMAL"], 0))
    if fvf & 0x20: el.append((0, USAGE["PSIZE"], 0))
    if fvf & 0x40: el.append((D3DCOLOR_T, USAGE["COLOR"], 0))
    if fvf & 0x80: el.append((D3DCOLOR_T, USAGE["COLOR"], 1))
    for i in range(ntex): el.append(([1, 2, 3, 0][(fvf >> (16 + 2 * i)) & 3], USAGE["TEXCOORD"], i))
    return el

def decl_words(elements):
    """NxFfpVsDecl (20 words) of [(type, usage, index, method)]: the first element of a (usage, index) wins, Method != 0 is not a stream input, TEXCOORD index >= 8 ignored"""
    posT = nbw = bidx = nrm = psz = c0 = c1 = p1 = n1 = 0; ntex = [0] * 8; seen = set()
    for (t, u, i, m) in elements:
        if m != 0 or u > 13 or i > 15 or (u, i) in seen: continue
        seen.add((u, i)); k = COMPS[t]
        if u == USAGE["POSITIONT"] and i == 0: posT = 1
        elif u == USAGE["POSITION"] and i == 1: p1 = 1
        elif u == USAGE["BLENDWEIGHT"] and i == 0: nbw = k
        elif u == USAGE["BLENDINDICES"] and i == 0: bidx = 2 if t == D3DCOLOR_T else 1
        elif u == USAGE["NORMAL"]: nrm, n1 = (1, n1) if i == 0 else (nrm, 1) if i == 1 else (nrm, n1)
        elif u == USAGE["PSIZE"] and i == 0: psz = 1
        elif u == USAGE["COLOR"]: c0, c1 = (1, c1) if i == 0 else (c0, 1) if i == 1 else (c0, c1)
        elif u == USAGE["TEXCOORD"] and i < 8: ntex[i] = k
    return [posT, nbw, bidx, nrm, psz, c0, c1, p1, n1, 0] + ntex + [0, 0]

def glsl_locations(path):
    """the vertex attribute locations the program declares: {name: location}"""
    return {m.group(2): int(m.group(1)) for m in re.finditer(r"layout\s*\(\s*location\s*=\s*(\d+)\s*\)\s*in\s+vec4\s+(\w+)", open(path).read())}

# attribute table of the program, by GLSL names (ffp.vert.glsl) -> role; the locations come from the source and are checked against LOCS below
LOCS = {"aPos": 0, "aBW": 1, "aBI": 2, "aNrm": 3, "aC0": 4, "aC1": 5, **{"aTex%d" % i: 6 + i for i in range(8)}, "aPsz": 14}

def vs_inputs(decl, vblend, indexed, tci):
    """[(pair, loc)] ascending by location: the pairs the program reads for these states (ffp_sets.cpp V1/V2)"""
    posT, nbw, bidx, nrm, psz, c0, c1, p1, n1 = decl[:9]; ntex = decl[10:18]
    weights, zero, tween = vblend in (1, 2, 3), vblend == 256, vblend == 255
    out = [(PAIR(USAGE["POSITIONT"] if posT else USAGE["POSITION"], 0), 0)]
    if weights and nbw > 0: out.append((PAIR(USAGE["BLENDWEIGHT"], 0), 1))
    if tween and p1: out.append((PAIR(USAGE["POSITION"], 1), 1))
    if indexed and (weights or zero) and bidx: out.append((PAIR(USAGE["BLENDINDICES"], 0), 2))
    if tween and n1: out.append((PAIR(USAGE["NORMAL"], 1), 2))
    if nrm: out.append((PAIR(USAGE["NORMAL"], 0), 3))
    if c0: out.append((PAIR(USAGE["COLOR"], 0), 4))
    if c1: out.append((PAIR(USAGE["COLOR"], 1), 5))
    used = set()
    for s in range(8):
        hi, lo = tci[s] >> 16, tci[s] & 0xFFFF
        if not (1 <= hi <= 4) and lo < 8 and ntex[lo] > 0: used.add(lo)
    for t in sorted(used): out.append((PAIR(USAGE["TEXCOORD"], t), 6 + t))
    if psz: out.append((PAIR(USAGE["PSIZE"], 0), 14))
    return out

def ps_sets(colorop, kinds):
    mask = 0
    for s in range(8):
        if colorop[s] == 1: break
        if kinds[s] != 0: mask |= 1 << s
    pairs = [PAIR(USAGE["COLOR"], 0), PAIR(USAGE["COLOR"], 1)] + [PAIR(USAGE["TEXCOORD"], s) for s in range(8) if mask >> s & 1] + [PAIR(USAGE["FOG"], 0)]
    return mask, pairs


class Dev:
    """the device state the modules read, as the case files set it; every method appends the D3D9 call to `cases` and updates the model"""
    def __init__(self):
        self.cases, self.exp = [], []; self.rs = {}; self.tss = [dict() for _ in range(8)]; self.xf = {}; self.mat = [0] * 17; self.vp = [0, 0, 64, 64]
        self.lights = {}; self.enabled = set(); self.clip = [[0] * 4 for _ in range(6)]; self.kind = [0] * 8; self.fvf = 0; self.elements = None; self.nq = 0; self.family = {}
    def line(self, tag, *w): self.cases.append(tag + " " + " ".join("%x" % (x & 0xFFFFFFFF) for x in w))
    def set_rs(self, i, v): self.rs[i] = v & 0xFFFFFFFF; self.line("R", i, v)
    def set_tss(self, s, t, v): self.tss[s][t] = v & 0xFFFFFFFF; self.line("T", s, t, v)
    def set_xf(self, st, m): self.xf[st] = list(m); self.line("X", st, *m)
    def set_mat(self, w): self.mat = list(w); self.line("M", *w)
    def set_vp(self, x, y, w, h): self.vp = [x, y, w, h]; self.line("V", x, y, w, h)
    def set_light(self, idx, w): self.lights[idx] = list(w); self.line("L", idx, *w)
    def enable(self, idx, on):
        if idx not in self.lights: raise SystemExit("model: LightEnable of an unset light")
        (self.enabled.add if on else self.enabled.discard)(idx); self.line("E", idx, 1 if on else 0)
    def set_clip(self, i, p): self.clip[i] = list(p); self.line("P", i, *p)
    def set_tex(self, s, k): self.kind[s] = k; self.line("S", s, k)
    def color_tex(self, s, argb): self.line("C", s, argb)
    def set_fvf(self, fvf): self.fvf = fvf; self.elements = None; self.line("F", fvf)
    def set_decl(self, els):
        """els: [(stream, offset, type, method, usage, index)]"""
        self.elements = list(els); self.fvf = None; self.line("D", len(els), *[x for e in els for x in e])
    def decl(self):
        if self.elements is not None: return decl_words([(t, u, i, m) for (_s, _o, t, m, u, i) in self.elements])
        return decl_words([(t, u, i, 0) for (t, u, i) in fvf_elements(self.fvf)])
    def palette(self): return [self.xf[D3DTS_WORLD + i] for i in range(256)]
    def key_words(self):
        k = list(self.decl()); k.append(len(VS_RS))
        for i in VS_RS: k += [i, self.rs[i]]
        k += [self.tss[s][TSS["TEXCOORDINDEX"]] for s in range(8)] + [self.tss[s][TSS["TEXTURETRANSFORMFLAGS"]] for s in range(8)]
        k += self.xf[D3DTS_VIEW] + self.xf[D3DTS_PROJECTION] + self.xf[D3DTS_WORLD]
        for i in range(8): k += self.xf[D3DTS_TEXTURE0 + i]
        for p in self.clip: k += p
        k += self.mat + self.vp
        act = sorted(self.enabled)[:8]; k.append(len(act))
        for i in act: k += self.lights[i]
        return k
    def ps_words(self):
        w = []
        for s in range(8): w += [self.tss[s][t] for t in PS_TSS]
        return w + [self.rs[RS["TEXTUREFACTOR"]], self.rs[RS["SPECULARENABLE"]]] + list(self.kind)
    def q(self, family, tag="Q", *draw):
        self.family[family] = self.family.get(family, 0) + 1; self.nq += 1
        d = self.decl(); tci = [self.tss[s][TSS["TEXCOORDINDEX"]] for s in range(8)]
        ins = vs_inputs(d, self.rs[RS["VERTEXBLEND"]], self.rs[RS["INDEXEDVERTEXBLENDENABLE"]] != 0, tci)
        mask, pairs = ps_sets([self.tss[s][TSS["COLOROP"]] for s in range(8)], self.kind)
        pal = b"".join(struct.pack("<16I", *m) for m in self.palette()); h = fnv64(pal)
        self.exp.append("K " + " ".join("%x" % x for x in self.key_words()))
        self.exp.append("I " + " ".join("%x" % x for x in self.ps_words()))
        self.exp.append("A %x " % len(ins) + " ".join("%x %x" % pl for pl in ins))
        self.exp.append("B %x %x " % (mask, len(pairs)) + " ".join("%x" % p for p in pairs))
        self.exp.append("H %x %x" % (h & 0xFFFFFFFF, h >> 32))
        self.cases.append(tag + (" " + " ".join("%x" % x for x in draw[0]) if draw else ""))
