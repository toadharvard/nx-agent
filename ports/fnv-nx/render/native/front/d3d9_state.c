/* fnv-nx native renderer: D3D9 state -> native state translator. See d3d9_state.h for the rules. */
#include "d3d9_state.h"
#include "plat_mark.h"
#include <string.h>

/* D3D9 numeric constants (d3d9types.h). */
enum {
    RS_ZENABLE = 7, RS_FILLMODE = 8, RS_SHADEMODE = 9, RS_ZWRITEENABLE = 14, RS_ALPHATESTENABLE = 15,
    RS_LASTPIXEL = 16, RS_SRCBLEND = 19, RS_DESTBLEND = 20, RS_CULLMODE = 22, RS_ZFUNC = 23, RS_ALPHAREF = 24,
    RS_ALPHAFUNC = 25, RS_DITHERENABLE = 26, RS_ALPHABLENDENABLE = 27, RS_FOGENABLE = 28, RS_SPECULARENABLE = 29,
    RS_FOGCOLOR = 34, RS_FOGTABLEMODE = 35, RS_FOGSTART = 36, RS_FOGEND = 37, RS_FOGDENSITY = 38,
    RS_RANGEFOGENABLE = 48, RS_STENCILENABLE = 52, RS_STENCILFAIL = 53, RS_STENCILZFAIL = 54, RS_STENCILPASS = 55,
    RS_STENCILFUNC = 56, RS_STENCILREF = 57, RS_STENCILMASK = 58, RS_STENCILWRITEMASK = 59, RS_TEXTUREFACTOR = 60,
    RS_CLIPPING = 136, RS_LIGHTING = 137, RS_AMBIENT = 139, RS_FOGVERTEXMODE = 140, RS_COLORVERTEX = 141,
    RS_LOCALVIEWER = 142, RS_NORMALIZENORMALS = 143, RS_DIFFUSEMATERIALSOURCE = 145,
    RS_SPECULARMATERIALSOURCE = 146, RS_AMBIENTMATERIALSOURCE = 147, RS_EMISSIVEMATERIALSOURCE = 148,
    RS_VERTEXBLEND = 151, RS_CLIPPLANEENABLE = 152, RS_POINTSIZE = 154, RS_POINTSIZE_MIN = 155,
    RS_POINTSPRITEENABLE = 156, RS_POINTSCALEENABLE = 157, RS_POINTSCALE_A = 158, RS_POINTSCALE_B = 159,
    RS_POINTSCALE_C = 160, RS_MULTISAMPLEANTIALIAS = 161, RS_MULTISAMPLEMASK = 162, RS_PATCHEDGESTYLE = 163,
    RS_DEBUGMONITORTOKEN = 165, RS_POINTSIZE_MAX = 166, RS_INDEXEDVERTEXBLENDENABLE = 167,
    RS_COLORWRITEENABLE = 168, RS_TWEENFACTOR = 170, RS_BLENDOP = 171, RS_POSITIONDEGREE = 172,
    RS_NORMALDEGREE = 173, RS_SCISSORTESTENABLE = 174, RS_SLOPESCALEDEPTHBIAS = 175,
    RS_ANTIALIASEDLINEENABLE = 176, RS_MINTESSELLATIONLEVEL = 178, RS_MAXTESSELLATIONLEVEL = 179,
    RS_ADAPTIVETESS_X = 180, RS_ADAPTIVETESS_Y = 181, RS_ADAPTIVETESS_Z = 182, RS_ADAPTIVETESS_W = 183,
    RS_ENABLEADAPTIVETESSELLATION = 184, RS_TWOSIDEDSTENCILMODE = 185, RS_CCW_STENCILFAIL = 186,
    RS_CCW_STENCILZFAIL = 187, RS_CCW_STENCILPASS = 188, RS_CCW_STENCILFUNC = 189, RS_COLORWRITEENABLE1 = 190,
    RS_COLORWRITEENABLE2 = 191, RS_COLORWRITEENABLE3 = 192, RS_BLENDFACTOR = 193, RS_SRGBWRITEENABLE = 194,
    RS_DEPTHBIAS = 195, RS_SEPARATEALPHABLENDENABLE = 206, RS_SRCBLENDALPHA = 207, RS_DESTBLENDALPHA = 208,
    RS_BLENDOPALPHA = 209,
};
enum {
    SAMP_ADDRESSU = 1, SAMP_ADDRESSV = 2, SAMP_ADDRESSW = 3, SAMP_BORDERCOLOR = 4, SAMP_MAGFILTER = 5,
    SAMP_MINFILTER = 6, SAMP_MIPFILTER = 7, SAMP_MIPMAPLODBIAS = 8, SAMP_MAXMIPLEVEL = 9, SAMP_MAXANISOTROPY = 10,
    SAMP_SRGBTEXTURE = 11,
};
enum { BLEND_BOTHSRCALPHA = 12, BLEND_BOTHINVSRCALPHA = 13 };
enum { TEXF_NONE = 0, TEXF_POINT = 1, TEXF_ANISOTROPIC = 3 };

