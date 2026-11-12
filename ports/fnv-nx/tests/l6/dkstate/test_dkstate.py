#!/usr/bin/env python3
"""test_dkstate.py: tests of render/backend/nx/dk_state.c (nxgpu v4.1 state words -> deko3d state structs).
    python3 -I tests/l6/dkstate/test_dkstate.py [--no-mutations]       needs gcc, the aarch64 cross gcc and the deko3d headers (FNVNX_DK, default /home/user/work/l6/dk)
 a  dk_state.c compiles for the host against the real deko3d.h and for aarch64 with -Wall -Wextra -Werror
 b  every value of every state field is run through the real functions (single-field enumeration over several baselines, the full cross products
    of blend factors x alpha factors, blend ops x dst factors, the four colour masks, cull x fill x flags, and seeded random words) and compared with
    an expectation written from the GL reference (gl4_state.c, gl4_sampler.c: GL enum -> meaning -> Dk enum by NAME through dk_facts.tsv)
 c  the expressibility rule is checked on its own meaning table: an alpha-channel factor meaning, per factor, not the dk_state.c table
 d  errors: precedence, *out all zero on every error, the exact error code
 e  mutations of a copy of dk_state.c: every one must be caught
Output: rows per table, the number of cases, mutations caught, ALL OK."""
import os, random, re, struct, subprocess, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
BACK = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACK, "..", "backend")); NXD = os.path.join(os.path.normpath(os.path.join(BACK, "..", "backend")), "nx")
DK = os.environ.get("FNVNX_DK", "/home/user/work/l6/dk")
INC = os.path.join(DK, "deko3d", "include")
CROSS = os.path.join(DK, "dkp", "devkitA64", "bin", "aarch64-none-elf-gcc")
TSV = os.path.join(NXD, "dk_facts.tsv")
def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)
def rd(*p): return open(os.path.join(*p)).read()

# ---- the sources of truth, parsed -----------------------------------------------------------------------------------------------------------
DKV = {}
for line in rd(TSV).splitlines()[1:]:
    k, n, v = line.split("\t")
    if k.startswith("Dk") and v.lstrip("-").isdigit(): DKV[n] = int(v)
def dk(kind, name):
    return DKV["%s_%s" % (kind, name)]
STATE_H, GL_S, GL_M, NXGPU4 = rd(NXD, "state.h"), rd(BACK, "gl4_state.c"), rd(BACK, "gl4_sampler.c"), rd(BACK, "nxgpu4.h")
RS = {m[0]: int(m[1]) for m in re.findall(r"#define (NXRS_[A-Z_0-9]+)\s+(\d+)", STATE_H)}
MS = {m[0]: int(m[1]) for m in re.findall(r"#define NXRS_M_(\w+)\s+(\d+)", NXGPU4)}
def enum_vals(prefix): return {m[0]: int(m[1]) for m in re.findall(prefix + r"_(\w+) = (\d+)", STATE_H)}
NXBF, NXBOP, NXCULL, NXFILL, NXWRAP, NXMIP = [enum_vals(p) for p in ("NxBf", "NxBop", "NxCull", "NxFill", "NxWrap", "NxMip")]
NXBF.pop("Count"); NBF = 15
assert len(NXBF) == NBF and RS["NXRS_B_SRC"] == 1 and MS["SAMPLEMASK"] == 32 and set(MS) >= {"MSAA", "AALINE", "LASTPIXEL", "NOCLIP", "SAMPLEMASK"}

GLCMP = {"NEVER": "Never", "LESS": "Less", "EQUAL": "Equal", "LEQUAL": "Lequal", "GREATER": "Greater", "NOTEQUAL": "NotEqual", "GEQUAL": "Gequal", "ALWAYS": "Always"}
GLSOP = {"KEEP": "Keep", "ZERO": "Zero", "REPLACE": "Replace", "INCR": "Incr", "DECR": "Decr", "INVERT": "Invert", "INCR_WRAP": "IncrWrap", "DECR_WRAP": "DecrWrap"}
GLBF = {"ZERO": "Zero", "ONE": "One", "SRC_COLOR": "SrcColor", "ONE_MINUS_SRC_COLOR": "InvSrcColor", "SRC_ALPHA": "SrcAlpha", "ONE_MINUS_SRC_ALPHA": "InvSrcAlpha",
        "DST_ALPHA": "DstAlpha", "ONE_MINUS_DST_ALPHA": "InvDstAlpha", "DST_COLOR": "DstColor", "ONE_MINUS_DST_COLOR": "InvDstColor",
        "SRC_ALPHA_SATURATE": "SrcAlphaSaturate", "CONSTANT_COLOR": "ConstColor", "ONE_MINUS_CONSTANT_COLOR": "InvConstColor", "SRC1_COLOR": "Src1Color",
        "ONE_MINUS_SRC1_COLOR": "InvSrc1Color"}
