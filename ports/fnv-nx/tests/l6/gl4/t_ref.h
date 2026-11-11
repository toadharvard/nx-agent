/* CPU reference filters of the oracle (RGBA8-style interleaved bytes) */
#ifndef T_GL4_REF_H
#define T_GL4_REF_H
#include "t_common.h"
static inline int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }
/* point: dst texel (i,j) takes src texel (floor((i + .5) * sw / dw), floor((j + .5) * sh / dh)) */
static inline void ref_point(const uint8_t* s, int sw, int sh, uint8_t* d, int dw, int dh, int bpp) {
    for (int j = 0; j < dh; j++) for (int i = 0; i < dw; i++)
        memcpy(d + (j * dw + i) * bpp, s + (((2 * j + 1) * sh / (2 * dh)) * sw + (2 * i + 1) * sw / (2 * dw)) * bpp, bpp);
}
/* bilinear with clamped coordinates (the 2x2 box when halving) */
static inline void ref_bilinear(const uint8_t* s, int sw, int sh, uint8_t* d, int dw, int dh, int nch) {
    for (int j = 0; j < dh; j++) for (int i = 0; i < dw; i++) {
        double u = (i + 0.5) * sw / dw - 0.5, v = (j + 0.5) * sh / dh - 0.5;
        int x0 = (int)floor(u), y0 = (int)floor(v);
        double fx = u - x0, fy = v - y0;
        int xa = clampi(x0, 0, sw - 1), xb = clampi(x0 + 1, 0, sw - 1), ya = clampi(y0, 0, sh - 1), yb = clampi(y0 + 1, 0, sh - 1);
        for (int c = 0; c < nch; c++) {
            double t = (1 - fy) * ((1 - fx) * s[(ya * sw + xa) * nch + c] + fx * s[(ya * sw + xb) * nch + c]) +
                       fy * ((1 - fx) * s[(yb * sw + xa) * nch + c] + fx * s[(yb * sw + xb) * nch + c]);
            d[(j * dw + i) * nch + c] = (uint8_t)(t + 0.5);
        }
    }
}
/* max abs difference of two byte arrays */
static inline int maxdiff(const uint8_t* a, const uint8_t* b, size_t n) {
    int m = 0;
    for (size_t i = 0; i < n; i++) { int d = abs((int)a[i] - (int)b[i]); if (d > m) m = d; }
    return m;
}
/* point minification: GL leaves the texel under a destination centre that falls exactly on a texel boundary (2:1)
 * open, so the oracle accepts any texel of the destination texel's source footprint (per axis) */
static inline int point_ok(const uint8_t* s, int sw, int sh, int sd, const uint8_t* d, int dw, int dh, int dd, int bpp) {
    for (int k = 0; k < dd; k++) for (int j = 0; j < dh; j++) for (int i = 0; i < dw; i++) {
        int x0 = i * sw / dw, x1 = ((i + 1) * sw + dw - 1) / dw - 1, y0 = j * sh / dh, y1 = ((j + 1) * sh + dh - 1) / dh - 1;
        int z0 = k * sd / dd, z1 = ((k + 1) * sd + dd - 1) / dd - 1, found = 0;
        for (int z = z0; z <= z1 && !found; z++) for (int y = y0; y <= y1 && !found; y++) for (int x = x0; x <= x1 && !found; x++)
            found = !memcmp(d + (((size_t)k * dh + j) * dw + i) * bpp, s + (((size_t)z * sh + y) * sw + x) * bpp, bpp);
        if (!found) return 0;
    }
    return 1;
}
#endif