static float asfloat(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

/* ---- enum tables (Nine nine_pipe.h) ---------------------------------------------------------------------------- */
/* a value outside D3D9's range is marked unsupported (plat_mark.h); 0, the zero-initialised state, is not */
static uint32_t cmp(uint32_t d) {                                                             /* NEVER_ZERO=0 too */
    if (d >= 1 && d <= 8) return d - 1;
    if (d) NX_PLAT_UNSUPPORTED("D3DCMPFUNC", d);
    return NxCmp_Never;
}
static uint32_t sop(uint32_t d) {
    if (d >= 1 && d <= 8) return d - 1;
    if (d) NX_PLAT_UNSUPPORTED("D3DSTENCILOP", d);
    return NxSop_Zero;
}
static uint32_t bop(uint32_t d) {
    if (d >= 1 && d <= 5) return d - 1;
    if (d) NX_PLAT_UNSUPPORTED("D3DBLENDOP", d);
    return NxBop_Add;
}

static uint32_t bf_common(uint32_t b) {
    switch (b) {
    case 1: return NxBf_Zero;          case 2: return NxBf_One;
    case 3: return NxBf_SrcColor;      case 4: return NxBf_InvSrcColor;
    case 5: return NxBf_SrcAlpha;      case 6: return NxBf_InvSrcAlpha;
    case 7: return NxBf_DstAlpha;      case 8: return NxBf_InvDstAlpha;
    case 9: return NxBf_DstColor;      case 10: return NxBf_InvDstColor;
    case 11: return NxBf_SrcAlphaSat;  case 12: return NxBf_SrcAlpha;      /* BOTHSRCALPHA */
    case 13: return NxBf_InvSrcAlpha;  /* BOTHINVSRCALPHA */
    case 14: return NxBf_ConstColor;   case 15: return NxBf_InvConstColor;
    default: if (b) NX_PLAT_UNSUPPORTED("D3DBLEND", b); return NxBf_Zero;
    }
}
static uint32_t bf_color(uint32_t b) {
    if (b == 16) return NxBf_Src1Color;
    if (b == 17) return NxBf_InvSrc1Color;
    return bf_common(b);
}
static uint32_t bf_alpha(uint32_t b) {
    if (b == 16) return NxBf_One;     /* Nine: SRCCOLOR2 -> ONE (as Nine does) */
    if (b == 17) return NxBf_Zero;    /* INVSRCCOLOR2 -> ZERO */
    return bf_common(b);
}

/* ---- blend --------------------------------------------------------------------------------------------------- */
uint64_t nx_d3d9_blend_word(const uint32_t* rs) {
    uint64_t w = 0;
    if (rs[RS_ALPHABLENDENABLE]) {
        uint32_t op = bop(rs[RS_BLENDOP]);
        uint32_t src = bf_color(rs[RS_SRCBLEND]), dst = bf_color(rs[RS_DESTBLEND]);
        uint32_t opa, srca, dsta;
        int sep = rs[RS_SEPARATEALPHABLENDENABLE] != 0;
        if (sep) {
            opa = bop(rs[RS_BLENDOPALPHA]);
            srca = bf_alpha(rs[RS_SRCBLENDALPHA]);
            dsta = bf_alpha(rs[RS_DESTBLENDALPHA]);
        } else {
            opa = op;
            srca = bf_alpha(rs[RS_SRCBLEND]);
            dsta = bf_alpha(rs[RS_DESTBLEND]);
        }
        /* nine_convert_blend_state_fixup */
        if (rs[RS_SRCBLEND] == BLEND_BOTHSRCALPHA || rs[RS_SRCBLEND] == BLEND_BOTHINVSRCALPHA) {
            dst = rs[RS_SRCBLEND] == BLEND_BOTHSRCALPHA ? NxBf_InvSrcAlpha : NxBf_SrcAlpha;
            if (!sep) dsta = dst;
        } else if (sep && (rs[RS_SRCBLENDALPHA] == BLEND_BOTHSRCALPHA ||
                           rs[RS_SRCBLENDALPHA] == BLEND_BOTHINVSRCALPHA)) {
            dsta = rs[RS_SRCBLENDALPHA] == BLEND_BOTHSRCALPHA ? NxBf_InvSrcAlpha : NxBf_SrcAlpha;
        }
        w |= 1ull << NXRS_B_ENABLE;
        w |= (uint64_t)src << NXRS_B_SRC | (uint64_t)dst << NXRS_B_DST | (uint64_t)op << NXRS_B_OP;
        w |= (uint64_t)srca << NXRS_B_SRCA | (uint64_t)dsta << NXRS_B_DSTA | (uint64_t)opa << NXRS_B_OPA;
    }
    w |= (uint64_t)(rs[RS_COLORWRITEENABLE] & 15) << NXRS_B_MASK(0);
    w |= (uint64_t)(rs[RS_COLORWRITEENABLE1] & 15) << NXRS_B_MASK(1);
    w |= (uint64_t)(rs[RS_COLORWRITEENABLE2] & 15) << NXRS_B_MASK(2);
    w |= (uint64_t)(rs[RS_COLORWRITEENABLE3] & 15) << NXRS_B_MASK(3);
    return w;
}

/* ---- depth / stencil ---------------------------------------------------------------------------------------- */
uint64_t nx_d3d9_depth_stencil_word(const uint32_t* rs) {
    uint64_t w = 0;
    if (rs[RS_ZENABLE]) {
        uint32_t f = cmp(rs[RS_ZFUNC]);
        int write = rs[RS_ZWRITEENABLE] && f != NxCmp_Equal && f != NxCmp_Never;
        w |= 1ull << NXRS_D_TEST | (uint64_t)write << NXRS_D_WRITE | (uint64_t)f << NXRS_D_FUNC;
    }
    if (rs[RS_STENCILENABLE]) {
        uint64_t front = (uint64_t)cmp(rs[RS_STENCILFUNC]) | (uint64_t)sop(rs[RS_STENCILFAIL]) << 3 |
                         (uint64_t)sop(rs[RS_STENCILZFAIL]) << 6 | (uint64_t)sop(rs[RS_STENCILPASS]) << 9;
        uint64_t back = front;
        if (rs[RS_TWOSIDEDSTENCILMODE]) {
            back = (uint64_t)cmp(rs[RS_CCW_STENCILFUNC]) | (uint64_t)sop(rs[RS_CCW_STENCILFAIL]) << 3 |
                   (uint64_t)sop(rs[RS_CCW_STENCILZFAIL]) << 6 | (uint64_t)sop(rs[RS_CCW_STENCILPASS]) << 9;
            w |= 1ull << NXRS_S_TWOSIDED;
        }
        w |= 1ull << NXRS_S_ENABLE | front << NXRS_S_FUNC | back << NXRS_S_BFUNC;
        w |= (uint64_t)(rs[RS_STENCILMASK] & 0xFF) << NXRS_S_READMASK;
        w |= (uint64_t)(rs[RS_STENCILWRITEMASK] & 0xFF) << NXRS_S_WRITEMASK;
        w |= (uint64_t)(rs[RS_STENCILREF] & 0xFF) << NXRS_S_REF;
    }
    return w;
}

/* ---- rasterizer ----------------------------------------------------------------------------------------------- */
uint64_t nx_d3d9_raster_word(const uint32_t* rs) {
    uint32_t cull = rs[RS_CULLMODE] == 2 ? NxCull_CW : rs[RS_CULLMODE] == 3 ? NxCull_CCW : NxCull_None;
    uint32_t fill = rs[RS_FILLMODE] == 1 ? NxFill_Point : rs[RS_FILLMODE] == 2 ? NxFill_Wireframe : NxFill_Solid;
    uint64_t w = (uint64_t)cull << NXRS_R_CULL | (uint64_t)fill << NXRS_R_FILL;
    if (rs[RS_DEPTHBIAS] | rs[RS_SLOPESCALEDEPTHBIAS]) w |= 1ull << NXRS_R_BIAS;
    if (rs[RS_SCISSORTESTENABLE]) w |= 1ull << NXRS_R_SCISSOR;
    if (rs[RS_SRGBWRITEENABLE]) w |= 1ull << NXRS_R_SRGB;
    if (rs[RS_SHADEMODE] == 1) w |= 1ull << NXRS_R_FLAT;
    w |= (uint64_t)(rs[RS_CLIPPLANEENABLE] & 0xFF) << NXRS_R_CLIP;
    if (rs[RS_POINTSPRITEENABLE]) w |= 1ull << NXRS_R_POINTSPRITE;
    return w;
}

/* ---- shader bits -------------------------------------------------------------------------------------------- */
uint32_t nx_d3d9_shader_bits(const uint32_t* rs, uint32_t ps_version) {
    uint32_t b = 0;
    if (rs[RS_ALPHATESTENABLE]) b |= 1u << NXSB_ALPHATEST | cmp(rs[RS_ALPHAFUNC]) << NXSB_ALPHAFUNC;
    if (rs[RS_FOGENABLE] && (ps_version & 0xFFFF) < 0x0300) {
        uint32_t m;
        switch (rs[RS_FOGTABLEMODE]) {
        case 1: m = NxFog_Exp; break;
        case 2: m = NxFog_Exp2; break;
        case 3: m = NxFog_Linear; break;
        default: if (rs[RS_FOGTABLEMODE]) NX_PLAT_UNSUPPORTED("D3DRS_FOGTABLEMODE", rs[RS_FOGTABLEMODE]); m = NxFog_Vertex; break;
        }
        b |= m << NXSB_FOG;
    }
    return b;
}

/* ---- dynamic values ------------------------------------------------------------------------------------------ */
void nx_d3d9_color(uint32_t c, float rgba[4]) {
    rgba[0] = (float)((c >> 16) & 0xFF) / 0xFF;
    rgba[1] = (float)((c >> 8) & 0xFF) / 0xFF;
    rgba[2] = (float)(c & 0xFF) / 0xFF;
    rgba[3] = (float)((c >> 24) & 0xFF) / 0xFF;
}

float nx_d3d9_bias_scale(uint32_t f) {
    switch (f) {
    case 80: case 70: return 65536.0f;                    /* D16, D16_LOCKABLE */
    case 73: return 32768.0f;                             /* D15S1 */
    case 75: case 77: case 79: return 16777216.0f;        /* D24S8, D24X8, D24X4S4 */
    case 71: return 4294967296.0f;                        /* D32 */
    case NXD3D_FOURCC('I', 'N', 'T', 'Z'): case NXD3D_FOURCC('D', 'F', '2', '4'): return 16777216.0f;
    case NXD3D_FOURCC('D', 'F', '1', '6'): return 65536.0f;
    default: return 8388608.0f;                           /* float depth (82 D32F_LOCKABLE, 83 D24FS8), none */
    }
}

void nx_d3d9_viewport(const NxD3D9Viewport* vp, NxViewport* o) {
    o->x = (float)vp->X + 0.5f;
    o->y = (float)vp->Y + 0.5f;
    o->w = (float)vp->Width;
    o->h = (float)vp->Height;
    o->minZ = vp->MinZ;
    o->maxZ = vp->MaxZ;
}

void nx_d3d9_scissor(const NxD3D9Rect* r, NxScissor* o) {
    o->x = r->left;
    o->y = r->top;
    o->w = r->right > r->left ? r->right - r->left : 0;
    o->h = r->bottom > r->top ? r->bottom - r->top : 0;
}

#define NX_CLAMP(X, MIN, MAX) ((X) > (MIN) ? ((X) > (MAX) ? (MAX) : (X)) : (MIN))   /* Mesa's CLAMP */

void nx_d3d9_translate(const NxD3D9Input* in, NxRenderState* st, NxDynamicState* dyn, uint32_t* sb) {
    const uint32_t* rs = in->rs;
    if (st) {
        st->w[0] = nx_d3d9_blend_word(rs);
        st->w[1] = nx_d3d9_depth_stencil_word(rs);
        st->w[2] = nx_d3d9_raster_word(rs);
        st->w[3] = 0;
    }
    if (sb) *sb = nx_d3d9_shader_bits(rs, in->ps_version);
    if (!dyn) return;
    memset(dyn, 0, sizeof *dyn);
    nx_d3d9_color(rs[RS_BLENDFACTOR], dyn->blendConstant);
    dyn->depthBiasD3D = asfloat(rs[RS_DEPTHBIAS]);
    dyn->depthBiasUnits = dyn->depthBiasD3D * nx_d3d9_bias_scale(in->depth_format);
    dyn->depthBiasSlope = asfloat(rs[RS_SLOPESCALEDEPTHBIAS]);
    dyn->alphaRef = (float)rs[RS_ALPHAREF] / 255.0f;
    nx_d3d9_color(rs[RS_FOGCOLOR], dyn->fogColor);
    dyn->fogEnd = asfloat(rs[RS_FOGEND]);
    dyn->fogScale = 1.0f / (asfloat(rs[RS_FOGEND]) - asfloat(rs[RS_FOGSTART]));
    dyn->fogDensity = asfloat(rs[RS_FOGDENSITY]);
    dyn->pointSize = NX_CLAMP(asfloat(rs[RS_POINTSIZE]), asfloat(rs[RS_POINTSIZE_MIN]), asfloat(rs[RS_POINTSIZE_MAX]));
    dyn->stencilRef = rs[RS_STENCILREF] & 0xFF;
    dyn->stencilReadMask = rs[RS_STENCILMASK] & 0xFF;
    dyn->stencilWriteMask = rs[RS_STENCILWRITEMASK] & 0xFF;
    nx_d3d9_viewport(&in->viewport, &dyn->viewport);
    nx_d3d9_scissor(&in->scissor, &dyn->scissor);
    if (in->clip_planes) memcpy(dyn->clipPlane, in->clip_planes, sizeof dyn->clipPlane);
}

/* ---- samplers ------------------------------------------------------------------------------------------------- */
static uint32_t wrap(uint32_t a) {
    switch (a) {
    case 1: return NxWrap_Repeat;
    case 2: return NxWrap_Mirror;
    case 3: return NxWrap_Clamp;
    case 4: return NxWrap_Border;
    case 5: return NxWrap_MirrorOnce;
    default: if (a) NX_PLAT_UNSUPPORTED("D3DTEXTUREADDRESS", a); return NxWrap_Clamp;
    }
}

void nx_d3d9_sampler(const uint32_t* s, const NxD3D9Texture* tex, NxSamplerState* o) {
    static const NxD3D9Texture none = {0, 0, 0, 0, 0};
    if (!tex) tex = &none;
    memset(o, 0, sizeof *o);
    uint32_t f = 0;
    if (s[SAMP_MIPFILTER] != TEXF_NONE) {
        o->lodBias = asfloat(s[SAMP_MIPMAPLODBIAS]);
        int lod = (int)s[SAMP_MAXMIPLEVEL] - (int)tex->managed_lod;
        if (lod < 0) lod = 0;
        if (lod > 15) lod = 15;
        o->minLod = (uint8_t)lod;
        f |= (s[SAMP_MIPFILTER] == TEXF_POINT ? NxMip_Nearest : NxMip_Linear) << NXSMP_MIP_SHIFT;
    }
    o->maxLod = 15;
    if (tex->cube) {
        o->wrap = NxWrap_Clamp | NxWrap_Clamp << 3 | NxWrap_Clamp << 6;
    } else {
        o->wrap = (uint16_t)(wrap(s[SAMP_ADDRESSU]) | wrap(s[SAMP_ADDRESSV]) << 3 | wrap(s[SAMP_ADDRESSW]) << 6);
    }
    if (!(s[SAMP_MINFILTER] == TEXF_POINT && !tex->shadow)) f |= NXSMP_MIN_LINEAR;
    if (!(s[SAMP_MAGFILTER] == TEXF_POINT && !tex->shadow)) f |= NXSMP_MAG_LINEAR;
    if (s[SAMP_MINFILTER] == TEXF_ANISOTROPIC || s[SAMP_MAGFILTER] == TEXF_ANISOTROPIC) {
        uint32_t a = s[SAMP_MAXANISOTROPY];
        o->maxAniso = (uint8_t)(a > 255 ? 255 : a);
    }
    if (tex->shadow) f |= NXSMP_SHADOW;
    if (s[SAMP_SRGBTEXTURE] && (nx_d3d9_format(tex->format).flags & NXFMT_SRGB)) f |= NXSMP_SRGB;
    o->filter = (uint8_t)f;
    uint32_t c = s[SAMP_BORDERCOLOR];   /* ARGB -> RGBA8 */
    o->border = ((c >> 16) & 0xFF) | (c & 0xFF00) | (c & 0xFF) << 16 | (c & 0xFF000000u);
}

/* ---- formats ---------------------------------------------------------------------------------------------------- */
#define S_(r, g, b, a) {NxSwz_##r, NxSwz_##g, NxSwz_##b, NxSwz_##a}
#define F_(fmt, sw, flags, bytes) do { NxFormatInfo r_ = {fmt, sw, flags, bytes, bytes}; return r_; } while (0)
#define FC_(fmt, sw, flags, bytes, src) do { NxFormatInfo r_ = {fmt, sw, (uint8_t)((flags) | NXFMT_CONVERT), bytes, src}; \
                                             return r_; } while (0)

