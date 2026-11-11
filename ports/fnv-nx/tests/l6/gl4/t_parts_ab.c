/* Part a: every NxFormat x {2D, Cube, 3D}: create, update every level/layer with a pattern, read back byte-equal.
 * Part b: sub-box updates (pitched, offset sources, BCn block boxes, 3D slices, cube faces) against a CPU model, and
 * rejected updates. */
#include "t_common.h"

typedef struct { uint32_t type, w, h, d, mips; } Shape;
static const Shape k_shapes[3] = {{NxImgType_2D, 13, 9, 1, 4}, {NxImgType_Cube, 8, 8, 1, 4}, {NxImgType_3D, 8, 4, 4, 4}};
static const char* const k_tname[3] = {"2D", "Cube", "3D"};

static void part_a(void) {
    for (int fi = 0; fi < g_nfmts; fi++) {
        const TFmt* f = &g_fmts[fi];
        for (int si = 0; si < 3; si++) {
            const Shape* sh = &k_shapes[si];
            if (f->depth && si != 0) continue;   /* depth images are 2D only (GL has no cube / 3D depth) */
            if (si == 2 && (f->fmt == NxFmt_BC4 || f->fmt == NxFmt_BC5)) {   /* open point: GL 4.5 / llvmpipe have no 3D RGTC */
                CHECKM(t_image(sh->type, f->fmt, sh->w, sh->h, sh->d, sh->mips, 1) == 0, "%s 3D is refused (failed create, no half-made image)", f->name);
                continue;
            }
            uint32_t img = t_image(sh->type, f->fmt, sh->w, sh->h, sh->d, sh->mips, 1);
            CHECKM(img != 0, "create %s %s", f->name, k_tname[si]);
            if (!img) continue;
            uint32_t layers = sh->type == NxImgType_Cube ? 6 : 1;
            for (uint32_t l = 0; l < sh->mips; l++)
                for (uint32_t ly = 0; ly < layers; ly++) {
                    uint32_t w = lvl(sh->w, l), h = lvl(sh->h, l), d = lvl(sh->d, l), box[6] = {0, 0, 0, w, h, d};
                    size_t rb = t_rowbytes(f, w), n = rb * t_rows(f, h) * d;
                    uint8_t* p = (uint8_t*)malloc(n);
                    t_pattern(f, p, n / f->bpp, f->fmt * 1000 + si * 100 + l * 10 + ly);
                    CHECKM(t_update(img, l, ly, box, p, n, rb, 0) == 0, "update %s %s l%u f%u", f->name, k_tname[si], l, ly);
                    free(p);
                }
            for (uint32_t l = 0; l < sh->mips; l++)   /* second pass: later updates must not have clobbered earlier ones */
                for (uint32_t ly = 0; ly < layers; ly++) {
                    uint32_t w = lvl(sh->w, l), h = lvl(sh->h, l), d = lvl(sh->d, l);
                    size_t rb = t_rowbytes(f, w), n = rb * t_rows(f, h) * d;
                    uint8_t* want = (uint8_t*)malloc(n);
                    t_pattern(f, want, n / f->bpp, f->fmt * 1000 + si * 100 + l * 10 + ly);
                    uint8_t* got = t_read_all(img, l, ly, w, h, d, f);
                    CHECKM(got && !memcmp(got, want, n), "readback %s %s l%u f%u", f->name, k_tname[si], l, ly);
                    free(got); free(want);
                }
            CHECK(t_ext(NXGPU_FN_IMAGE_DESTROY, 1, (uint64_t)img) == 0);
        }
    }
}

