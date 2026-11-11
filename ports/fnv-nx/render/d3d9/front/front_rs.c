/* fnv-nx L6 front: blend / depth / stencil / raster state words. Rules and the out-of-range results: d3d9_state.h.
 * Every enumeration is a switch over its generated enum type without a default, built with -Werror=switch-enum; the
 * test for a value outside the enumeration comes before the switch (nxd3d_in_<enum>). */
#include "front_int.h"

/* ---- D3DCMPFUNC -> NxCompare ---------------------------------------------------------------------------------- */
uint32_t nxfront_cmp(uint32_t d, uint32_t oor) {
    uint32_t r = oor;
    if (!nxd3d_in_D3DCMPFUNC(d)) return r;
    switch ((NxD3DCMPFUNC)d) {
    case NX_D3DCMP_NEVER: r = NxCmp_Never; break;
    case NX_D3DCMP_LESS: r = NxCmp_Less; break;
    case NX_D3DCMP_EQUAL: r = NxCmp_Equal; break;
    case NX_D3DCMP_LESSEQUAL: r = NxCmp_LEqual; break;
    case NX_D3DCMP_GREATER: r = NxCmp_Greater; break;
    case NX_D3DCMP_NOTEQUAL: r = NxCmp_NotEqual; break;
    case NX_D3DCMP_GREATEREQUAL: r = NxCmp_GEqual; break;
    case NX_D3DCMP_ALWAYS: r = NxCmp_Always; break;
    case NX_D3DCMP_FORCE_DWORD: break;
    }
    return r;
}

/* wined3d: out of range stencil func -> GL_ALWAYS (state_stencil) */
static uint32_t stencil_func(uint32_t d) { return nxfront_cmp(d, NxCmp_Always); }

/* D3DSTENCILOP -> NxStencilOp; out of range -> KEEP (gl_stencil_op) */
static uint32_t stencil_op(uint32_t d) {
    uint32_t r = NxSop_Keep;
    if (!nxd3d_in_D3DSTENCILOP(d)) return r;
    switch ((NxD3DSTENCILOP)d) {
    case NX_D3DSTENCILOP_KEEP: r = NxSop_Keep; break;
    case NX_D3DSTENCILOP_ZERO: r = NxSop_Zero; break;
    case NX_D3DSTENCILOP_REPLACE: r = NxSop_Replace; break;
    case NX_D3DSTENCILOP_INCRSAT: r = NxSop_IncrSat; break;
    case NX_D3DSTENCILOP_DECRSAT: r = NxSop_DecrSat; break;
    case NX_D3DSTENCILOP_INVERT: r = NxSop_Invert; break;
    case NX_D3DSTENCILOP_INCR: r = NxSop_IncrWrap; break;
    case NX_D3DSTENCILOP_DECR: r = NxSop_DecrWrap; break;
    case NX_D3DSTENCILOP_FORCE_DWORD: break;
    }
    return r;
}

/* D3DBLENDOP -> NxBlendOp; out of range -> ADD (gl_blend_op) */
static uint32_t blend_op(uint32_t d) {
    uint32_t r = NxBop_Add;
    if (!nxd3d_in_D3DBLENDOP(d)) return r;
    switch ((NxD3DBLENDOP)d) {
    case NX_D3DBLENDOP_ADD: r = NxBop_Add; break;
    case NX_D3DBLENDOP_SUBTRACT: r = NxBop_Subtract; break;
    case NX_D3DBLENDOP_REVSUBTRACT: r = NxBop_RevSubtract; break;
    case NX_D3DBLENDOP_MIN: r = NxBop_Min; break;
    case NX_D3DBLENDOP_MAX: r = NxBop_Max; break;
    case NX_D3DBLENDOP_FORCE_DWORD: break;
    }
    return r;
}

