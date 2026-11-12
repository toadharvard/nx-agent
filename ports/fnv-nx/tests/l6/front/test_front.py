#!/usr/bin/env python3
"""test_front.py: tests of render/d3d9/front (the D3D9 state translation; rule: total by construction).
    python3 -I tests/l6/front/test_front.py [--no-mutations]          needs gcc and python3 (ctypes), about 20 s
 a  generated enums: regenerated header == committed header; every enumerator of the 12 consumed enumerations and its value == the
    independent table ORACLE_ENUMS (MS d3d9types.h numbers) and the names == the spec's "enums"
 b  state sweeps: for every enumeration and EVERY value in [0, max+2] plus 0x7fffffff and 0xffffffff (D3DFORMAT: [0, 0xca] plus every
    enumerator +-1, see fmt_domain) in two contexts, the C word / bits / sampler == the python oracle written from the wined3d 9.0 rules
 c  latch: the four states whose out-of-range result is "GL keeps its value" over every (valid value, out-of-range value) pair
 d  formats: table == oracle table (all values of the domain); against the env table (render/d3d9/env/env_*.tsv through tests/l6/env/envq.py), PER ROLE (resource type x usage; buffers are
    not image types): every format an application can create has a native format with the capability of the role, or is NULL (a target without storage: NXFMT_NULL), or is a zero-size
    class (capability probe, layout oracle of tests/l6/tex). No pinned list of exceptions: a gap fails the part.
 e  texel conversion (A8 L8 A8L8 L16 R8G8B8 ATI1 against an independent BC4 decoder; L6V5U5 over all 2^16 texels, X8L8V8U8 / R8G8B8 structured; target rules, clear value, read-back, sampler fixup)
 f  mutations of a copy of the sources: each must make the sweeps fail, or the build (a case removed from a switch)
The oracles are independent of the C: python tables and rules from wined3d 9.0 (state.c, stateblock.c, shader.c, utils.c) and Nine."""
import ctypes as C, json, os, re, shutil, subprocess, sys, tempfile
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")); import san; san.install(preload=True); import mutsrc
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FRONT = os.path.join(ROOT, "render", "d3d9", "front"); BACKEND = os.path.join(ROOT, "render", "gl4"); NXB = os.path.normpath(os.path.join(BACKEND, "..", "backend"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "render", "d3d9", "env")); sys.path.insert(0, os.path.join(HERE, "..", "tex")); import envq, texoracle   # noqa: E402  (query domains of the environment table; the layout oracle)
M32, MASKS = 0xffffffff, [0, 1, 2, 3, 0xf, 0x10, 0xff, 0x100, 0x80000000, 0x7fffffff, 0xffffffff]

# ---- independent table: the numbers of d3d9types.h for the consumed enumerations (without FORCE_DWORD) ----------------------------
def tab(s): w = s.split(); return {w[i]: int(w[i + 1]) for i in range(0, len(w), 2)}
ORACLE_ENUMS = {
    "D3DCMPFUNC": tab("D3DCMP_NEVER 1 D3DCMP_LESS 2 D3DCMP_EQUAL 3 D3DCMP_LESSEQUAL 4 D3DCMP_GREATER 5 D3DCMP_NOTEQUAL 6 D3DCMP_GREATEREQUAL 7 D3DCMP_ALWAYS 8"),
    "D3DSTENCILOP": tab("D3DSTENCILOP_KEEP 1 D3DSTENCILOP_ZERO 2 D3DSTENCILOP_REPLACE 3 D3DSTENCILOP_INCRSAT 4 D3DSTENCILOP_DECRSAT 5 D3DSTENCILOP_INVERT 6 D3DSTENCILOP_INCR 7 D3DSTENCILOP_DECR 8"),
    "D3DBLEND": tab("D3DBLEND_ZERO 1 D3DBLEND_ONE 2 D3DBLEND_SRCCOLOR 3 D3DBLEND_INVSRCCOLOR 4 D3DBLEND_SRCALPHA 5 D3DBLEND_INVSRCALPHA 6 D3DBLEND_DESTALPHA 7 "
                    "D3DBLEND_INVDESTALPHA 8 D3DBLEND_DESTCOLOR 9 D3DBLEND_INVDESTCOLOR 10 D3DBLEND_SRCALPHASAT 11 D3DBLEND_BOTHSRCALPHA 12 D3DBLEND_BOTHINVSRCALPHA 13 "
                    "D3DBLEND_BLENDFACTOR 14 D3DBLEND_INVBLENDFACTOR 15 D3DBLEND_SRCCOLOR2 16 D3DBLEND_INVSRCCOLOR2 17"),
    "D3DBLENDOP": tab("D3DBLENDOP_ADD 1 D3DBLENDOP_SUBTRACT 2 D3DBLENDOP_REVSUBTRACT 3 D3DBLENDOP_MIN 4 D3DBLENDOP_MAX 5"),
    "D3DCULL": tab("D3DCULL_NONE 1 D3DCULL_CW 2 D3DCULL_CCW 3"), "D3DFILLMODE": tab("D3DFILL_POINT 1 D3DFILL_WIREFRAME 2 D3DFILL_SOLID 3"),
    "D3DSHADEMODE": tab("D3DSHADE_FLAT 1 D3DSHADE_GOURAUD 2 D3DSHADE_PHONG 3"), "D3DFOGMODE": tab("D3DFOG_NONE 0 D3DFOG_EXP 1 D3DFOG_EXP2 2 D3DFOG_LINEAR 3"),
    "D3DZBUFFERTYPE": tab("D3DZB_FALSE 0 D3DZB_TRUE 1 D3DZB_USEW 2"),
    "D3DTEXTUREADDRESS": tab("D3DTADDRESS_WRAP 1 D3DTADDRESS_MIRROR 2 D3DTADDRESS_CLAMP 3 D3DTADDRESS_BORDER 4 D3DTADDRESS_MIRRORONCE 5"),
    "D3DTEXTUREFILTERTYPE": tab("D3DTEXF_NONE 0 D3DTEXF_POINT 1 D3DTEXF_LINEAR 2 D3DTEXF_ANISOTROPIC 3 D3DTEXF_FLATCUBIC 4 D3DTEXF_GAUSSIANCUBIC 5 "
                                "D3DTEXF_PYRAMIDALQUAD 6 D3DTEXF_GAUSSIANQUAD 7 D3DTEXF_CONVOLUTIONMONO 8"),
}
FCC = lambda s: sum(ord(c) << (8 * i) for i, c in enumerate(s))
ORACLE_ENUMS["D3DFORMAT"] = tab("D3DFMT_UNKNOWN 0 D3DFMT_R8G8B8 20 D3DFMT_A8R8G8B8 21 D3DFMT_X8R8G8B8 22 D3DFMT_R5G6B5 23 D3DFMT_X1R5G5B5 24 D3DFMT_A1R5G5B5 25 "
    "D3DFMT_A4R4G4B4 26 D3DFMT_R3G3B2 27 D3DFMT_A8 28 D3DFMT_A8R3G3B2 29 D3DFMT_X4R4G4B4 30 D3DFMT_A2B10G10R10 31 D3DFMT_A8B8G8R8 32 D3DFMT_X8B8G8R8 33 D3DFMT_G16R16 34 "
    "D3DFMT_A2R10G10B10 35 D3DFMT_A16B16G16R16 36 D3DFMT_A8P8 40 D3DFMT_P8 41 D3DFMT_L8 50 D3DFMT_A8L8 51 D3DFMT_A4L4 52 D3DFMT_V8U8 60 D3DFMT_L6V5U5 61 D3DFMT_X8L8V8U8 62 "
    "D3DFMT_Q8W8V8U8 63 D3DFMT_V16U16 64 D3DFMT_A2W10V10U10 67 D3DFMT_D16_LOCKABLE 70 D3DFMT_D32 71 D3DFMT_D15S1 73 D3DFMT_D24S8 75 D3DFMT_D24X8 77 D3DFMT_D24X4S4 79 "
    "D3DFMT_D16 80 D3DFMT_L16 81 D3DFMT_D32F_LOCKABLE 82 D3DFMT_D24FS8 83 D3DFMT_D32_LOCKABLE 84 D3DFMT_S8_LOCKABLE 85 D3DFMT_VERTEXDATA 100 D3DFMT_INDEX16 101 "
    "D3DFMT_INDEX32 102 D3DFMT_Q16W16V16U16 110 D3DFMT_R16F 111 D3DFMT_G16R16F 112 D3DFMT_A16B16G16R16F 113 D3DFMT_R32F 114 D3DFMT_G32R32F 115 D3DFMT_A32B32G32R32F 116 "
    "D3DFMT_CxV8U8 117 D3DFMT_A1 118 D3DFMT_A2B10G10R10_XR_BIAS 119 D3DFMT_BINARYBUFFER 199")
