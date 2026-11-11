/* Part c: NxOp_Blit (copy, point/linear scaling, depth, MSAA resolve, conversion, swapchain destination).
 * Part d: NxOp_GenMips against CPU box / point references. */
#include "t_ref.h"

static const TFmt* fmt_of(uint32_t f) { for (int i = 0; i < g_nfmts; i++) if (g_fmts[i].fmt == f) return &g_fmts[i]; return NULL; }

static uint32_t mk(uint32_t fmt, uint32_t w, uint32_t h, uint32_t seed, uint8_t** keep) {   /* 2D image filled with a pattern */
    const TFmt* f = fmt_of(fmt);
    uint32_t img = t_image(NxImgType_2D, fmt, w, h, 1, 1, 1);
    size_t n = t_rowbytes(f, w) * t_rows(f, h);
    uint8_t* p = (uint8_t*)calloc(1, n);
    if (seed) t_pattern(f, p, n / f->bpp, seed);
    uint32_t box[6] = {0, 0, 0, w, h, 1};
    CHECK(img && t_update(img, 0, 0, box, p, n, t_rowbytes(f, w), 0) == 0);
    if (keep) *keep = p; else free(p);
    return img;
}
static uint64_t blit(uint32_t src, const uint32_t sr[4], uint32_t dst, const uint32_t dr[4], uint32_t filter) {
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_Blit, 13, src, 0, sr[0], sr[1], sr[2], sr[3], dst, 0, dr[0], dr[1], dr[2], dr[3], filter);
    uint64_t r = t_submit(s);
    free(s);
    return r;
}

static void c_copy(void) {
    static const uint32_t fm[] = {NxFmt_RGBA8, NxFmt_RGBA16F, NxFmt_R5G6B5, NxFmt_BC1, NxFmt_BC3, NxFmt_Z24S8, NxFmt_Z32F,
                                  NxFmt_Z32FS8, NxFmt_RGBA32F, NxFmt_RG8S, NxFmt_RGB10A2};
    for (unsigned i = 0; i < sizeof fm / 4; i++) {
        const TFmt* f = fmt_of(fm[i]);
        uint8_t* sp; uint8_t* dp;
        uint32_t s = mk(fm[i], 12, 12, 40 + i, &sp), d = mk(fm[i], 16, 16, 0, &dp);
        uint32_t sr[4] = {4, 4, 8, 4}, dr[4] = {8, 12, 8, 4};
        if (!f->compressed) { sr[0] = 2; sr[1] = 1; sr[2] = 8; sr[3] = 6; dr[0] = 5; dr[1] = 7; dr[2] = 8; dr[3] = 6; }
        CHECKM(blit(s, sr, d, dr, 0) == 0, "copy blit %s", f->name);
        size_t srb = t_rowbytes(f, 12), drb = t_rowbytes(f, 16);
        uint32_t u = f->compressed ? 4 : 1;
        for (uint32_t y = 0; y < sr[3] / u; y++)
            memcpy(dp + (dr[1] / u + y) * drb + dr[0] / u * f->bpp, sp + (sr[1] / u + y) * srb + sr[0] / u * f->bpp, sr[2] / u * f->bpp);
        uint8_t* got = t_read_all(d, 0, 0, 16, 16, 1, f);
        CHECKM(got && !memcmp(got, dp, drb * t_rows(f, 16)), "copy result %s", f->name);
        free(got); free(sp); free(dp);
    }
}

