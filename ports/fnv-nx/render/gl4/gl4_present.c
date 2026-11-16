/* fnv-nx L6 GL 4.5 backend v4: present (NxPresent, ext_call NXGPU_FN_PRESENT).
 *
 * The source rect of `image` (a single- or multi-sample colour image, level 0; multisampled sources are resolved
 * first) is written into the destination rect of the swapchain image; the rest of the swapchain image is untouched.
 * When the rect sizes differ the source is sampled with the POINT rule of gl4_scale.c (destination texel d takes the
 * source texel floor((d + 0.5) * sw / dw)); D3D9 leaves the presentation filter to the driver. The source is
 * converted to the 8-bit RGBA swapchain as GL converts (round to nearest). With NxPresent.gamma the present blit
 * applies the ramp per channel: v = round(clamp(channel, 0, 1) * 255) (= the 8-bit channel value of an 8-bit
 * source), out = ramp[channel][v] >> 8; alpha is not changed. The ramp is 3 x 256 u16 (D3DGAMMARAMP: red, green,
 * blue).
 *
 * interval (0-4, else BAD_CALL) is the call contract only: a surfaceless EGL context has no vertical blank, so it has
 * no observable effect on the image; pacing belongs to the embedder, which learns of every present from
 *     void nxgl4_on_present(uint32_t frame);
 * called after the image is complete (and, when the environment variable NXGL4_FRAMES names a directory, after the
 * files <dir>/frame_%06u.rgba (the whole swapchain image, RGBA8, top row first) and <dir>/frame_%06u.txt
 * ("width height") are written; a file that cannot be written makes the call BAD_CALL and no hook runs).
 *
 * The software cursor (NxPresent.cursor, rule P2 of nxgpu4.h, Wine 9.0 cs.c wined3d_cs_exec_present) is stamped into the SOURCE image before the present blit (gl4_scale.c gl4_cursor_stamp): the
 * cursor texels whose alpha is not 0 replace the image's texels, so the gamma ramp of the blit applies to them and the image keeps the cursor. A cursor entirely outside the image changes nothing.
 *
 * NXGPU_FN_WAIT_VBLANK: GL on a surfaceless context has no display. The host reference models the output as a 60 Hz
 * panel whose vertical blanks are the instants k * 10^9 / 60 ns (k integer) of CLOCK_MONOTONIC; the call returns at the
 * first blank strictly after the instant of the call. */
#define _GNU_SOURCE
#include "gl4.h"
#include <stdio.h>
#include <errno.h>
#include <time.h>
#include <stdlib.h>
#include <string.h>

static int in_rect(int64_t x, int64_t y, int64_t w, int64_t h, uint32_t iw, uint32_t ih) {
    return x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= iw && y + h <= ih;
}

/* the swapchain image starts as RGBA (0, 0, 0, 0) (nxgpu4.h: contents of the swapchain image) */
int gl4_swap_zero(void) {
    const Gl4Image* sw = g4.swapImage ? gl4_img(g4.swapImage) : NULL;
    if (!sw) return 0;
    uint8_t* z = (uint8_t*)calloc((size_t)sw->w * sw->h, 4);
    if (!z) return 1;
    while (p_glGetError()) {}
    p_glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    p_glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
    p_glTextureSubImage2D(sw->tex, 0, 0, 0, sw->w, sw->h, GL_RGBA, GL_UNSIGNED_BYTE, z);
    free(z);
    return p_glGetError() != 0;
}

