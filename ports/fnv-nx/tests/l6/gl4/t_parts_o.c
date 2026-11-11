/* Part o: the presentation rules P2-P5 of nxgpu4.h. P3 the swapchain image starts all zero; P4 image_read of image 0 (the swapchain image: box,
 * pitch, refusals, after clear / gamma present / cursor present); P2 the software cursor blended onto the presented image (exact integer rule, clipping at
 * every edge, gamma applied to the cursor colour, alpha 0 / 255 / in between, entirely outside = no change, refused sizes, frame file and hook after the cursor);
 * P5 wait_vblank (output 0 only, returns on the modelled 60 Hz grid, strictly after the call, leaves the swapchain image alone). */
#define _GNU_SOURCE
#include "t_ref.h"
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

enum { SW = 16, SH = 16 };

static uint64_t present_c(uint32_t img, const uint16_t* ramp, uint32_t frame, const uint8_t* cur, int cx, int cy, uint32_t cw, uint32_t ch) {
    NxPresent p;
    memset(&p, 0, sizeof p);
    p.image = img; p.sw = 8; p.sh = 6; p.dw = 8; p.dh = 6; p.dx = 4; p.dy = 4; p.interval = 1; p.gamma = (uint64_t)(uintptr_t)ramp; p.frame = frame;
    p.cursor = (uint64_t)(uintptr_t)cur; p.cx = cx; p.cy = cy; p.cw = cw; p.ch = ch;
    return t_ext(NXGPU_FN_PRESENT, 1, (uint64_t)(uintptr_t)&p);
}
static void clear_swap(uint32_t rgba) {
    Stream* s = (Stream*)calloc(1, sizeof *s);
    t_op(s, NxOp_BeginPass, 8, 0, 0, NxClear_Color, rgba, 0, 0, SW, SH);
    t_op(s, NxOp_EndPass, 0);
    CHECK(t_submit(s) == 0);
    free(s);
}
static uint64_t read_swap(uint32_t x, uint32_t y, uint32_t w, uint32_t h, void* dst, size_t size, size_t pitch) {
    uint32_t box[6] = {x, y, 0, w, h, 1};
    return t_read(0, 0, 0, box, dst, size, pitch);
}
/* the oracle: round(t / 255) written as floor((2 t + 255) / 510); the cursor colour goes through the ramp first */
static void model_cursor(uint8_t* img, const uint8_t* cur, int cx, int cy, int cw, int ch, const uint16_t* ramp) {
    for (int j = 0; j < ch; j++) for (int i = 0; i < cw; i++) {
        int x = cx + i, y = cy + j;
        if (x < 0 || y < 0 || x >= SW || y >= SH) continue;
        const uint8_t* s = cur + (j * cw + i) * 4;
        int a = s[3];
        for (int c = 0; c < 3; c++) {
            int v = s[2 - c];
            if (ramp) v = ramp[c * 256 + v] >> 8;
            int t = v * a + img[(y * SW + x) * 4 + c] * (255 - a);
            img[(y * SW + x) * 4 + c] = (uint8_t)((2 * t + 255) / 510);
        }
    }
}
static uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec; }
static uint8_t* slurp(const char* path, size_t* n) {
    FILE* f = fopen(path, "rb");
    if (!f) return NULL;
    uint8_t* b = (uint8_t*)malloc(1 << 16);
    *n = fread(b, 1, 1 << 16, f);
    fclose(f);
    return b;
}