static void c_scale(void) {
    uint8_t* sp; uint8_t dp[16 * 16 * 4], ex[16 * 16 * 4];
    uint32_t s = mk(NxFmt_RGBA8, 8, 8, 5, &sp), d = mk(NxFmt_RGBA8, 16, 16, 0, NULL);
    const TFmt* f = fmt_of(NxFmt_RGBA8);
    uint32_t sr[4] = {0, 0, 8, 8}, dr[4] = {0, 0, 16, 16};
    CHECK(blit(s, sr, d, dr, 0) == 0);
    uint8_t* got = t_read_all(d, 0, 0, 16, 16, 1, f);
    ref_point(sp, 8, 8, ex, 16, 16, 4);
    CHECK(got && !memcmp(got, ex, sizeof ex)); free(got);
    for (int y = 0; y < 16; y++) for (int x = 0; x < 16; x++) CHECK(!memcmp(ex + (y * 16 + x) * 4, sp + ((y / 2) * 8 + x / 2) * 4, 4) || 1);
    int exact = 1;
    for (int y = 0; y < 16 && exact; y++) for (int x = 0; x < 16; x++) if (memcmp(ex + (y * 16 + x) * 4, sp + ((y / 2) * 8 + x / 2) * 4, 4)) { exact = 0; break; }
    CHECK(exact);   /* the reference itself is the 2x pixel replication */
    /* sub-rect upscale into a zeroed image: src (2,2,4x4) -> dst (4,4,8x8) */
    uint32_t d2 = mk(NxFmt_RGBA8, 16, 16, 0, NULL), sr2[4] = {2, 2, 4, 4}, dr2[4] = {4, 4, 8, 8};
    CHECK(blit(s, sr2, d2, dr2, 0) == 0);
    memset(dp, 0, sizeof dp);
    for (int y = 0; y < 8; y++) for (int x = 0; x < 8; x++) memcpy(dp + ((4 + y) * 16 + 4 + x) * 4, sp + ((2 + y / 2) * 8 + 2 + x / 2) * 4, 4);
    got = t_read_all(d2, 0, 0, 16, 16, 1, f);
    CHECK(got && !memcmp(got, dp, sizeof dp)); free(got);
    /* point downscale 16 -> 8: the rule takes the texel 2d + 1 of each pair */
    free(sp);   /* the 8x8 source of the upscales above; sp is reassigned */
    uint32_t big = mk(NxFmt_RGBA8, 16, 16, 9, &sp), small = mk(NxFmt_RGBA8, 8, 8, 0, NULL);
    uint32_t bs[4] = {0, 0, 16, 16}, ss[4] = {0, 0, 8, 8};
    CHECK(blit(big, bs, small, ss, 0) == 0);
    uint8_t e8[8 * 8 * 4];
    got = t_read_all(small, 0, 0, 8, 8, 1, f);
    ref_point(sp, 16, 16, e8, 8, 8, 4);
    CHECK(got && !memcmp(got, e8, sizeof e8)); free(got);
    /* linear: 2x up, 2:1 down (box), 3:2 up against the bilinear reference, <= 1 LSB */
    static const int sizes[3][2] = {{8, 16}, {16, 8}, {8, 12}};
    for (int k = 0; k < 3; k++) {
        int sw = sizes[k][0], dw = sizes[k][1];
        uint8_t* lp; uint32_t ls = mk(NxFmt_RGBA8, sw, sw, 20 + k, &lp), ld = mk(NxFmt_RGBA8, dw, dw, 0, NULL);
        uint32_t a[4] = {0, 0, sw, sw}, b[4] = {0, 0, dw, dw};
        CHECK(blit(ls, a, ld, b, 1) == 0);
        uint8_t* le = (uint8_t*)malloc(dw * dw * 4);
        ref_bilinear(lp, sw, sw, le, dw, dw, 4);
        got = t_read_all(ld, 0, 0, dw, dw, 1, f);
        int md = got ? maxdiff(got, le, dw * dw * 4) : 99;
        CHECKM(md <= 1, "linear %d -> %d: max diff %d", sw, dw, md);
        free(got); free(le); free(lp);
    }
    free(sp);
}

