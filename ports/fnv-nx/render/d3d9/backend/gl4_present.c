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
 * ("width height") are written; a file that cannot be written makes the call BAD_CALL and no hook runs). */
#include "gl4.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int in_rect(int64_t x, int64_t y, int64_t w, int64_t h, uint32_t iw, uint32_t ih) {
    return x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= iw && y + h <= ih;
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
    if (!src || src->isView || src->type != NxImgType_2D || src->isDepth || gl4_fmt(src->fmt).compressed || !sw ||
        !in_rect(p->sx, p->sy, p->sw, p->sh, src->w, src->h) || !in_rect(p->dx, p->dy, p->dw, p->dh, sw->w, sw->h) ||
        p->interval > 4 || (p->gamma && !ramp)) {
        gl4_err("present: bad image %u, rects, interval %u or gamma ramp", p->image, p->interval);
        return NXGPU_BAD_CALL;
    }
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
