/* ffp_ps_ref.c - see ffp_ps_ref.h. Each function below has a same-named twin in ffp.frag.glsl. */
#include <math.h>
#include "ffp_ps_ref.h"

typedef struct { float v[4]; } V4;
static V4 mk(float x, float y, float z, float w) { V4 r = {{x, y, z, w}}; return r; }
static V4 splat(float x) { return mk(x, x, x, x); }
static V4 add(V4 a, V4 b) { for (int k = 0; k < 4; k++) a.v[k] += b.v[k]; return a; }
static V4 sub(V4 a, V4 b) { for (int k = 0; k < 4; k++) a.v[k] -= b.v[k]; return a; }
static V4 mul(V4 a, V4 b) { for (int k = 0; k < 4; k++) a.v[k] *= b.v[k]; return a; }
static V4 sat4(V4 a)
{
    for (int k = 0; k < 4; k++) a.v[k] = a.v[k] < 0.0f ? 0.0f : (a.v[k] > 1.0f ? 1.0f : a.v[k]);
    return a;
}
static float sat1(float x) { return x < 0.0f ? 0.0f : (x > 1.0f ? 1.0f : x); }

uint32_t nx_ffp_norm_op(uint32_t v) { return (v >= 1u && v <= 26u) ? v : 0u; }
uint32_t nx_ffp_norm_arg_sel(uint32_t a) { uint32_t s = a & 15u; return s > 6u ? 7u : s; }
uint32_t nx_ffp_norm_textype(uint32_t t) { return (t >= 1u && t <= 3u) ? t : 0u; }

/* argument slots an op reads: bit0 arg1, bit1 arg2, bit2 arg0 (wined3d_ffp_get_fs_settings args[]) */
static uint32_t ffp_used(uint32_t op)
{
    if (op == NX_FFP_TOP_SELECTARG1 || op == NX_FFP_TOP_PREMODULATE) return 1u;   /* PREMODULATE: documented S = Arg1 */
    if (op == NX_FFP_TOP_SELECTARG2) return 2u;
    if (op == NX_FFP_TOP_MULTIPLYADD || op == NX_FFP_TOP_LERP) return 7u;
    if (op >= NX_FFP_TOP_MODULATE) return 3u;
    return 0u;
}

/* is_invalid_op: a read of TEXTURE by an op in a stage with no bound texture */
static int ffp_invalid(int hasTex, uint32_t op, uint32_t a0, uint32_t a1, uint32_t a2)
{
    uint32_t u = ffp_used(op);
    if (hasTex) return 0;
    return ((u & 1u) && nx_ffp_norm_arg_sel(a1) == NX_FFP_TA_TEXTURE) ||
           ((u & 2u) && nx_ffp_norm_arg_sel(a2) == NX_FFP_TA_TEXTURE) ||
           ((u & 4u) && nx_ffp_norm_arg_sel(a0) == NX_FFP_TA_TEXTURE);
}

typedef struct { V4 diffuse, spec, cur, tmp, tex, tf, cst; } Regs;

static V4 ffp_source(uint32_t sel, const Regs *r)
{
    switch (sel) {   /* sel is normalised 0..7: total */
    case NX_FFP_TA_DIFFUSE: return r->diffuse;
    case NX_FFP_TA_CURRENT: return r->cur;
    case NX_FFP_TA_TEXTURE: return r->tex;
    case NX_FFP_TA_TFACTOR: return r->tf;
    case NX_FFP_TA_SPECULAR: return r->spec;
    case NX_FFP_TA_TEMP: return r->tmp;
    case NX_FFP_TA_CONSTANT: return r->cst;
    }
    return splat(0.0f);                                             /* NX_FFP_TA_ZERO */
}

static V4 ffp_arg(uint32_t a, const Regs *r)
{
    V4 v = ffp_source(nx_ffp_norm_arg_sel(a), r);
    if (a & NX_FFP_TA_COMPLEMENT) v = sub(splat(1.0f), v);
    if (a & NX_FFP_TA_ALPHAREPLICATE) v = splat(v.v[3]);
    return v;
}