static void c_depth(void) {
    static const uint32_t fm[] = {NxFmt_Z24S8, NxFmt_Z32F, NxFmt_Z16};
    for (unsigned i = 0; i < 3; i++) {
        const TFmt* f = fmt_of(fm[i]);
        uint8_t* sp;
        uint32_t s = mk(fm[i], 8, 8, 60 + i, &sp), d = mk(fm[i], 16, 16, 0, NULL);
        uint32_t sr[4] = {0, 0, 8, 8}, dr[4] = {0, 0, 16, 16};
        CHECKM(blit(s, sr, d, dr, 1) == 0, "depth blit %s (linear asked: point)", f->name);   /* depth is always point sampled */
        uint8_t* ex = (uint8_t*)malloc(16 * 16 * f->bpp);
        ref_point(sp, 8, 8, ex, 16, 16, f->bpp);
        uint8_t* got = t_read_all(d, 0, 0, 16, 16, 1, f);
        int same = got != NULL;   /* GL converts depth through float: Z24 may differ by 1 in its 24-bit depth, stencil exact */
        for (int q = 0; same && q < 256; q++) {
            if (f->fmt == NxFmt_Z24S8) { uint32_t a, b; memcpy(&a, got + q * 4, 4); memcpy(&b, ex + q * 4, 4); same = abs((int)(a >> 8) - (int)(b >> 8)) <= 1 && (a & 255) == (b & 255); }
            else same = !memcmp(got + q * f->bpp, ex + q * f->bpp, f->bpp);
        }
        CHECKM(same, "depth scaled %s", f->name);
        free(got); free(ex); free(sp);
    }
    /* depth blit between different formats has no rule here: it is refused, not approximated */
    uint32_t a = mk(NxFmt_Z24S8, 8, 8, 1, NULL), b = mk(NxFmt_Z32F, 8, 8, 0, NULL);
    uint32_t r[4] = {0, 0, 8, 8};
    CHECK(blit(a, r, b, r, 0) == NXGPU_BAD_CALL);
}

static void c_msaa(void) {
    static const uint32_t ns[3] = {2, 4, 8};
    const TFmt* rgba = fmt_of(NxFmt_RGBA8);
    for (int k = 0; k < 3; k++) {
        uint32_t col = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, ns[k]);
        uint32_t dep = t_image(NxImgType_2D, NxFmt_Z24S8, 8, 8, 1, 1, ns[k]);
        if (ns[k] == 8) {   /* open point: llvmpipe supports at most 4 samples; the failed create leaves nothing behind */
            CHECKM(!col && !dep, "8x MSAA is refused on this GL (MAX_SAMPLES 4)");
            continue;
        }
        CHECKM(col && dep, "create %ux MSAA colour + depth-stencil", ns[k]);
        if (!col || !dep) continue;
        Stream* s = (Stream*)calloc(1, sizeof *s);
        float z = 0.25f; uint32_t zb; memcpy(&zb, &z, 4);
        t_op(s, NxOp_BeginPass, 8, col, dep, 7, 0x80302010u, zb, 7, 8, 8);
        t_op(s, NxOp_EndPass, 0);
        CHECK(t_submit(s) == 0);
        free(s);
        uint32_t one = mk(NxFmt_RGBA8, 8, 8, 0, NULL), big = mk(NxFmt_RGBA8, 16, 16, 0, NULL);
        uint32_t r8[4] = {0, 0, 8, 8}, r16[4] = {0, 0, 16, 16};
        uint8_t want8[8 * 8 * 4], want16[16 * 16 * 4];
        for (int i = 0; i < 64; i++) { want8[i * 4] = 0x10; want8[i * 4 + 1] = 0x20; want8[i * 4 + 2] = 0x30; want8[i * 4 + 3] = 0x80; }
        for (int i = 0; i < 256; i++) memcpy(want16 + i * 4, want8, 4);
        CHECK(blit(col, r8, one, r8, 0) == 0);
        uint8_t* got = t_read_all(one, 0, 0, 8, 8, 1, rgba);
        CHECKM(got && !memcmp(got, want8, sizeof want8), "%ux resolve blit", ns[k]); free(got);
        got = t_read_all(col, 0, 0, 8, 8, 1, rgba);   /* image_read of the multisampled image resolves first */
        CHECKM(got && !memcmp(got, want8, sizeof want8), "%ux image_read resolve", ns[k]); free(got);
        CHECK(blit(col, r8, big, r16, 1) == 0);   /* resolve, then scale */
        got = t_read_all(big, 0, 0, 16, 16, 1, rgba);
        CHECKM(got && !memcmp(got, want16, sizeof want16), "%ux resolve + scale", ns[k]); free(got);
        /* depth-stencil: depth 0.25 -> d24 = round(0.25 * 0xFFFFFF) = 0x400000, stencil 7 */
        uint32_t dres = mk(NxFmt_Z24S8, 8, 8, 0, NULL);
        CHECK(blit(dep, r8, dres, r8, 0) == 0);
        got = t_read_all(dres, 0, 0, 8, 8, 1, fmt_of(NxFmt_Z24S8));
        int ok = got != NULL;
        for (int i = 0; ok && i < 64; i++) { uint32_t v; memcpy(&v, got + i * 4, 4); ok = (v >> 8) >= 0x3FFFFF && (v >> 8) <= 0x400001 && (v & 255) == 7; }
        CHECKM(ok, "%ux depth-stencil resolve", ns[k]); free(got);
    }
    /* resolve into the swapchain (dst image 0): v3 readback of the swapchain image */
    uint32_t col = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 4);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_BeginPass, 8, col, 0, 1, 0xFF0000FFu, 0, 0, 16, 16);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    uint32_t r[4] = {0, 0, 16, 16};
    t_op(s, NxOp_Blit, 13, col, 0, 0, 0, 16, 16, 0, 0, 0, 0, 16, 16, 0);
    CHECK(t_submit(s) == 0);
    free(s);
    static uint8_t rb[16 * 16 * 4];
    NxReadback rd = {0, 0, 0, 16, 16, NxRb_RGBA8, (uint64_t)(uintptr_t)rb, sizeof rb, 0};
    CHECK(t_ext(NXGPU_FN_READBACK, 1, (uint64_t)(uintptr_t)&rd) == 0);
    int ok = 1;
    for (int i = 0; i < 256; i++) ok &= rb[i * 4] == 0xFF && rb[i * 4 + 1] == 0 && rb[i * 4 + 2] == 0 && rb[i * 4 + 3] == 0xFF;
    CHECK(ok);
    (void)r;
}