ORACLE_ENUMS["D3DFORMAT"].update({"D3DFMT_" + k: FCC(v) for k, v in dict(UYVY="UYVY", YUY2="YUY2", DXT1="DXT1", DXT2="DXT2", DXT3="DXT3", DXT4="DXT4", DXT5="DXT5",
    MULTI2_ARGB8="MET1", G8R8_G8B8="GRGB", R8G8_B8G8="RGBG", YV12="YV12", ATI1="ATI1", ATI2="ATI2", INST="INST", NVDB="NVDB", NVHU="NVHU", NVHS="NVHS", INTZ="INTZ",
    RESZ="RESZ", NULL="NULL", R16=" R16", AL16="AL16", NV12="NV12", DF16="DF16", DF24="DF24", ATOC="ATOC").items()})
RSN = tab("ZENABLE 7 FILLMODE 8 SHADEMODE 9 ZWRITEENABLE 14 ALPHATESTENABLE 15 SRCBLEND 19 DESTBLEND 20 CULLMODE 22 ZFUNC 23 ALPHAREF 24 ALPHAFUNC 25 "
          "ALPHABLENDENABLE 27 FOGENABLE 28 FOGCOLOR 34 FOGTABLEMODE 35 FOGSTART 36 FOGEND 37 FOGDENSITY 38 STENCILENABLE 52 STENCILFAIL 53 STENCILZFAIL 54 "
          "STENCILPASS 55 STENCILFUNC 56 STENCILREF 57 STENCILMASK 58 STENCILWRITEMASK 59 CLIPPING 136 CLIPPLANEENABLE 152 POINTSIZE 154 POINTSIZE_MIN 155 POINTSPRITEENABLE 156 "
          "POINTSIZE_MAX 166 COLORWRITEENABLE 168 BLENDOP 171 SCISSORTESTENABLE 174 SLOPESCALEDEPTHBIAS 175 TWOSIDEDSTENCILMODE 185 CCW_STENCILFAIL 186 "
          "CCW_STENCILZFAIL 187 CCW_STENCILPASS 188 CCW_STENCILFUNC 189 COLORWRITEENABLE1 190 COLORWRITEENABLE2 191 COLORWRITEENABLE3 192 BLENDFACTOR 193 "
          "SRGBWRITEENABLE 194 DEPTHBIAS 195 SEPARATEALPHABLENDENABLE 206 SRCBLENDALPHA 207 DESTBLENDALPHA 208 BLENDOPALPHA 209")
SMN = tab("ADDRESSU 1 ADDRESSV 2 ADDRESSW 3 BORDERCOLOR 4 MAGFILTER 5 MINFILTER 6 MIPFILTER 7 MIPMAPLODBIAS 8 MAXMIPLEVEL 9 MAXANISOTROPY 10 SRGBTEXTURE 11")
# NxFormat / NxFog / NxWrap ... names and NXRS macros the oracle uses: values come from the compiler (consts lib below)
KN = """NxCmp_Never NxCmp_Less NxCmp_Equal NxCmp_LEqual NxCmp_Greater NxCmp_NotEqual NxCmp_GEqual NxCmp_Always NxSop_Keep NxSop_Zero NxSop_Replace NxSop_IncrSat NxSop_DecrSat
NxSop_Invert NxSop_IncrWrap NxSop_DecrWrap NxBf_Zero NxBf_One NxBf_SrcColor NxBf_InvSrcColor NxBf_SrcAlpha NxBf_InvSrcAlpha NxBf_DstAlpha NxBf_InvDstAlpha NxBf_DstColor
NxBf_InvDstColor NxBf_SrcAlphaSat NxBf_ConstColor NxBf_InvConstColor NxBf_Src1Color NxBf_InvSrc1Color NxBop_Add NxBop_Subtract NxBop_RevSubtract NxBop_Min NxBop_Max NxCull_None
NxCull_CW NxCull_CCW NxFill_Solid NxFill_Wireframe NxFill_Point NxFog_None NxFog_Linear NxFog_Exp NxFog_Exp2 NxFog_Vertex NxWrap_Repeat NxWrap_Mirror NxWrap_Clamp NxWrap_Border
NxWrap_MirrorOnce NxMip_None NxMip_Nearest NxMip_Linear NXSMP_MIN_LINEAR NXSMP_MAG_LINEAR NXSMP_MIP_SHIFT NXSMP_SRGB NXSMP_SHADOW NXRS_B_ENABLE NXRS_B_SRC NXRS_B_DST NXRS_B_OP
NXRS_B_SRCA NXRS_B_DSTA NXRS_B_OPA NXRS_D_TEST NXRS_D_WRITE NXRS_D_FUNC NXRS_S_ENABLE NXRS_S_TWOSIDED NXRS_S_FUNC NXRS_S_BFUNC NXRS_S_READMASK NXRS_S_WRITEMASK NXRS_S_REF
NXRS_R_CULL NXRS_R_FILL NXRS_R_BIAS NXRS_R_SCISSOR NXRS_R_SRGB NXRS_R_FLAT NXRS_R_CLIP NXRS_R_POINTSPRITE NXSB_ALPHAFUNC NXSB_ALPHATEST NXSB_FOG NXFMT_COMPRESSED NXFMT_DEPTH
NXFMT_STENCIL NXFMT_SRGB NXFMT_RENDER NXFMT_CONVERT NXFMT_NULL NxFmt_BGR10A2 NxFmt_R16 NxFmt_None NxFmt_RGBA8 NxFmt_BGRA8 NxFmt_BC1 NxFmt_BC2 NxFmt_BC3 NxFmt_R5G6B5 NxFmt_RGBA16F NxFmt_R32F NxFmt_Z24S8 NxFmt_Z32F
NxFmt_BGRX8 NxFmt_RGBX8 NxFmt_A1R5G5B5 NxFmt_X1R5G5B5 NxFmt_A4R4G4B4 NxFmt_RGB10A2 NxFmt_RG16 NxFmt_RGBA16 NxFmt_RG8S NxFmt_RGBA8S NxFmt_RG16S NxFmt_RGBA16S NxFmt_R16F
NxFmt_RG16F NxFmt_RG32F NxFmt_RGBA32F NxFmt_BC5 NxFmt_Z16 NxFmt_Z24X8 NxFmt_Z32FS8 NxSwz_R NxSwz_G NxSwz_B NxSwz_A NxSwz_0 NxSwz_1""".split()