NxFormatInfo nx_d3d9_format(uint32_t d) {
    const uint8_t C = NXFMT_COMPRESSED, Z = NXFMT_DEPTH, ZS = NXFMT_DEPTH | NXFMT_STENCIL, SR = NXFMT_SRGB,
                  RT = NXFMT_RENDER;
    switch (d) {
    case 21: F_(NxFmt_BGRA8, S_(R, G, B, A), SR | RT, 4);           /* A8R8G8B8 */
    case 22: F_(NxFmt_BGRX8, S_(R, G, B, 1), SR | RT, 4);           /* X8R8G8B8 */
    case 32: F_(NxFmt_RGBA8, S_(R, G, B, A), SR | RT, 4);           /* A8B8G8R8 */
    case 33: F_(NxFmt_RGBX8, S_(R, G, B, 1), SR | RT, 4);           /* X8B8G8R8 */
    case 23: F_(NxFmt_R5G6B5, S_(R, G, B, 1), RT, 2);               /* R5G6B5 */
    case 24: F_(NxFmt_X1R5G5B5, S_(R, G, B, 1), RT, 2);             /* X1R5G5B5 */
    case 25: F_(NxFmt_A1R5G5B5, S_(R, G, B, A), RT, 2);             /* A1R5G5B5 */
    case 26: F_(NxFmt_A4R4G4B4, S_(R, G, B, A), 0, 2);              /* A4R4G4B4 */
    case 30: F_(NxFmt_A4R4G4B4, S_(R, G, B, 1), 0, 2);              /* X4R4G4B4 */
    case 31: F_(NxFmt_RGB10A2, S_(R, G, B, A), RT, 4);              /* A2B10G10R10 */
    case 35: FC_(NxFmt_RGB10A2, S_(R, G, B, A), 0, 4, 4);           /* A2R10G10B10: R and B swapped on upload */
    case 34: F_(NxFmt_RG16, S_(R, G, 1, 1), RT, 4);                 /* G16R16 */
    case 36: F_(NxFmt_RGBA16, S_(R, G, B, A), RT, 8);               /* A16B16G16R16 */
    case 28: FC_(NxFmt_RGBA8, S_(R, G, B, A), 0, 4, 1);             /* A8 -> (0, 0, 0, A) */
    case 50: FC_(NxFmt_RGBA8, S_(R, G, B, 1), SR, 4, 1);            /* L8 -> (L, L, L, 1) */
    case 51: FC_(NxFmt_RGBA8, S_(R, G, B, A), SR, 4, 2);            /* A8L8 -> (L, L, L, A) */
    case 81: FC_(NxFmt_RGBA16, S_(R, G, B, 1), 0, 8, 2);            /* L16 -> (L, L, L, 1) */
    case 60: F_(NxFmt_RG8S, S_(R, G, 1, 1), 0, 2);                  /* V8U8 */
    case 63: F_(NxFmt_RGBA8S, S_(R, G, B, A), 0, 4);                /* Q8W8V8U8 */
    case 64: F_(NxFmt_RG16S, S_(R, G, 1, 1), 0, 4);                 /* V16U16 */
    case 110: F_(NxFmt_RGBA16S, S_(R, G, B, A), 0, 8);              /* Q16W16V16U16 */
    case 111: F_(NxFmt_R16F, S_(R, 1, 1, 1), RT, 2);                /* R16F */
    case 112: F_(NxFmt_RG16F, S_(R, G, 1, 1), RT, 4);               /* G16R16F */
    case 113: F_(NxFmt_RGBA16F, S_(R, G, B, A), RT, 8);             /* A16B16G16R16F */
    case 114: F_(NxFmt_R32F, S_(R, 1, 1, 1), RT, 4);                /* R32F */
    case 115: F_(NxFmt_RG32F, S_(R, G, 1, 1), RT, 8);               /* G32R32F */
    case 116: F_(NxFmt_RGBA32F, S_(R, G, B, A), RT, 16);            /* A32B32G32R32F */
    case NXD3D_FOURCC('D', 'X', 'T', '1'): F_(NxFmt_BC1, S_(R, G, B, A), C | SR, 8);
    case NXD3D_FOURCC('D', 'X', 'T', '2'):
    case NXD3D_FOURCC('D', 'X', 'T', '3'): F_(NxFmt_BC2, S_(R, G, B, A), C | SR, 16);
    case NXD3D_FOURCC('D', 'X', 'T', '4'):
    case NXD3D_FOURCC('D', 'X', 'T', '5'): F_(NxFmt_BC3, S_(R, G, B, A), C | SR, 16);
    case NXD3D_FOURCC('A', 'T', 'I', '1'): FC_(NxFmt_RGBA8, S_(R, G, B, A), 0, 4, 8);   /* decoded -> (R, 0, 0, 1) */
    case NXD3D_FOURCC('A', 'T', 'I', '2'): F_(NxFmt_BC5, S_(G, R, 1, 1), C, 16);  /* basetexture9.c: Y,X,1,1 */
    case 70: case 80: F_(NxFmt_Z16, S_(R, R, R, R), Z, 2);          /* D16_LOCKABLE, D16 */
    case 75: F_(NxFmt_Z24S8, S_(R, R, R, R), ZS, 4);                /* D24S8 */
    case 77: F_(NxFmt_Z24X8, S_(R, R, R, R), Z, 4);                 /* D24X8 */
    case 82: F_(NxFmt_Z32F, S_(R, R, R, R), Z, 4);                  /* D32F_LOCKABLE */
    case 83: F_(NxFmt_Z32FS8, S_(R, R, R, R), ZS, 8);               /* D24FS8 */
    case NXD3D_FOURCC('I', 'N', 'T', 'Z'): F_(NxFmt_Z24S8, S_(R, R, R, R), ZS, 4);
    case NXD3D_FOURCC('D', 'F', '2', '4'): F_(NxFmt_Z24X8, S_(R, 0, 0, 1), Z, 4);
    case NXD3D_FOURCC('D', 'F', '1', '6'): F_(NxFmt_Z16, S_(R, 0, 0, 1), Z, 2);
    default: if (d) NX_PLAT_UNSUPPORTED("D3DFORMAT", d); F_(NxFmt_None, S_(R, G, B, A), 0, 0);
    }
}

