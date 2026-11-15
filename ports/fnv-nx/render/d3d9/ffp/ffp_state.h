/*
 * ffp_state.h - D3D9 fixed-function PIXEL pipeline state (rule RD5): the data that selects the behaviour of the one
 * program ffp.frag.glsl / nx_ffp_ps_eval(). Plain C99, std140-compatible (the struct IS the UBO contents).
 *
 * The block stores the D3D9 values RAW (masked/converted only where the D3D9 type demands it). The program is total
 * over every bit pattern of every word: ffp_norm_op / ffp_norm_arg / ffp_norm_textype (ffp_ps_ref.h) give each
 * out-of-range value one defined meaning, in C and in GLSL alike.
 *
 * std140 layout (offsets in bytes, all vec4-aligned; sizeof == 800):
 *   0      NxFfpStage stage[8]   (96 bytes each)
 *     +0   u32 colorOp, alphaOp, resultArg, texType   D3DTSS_COLOROP, ALPHAOP, RESULTARG, bound texture type
 *     +16  u32 cArg0, cArg1, cArg2, ttff              D3DTSS_COLORARG0/1/2, TEXTURETRANSFORMFLAGS
 *     +32  u32 aArg0, aArg1, aArg2, pad               D3DTSS_ALPHAARG0/1/2
 *     +48  f32 constant[4]   D3DTSS_CONSTANT (D3DCOLOR) as r,g,b,a in [0,1]
 *     +64  f32 bump[4]       D3DTSS_BUMPENVMAT00, 01, 10, 11 (float bit patterns)
 *     +80  f32 lum[4]        D3DTSS_BUMPENVLSCALE, BUMPENVLOFFSET, 0, 0
 *   768    f32 tfactor[4]    D3DRS_TEXTUREFACTOR (D3DCOLOR) as r,g,b,a
 *   784    u32 specularEnable, pad[3]   D3DRS_SPECULARENABLE (any non-zero value = TRUE)
 * GLSL: uniform block binding NX_FFP_UBO_BINDING (3; fnvnx_dk_layout.h uses 0..2).
 */
#ifndef NX_FFP_STATE_H
#define NX_FFP_STATE_H
#include <stdint.h>
#include <stddef.h>

#define NX_FFP_STAGES 8
#define NX_FFP_UBO_BINDING 3