/* all four channels at once; the blend factors are the alpha of a raw (modifier-free) source */
static V4 ffp_op(uint32_t op, V4 a0, V4 a1, V4 a2, const Regs *r)
{
    V4 one = splat(1.0f), half = splat(0.5f), w1 = splat(a1.v[3]), al;
    switch (op) {
    case NX_FFP_TOP_SELECTARG1: return a1;
    case NX_FFP_TOP_SELECTARG2: return a2;
    case NX_FFP_TOP_MODULATE: return mul(a1, a2);
    case NX_FFP_TOP_MODULATE2X: return mul(mul(a1, a2), splat(2.0f));
    case NX_FFP_TOP_MODULATE4X: return mul(mul(a1, a2), splat(4.0f));
    case NX_FFP_TOP_ADD: return add(a1, a2);
    case NX_FFP_TOP_ADDSIGNED: return sub(add(a1, a2), half);
    case NX_FFP_TOP_ADDSIGNED2X: return mul(sub(add(a1, a2), half), splat(2.0f));
    case NX_FFP_TOP_SUBTRACT: return sub(a1, a2);
    case NX_FFP_TOP_ADDSMOOTH: return add(a1, mul(a2, sub(one, a1)));
    case NX_FFP_TOP_BLENDDIFFUSEALPHA:
    case NX_FFP_TOP_BLENDTEXTUREALPHA:
    case NX_FFP_TOP_BLENDFACTORALPHA:
    case NX_FFP_TOP_BLENDCURRENTALPHA:
        al = splat(op == NX_FFP_TOP_BLENDDIFFUSEALPHA ? r->diffuse.v[3] : op == NX_FFP_TOP_BLENDTEXTUREALPHA ? r->tex.v[3]
                   : op == NX_FFP_TOP_BLENDFACTORALPHA ? r->tf.v[3] : r->cur.v[3]);
        return add(mul(a1, al), mul(a2, sub(one, al)));
    case NX_FFP_TOP_BLENDTEXTUREALPHAPM: return add(a1, mul(a2, splat(1.0f - r->tex.v[3])));
    case NX_FFP_TOP_PREMODULATE: return a1;
    case NX_FFP_TOP_MODULATEALPHA_ADDCOLOR: return add(a1, mul(a2, w1));
    case NX_FFP_TOP_MODULATECOLOR_ADDALPHA: return add(mul(a1, a2), w1);
    case NX_FFP_TOP_MODULATEINVALPHA_ADDCOLOR: return add(a1, mul(a2, sub(one, w1)));
    case NX_FFP_TOP_MODULATEINVCOLOR_ADDALPHA: return add(mul(sub(one, a1), a2), w1);
    case NX_FFP_TOP_DOTPRODUCT3: {
        float d = 0.0f;
        for (int k = 0; k < 3; k++) d += (a1.v[k] - 0.5f) * (a2.v[k] - 0.5f);
        return splat(4.0f * d);
    }
    case NX_FFP_TOP_MULTIPLYADD: return add(mul(a1, a2), a0);
    case NX_FFP_TOP_LERP: return add(mul(a1, a0), mul(a2, sub(one, a0)));
    }
    return splat(0.0f);    /* NOP, DISABLE, BUMPENVMAP*: never written */
}