/* ---- defaults ----------------------------------------------------------------------------------------------------- */
void nx_d3d9_default_rs(uint32_t rs[NXD3D_RS_COUNT]) {
    memset(rs, 0, NXD3D_RS_COUNT * sizeof *rs);
    rs[RS_ZENABLE] = 0;             /* Nine: D3DZB_FALSE (no auto depth stencil) */
    rs[RS_FILLMODE] = 3;            rs[RS_SHADEMODE] = 2;           rs[RS_ZWRITEENABLE] = 1;
    rs[RS_LASTPIXEL] = 1;           rs[RS_SRCBLEND] = 2;            rs[RS_DESTBLEND] = 1;
    rs[RS_CULLMODE] = 3;            rs[RS_ZFUNC] = 4;               rs[RS_ALPHAFUNC] = 8;
    rs[RS_FOGEND] = 0x3F800000;     rs[RS_FOGDENSITY] = 0x3F800000;
    rs[RS_STENCILFAIL] = 1;         rs[RS_STENCILZFAIL] = 1;        rs[RS_STENCILPASS] = 1;
    rs[RS_STENCILMASK] = 0xFFFFFFFF; rs[RS_STENCILFUNC] = 8;        rs[RS_STENCILWRITEMASK] = 0xFFFFFFFF;
    rs[RS_TEXTUREFACTOR] = 0xFFFFFFFF;
    rs[RS_CLIPPING] = 1;            rs[RS_LIGHTING] = 1;            rs[RS_COLORVERTEX] = 1;
    rs[RS_LOCALVIEWER] = 1;         rs[RS_DIFFUSEMATERIALSOURCE] = 1; rs[RS_SPECULARMATERIALSOURCE] = 2;
    rs[RS_POINTSIZE] = 0x3F800000;  rs[RS_POINTSIZE_MIN] = 0x3F800000; rs[RS_POINTSCALE_A] = 0x3F800000;
    rs[RS_MULTISAMPLEANTIALIAS] = 1; rs[RS_MULTISAMPLEMASK] = 0xFFFFFFFF;
    rs[RS_DEBUGMONITORTOKEN] = 0xDEADCAFE; rs[RS_POINTSIZE_MAX] = 0x3F800000;
    rs[RS_COLORWRITEENABLE] = 0xF;  rs[RS_BLENDOP] = 1;             rs[RS_POSITIONDEGREE] = 3;
    rs[RS_NORMALDEGREE] = 1;        rs[RS_MINTESSELLATIONLEVEL] = 0x3F800000; rs[RS_MAXTESSELLATIONLEVEL] = 0x3F800000;
    rs[RS_ADAPTIVETESS_Z] = 0x3F800000;
    rs[RS_CCW_STENCILFAIL] = 1;     rs[RS_CCW_STENCILZFAIL] = 1;    rs[RS_CCW_STENCILPASS] = 1;
    rs[RS_CCW_STENCILFUNC] = 8;
    rs[RS_COLORWRITEENABLE1] = 0xF; rs[RS_COLORWRITEENABLE2] = 0xF; rs[RS_COLORWRITEENABLE3] = 0xF;
    rs[RS_BLENDFACTOR] = 0xFFFFFFFF;
    rs[RS_SRCBLENDALPHA] = 2;       rs[RS_DESTBLENDALPHA] = 1;      rs[RS_BLENDOPALPHA] = 1;
}