GLBOP = {"FUNC_ADD": "Add", "FUNC_SUBTRACT": "Sub", "FUNC_REVERSE_SUBTRACT": "RevSub", "MIN": "Min", "MAX": "Max"}
GLFILL = {"FILL": "Fill", "LINE": "Line", "POINT": "Point"}
GLWRAP = {"REPEAT": "Repeat", "MIRRORED_REPEAT": "MirroredRepeat", "CLAMP_TO_EDGE": "ClampToEdge", "CLAMP_TO_BORDER": "ClampToBorder", "MIRROR_CLAMP_TO_EDGE": "MirrorClampToEdge"}
GLFACE = {"FRONT": "Front", "BACK": "Back"}
def func_body(src, name): return re.search(r"static \w+ %s\(.*?\n\}\n" % name, src, re.S).group(0)
def tbl8(name, m):   # a 3-bit table {GL_A, ...}
    return [m[x] for x in re.findall(r"GL_(\w+)", re.search(r"\{(GL_.*?)\}", func_body(GL_S, name), re.S).group(1))]
CMP, SOP = tbl8("glcmp", GLCMP), tbl8("glsop", GLSOP)
BF = {NXBF[n]: GLBF[g] for n, g in re.findall(r"case NxBf_(\w+): return GL_(\w+);", func_body(GL_S, "glbf"))}
BOP = {NXBOP[n]: GLBOP[g] for n, g in re.findall(r"case NxBop_(\w+): return GL_(\w+);", func_body(GL_S, "glbop"))}
FILL = {NXFILL[n]: GLFILL[g] for n, g in re.findall(r"case NxFill_(\w+): return GL_(\w+);", func_body(GL_S, "glfill"))}
CULL = {}
for n, en, f in re.findall(r"case NxCull_(\w+): (p_glDisable|p_glEnable)\(GL_CULL_FACE\);(?: p_glCullFace\(GL_(\w+)\);)?", func_body(GL_S, "apply_cull")):
    CULL[NXCULL[n]] = "None" if en == "p_glDisable" else GLFACE[f]
WRAP = {NXWRAP[n]: GLWRAP[g] for n, g in re.findall(r"case NxWrap_(\w+): return GL_(\w+);", func_body(GL_M, "glwrap"))}
MIP = {}
for n, ln, lm, nn, nm in re.findall(r"case NxMip_(\w+): return minL \? GL_(\w+?)(?:_MIPMAP_(\w+))? : GL_(\w+?)(?:_MIPMAP_(\w+))?;", func_body(GL_M, "glminfilter")):
    assert ln == "LINEAR" and nn == "NEAREST" and lm == nm
    MIP[NXMIP[n]] = {"": "None", "NEAREST": "Nearest", "LINEAR": "Linear"}[lm]
assert (len(CMP), len(SOP), len(BF), len(BOP), len(FILL), len(CULL), len(WRAP), len(MIP)) == (8, 8, NBF, 5, 3, 3, 5, 3), "GL tables not parsed"
# GL semantics of an alpha-slot factor (GL 4.5 17.3.8: the factor is evaluated per channel, so a colour factor in the alpha slot is its alpha component)
ALPHA_MEANING = {"Zero": "0", "One": "1", "SrcColor": "sa", "InvSrcColor": "1-sa", "SrcAlpha": "sa", "InvSrcAlpha": "1-sa", "DstAlpha": "da", "InvDstAlpha": "1-da",
                 "DstColor": "da", "InvDstColor": "1-da", "SrcAlphaSaturate": "1", "ConstColor": "ca", "InvConstColor": "1-ca", "Src1Color": "s1a", "InvSrc1Color": "1-s1a"}
assert set(ALPHA_MEANING) == set(BF.values())

def g(n, w): return (w >> RS[n]) & 1
def bits(w, sh, n): return (w >> sh) & ((1 << n) - 1)
def f32(x): return struct.unpack("<I", struct.pack("<f", x))[0]
def fl(b): return struct.unpack("<f", struct.pack("<I", b))[0]