/* D3DBLEND -> NxBlendFactor for the colour slots (alpha = 0) or the alpha slots (alpha = 1); out of range -> ZERO
 * (gl_blend_factor returns GL_NONE, which is 0 = GL_ZERO). SRCCOLOR2 / INVSRCCOLOR2 in an alpha slot: One / Zero, as Nine. */
static uint32_t blend_factor(uint32_t d, int alpha) {
    uint32_t r = NxBf_Zero;
    if (!nxd3d_in_D3DBLEND(d)) return r;
    switch ((NxD3DBLEND)d) {
    case NX_D3DBLEND_ZERO: r = NxBf_Zero; break;
    case NX_D3DBLEND_ONE: r = NxBf_One; break;
    case NX_D3DBLEND_SRCCOLOR: r = NxBf_SrcColor; break;
    case NX_D3DBLEND_INVSRCCOLOR: r = NxBf_InvSrcColor; break;
    case NX_D3DBLEND_SRCALPHA: r = NxBf_SrcAlpha; break;
    case NX_D3DBLEND_INVSRCALPHA: r = NxBf_InvSrcAlpha; break;
    case NX_D3DBLEND_DESTALPHA: r = NxBf_DstAlpha; break;
    case NX_D3DBLEND_INVDESTALPHA: r = NxBf_InvDstAlpha; break;
    case NX_D3DBLEND_DESTCOLOR: r = NxBf_DstColor; break;
    case NX_D3DBLEND_INVDESTCOLOR: r = NxBf_InvDstColor; break;
    case NX_D3DBLEND_SRCALPHASAT: r = NxBf_SrcAlphaSat; break;
    case NX_D3DBLEND_BOTHSRCALPHA: r = NxBf_SrcAlpha; break;
    case NX_D3DBLEND_BOTHINVSRCALPHA: r = NxBf_InvSrcAlpha; break;
    case NX_D3DBLEND_BLENDFACTOR: r = NxBf_ConstColor; break;
    case NX_D3DBLEND_INVBLENDFACTOR: r = NxBf_InvConstColor; break;
    case NX_D3DBLEND_SRCCOLOR2: r = alpha ? NxBf_One : NxBf_Src1Color; break;
    case NX_D3DBLEND_INVSRCCOLOR2: r = alpha ? NxBf_Zero : NxBf_InvSrc1Color; break;
    case NX_D3DBLEND_FORCE_DWORD: break;
    }
    return r;
}

/* D3DZBUFFERTYPE -> depth test on/off; out of range -> off (stateblock.c: desc.depth stays FALSE) */
static int depth_test(uint32_t d) {
    int r = 0;
    if (!nxd3d_in_D3DZBUFFERTYPE(d)) return r;
    switch ((NxD3DZBUFFERTYPE)d) {
    case NX_D3DZB_FALSE: r = 0; break;
    case NX_D3DZB_TRUE: r = 1; break;
    case NX_D3DZB_USEW: r = 1; break;
    case NX_D3DZB_FORCE_DWORD: break;
    }
    return r;
}

/* D3DCULL / D3DFILLMODE / D3DSHADEMODE: out of range -> oor = the value the GL state already has (cullmode, fillmode and
 * state_shademode make no GL call). */
static uint32_t cull_mode(uint32_t d, uint32_t oor) {
    uint32_t r = oor;
    if (!nxd3d_in_D3DCULL(d)) return r;
    switch ((NxD3DCULL)d) {
    case NX_D3DCULL_NONE: r = NxCull_None; break;
    case NX_D3DCULL_CW: r = NxCull_CW; break;
    case NX_D3DCULL_CCW: r = NxCull_CCW; break;
    case NX_D3DCULL_FORCE_DWORD: break;
    }
    return r;
}

