/* fnv-nx native renderer: backend-neutral render-state encoding (stream S6n).
 *
 * Pure C99, no dependencies, identical layout in ILP32 and LP64. Used by
 *   - the D3D9 state translator (render/d3d9/front/d3d9_state.h), which produces these values from D3D9 render/sampler states;
 *   - the front end (nx/front.h), which carries NxRenderState in DefineState ops;
 *   - both backends (host/nxgpu.c on deko3d, gl/nxgl.c on desktop GL 4.5), which consume them.
 *
 * Conventions every backend implements (and the conformance scenes check):
 *   - Window space is y-down with the origin at the top-left pixel; image row 0 is the top row (D3D9).
 *     Clip-space +y maps to the top of the viewport. Depth range is [0,1] (half-z).
 *   - "CW" / "CCW" mean the winding of a triangle as seen on screen in that y-down window space.
 *   - Blend factors and ops have the OpenGL/deko3d meaning; a colour factor used in an alpha slot means its alpha
 *     component (e.g. NxBf_ConstColor in an alpha slot uses constant.a), as in GL.
 *   - Colour write masks: bit 0 R, bit 1 G, bit 2 B, bit 3 A (the D3DCOLORWRITEENABLE bit order).
 */
#ifndef FNVNX_NX_STATE_H
#define FNVNX_NX_STATE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- enums ---------------------------------------------------------------------------------------------------- */
enum NxCompare {       /* = D3DCMP_* - 1 = GL_NEVER.. order */
    NxCmp_Never = 0, NxCmp_Less = 1, NxCmp_Equal = 2, NxCmp_LEqual = 3, NxCmp_Greater = 4, NxCmp_NotEqual = 5,
    NxCmp_GEqual = 6, NxCmp_Always = 7
};
enum NxStencilOp {     /* = D3DSTENCILOP_* - 1 */
    NxSop_Keep = 0, NxSop_Zero = 1, NxSop_Replace = 2, NxSop_IncrSat = 3, NxSop_DecrSat = 4, NxSop_Invert = 5,
    NxSop_IncrWrap = 6, NxSop_DecrWrap = 7
};
enum NxBlendFactor {   /* 5 bits */
    NxBf_Zero = 0, NxBf_One = 1, NxBf_SrcColor = 2, NxBf_InvSrcColor = 3, NxBf_SrcAlpha = 4, NxBf_InvSrcAlpha = 5,
    NxBf_DstAlpha = 6, NxBf_InvDstAlpha = 7, NxBf_DstColor = 8, NxBf_InvDstColor = 9, NxBf_SrcAlphaSat = 10,
    NxBf_ConstColor = 11, NxBf_InvConstColor = 12, NxBf_Src1Color = 13, NxBf_InvSrc1Color = 14,
    NxBf_Count = 15
};
enum NxBlendOp {       /* = D3DBLENDOP_* - 1; 3 bits */
    NxBop_Add = 0, NxBop_Subtract = 1, NxBop_RevSubtract = 2, NxBop_Min = 3, NxBop_Max = 4
};
enum NxCull {          /* on-screen winding in y-down window space */
    NxCull_None = 0, NxCull_CW = 1, NxCull_CCW = 2
};
enum NxFill { NxFill_Solid = 0, NxFill_Wireframe = 1, NxFill_Point = 2 };