# ---- expectation: GL semantics -> Dk values -------------------------------------------------------------------------------------------------
E_FIELD, E_SAMPLES, E_BLEND, E_RANGE = 1, 2, 3, 4
SZ = 10 + 3 + 1 + 6 + 12 + 6
def exp_state(w0, w1, w2, w3, samples):
    on = w0 & 1
    sf, df, op, saf, daf, opa = (bits(w0, RS[k], n) for k, n in (("NXRS_B_SRC", 5), ("NXRS_B_DST", 5), ("NXRS_B_OP", 3), ("NXRS_B_SRCA", 5), ("NXRS_B_DSTA", 5), ("NXRS_B_OPA", 3)))
    cull, fill = bits(w2, RS["NXRS_R_CULL"], 2), bits(w2, RS["NXRS_R_FILL"], 2)
    bad = (on and (max(sf, df, saf, daf) >= NBF or op not in BOP or opa not in BOP)) or cull not in CULL or fill not in FILL or bits(w3, 4, 28) != 0
    if bad: return E_FIELD, [0] * SZ
    if samples not in (1, 2, 4, 8): return E_SAMPLES, [0] * SZ
    if on and BOP[opa] not in ("Min", "Max") and ALPHA_MEANING[BF[daf]] != ALPHA_MEANING[BF[df]]: return E_BLEND, [0] * SZ
    ms = {1: "1x", 2: "2x", 4: "4x", 8: "8x"}[samples] if g_(w3, "MSAA") else "1x"
    v = [1, g_(w3, "NOCLIP"), 0, dk("DkPolygonMode", FILL[fill]), dk("DkPolygonMode", FILL[fill]), dk("DkFace", CULL[cull]), dk("DkFrontFace", "CW"),
         dk("DkProvokingVertex", "First"), dk("DkPolygonFlag", "Line") if g_(w3, "AALINE") else 0, dk("DkPolygonFlag", "All") if g("NXRS_R_BIAS", w2) else 0]
    v += [0xF if on else 0, dk("DkLogicOp", "Copy"), dk("DkCompareOp", "Always")]
    masks = 0
    for rt in range(8):
        m = bits(w0, 32 + 4 * rt, 4) if rt < 4 else 15
        d = sum(dk("DkColorMask", n) for b, n in enumerate("RGBA") if m >> b & 1)
        masks |= d << 4 * rt
    v += [masks]
    if on: v += [dk("DkBlendOp", BOP[op]), dk("DkBlendFactor", BF[sf]), dk("DkBlendFactor", BF[df]), dk("DkBlendOp", BOP[opa]), dk("DkBlendFactor", BF[saf]), dk("DkBlendFactor", BF[daf])]
    else: v += [dk("DkBlendOp", "Add"), dk("DkBlendFactor", "One"), dk("DkBlendFactor", "Zero"), dk("DkBlendOp", "Add"), dk("DkBlendFactor", "One"), dk("DkBlendFactor", "Zero")]
    dt, dw, st = g("NXRS_D_TEST", w1), g("NXRS_D_WRITE", w1), g("NXRS_S_ENABLE", w1)   # GL: no depth write while the depth test is off
    def c(n): return dk("DkCompareOp", CMP[bits(w1, RS[n], 3)]) if st else dk("DkCompareOp", "Always")
    def s(n): return dk("DkStencilOp", SOP[bits(w1, RS[n], 3)]) if st else dk("DkStencilOp", "Keep")
    v += [dt, dt & dw, st, dk("DkCompareOp", CMP[bits(w1, RS["NXRS_D_FUNC"], 3)]) if dt else dk("DkCompareOp", "Always"),
          s("NXRS_S_FAIL"), s("NXRS_S_PASS"), s("NXRS_S_ZFAIL"), c("NXRS_S_FUNC"), s("NXRS_S_BFAIL"), s("NXRS_S_BPASS"), s("NXRS_S_BZFAIL"), c("NXRS_S_BFUNC")]
    v += [bits(w2, RS["NXRS_R_CLIP"], 8), g("NXRS_R_POINTSPRITE", w2), g("NXRS_R_FLAT", w2), g("NXRS_R_SRGB", w2), g("NXRS_R_SCISSOR", w2), dk("DkMsMode", ms)]
    return 0, v