static void part_b(void) {
    /* RGBA8 16x16: zero fill, box (3,2) 5x4 from an offset source with a padded pitch, CPU model of the result */
    uint32_t img = t_image(NxImgType_2D, NxFmt_RGBA8, 16, 16, 1, 1, 1);
    const TFmt* f = &g_fmts[0];
    static uint8_t model[16 * 16 * 4], zero[16 * 16 * 4], src[4 * 64], got[16 * 16 * 4];
    uint32_t whole[6] = {0, 0, 0, 16, 16, 1};
    CHECK(t_update(img, 0, 0, whole, zero, sizeof zero, 64, 0) == 0);
    t_pattern(f, src, sizeof src / 4, 77);
    uint32_t box[6] = {3, 2, 0, 5, 4, 1};
    CHECK(t_update(img, 0, 0, box, src, 3 * 64 + 20, 64, 4096) == 0);   /* pitch 64 > 20 bytes of row; 4 rows */
    for (int y = 0; y < 4; y++) memcpy(model + ((2 + y) * 16 + 3) * 4, src + y * 64, 20);
    CHECK(t_read(img, 0, 0, whole, got, sizeof got, 64) == 0 && !memcmp(got, model, sizeof got));
    /* a read of a sub-rect with a padded destination pitch */
    static uint8_t sub[4 * 100];
    uint32_t rb[6] = {4, 3, 0, 3, 4, 1};
    CHECK(t_read(img, 0, 0, rb, sub, sizeof sub, 100) == 0);
    int ok = 1;
    for (int y = 0; y < 4; y++) ok &= !memcmp(sub + y * 100, model + ((3 + y) * 16 + 4) * 4, 12);
    CHECK(ok);
    /* rejected updates change nothing and fail the submit: source too small for the box, box outside the image */
    uint8_t other[4 * 64];
    memset(other, 0xAB, sizeof other);
    uint32_t box2[6] = {0, 0, 0, 4, 4, 1};
    memcpy(g_scratch, other, sizeof other);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_ImageUpdate, 12, img, 0, 0, 0, 0, 4, 4, 1, g_mem, 0, 20, 64);   /* size 20 < 3 * 64 + 16 */
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    t_op(s, NxOp_ImageUpdate, 12, img, 0, 13, 0, 0, 4, 4, 1, g_mem, 0, 4 * 64, 64);   /* x + w = 17 > 16 */
    CHECK(t_submit(s) == NXGPU_BAD_CALL);
    free(s);
    (void)box2;
    CHECK(t_read(img, 0, 0, whole, got, sizeof got, 64) == 0 && !memcmp(got, model, sizeof got));
    /* BCn block boxes: BC1 16x16 (4x4 blocks of 8 bytes): box of blocks (1,2)-(3,3); BC3 13x9 edge box reaching the edge */
    for (int pass = 0; pass < 2; pass++) {
        const TFmt* bf = &g_fmts[pass ? 4 : 2];
        uint32_t W = pass ? 13 : 16, H = pass ? 9 : 16;
        uint32_t bi = t_image(NxImgType_2D, bf->fmt, W, H, 1, 1, 1);
        size_t rbw = t_rowbytes(bf, W), nb = rbw * t_rows(bf, H);
        uint8_t* m = (uint8_t*)calloc(1, nb);
        uint32_t wb[6] = {0, 0, 0, W, H, 1};
        CHECK(t_update(bi, 0, 0, wb, m, nb, rbw, 0) == 0);
        uint32_t bx = pass ? 8 : 4, by = pass ? 4 : 8, bw = pass ? 5 : 8, bh = pass ? 5 : 4;   /* pass 1: reaches the edge (13, 9) */
        size_t brb = t_rowbytes(bf, bw), brows = t_rows(bf, bh);
        uint8_t* bs = (uint8_t*)malloc(brb * brows);
        t_pattern(bf, bs, brb * brows / bf->bpp, 900 + pass);
        uint32_t bb[6] = {bx, by, 0, bw, bh, 1};
        CHECKM(t_update(bi, 0, 0, bb, bs, brb * brows, brb, 0) == 0, "BCn box update %d", pass);
        for (size_t r = 0; r < brows; r++) memcpy(m + (by / 4 + r) * rbw + bx / 4 * bf->bpp, bs + r * brb, brb);
        uint8_t* g2 = t_read_all(bi, 0, 0, W, H, 1, bf);
        CHECKM(g2 && !memcmp(g2, m, nb), "BCn whole readback %d", pass);
        uint8_t* sg = (uint8_t*)malloc(brb * brows);
        CHECK(t_read(bi, 0, 0, bb, sg, brb * brows, brb) == 0 && !memcmp(sg, bs, brb * brows));
        free(sg); free(g2); free(bs); free(m);
        Stream* us = (Stream*)calloc(1, sizeof *us);   /* x not block aligned: rejected */
        t_op(us, NxOp_ImageUpdate, 12, bi, 0, 1, 0, 0, 4, 4, 1, g_mem, 0, 4096, (uint32_t)(bf->bpp));
        CHECK(t_submit(us) == NXGPU_BAD_CALL);
        free(us);
    }
    /* 3D: update slices 1..2 of an 8x4x4 R8 volume with a padded pitch, model-compare the whole volume */
    uint32_t vol = t_image(NxImgType_3D, NxFmt_R8, 8, 4, 4, 1, 1);
    static uint8_t vm[8 * 4 * 4], vz[8 * 4 * 4], vs[2 * 3 * 16], vg[8 * 4 * 4];
    uint32_t vw[6] = {0, 0, 0, 8, 4, 4};
    CHECK(t_update(vol, 0, 0, vw, vz, sizeof vz, 8, 0) == 0);
    t_pattern(&g_fmts[5], vs, sizeof vs, 5);
    uint32_t vb[6] = {2, 1, 1, 4, 3, 2};   /* x2 y1 z1, 4x3x2 texels, pitch 16, 3 rows per slice */
    CHECK(t_update(vol, 0, 0, vb, vs, 2 * 3 * 16, 16, 128) == 0);
    for (int z = 0; z < 2; z++) for (int y = 0; y < 3; y++) memcpy(vm + ((1 + z) * 4 + 1 + y) * 8 + 2, vs + (z * 3 + y) * 16, 4);
    CHECK(t_read(vol, 0, 0, vw, vg, sizeof vg, 8) == 0 && !memcmp(vg, vm, sizeof vm));
    uint8_t one[8 * 4];
    uint32_t sl[6] = {0, 0, 2, 8, 4, 1};
    CHECK(t_read(vol, 0, 0, sl, one, sizeof one, 8) == 0 && !memcmp(one, vm + 2 * 32, 32));
    /* cube face 3 only */
    uint32_t cube = t_image(NxImgType_Cube, NxFmt_RGBA8, 4, 4, 1, 1, 1);
    uint32_t cb[6] = {0, 0, 0, 4, 4, 1};
    uint8_t cz[64] = {0}, cp[64];
    for (uint32_t face = 0; face < 6; face++) CHECK(t_update(cube, 0, face, cb, cz, 64, 16, 0) == 0);
    t_pattern(f, cp, 16, 31);
    CHECK(t_update(cube, 0, 3, cb, cp, 64, 16, 0) == 0);
    for (uint32_t face = 0; face < 6; face++) {
        uint8_t* g3 = t_read_all(cube, 0, face, 4, 4, 1, f);
        CHECKM(g3 && !memcmp(g3, face == 3 ? cp : cz, 64), "cube face %u", face);
        free(g3);
    }
}

void part_ab(char which) { if (which == 'a') part_a(); else part_b(); }