class Lib:
    """the front compiled as a shared library (64 bit) + the compiler's values of the constants the oracle names"""
    def __init__(self, front, tmp, tag):
        os.makedirs(tmp, exist_ok=True); self.so = os.path.join(tmp, tag + ".so"); self.err = ""
        srcs = sorted(os.path.join(front, f) for f in os.listdir(front) if f.endswith(".c"))
        r = subprocess.run(["gcc", "-shared", "-fPIC", "-std=c11", "-O1", "-Wall", "-Wextra", "-Werror", "-Werror=switch", "-Werror=switch-enum", "-I", BACKEND, "-I", NXB, "-I", front,
                            *srcs, "-o", self.so], capture_output=True, text=True)
        if r.returncode: self.err = r.stderr[-600:]; self.lib = None; return
        self.lib = L = C.CDLL(self.so)
        L.nx_d3d9_blend_word.restype = C.c_uint64; L.nx_d3d9_blend_word.argtypes = [C.c_void_p]
        for n in ("nx_d3d9_depth_stencil_word", "nx_d3d9_raster_word"): getattr(L, n).restype = C.c_uint64; getattr(L, n).argtypes = [C.c_void_p, C.c_void_p]
        L.nx_d3d9_shader_bits.restype = C.c_uint32; L.nx_d3d9_shader_bits.argtypes = [C.c_void_p, C.c_uint32]
        L.nx_d3d9_format.restype = NxFormatInfo; L.nx_d3d9_format.argtypes = [C.c_uint32]
        L.nx_d3d9_bias_scale.restype = C.c_float; L.nx_d3d9_bias_scale.argtypes = [C.c_uint32]
        L.nx_d3d9_sampler.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p]
        L.nx_d3d9_default_rs.argtypes = [C.c_void_p]; L.nx_d3d9_default_samp.argtypes = [C.c_void_p]; L.nx_d3d9_latch_init.argtypes = [C.c_void_p]
        L.nx_d3d9_converted_size.restype = C.c_uint32; L.nx_d3d9_converted_size.argtypes = [C.c_uint32] * 5
        L.nx_d3d9_convert.restype = C.c_uint32
        L.nx_d3d9_convert.argtypes = [C.c_uint32] * 5 + [C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32]


class NxFormatInfo(C.Structure): _fields_ = [("format", C.c_uint8), ("swizzle", C.c_uint8 * 4), ("flags", C.c_uint8), ("bytes", C.c_uint8), ("srcBytes", C.c_uint8)]
class NxSamplerState(C.Structure): _fields_ = [("filter", C.c_uint8), ("maxAniso", C.c_uint8), ("wrap", C.c_uint16), ("minLod", C.c_uint8), ("maxLod", C.c_uint8), ("pad", C.c_uint8 * 2), ("lodBias", C.c_float), ("border", C.c_uint32)]
class NxTex(C.Structure): _fields_ = [("format", C.c_uint32), ("cube", C.c_uint8), ("shadow", C.c_uint8), ("managed_lod", C.c_uint8), ("pad", C.c_uint8)]
class Latch(C.Structure): _fields_ = [("depth_func", C.c_uint8), ("cull", C.c_uint8), ("fill", C.c_uint8), ("flat", C.c_uint8)]


def consts(tmp):
    src = '#include "nx/state.h"\n#include <stdint.h>\nconst struct { const char* n; uint64_t v; } T[] = {' + ",".join('{"%s", (uint64_t)%s}' % (n, n) for n in KN) + ',{0,0}};\n'
    c, so = os.path.join(tmp, "k.c"), os.path.join(tmp, "k.so"); open(c, "w").write(src)
    subprocess.run(["gcc", "-shared", "-fPIC", "-std=c11", "-I", BACKEND, "-I", NXB, c, "-o", so], check=True, capture_output=True)
    class E(C.Structure): _fields_ = [("n", C.c_char_p), ("v", C.c_uint64)]
    arr = (E * (len(KN) + 1)).in_dll(C.CDLL(so), "T")
    return {e.n.decode(): e.v for e in arr if e.n}


class Chk:
    def __init__(self): self.n = {}; self.f = {}; self.msg = []
    def ok(self, part, cond, what):
        self.n[part] = self.n.get(part, 0) + 1
        if not cond:
            self.f[part] = self.f.get(part, 0) + 1
            if len(self.msg) < 25: self.msg.append(f"{part}: {what}")
    def failed(self): return sum(self.f.values())