void nx_d3d9_default_samp(uint32_t s[NXD3D_SAMP_COUNT]) {
    memset(s, 0, NXD3D_SAMP_COUNT * sizeof *s);
    s[SAMP_ADDRESSU] = 1; s[SAMP_ADDRESSV] = 1; s[SAMP_ADDRESSW] = 1;
    s[SAMP_MAGFILTER] = 1; s[SAMP_MINFILTER] = 1; s[SAMP_MIPFILTER] = 0; s[SAMP_MAXANISOTROPY] = 1;
}

/* ---- texel conversion for NXFMT_CONVERT formats ---------------------------------------------------------------- */
static uint32_t mip_pixels(uint32_t w, uint32_t h, uint32_t mips, int blocks) {
    uint32_t n = 0;
    for (uint32_t m = 0; m < mips; m++) {
        n += blocks ? ((w + 3) / 4) * ((h + 3) / 4) : w * h;
        w = w > 1 ? w / 2 : 1;
        h = h > 1 ? h / 2 : 1;
    }
    return n;
}

uint32_t nx_d3d9_converted_size(uint32_t d3dfmt, uint32_t w, uint32_t h, uint32_t mips, uint32_t faces) {
    NxFormatInfo f = nx_d3d9_format(d3dfmt);
    if (f.format == NxFmt_None || !w || !h) return 0;
    if (!mips) mips = 1;
    if (!faces) faces = 1;
    if (d3dfmt == NXD3D_FOURCC('A', 'T', 'I', '1')) {   /* blocks in, pixels out (block-padded mips stay exact) */
        uint32_t n = 0;
        for (uint32_t m = 0, mw = w, mh = h; m < mips; m++, mw = mw > 1 ? mw / 2 : 1, mh = mh > 1 ? mh / 2 : 1) n += mw * mh;
        return n * 4 * faces;
    }
    int blocks = (f.flags & NXFMT_COMPRESSED) != 0;
    return mip_pixels(w, h, mips, blocks) * f.bytes * faces;
}