/* ---- NxRenderState: four 64-bit words -------------------------------------------------------------------------- */
/* w[0] blend + colour write (one blend equation shared by all render targets, four write masks)
 *   bit  0      blend enable (when 0, all factor/op fields below are 0)
 *   bits 1-5    src colour factor (NxBlendFactor)
 *   bits 6-10   dst colour factor
 *   bits 11-13  colour op (NxBlendOp)
 *   bits 14-18  src alpha factor
 *   bits 19-23  dst alpha factor
 *   bits 24-26  alpha op
 *   bits 32-35  RT0 write mask, 36-39 RT1, 40-43 RT2, 44-47 RT3
 * w[1] depth-stencil
 *   bit  0      depth test enable           (0: bits 1-4 are 0)
 *   bit  1      depth write enable
 *   bits 2-4    depth func (NxCompare)
 *   bit  5      stencil enable              (0: bits 6-55 are 0)
 *   bit  6      two-sided stencil requested (informational: the back fields are always valid)
 *   bits 8-10   front func (NxCompare), 11-13 front fail op, 14-16 front depth-fail op, 17-19 front pass op
 *   bits 20-22  back func,              23-25 back fail op,  26-28 back depth-fail op,  29-31 back pass op
 *               ("front" = CW on screen; back = copy of front when two-sided is off)
 *   bits 32-39  stencil compare (read) mask, 40-47 stencil write mask, 48-55 stencil reference
 *               (both faces share them). Backends apply 32-55 as dynamic state.
 * w[2] rasterizer
 *   bits 0-1    cull (NxCull)
 *   bits 2-3    fill (NxFill)
 *   bit  4      depth bias enable (D3DRS_DEPTHBIAS or SLOPESCALEDEPTHBIAS has a nonzero bit pattern)
 *   bit  5      scissor test enable
 *   bit  6      sRGB write (the colour target is written through its sRGB view; blending in linear space)
 *   bit  7      flat shading (provoking vertex = first vertex, always)
 *   bits 8-15   user clip plane enable mask (bit i = plane i, clip-space planes)
 *   bit  16     point sprite enable (point coordinate origin upper-left)
 * w[3] reserved, 0.
 */
typedef struct NxRenderState { uint64_t w[4]; } NxRenderState;

#define NXRS_B_ENABLE      0
#define NXRS_B_SRC         1
#define NXRS_B_DST         6
#define NXRS_B_OP          11
#define NXRS_B_SRCA        14
#define NXRS_B_DSTA        19
#define NXRS_B_OPA         24
#define NXRS_B_MASK(rt)    (32 + 4 * (rt))

#define NXRS_D_TEST        0
#define NXRS_D_WRITE       1
#define NXRS_D_FUNC        2
#define NXRS_S_ENABLE      5
#define NXRS_S_TWOSIDED    6
#define NXRS_S_FUNC        8
#define NXRS_S_FAIL        11
#define NXRS_S_ZFAIL       14
#define NXRS_S_PASS        17
#define NXRS_S_BFUNC       20
#define NXRS_S_BFAIL       23
#define NXRS_S_BZFAIL      26
#define NXRS_S_BPASS       29
#define NXRS_S_READMASK    32
#define NXRS_S_WRITEMASK   40
#define NXRS_S_REF         48

#define NXRS_R_CULL        0
#define NXRS_R_FILL        2
#define NXRS_R_BIAS        4
#define NXRS_R_SCISSOR     5
#define NXRS_R_SRGB        6
#define NXRS_R_FLAT        7
#define NXRS_R_CLIP        8
#define NXRS_R_POINTSPRITE 16

#define NXRS_GET(word, shift, bits) ((uint32_t)(((word) >> (shift)) & ((1ull << (bits)) - 1)))

/* ---- dynamic values -------------------------------------------------------------------------------------------- */
typedef struct NxViewport {    /* window space as above; a rasterizer with pixel centres at +0.5 */
    float x, y, w, h;          /* ndc (-1,+1) maps to (x, y), ndc (+1,-1) to (x + w, y + h) */
    float minZ, maxZ;
} NxViewport;
typedef struct NxScissor { int32_t x, y, w, h; } NxScissor;   /* pixels, top-left origin; w/h >= 0 */

typedef struct NxDynamicState {
    float blendConstant[4];    /* RGBA in [0,1] (D3DRS_BLENDFACTOR, ARGB -> RGBA, c/255) */
    float depthBiasUnits;      /* constant bias in the backend's r units (GL glPolygonOffset 'units') */
    float depthBiasSlope;      /* slope factor (GL 'factor') */
    float depthBiasD3D;        /* D3DRS_DEPTHBIAS as given (depth-buffer units), for unscaled backends */
    float alphaRef;            /* D3DRS_ALPHAREF / 255 (not clamped) */
    float fogColor[4];         /* RGBA from D3DRS_FOGCOLOR */
    float fogEnd;              /* D3DRS_FOGEND */
    float fogScale;            /* 1 / (FOGEND - FOGSTART)  (linear) */
    float fogDensity;          /* D3DRS_FOGDENSITY        (exp, exp2) */
    float pointSize;           /* clamp(POINTSIZE, POINTSIZE_MIN, POINTSIZE_MAX) */
    uint32_t stencilRef, stencilReadMask, stencilWriteMask;   /* 8-bit values (= w[1] bits 32-55) */
    uint32_t pad;
    NxViewport viewport;
    NxScissor scissor;         /* the D3D scissor rect (used when w[2] scissor bit is set) */
    float clipPlane[6][4];     /* clip-space planes (copied from the input; 0 when none given) */
} NxDynamicState;