# ---- oracles (wined3d 9.0 rules / Nine) ----------------------------------------------------------------------------------------------
class Oracle:
    def __init__(self, K): self.K = K
    def cmp(self, v, oor): return v - 1 if 1 <= v <= 8 else oor                      # NxCompare = D3DCMP - 1
    def sop(self, v): return v - 1 if 1 <= v <= 8 else self.K["NxSop_Keep"]            # gl_stencil_op default KEEP
    def bop(self, v): return v - 1 if 1 <= v <= 5 else self.K["NxBop_Add"]             # gl_blend_op default FUNC_ADD
    def bf(self, v, alpha):
        K = self.K; t = {1: "Zero", 2: "One", 3: "SrcColor", 4: "InvSrcColor", 5: "SrcAlpha", 6: "InvSrcAlpha", 7: "DstAlpha", 8: "InvDstAlpha", 9: "DstColor", 10: "InvDstColor",
                         11: "SrcAlphaSat", 12: "SrcAlpha", 13: "InvSrcAlpha", 14: "ConstColor", 15: "InvConstColor"}
        if v == 16: return K["NxBf_One"] if alpha else K["NxBf_Src1Color"]
        if v == 17: return K["NxBf_Zero"] if alpha else K["NxBf_InvSrc1Color"]
        return K["NxBf_" + t[v]] if v in t else K["NxBf_Zero"]                         # gl_blend_factor default GL_NONE == GL_ZERO
    def blend(self, rs):
        K, g = self.K, lambda n: rs[RSN[n]]; w = 0
        if g("ALPHABLENDENABLE"):
            sb, db, sab = g("SRCBLEND"), g("DESTBLEND"), g("SRCBLENDALPHA"); op = self.bop(g("BLENDOP")); sep = g("SEPARATEALPHABLENDENABLE") != 0
            src, dst = self.bf(sb, 0), self.bf(db, 0)
            if sep: opa, srca, dsta = self.bop(g("BLENDOPALPHA")), self.bf(sab, 1), self.bf(g("DESTBLENDALPHA"), 1)
            else: opa, srca, dsta = op, self.bf(sb, 1), self.bf(db, 1)
            if sb in (12, 13):
                dst = K["NxBf_InvSrcAlpha"] if sb == 12 else K["NxBf_SrcAlpha"]
                if not sep: dsta = dst
            elif sep and sab in (12, 13): dsta = K["NxBf_InvSrcAlpha"] if sab == 12 else K["NxBf_SrcAlpha"]
            w |= 1 << K["NXRS_B_ENABLE"] | src << K["NXRS_B_SRC"] | dst << K["NXRS_B_DST"] | op << K["NXRS_B_OP"] | srca << K["NXRS_B_SRCA"] | dsta << K["NXRS_B_DSTA"] | opa << K["NXRS_B_OPA"]
        for i, n in enumerate(("COLORWRITEENABLE", "COLORWRITEENABLE1", "COLORWRITEENABLE2", "COLORWRITEENABLE3")): w |= (g(n) & 15) << (32 + 4 * i)
        return w
    def ztest(self, v): return v in (1, 2)                                              # D3DZB_TRUE, D3DZB_USEW; anything else: desc.depth stays FALSE
    def ds(self, rs, lt):
        K, g = self.K, lambda n: rs[RSN[n]]; w = 0
        lt["depth_func"] = self.cmp(g("ZFUNC"), lt["depth_func"]); f = lt["depth_func"]
        if self.ztest(g("ZENABLE")):
            wr = int(bool(g("ZWRITEENABLE")) and f not in (K["NxCmp_Equal"], K["NxCmp_Never"]))
            w |= 1 << K["NXRS_D_TEST"] | wr << K["NXRS_D_WRITE"] | f << K["NXRS_D_FUNC"]
        if g("STENCILENABLE"):
            face = lambda p: self.cmp(g(p + "FUNC"), K["NxCmp_Always"]) | self.sop(g(p + "FAIL")) << 3 | self.sop(g(p + "ZFAIL")) << 6 | self.sop(g(p + "PASS")) << 9
            front = face("STENCIL"); back = front
            if g("TWOSIDEDSTENCILMODE"): back = face("CCW_STENCIL"); w |= 1 << K["NXRS_S_TWOSIDED"]
            w |= 1 << K["NXRS_S_ENABLE"] | front << K["NXRS_S_FUNC"] | back << K["NXRS_S_BFUNC"]
            w |= (g("STENCILMASK") & 255) << K["NXRS_S_READMASK"] | (g("STENCILWRITEMASK") & 255) << K["NXRS_S_WRITEMASK"] | (g("STENCILREF") & 255) << K["NXRS_S_REF"]
        return w
    def raster(self, rs, lt):
        K, g = self.K, lambda n: rs[RSN[n]]
        lt["cull"] = {1: K["NxCull_None"], 2: K["NxCull_CW"], 3: K["NxCull_CCW"]}.get(g("CULLMODE"), lt["cull"])
        lt["fill"] = {1: K["NxFill_Point"], 2: K["NxFill_Wireframe"], 3: K["NxFill_Solid"]}.get(g("FILLMODE"), lt["fill"])
        lt["flat"] = {1: 1, 2: 0, 3: 0}.get(g("SHADEMODE"), lt["flat"])
        w = lt["cull"] << K["NXRS_R_CULL"] | lt["fill"] << K["NXRS_R_FILL"]
        if g("DEPTHBIAS") | g("SLOPESCALEDEPTHBIAS"): w |= 1 << K["NXRS_R_BIAS"]
        if g("SCISSORTESTENABLE"): w |= 1 << K["NXRS_R_SCISSOR"]
        if g("SRGBWRITEENABLE"): w |= 1 << K["NXRS_R_SRGB"]
        if lt["flat"]: w |= 1 << K["NXRS_R_FLAT"]
        if g("CLIPPING"): w |= (g("CLIPPLANEENABLE") & 255) << K["NXRS_R_CLIP"]
        if g("POINTSPRITEENABLE"): w |= 1 << K["NXRS_R_POINTSPRITE"]
        return w
    def bits(self, rs, ps):
        K, g = self.K, lambda n: rs[RSN[n]]; b = 0
        if g("ALPHATESTENABLE"): b |= 1 << K["NXSB_ALPHATEST"] | self.cmp(g("ALPHAFUNC"), K["NxCmp_Always"]) << K["NXSB_ALPHAFUNC"]   # wined3d_sanitize_cmp_func
        if g("FOGENABLE") and (ps & 0xffff) < 0x300:
            b |= {0: K["NxFog_Vertex"], 1: K["NxFog_Exp"], 2: K["NxFog_Exp2"], 3: K["NxFog_Linear"]}.get(g("FOGTABLEMODE"), K["NxFog_None"]) << K["NXSB_FOG"]
        return b
    def wrap(self, a, cube):
        K = self.K; t = {1: "Repeat", 2: "Mirror", 3: "Clamp", 4: "Border", 5: "MirrorOnce"}
        if a not in t: return K["NxWrap_Repeat"]        # get_texture_address_mode: out of range -> WRAP, before the cube clamp
        return K["NxWrap_Clamp"] if cube else K["NxWrap_" + t[a]]
    def filt(self, v):                                    # (point, aniso, mip)  min(max(v, POINT), LINEAR) for out of range
        K = self.K
        if v == 0: return (0, 0, K["NxMip_None"])
        if v == 1: return (1, 0, K["NxMip_Nearest"])
        return (0, int(v == 3), K["NxMip_Linear"])
    def sampler(self, s, tex, fmtflags):
        K, g = self.K, lambda n: s[SMN[n]]; fl = 0; lb = 0.0; minlod = 0
        mip, mag, mn = self.filt(g("MIPFILTER")), self.filt(g("MAGFILTER")), self.filt(g("MINFILTER"))
        if mip[2] != K["NxMip_None"]:
            lb = f32(g("MIPMAPLODBIAS")); minlod = min(15, max(0, g("MAXMIPLEVEL") - tex["managed_lod"])); fl |= mip[2] << K["NXSMP_MIP_SHIFT"]
        wrap = self.wrap(g("ADDRESSU"), tex["cube"]) | self.wrap(g("ADDRESSV"), tex["cube"]) << 3 | self.wrap(g("ADDRESSW"), tex["cube"]) << 6
        if not (mn[0] and not tex["shadow"]): fl |= K["NXSMP_MIN_LINEAR"]
        if not (mag[0] and not tex["shadow"]): fl |= K["NXSMP_MAG_LINEAR"]
        aniso = min(255, g("MAXANISOTROPY")) if (mn[1] or mag[1]) else 0
        if tex["shadow"]: fl |= K["NXSMP_SHADOW"]
        if g("SRGBTEXTURE") and fmtflags & K["NXFMT_SRGB"]: fl |= K["NXSMP_SRGB"]
        c = g("BORDERCOLOR"); border = (c >> 16 & 255) | (c & 0xff00) | (c & 255) << 16 | (c & 0xff000000)
        return dict(filter=fl, maxAniso=aniso, wrap=wrap, minLod=minlod, maxLod=15, lodBias=lb, border=border)


def f32(u): import struct; return struct.unpack("<f", struct.pack("<I", u))[0]


def fmt_table(K):
    """oracle: D3DFORMAT value -> (NxFmt, swizzle, flags, bytes, srcBytes, depth bias scale); absent = no native layout (Nine's basetexture9.c / nine_pipe.c tables)"""
    SR, RT, CMP, Z, ZS, CV = K["NXFMT_SRGB"], K["NXFMT_RENDER"], K["NXFMT_COMPRESSED"], K["NXFMT_DEPTH"], K["NXFMT_DEPTH"] | K["NXFMT_STENCIL"], K["NXFMT_CONVERT"]
    R, G, B, A, S0, S1 = (K["NxSwz_" + c] for c in "RGBA01"); n = lambda s: K["NxFmt_" + s]; D = 2.0 ** 23; T = {}
    def put(v, fmt, sw, fl, by, src=None, bias=D): T[v] = (n(fmt), tuple(sw), fl, by, by if src is None else src, bias)
    E = ORACLE_ENUMS["D3DFORMAT"]; e = lambda s: E["D3DFMT_" + s]
    put(21, "BGRA8", (R, G, B, A), SR | RT, 4); put(22, "BGRX8", (R, G, B, S1), SR | RT, 4); put(32, "RGBA8", (R, G, B, A), SR | RT, 4); put(33, "RGBX8", (R, G, B, S1), SR | RT, 4)
    put(23, "R5G6B5", (R, G, B, S1), RT, 2); put(24, "X1R5G5B5", (R, G, B, S1), RT, 2); put(25, "A1R5G5B5", (R, G, B, A), RT, 2); put(26, "A4R4G4B4", (R, G, B, A), 0, 2)
    put(30, "A4R4G4B4", (R, G, B, S1), 0, 2); put(31, "RGB10A2", (R, G, B, A), RT, 4); put(35, "BGR10A2", (R, G, B, A), RT, 4); put(34, "RG16", (R, G, S1, S1), RT, 4)
    put(36, "RGBA16", (R, G, B, A), RT, 8); put(28, "RGBA8", (R, G, B, A), CV | RT, 4, 1); put(50, "RGBA8", (R, R, R, S1), SR | CV | RT, 4, 1); put(51, "RGBA8", (R, G, B, A), SR | CV, 4, 2)
    put(81, "RGBA16", (R, G, B, S1), CV, 8, 2); put(60, "RG8S", (R, G, S1, S1), RT, 2); put(63, "RGBA8S", (R, G, B, A), RT, 4); put(64, "RG16S", (R, G, S1, S1), RT, 4)
    put(110, "RGBA16S", (R, G, B, A), RT, 8); put(20, "BGRX8", (R, G, B, S1), SR | CV | RT, 4, 3); put(61, "RGBA32F", (R, G, B, S1), CV, 16, 2); put(62, "RGBA32F", (R, G, B, S1), CV, 16, 4)
    put(101, "R16", (R, S1, S1, S1), RT, 2); put(102, "R32F", (R, S1, S1, S1), RT, 4); put(111, "R16F", (R, S1, S1, S1), RT, 2); put(112, "RG16F", (R, G, S1, S1), RT, 4); put(113, "RGBA16F", (R, G, B, A), RT, 8)
    put(114, "R32F", (R, S1, S1, S1), RT, 4); put(115, "RG32F", (R, G, S1, S1), RT, 8); put(116, "RGBA32F", (R, G, B, A), RT, 16)
    put(e("DXT1"), "BC1", (R, G, B, A), CMP | SR, 8); put(e("DXT2"), "BC2", (R, G, B, A), CMP | SR, 16); put(e("DXT3"), "BC2", (R, G, B, A), CMP | SR, 16)
    put(e("DXT4"), "BC3", (R, G, B, A), CMP | SR, 16); put(e("DXT5"), "BC3", (R, G, B, A), CMP | SR, 16)
    put(e("ATI1"), "RGBA8", (R, G, B, A), CV, 4, 8); put(e("ATI2"), "BC5", (G, R, S1, S1), CMP, 16)
    put(70, "Z16", (R, R, R, R), Z, 2, bias=2.0 ** 16); put(80, "Z16", (R, R, R, R), Z, 2, bias=2.0 ** 16); put(75, "Z24S8", (R, R, R, R), ZS, 4, bias=2.0 ** 24)
    put(77, "Z24X8", (R, R, R, R), Z, 4, bias=2.0 ** 24); put(82, "Z32F", (R, R, R, R), Z, 4); put(83, "Z32FS8", (R, R, R, R), ZS, 8)
    put(e("INTZ"), "Z24S8", (R, R, R, R), ZS, 4, bias=2.0 ** 24); put(e("DF24"), "Z24X8", (R, S0, S0, S1), Z, 4, bias=2.0 ** 24); put(e("DF16"), "Z16", (R, S0, S0, S1), Z, 2, bias=2.0 ** 16)
    for v, b in ((71, 2.0 ** 32), (73, 2.0 ** 15), (79, 2.0 ** 24)): T[v] = (0, (R, G, B, A), 0, 0, 0, b)   # no native layout, but a bias scale
    return T