static void c_convert(void) {
    uint8_t* sp;
    uint32_t s = mk(NxFmt_RGBA8, 8, 8, 3, &sp), r[4] = {0, 0, 8, 8};
    uint32_t bg = mk(NxFmt_BGRA8, 8, 8, 0, NULL), r8 = mk(NxFmt_R8, 8, 8, 0, NULL), hf = mk(NxFmt_RGBA16F, 8, 8, 0, NULL);
    CHECK(blit(s, r, bg, r, 0) == 0 && blit(s, r, r8, r, 0) == 0 && blit(s, r, hf, r, 0) == 0);
    uint8_t* g = t_read_all(bg, 0, 0, 8, 8, 1, fmt_of(NxFmt_BGRA8));
    int ok = g != NULL;
    for (int i = 0; ok && i < 64; i++) ok = g[i * 4] == sp[i * 4 + 2] && g[i * 4 + 1] == sp[i * 4 + 1] && g[i * 4 + 2] == sp[i * 4] && g[i * 4 + 3] == sp[i * 4 + 3];
    CHECK(ok); free(g);
    g = t_read_all(r8, 0, 0, 8, 8, 1, fmt_of(NxFmt_R8));
    ok = g != NULL;
    for (int i = 0; ok && i < 64; i++) ok = g[i] == sp[i * 4];
    CHECK(ok); free(g);
    uint8_t* h = t_read_all(hf, 0, 0, 8, 8, 1, fmt_of(NxFmt_RGBA16F));
    ok = h != NULL;
    for (int i = 0; ok && i < 256; i++) { uint16_t v; memcpy(&v, h + 2 * i, 2); ok = fabsf(t_half(v) - sp[i] / 255.0f) < 1e-3f; }
    CHECK(ok); free(h); free(sp);
    /* rejected: out of range rect, compressed destination with scaling, multisampled destination */
    uint32_t bad[4] = {4, 4, 8, 8};
    CHECK(blit(s, bad, bg, r, 0) == NXGPU_BAD_CALL);
    uint32_t bc = mk(NxFmt_BC1, 8, 8, 0, NULL);
    CHECK(blit(s, r, bc, r, 0) == NXGPU_BAD_CALL);
    uint32_t ms = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 4);
    CHECK(blit(s, r, ms, r, 0) == NXGPU_BAD_CALL);
}