/* BC4 unorm block, decoded as Mesa's util_format_rgtc (the softpipe oracle): integer division, truncating. */
static void bc4_block(const uint8_t* b, uint8_t out[16]) {
    uint8_t e[8];
    e[0] = b[0]; e[1] = b[1];
    if (e[0] > e[1]) for (int c = 2; c < 8; c++) e[c] = (uint8_t)(((8 - c) * e[0] + (c - 1) * e[1]) / 7);
    else { for (int c = 2; c < 6; c++) e[c] = (uint8_t)(((6 - c) * e[0] + (c - 1) * e[1]) / 5); e[6] = 0; e[7] = 255; }
    uint64_t bits = 0;
    for (int i = 0; i < 6; i++) bits |= (uint64_t)b[2 + i] << (8 * i);
    for (int t = 0; t < 16; t++) out[t] = e[(bits >> (3 * t)) & 7];
}

uint32_t nx_d3d9_convert(uint32_t d3dfmt, uint32_t w, uint32_t h, uint32_t mips, uint32_t faces, const void* src,
                         uint32_t srcSize, void* dst, uint32_t dstCap) {
    NxFormatInfo f = nx_d3d9_format(d3dfmt);
    if (!(f.flags & NXFMT_CONVERT) || !src || !dst) return 0;
    uint32_t need = nx_d3d9_converted_size(d3dfmt, w, h, mips, faces);
    if (!need || need > dstCap) return 0;
    if (!mips) mips = 1;
    if (!faces) faces = 1;
    const uint8_t* s = (const uint8_t*)src;
    uint8_t* d = (uint8_t*)dst;
    if (d3dfmt == NXD3D_FOURCC('A', 'T', 'I', '1')) {
        uint32_t in = mip_pixels(w, h, mips, 1) * 8 * faces;
        if (srcSize < in) return 0;
        for (uint32_t face = 0; face < faces; face++)
            for (uint32_t m = 0, mw = w, mh = h; m < mips; m++, mw = mw > 1 ? mw / 2 : 1, mh = mh > 1 ? mh / 2 : 1) {
                uint32_t bw = (mw + 3) / 4, bh = (mh + 3) / 4;
                for (uint32_t by = 0; by < bh; by++)
                    for (uint32_t bx = 0; bx < bw; bx++, s += 8) {
                        uint8_t t[16];
                        bc4_block(s, t);
                        for (uint32_t y = 0; y < 4; y++)
                            for (uint32_t x = 0; x < 4; x++) {
                                uint32_t px = bx * 4 + x, py = by * 4 + y;
                                if (px >= mw || py >= mh) continue;
                                uint8_t* o = d + (py * mw + px) * 4;
                                o[0] = t[y * 4 + x]; o[1] = 0; o[2] = 0; o[3] = 255;
                            }
                    }
                d += mw * mh * 4;
            }
        return need;
    }
    uint32_t n = mip_pixels(w, h, mips, 0) * faces;
    if (srcSize < n * f.srcBytes) return 0;
    for (uint32_t i = 0; i < n; i++) {
        switch (d3dfmt) {
        case 28: d[0] = 0; d[1] = 0; d[2] = 0; d[3] = s[0]; s += 1; d += 4; break;                  /* A8 */
        case 50: d[0] = d[1] = d[2] = s[0]; d[3] = 255; s += 1; d += 4; break;                       /* L8 */
        case 51: d[0] = d[1] = d[2] = s[0]; d[3] = s[1]; s += 2; d += 4; break;                      /* A8L8 */
        case 81:                                                                                      /* L16 */
            d[0] = d[2] = d[4] = s[0]; d[1] = d[3] = d[5] = s[1]; d[6] = d[7] = 255; s += 2; d += 8; break;
        case 35: {                                                                                    /* A2R10G10B10 */
            uint32_t v = (uint32_t)s[0] | (uint32_t)s[1] << 8 | (uint32_t)s[2] << 16 | (uint32_t)s[3] << 24;
            v = (v & 0xC00FFC00u) | (v >> 20 & 0x3FF) | (v & 0x3FF) << 20;
            d[0] = (uint8_t)v; d[1] = (uint8_t)(v >> 8); d[2] = (uint8_t)(v >> 16); d[3] = (uint8_t)(v >> 24);
            s += 4; d += 4;
            break;
        }
        default: NX_PLAT_UNSUPPORTED("D3DFORMAT conversion", d3dfmt); return 0;
        }
    }
    return need;
}
