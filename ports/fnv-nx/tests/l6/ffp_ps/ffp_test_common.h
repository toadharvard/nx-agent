/* Shared by ffp_driver.c and gl_driver.c: the general case record, the state builder, the deterministic texel function.
 * Record = 186 u32 words little endian: per stage (x8) 18 words
 *   colorOp alphaOp resultArg texType cArg0 cArg1 cArg2 ttff aArg0 aArg1 aArg2 constantD3DCOLOR m00 m01 m10 m11 lscale loffset
 * then tfactorD3DCOLOR specularEnable diffuse[4] specular[4] texcoord[8][4] (floats as bit patterns). */
#ifndef FFP_TEST_COMMON_H
#define FFP_TEST_COMMON_H
#include <math.h>
#include <string.h>
#include "../../../render/d3d9/ffp/ffp_ps_ref.h"
#define REC_WORDS 186

typedef struct { uint32_t w[REC_WORDS]; } Rec;
static float rf(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void rec_to_state(const Rec *r, NxFfpPsState *st, NxFfpPsInputs *in)
{
    static const uint8_t tt_dummy[8] = {0};
    uint32_t tss[8][33]; uint8_t tt[8]; uint32_t rs[NX_RS_COUNT];
    (void)tt_dummy;
    memset(tss, 0, sizeof tss); memset(rs, 0, sizeof rs);
    for (int s = 0; s < 8; s++) {
        const uint32_t *p = &r->w[s * 18];
        tss[s][NX_TSS_COLOROP] = p[0]; tss[s][NX_TSS_ALPHAOP] = p[1]; tss[s][NX_TSS_RESULTARG] = p[2]; tt[s] = (uint8_t)p[3];
        tss[s][NX_TSS_COLORARG0] = p[4]; tss[s][NX_TSS_COLORARG1] = p[5]; tss[s][NX_TSS_COLORARG2] = p[6];
        tss[s][NX_TSS_TEXTURETRANSFORMFLAGS] = p[7];
        tss[s][NX_TSS_ALPHAARG0] = p[8]; tss[s][NX_TSS_ALPHAARG1] = p[9]; tss[s][NX_TSS_ALPHAARG2] = p[10];
        tss[s][NX_TSS_CONSTANT] = p[11];
        tss[s][NX_TSS_BUMPENVMAT00] = p[12]; tss[s][NX_TSS_BUMPENVMAT01] = p[13];
        tss[s][NX_TSS_BUMPENVMAT10] = p[14]; tss[s][NX_TSS_BUMPENVMAT11] = p[15];
        tss[s][NX_TSS_BUMPENVLSCALE] = p[16]; tss[s][NX_TSS_BUMPENVLOFFSET] = p[17];
    }
    const uint32_t *g = &r->w[144];
    rs[NX_RS_TEXTUREFACTOR] = g[0]; rs[NX_RS_SPECULARENABLE] = g[1];
    nx_ffp_ps_state_from_d3d9((const uint32_t (*)[33])tss, rs, tt, st);
    for (int k = 0; k < 4; k++) { in->diffuse[k] = rf(g[2 + k]); in->specular[k] = rf(g[6 + k]); }
    for (int s = 0; s < 8; s++) for (int k = 0; k < 4; k++) in->texcoord[s][k] = rf(g[10 + s * 4 + k]);
}

/* texel(stage,type,coord): smooth, bounded in [0,1], same formula in oracle.py (type 1 ignores coord[2]) */
static void texel(uint32_t stage, uint32_t type, const float c[3], float out[4])
{
    for (int k = 0; k < 4; k++) {
        double x = 0.37 * stage + 0.23 * type + 0.11 * k + 0.53 * c[0] + 0.29 * c[1] + (type == 1 ? 0.0 : 0.17 * c[2]);
        double u = 4.0 * x - 1.5;
        out[k] = (float)(0.5 + 0.5 * u / (1.0 + fabs(u)));
    }
}
#endif
