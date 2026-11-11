/* Part l: present. Source rect -> destination rect of the swapchain image (point rule when the sizes differ), the
 * gamma ramp (out = ramp[channel][value] >> 8) against a CPU reference, converted source formats, a multisampled
 * source, the interval contract, refused presents, the NXGL4_FRAMES frame files and the nxgl4_on_present hook. */
#define _GNU_SOURCE
#include "t_ref.h"
#include <sys/stat.h>
#include <unistd.h>

enum { SW = 16, SH = 16 };

static uint64_t present(uint32_t img, int sx, int sy, int sw, int sh, int dx, int dy, int dw, int dh, uint32_t interval, const uint16_t* ramp, uint32_t frame) {
    NxPresent p = {img, sx, sy, sw, sh, dx, dy, dw, dh, interval, (uint64_t)(uintptr_t)ramp, frame, 0};
    return t_ext(NXGPU_FN_PRESENT, 1, (uint64_t)(uintptr_t)&p);
}

/* the oracle: the presented swapchain image (RGBA bytes) from the logical RGBA source */
static void model(uint8_t* swap, const uint8_t* src, int srcw, int sx, int sy, int sw, int sh, int dx, int dy, int dw, int dh, const uint16_t* ramp) {
    for (int j = 0; j < dh; j++) for (int i = 0; i < dw; i++) {
        int x = sx + (2 * i + 1) * sw / (2 * dw), y = sy + (2 * j + 1) * sh / (2 * dh);
        uint8_t* d = swap + ((dy + j) * SW + dx + i) * 4;
        memcpy(d, src + (y * srcw + x) * 4, 4);
        if (ramp) for (int c = 0; c < 3; c++) d[c] = (uint8_t)(ramp[c * 256 + d[c]] >> 8);
    }
}

static uint8_t* slurp(const char* path, size_t* n) {
    FILE* f = fopen(path, "rb");
    if (!f) return NULL;
    uint8_t* b = (uint8_t*)malloc(1 << 20);
    *n = fread(b, 1, 1 << 20, f);
    fclose(f);
    return b;
}

static void clear_swap(uint32_t rgba) {
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_BeginPass, 8, 0, 0, NxClear_Color, rgba, 0, 0, SW, SH);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    free(s);
}

static uint32_t make_src(uint32_t fmt, int w, int h, uint32_t seed, uint8_t** keep) {
    const TFmt* f = t_fmt(fmt);
    uint32_t img = t_image(NxImgType_2D, fmt, (uint32_t)w, (uint32_t)h, 1, 1, 1), box[6] = {0, 0, 0, (uint32_t)w, (uint32_t)h, 1};
    uint8_t* p = (uint8_t*)malloc((size_t)w * h * f->bpp);
    t_pattern(f, p, (size_t)w * h, seed);
    CHECK(t_update(img, 0, 0, box, p, (size_t)w * h * f->bpp, (size_t)w * f->bpp, 0) == 0);
    *keep = p;
    return img;
}