static void part_c(void) { c_copy(); c_scale(); c_depth(); c_msaa(); c_convert(); }

/* ---- part d ------------------------------------------------------------------------------------------------- */
static uint32_t g_gm(uint32_t type, uint32_t w, uint32_t h, uint32_t d, uint32_t mips, uint32_t fmt, uint32_t filter, uint32_t seed) {
    const TFmt* f = fmt_of(fmt);
    uint32_t img = t_image(type, fmt, w, h, d, mips, 1), layers = type == NxImgType_Cube ? 6 : 1;
    for (uint32_t ly = 0; ly < layers; ly++) {
        size_t n = t_rowbytes(f, w) * h * d;
        uint8_t* p = (uint8_t*)malloc(n);
        t_pattern(f, p, n / f->bpp, seed + ly);
        uint32_t box[6] = {0, 0, 0, w, h, d};
        CHECK(t_update(img, 0, ly, box, p, n, t_rowbytes(f, w), 0) == 0);
        free(p);
    }
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_GenMips, 2, img, filter);
    CHECK(t_submit(s) == 0);
    free(s);
    return img;
}

/* verifies every level l >= 1 of every layer against the reference applied to the backend's own level l - 1 */
static void d_check(uint32_t img, uint32_t type, uint32_t w, uint32_t h, uint32_t dd, uint32_t mips, uint32_t fmt, uint32_t filter, const char* what) {
    const TFmt* f = fmt_of(fmt);
    int nch = f->bpp, layers = type == NxImgType_Cube ? 6 : 1, worst = 0, bad = 0;
    for (int ly = 0; ly < layers; ly++)
        for (uint32_t l = 1; l < mips; l++) {
            uint32_t sw = lvl(w, l - 1), sh = lvl(h, l - 1), sd = lvl(dd, l - 1), dw = lvl(w, l), dh = lvl(h, l), ddp = lvl(dd, l);
            uint8_t* src = t_read_all(img, l - 1, ly, sw, sh, sd, f);
            uint8_t* got = t_read_all(img, l, ly, dw, dh, ddp, f);
            uint8_t* ex = (uint8_t*)malloc((size_t)dw * dh * ddp * nch);
            if (!src || !got) { bad++; free(src); free(got); free(ex); continue; }
            for (uint32_t z = 0; z < ddp; z++) {
                if (type == NxImgType_3D && filter == 1) {   /* 2x2x2 box over the (clamped) slice pair, then the plane filter */
                    uint32_t z0 = z * sd / ddp, z1 = z0 + 1 < sd && sd > 1 ? z0 + 1 : z0;
                    uint8_t* a = (uint8_t*)malloc((size_t)dw * dh * nch), *b = (uint8_t*)malloc((size_t)dw * dh * nch);
                    ref_bilinear(src + (size_t)z0 * sw * sh * nch, sw, sh, a, dw, dh, nch);
                    ref_bilinear(src + (size_t)z1 * sw * sh * nch, sw, sh, b, dw, dh, nch);
                    for (size_t i = 0; i < (size_t)dw * dh * nch; i++) ex[(size_t)z * dw * dh * nch + i] = (uint8_t)((a[i] + b[i] + 1) / 2);
                    free(a); free(b);
                } else if (filter == 1) {
                    ref_bilinear(src, sw, sh, ex, dw, dh, nch);
                }
            }
            if (filter == 0) {   /* exactly the rule: texel floor((d + 0.5) * s / dsize) per axis (slice rule on z for 3D) */
                for (uint32_t z = 0; z < ddp; z++) {
                    uint32_t zs = type == NxImgType_3D ? (uint32_t)(((2ull * z + 1) * sd) / (2ull * ddp)) : 0;
                    ref_point(src + (size_t)zs * sw * sh * nch, (int)sw, (int)sh, ex + (size_t)z * dw * dh * nch, (int)dw, (int)dh, nch);
                }
                if (memcmp(got, ex, (size_t)dw * dh * ddp * nch)) { bad++; printf("  %s level %u: point result differs from the rule\n", what, l); }
                free(src); free(got); free(ex);
                continue;
            }
            int md = maxdiff(got, ex, (size_t)dw * dh * ddp * nch);
            if (md > worst) worst = md;
            if (md > 1) { bad++; printf("  %s level %u (%ux%u -> %ux%u): diff %d\n", what, l, sw, sh, dw, dh, md); }
            free(src); free(got); free(ex);
        }
    CHECKM(!bad, "%s filter %u: %d bad levels, worst diff %d", what, filter, bad, worst);
}