def fmt_domain():
    """every value of [0, 0xca] (max enumerator below the FOURCCs 199 + 2 + margin), every enumerator and its neighbours, the extremes"""
    d = set(range(0, 0xcb)) | {0x7fffffff, 0xffffffff}
    for v in ORACLE_ENUMS["D3DFORMAT"].values(): d |= {v - 1, v, v + 1}
    return sorted(x & M32 for x in d)


def dom(e):
    mx = max(ORACLE_ENUMS[e].values()); return sorted(set(range(0, mx + 3)) | {0x7fffffff, 0xffffffff})


# ---- the checks ----------------------------------------------------------------------------------------------------------------------
def sweeps(L, K, ck):
    lib, O = L.lib, Oracle(K); RS = (C.c_uint32 * 210); SM = (C.c_uint32 * 14)
    def mk_rs(ctx):
        rs = RS(); lib.nx_d3d9_default_rs(rs)
        if ctx == 1:
            for n, v in dict(ALPHABLENDENABLE=1, STENCILENABLE=1, ZENABLE=1, ALPHATESTENABLE=1, FOGENABLE=1, TWOSIDEDSTENCILMODE=1, SEPARATEALPHABLENDENABLE=1, SRCBLEND=5, DESTBLEND=6,
                             SRCBLENDALPHA=7, DESTBLENDALPHA=8, STENCILFUNC=3, CCW_STENCILFUNC=6, STENCILFAIL=3, CCW_STENCILPASS=7, ZFUNC=2, ALPHAFUNC=5, FOGTABLEMODE=2, CULLMODE=2,
                             FILLMODE=2, SHADEMODE=1, BLENDOP=3, BLENDOPALPHA=4, ZWRITEENABLE=1).items(): rs[RSN[n]] = v
        return rs
    def lt_init():
        l = Latch(); lib.nx_d3d9_latch_init(C.byref(l)); return l
    def whole(rs, ctxname, what):
        l = lt_init(); lo = dict(depth_func=l.depth_func, cull=l.cull, fill=l.fill, flat=l.flat)
        pyrs = list(rs)
        for ps in (0, 0x200, 0x300):
            ck.ok("b", lib.nx_d3d9_shader_bits(rs, ps) == O.bits(pyrs, ps), f"bits {what} {ctxname} ps={ps:x}")
        ck.ok("b", lib.nx_d3d9_blend_word(rs) == O.blend(pyrs), f"blend {what} {ctxname}")
        ck.ok("b", lib.nx_d3d9_depth_stencil_word(rs, C.byref(l)) == O.ds(pyrs, lo), f"ds {what} {ctxname}")
        ck.ok("b", lib.nx_d3d9_raster_word(rs, C.byref(l)) == O.raster(pyrs, lo), f"raster {what} {ctxname}")
        ck.ok("b", (l.depth_func, l.cull, l.fill, l.flat) == (lo["depth_func"], lo["cull"], lo["fill"], lo["flat"]), f"latch {what} {ctxname}")
    # states holding an enumeration
    ENUMSTATE = {"ZENABLE": "D3DZBUFFERTYPE", "ZFUNC": "D3DCMPFUNC", "ALPHAFUNC": "D3DCMPFUNC", "STENCILFUNC": "D3DCMPFUNC", "CCW_STENCILFUNC": "D3DCMPFUNC",
                 "STENCILFAIL": "D3DSTENCILOP", "STENCILZFAIL": "D3DSTENCILOP", "STENCILPASS": "D3DSTENCILOP", "CCW_STENCILFAIL": "D3DSTENCILOP", "CCW_STENCILZFAIL": "D3DSTENCILOP",
                 "CCW_STENCILPASS": "D3DSTENCILOP", "SRCBLEND": "D3DBLEND", "DESTBLEND": "D3DBLEND", "SRCBLENDALPHA": "D3DBLEND", "DESTBLENDALPHA": "D3DBLEND",
                 "BLENDOP": "D3DBLENDOP", "BLENDOPALPHA": "D3DBLENDOP", "CULLMODE": "D3DCULL", "FILLMODE": "D3DFILLMODE", "SHADEMODE": "D3DSHADEMODE", "FOGTABLEMODE": "D3DFOGMODE"}
    for st, e in ENUMSTATE.items():
        for ctx in (0, 1):
            for v in dom(e):
                rs = mk_rs(ctx); rs[RSN[st]] = v; whole(rs, f"ctx{ctx}", f"{st}={v:#x}")
    # states that are tested for non-zero / masked: a spread of values
    for st in ("ZWRITEENABLE", "STENCILENABLE", "ALPHATESTENABLE", "ALPHABLENDENABLE", "FOGENABLE", "TWOSIDEDSTENCILMODE", "SEPARATEALPHABLENDENABLE", "SCISSORTESTENABLE",
               "SRGBWRITEENABLE", "POINTSPRITEENABLE", "CLIPPING", "CLIPPLANEENABLE", "COLORWRITEENABLE", "COLORWRITEENABLE1", "COLORWRITEENABLE2", "COLORWRITEENABLE3", "STENCILMASK",
               "STENCILWRITEMASK", "STENCILREF", "DEPTHBIAS", "SLOPESCALEDEPTHBIAS"):
        for ctx in (0, 1):
            for v in MASKS:
                rs = mk_rs(ctx); rs[RSN[st]] = v; whole(rs, f"ctx{ctx}", f"{st}={v:#x}")
    # samplers
    FT = fmt_table(K); flagsof = lambda f: FT[f][2] if f in FT else 0
    for cube in (0, 1):
        for shadow in (0, 1):
            for st, e in (("ADDRESSU", "D3DTEXTUREADDRESS"), ("ADDRESSV", "D3DTEXTUREADDRESS"), ("ADDRESSW", "D3DTEXTUREADDRESS"), ("MAGFILTER", "D3DTEXTUREFILTERTYPE"),
                          ("MINFILTER", "D3DTEXTUREFILTERTYPE"), ("MIPFILTER", "D3DTEXTUREFILTERTYPE")):
                for v in dom(e):
                    for base in (0, 1):
                        s = SM(); lib.nx_d3d9_default_samp(s)
                        if base: s[SMN["MAGFILTER"]] = 2; s[SMN["MINFILTER"]] = 3; s[SMN["MIPFILTER"]] = 2; s[SMN["MAXANISOTROPY"]] = 8; s[SMN["MIPMAPLODBIAS"]] = 0x3f000000
                        s[SMN[st]] = v; t = NxTex(21, cube, shadow, 1, 0); o = NxSamplerState(); lib.nx_d3d9_sampler(s, C.byref(t), C.byref(o))
                        want = O.sampler(list(s), dict(cube=cube, shadow=shadow, managed_lod=1), flagsof(21))
                        got = dict(filter=o.filter, maxAniso=o.maxAniso, wrap=o.wrap, minLod=o.minLod, maxLod=o.maxLod, lodBias=o.lodBias, border=o.border)
                        ck.ok("b", got == want, f"sampler {st}={v:#x} cube={cube} shadow={shadow} base={base}: {got} != {want}")
    for v in MASKS + [0x3f800000, 0xc0000000]:                       # the remaining sampler states
        for st in ("BORDERCOLOR", "MIPMAPLODBIAS", "MAXMIPLEVEL", "MAXANISOTROPY", "SRGBTEXTURE"):
            for f in (21, 28, 0x31545844, 0xdead):
                s = SM(); lib.nx_d3d9_default_samp(s); s[SMN["MIPFILTER"]] = 2; s[SMN["MINFILTER"]] = 3; s[SMN[st]] = v
                t = NxTex(f, 0, 0, 2, 0); o = NxSamplerState(); lib.nx_d3d9_sampler(s, C.byref(t), C.byref(o))
                want = O.sampler(list(s), dict(cube=0, shadow=0, managed_lod=2), flagsof(f))
                got = dict(filter=o.filter, maxAniso=o.maxAniso, wrap=o.wrap, minLod=o.minLod, maxLod=o.maxLod, lodBias=o.lodBias, border=o.border)
                ck.ok("b", got == want or (got["lodBias"] != got["lodBias"] and want["lodBias"] != want["lodBias"]), f"sampler {st}={v:#x} fmt={f:#x}: {got} != {want}")