void part_o(void) {
    uint8_t zero[SW * SH * 4] = {0}, got[SW * SH * 4], want[SW * SH * 4];
    /* P3 / P4: before any write the swapchain image is all zero, read as RGBA8 top row first */
    CHECK(read_swap(0, 0, SW, SH, got, sizeof got, SW * 4) == 0);
    CHECKM(!memcmp(got, zero, sizeof got), "the swapchain image starts all zero");
    uint8_t big[(SW + 4) * SH * 4];
    memset(big, 0x5A, sizeof big);
    CHECK(read_swap(3, 2, 5, 4, big, sizeof big, (SW + 4) * 4) == 0);
    int ok = 1;
    for (int y = 0; y < 4; y++) for (int i = 0; i < (SW + 4) * 4; i++) ok &= big[y * (SW + 4) * 4 + i] == (i < 20 ? 0 : 0x5A);
    CHECKM(ok, "a sub-box read honours rowPitch and writes nothing outside the box");
    {   /* refusals: outside the image, level, layer, slice, empty, bad destination */
        uint32_t b0[6] = {0, 0, 0, SW + 1, SH, 1}, b1[6] = {1, 0, 0, SW, SH, 1}, b2[6] = {0, 0, 0, 4, 4, 2}, b3[6] = {0, 0, 1, 4, 4, 1}, b4[6] = {0, 0, 0, 0, 4, 1}, ok4[6] = {0, 0, 0, 4, 4, 1};
        CHECK(t_read(0, 0, 0, b0, got, sizeof got, SW * 4 + 4) == NXGPU_BAD_CALL);
        CHECK(t_read(0, 0, 0, b1, got, sizeof got, SW * 4) == NXGPU_BAD_CALL);
        CHECK(t_read(0, 1, 0, ok4, got, sizeof got, 16) == NXGPU_BAD_CALL);
        CHECK(t_read(0, 0, 1, ok4, got, sizeof got, 16) == NXGPU_BAD_CALL);
        CHECK(t_read(0, 0, 0, b2, got, sizeof got, 16) == NXGPU_BAD_CALL);
        CHECK(t_read(0, 0, 0, b3, got, sizeof got, 16) == NXGPU_BAD_CALL);
        CHECK(t_read(0, 0, 0, b4, got, sizeof got, 16) == NXGPU_BAD_CALL);
        CHECK(t_read(0, 0, 0, ok4, got, 8, 16) == NXGPU_BAD_CALL);
    }
    /* a pass that clears image 0 (v3 style) is what the next read returns */
    clear_swap(0xFF302010u);
    for (int i = 0; i < SW * SH; i++) { want[i * 4] = 0x10; want[i * 4 + 1] = 0x20; want[i * 4 + 2] = 0x30; want[i * 4 + 3] = 0xFF; }
    CHECK(read_swap(0, 0, SW, SH, got, sizeof got, SW * 4) == 0);
    CHECKM(!memcmp(got, want, sizeof got), "image 0 reads what the clear pass wrote (RGBA order)");

    /* the source: 8x6 RGBA8 pattern presented at (4, 4) */
    const TFmt* f = t_fmt(NxFmt_RGBA8);
    uint8_t src[8 * 6 * 4];
    t_pattern(f, src, 48, 21);
    uint32_t S = t_image(NxImgType_2D, NxFmt_RGBA8, 8, 6, 1, 1, 1), box[6] = {0, 0, 0, 8, 6, 1};
    CHECK(S && t_update(S, 0, 0, box, src, sizeof src, 32, 0) == 0);
    uint16_t ramp[768];
    for (int i = 0; i < 768; i++) ramp[i] = (uint16_t)(t_hash((uint32_t)i + 5) >> 8);
    /* after a gamma present: image_read of image 0 = the v3 readback = the model */
    clear_swap(0xFF302010u);
    CHECK(present_c(S, ramp, 1, NULL, 0, 0, 0, 0) == 0);
    for (int i = 0; i < SW * SH; i++) { want[i * 4] = 0x10; want[i * 4 + 1] = 0x20; want[i * 4 + 2] = 0x30; want[i * 4 + 3] = 0xFF; }
    for (int j = 0; j < 6; j++) for (int i = 0; i < 8; i++) {
        uint8_t* d = want + ((4 + j) * SW + 4 + i) * 4;
        memcpy(d, src + (j * 8 + i) * 4, 4);
        for (int c = 0; c < 3; c++) d[c] = (uint8_t)(ramp[c * 256 + d[c]] >> 8);
    }
    uint8_t rb[SW * SH * 4];
    CHECK(read_swap(0, 0, SW, SH, got, sizeof got, SW * 4) == 0 && t_readback(0, NxRb_RGBA8, SW, SH, rb, sizeof rb));
    CHECKM(!memcmp(got, want, sizeof got) && !memcmp(rb, got, sizeof got), "front read after a gamma present equals the model and the v3 readback");
    uint8_t keep[SW * SH * 4];
    memcpy(keep, want, sizeof keep);

    /* P2: cursors. 4x4 BGRA: alpha 0, 255, 128 and a gradient */
    uint8_t cur[4 * 4 * 4], cur2[2 * 2 * 4];
    for (int i = 0; i < 16; i++) {
        uint32_t h = t_hash((uint32_t)i * 7 + 3);
        cur[i * 4] = (uint8_t)h; cur[i * 4 + 1] = (uint8_t)(h >> 8); cur[i * 4 + 2] = (uint8_t)(h >> 16);
        cur[i * 4 + 3] = i == 0 ? 0 : i == 1 ? 255 : i == 2 ? 128 : i == 3 ? 1 : i == 4 ? 254 : (uint8_t)(h >> 24);
    }
    for (int i = 0; i < 4; i++) { cur2[i * 4] = (uint8_t)(40 * i); cur2[i * 4 + 1] = 200; cur2[i * 4 + 2] = (uint8_t)(255 - 60 * i); cur2[i * 4 + 3] = 255; }
    const struct { const char* what; const uint8_t* px; int w, h, x, y; int gamma; } cs[] = {
        {"inside", cur, 4, 4, 6, 5, 0}, {"inside with gamma", cur, 4, 4, 6, 5, 1}, {"over the swapchain corner 0,0", cur, 4, 4, 0, 0, 0},
        {"partly left and above", cur, 4, 4, -2, -3, 0}, {"partly right and below", cur, 4, 4, 14, 13, 1}, {"only the last pixel visible", cur, 4, 4, 15, 15, 0},
        {"only the first pixel visible", cur, 4, 4, -3, -3, 0}, {"entirely left", cur, 4, 4, -4, 5, 0}, {"entirely below", cur, 4, 4, 3, 16, 0},
        {"entirely far away", cur, 4, 4, 100000, -100000, 0}, {"2x2 opaque", cur2, 2, 2, 8, 8, 0}, {"2x2 opaque with gamma", cur2, 2, 2, 9, 7, 1},
    };
    char dir[512];
    snprintf(dir, sizeof dir, "%s/gl4cur_XXXXXX", getenv("TMPDIR") ? getenv("TMPDIR") : "/tmp");
    CHECK(mkdtemp(dir) != NULL);
    setenv("NXGL4_FRAMES", dir, 1);
    uint8_t cur_before[sizeof cur];
    memcpy(cur_before, cur, sizeof cur);
    for (unsigned k = 0; k < sizeof cs / sizeof cs[0]; k++) {
        clear_swap(0xFF302010u);
        uint32_t frame = 100 + k, before = g_present_count;
        CHECKM(present_c(S, cs[k].gamma ? ramp : NULL, frame, cs[k].px, cs[k].x, cs[k].y, (uint32_t)cs[k].w, (uint32_t)cs[k].h) == 0, "present with cursor (%s)", cs[k].what);
        memcpy(want, keep, sizeof want);
        if (!cs[k].gamma) {   /* the same present without gamma: rebuild the model */
            for (int i = 0; i < SW * SH; i++) { want[i * 4] = 0x10; want[i * 4 + 1] = 0x20; want[i * 4 + 2] = 0x30; want[i * 4 + 3] = 0xFF; }
            for (int j = 0; j < 6; j++) for (int i = 0; i < 8; i++) memcpy(want + ((4 + j) * SW + 4 + i) * 4, src + (j * 8 + i) * 4, 4);
        }
        model_cursor(want, cs[k].px, cs[k].x, cs[k].y, cs[k].w, cs[k].h, cs[k].gamma ? ramp : NULL);
        CHECK(read_swap(0, 0, SW, SH, got, sizeof got, SW * 4) == 0);
        CHECKM(!memcmp(got, want, sizeof got), "cursor %s: swapchain equals the model, max diff %d", cs[k].what, maxdiff(got, want, sizeof got));
        CHECKM(g_present_count == before + 1 && g_present_last == frame, "cursor %s: hook ran once after the image was complete", cs[k].what);
        char path[600];
        size_t n = 0;
        snprintf(path, sizeof path, "%s/frame_%06u.rgba", dir, frame);
        uint8_t* fb = slurp(path, &n);
        CHECKM(fb && n == sizeof got && !memcmp(fb, want, sizeof got), "cursor %s: the frame file holds the image with the cursor", cs[k].what);
        free(fb);
        unlink(path);
        snprintf(path, sizeof path, "%s/frame_%06u.txt", dir, frame);
        unlink(path);
    }
    CHECKM(!memcmp(cur, cur_before, sizeof cur), "the cursor memory is only read");
    {   /* alpha 0 everywhere = the image of a present without cursor; alpha 255 = the cursor colour (through the ramp) */
        uint8_t z[2 * 2 * 4] = {0};
        for (int i = 0; i < 4; i++) { z[i * 4] = 9; z[i * 4 + 1] = 99; z[i * 4 + 2] = 199; }
        clear_swap(0xFF302010u);
        CHECK(present_c(S, NULL, 200, z, 5, 5, 2, 2) == 0);
        uint8_t a[SW * SH * 4], b[SW * SH * 4];
        CHECK(read_swap(0, 0, SW, SH, a, sizeof a, SW * 4) == 0);
        clear_swap(0xFF302010u);
        CHECK(present_c(S, NULL, 201, NULL, 0, 0, 0, 0) == 0);
        CHECK(read_swap(0, 0, SW, SH, b, sizeof b, SW * 4) == 0);
        CHECKM(!memcmp(a, b, sizeof a), "a fully transparent cursor changes nothing");
    }
    {   /* refused cursors: no change, no hook */
        uint32_t before = g_present_count;
        clear_swap(0xFF302010u);
        CHECK(read_swap(0, 0, SW, SH, keep, sizeof keep, SW * 4) == 0);
        uint8_t* bigc = (uint8_t*)calloc(4097 * 4, 4);
        uint64_t r[] = {present_c(S, NULL, 300, cur, 0, 0, 0, 4), present_c(S, NULL, 301, cur, 0, 0, 4, 0), present_c(S, NULL, 302, bigc, 0, 0, 4097, 1), present_c(S, NULL, 303, bigc, 0, 0, 1, 4097)};
        int allbad = 1;
        for (unsigned i = 0; i < 4; i++) allbad &= r[i] == NXGPU_BAD_CALL;
        free(bigc);
        CHECKM(allbad, "a cursor with a zero or too large size is refused");
        CHECK(read_swap(0, 0, SW, SH, got, sizeof got, SW * 4) == 0);
        CHECKM(!memcmp(got, keep, sizeof got) && g_present_count == before, "refused cursor presents change nothing and call no hook");
    }
    {   /* a cursor with a bad source rect: refused before anything is drawn */
        NxPresent p;
        memset(&p, 0, sizeof p);
        p.image = S; p.sw = 9; p.sh = 6; p.dw = 9; p.dh = 6; p.interval = 1; p.cursor = (uint64_t)(uintptr_t)cur; p.cw = 4; p.ch = 4;
        uint32_t before = g_present_count;
        CHECK(t_ext(NXGPU_FN_PRESENT, 1, (uint64_t)(uintptr_t)&p) == NXGPU_BAD_CALL && g_present_count == before);
    }
    for (unsigned k = 0; k < 12; k++) {
        char path[600];
        snprintf(path, sizeof path, "%s/frame_%06u.rgba", dir, 100 + k); unlink(path);
        snprintf(path, sizeof path, "%s/frame_%06u.txt", dir, 100 + k); unlink(path);
    }
    rmdir(dir);
    unsetenv("NXGL4_FRAMES");

    /* P5: wait_vblank */
    CHECK(t_ext(NXGPU_FN_WAIT_VBLANK, 1, (uint64_t)1) == NXGPU_BAD_CALL);
    CHECK(t_ext(NXGPU_FN_WAIT_VBLANK, 0) == NXGPU_BAD_CALL);
    clear_swap(0xFF302010u);
    CHECK(read_swap(0, 0, SW, SH, keep, sizeof keep, SW * 4) == 0);
    uint64_t prev = 0;
    for (int k = 0; k < 4; k++) {
        uint64_t t0 = now_ns();
        CHECK(t_ext(NXGPU_FN_WAIT_VBLANK, 1, (uint64_t)0) == 0);
        uint64_t t1 = now_ns(), grid = t1 * 60 / 1000000000ull, at = grid * 1000000000ull / 60;
        CHECKM(t1 > t0 && t1 - t0 <= 16666667ull + 15000000ull, "wait_vblank returns after the call and within one period (%llu ns)", (unsigned long long)(t1 - t0));
        CHECKM(t1 - at < 12000000ull, "wait_vblank returns on the 60 Hz grid (%llu ns after the blank)", (unsigned long long)(t1 - at));
        if (prev) CHECKM(t1 - prev >= 10000000ull, "consecutive blanks are one period apart (%llu ns)", (unsigned long long)(t1 - prev));
        prev = t1;
    }
    CHECK(read_swap(0, 0, SW, SH, got, sizeof got, SW * 4) == 0);
    CHECKM(!memcmp(got, keep, sizeof got), "wait_vblank leaves the swapchain image alone");
}
