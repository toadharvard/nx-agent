// pvw_math.cpp: the float helpers of Wine 9.0 wined3d (utils.c multiply_matrix / invert_matrix, device.c vec3 helpers, wined3d_private.h wined3d_vec4_transform), re-implemented with the same operation order:
// every sum is evaluated left to right in binary32 and nothing is fused (the guest compiler fuses by default: contraction is switched off for this file), so results are bit-equal with a C model of Wine's code.
#pragma GCC optimize("fp-contract=off")
#include "pvw.h"
#include <math.h>

void pvw_mul(PvMat* dst, const PvMat* s1, const PvMat* s2) {   // tmp[r][c] = ((s1[0][c]*s2[r][0] + s1[1][c]*s2[r][1]) + s1[2][c]*s2[r][2]) + s1[3][c]*s2[r][3]
    PvMat t;
    for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) {
        float a = s1->m[0][c] * s2->m[r][0], b = s1->m[1][c] * s2->m[r][1], e = s1->m[2][c] * s2->m[r][2], f = s1->m[3][c] * s2->m[r][3];
        t.m[r][c] = ((a + b) + e) + f;
    }
    *dst = t;
}
static void swp(float** a, float** b) { float* t = *a; *a = *b; *b = t; }
bool pvw_invert(PvMat* out, const PvMat* in) {   // invert_matrix: Gauss-Jordan over [M | I] with partial pivoting on the row pointers; the zero tests of the right half (columns 4..7) are Wine's
    float w[4][8]; float* r[4]; float m0, m1, m2, m3, s; int c;
    for (int i = 0; i < 4; i++) { r[i] = w[i]; for (int j = 0; j < 4; j++) { w[i][j] = in->m[i][j]; w[i][4 + j] = i == j ? 1.0f : 0.0f; } }
    if (fabsf(r[3][0]) > fabsf(r[2][0])) swp(&r[3], &r[2]);
    if (fabsf(r[2][0]) > fabsf(r[1][0])) swp(&r[2], &r[1]);
    if (fabsf(r[1][0]) > fabsf(r[0][0])) swp(&r[1], &r[0]);
    if (r[0][0] == 0.0f) return false;
    m1 = r[1][0] / r[0][0]; m2 = r[2][0] / r[0][0]; m3 = r[3][0] / r[0][0];
    for (c = 1; c < 8; c++) { s = r[0][c]; if (c >= 4 && s == 0.0f) continue; r[1][c] -= m1 * s; r[2][c] -= m2 * s; r[3][c] -= m3 * s; }
    if (fabsf(r[3][1]) > fabsf(r[2][1])) swp(&r[3], &r[2]);
    if (fabsf(r[2][1]) > fabsf(r[1][1])) swp(&r[2], &r[1]);
    if (r[1][1] == 0.0f) return false;
    m2 = r[2][1] / r[1][1]; m3 = r[3][1] / r[1][1];
    for (c = 2; c < 8; c++) { s = r[1][c]; if (c >= 4 && s == 0.0f) continue; r[2][c] -= m2 * s; r[3][c] -= m3 * s; }
    if (fabsf(r[3][2]) > fabsf(r[2][2])) swp(&r[3], &r[2]);
    if (r[2][2] == 0.0f) return false;
    m3 = r[3][2] / r[2][2];
    for (c = 3; c < 8; c++) r[3][c] -= m3 * r[2][c];
    if (r[3][3] == 0.0f) return false;
    s = 1.0f / r[3][3]; for (c = 4; c < 8; c++) r[3][c] *= s;                                                   // back substitution, row 3
    m2 = r[2][3]; s = 1.0f / r[2][2]; for (c = 4; c < 8; c++) r[2][c] = s * (r[2][c] - r[3][c] * m2);          // row 2
    m1 = r[1][3]; for (c = 4; c < 8; c++) r[1][c] -= r[3][c] * m1;
    m0 = r[0][3]; for (c = 4; c < 8; c++) r[0][c] -= r[3][c] * m0;
    m1 = r[1][2]; s = 1.0f / r[1][1]; for (c = 4; c < 8; c++) r[1][c] = s * (r[1][c] - r[2][c] * m1);          // row 1
    m0 = r[0][2]; for (c = 4; c < 8; c++) r[0][c] -= r[2][c] * m0;
    m0 = r[0][1]; s = 1.0f / r[0][0]; for (c = 4; c < 8; c++) r[0][c] = s * (r[0][c] - r[1][c] * m0);          // row 0
    for (int i = 0; i < 4; i++) for (int j = 0; j < 4; j++) out->m[i][j] = r[i][4 + j];
    return true;
}
float pvw_dot(const PvVec3* a, const PvVec3* b) { return a->x * b->x + a->y * b->y + a->z * b->z; }
void pvw_normalise(PvVec3* v) {
    float rn = 1.0f / sqrtf(pvw_dot(v, v));
    if (isfinite(rn)) { v->x *= rn; v->y *= rn; v->z *= rn; }
}
void pvw_xform4(PvVec4* dst, const PvVec4* v, const PvMat* m) {
    PvVec4 t;
    t.x = v->x * m->m[0][0] + v->y * m->m[1][0] + v->z * m->m[2][0] + v->w * m->m[3][0];
    t.y = v->x * m->m[0][1] + v->y * m->m[1][1] + v->z * m->m[2][1] + v->w * m->m[3][1];
    t.z = v->x * m->m[0][2] + v->y * m->m[1][2] + v->z * m->m[2][2] + v->w * m->m[3][2];
    t.w = v->x * m->m[0][3] + v->y * m->m[1][3] + v->z * m->m[2][3] + v->w * m->m[3][3];
    *dst = t;
}
float pvw_clamp(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }
