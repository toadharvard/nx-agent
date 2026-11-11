/* ffp_vs_ref.c - CPU reference of the D3D9 fixed-function vertex pipeline; twin of ffp.vert.glsl (same step order).
 * Matrices are D3D row-major with row vectors: vm(v, M)[c] = sum_r v[r] * M[r*4+c]. */
#include <math.h>
#include <string.h>
#include "ffp_vs_ref.h"

static float sat(float x) { return x < 0.0f ? 0.0f : x > 1.0f ? 1.0f : x; }
static float dot3(const float *a, const float *b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
static void nz3(float *v)         /* ffp_normalize: the zero vector stays zero */
{
    float l = dot3(v, v);
    if (l != 0.0f) { l = 1.0f / sqrtf(l); v[0] *= l; v[1] *= l; v[2] *= l; }
}
static void vm(const float v[4], const float *m, float o[4])
{ for (int c = 0; c < 4; c++) o[c] = v[0] * m[c] + v[1] * m[4 + c] + v[2] * m[8 + c] + v[3] * m[12 + c]; }
static void vm3(const float v[3], const float *m, float o[3])        /* row vector (v,0) times the upper 3x3 */
{ for (int c = 0; c < 3; c++) o[c] = v[0] * m[c] + v[1] * m[4 + c] + v[2] * m[8 + c]; }
static void mm(const float *a, const float *b, float *o)
{ for (int r = 0; r < 4; r++) vm(a + 4 * r, b, o + 4 * r); }

/* D3DMATERIALCOLORSOURCE: COLOR1 = vertex diffuse (COLOR0 input), COLOR2 = vertex specular (COLOR1 input); a source whose
 * colour is not provided, any other value, and COLORVERTEX == 0 select the material colour. */
static void mcs(uint32_t src, int colorVertex, int h0, int h1, const float *c0, const float *c1, const float *mat, float *o)
{
    const float *p = mat;
    if (colorVertex && src == 1 && h0) p = c0;
    if (colorVertex && src == 2 && h1) p = c1;
    memcpy(o, p, 16);
}

void nx_ffp_vs_eval(const NxFfpVsState *st, const float (*palette)[16], const float posfix[4], const NxFfpVsIn *in,
                    NxFfpVsOut *out)
{
    const uint32_t fl = st->inputs[0];
    const int posT = (fl & NX_FFP_IN_POSITIONT) != 0, hasN = (fl & NX_FFP_IN_NORMAL) != 0;
    const int hasC0 = (fl & NX_FFP_IN_COLOR0) != 0, hasC1 = (fl & NX_FFP_IN_COLOR1) != 0;
    uint32_t nbw = (fl >> NX_FFP_IN_NBLEND_SHIFT) & 7u; if (nbw > 4) nbw = 4;
    const float one4[4] = { 1, 1, 1, 1 }, zero4[4] = { 0, 0, 0, 0 };
    const float *c0 = hasC0 ? in->color0 : one4, *c1 = hasC1 ? in->color1 : zero4;
    float nin[3] = { 0, 0, 0 }, ec[4], ecRaw[4], clip[4], normal[3] = { 0, 0, 0 };
    if (hasN) memcpy(nin, in->normal, 12);

    const uint32_t vb = st->rsBlend[0];
    const uint32_t nb = (vb >= 1 && vb <= 3) ? vb : 0, K = nb + 1;
    const int tween = vb == 255;
    const int idxOn = st->rsBlend[1] != 0 && ((vb >= 1 && vb <= 3) || vb == 256);

    if (posT) {                                              /* POSITIONT: viewport -> clip, no transform, no lighting */
        float X = in->pos[0], Y = in->pos[1], Z = in->pos[2], rhw = in->pos[3];
        float vx = st->viewport[0], vy = st->viewport[1], vw = st->viewport[2], vh = st->viewport[3];
        float nx = vw != 0.0f ? 2.0f * (X - vx) / vw - 1.0f : 0.0f, ny = vh != 0.0f ? 1.0f - 2.0f * (Y - vy) / vh : 0.0f;
        float w = rhw != 0.0f ? 1.0f / rhw : 1.0f;
        clip[0] = nx * w; clip[1] = ny * w; clip[2] = Z * w; clip[3] = w;
        ec[0] = X; ec[1] = Y; ec[2] = Z; ec[3] = 1.0f; memcpy(ecRaw, ec, 16);
    } else {
        float p[4] = { in->pos[0], in->pos[1], in->pos[2], 1.0f }, nn[3];
        memcpy(nn, nin, 12);
        if (tween) {
            float t = st->fog[3], p1[3] = { 0, 0, 0 }, n1[3] = { 0, 0, 0 };
            if (fl & NX_FFP_IN_POSITION1) memcpy(p1, in->pos1, 12);
            if (fl & NX_FFP_IN_NORMAL1) memcpy(n1, in->normal1, 12);
            for (int k = 0; k < 3; k++) { p[k] = (1.0f - t) * p[k] + t * p1[k]; nn[k] = (1.0f - t) * nn[k] + t * n1[k]; }
        }
        float wt[4], bw[4], wv[4][16];
        for (int i = 0; i < 4; i++) bw[i] = (uint32_t)i < nbw ? in->blendweight[i] : 0.0f;
        float sum = 0.0f;
        for (uint32_t i = 0; i < nb; i++) { wt[i] = bw[i]; sum += bw[i]; }
        wt[nb] = 1.0f - sum;
        const float sc = (fl & NX_FFP_IN_IDX_UNORM) ? 255.0f : 1.0f;
        memset(ec, 0, 16);
        for (uint32_t i = 0; i < K; i++) {
            uint32_t mi = i;
            if (idxOn) {
                float x = (fl & NX_FFP_IN_BLENDINDICES) ? floorf(in->blendindex[i] * sc + 0.5f) : 0.0f;
                mi = x < 0.0f ? 0u : x > 255.0f ? 255u : (uint32_t)x;
            }
            mm(palette[mi], st->view, wv[i]);
            float e[4]; vm(p, wv[i], e);
            for (int k = 0; k < 4; k++) ec[k] += wt[i] * e[k];
        }
        vm(ec, st->proj, clip);
        memcpy(ecRaw, ec, 16);
        if (ec[3] != 0.0f) { float iw = 1.0f / ec[3]; for (int k = 0; k < 4; k++) ec[k] *= iw; }
        float nr[3];
        if (K == 1 && !idxOn) vm3(nn, st->normalMat, nr);
        else {
            nr[0] = nr[1] = nr[2] = 0.0f;
            for (uint32_t i = 0; i < K; i++) { float t3[3]; vm3(nn, wv[i], t3); for (int k = 0; k < 3; k++) nr[k] += wt[i] * t3[k]; }
        }
        if (st->rsBlend[2] != 0) nz3(nr);
        memcpy(normal, nr, 12);
    }

    /* ---- lighting (D3D9 "Mathematics of Lighting", eye space) */
    float o0[4], o1[4];
    if (!posT && st->rsBlend[3] != 0) {
        const int cv = st->rsLight[0] != 0, specOn = st->rsLight[1] != 0, localViewer = st->rsLight[2] != 0;
        float accA[3] = { st->ambient[0], st->ambient[1], st->ambient[2] }, accD[3] = { 0, 0, 0 }, accS[4] = { 0, 0, 0, 0 };
        for (uint32_t i = 0; i < st->rsMisc[2] && i < NX_FFP_MAX_LIGHTS; i++) {
            const NxFfpVsLight *L = &st->light[i];
            float ldir[3], att = 1.0f;
            const uint32_t kind = L->kind[0];
            if (kind == NX_FFP_LIGHT_DIRECTIONAL) {
                memcpy(ldir, L->position, 12);
                for (int k = 0; k < 3; k++) accA[k] += L->ambient[k];
            } else if (kind == NX_FFP_LIGHT_POINT || kind == NX_FFP_LIGHT_SPOT) {
                for (int k = 0; k < 3; k++) ldir[k] = L->position[k] - ec[k];
                float dist = sqrtf(dot3(ldir, ldir));
                if (dist > L->direction[3]) continue;                     /* beyond Range: no contribution at all */
                nz3(ldir);
                float spot = 1.0f;
                if (kind == NX_FFP_LIGHT_SPOT) {
                    float rho = -dot3(ldir, L->direction);
                    spot = rho > L->cone[0] ? 1.0f : rho <= L->cone[1] ? 0.0f
                         : powf((rho - L->cone[1]) / (L->cone[0] - L->cone[1]), L->att[3]);
                }
                float den = L->att[0] + L->att[1] * dist + L->att[2] * dist * dist;
                att = den == 0.0f ? 0.0f : spot / den;
                for (int k = 0; k < 3; k++) accA[k] += L->ambient[k] * att;
            } else continue;
            float ndl = dot3(ldir, normal);
            for (int k = 0; k < 3; k++) accD[k] += sat(ndl) * L->diffuse[k] * att;
            if (specOn) {
                float hv[3], e[3] = { ec[0], ec[1], ec[2] };
                if (localViewer) { nz3(e); for (int k = 0; k < 3; k++) hv[k] = ldir[k] - e[k]; }
                else { hv[0] = ldir[0]; hv[1] = ldir[1]; hv[2] = ldir[2] - 1.0f; }
                nz3(hv);
                float t = dot3(normal, hv);
                if (ndl > 0.0f && t > 0.0f) { float pw = powf(t, st->mPower[0]) * att; for (int k = 0; k < 4; k++) accS[k] += pw * L->specular[k]; }
            }
        }
        float A[4], D[4], S[4], E[4];
        mcs(st->rsSrc[2], cv, hasC0, hasC1, c0, c1, st->mAmbient, A);
        mcs(st->rsSrc[0], cv, hasC0, hasC1, c0, c1, st->mDiffuse, D);
        mcs(st->rsSrc[1], cv, hasC0, hasC1, c0, c1, st->mSpecular, S);
        mcs(st->rsSrc[3], cv, hasC0, hasC1, c0, c1, st->mEmissive, E);
        for (int k = 0; k < 3; k++) o0[k] = A[k] * accA[k] + D[k] * accD[k] + E[k];
        o0[3] = D[3];
        for (int k = 0; k < 4; k++) o1[k] = specOn ? S[k] * accS[k] : c1[k];
    } else { memcpy(o0, c0, 16); memcpy(o1, c1, 16); }
    for (int k = 0; k < 4; k++) { out->color0[k] = sat(o0[k]); out->color1[k] = sat(o1[k]); }

    /* ---- texture coordinates */
    for (int s = 0; s < 8; s++) {
        const uint32_t tci = st->tci[s], ttff = st->ttff[s], hi = tci >> 16, set = tci & 0xFFFFu;
        const uint32_t gen = (hi >= 1 && hi <= 4) ? hi : 0;
        float src[4]; uint32_t n;
        if (gen) {
            n = 3;
            if (gen == 1) { memcpy(src, normal, 12); src[3] = 1.0f; }
            else if (gen == 2) memcpy(src, ec, 16);
            else {
                float e[3] = { ec[0], ec[1], ec[2] }, r[3];
                nz3(e);
                float d2 = 2.0f * dot3(normal, e);
                for (int k = 0; k < 3; k++) r[k] = e[k] - d2 * normal[k];
                if (gen == 3) { memcpy(src, r, 12); src[3] = 1.0f; }
                else {
                    float m = 2.0f * sqrtf(r[0] * r[0] + r[1] * r[1] + (r[2] - 1.0f) * (r[2] - 1.0f));
                    src[0] = m != 0.0f ? r[0] / m + 0.5f : 0.5f; src[1] = m != 0.0f ? r[1] / m + 0.5f : 0.5f;
                    src[2] = 0.0f; src[3] = 1.0f;
                }
            }
        } else {
            n = set < 8 ? (st->inputs[1] >> (4 * set)) & 15u : 0u; if (n > 4) n = 4;
            for (uint32_t k = 0; k < 4; k++) src[k] = (set < 8 && k < n) ? in->tex[set][k] : (k == 3 ? 1.0f : 0.0f);
        }
        const uint32_t cnt = ttff & 255u, proj = (ttff >> 8) & 1u;
        float o[4];
        if (!posT && cnt >= 1 && cnt <= 4) {
            float pad[4];
            for (uint32_t k = 0; k < 4; k++) pad[k] = n == 0 ? (k == 3 ? 1.0f : 0.0f) : k < n ? src[k] : (k == n ? 1.0f : 0.0f);
            vm(pad, st->tex[s], o);
            for (uint32_t k = cnt; k < 4; k++) o[k] = 0.0f;
            if (proj && cnt < 4) o[3] = o[cnt - 1];
        } else {
            memcpy(o, src, 16);
            if (!posT && proj && n >= 1 && n <= 3) o[3] = src[n - 1];
        }
        memcpy(out->tex[s], o, 16);
    }

    /* ---- fog: factor 1 = unfogged; table fog: eye depth for the pixel epilogue */
    {
        const uint32_t tm = (st->rsFog[1] >= 1 && st->rsFog[1] <= 3) ? st->rsFog[1] : 0;
        const uint32_t vmode = (st->rsFog[0] >= 1 && st->rsFog[0] <= 3) ? st->rsFog[0] : 0;
        float f;
        if (st->rsLight[3] == 0) f = 1.0f;
        else if (posT) f = tm ? in->pos[2] : sat(c1[3]);
        else if (tm) f = fabsf(ec[2]);
        else if (vmode == 0) f = sat(c1[3]);
        else {
            float d = st->rsFog[2] != 0 ? sqrtf(dot3(ec, ec)) : fabsf(ec[2]);
            float s = st->fog[0], e = st->fog[1], dn = st->fog[2];
            if (vmode == 3) f = e != s ? (e - d) / (e - s) : (d <= e ? 1.0f : 0.0f);
            else if (vmode == 1) f = expf(-d * dn);
            else f = expf(-(d * dn) * (d * dn));
            f = sat(f);
        }
        out->fog = f;
    }
    /* ---- point size */
    {
        float si = (fl & NX_FFP_IN_PSIZE) ? in->psize : st->point[0], s = si;
        if (st->rsFog[3] != 0) {
            float de = sqrtf(dot3(ec, ec)), den = st->point[3] + st->point2[0] * de + st->point2[1] * de * de;
            s = den > 0.0f ? st->point2[2] * si / sqrtf(den) : INFINITY;
        }
        s = s < st->point[1] ? st->point[1] : s;
        out->psize = s > st->point[2] ? st->point[2] : s;
    }
    /* ---- user clip planes (eye space; none on pre-transformed vertices or with CLIPPING off) */
    for (int i = 0; i < 6; i++) {
        int on = !posT && st->rsMisc[1] != 0 && ((st->rsMisc[0] >> i) & 1u);
        const float *pl = st->clipPlane[i];
        out->clip[i] = on ? ecRaw[0] * pl[0] + ecRaw[1] * pl[1] + ecRaw[2] * pl[2] + ecRaw[3] * pl[3] : 1.0f;
    }
    /* ---- position convention of the translated shaders (README "D3D9 conventions") */
    out->position[0] = clip[0] + posfix[0] * clip[3];
    out->position[1] = (clip[1] + posfix[1] * clip[3]) * posfix[2];
    out->position[2] = clip[2]; out->position[3] = clip[3];
}