def g_(w3, n): return (w3 >> MS[n]) & 1

def exp_dyn(w0, w1, w2, w3, samples, bc, bias, slope, psize):
    if samples not in (1, 2, 4, 8): return E_SAMPLES, [0] * 13
    mask = bits(w3, 32, 16) if samples > 1 else 0xFFFF
    return 0, [f32(x) for x in bc] + [bits(w1, RS["NXRS_S_REF"], 8), bits(w1, RS["NXRS_S_READMASK"], 8), bits(w1, RS["NXRS_S_WRITEMASK"], 8),
                                      f32(bias), f32(0.0), f32(slope), f32(psize), f32(1.0), mask]
DYN_SZ = 13

def exp_samp(filt, aniso, wrap, mnl, mxl, p0, p1, bias, border):
    mip, wr = bits(filt, 2, 2), [bits(wrap, 0, 3), bits(wrap, 3, 3), bits(wrap, 6, 3)]
    if mip not in MIP or filt & 0xC0 or wrap >> 9 or p0 or p1 or any(x not in WRAP for x in wr): return E_FIELD, 0, [0] * 18
    fb = fl(bias)
    if mnl > 15 or mxl > 15 or not (-15.0 <= fb <= 15.0): return E_RANGE, 0, [0] * 18
    F = lambda b: dk("DkFilter", "Linear" if b else "Nearest")
    v = [F(filt & 1), F(filt & 2), dk("DkMipFilter", MIP[mip])] + [dk("DkWrapMode", WRAP[x]) for x in wr]
    v += [f32(float(mnl)), f32(float(mxl)), bias, f32(0.0), 1 if filt & 0x20 else 0, dk("DkCompareOp", "Lequal")]
    v += [f32(((border >> 8 * i) & 255) / 255.0) for i in range(4)]
    v += [f32(float(aniso) if aniso > 1 else 1.0), dk("DkSamplerReduction", "WeightedAverage")]
    return 0, 1 if filt & 0x10 else 0, v

