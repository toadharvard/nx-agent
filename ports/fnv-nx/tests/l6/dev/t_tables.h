// INDEPENDENT oracle tables, typed from the MS pages (not derived from state_model.py): D3DRENDERSTATETYPE / D3DSAMPLERSTATETYPE / D3DTEXTURESTAGESTATETYPE defaults and
// "Saving Pixel/Vertex States With a StateBlock". blk: P = in the MS pixel list, V = in the MS vertex list (FOGENABLE / NORMALIZENORMALS: neither MS list names them, Wine's
// vertex list does). ZENABLE default depends on the creation parameters (MS: D3DZB_TRUE iff EnableAutoDepthStencil), marked 0xDEAD here.
#pragma once
#include "t_common.h"
static inline uint32_t FB(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }
struct TRs { uint32_t k; uint32_t def; const char* blk; };
#define R(n, d, b) {D3DRS_##n, (uint32_t)(d), b}
static const TRs T_RS[] = {
    R(ZENABLE, 0xDEAD, "P"), R(FILLMODE, 3, "P"), R(SHADEMODE, 2, "P"), R(ZWRITEENABLE, 1, "P"), R(ALPHATESTENABLE, 0, "P"), R(LASTPIXEL, 1, "P"), R(SRCBLEND, 2, "P"), R(DESTBLEND, 1, "P"),
    R(CULLMODE, 3, "V"), R(ZFUNC, 4, "P"), R(ALPHAREF, 0, "P"), R(ALPHAFUNC, 8, "P"), R(DITHERENABLE, 0, "P"), R(ALPHABLENDENABLE, 0, "P"), R(FOGENABLE, 0, "V"), R(SPECULARENABLE, 0, "P"),
    R(FOGCOLOR, 0, "V"), R(FOGTABLEMODE, 0, "V"), R(FOGSTART, FB(0.0f), "PV"), R(FOGEND, FB(1.0f), "PV"), R(FOGDENSITY, FB(1.0f), "PV"), R(RANGEFOGENABLE, 0, "V"),
    R(STENCILENABLE, 0, "P"), R(STENCILFAIL, 1, "P"), R(STENCILZFAIL, 1, "P"), R(STENCILPASS, 1, "P"), R(STENCILFUNC, 8, "P"), R(STENCILREF, 0, "P"), R(STENCILMASK, 0xFFFFFFFF, "P"),
    R(STENCILWRITEMASK, 0xFFFFFFFF, "P"), R(TEXTUREFACTOR, 0xFFFFFFFF, "P"),
    R(WRAP0, 0, "P"), R(WRAP1, 0, "P"), R(WRAP2, 0, "P"), R(WRAP3, 0, "P"), R(WRAP4, 0, "P"), R(WRAP5, 0, "P"), R(WRAP6, 0, "P"), R(WRAP7, 0, "P"),
    R(WRAP8, 0, "P"), R(WRAP9, 0, "P"), R(WRAP10, 0, "P"), R(WRAP11, 0, "P"), R(WRAP12, 0, "P"), R(WRAP13, 0, "P"), R(WRAP14, 0, "P"), R(WRAP15, 0, "P"),
    R(CLIPPING, 1, "V"), R(LIGHTING, 1, "V"), R(AMBIENT, 0, "V"), R(FOGVERTEXMODE, 0, "V"), R(COLORVERTEX, 1, "V"), R(LOCALVIEWER, 1, "PV"), R(NORMALIZENORMALS, 0, "V"),
    R(DIFFUSEMATERIALSOURCE, 1, "PV"), R(SPECULARMATERIALSOURCE, 2, "PV"), R(AMBIENTMATERIALSOURCE, 0, "PV"), R(EMISSIVEMATERIALSOURCE, 0, "PV"),
    R(VERTEXBLEND, 0, "V"), R(CLIPPLANEENABLE, 0, "V"), R(POINTSIZE, FB(1.0f), "V"), R(POINTSIZE_MIN, FB(1.0f), "V"), R(POINTSPRITEENABLE, 0, "V"), R(POINTSCALEENABLE, 0, "V"),
    R(POINTSCALE_A, FB(1.0f), "V"), R(POINTSCALE_B, 0, "V"), R(POINTSCALE_C, 0, "V"), R(MULTISAMPLEANTIALIAS, 1, "V"), R(MULTISAMPLEMASK, 0xFFFFFFFF, "V"), R(PATCHEDGESTYLE, 0, "V"),
    R(DEBUGMONITORTOKEN, 0, ""), R(POINTSIZE_MAX, FB(1.0f), "V"), R(INDEXEDVERTEXBLENDENABLE, 0, "V"), R(COLORWRITEENABLE, 0xF, "P"), R(TWEENFACTOR, 0, "V"), R(BLENDOP, 1, "P"),
    R(POSITIONDEGREE, 3, "V"), R(NORMALDEGREE, 1, "V"), R(SCISSORTESTENABLE, 0, "P"), R(SLOPESCALEDEPTHBIAS, 0, "P"), R(ANTIALIASEDLINEENABLE, 0, "P"),
    R(MINTESSELLATIONLEVEL, FB(1.0f), "V"), R(MAXTESSELLATIONLEVEL, FB(1.0f), "V"), R(ADAPTIVETESS_X, 0, "V"), R(ADAPTIVETESS_Y, 0, "V"), R(ADAPTIVETESS_Z, FB(1.0f), "V"),
    R(ADAPTIVETESS_W, 0, "V"), R(ENABLEADAPTIVETESSELLATION, 0, "V"), R(TWOSIDEDSTENCILMODE, 0, "P"), R(CCW_STENCILFAIL, 1, "P"), R(CCW_STENCILZFAIL, 1, "P"), R(CCW_STENCILPASS, 1, "P"),
    R(CCW_STENCILFUNC, 8, "P"), R(COLORWRITEENABLE1, 0xF, "P"), R(COLORWRITEENABLE2, 0xF, "P"), R(COLORWRITEENABLE3, 0xF, "P"), R(BLENDFACTOR, 0xFFFFFFFF, "P"), R(SRGBWRITEENABLE, 0, "P"),
    R(DEPTHBIAS, 0, "P"), R(SEPARATEALPHABLENDENABLE, 0, "P"), R(SRCBLENDALPHA, 2, "P"), R(DESTBLENDALPHA, 1, "P"), R(BLENDOPALPHA, 1, "P"),
};
#undef R
enum { T_NRS = sizeof(T_RS) / sizeof(T_RS[0]) };
// samplers: types 1..13, all pixel except DMAPOFFSET (13, vertex); defaults per the MS D3DSAMPLERSTATETYPE page (DMAPOFFSET: sampler page 0, vertex-state page 256 -> README)
static const uint32_t T_SAMP_DEF[14] = {0, 1, 1, 1, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0};
// texture stage states (D3DTSS values 1-11, 22-24, 26-28 and CONSTANT 32): MS defaults, stage 0: COLOROP MODULATE(4), ALPHAOP SELECTARG1(2), others DISABLE(1); TEXCOORDINDEX = stage
static const uint32_t T_TSS[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 22, 23, 24, 26, 27, 28, 32};
static inline uint32_t t_tss_def(uint32_t st, uint32_t t) {
    switch (t) {
    case D3DTSS_COLOROP: return st ? 1 : 4;
    case D3DTSS_ALPHAOP: return st ? 1 : 2;
    case D3DTSS_COLORARG1: case D3DTSS_ALPHAARG1: return D3DTA_TEXTURE;
    case D3DTSS_COLORARG2: case D3DTSS_ALPHAARG2: case D3DTSS_COLORARG0: case D3DTSS_ALPHAARG0: case D3DTSS_RESULTARG: return D3DTA_CURRENT;
    case D3DTSS_TEXCOORDINDEX: return st;
    default: return 0;
    }
}
// the 21 sampler indices: 0-15, D3DDMAPSAMPLER, D3DVERTEXTEXTURESAMPLER0-3
static inline uint32_t t_samp_id(int i) { return i < 16 ? (uint32_t)i : 256u + (uint32_t)(i - 16); }
// membership oracle for the non-table state, by family and block type (0 ALL, 1 PIXEL, 2 VERTEX), from the MS pages
static inline bool t_rs_member(const TRs& r, int t) { return t == 0 || (t == 1 && strchr(r.blk, 'P')) || (t == 2 && strchr(r.blk, 'V')); }
static inline bool t_tss_member(uint32_t type, int t) {
    if (t == 0) return true;
    bool both = type == D3DTSS_TEXCOORDINDEX || type == D3DTSS_TEXTURETRANSFORMFLAGS;
    if (t == 2) return both;
    return type != D3DTSS_CONSTANT;  // pixel: every named type but CONSTANT (MS pixel table lists 17)
}
