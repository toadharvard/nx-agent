/* fnv-nx L6 GL 4.5 backend v4: NxOp_Blit and NxOp_GenMips.
 *
 * Blit (outside any pass) is, by rule:
 *   1. identical format, identical rect size, equal sample counts -> glCopyImageSubData: an exact copy of any
 *      format (BCn, depth-stencil, multisampled included);
 *   2. POINT filter (0) with different rect sizes -> gl4_scale_point (gl4_scale.c): destination texel d takes the
 *      source texel floor((d + 0.5) * sw / dw), same in y, any colour or depth-stencil format (identical formats
 *      for depth-stencil); a multisampled source is first resolved;
 *   3. otherwise (linear filter, or equal sizes with a format conversion) a framebuffer blit between the base
 *      textures (no sRGB conversion, raw values: D3D9 StretchRect): colour with the linear filter (GL's bilinear
 *      at the destination centre) and conversion to the destination format; depth-stencil only with the same
 *      format; a multisampled source is first resolved (whole image) into a single-sample texture. A
 *      multisampled destination is not a blit target.
 * Rects use the texel space of the subresource (row 0 = top) on both sides; dst image 0 = the swapchain image.
 * GenMips(image, filter) = for every level l >= 1 and layer: Blit(level l - 1 whole -> level l whole, filter): point
 * is the rule above (an exact 2:1 takes the texel 2d + 1), linear is the 2x2 box for halving. A 3D image takes the
 * slice floor((2z + 1) * srcDepth / (2 * dstDepth)) of level l - 1 for slice z of level l (point rule on z) and, with
 * the linear filter, uses glGenerateTextureMipmap (GL leaves its filter implementation defined: open point). */
#include "gl4.h"
#include <string.h>

#define NEED(n, name) do { if (argc < (n)) { gl4_err(#name ": %u words, need %u", argc, (unsigned)(n)); return; } } while (0)

void gl4_attach(GLuint fbo, GLenum att, const Gl4Image* im, uint32_t level, uint32_t layer) {
    if (im->target == GL_TEXTURE_CUBE_MAP || im->target == GL_TEXTURE_3D)
        p_glNamedFramebufferTextureLayer(fbo, att, im->tex, (GLint)level, (GLint)layer);
    else
        p_glNamedFramebufferTexture(fbo, att, im->tex, (GLint)level);
}

void gl4_detach_all(GLuint fbo) {
    p_glNamedFramebufferTexture(fbo, GL_COLOR_ATTACHMENT0, 0, 0);
    p_glNamedFramebufferTexture(fbo, GL_DEPTH_ATTACHMENT, 0, 0);
    p_glNamedFramebufferTexture(fbo, GL_DEPTH_STENCIL_ATTACHMENT, 0, 0);
}

typedef Gl4Sub Sub;

/* the framebuffer blit of rule 2 (src already single-sample) */
static void fbo_blit(const Sub* s, const Sub* d, uint32_t filter) {
    if (!g4.blitFbo[0]) p_glCreateFramebuffers(2, g4.blitFbo);
    const Gl4Fmt f = gl4_fmt(s->im->fmt);
    GLenum att = f.depth ? (f.glds ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT) : GL_COLOR_ATTACHMENT0;
    GLbitfield mask = f.depth ? GL_DEPTH_BUFFER_BIT | (f.stencil ? GL_STENCIL_BUFFER_BIT : 0) : GL_COLOR_BUFFER_BIT;
    gl4_detach_all(g4.blitFbo[0]); gl4_detach_all(g4.blitFbo[1]);
    gl4_attach(g4.blitFbo[0], att, s->im, s->level, s->layer);
    gl4_attach(g4.blitFbo[1], att, d->im, d->level, d->layer);
    p_glNamedFramebufferReadBuffer(g4.blitFbo[0], f.depth ? GL_NONE : GL_COLOR_ATTACHMENT0);
    p_glNamedFramebufferDrawBuffer(g4.blitFbo[1], f.depth ? GL_NONE : GL_COLOR_ATTACHMENT0);
    gl4_neutral_state();
    p_glBlitNamedFramebuffer(g4.blitFbo[0], g4.blitFbo[1], (GLint)s->x, (GLint)s->y, (GLint)(s->x + s->w), (GLint)(s->y + s->h),
                             (GLint)d->x, (GLint)d->y, (GLint)(d->x + d->w), (GLint)(d->y + d->h), mask,
                             !f.depth && filter ? GL_LINEAR : GL_NEAREST);
    gl4_detach_all(g4.blitFbo[0]); gl4_detach_all(g4.blitFbo[1]);
}

GLuint gl4_resolve_tex(const Gl4Image* im) {
    Gl4Fmt f = gl4_fmt(im->fmt);
    GLuint tex;
    p_glCreateTextures(GL_TEXTURE_2D, 1, &tex);
    p_glTextureStorage2D(tex, 1, f.internal, im->w, im->h);
    Gl4Image t = *im;
    t.tex = tex; t.target = GL_TEXTURE_2D; t.samples = 1;
    Sub s = {im, 0, 0, 0, 0, im->w, im->h}, d = {&t, 0, 0, 0, 0, im->w, im->h};
    fbo_blit(&s, &d, 0);
    return tex;
}