static uint32_t fill_mode(uint32_t d, uint32_t oor) {
    uint32_t r = oor;
    if (!nxd3d_in_D3DFILLMODE(d)) return r;
    switch ((NxD3DFILLMODE)d) {
    case NX_D3DFILL_POINT: r = NxFill_Point; break;
    case NX_D3DFILL_WIREFRAME: r = NxFill_Wireframe; break;
    case NX_D3DFILL_SOLID: r = NxFill_Solid; break;
    case NX_D3DFILL_FORCE_DWORD: break;
    }
    return r;
}

static uint32_t flat_shade(uint32_t d, uint32_t oor) {
    uint32_t r = oor;
    if (!nxd3d_in_D3DSHADEMODE(d)) return r;
    switch ((NxD3DSHADEMODE)d) {
    case NX_D3DSHADE_FLAT: r = 1; break;
    case NX_D3DSHADE_GOURAUD: r = 0; break;
    case NX_D3DSHADE_PHONG: r = 0; break;
    case NX_D3DSHADE_FORCE_DWORD: break;
    }
    return r;
}

/* ---- blend ---------------------------------------------------------------------------------------------------- */
uint64_t nx_d3d9_blend_word(const uint32_t* rs) {
    uint64_t w = 0;
    if (NXF_RS(rs, ALPHABLENDENABLE)) {
        uint32_t src_d3d = NXF_RS(rs, SRCBLEND), sa_d3d = NXF_RS(rs, SRCBLENDALPHA);
        uint32_t op = blend_op(NXF_RS(rs, BLENDOP));
        uint32_t src = blend_factor(src_d3d, 0), dst = blend_factor(NXF_RS(rs, DESTBLEND), 0);
        uint32_t opa, srca, dsta;
        int sep = NXF_RS(rs, SEPARATEALPHABLENDENABLE) != 0;
        if (sep) {
            opa = blend_op(NXF_RS(rs, BLENDOPALPHA));
            srca = blend_factor(sa_d3d, 1);
            dsta = blend_factor(NXF_RS(rs, DESTBLENDALPHA), 1);
        } else {
            opa = op;
            srca = blend_factor(src_d3d, 1);
            dsta = blend_factor(NXF_RS(rs, DESTBLEND), 1);
        }
        /* nine_convert_blend_state_fixup */
        if (src_d3d == NX_D3DBLEND_BOTHSRCALPHA || src_d3d == NX_D3DBLEND_BOTHINVSRCALPHA) {
            dst = src_d3d == NX_D3DBLEND_BOTHSRCALPHA ? NxBf_InvSrcAlpha : NxBf_SrcAlpha;
            if (!sep) dsta = dst;
        } else if (sep && (sa_d3d == NX_D3DBLEND_BOTHSRCALPHA || sa_d3d == NX_D3DBLEND_BOTHINVSRCALPHA)) {
            dsta = sa_d3d == NX_D3DBLEND_BOTHSRCALPHA ? NxBf_InvSrcAlpha : NxBf_SrcAlpha;
        }
        w |= 1ull << NXRS_B_ENABLE;
        w |= (uint64_t)src << NXRS_B_SRC | (uint64_t)dst << NXRS_B_DST | (uint64_t)op << NXRS_B_OP;
        w |= (uint64_t)srca << NXRS_B_SRCA | (uint64_t)dsta << NXRS_B_DSTA | (uint64_t)opa << NXRS_B_OPA;
    }
    w |= (uint64_t)(NXF_RS(rs, COLORWRITEENABLE) & 15) << NXRS_B_MASK(0);
    w |= (uint64_t)(NXF_RS(rs, COLORWRITEENABLE1) & 15) << NXRS_B_MASK(1);
    w |= (uint64_t)(NXF_RS(rs, COLORWRITEENABLE2) & 15) << NXRS_B_MASK(2);
    w |= (uint64_t)(NXF_RS(rs, COLORWRITEENABLE3) & 15) << NXRS_B_MASK(3);
    return w;
}