/* ---- shader bits: what the shader epilogue implements (D3D9 alpha test and fog) ------------------------------- */
/*   bits 0-2   alpha func (NxCompare)                  bit 3  alpha test enable (0: bits 0-2 = 0)
 *   bits 4-6   fog mode (NxFog)                         */
enum NxFog { NxFog_None = 0, NxFog_Linear = 1, NxFog_Exp = 2, NxFog_Exp2 = 3, NxFog_Vertex = 4 };
#define NXSB_ALPHAFUNC  0
#define NXSB_ALPHATEST  3
#define NXSB_FOG        4

/* ---- samplers (16 bytes) ---------------------------------------------------------------------------------------- */
enum NxWrap { NxWrap_Repeat = 0, NxWrap_Mirror = 1, NxWrap_Clamp = 2, NxWrap_Border = 3, NxWrap_MirrorOnce = 4 };
enum NxMipFilter { NxMip_None = 0, NxMip_Nearest = 1, NxMip_Linear = 2 };
#define NXSMP_MIN_LINEAR  0x01   /* filter bits */
#define NXSMP_MAG_LINEAR  0x02
#define NXSMP_MIP_SHIFT   2      /* 2 bits NxMipFilter */
#define NXSMP_SRGB        0x10   /* sample through the sRGB view (when the format has one) */
#define NXSMP_SHADOW      0x20   /* depth compare, func LEQUAL */
typedef struct NxSamplerState {
    uint8_t filter;            /* NXSMP_* bits */
    uint8_t maxAniso;          /* 0 = off, else D3DSAMP_MAXANISOTROPY (not clamped) */
    uint16_t wrap;             /* bits 0-2 U, 3-5 V, 6-8 W (NxWrap) */
    uint8_t minLod, maxLod;    /* integer LOD clamp (mip levels) */
    uint8_t pad[2];
    float lodBias;             /* D3DSAMP_MIPMAPLODBIAS as float (0 when mip filter is none) */
    uint32_t border;           /* RGBA8: R in bits 0-7, G 8-15, B 16-23, A 24-31 (float = byte / 255) */
} NxSamplerState;

/* ---- texture formats -------------------------------------------------------------------------------------------- */
/* Memory layouts; names list components from the least significant bit / lowest byte unless said otherwise. */
enum NxFormat {
    NxFmt_None = 0,
    NxFmt_RGBA8 = 1,           /* bytes R,G,B,A (D3DFMT_A8B8G8R8) */
    NxFmt_BGRA8 = 2,           /* bytes B,G,R,A (D3DFMT_A8R8G8B8) */
    NxFmt_BC1 = 3, NxFmt_BC2 = 4, NxFmt_BC3 = 5,
    NxFmt_R8 = 6, NxFmt_RG8 = 7,
    NxFmt_R5G6B5 = 8,          /* u16: B 0-4, G 5-10, R 11-15 (D3DFMT_R5G6B5) */
    NxFmt_RGBA16F = 9, NxFmt_R32F = 10,
    NxFmt_Z24S8 = 11,          /* depth 24 unorm + stencil 8 */
    NxFmt_Z32F = 12,
    NxFmt_BGRX8 = 13,          /* bytes B,G,R,x */
    NxFmt_RGBX8 = 14,          /* bytes R,G,B,x */
    NxFmt_A1R5G5B5 = 15,       /* u16: B 0-4, G 5-9, R 10-14, A 15 */
    NxFmt_X1R5G5B5 = 16,
    NxFmt_A4R4G4B4 = 17,       /* u16: B 0-3, G 4-7, R 8-11, A 12-15 */
    NxFmt_RGB10A2 = 18,        /* u32: R 0-9, G 10-19, B 20-29, A 30-31 (D3DFMT_A2B10G10R10) */
    NxFmt_RG16 = 19, NxFmt_RGBA16 = 20, NxFmt_R16 = 21,
    NxFmt_RG8S = 22, NxFmt_RGBA8S = 23, NxFmt_RG16S = 24, NxFmt_RGBA16S = 25,
    NxFmt_R16F = 26, NxFmt_RG16F = 27, NxFmt_RG32F = 28, NxFmt_RGBA32F = 29,
    NxFmt_BC4 = 30, NxFmt_BC5 = 31,
    NxFmt_Z16 = 32, NxFmt_Z24X8 = 33, NxFmt_Z32FS8 = 34,
    NxFmt_BGR10A2 = 35,        /* u32: B 0-9, G 10-19, R 20-29, A 30-31 (D3DFMT_A2R10G10B10; GL_RGB10_A2 / GL_BGRA / 2_10_10_10_REV) */
    NxFmt_Count
};
enum NxSwizzle { NxSwz_R = 0, NxSwz_G = 1, NxSwz_B = 2, NxSwz_A = 3, NxSwz_0 = 4, NxSwz_1 = 5 };
#define NXFMT_COMPRESSED 0x01  /* 4x4 blocks */
#define NXFMT_DEPTH      0x02
#define NXFMT_STENCIL    0x04
#define NXFMT_SRGB       0x08  /* an sRGB view exists on both backends */
#define NXFMT_RENDER     0x10  /* usable as a colour render target */
#define NXFMT_NULL       0x40  /* NxFormatInfo.format is NxFmt_None AND the D3D format is a target without storage (D3DFMT_NULL): a pass
                                  simply has no attachment for it; it is not an unsupported format */
