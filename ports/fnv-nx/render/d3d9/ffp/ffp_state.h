/*
 * ffp_state.h - D3D9 fixed-function PIXEL pipeline state (rule RD5): the data that selects the behaviour of the one
 * program ffp.frag.glsl / nx_ffp_ps_eval(). Plain C99, std140-compatible (the struct IS the UBO contents).
 *
 * The block stores the D3D9 values RAW (masked/converted only where the D3D9 type demands it). The program is total
 * over every bit pattern of every word: ffp_norm_op / ffp_norm_arg / ffp_norm_textype (ffp_ps_ref.h) give each
 * out-of-range value one defined meaning, in C and in GLSL alike.
 *
 * The std140 layout is the struct: render/gl4shim/ffp/gen_glsl.py writes the GLSL block of NxFfpPsState from it (UBO NX_FFP_UBO_BINDING) and the offset of
 * every member as typedef-char assertions (ffp_layout_ps.gen.h, included by ffp_state.c), so the C and GLSL layouts cannot differ.
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

/* indices of NxFfpStage.op / carg / aarg and NxFfpPsState.spec */
enum { NX_FFP_OP_COLOROP = 0, NX_FFP_OP_ALPHAOP = 1, NX_FFP_OP_RESULTARG = 2, NX_FFP_OP_TEXTYPE = 3 };
enum { NX_FFP_CARG0 = 0, NX_FFP_CARG1 = 1, NX_FFP_CARG2 = 2, NX_FFP_CARG_TTFF = 3 };
enum { NX_FFP_AARG0 = 0, NX_FFP_AARG1 = 1, NX_FFP_AARG2 = 2, NX_FFP_AARG_PAD = 3 };
enum { NX_FFP_SPEC_ENABLE = 0 };

typedef struct NxFfpStage {
    uint32_t op[4];        /* D3DTSS_COLOROP, D3DTSS_ALPHAOP, D3DTSS_RESULTARG, bound texture type (NX_FFP_OP_*) */
    uint32_t carg[4];      /* D3DTSS_COLORARG0/1/2, D3DTSS_TEXTURETRANSFORMFLAGS (NX_FFP_CARG*) */
    uint32_t aarg[4];      /* D3DTSS_ALPHAARG0/1/2, pad (NX_FFP_AARG*) */
    float constant[4];     /* D3DTSS_CONSTANT (D3DCOLOR) as r,g,b,a in [0,1] */
    float bump[4];         /* D3DTSS_BUMPENVMAT00, 01, 10, 11 (float bit patterns) */
    float lum[4];          /* D3DTSS_BUMPENVLSCALE, BUMPENVLOFFSET, 0, 0 */
} NxFfpStage;

typedef struct NxFfpPsState {
    NxFfpStage stage[NX_FFP_STAGES];
    float tfactor[4];      /* D3DRS_TEXTUREFACTOR (D3DCOLOR) as r,g,b,a */
    uint32_t spec[4];      /* D3DRS_SPECULARENABLE (any non-zero value = TRUE), 0, 0, 0 (NX_FFP_SPEC_ENABLE) */
} NxFfpPsState;


/* Pure and total: tss[stage][D3DTSS_*] (8 x 33), rs[D3DRS_*] (>= NX_RS_COUNT entries; only TEXTUREFACTOR and
 * SPECULARENABLE are read), texType[stage] (NX_FFP_TEX_*; any other value is stored and means NONE). */
void nx_ffp_ps_state_from_d3d9(const uint32_t tss[8][33], const uint32_t *rs, const uint8_t texType[8],
                               NxFfpPsState *out);
#endif