# ---- the harness ---------------------------------------------------------------------------------------------------------------------------
HARNESS = r'''
#include <stdio.h>
#include <string.h>
#include "dk_state.h"
static unsigned fb(float f) { unsigned u; memcpy(&u, &f, 4); return u; }
static float bf(unsigned u) { float f; memcpy(&f, &u, 4); return f; }
static int zero(const void* p, size_t n) { const unsigned char* c = p; for (size_t i = 0; i < n; i++) if (c[i]) return 0; return 1; }
int main(void) {
    char t; unsigned long long a, b, c, d; unsigned s, x[8];
    while (scanf(" %c", &t) == 1) {
        if (t == 'S') {
            scanf("%llx %llx %llx %llx %u", &a, &b, &c, &d, &s);
            NxRenderState rs = {{a, b, c, d}}; NxDkState o; memset(&o, 0x5A, sizeof o);
            int e = nxdk_state(&rs, s, &o);
            printf("%d %d", e, e ? zero(&o, sizeof o) : 1);
            printf(" %u %u %u %u %u %u %u %u %u %u", o.rast.rasterizerEnable, o.rast.depthClampEnable, o.rast.fillRectangleEnable, o.rast.polygonModeFront, o.rast.polygonModeBack,
                   o.rast.cullMode, o.rast.frontFace, o.rast.provokingVertex, o.rast.polygonSmoothEnableMask, o.rast.depthBiasEnableMask);
            printf(" %u %u %u %u", o.color.blendEnableMask, o.color.logicOp, o.color.alphaCompareOp, o.write.masks);
            printf(" %u %u %u %u %u %u", o.blend.colorBlendOp, o.blend.srcColorBlendFactor, o.blend.dstColorBlendFactor, o.blend.alphaBlendOp, o.blend.srcAlphaBlendFactor, o.blend.dstAlphaBlendFactor);
            printf(" %u %u %u %u %u %u %u %u %u %u %u %u", o.ds.depthTestEnable, o.ds.depthWriteEnable, o.ds.stencilTestEnable, o.ds.depthCompareOp, o.ds.stencilFrontFailOp,
                   o.ds.stencilFrontPassOp, o.ds.stencilFrontDepthFailOp, o.ds.stencilFrontCompareOp, o.ds.stencilBackFailOp, o.ds.stencilBackPassOp, o.ds.stencilBackDepthFailOp, o.ds.stencilBackCompareOp);
            printf(" %u %u %u %u %u %u\n", o.derived.clipPlaneMask, o.derived.pointSprite, o.derived.flatShade, o.derived.srgbWrite, o.derived.scissorEnable, (unsigned)o.derived.msMode);
        } else if (t == 'D') {
            scanf("%llx %llx %llx %llx %u %x %x %x %x %x %x %x", &a, &b, &c, &d, &s, &x[0], &x[1], &x[2], &x[3], &x[4], &x[5], &x[6]);
            NxRenderState rs = {{a, b, c, d}}; NxDynamicState dy; NxDkDyn o; memset(&dy, 0, sizeof dy); memset(&o, 0x5A, sizeof o);
            for (int i = 0; i < 4; i++) dy.blendConstant[i] = bf(x[i]);
            dy.depthBiasUnits = bf(x[4]); dy.depthBiasSlope = bf(x[5]); dy.pointSize = bf(x[6]); dy.depthBiasD3D = 77.0f;
            dy.stencilRef = dy.stencilReadMask = dy.stencilWriteMask = 0x5A5A;
            int e = nxdk_dyn(&rs, &dy, s, &o);
            printf("%d %d", e, e ? zero(&o, sizeof o) : 1);
            printf(" %u %u %u %u %u %u %u %u %u %u %u %u %u\n", fb(o.blendConst[0]), fb(o.blendConst[1]), fb(o.blendConst[2]), fb(o.blendConst[3]), o.stencilRef, o.stencilReadMask,
                   o.stencilWriteMask, fb(o.biasConstant), fb(o.biasClamp), fb(o.biasSlope), fb(o.pointSize), fb(o.lineWidth), o.sampleMask);
        } else if (t == 'P') {
            scanf("%x %x %x %x %x %x %x %x %x", &x[0], &x[1], &x[2], &x[3], &x[4], &x[5], &x[6], &x[7], &s);
            NxSamplerState sp; DkSampler o; uint32_t srgb = 0x5A; memset(&sp, 0, sizeof sp); memset(&o, 0x5A, sizeof o);
            sp.filter = x[0]; sp.maxAniso = x[1]; sp.wrap = x[2]; sp.minLod = x[3]; sp.maxLod = x[4]; sp.pad[0] = x[5]; sp.pad[1] = x[6]; sp.lodBias = bf(x[7]); sp.border = s;
            int e = nxdk_sampler(&sp, &o, &srgb);
            printf("%d %d %u", e, e ? (zero(&o, sizeof o) && srgb == 0) : 1, srgb);
            printf(" %u %u %u %u %u %u %u %u %u %u %u %u", o.minFilter, o.magFilter, o.mipFilter, o.wrapMode[0], o.wrapMode[1], o.wrapMode[2], fb(o.lodClampMin), fb(o.lodClampMax),
                   fb(o.lodBias), fb(o.lodSnap), o.compareEnable, o.compareOp);
            printf(" %u %u %u %u %u %u\n", fb(o.borderColor[0].value_f), fb(o.borderColor[1].value_f), fb(o.borderColor[2].value_f), fb(o.borderColor[3].value_f), fb(o.maxAnisotropy), o.reductionMode);
        }
    }
    return 0;
}
'''
NANBITS, PINF, NINF = 0x7FC00000, 0x7F800000, 0xFF800000
def cases():
    rnd = random.Random(20261111)
    def rs(): return [rnd.getrandbits(64) & ~(3 << 44) for _ in range(3)] + [rnd.getrandbits(4) | (rnd.getrandbits(32) << 32)]
    C = []                                                      # (kind, args)
    base = [[0, 0, 0, 0xFFFFFFFF << 32], [0x1, 0x2A5, 0x2D5, 0x0F | 0xFFFF0000 << 32]] + [rs() for _ in range(4)]
    def with_(w, i, sh, n, val): w = list(w); w[i] = (w[i] & ~(((1 << n) - 1) << sh)) | (val << sh); return w
    for bw in base:                                               # every value of every field, alone
        for i, sh, n in [(0, 0, 1)] + [(0, s_, 5) for s_ in (1, 6, 14, 19)] + [(0, s_, 3) for s_ in (11, 24)] + [(0, 32 + 4 * r, 4) for r in range(8)] + \
                [(1, 0, 1), (1, 1, 1), (1, 2, 3), (1, 5, 1), (1, 6, 1)] + [(1, s_, 3) for s_ in (8, 11, 14, 17, 20, 23, 26, 29)] + [(1, s_, 8) for s_ in (32, 40, 48)] + \
                [(2, 0, 2), (2, 2, 2), (2, 4, 1), (2, 5, 1), (2, 6, 1), (2, 7, 1), (2, 8, 8), (2, 16, 1), (3, 0, 4), (3, 32, 16), (3, 48, 16)]:
            for val in range(1 << n): C.append(("S", with_(bw, i, sh, n, val) + [4]))
        for sb in range(4, 32): C.append(("S", with_(bw, 3, sb, 1, 1) + [4]))
        for smp in list(range(0, 18)) + [255, 256, 1 << 31, 0xFFFFFFFF]: C.append(("S", list(bw) + [smp])); C.append(("D", list(bw) + [smp]))
    for sf in range(16):                                          # blend: the full cross of the four factors with Add/Add
        for df in range(16):
            for saf in range(16):
                for daf in range(16): C.append(("S", [1 | sf << 1 | df << 6 | daf << 19 | saf << 14, 0, 0, 0xF] + [8]))
    for op in range(8):                                           # ops x (dst, dst alpha) factors
        for opa in range(8):
            for df in range(16):
                for daf in range(16): C.append(("S", [1 | 4 << 1 | df << 6 | op << 11 | 1 << 14 | daf << 19 | opa << 24, 0, 0, 0xF, 1]))
    for m in range(1 << 16):                                      # the four colour masks
        C.append(("S", [m << 32, 0, 0, 0xF, 1])); C.append(("S", [m << 32 | 1 | 4 << 1 | 5 << 6 | 1 << 14, 0, 0, 0xF, 1]))
    for cull in range(4):                                         # cull x fill x flags
        for fill in range(4):
            for fl_ in range(1 << 5):
                for w3 in range(16): C.append(("S", [0, 0, cull | fill << 2 | fl_ << 4, w3, 4]))
    for _ in range(6000): C.append(("S", rs() + [rnd.choice([1, 2, 4, 8, 3])])); C.append(("D", rs() + [rnd.choice([1, 2, 4, 8, 0])]))
    for _ in range(3000):
        w = rs(); w[3] &= 0xFFFFFFFF0000000F; C.append(("S", w + [rnd.choice([1, 2, 4, 8])]))   # valid reserved bits: only the blend / field validity varies
    return C
