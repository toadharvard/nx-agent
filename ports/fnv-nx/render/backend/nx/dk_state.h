/* fnv-nx L6 deko3d backend: nxgpu v4.1 state words -> deko3d state structs. Host side (links deko3d.h, no device calls).
 *
 * Rule: every function is pure and total. Each ABI enum / bitfield value has a row (tables sized by _Static_assert on the Nx enum
 * counts; tests/l6/dkstate enumerates every value against the GL reference gl4_state.c / gl4_sampler.c). A value that the ABI
 * allows and deko3d cannot express is an error code, never a dropped or approximated field. On any error *out is all zero.
 * Error precedence (first that applies): NXDK_E_FIELD, NXDK_E_SAMPLES, NXDK_E_BLEND_ALPHA_DST / NXDK_E_RANGE.
 * The caller reports an error through nxchk_err (nxgpu4_check.h).
 *
 * Assumptions (stated, not proven here):
 *   A1 the device is created with the upper-left window origin, so DkFrontFace_CW is the on-screen CW of the y-down window space (state.h);
 *   A2 the blend unit applies a factor per channel with the GL meaning, so a colour factor in an alpha slot means its alpha component
 *      and SrcAlphaSat in an alpha slot is 1 (GL 4.5 section 17.3.8);
 *   A3 Min / Max ignore the factors (GL), so the dst-alpha factor of an alpha op Min / Max is irrelevant;
 *   A4 DkMsMode_1x on a multisampled target is GL_MULTISAMPLE off (gpu_3d_ms.cpp: MultisampleEnable = mode || rasterizerMode);
 *   A5 anisotropy is quantised by the hardware (tsc_generate.cpp anisoTable), which GL_EXT_texture_filter_anisotropic permits;
 *   A6 the sRGB border colour is encoded by deko3d (tsc_generate.cpp floatToSrgb8) so that the sampled border equals the linear value. */
#ifndef FNVNX_DK_STATE_H
#define FNVNX_DK_STATE_H
#include <stdint.h>
#include <deko3d.h>
#include "state.h"

#ifdef __cplusplus
extern "C" {
#endif

enum NxDkErr {
    NXDK_OK = 0,
    NXDK_E_FIELD = 1,           /* a field value outside its ABI enum, or a reserved bit set */
    NXDK_E_SAMPLES = 2,         /* the target sample count is not 1, 2, 4 or 8 */
    NXDK_E_BLEND_ALPHA_DST = 3, /* dkCmdBufBindBlendStates writes FuncAlphaDst from dstColorBlendFactor (gpu_3d_state.cpp:130), so a
                                   dst-alpha factor whose alpha meaning differs from the dst-colour factor's cannot be expressed */
    NXDK_E_RANGE = 4            /* sampler lod clamp > 15 (tsc_generate.cpp:66-67) or lodBias NaN / outside [-15, 15] (tsc_generate.cpp:87) */
};

/* Properties deko3d applies elsewhere; the caller applies each where noted. */
typedef struct NxDkDerived {
    uint8_t clipPlaneMask;      /* w[2] bits 8-15: deko3d fixes ClipDistanceEnable = 0xFF (gpu_3d_base.cpp:174), so the vertex epilogue / driver UBO applies the planes */
    uint8_t pointSprite;        /* w[2] bit 16: deko3d fixes point sprites on (gpu_3d_base.cpp:177): the fragment shader selects the point coordinate */
    uint8_t flatShade;          /* w[2] bit 7: shader interpolation qualifier (the provoking vertex is always First in the rasterizer state) */
    uint8_t srgbWrite;          /* w[2] bit 6: bind the sRGB DkImageView of the colour target (DkFmt.srgb) */
    uint8_t scissorEnable;      /* w[2] bit 5: dkCmdBufSetScissors with NxDynamicState.scissor, else with the whole target */
    DkMsMode msMode;            /* DkMultisampleState.mode (and rasterizerMode): the target's mode, or 1x when MULTISAMPLEANTIALIAS is off */
} NxDkDerived;

typedef struct NxDkState {
    DkRasterizerState rast;     /* dkCmdBufBindRasterizerState */
    DkColorState color;         /* dkCmdBufBindColorState (blend on RT 0-3, logic op Copy, alpha compare Always: alpha test is shader work) */
    DkColorWriteState write;    /* dkCmdBufBindColorWriteState (RT 4-7: all channels) */
    DkBlendState blend;         /* dkCmdBufBindBlendStates for ids 0-3 (one equation); Add / One / Zero when blending is off */
    DkDepthStencilState ds;     /* dkCmdBufBindDepthStencilState; Always / Keep when a test is off */
    NxDkDerived derived;
} NxDkState;

typedef struct NxDkDyn {        /* the dynamic commands */
    float blendConst[4];        /* dkCmdBufSetBlendConst */
    uint8_t stencilRef, stencilReadMask, stencilWriteMask;   /* dkCmdBufSetStencil(DkFace_FrontAndBack, writeMask, ref, readMask) */
    float biasConstant, biasClamp, biasSlope;                /* dkCmdBufSetDepthBias (deko3d doubles the constant like glPolygonOffset units * 2) */
    float pointSize, lineWidth; /* dkCmdBufSetPointSize, dkCmdBufSetLineWidth (1: D3D9 has no line width) */
    uint32_t sampleMask;        /* dkCmdBufSetSampleMask: the ABI mask & 0xFFFF (a target has <= 8 samples; 0xFFFF is deko3d's default), 0xFFFF when single-sample (GL: no effect) */
} NxDkDyn;

int nxdk_state(const NxRenderState* rs, uint32_t samples, NxDkState* out);
int nxdk_dyn(const NxRenderState* rs, const NxDynamicState* d, uint32_t samples, NxDkDyn* out);
/* srgbView: NXSMP_SRGB (a flag of the image view, DkFmt.srgb), not of DkSampler. */
int nxdk_sampler(const NxSamplerState* s, DkSampler* out, uint32_t* srgbView);

#ifdef __cplusplus
}
#endif
#endif
