/* Part n: the point-filter rule of Blit and GenMips, exactly: destination texel d of a dw-wide destination from an
 * sw-wide source takes source texel floor((d + 0.5) * sw / dw), same in y. Odd and even ratios, up and down, sub-rects
 * on both sides, every uncompressed colour format, depth and depth-stencil, a multisampled source. */
#include "t_ref.h"

static uint64_t blit(uint32_t src, const uint32_t sr[4], uint32_t dst, const uint32_t dr[4], uint32_t filter) {
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_Blit, 13, src, 0, sr[0], sr[1], sr[2], sr[3], dst, 0, dr[0], dr[1], dr[2], dr[3], filter);
    uint64_t r = t_submit(s);
    free(s);
    return r;
}

/* the oracle: model (dw x dh buffer of W x H texels) gets the rule applied to the source rect */
static void apply_rule(const uint8_t* src, int SW, const uint32_t sr[4], uint8_t* dst, int DW, const uint32_t dr[4], int bpp) {
    for (uint32_t j = 0; j < dr[3]; j++) for (uint32_t i = 0; i < dr[2]; i++) {
        uint32_t x = sr[0] + (2 * i + 1) * sr[2] / (2 * dr[2]), y = sr[1] + (2 * j + 1) * sr[3] / (2 * dr[3]);
        memcpy(dst + ((dr[1] + j) * (uint32_t)DW + dr[0] + i) * (uint32_t)bpp, src + (y * (uint32_t)SW + x) * (uint32_t)bpp, (size_t)bpp);
    }
}

static uint32_t mk(uint32_t fmt, int w, int h, uint32_t seed, uint8_t** keep) {
    const TFmt* f = t_fmt(fmt);
    uint32_t img = t_image(NxImgType_2D, fmt, (uint32_t)w, (uint32_t)h, 1, 1, 1), box[6] = {0, 0, 0, (uint32_t)w, (uint32_t)h, 1};
    size_t n = (size_t)w * h * f->bpp;
    uint8_t* p = (uint8_t*)calloc(1, n);
    t_pattern(f, p, (size_t)w * h, seed);
    CHECK(img && t_update(img, 0, 0, box, p, n, (size_t)w * f->bpp, 0) == 0);
    *keep = p;
    return img;
}

static void colour_formats(void) {
    static const uint32_t ratios[][4] = {{3, 3, 7, 7}, {5, 4, 8, 6}, {2, 2, 3, 3}, {7, 5, 3, 2}, {8, 8, 5, 5}, {16, 16, 8, 8},
                                         {9, 9, 4, 4}, {1, 1, 5, 5}, {5, 5, 1, 1}, {13, 9, 6, 4}, {8, 8, 8, 16}, {6, 6, 6, 3}};
    int bad = 0, n = 0;
    for (int fi = 0; fi < g_nfmts; fi++) {
        const TFmt* f = &g_fmts[fi];
        if (f->compressed || f->depth || f->kind == K_X8) continue;
        for (unsigned r = 0; r < sizeof ratios / sizeof ratios[0]; r++) {
            const uint32_t* q = ratios[r];
            uint8_t *sp, *dp;
            uint32_t s = mk(f->fmt, 20, 20, 3 + (uint32_t)r, &sp), d = mk(f->fmt, 24, 24, 0, &dp);
            uint32_t sr[4] = {3, 2, q[0], q[1]}, dr[4] = {5, 4, q[2], q[3]};
            apply_rule(sp, 20, sr, dp, 24, dr, f->bpp);
            uint8_t* got = NULL;
            int ok = blit(s, sr, d, dr, 0) == 0 && (got = t_read_all(d, 0, 0, 24, 24, 1, f)) != NULL && !memcmp(got, dp, (size_t)24 * 24 * f->bpp);
            if (!ok) { bad++; if (bad < 6) printf("  %s %ux%u -> %ux%u differs\n", f->name, q[0], q[1], q[2], q[3]); }
            n++;
            free(got); free(sp); free(dp);
            t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)s); t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)d);
        }
    }
    CHECKM(!bad, "colour point blits equal the rule: %d of %d differ", bad, n);
}

static void depth_formats(void) {
    static const uint32_t ratios[][4] = {{8, 8, 5, 3}, {3, 3, 7, 7}, {7, 5, 2, 2}, {16, 4, 8, 4}};
    static const uint32_t fm[] = {NxFmt_Z32F, NxFmt_Z16, NxFmt_Z24S8, NxFmt_Z24X8, NxFmt_Z32FS8};
    for (unsigned k = 0; k < 5; k++) {
        const TFmt* f = t_fmt(fm[k]);
        for (unsigned r = 0; r < 4; r++) {
            const uint32_t* q = ratios[r];
            uint8_t *sp, *dp;
            uint32_t s = mk(fm[k], 20, 20, 50 + (uint32_t)r, &sp), d = mk(fm[k], 24, 24, 90 + (uint32_t)r, &dp);   /* the destination holds other values and stencil bits */
            uint32_t sr[4] = {3, 2, q[0], q[1]}, dr[4] = {5, 4, q[2], q[3]};
            apply_rule(sp, 20, sr, dp, 24, dr, f->bpp);
            uint8_t* got = NULL;
            int ok = blit(s, sr, d, dr, 0) == 0 && (got = t_read_all(d, 0, 0, 24, 24, 1, f)) != NULL;
            int worst = 0;
            for (int i = 0; ok && i < 24 * 24; i++) {
                if (f->fmt == NxFmt_Z24S8 || f->fmt == NxFmt_Z24X8) {   /* 24-bit depth goes through float: +-1; the low byte (stencil) is exact */
                    uint32_t a, b;
                    memcpy(&a, got + 4 * i, 4); memcpy(&b, dp + 4 * i, 4);
                    int dd = abs((int)(a >> 8) - (int)(b >> 8));
                    worst = dd > worst ? dd : worst;
                    ok = dd <= 1 && (a & 255) == (b & 255);
                } else {
                    ok = !memcmp(got + (size_t)i * f->bpp, dp + (size_t)i * f->bpp, f->bpp);
                }
            }
            CHECKM(ok, "%s point blit %ux%u -> %ux%u (worst depth diff %d; texels outside the rect keep their value)", f->name, q[0], q[1], q[2], q[3], worst);
            free(got); free(sp); free(dp);
        }
    }
}

static void multisampled(void) {
    uint32_t ms = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 4);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_BeginPass, 8, ms, 0, NxClear_Color, 0xFF402010u, 0, 0, 8, 8);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    free(s);
    static const uint32_t sizes[3] = {16, 3, 5};
    for (int k = 0; k < 3; k++) {
        uint32_t d = t_image(NxImgType_2D, NxFmt_RGBA8, sizes[k], sizes[k], 1, 1, 1);
        uint32_t sr[4] = {0, 0, 8, 8}, dr[4] = {0, 0, sizes[k], sizes[k]};
        CHECK(blit(ms, sr, d, dr, 0) == 0);
        uint8_t* g = t_read_all(d, 0, 0, sizes[k], sizes[k], 1, t_fmt(NxFmt_RGBA8));
        int ok = g != NULL;
        for (uint32_t i = 0; ok && i < sizes[k] * sizes[k]; i++) ok = g[i * 4] == 0x10 && g[i * 4 + 1] == 0x20 && g[i * 4 + 2] == 0x40 && g[i * 4 + 3] == 0xFF;
        CHECKM(ok, "multisampled source scaled to %u x %u by the point rule", sizes[k], sizes[k]);
        free(g);
    }
}

void part_n(void) { colour_formats(); depth_formats(); multisampled(); }
