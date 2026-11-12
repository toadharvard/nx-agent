/* ffp_vs_state.c - D3D9 state -> NxFfpVsState. Copies plus the derivations D3D9 itself defines and the program cannot
 * do per vertex: D3DCOLOR -> floats, float bit patterns, normal matrix, eye-space lights and clip planes, half cone
 * angle cosines, the "first NX_FFP_MAX_LIGHTS enabled lights" rule. Derivations run in double and round once to float.
 * Total: a singular matrix has no inverse, so its inverse (transpose) is replaced by the adjugate (transpose), the
 * continuous extension up to the scale det (which is 0 or not finite there). */
#include <math.h>
#include <string.h>
#include "ffp_vs_state.h"

static float bits_f(uint32_t u) { float f; memcpy(&f, &u, 4); return f; }

static void color4(float o[4], uint32_t c)   /* D3DCOLOR 0xAARRGGBB */
{
    o[0] = (float)((c >> 16) & 255u) / 255.0f; o[1] = (float)((c >> 8) & 255u) / 255.0f;
    o[2] = (float)(c & 255u) / 255.0f;         o[3] = (float)((c >> 24) & 255u) / 255.0f;
}

static double det_n(const double *m, int n)          /* m: n x n row-major, n <= 4 (cofactor expansion, row 0) */
{
    if (n == 1) return m[0];
    double s = 0, sub[9];
    for (int c = 0; c < n; c++) {
        int k = 0;
        for (int r = 1; r < n; r++) for (int cc = 0; cc < n; cc++) if (cc != c) sub[k++] = m[r * n + cc];
        s += ((c & 1) ? -1.0 : 1.0) * m[c] * det_n(sub, n - 1);
    }
    return s;
}

static void cofactor(const double *m, int n, double *out)   /* out[r][c] = (-1)^(r+c) * minor(r,c) */
{
    double sub[9];
    for (int r = 0; r < n; r++) for (int c = 0; c < n; c++) {
        int k = 0;
        for (int rr = 0; rr < n; rr++) for (int cc = 0; cc < n; cc++) if (rr != r && cc != c) sub[k++] = m[rr * n + cc];
        out[r * n + c] = (((r + c) & 1) ? -1.0 : 1.0) * det_n(sub, n - 1);
    }
}

/* cof / det when det is finite and non-zero, else cof (the rule above) */
static void cof_over_det(const double *m, int n, double *out)
{
    double d = det_n(m, n);
    cofactor(m, n, out);
    if (d != 0.0 && isfinite(d)) for (int i = 0; i < n * n; i++) out[i] /= d;
}

static void vec_mat(const double v[4], const float *m, double o[4])   /* row vector times D3DMATRIX */
{ for (int c = 0; c < 4; c++) o[c] = v[0] * m[c] + v[1] * m[4 + c] + v[2] * m[8 + c] + v[3] * m[12 + c]; }

static void norm3(double v[3])   /* ffp_normalize: the zero vector stays zero */
{
    double l = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
    if (l != 0.0) { l = 1.0 / sqrt(l); v[0] *= l; v[1] *= l; v[2] *= l; }
}


