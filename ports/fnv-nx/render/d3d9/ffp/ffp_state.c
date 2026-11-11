/* ffp_state.c - D3D9 state -> NxFfpPsState. A copy plus the two type conversions D3D9 itself defines
 * (D3DCOLOR -> rgba floats, DWORD -> float bit pattern). No value is interpreted here: every enumeration value,
 * valid or not, is stored raw and interpreted by the program (see ffp_ps_ref.h). */
#include <string.h>
#include "ffp_state.h"

static void color4(float o[4], uint32_t c)   /* D3DCOLOR 0xAARRGGBB */
{
    o[0] = (float)((c >> 16) & 255u) / 255.0f;
    o[1] = (float)((c >> 8) & 255u) / 255.0f;
    o[2] = (float)(c & 255u) / 255.0f;
    o[3] = (float)((c >> 24) & 255u) / 255.0f;
}

static float bits_f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

void nx_ffp_ps_state_from_d3d9(const uint32_t tss[8][33], const uint32_t *rs, const uint8_t texType[8],
                               NxFfpPsState *out)
{
    memset(out, 0, sizeof *out);
    for (int i = 0; i < NX_FFP_STAGES; i++) {
        const uint32_t *t = tss[i];
        NxFfpStage *s = &out->stage[i];
        s->colorOp = t[NX_TSS_COLOROP];
        s->alphaOp = t[NX_TSS_ALPHAOP];
        s->resultArg = t[NX_TSS_RESULTARG];
        s->texType = texType[i];
        s->cArg0 = t[NX_TSS_COLORARG0];
        s->cArg1 = t[NX_TSS_COLORARG1];
        s->cArg2 = t[NX_TSS_COLORARG2];
        s->ttff = t[NX_TSS_TEXTURETRANSFORMFLAGS];
        s->aArg0 = t[NX_TSS_ALPHAARG0];
        s->aArg1 = t[NX_TSS_ALPHAARG1];
        s->aArg2 = t[NX_TSS_ALPHAARG2];
        color4(s->constant, t[NX_TSS_CONSTANT]);
        s->bump[0] = bits_f(t[NX_TSS_BUMPENVMAT00]);
        s->bump[1] = bits_f(t[NX_TSS_BUMPENVMAT01]);
        s->bump[2] = bits_f(t[NX_TSS_BUMPENVMAT10]);
        s->bump[3] = bits_f(t[NX_TSS_BUMPENVMAT11]);
        s->lum[0] = bits_f(t[NX_TSS_BUMPENVLSCALE]);
        s->lum[1] = bits_f(t[NX_TSS_BUMPENVLOFFSET]);
    }
    color4(out->tfactor, rs[NX_RS_TEXTUREFACTOR]);
    out->specularEnable = rs[NX_RS_SPECULARENABLE];
}