def dyn_inputs(rnd):
    sp = [0, 1, f32(1.0), f32(-0.5), NANBITS, PINF, 0x80000000, f32(1e30), f32(-1e30), 0x00000001]
    return [rnd.choice(sp) for _ in range(7)]
def samp_cases():
    rnd = random.Random(7); C = []
    BI = [0, 0x80000000, f32(0.5), f32(-0.5), f32(15.0), f32(-15.0), f32(14.999999), 0x41700001, 0xC1700001, f32(16.0), f32(-16.0), NANBITS, 0x7FA00000, PINF, NINF, f32(1e30), 1, f32(3.25)]
    for filt in range(256): C.append([filt, 0, 0, 0, 15, 0, 0, 0, 0xA1B2C3D4])
    for an in range(256): C.append([0x15, an, 0x49, 0, 0, 0, 0, 0, 0xFF00FF00])
    for wr in range(1 << 16): C.append([0x03, 3, wr, 1, 2, 0, 0, f32(1.25), 0x80FF7F01])
    for lo in range(256):
        for hi in (0, 1, 7, 15, 16, 255, lo): C.append([0x0F, 1, 0x24, lo, hi, 0, 0, 0, 0x01020304])
    for p0 in range(256): C.append([0, 0, 0, 0, 0, p0, 0, 0, 0]); C.append([0, 0, 0, 0, 0, 0, p0, 0, 0])
    for b in BI:
        for filt in (0, 4, 8, 0x3F): C.append([filt, 16, 0x1B, 0, 0, 0, 0, b, 0])
    for _ in range(3000): C.append([rnd.choice([rnd.getrandbits(8), rnd.getrandbits(6)]), rnd.getrandbits(8), rnd.choice([rnd.getrandbits(16), rnd.getrandbits(9)]), rnd.choice([0, 15, 16, rnd.getrandbits(8)]),
                                    rnd.choice([0, 15, 16, rnd.getrandbits(8)]), rnd.choice([0, 0, 0, 1]), 0, rnd.choice(BI), rnd.getrandbits(32)])
    return C