def latch_checks(L, K, ck):
    lib, O = L.lib, Oracle(K); RS = (C.c_uint32 * 210)
    OOR = {"ZFUNC": [0, 9, 10, 0x7fffffff, 0xffffffff], "CULLMODE": [0, 4, 5, 0x7fffffff, 0xffffffff], "FILLMODE": [0, 4, 5, 0x7fffffff, 0xffffffff],
           "SHADEMODE": [0, 4, 5, 0x7fffffff, 0xffffffff]}
    VALID = {"ZFUNC": range(1, 9), "CULLMODE": range(1, 4), "FILLMODE": range(1, 4), "SHADEMODE": range(1, 4)}
    fieldof = {"ZFUNC": "depth_func", "CULLMODE": "cull", "FILLMODE": "fill", "SHADEMODE": "flat"}
    l0 = Latch(); lib.nx_d3d9_latch_init(C.byref(l0))
    rs = RS(); lib.nx_d3d9_default_rs(rs); l = Latch(); lib.nx_d3d9_depth_stencil_word(rs, C.byref(l)); lib.nx_d3d9_raster_word(rs, C.byref(l))
    ck.ok("c", (l0.depth_func, l0.cull, l0.fill, l0.flat) == (K["NxCmp_LEqual"], K["NxCull_CCW"], K["NxFill_Solid"], 0), "latch_init = translation of the defaults")
    for st, fld in fieldof.items():
        for x in VALID[st]:
            for y in OOR[st]:
                for zen in (0, 1):                                    # the latch takes the func also while the depth test is off
                    rs = RS(); lib.nx_d3d9_default_rs(rs); rs[RSN["ZENABLE"]] = zen; lat = Latch(); lib.nx_d3d9_latch_init(C.byref(lat))
                    rs[RSN[st]] = x; lib.nx_d3d9_depth_stencil_word(rs, C.byref(lat)); a = lib.nx_d3d9_raster_word(rs, C.byref(lat)); kept = getattr(lat, fld)
                    rs[RSN[st]] = y; b = lib.nx_d3d9_depth_stencil_word(rs, C.byref(lat)); c = lib.nx_d3d9_raster_word(rs, C.byref(lat))
                    ck.ok("c", getattr(lat, fld) == kept, f"{st}: valid {x} then {y:#x} must keep the value")
                    ck.ok("c", c == a, f"{st}: raster word after {y:#x} == word after {x}")


def format_checks(L, K, ck):
    lib = L.lib; FT = fmt_table(K); gaps = {k: [] for k in ("texture", "texture RT", "texture DS", "cube", "cube RT", "cube DS", "volume", "volume RT", "volume DS", "surface", "surface RT", "surface DS")}
    for v in fmt_domain():
        i = lib.nx_d3d9_format(v); got = (i.format, tuple(i.swizzle), i.flags, i.bytes, i.srcBytes) if i.format else (0, tuple(i.swizzle), i.flags, 0, 0)
        w = FT.get(v); want = (w[0], w[1], w[2], w[3], w[4]) if w and w[0] else (0, (K["NxSwz_R"], K["NxSwz_G"], K["NxSwz_B"], K["NxSwz_A"]), K["NXFMT_NULL"] if v == FCC("NULL") else 0, 0, 0)
        ck.ok("d", got == want, f"format {v:#x}: {got} != {want}")
        ck.ok("d", lib.nx_d3d9_bias_scale(v) == (w[5] if w else 2.0 ** 23), f"bias scale {v:#x}")
    # the environment table, per ROLE (resource type x usage): every format the reference environment (Wine 9.0 + llvmpipe) lets an application create in an image role
    # has a native format with the capability the role needs, or is NULL (a target without storage), or is a zero-size class (a capability probe, not creatable storage:
    # the layout oracle of tests/l6/tex). Buffers (index / vertex) are not image types and are not asked.
    cdf = {}
    for prof in ("handheld", "docked"):
        for ln in open(os.path.join(ROOT, "render", "d3d9", "env", f"env_{prof}.tsv")):
            m = re.match(r"CheckDeviceFormat\(0,1,16,([0-9a-f]+),([0-9a-f]+),([0-9a-f]+)\)\t([0-9a-f]+)\t", ln)
            if m and int(m.group(4), 16) == 0: cdf.setdefault((int(m.group(1), 16), int(m.group(2), 16)), set()).add(int(m.group(3), 16))   # (usage, resource type)
    ck.ok("d", all(v in envq.FMT for s in cdf.values() for v in s), "env formats are in the envq domain")
    for (rt, us), name in (((3, 0), "texture"), ((3, 1), "texture RT"), ((3, 2), "texture DS"), ((5, 0), "cube"), ((5, 1), "cube RT"), ((5, 2), "cube DS"), ((4, 0), "volume"), ((4, 1), "volume RT"),
                           ((4, 2), "volume DS"), ((1, 0), "surface"), ((1, 1), "surface RT"), ((1, 2), "surface DS")):
        need = {0: 0, 1: K["NXFMT_RENDER"], 2: K["NXFMT_DEPTH"]}[us]
        for v in sorted(cdf.get((us, rt), ())):   # key (usage, resource type)
            i = lib.nx_d3d9_format(v)
            if texoracle.layout(v) is None: continue                       # zero-size class: not creatable
            if i.format == 0 and (i.flags & K["NXFMT_NULL"]): continue      # NULL: no storage by rule
            if rt == 1 and us == 0: continue                                # a plain offscreen surface needs a memory layout only (CPU copy)
            if i.format == 0 or (need and not i.flags & need): gaps[name].append(v)
    return {k: sorted(x) for k, x in gaps.items() if x}