#define NXFMT_CONVERT    0x20  /* the D3D texel data must go through nx_d3d9_convert() before the upload: the format
                                  is stored expanded so that no view swizzle has to move or invent a channel (a view
                                  swizzle also applies to the BORDER colour; Nine's formats have no such swizzle) */
typedef struct NxFormatInfo {
    uint8_t format;            /* NxFormat, NxFmt_None when unsupported */
    uint8_t swizzle[4];        /* NxSwizzle for the sampled r,g,b,a, applied to the format's own channels */
    uint8_t flags;             /* NXFMT_* */
    uint8_t bytes;             /* native bytes per pixel, or per 4x4 block when compressed */
    uint8_t srcBytes;          /* D3D bytes per pixel (per 4x4 block when compressed) of the source data */
} NxFormatInfo;

/* The D3D9 pixel epilogue's uniform block (shaders/d3d9_epilogue.glsl, fragment UBO slot 2, std140, 48 bytes). */
typedef struct NxEpilogue {
    float fogColor[4];
    float fogEnd, fogScale, fogDensity, alphaRef;
    uint32_t alphaFunc, fogMode, alphaTest, pad;
} NxEpilogue;
static inline void nx_epilogue_fill(NxEpilogue* e, const NxDynamicState* d, uint32_t shaderBits) {
    for (int i = 0; i < 4; i++) e->fogColor[i] = d->fogColor[i];
    e->fogEnd = d->fogEnd; e->fogScale = d->fogScale; e->fogDensity = d->fogDensity; e->alphaRef = d->alphaRef;
    e->alphaFunc = (shaderBits >> NXSB_ALPHAFUNC) & 7;
    e->fogMode = (shaderBits >> NXSB_FOG) & 7;
    e->alphaTest = (shaderBits >> NXSB_ALPHATEST) & 1;
    e->pad = 0;
}

/* Native formats with an sRGB view on both backends (deko3d *_sRGB formats, GL sRGB texture views). */
static inline int nx_format_has_srgb(uint32_t f) {
    return f == NxFmt_RGBA8 || f == NxFmt_BGRA8 || f == NxFmt_BGRX8 || f == NxFmt_RGBX8 || f == NxFmt_BC1 ||
           f == NxFmt_BC2 || f == NxFmt_BC3;
}

#ifdef __cplusplus
}
#define NX_STATE_ASSERT_ static_assert
#else
#define NX_STATE_ASSERT_ _Static_assert
#endif
NX_STATE_ASSERT_(sizeof(NxRenderState) == 32, "NxRenderState");
NX_STATE_ASSERT_(sizeof(NxSamplerState) == 16, "NxSamplerState");
NX_STATE_ASSERT_(sizeof(NxFormatInfo) == 8, "NxFormatInfo");
NX_STATE_ASSERT_(sizeof(NxEpilogue) == 48, "NxEpilogue");
NX_STATE_ASSERT_(sizeof(NxDynamicState) == 216, "NxDynamicState");

#endif