void part_l(void) {
    char dir[512];
    snprintf(dir, sizeof dir, "%s/gl4frames_XXXXXX", getenv("TMPDIR") ? getenv("TMPDIR") : "/tmp");
    CHECK(mkdtemp(dir) != NULL);
    setenv("NXGL4_FRAMES", dir, 1);
    uint8_t *src, *srcb;
    uint32_t S = make_src(NxFmt_RGBA8, 8, 6, 5, &src), B = make_src(NxFmt_BGRA8, 8, 6, 6, &srcb);
    uint8_t srcb_rgba[8 * 6 * 4];
    for (int i = 0; i < 48; i++) { srcb_rgba[i * 4] = srcb[i * 4 + 2]; srcb_rgba[i * 4 + 1] = srcb[i * 4 + 1]; srcb_rgba[i * 4 + 2] = srcb[i * 4]; srcb_rgba[i * 4 + 3] = srcb[i * 4 + 3]; }
    uint16_t ramp[768], ident[768];
    for (int i = 0; i < 768; i++) { ramp[i] = (uint16_t)(t_hash((uint32_t)i + 77) >> 8); ident[i] = (uint16_t)((i & 255) * 257); }
    uint8_t want[SW * SH * 4], got[SW * SH * 4];
    static const uint8_t bg[4] = {0x10, 0x20, 0x30, 0xFF};
    uint32_t calls = 0;
    struct { const char* what; uint32_t img; const uint8_t* srcpix; int sx, sy, sw, sh, dx, dy, dw, dh; const uint16_t* ramp; uint32_t interval; } cs[] = {
        {"2x scale into a sub-rect", S, src, 1, 1, 6, 4, 4, 2, 12, 8, NULL, 1},
        {"odd ratio 8x6 -> 5x5", S, src, 0, 0, 8, 6, 3, 3, 5, 5, NULL, 0},
        {"1:1 with a random gamma ramp", S, src, 0, 0, 8, 6, 2, 2, 8, 6, ramp, 2},
        {"1:1 with the identity ramp", S, src, 0, 0, 8, 6, 5, 7, 8, 6, ident, 4},
        {"gamma and 2x scale", S, src, 2, 0, 4, 6, 0, 0, 8, 12, ramp, 1},
        {"BGRA8 source, 3:2 up", B, srcb_rgba, 0, 0, 8, 6, 1, 1, 12, 9, NULL, 1},
        {"BGRA8 source with gamma, 5:3 down", B, srcb_rgba, 0, 0, 5, 6, 8, 8, 3, 2, ramp, 3},
    };
    for (unsigned k = 0; k < sizeof cs / sizeof cs[0]; k++) {
        clear_swap(0xFF302010u);
        for (int i = 0; i < SW * SH; i++) memcpy(want + i * 4, bg, 4);
        uint32_t frame = 10 + k;
        uint64_t r = present(cs[k].img, cs[k].sx, cs[k].sy, cs[k].sw, cs[k].sh, cs[k].dx, cs[k].dy, cs[k].dw, cs[k].dh, cs[k].interval, cs[k].ramp, frame);
        calls++;
        CHECKM(r == 0, "present (%s)", cs[k].what);
        model(want, cs[k].srcpix, 8, cs[k].sx, cs[k].sy, cs[k].sw, cs[k].sh, cs[k].dx, cs[k].dy, cs[k].dw, cs[k].dh, cs[k].ramp);
        CHECK(t_readback(0, NxRb_RGBA8, SW, SH, got, sizeof got));
        CHECKM(!memcmp(got, want, sizeof want), "presented image equals the CPU reference (%s), max diff %d", cs[k].what, maxdiff(got, want, sizeof want));
        CHECKM(g_present_count == calls && g_present_last == frame, "on_present called once with frame %u (count %u last %u)", frame, g_present_count, g_present_last);
        char path[600];
        size_t n = 0;
        snprintf(path, sizeof path, "%s/frame_%06u.rgba", dir, frame);
        uint8_t* fb = slurp(path, &n);
        CHECKM(fb && n == sizeof got && !memcmp(fb, got, sizeof got), "frame file %06u holds the presented swapchain image", frame);
        free(fb);
        snprintf(path, sizeof path, "%s/frame_%06u.txt", dir, frame);
        uint8_t* tb = slurp(path, &n);
        CHECKM(tb && n == 6 && !memcmp(tb, "16 16\n", 6), "frame file %06u.txt is \"16 16\"", frame);
        free(tb);
    }
    /* a multisampled source is resolved */
    uint32_t ms = t_image(NxImgType_2D, NxFmt_RGBA8, SW, SH, 1, 1, 4);
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_BeginPass, 8, ms, 0, NxClear_Color, 0xFF0000FFu, 0, 0, SW, SH);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    clear_swap(0xFF302010u);
    CHECK(present(ms, 0, 0, SW, SH, 0, 0, SW, SH, 1, NULL, 30) == 0);
    calls++;
    CHECK(t_readback(0, NxRb_RGBA8, SW, SH, got, sizeof got));
    int red = 1;
    for (int i = 0; i < SW * SH; i++) red &= got[i * 4] == 255 && got[i * 4 + 1] == 0 && got[i * 4 + 2] == 0 && got[i * 4 + 3] == 255;
    CHECKM(red, "multisampled source presented");
    /* refused presents: BAD_CALL, no hook, no file */
    uint32_t before = g_present_count;
    uint64_t bad[] = {
        present(0, 0, 0, 8, 6, 0, 0, 8, 6, 1, NULL, 40), present(S, 0, 0, 9, 6, 0, 0, 8, 6, 1, NULL, 41),
        present(S, -1, 0, 8, 6, 0, 0, 8, 6, 1, NULL, 42), present(S, 0, 0, 8, 6, 10, 10, 8, 8, 1, NULL, 43),
        present(S, 0, 0, 0, 6, 0, 0, 8, 6, 1, NULL, 44), present(S, 0, 0, 8, 6, 0, 0, 8, 6, 5, NULL, 45),
        present(5000, 0, 0, 8, 6, 0, 0, 8, 6, 1, NULL, 46), present(S, 0, 0, 8, 6, 0, 0, 8, -6, 1, NULL, 47),
    };
    int allbad = 1;
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) allbad &= bad[i] == NXGPU_BAD_CALL;
    CHECK(allbad);
    CHECK(t_ext(NXGPU_FN_PRESENT, 1, (uint64_t)0) == NXGPU_BAD_CALL);
    CHECKM(g_present_count == before, "refused presents call no hook");
    char path[600];
    snprintf(path, sizeof path, "%s/frame_%06u.rgba", dir, 41u);
    CHECK(access(path, F_OK) != 0);
    /* without NXGL4_FRAMES: the hook still runs, no file is written */
    unsetenv("NXGL4_FRAMES");
    CHECK(present(S, 0, 0, 8, 6, 0, 0, 8, 6, 1, NULL, 50) == 0 && g_present_count == before + 1 && g_present_last == 50);
    snprintf(path, sizeof path, "%s/frame_%06u.rgba", dir, 50u);
    CHECK(access(path, F_OK) != 0);
    /* a directory that cannot be written is an error of the call, and the hook does not run */
    setenv("NXGL4_FRAMES", "/nonexistent-gl4-frame-dir", 1);
    CHECK(present(S, 0, 0, 8, 6, 0, 0, 8, 6, 1, NULL, 51) == NXGPU_BAD_CALL && g_present_count == before + 1);
    unsetenv("NXGL4_FRAMES");
    for (unsigned k = 0; k < 8; k++) {
        const char* e[2] = {"rgba", "txt"};
        for (int q = 0; q < 2; q++) { snprintf(path, sizeof path, "%s/frame_%06u.%s", dir, k < 7 ? 10 + k : 30, e[q]); unlink(path); }
    }
    rmdir(dir);
    free(src); free(srcb); free(s);
}