static int write_frame(const char* dir, uint32_t frame, const Gl4Image* sw) {
    size_t n = (size_t)sw->w * sw->h * 4;
    uint8_t* buf = (uint8_t*)malloc(n);
    char path[1024];
    if (!buf) return 1;
    p_glPixelStorei(GL_PACK_ALIGNMENT, 1);
    p_glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    p_glGetTextureSubImage(sw->tex, 0, 0, 0, 0, sw->w, sw->h, 1, GL_RGBA, GL_UNSIGNED_BYTE, (GLsizei)n, buf);
    int bad = p_glGetError() != 0;
    snprintf(path, sizeof path, "%s/frame_%06u.rgba", dir, frame);
    FILE* f = bad ? NULL : fopen(path, "wb");
    if (f) { bad = fwrite(buf, 1, n, f) != n; bad |= fclose(f) != 0; } else bad = 1;
    snprintf(path, sizeof path, "%s/frame_%06u.txt", dir, frame);
    f = bad ? NULL : fopen(path, "wb");
    if (f) { bad = fprintf(f, "%u %u\n", sw->w, sw->h) < 0; bad |= fclose(f) != 0; } else bad = 1;
    free(buf);
    return bad;
}

uint64_t gl4_api_present(uint64_t a) {
    const NxPresent* p = (const NxPresent*)nxgl4_guest_ptr(a, sizeof *p);
    if (!p) return NXGPU_BAD_CALL;
    const Gl4Image* src = p->image <= NX_MAX_IMAGES ? gl4_img(p->image) : NULL;
    const Gl4Image* sw = g4.swapImage ? gl4_img(g4.swapImage) : NULL;
    const uint16_t* ramp = p->gamma ? (const uint16_t*)nxgl4_guest_ptr(p->gamma, 3 * 256 * sizeof(uint16_t)) : NULL;
    const uint8_t* cur = p->cursor && p->cw && p->ch && p->cw <= 4096 && p->ch <= 4096 ? (const uint8_t*)nxgl4_guest_ptr(p->cursor, (uint64_t)p->cw * p->ch * 4) : NULL;
    if (!src || (p->cursor && !cur) || src->isView || src->type != NxImgType_2D || src->isDepth || gl4_fmt(src->fmt).compressed || !sw ||
        !in_rect(p->sx, p->sy, p->sw, p->sh, src->w, src->h) || !in_rect(p->dx, p->dy, p->dw, p->dh, sw->w, sw->h) ||
        p->interval > 4 || (p->gamma && !ramp)) {
        gl4_err("present: bad image %u, rects, interval %u or gamma ramp", p->image, p->interval);
        return NXGPU_BAD_CALL;
    }
    if (cur && gl4_cursor_stamp(src, p->cx, p->cy, p->cw, p->ch, cur)) { gl4_err("present: cursor"); return NXGPU_BAD_CALL; }
    Gl4Image t = *src;
    GLuint tmp = 0;
    if (src->samples > 1) { tmp = gl4_resolve_tex(src); t.tex = tmp; t.target = GL_TEXTURE_2D; t.samples = 1; }
    Gl4Sub s = {&t, 0, 0, (uint32_t)p->sx, (uint32_t)p->sy, (uint32_t)p->sw, (uint32_t)p->sh};
    Gl4Sub d = {sw, 0, 0, (uint32_t)p->dx, (uint32_t)p->dy, (uint32_t)p->dw, (uint32_t)p->dh};
    while (p_glGetError()) {}
    gl4_scale_point(&s, &d, ramp);
    if (tmp) p_glDeleteTextures(1, &tmp);
    p_glFinish();
    if (p_glGetError()) { gl4_err("present: GL error"); return NXGPU_BAD_CALL; }
    const char* dir = getenv("NXGL4_FRAMES");
    if (dir && *dir && write_frame(dir, p->frame, sw)) { gl4_err("present: cannot write the frame file in %s", dir); return NXGPU_BAD_CALL; }
    nxgl4_on_present(p->frame);
    return 0;
}

uint64_t gl4_api_wait_vblank(uint64_t output) {
    if (output != 0 || !g4.swapImage) { gl4_err("wait_vblank: output %llu", (unsigned long long)output); return NXGPU_BAD_CALL; }
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    uint64_t ns = (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec, k = ns * 60 / 1000000000ull + 1, at = k * 1000000000ull / 60;
    if (at <= ns) at++;   /* integer rounding of the blank instant: strictly after the call */
    t.tv_sec = (time_t)(at / 1000000000ull); t.tv_nsec = (long)(at % 1000000000ull);
    while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, NULL) == EINTR) {}
    return 0;
}