static void part_d(void) {
    for (uint32_t filter = 0; filter < 2; filter++) {
        uint32_t i;
        i = g_gm(NxImgType_2D, 16, 16, 1, 5, NxFmt_RGBA8, filter, 100); d_check(i, NxImgType_2D, 16, 16, 1, 5, NxFmt_RGBA8, filter, "2D 16x16");
        i = g_gm(NxImgType_2D, 16, 4, 1, 5, NxFmt_RGBA8, filter, 110); d_check(i, NxImgType_2D, 16, 4, 1, 5, NxFmt_RGBA8, filter, "2D 16x4");
        i = g_gm(NxImgType_2D, 13, 9, 1, 4, NxFmt_RGBA8, filter, 120); d_check(i, NxImgType_2D, 13, 9, 1, 4, NxFmt_RGBA8, filter, "2D 13x9");
        i = g_gm(NxImgType_2D, 32, 32, 1, 6, NxFmt_R8, filter, 130); d_check(i, NxImgType_2D, 32, 32, 1, 6, NxFmt_R8, filter, "2D R8");
        i = g_gm(NxImgType_Cube, 8, 8, 1, 4, NxFmt_RGBA8, filter, 140); d_check(i, NxImgType_Cube, 8, 8, 1, 4, NxFmt_RGBA8, filter, "Cube");
        i = g_gm(NxImgType_3D, 8, 8, 4, 4, NxFmt_RGBA8, filter, 150); d_check(i, NxImgType_3D, 8, 8, 4, 4, NxFmt_RGBA8, filter, "3D 8x8x4");
    }
    /* level 0 is untouched, and an image with one level accepts GenMips (nothing to generate) */
    const TFmt* f = fmt_of(NxFmt_RGBA8);
    uint32_t i = g_gm(NxImgType_2D, 8, 8, 1, 1, NxFmt_RGBA8, 1, 7);
    uint8_t want[8 * 8 * 4]; t_pattern(f, want, 64, 7);
    uint8_t* got = t_read_all(i, 0, 0, 8, 8, 1, f);
    CHECK(got && !memcmp(got, want, sizeof want)); free(got);
    i = g_gm(NxImgType_2D, 16, 16, 1, 5, NxFmt_RGBA8, 1, 9);
    uint8_t w16[16 * 16 * 4]; t_pattern(f, w16, 256, 9);
    got = t_read_all(i, 0, 0, 16, 16, 1, f);
    CHECK(got && !memcmp(got, w16, sizeof w16)); free(got);
    /* GenMips on a multisampled image is refused */
    uint32_t ms = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 8, 1, 1, 4);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_GenMips, 2, ms, 0);
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    free(s);
}

void part_cd(char which) { if (which == 'c') part_c(); else part_d(); }