/* ---- depth / stencil ------------------------------------------------------------------------------------------ */
static uint64_t stencil_face(uint32_t func, uint32_t fail, uint32_t zfail, uint32_t pass) {
    return (uint64_t)stencil_func(func) | (uint64_t)stencil_op(fail) << 3 | (uint64_t)stencil_op(zfail) << 6 |
           (uint64_t)stencil_op(pass) << 9;
}

uint64_t nx_d3d9_depth_stencil_word(const uint32_t* rs, NxD3D9Latch* latch) {
    uint64_t w = 0;
    uint32_t f = nxfront_cmp(NXF_RS(rs, ZFUNC), latch->depth_func);   /* out of range: GL keeps its depth func */
    latch->depth_func = (uint8_t)f;
    if (depth_test(NXF_RS(rs, ZENABLE))) {
        int write = NXF_RS(rs, ZWRITEENABLE) && f != NxCmp_Equal && f != NxCmp_Never;
        w |= 1ull << NXRS_D_TEST | (uint64_t)write << NXRS_D_WRITE | (uint64_t)f << NXRS_D_FUNC;
    }
    if (NXF_RS(rs, STENCILENABLE)) {
        uint64_t front = stencil_face(NXF_RS(rs, STENCILFUNC), NXF_RS(rs, STENCILFAIL), NXF_RS(rs, STENCILZFAIL),
                                      NXF_RS(rs, STENCILPASS));
        uint64_t back = front;
        if (NXF_RS(rs, TWOSIDEDSTENCILMODE)) {
            back = stencil_face(NXF_RS(rs, CCW_STENCILFUNC), NXF_RS(rs, CCW_STENCILFAIL), NXF_RS(rs, CCW_STENCILZFAIL),
                                NXF_RS(rs, CCW_STENCILPASS));
            w |= 1ull << NXRS_S_TWOSIDED;
        }
        w |= 1ull << NXRS_S_ENABLE | front << NXRS_S_FUNC | back << NXRS_S_BFUNC;
        w |= (uint64_t)(NXF_RS(rs, STENCILMASK) & 0xFF) << NXRS_S_READMASK;
        w |= (uint64_t)(NXF_RS(rs, STENCILWRITEMASK) & 0xFF) << NXRS_S_WRITEMASK;
        w |= (uint64_t)(NXF_RS(rs, STENCILREF) & 0xFF) << NXRS_S_REF;
    }
    return w;
}

/* ---- rasterizer ----------------------------------------------------------------------------------------------- */
uint64_t nx_d3d9_raster_word(const uint32_t* rs, NxD3D9Latch* latch) {
    latch->cull = (uint8_t)cull_mode(NXF_RS(rs, CULLMODE), latch->cull);
    latch->fill = (uint8_t)fill_mode(NXF_RS(rs, FILLMODE), latch->fill);
    latch->flat = (uint8_t)flat_shade(NXF_RS(rs, SHADEMODE), latch->flat);
    uint64_t w = (uint64_t)latch->cull << NXRS_R_CULL | (uint64_t)latch->fill << NXRS_R_FILL;
    if (NXF_RS(rs, DEPTHBIAS) | NXF_RS(rs, SLOPESCALEDEPTHBIAS)) w |= 1ull << NXRS_R_BIAS;
    if (NXF_RS(rs, SCISSORTESTENABLE)) w |= 1ull << NXRS_R_SCISSOR;
    if (NXF_RS(rs, SRGBWRITEENABLE)) w |= 1ull << NXRS_R_SRGB;
    if (latch->flat) w |= 1ull << NXRS_R_FLAT;
    // WineD3D 9.0 state.c state_clipping: the enabled planes are CLIPPLANEENABLE when CLIPPING is set, none otherwise
    if (NXF_RS(rs, CLIPPING)) w |= (uint64_t)(NXF_RS(rs, CLIPPLANEENABLE) & 0xFF) << NXRS_R_CLIP;
    if (NXF_RS(rs, POINTSPRITEENABLE)) w |= 1ull << NXRS_R_POINTSPRITE;
    return w;
}