def conv_checks(L, K, ck):
    lib = L.lib
    def run(fmt, w, h, mips, faces, src):
        n = lib.nx_d3d9_converted_size(fmt, w, h, mips, faces); b = (C.c_uint8 * max(n, 1))(); s = (C.c_uint8 * len(src))(*src)
        r = lib.nx_d3d9_convert(fmt, w, h, mips, faces, s, len(src), b, n); return n, r, bytes(b[:n])
    n, r, o = run(28, 2, 1, 1, 1, [7, 9]); ck.ok("e", (n, r, o) == (8, 8, bytes([0, 0, 0, 7, 0, 0, 0, 9])), f"A8 {o}")
    n, r, o = run(50, 2, 1, 1, 1, [7, 9]); ck.ok("e", o == bytes([7, 7, 7, 255, 9, 9, 9, 255]), f"L8 {o}")
    n, r, o = run(51, 1, 1, 1, 1, [7, 9]); ck.ok("e", o == bytes([7, 7, 7, 9]), f"A8L8 {o}")
    n, r, o = run(81, 1, 1, 1, 1, [0x34, 0x12]); ck.ok("e", o == bytes([0x34, 0x12, 0x34, 0x12, 0x34, 0x12, 255, 255]), f"L16 {o}")
    ck.ok("e", lib.nx_d3d9_convert(35, 1, 1, 1, 1, bytes(4), 4, bytes(4), 4) == 0 and lib.nx_d3d9_converted_size(35, 4, 4, 1, 1) == 64, "A2R10G10B10 is native (no conversion)")
    # R8G8B8: bytes B,G,R -> B,G,R,255; unconvert is the inverse
    px = [(0, 0, 0), (1, 2, 3), (255, 0, 128), (200, 100, 50)]; src = [c for p in px for c in p]
    n, r, o = run(20, 4, 1, 1, 1, src); ck.ok("e", o == bytes(c for p in px for c in (*p, 255)), f"R8G8B8 {o.hex()}")
    nb = (C.c_uint8 * 12)(); lib.nx_d3d9_unconvert.restype = C.c_uint32; lib.nx_d3d9_unconvert.argtypes = [C.c_uint32] * 3 + [C.c_void_p, C.c_uint32, C.c_void_p, C.c_uint32]
    ck.ok("e", lib.nx_d3d9_unconvert(20, 4, 1, o, 16, nb, 12) == 12 and bytes(nb) == bytes(src), "R8G8B8 unconvert = inverse")
    # L6V5U5 / X8L8V8U8 storage = RGBA32F (U, V = max(c, -m) / m, L = c / (2^n - 1), alpha = side word): every code against an independent decoder, then the exact round trip through unconvert
    import struct
    def sgn(v, bits): return v - (1 << bits) if v >> (bits - 1) else v
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
    unc = lambda fmt, n, raw, ob: (lambda d: (lib.nx_d3d9_unconvert(fmt, n, 1, raw, len(raw), d, n * ob), bytes(d))[1])((C.c_uint8 * (n * ob))())
    allv = b"".join(v.to_bytes(2, "little") for v in range(65536)); n, r, o = run(61, 65536, 1, 1, 1, list(allv)); good = n == 65536 * 16 and r == n
    for v in range(65536):
        u, vv, l = sgn(v & 31, 5), sgn(v >> 5 & 31, 5), v >> 10; w = struct.unpack_from("<4f", o, 16 * v)
        want = (f32(max(u, -15) / 15), f32(max(vv, -15) / 15), f32(l / 63), float((u == -16) | (vv == -16) << 1))
        if w != want: good = False; ck.ok("e", False, f"L6V5U5 {v:#06x}: {w} != {want}"); break
    ck.ok("e", good, "L6V5U5 all 65536 texels = D3D9 sampled values")
    ck.ok("e", unc(61, 65536, o, 2) == allv, "L6V5U5 unconvert: all 65536 codes round-trip exactly (incl. the most negative U and V)")
    lib.nx_d3d9_unconvert(61, 1, 1, o, 15, (C.c_uint8 * 2)(), 2); ck.ok("e", lib.nx_d3d9_unconvert(61, 1, 1, o, 15, (C.c_uint8 * 2)(), 2) == 0, "L6V5U5 unconvert: short source")
    # X8L8V8U8: every code of each channel (U and V over all 256 with every L, and x over all 256), the codes of the three channels independent
    vals = [(u, v, 0, 0) for u in range(256) for v in range(256)] + [(0x80, 0x80, l, 0) for l in range(256)] + [(5, 0xF0, 3, x) for x in range(256)] + [(l, l, l, l) for l in range(256)]
    src = b"".join(bytes(t) for t in vals); n, r, o = run(62, len(vals), 1, 1, 1, list(src)); good = n == len(vals) * 16 and r == n
    for k, (u, v, l, x) in enumerate(vals):
        w = struct.unpack_from("<4f", o, 16 * k); want = (f32(max(sgn(u, 8), -127) / 127), f32(max(sgn(v, 8), -127) / 127), f32(l / 255), float((u == 128) | (v == 128) << 1 | x << 2))
        if w != want: good = False; ck.ok("e", False, f"X8L8V8U8 {(u, v, l, x)}: {w} != {want}"); break
    ck.ok("e", good, "X8L8V8U8 every code of U, V, L, x = D3D9 sampled values")
    ck.ok("e", unc(62, len(vals), o, 4) == src, "X8L8V8U8 unconvert: every code of each channel (incl. the x byte) round-trips exactly")
    # target rules: write mask / clear value / read-back of A8, L8, R8G8B8; everything else the identity
    m = C.c_uint8(); lib.nx_d3d9_rt_rule.argtypes = [C.c_uint32, C.c_void_p]; lib.nx_d3d9_clear_rgba8.restype = C.c_uint32; lib.nx_d3d9_clear_rgba8.argtypes = [C.c_uint32] * 2
    for f, wm in ((28, 8), (50, 1), (20, 7), (21, 15), (51, 15), (36, 15), (61, 15), (62, 15)):
        lib.nx_d3d9_rt_rule(f, C.byref(m)); ck.ok("e", m.value == wm, f"rt rule of {f}: {m.value}")
    col = 0x80402010                                                                   # A 0x80, R 0x40, G 0x20, B 0x10
    for f, want in ((28, 0xFF000000 & 0 | 0x80000000), (50, 0xFF000040), (20, 0xFF102040), (21, 0x80102040)):
        ck.ok("e", lib.nx_d3d9_clear_rgba8(f, col) == want, f"clear value of {f}: {lib.nx_d3d9_clear_rgba8(f, col):#x} != {want:#x}")
    nat = bytes([5, 6, 7, 8, 1, 2, 3, 4]); b4 = (C.c_uint8 * 2)()
    ck.ok("e", lib.nx_d3d9_unconvert(28, 2, 1, nat, 8, b4, 2) == 2 and bytes(b4) == bytes([8, 4]), "A8 unconvert takes alpha")
    ck.ok("e", lib.nx_d3d9_unconvert(50, 2, 1, nat, 8, b4, 2) == 2 and bytes(b4) == bytes([5, 1]), "L8 unconvert takes red")
    ck.ok("e", lib.nx_d3d9_unconvert(21, 2, 1, nat, 8, b4, 2) == 0 and lib.nx_d3d9_unconvert(20, 2, 1, nat, 7, bytes(6), 6) == 0, "no unconvert for a plain format / a short source")
    for blk in ([200, 100, 0x88, 0xc6, 0xfa, 0x1b, 0x2d, 0x3f], [10, 200, 0x91, 0x2a, 0x55, 0xaa, 0x0f, 0xf0], [0, 0] + [0xff] * 6):
        e = [blk[0], blk[1]]
        if e[0] > e[1]: e += [((8 - c) * e[0] + (c - 1) * e[1]) // 7 for c in range(2, 8)]
        else: e += [((6 - c) * e[0] + (c - 1) * e[1]) // 5 for c in range(2, 6)] + [0, 255]
        bits = int.from_bytes(bytes(blk[2:]), "little"); want = b"".join(bytes([e[(bits >> 3 * t) & 7], 0, 0, 255]) for t in range(16))
        n, r, o = run(FCC("ATI1"), 4, 4, 1, 1, blk); ck.ok("e", o == want, f"ATI1 block {blk[:2]}")
    ck.ok("e", lib.nx_d3d9_converted_size(21, 4, 4, 1, 1) == 64 and lib.nx_d3d9_convert(21, 4, 4, 1, 1, bytes(64), 64, bytes(64), 64) == 0, "no conversion for a plain format")
    ck.ok("e", lib.nx_d3d9_convert(50, 2, 2, 1, 1, bytes(3), 3, bytes(16), 16) == 0, "short source")


def enum_checks(tmp, ck):
    h, j = os.path.join(tmp, "gen.h"), os.path.join(tmp, "gen.json")
    subprocess.run([sys.executable, "-I", os.path.join(FRONT, "gen_enums.py"), h, j], check=True, capture_output=True)
    ck.ok("a", open(h).read() == open(os.path.join(FRONT, "d3d9_enums.gen.h")).read(), "regenerated header == committed header")
    gen = json.load(open(j)); spec = json.load(open(os.path.join(ROOT, "render", "d3d9", "spec", "d3d9_spec.json")))["enums"]
    for e, want in ORACLE_ENUMS.items():
        got = {n: v for n, v in gen["enums"][e] if not n.endswith("FORCE_DWORD")}
        ck.ok("a", got == want, f"{e}: generated enumerators != oracle table ({set(got.items()) ^ set(want.items())})")
        names = [n for n, _ in gen["enums"][e]]
        ck.ok("a", set(spec[e]) <= set(names) and set(names) - set(spec[e]) <= {"D3DFMT_" + s.strip() for s in gen["vendor_fourcc"]}, f"{e}: names == spec names (+ vendor FOURCCs)")
    vend = {"D3DFMT_" + s.strip() for s in gen["vendor_fourcc"]}
    ck.ok("a", not (vend & set(spec["D3DFORMAT"])), "the vendor FOURCCs are not enumerators of the spec's D3DFORMAT")
    ck.ok("a", set(gen["vendor_fourcc"]) <= set(envq.FOURCC_LIST), "the vendor FOURCCs are in envq's FOURCC list (the environment probe asks for each)")
    for n, i in RSN.items(): ck.ok("a", dict(gen["enums"]["D3DRENDERSTATETYPE"]).get("D3DRS_" + n) == i, f"D3DRS_{n} == {i}")
    for n, i in SMN.items(): ck.ok("a", dict(gen["enums"]["D3DSAMPLERSTATETYPE"]).get("D3DSAMP_" + n) == i, f"D3DSAMP_{n} == {i}")


def check_all(L, K, tmp, ck, with_enum=True):
    if with_enum: enum_checks(tmp, ck)
    sweeps(L, K, ck); latch_checks(L, K, ck); gaps = format_checks(L, K, ck); conv_checks(L, K, ck)
    ck.ok("d", not gaps, f"formats the env table lets an application create without storage rule / native format: {gaps}")
    return gaps


MUTATIONS = [   # (file, old, new): each must be caught by the sweeps (or by the build)
    ("front_rs.c", "uint32_t r = NxSop_Keep;", "uint32_t r = NxSop_Zero;"),                                 # stencil op out of range: ZERO instead of KEEP
    ("front_rs.c", "static uint32_t stencil_func(uint32_t d) { return nxfront_cmp(d, NxCmp_Always); }", "static uint32_t stencil_func(uint32_t d) { return nxfront_cmp(d, NxCmp_Never); }"),
    ("front_rs.c", "uint32_t r = NxBf_Zero;\n    if (!nxd3d_in_D3DBLEND(d)) return r;", "uint32_t r = NxBf_One;\n    if (!nxd3d_in_D3DBLEND(d)) return r;"),
    ("front_rs.c", "case NX_D3DZB_USEW: r = 1; break;", "case NX_D3DZB_USEW: r = 0; break;"),
    ("front_rs.c", "latch->cull = (uint8_t)cull_mode(NXF_RS(rs, CULLMODE), latch->cull);", "latch->cull = (uint8_t)cull_mode(NXF_RS(rs, CULLMODE), NxCull_None);"),
    ("front_rs.c", "uint32_t f = nxfront_cmp(NXF_RS(rs, ZFUNC), latch->depth_func);", "uint32_t f = nxfront_cmp(NXF_RS(rs, ZFUNC), NxCmp_Never);"),
    ("front_rs.c", "case NX_D3DBLEND_SRCCOLOR2: r = alpha ? NxBf_One : NxBf_Src1Color; break;", "case NX_D3DBLEND_SRCCOLOR2: r = NxBf_Src1Color; break;"),
    ("front_dyn.c", "uint32_t r = NxFog_None;", "uint32_t r = NxFog_Vertex;"),
    ("front_dyn.c", "nxfront_cmp(NXF_RS(rs, ALPHAFUNC), NxCmp_Always)", "nxfront_cmp(NXF_RS(rs, ALPHAFUNC), NxCmp_Never)"),
    ("front_samp.c", "uint32_t r = NxWrap_Repeat;\n    if (!nxd3d_in_D3DTEXTUREADDRESS(a)) return r;", "uint32_t r = NxWrap_Clamp;\n    if (!nxd3d_in_D3DTEXTUREADDRESS(a)) return r;"),
    ("front_samp.c", "FilterClass r = {0, 0, NxMip_Linear};", "FilterClass r = {1, 0, NxMip_Linear};"),
    ("front_fmt.c", "NONE();                                  /* out of range (WINED3DFMT_UNKNOWN) */\n    if (!nxd3d_in_D3DFORMAT(d)) return r;", "NAT(NxFmt_BGRA8, R, G, B, A, 0, 4);\n    if (!nxd3d_in_D3DFORMAT(d)) return r;"),
    ("front_fmt.c", "case NX_D3DFMT_DXT5: NAT(NxFmt_BC3, R, G, B, A, C_ | SR_, 16); break;", "case NX_D3DFMT_DXT5: NAT(NxFmt_BC2, R, G, B, A, C_ | SR_, 16); break;"),
    ("front_rs.c", "    case NX_D3DCMP_GREATER: r = NxCmp_Greater; break;\n", ""),                         # a case removed from a switch: the build must fail (-Werror=switch-enum)
]


def mutations(tmp, ck, K):
    import concurrent.futures as cf
    def one(i):
        f, old, new = MUTATIONS[i]; d = os.path.join(tmp, f"m{i}"); shutil.copytree(FRONT, d, ignore=shutil.ignore_patterns("__pycache__"))
        txt = open(os.path.join(d, f)).read()
        open(os.path.join(d, f), "w").write(mutsrc.apply(txt, old, new, f"{f} #{i}")); L = Lib(d, os.path.join(tmp, f"m{i}o"), "m")
        if L.lib is None: return i, "build fails" if "switch" in L.err else "unexpected build error: " + L.err
        c = Chk(); sweeps(L, K, c); latch_checks(L, K, c); format_checks(L, K, c); return i, "caught" if c.failed() else "NOT CAUGHT"
    with cf.ThreadPoolExecutor(2) as ex:
        for i, r in ex.map(one, range(len(MUTATIONS))):
            print(f"mutation {i} ({MUTATIONS[i][0]}: {MUTATIONS[i][1][:56]!r}): {r}"); ck.ok("f", r in ("caught", "build fails"), f"mutation {i}: {r}")


def main():
    tmp = tempfile.mkdtemp(prefix="test_front_"); ck = Chk()
    try:
        K = consts(tmp); L = Lib(FRONT, os.path.join(tmp, "base"), "front")
        if L.lib is None: print("BUILD FAILED\n" + L.err); return 1
        gaps = check_all(L, K, tmp, ck)
        print("gaps (creatable in the reference environment, no native layout / render flag): " + json.dumps({k: [hex(x) for x in v] for k, v in gaps.items()}))
        if "--no-mutations" not in sys.argv: mutations(tmp, ck, K)
        for p in sorted(ck.n): print(f"PART {p}: {ck.n[p]} checks, {ck.f.get(p, 0)} failed")
        for m in ck.msg: print("  " + m[:300])
        print("ALL OK" if not ck.failed() else "FAILED")
        return 1 if ck.failed() else 0
    finally: shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