static void copy_sub(const Sub* s, const Sub* d) {
    GLint sz = s->im->target == GL_TEXTURE_3D || s->im->target == GL_TEXTURE_CUBE_MAP ? (GLint)s->layer : 0;
    GLint dz = d->im->target == GL_TEXTURE_3D || d->im->target == GL_TEXTURE_CUBE_MAP ? (GLint)d->layer : 0;
    p_glCopyImageSubData(s->im->tex, s->im->target, (GLint)s->level, (GLint)s->x, (GLint)s->y, sz, d->im->tex, d->im->target,
                         (GLint)d->level, (GLint)d->x, (GLint)d->y, dz, (GLsizei)s->w, (GLsizei)s->h, 1);
}

/* validated sub-resources -> rule 1 or 2 */
static void blit_sub(const Sub* s, const Sub* d, uint32_t filter) {
    const Gl4Fmt sf = gl4_fmt(s->im->fmt), df = gl4_fmt(d->im->fmt);
    if (d->im->samples > 1) { gl4_err("Blit: multisampled destination"); return; }
    if (s->im->fmt == d->im->fmt && s->w == d->w && s->h == d->h && s->im->samples == d->im->samples) { copy_sub(s, d); return; }
    if (sf.compressed || df.compressed) { gl4_err("Blit: compressed formats only copy (same format and size)"); return; }
    if (sf.depth != df.depth || (sf.depth && s->im->fmt != d->im->fmt)) {
        gl4_err("Blit: depth-stencil blit needs identical formats (%u -> %u)", s->im->fmt, d->im->fmt);
        return;
    }
    if (s->im->samples > 1) {
        GLuint tmp = gl4_resolve_tex(s->im);
        Gl4Image t = *s->im;
        t.tex = tmp; t.target = GL_TEXTURE_2D; t.samples = 1;
        Sub rs = *s;
        rs.im = &t;
        if (filter == 0 && (s->w != d->w || s->h != d->h)) gl4_scale_point(&rs, d, NULL); else fbo_blit(&rs, d, filter);
        p_glDeleteTextures(1, &tmp);
        return;
    }
    if (filter == 0 && (s->w != d->w || s->h != d->h)) { gl4_scale_point(s, d, NULL); return; }
    fbo_blit(s, d, filter);
}

static const Gl4Image* base_of(const Gl4Image* im) { return im && im->isView ? gl4_img(im->base) : im; }

static int sub_ok(const Gl4Image* im, uint32_t level, uint32_t layer, uint32_t x, uint32_t y, uint32_t w, uint32_t h) {
    uint32_t layers = im->type == NxImgType_Cube ? 6 : im->type == NxImgType_3D ? gl4_lvl(im->d, level) : 1;
    return level < im->mips && layer < layers && w && h && (uint64_t)x + w <= gl4_lvl(im->w, level) &&
           (uint64_t)y + h <= gl4_lvl(im->h, level);
}

/* NxOp_Blit: src image, src level | layer << 16, sx, sy, sw, sh, dst image, dst level | layer << 16, dx, dy, dw, dh, filter */
void gl4_op_blit(const uint32_t* a, uint32_t argc) {
    NEED(13, Blit);
    const Gl4Image* si = base_of(gl4_img(a[0]));
    const Gl4Image* di = base_of(gl4_img(a[6] ? a[6] : g4.swapImage));
    Sub s = {si, a[1] & 0xFFFF, a[1] >> 16, a[2], a[3], a[4], a[5]};
    Sub d = {di, a[7] & 0xFFFF, a[7] >> 16, a[8], a[9], a[10], a[11]};
    if (!si || !di || a[12] > 1 || !sub_ok(si, s.level, s.layer, s.x, s.y, s.w, s.h) || !sub_ok(di, d.level, d.layer, d.x, d.y, d.w, d.h)) {
        gl4_err("Blit: bad source %u or destination %u", a[0], a[6]);
        return;
    }
    while (p_glGetError()) {}
    blit_sub(&s, &d, a[12]);
    GLenum e = p_glGetError();
    if (e) gl4_err("Blit: GL error 0x%x", e);
}

/* NxOp_GenMips: image, filter */
void gl4_op_genmips(const uint32_t* a, uint32_t argc) {
    NEED(2, GenMips);
    const Gl4Image* im = base_of(gl4_img(a[0]));
    if (!im || a[1] > 1 || im->samples > 1) { gl4_err("GenMips: bad image %u / filter %u", a[0], a[1]); return; }
    while (p_glGetError()) {}
    if (im->type == NxImgType_3D && a[1] == 1) {
        p_glGenerateTextureMipmap(im->tex);
    } else {
        for (uint32_t l = 1; l < im->mips; l++) {
            uint32_t layers = im->type == NxImgType_Cube ? 6 : im->type == NxImgType_3D ? gl4_lvl(im->d, l) : 1;
            uint32_t sd = gl4_lvl(im->d, l - 1);
            for (uint32_t z = 0; z < layers; z++) {
                uint32_t zs = im->type == NxImgType_3D ? (uint32_t)(((2ull * z + 1) * sd) / (2ull * layers)) : z;
                Sub s = {im, l - 1, zs, 0, 0, gl4_lvl(im->w, l - 1), gl4_lvl(im->h, l - 1)};
                Sub d = {im, l, z, 0, 0, gl4_lvl(im->w, l), gl4_lvl(im->h, l)};
                blit_sub(&s, &d, a[1]);
            }
        }
    }
    GLenum e = p_glGetError();
    if (e) gl4_err("GenMips: GL error 0x%x", e);
}