def run(exe, lines):
    r = subprocess.run([exe], input="\n".join(lines) + "\n", capture_output=True, text=True)
    if r.returncode: raise RuntimeError("harness: " + r.stderr[-200:])
    return r.stdout.splitlines()

def hx(v): return "%x" % v
def check(d, dkc):
    """-> (failures, stats) for one dk_state.c"""
    cdir = os.path.join(d, "t"); os.makedirs(cdir, exist_ok=True)
    h = os.path.join(cdir, "h.c"); exe = os.path.join(cdir, "h"); open(h, "w").write(HARNESS)
    r = sh(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Werror=switch-enum", "-I", INC, "-I", NXD, "-I", BACK, "-I", NXB, "-I", ROOT, h, dkc, "-o", exe])
    if r.returncode: return ["host build: " + (r.stderr.strip().splitlines() or ["?"])[-1][:200]], {}
    cs = cases(); rnd = random.Random(5); lines, exps = [], []
    for k, a in cs:
        if k == "S":
            lines.append("S %x %x %x %x %u" % tuple(a)); exps.append(("S", a, exp_state(*a)))
        else:
            w0, w1, w2, w3, smp = a; x = dyn_inputs(rnd)
            lines.append("D %x %x %x %x %u %s" % (w0, w1, w2, w3, smp, " ".join(hx(v) for v in x)))
            exps.append(("D", a, exp_dyn(w0, w1, w2, w3, smp, [fl(v) for v in x[:4]], fl(x[4]), fl(x[5]), fl(x[6]))))
    sc = samp_cases()
    for a in sc: lines.append("P %x %x %x %x %x %x %x %x %x" % tuple(a)); exps.append(("P", a, exp_samp(*a)))
    out = run(exe, lines); fails = []
    if len(out) != len(lines): return ["harness produced %d lines for %d cases" % (len(out), len(lines))], {}
    nerr = {}
    for (k, a, e), o in zip(exps, out):
        got = [int(x) for x in o.split()]
        if k == "S": want = [e[0], 1] + e[1]
        elif k == "D": want = [e[0], 1] + e[1]
        else: want = [e[0], 1, e[1]] + e[2]
        if got != want:
            fails.append("%s case %s: got %s want %s" % (k, [hx(x) for x in a], got[:24], want[:24]));
            if len(fails) > 3: break
        nerr[(k, e[0])] = nerr.get((k, e[0]), 0) + 1
    return fails, {"cases": len(lines), "errs": nerr}

def cross(dkc):
    r = sh([CROSS, "-std=gnu11", "-ffreestanding", "-O2", "-Wall", "-Wextra", "-Werror", "-Werror=switch-enum", "-I", INC, "-I", NXD, "-I", BACK, "-I", NXB, "-I", ROOT, "-c", dkc, "-o", os.devnull])
    return None if r.returncode == 0 else (r.stderr.strip().splitlines() or ["?"])[-1][:200]

def mutations(src):
    M = [("AM: DstColor means SrcAlpha", "[NxBf_DstColor] = NxBf_DstAlpha", "[NxBf_DstColor] = NxBf_SrcAlpha"),
         ("AM: SrcAlphaSat is not One", "[NxBf_SrcAlphaSat] = NxBf_One", "[NxBf_SrcAlphaSat] = NxBf_SrcAlpha"),
         ("Min / Max exemption dropped", "opa != NxBop_Min && opa != NxBop_Max && ", ""),
         ("frontFace CCW", "o->rast.frontFace = DkFrontFace_CW;", "o->rast.frontFace = DkFrontFace_CCW;"),
         ("cull CW/CCW swapped", "[NxCull_CW] = DkFace_Front, [NxCull_CCW] = DkFace_Back", "[NxCull_CW] = DkFace_Back, [NxCull_CCW] = DkFace_Front"),
         ("Sub / RevSub swapped", "[NxBop_Subtract] = DkBlendOp_Sub, [NxBop_RevSubtract] = DkBlendOp_RevSub", "[NxBop_Subtract] = DkBlendOp_RevSub, [NxBop_RevSubtract] = DkBlendOp_Sub"),
         ("IncrWrap / DecrWrap swapped", "DkStencilOp_IncrWrap, DkStencilOp_DecrWrap}", "DkStencilOp_DecrWrap, DkStencilOp_IncrWrap}"),
         ("colour mask B / A swapped", "(m & 4 ? DkColorMask_B : 0) | (m & 8 ? DkColorMask_A : 0)", "(m & 8 ? DkColorMask_B : 0) | (m & 4 ? DkColorMask_A : 0)"),
         ("depth write without test", "o->ds.depthWriteEnable = dt && F(w1, NXRS_D_WRITE, 1);", "o->ds.depthWriteEnable = F(w1, NXRS_D_WRITE, 1);"),
         ("MSAA off ignored", "if (!(w3 >> NXRS_M_MSAA & 1)) o->derived.msMode = DkMsMode_1x;", ""),
         ("sampler lodBias dropped", "o->lodBias = s->lodBias;", "o->lodBias = 0.0f;"),
         ("anisotropy threshold", "s->maxAniso > 1 ?", "s->maxAniso > 2 ?"),
         ("reserved w3 bit 4 unchecked", "(w3 >> 4 & 0x0FFFFFFF) != 0", "(w3 >> 5 & 0x07FFFFFF) != 0"),
         ("sample mask on 2x ignored", "samples > 1 ? (uint32_t)", "samples > 2 ? (uint32_t)"),
         ("srgb view from the shadow bit", "*srgbView = (s->filter & NXSMP_SRGB) != 0;", "*srgbView = (s->filter & NXSMP_SHADOW) != 0;"),
         ("blend enabled on RT0 only", "o->color.blendEnableMask = on ? 0x0F : 0;", "o->color.blendEnableMask = on ? 0x01 : 0;"),
         ("depth bias on fill only", "? DkPolygonFlag_All : 0;", "? DkPolygonFlag_Fill : 0;"),
         ("lod clamp > 15 accepted", "s->minLod > 15 || s->maxLod > 15 ||", "s->minLod > 15 ||"),
         ("lodBias NaN accepted", "!(s->lodBias >= -15.0f && s->lodBias <= 15.0f)", "(s->lodBias < -15.0f || s->lodBias > 15.0f)"),
         ("error leaves *out filled", "static int fail(void* o, size_t n, int e) { memset(o, 0, n); return e; }", "static int fail(void* o, size_t n, int e) { (void)o; (void)n; return e; }")]
    for desc, a, b in M:
        if src.count(a) != 1: sys.exit("mutation %r: pattern occurs %d times" % (desc, src.count(a)))
    return [(d, src.replace(a, b)) for d, a, b in M]

def main():
    src = rd(NXD, "dk_state.c"); nl = len(src.splitlines()) + len(rd(NXD, "dk_state.h").splitlines())
    print("a  size: dk_state.c + dk_state.h = %d lines (limit 300)" % nl)
    if nl > 300: sys.exit("FAIL too long")
    e = cross(os.path.join(NXD, "dk_state.c"))
    print("a  aarch64 -Wall -Wextra -Werror:", "ok" if e is None else "FAIL " + e)
    if e: sys.exit(1)
    with tempfile.TemporaryDirectory() as d:
        fails, st = check(d, os.path.join(NXD, "dk_state.c"))
        for f in fails: print("FAIL", f)
        if fails: sys.exit(1)
        print("b  rows: blend factor %d, blend op %d, cull %d, fill %d, compare %d, stencil op %d, wrap %d, mip %d; %d cases ok (success %d / E_FIELD %d / E_SAMPLES %d / E_BLEND_ALPHA_DST %d / E_RANGE %d)" % (
            len(BF), len(BOP), len(CULL), len(FILL), len(CMP), len(SOP), len(WRAP), len(MIP), st["cases"], *(sum(v for (k, c), v in st["errs"].items() if c == ec) for ec in (0, 1, 2, 3, 4))))
        if "--no-mutations" not in sys.argv:
            caught = 0; ms = mutations(src)
            for desc, text in ms:
                c = os.path.join(d, "mut"); os.makedirs(c, exist_ok=True); p = os.path.join(c, "dk_state.c"); open(p, "w").write(text)
                mf, _ = check(d, p)
                print("  mutation %-36s %s" % (desc, "caught (" + mf[0][:50] + ")" if mf else "NOT CAUGHT")); caught += bool(mf)
            print("e  mutations caught %d/%d" % (caught, len(ms)))
            if caught != len(ms) or len(ms) < 8: sys.exit(1)
    print("ALL OK")

if __name__ == "__main__":
    main()