enum { /* D3DTEXTUREOP (D3D9 values); NX_FFP_TOP_NOP is the normal form of every value outside 1..26 */
    NX_FFP_TOP_NOP = 0, NX_FFP_TOP_DISABLE = 1, NX_FFP_TOP_SELECTARG1, NX_FFP_TOP_SELECTARG2, NX_FFP_TOP_MODULATE,
    NX_FFP_TOP_MODULATE2X, NX_FFP_TOP_MODULATE4X, NX_FFP_TOP_ADD, NX_FFP_TOP_ADDSIGNED, NX_FFP_TOP_ADDSIGNED2X,
    NX_FFP_TOP_SUBTRACT, NX_FFP_TOP_ADDSMOOTH, NX_FFP_TOP_BLENDDIFFUSEALPHA, NX_FFP_TOP_BLENDTEXTUREALPHA,
    NX_FFP_TOP_BLENDFACTORALPHA, NX_FFP_TOP_BLENDTEXTUREALPHAPM, NX_FFP_TOP_BLENDCURRENTALPHA,
    NX_FFP_TOP_PREMODULATE, NX_FFP_TOP_MODULATEALPHA_ADDCOLOR, NX_FFP_TOP_MODULATECOLOR_ADDALPHA,
    NX_FFP_TOP_MODULATEINVALPHA_ADDCOLOR, NX_FFP_TOP_MODULATEINVCOLOR_ADDALPHA, NX_FFP_TOP_BUMPENVMAP,
    NX_FFP_TOP_BUMPENVMAPLUMINANCE, NX_FFP_TOP_DOTPRODUCT3, NX_FFP_TOP_MULTIPLYADD, NX_FFP_TOP_LERP
};
enum { /* D3DTA_* (low 4 bits = source, 0x10 COMPLEMENT, 0x20 ALPHAREPLICATE); source 7 = "zero" (normal form of 7..15) */
    NX_FFP_TA_DIFFUSE = 0, NX_FFP_TA_CURRENT, NX_FFP_TA_TEXTURE, NX_FFP_TA_TFACTOR, NX_FFP_TA_SPECULAR,
    NX_FFP_TA_TEMP, NX_FFP_TA_CONSTANT, NX_FFP_TA_ZERO,
    NX_FFP_TA_COMPLEMENT = 0x10, NX_FFP_TA_ALPHAREPLICATE = 0x20
};
enum { NX_FFP_TEX_NONE = 0, NX_FFP_TEX_2D = 1, NX_FFP_TEX_CUBE = 2, NX_FFP_TEX_VOLUME = 3 }; /* other values = NONE */
enum { /* D3D9 D3DTSS_* indices (tss[stage][i]) */
    NX_TSS_COLOROP = 1, NX_TSS_COLORARG1 = 2, NX_TSS_COLORARG2 = 3, NX_TSS_ALPHAOP = 4, NX_TSS_ALPHAARG1 = 5,
    NX_TSS_ALPHAARG2 = 6, NX_TSS_BUMPENVMAT00 = 7, NX_TSS_BUMPENVMAT01 = 8, NX_TSS_BUMPENVMAT10 = 9,
    NX_TSS_BUMPENVMAT11 = 10, NX_TSS_BUMPENVLSCALE = 22, NX_TSS_BUMPENVLOFFSET = 23, NX_TSS_TEXTURETRANSFORMFLAGS = 24,
    NX_TSS_COLORARG0 = 26, NX_TSS_ALPHAARG0 = 27, NX_TSS_RESULTARG = 28, NX_TSS_CONSTANT = 32, NX_TSS_COUNT = 33
};
enum { NX_RS_SPECULARENABLE = 29, NX_RS_TEXTUREFACTOR = 60, NX_RS_COUNT = 210 }; /* D3DRENDERSTATETYPE */

typedef struct NxFfpStage {
    uint32_t colorOp, alphaOp, resultArg, texType;
    uint32_t cArg0, cArg1, cArg2, ttff;
    uint32_t aArg0, aArg1, aArg2, pad;
    float constant[4];
    float bump[4];
    float lum[4];
} NxFfpStage;

typedef struct NxFfpPsState {
    NxFfpStage stage[NX_FFP_STAGES];
    float tfactor[4];
    uint32_t specularEnable, pad[3];
} NxFfpPsState;

#define NX_FFP_SA(n, c) typedef char nx_ffp_sa_##n[(c) ? 1 : -1]
NX_FFP_SA(stage_size, sizeof(NxFfpStage) == 96);
NX_FFP_SA(stage_carg, offsetof(NxFfpStage, cArg0) == 16 && offsetof(NxFfpStage, aArg0) == 32);
NX_FFP_SA(stage_const, offsetof(NxFfpStage, constant) == 48 && offsetof(NxFfpStage, bump) == 64);
NX_FFP_SA(stage_lum, offsetof(NxFfpStage, lum) == 80);
NX_FFP_SA(state_tf, offsetof(NxFfpPsState, tfactor) == 768 && offsetof(NxFfpPsState, specularEnable) == 784);
NX_FFP_SA(state_size, sizeof(NxFfpPsState) == 800);

/* Pure and total: tss[stage][D3DTSS_*] (8 x 33), rs[D3DRS_*] (>= NX_RS_COUNT entries; only TEXTUREFACTOR and
 * SPECULARENABLE are read), texType[stage] (NX_FFP_TEX_*; any other value is stored and means NONE). */
void nx_ffp_ps_state_from_d3d9(const uint32_t tss[8][33], const uint32_t *rs, const uint8_t texType[8],
                               NxFfpPsState *out);
#endif