void nx_ffp_vs_state_from_d3d9(const NxFfpVsD3d9 *in, NxFfpVsState *out)
{
    const NxFfpVsDecl *d = in->decl; const uint32_t *rs = in->rs;
    memset(out, 0, sizeof *out);
    uint32_t nb = d->nBlendW > 4 ? 4 : d->nBlendW, tc = 0;
    out->inputs[0] = (d->posT ? NX_FFP_IN_POSITIONT : 0) | (d->hasNormal ? NX_FFP_IN_NORMAL : 0) |
        (d->hasPsize ? NX_FFP_IN_PSIZE : 0) | (d->hasColor0 ? NX_FFP_IN_COLOR0 : 0) | (d->hasColor1 ? NX_FFP_IN_COLOR1 : 0) |
        (d->hasPos1 ? NX_FFP_IN_POSITION1 : 0) | (d->hasNormal1 ? NX_FFP_IN_NORMAL1 : 0) |
        (d->blendIdx ? NX_FFP_IN_BLENDINDICES : 0) | (d->blendIdx == 2 ? NX_FFP_IN_IDX_UNORM : 0) |
        (nb << NX_FFP_IN_NBLEND_SHIFT);
    for (int s = 0; s < 8; s++) tc |= (d->nTex[s] > 4 ? 4u : d->nTex[s]) << (4 * s);
    out->inputs[1] = tc;
    out->rsBlend[0] = rs[NX_VS_RS_VERTEXBLEND]; out->rsBlend[1] = rs[NX_VS_RS_INDEXEDVERTEXBLENDENABLE];
    out->rsBlend[2] = rs[NX_VS_RS_NORMALIZENORMALS]; out->rsBlend[3] = rs[NX_VS_RS_LIGHTING];
    out->rsLight[0] = rs[NX_VS_RS_COLORVERTEX]; out->rsLight[1] = rs[NX_VS_RS_SPECULARENABLE];
    out->rsLight[2] = rs[NX_VS_RS_LOCALVIEWER]; out->rsLight[3] = rs[NX_VS_RS_FOGENABLE];
    out->rsFog[0] = rs[NX_VS_RS_FOGVERTEXMODE]; out->rsFog[1] = rs[NX_VS_RS_FOGTABLEMODE];
    out->rsFog[2] = rs[NX_VS_RS_RANGEFOGENABLE]; out->rsFog[3] = rs[NX_VS_RS_POINTSCALEENABLE];
    out->rsSrc[0] = rs[NX_VS_RS_DIFFUSEMATERIALSOURCE]; out->rsSrc[1] = rs[NX_VS_RS_SPECULARMATERIALSOURCE];
    out->rsSrc[2] = rs[NX_VS_RS_AMBIENTMATERIALSOURCE]; out->rsSrc[3] = rs[NX_VS_RS_EMISSIVEMATERIALSOURCE];
    out->rsMisc[0] = rs[NX_VS_RS_CLIPPLANEENABLE]; out->rsMisc[1] = rs[NX_VS_RS_CLIPPING];
    out->rsMisc[2] = in->nLights > NX_FFP_MAX_LIGHTS ? NX_FFP_MAX_LIGHTS : in->nLights;
    for (int s = 0; s < 8; s++) { out->tci[s] = in->texCoordIndex[s]; out->ttff[s] = in->texTransformFlags[s]; }
    out->fog[0] = bits_f(rs[NX_VS_RS_FOGSTART]); out->fog[1] = bits_f(rs[NX_VS_RS_FOGEND]);
    out->fog[2] = bits_f(rs[NX_VS_RS_FOGDENSITY]); out->fog[3] = bits_f(rs[NX_VS_RS_TWEENFACTOR]);
    out->point[0] = bits_f(rs[NX_VS_RS_POINTSIZE]); out->point[1] = bits_f(rs[NX_VS_RS_POINTSIZE_MIN]);
    out->point[2] = bits_f(rs[NX_VS_RS_POINTSIZE_MAX]); out->point[3] = bits_f(rs[NX_VS_RS_POINTSCALE_A]);
    out->point2[0] = bits_f(rs[NX_VS_RS_POINTSCALE_B]); out->point2[1] = bits_f(rs[NX_VS_RS_POINTSCALE_C]);
    out->point2[2] = (float)in->viewport[3];
    for (int i = 0; i < 4; i++) out->viewport[i] = (float)in->viewport[i];
    color4(out->ambient, rs[NX_VS_RS_AMBIENT]);
    memcpy(out->mDiffuse, in->mDiffuse, 16); memcpy(out->mAmbient, in->mAmbient, 16);
    memcpy(out->mSpecular, in->mSpecular, 16); memcpy(out->mEmissive, in->mEmissive, 16);
    out->mPower[0] = in->mPower;
    memcpy(out->view, in->view, 64); memcpy(out->proj, in->proj, 64); memcpy(out->tex, in->texMatrix, 8 * 64);

    double wv[16], m3[9], n3[9];                           /* normal matrix: (WORLD0 * VIEW)[0..2][0..2] */
    for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) {
        double s = 0; for (int k = 0; k < 4; k++) s += (double)in->world0[r * 4 + k] * in->view[k * 4 + c];
        wv[r * 4 + c] = s;
    }
    for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) m3[r * 3 + c] = wv[r * 4 + c];
    cof_over_det(m3, 3, n3);
    out->normalMat[15] = 1.0f;
    for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) out->normalMat[r * 4 + c] = (float)n3[r * 3 + c];

    double v4[16], c4[16];                                 /* clip planes: eye plane k = sum_j cofdet[j][k] * plane[j] */
    for (int i = 0; i < 16; i++) v4[i] = in->view[i];
    cof_over_det(v4, 4, c4);
    for (int p = 0; p < 6; p++) for (int k = 0; k < 4; k++) {
        double s = 0; for (int j = 0; j < 4; j++) s += c4[j * 4 + k] * in->clipPlane[p][j];
        out->clipPlane[p][k] = (float)s;
    }

    for (uint32_t i = 0; i < out->rsMisc[2]; i++) {        /* first NX_FFP_MAX_LIGHTS enabled lights, in enable order */
        const NxFfpD3dLight *l = &in->lights[i]; NxFfpVsLight *o = &out->light[i];
        double p[4] = { l->position[0], l->position[1], l->position[2], 1.0 }, dr[4] = { l->direction[0], l->direction[1], l->direction[2], 0.0 };
        double pe[4], de[4], dn[4] = { -dr[0], -dr[1], -dr[2], 0.0 }, dnn[4];
        vec_mat(p, in->view, pe); vec_mat(dr, in->view, de); vec_mat(dn, in->view, dnn);
        norm3(de); norm3(dnn);
        memcpy(o->diffuse, l->diffuse, 16); memcpy(o->specular, l->specular, 16); memcpy(o->ambient, l->ambient, 16);
        o->kind[0] = l->type >= 1 && l->type <= 3 ? l->type : NX_FFP_LIGHT_NONE;
        if (l->type == NX_FFP_LIGHT_DIRECTIONAL) { for (int k = 0; k < 3; k++) o->position[k] = (float)dnn[k]; }
        else for (int k = 0; k < 3; k++) o->position[k] = (float)pe[k];
        for (int k = 0; k < 3; k++) o->direction[k] = (float)de[k];
        o->direction[3] = l->range;
        o->att[0] = l->attenuation0; o->att[1] = l->attenuation1; o->att[2] = l->attenuation2; o->att[3] = l->falloff;
        o->cone[0] = (float)cos((double)l->theta * 0.5); o->cone[1] = (float)cos((double)l->phi * 0.5);
    }
}
