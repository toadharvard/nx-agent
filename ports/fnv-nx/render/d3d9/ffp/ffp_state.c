/* ffp_state.c - D3D9 state -> NxFfpPsState. A copy plus the two type conversions D3D9 itself defines
 * (D3DCOLOR -> rgba floats, DWORD -> float bit pattern). No value is interpreted here: every enumeration value,
 * valid or not, is stored raw and interpreted by the program (see ffp.frag.glsl). */
#include <string.h>
#include "ffp_state.h"
#include "ffp_layout_ps.gen.h"   /* the std140 offsets of the UBO records (render/gl4shim/ffp/gen_glsl.py): a layout that differs is a compile error */

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
        s->op[NX_FFP_OP_COLOROP] = t[NX_TSS_COLOROP];
        s->op[NX_FFP_OP_ALPHAOP] = t[NX_TSS_ALPHAOP];
        s->op[NX_FFP_OP_RESULTARG] = t[NX_TSS_RESULTARG];
        s->op[NX_FFP_OP_TEXTYPE] = texType[i];
        s->carg[NX_FFP_CARG0] = t[NX_TSS_COLORARG0];
        s->carg[NX_FFP_CARG1] = t[NX_TSS_COLORARG1];
        s->carg[NX_FFP_CARG2] = t[NX_TSS_COLORARG2];
        s->carg[NX_FFP_CARG_TTFF] = t[NX_TSS_TEXTURETRANSFORMFLAGS];
        s->aarg[NX_FFP_AARG0] = t[NX_TSS_ALPHAARG0];
        s->aarg[NX_FFP_AARG1] = t[NX_TSS_ALPHAARG1];
        s->aarg[NX_FFP_AARG2] = t[NX_TSS_ALPHAARG2];
        color4(s->constant, t[NX_TSS_CONSTANT]);
        s->bump[0] = bits_f(t[NX_TSS_BUMPENVMAT00]);
        s->bump[1] = bits_f(t[NX_TSS_BUMPENVMAT01]);
        s->bump[2] = bits_f(t[NX_TSS_BUMPENVMAT10]);
        s->bump[3] = bits_f(t[NX_TSS_BUMPENVMAT11]);
        s->lum[0] = bits_f(t[NX_TSS_BUMPENVLSCALE]);
        s->lum[1] = bits_f(t[NX_TSS_BUMPENVLOFFSET]);
    }
    color4(out->tfactor, rs[NX_RS_TEXTUREFACTOR]);
    out->spec[NX_FFP_SPEC_ENABLE] = rs[NX_RS_SPECULARENABLE];
}