void nx_ffp_ps_eval(const NxFfpPsState *st, const NxFfpPsInputs *in, float out_rgba[4])
{
    Regs r;
    r.diffuse = mk(in->diffuse[0], in->diffuse[1], in->diffuse[2], in->diffuse[3]);
    r.spec = mk(in->specular[0], in->specular[1], in->specular[2], in->specular[3]);
    r.tf = mk(st->tfactor[0], st->tfactor[1], st->tfactor[2], st->tfactor[3]);
    r.cur = r.diffuse;                         /* CURRENT at stage 0 = DIFFUSE */
    r.tmp = splat(0.0f);                       /* TEMP starts as 0 */
    uint32_t prevBump = 0u;                    /* previous stage: 1 BUMPENVMAP, 2 BUMPENVMAPLUMINANCE */
    int pmC = 0, pmA = 0;                      /* previous stage was PREMODULATE (colour / alpha) */
    V4 prevTex = splat(0.0f);

    for (uint32_t s = 0; s < NX_FFP_STAGES; s++) {
        const NxFfpStage *S = &st->stage[s];
        uint32_t cop = nx_ffp_norm_op(S->colorOp);
        if (cop == NX_FFP_TOP_DISABLE) break;                       /* COLOROP DISABLE ends the cascade */
        uint32_t type = nx_ffp_norm_textype(S->texType);
        int hasTex = type != NX_FFP_TEX_NONE;
        uint32_t c0 = S->cArg0, c1 = S->cArg1, c2 = S->cArg2;
        if (ffp_invalid(hasTex, cop, c0, c1, c2)) { cop = NX_FFP_TOP_SELECTARG1; c1 = NX_FFP_TA_CURRENT; }
        uint32_t aop, a0, a1, a2;
        if (cop == NX_FFP_TOP_DOTPRODUCT3) { aop = cop; a0 = c0; a1 = c1; a2 = c2; }   /* DOT3 replicates into alpha */
        else {
            aop = nx_ffp_norm_op(S->alphaOp); a0 = S->aArg0; a1 = S->aArg1; a2 = S->aArg2;
            if (ffp_invalid(hasTex, aop, a0, a1, a2)) { aop = NX_FFP_TOP_SELECTARG1; a1 = NX_FFP_TA_CURRENT; }
        }

        /* texture fetch: projective divide, BUMPENVMAP of the previous stage, luminance */
        V4 tex = splat(0.0f);
        if (hasTex) {
            uint32_t count = S->ttff & 255u;
            int proj = (S->ttff & 256u) != 0u && type != NX_FFP_TEX_CUBE;
            uint32_t di = (count >= 1u && count <= 4u) ? count - 1u : 3u;   /* the last transformed coordinate */
            float c[3] = { in->texcoord[s][0], in->texcoord[s][1], in->texcoord[s][2] };
            float d = in->texcoord[s][di];
            if (proj && d != 0.0f) { c[0] /= d; c[1] /= d; c[2] /= d; }
            if (prevBump != 0u) {
                const NxFfpStage *P = &st->stage[s - 1u];
                c[0] += P->bump[0] * prevTex.v[0] + P->bump[2] * prevTex.v[1];
                c[1] += P->bump[1] * prevTex.v[0] + P->bump[3] * prevTex.v[1];
            }
            in->sample(in->user, s, type, c, tex.v);
        }
        if (prevBump == 2u) {
            const NxFfpStage *P = &st->stage[s - 1u];
            tex = mul(tex, splat(sat1(prevTex.v[2] * P->lum[0] + P->lum[1])));
        }
        if (pmC) for (int k = 0; k < 3; k++) r.cur.v[k] *= tex.v[k];  /* PREMODULATE of the previous stage */
        if (pmA) r.cur.v[3] *= tex.v[3];
        r.tex = tex;
        r.cst = mk(S->constant[0], S->constant[1], S->constant[2], S->constant[3]);

        V4 rc = ffp_op(cop, ffp_arg(c0, &r), ffp_arg(c1, &r), ffp_arg(c2, &r), &r);
        V4 ra = ffp_op(aop, ffp_arg(a0, &r), ffp_arg(a1, &r), ffp_arg(a2, &r), &r);
        int bump = cop == NX_FFP_TOP_BUMPENVMAP || cop == NX_FFP_TOP_BUMPENVMAPLUMINANCE;
        int wc = cop != NX_FFP_TOP_NOP && !bump;
        int wa = !bump && aop != NX_FFP_TOP_NOP && aop != NX_FFP_TOP_DISABLE &&
                 aop != NX_FFP_TOP_BUMPENVMAP && aop != NX_FFP_TOP_BUMPENVMAPLUMINANCE;
        V4 *dst = (S->resultArg == NX_FFP_TA_TEMP) ? &r.tmp : &r.cur;   /* RESULTARG: exactly TEMP, else CURRENT */
        if (wc) for (int k = 0; k < 3; k++) dst->v[k] = sat1(rc.v[k]);
        if (wa) dst->v[3] = sat1(ra.v[3]);

        pmC = cop == NX_FFP_TOP_PREMODULATE;
        pmA = wa && aop == NX_FFP_TOP_PREMODULATE;
        prevBump = cop == NX_FFP_TOP_BUMPENVMAP ? 1u : (cop == NX_FFP_TOP_BUMPENVMAPLUMINANCE ? 2u : 0u);
        prevTex = tex;
    }
    float se = st->specularEnable != 0u ? 1.0f : 0.0f;                  /* D3DRS_SPECULARENABLE: RGB only */
    V4 o = sat4(add(r.cur, mul(r.spec, mk(se, se, se, 0.0f))));
    for (int k = 0; k < 4; k++) out_rgba[k] = o.v[k];
}
