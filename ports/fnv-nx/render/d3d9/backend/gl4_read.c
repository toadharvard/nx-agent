/* fnv-nx L6 GL 4.5 backend v4: reading images back. image_read returns the native bytes of any level / layer / rect
 * (layouts: gl4_fmt.c); a multisampled source is resolved first (sample-average for colour, sample 0 for depth and
 * stencil: what glBlitFramebuffer resolves to). v3 readback (RGBA8 / RGBA32F / Depth32F / Stencil8 of a 2D image,
 * level 0) is kept with its semantics. src.image 0 = the swapchain image: RGBA8, top row first, level 0 (what the last
 * present / pass wrote; all zero before the first write); no swapchain = BAD_CALL. Submits are synchronous, so everything submitted is complete here. */
#include "gl4.h"
#include <stdlib.h>
#include <string.h>

static void pack_defaults(void) {
    p_glPixelStorei(GL_PACK_ALIGNMENT, 1);
    p_glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    p_glPixelStorei(GL_PACK_IMAGE_HEIGHT, 0);
    p_glPixelStorei(GL_PACK_SKIP_PIXELS, 0);
    p_glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    p_glPixelStorei(GL_PACK_SKIP_IMAGES, 0);
}

/* NxImageRead: cube images read face `src.layer` (z = 0, d = 1); 3D images read slices z..z+d-1 (src.layer = 0);
 * rows of the destination are rowPitch bytes apart, slices rows * rowPitch apart. */
uint64_t gl4_api_image_read(uint64_t a) {
    const NxImageRead* r = (const NxImageRead*)nxgl4_guest_ptr(a, sizeof *r);
    if (!r) return NXGPU_BAD_CALL;
    const Gl4Image* im = gl4_img(r->src.image ? r->src.image : g4.swapImage);   /* image 0 = the swapchain image (nxgpu4.h) */
    if (im && im->isView) im = gl4_img(im->base);
    uint32_t level = r->src.level, layer = r->src.layer;
    if (!im || level >= im->mips) { gl4_err("image_read: bad image %u level %u", r->src.image, level); return NXGPU_BAD_CALL; }
    Gl4Fmt f = gl4_fmt(im->fmt);
    int is3d = im->type == NxImgType_3D, cube = im->type == NxImgType_Cube;
    uint32_t lw = gl4_lvl(im->w, level), lh = gl4_lvl(im->h, level), ld = gl4_lvl(im->d, level);
    if (layer >= (cube ? 6u : 1u) || (!is3d && (r->z != 0 || r->d != 1)) || !r->w || !r->h || !r->d ||
        (uint64_t)r->x + r->w > lw || (uint64_t)r->y + r->h > lh || (uint64_t)r->z + r->d > ld) {
        gl4_err("image_read: box outside image %u level %u", r->src.image, level);
        return NXGPU_BAD_CALL;
    }
    if (f.compressed && ((r->x | r->y) & 3 || ((r->w & 3) && r->x + r->w != lw) || ((r->h & 3) && r->y + r->h != lh))) {
        gl4_err("image_read: box not block aligned");
        return NXGPU_BAD_CALL;
    }
    size_t rb = gl4_row_bytes(&f, r->w), rows = gl4_rows(&f, r->h), tight = rb * rows * r->d;
    uint64_t pitch = r->rowPitch, need = ((uint64_t)r->d * rows - 1) * pitch + rb;
    if (!r->dst || pitch < rb || need > r->dst_size) { gl4_err("image_read: bad destination"); return NXGPU_BAD_CALL; }
    uint8_t* dst = (uint8_t*)nxgl4_guest_ptr(r->dst, r->dst_size);
    uint8_t* buf = (uint8_t*)malloc(tight);
    if (!buf) { gl4_err("image_read: out of memory"); return NXGPU_BAD_CALL; }
    GLuint tex = im->tex, tmp = 0;
    if (im->samples > 1) { tmp = gl4_resolve_tex(im); tex = tmp; }
    GLint zo = cube ? (GLint)layer : (GLint)r->z;
    while (p_glGetError()) {}
    pack_defaults();
    if (f.compressed) p_glGetCompressedTextureSubImage(tex, (GLint)level, (GLint)r->x, (GLint)r->y, zo, (GLsizei)r->w,
                                                       (GLsizei)r->h, (GLsizei)r->d, (GLsizei)tight, buf);
    else p_glGetTextureSubImage(tex, (GLint)level, (GLint)r->x, (GLint)r->y, zo, (GLsizei)r->w, (GLsizei)r->h,
                                (GLsizei)r->d, f.format, f.type, (GLsizei)tight, buf);
    if (tmp) p_glDeleteTextures(1, &tmp);
    GLenum e = p_glGetError();
    if (e) { free(buf); gl4_err("image_read: GL error 0x%x", e); return NXGPU_BAD_CALL; }
    for (size_t s = 0; s < (size_t)r->d * rows; s++) memcpy(dst + s * pitch, buf + s * rb, rb);
    free(buf);
    return 0;
}

/* v3 NxReadback (image 0 = the swapchain image): level 0 of a 2D image */
uint64_t gl4_api_readback(uint64_t a) {
    const NxReadback* r = (const NxReadback*)nxgl4_guest_ptr(a, sizeof *r);
    if (!r || !r->dst || r->format > NxRb_Stencil8) return NXGPU_BAD_CALL;
    const Gl4Image* im = gl4_img(r->image ? r->image : g4.swapImage);
    if (im && im->isView) im = gl4_img(im->base);
    if (!im || im->type != NxImgType_2D) return NXGPU_BAD_CALL;
    int depthFmt = r->format == NxRb_Depth32F || r->format == NxRb_Stencil8;
    if (depthFmt != im->isDepth || (r->format == NxRb_Stencil8 && !im->hasStencil)) return NXGPU_BAD_CALL;
    if ((uint64_t)r->w * r->h * nx_readback_bpp(r->format) > r->dst_size || (uint64_t)r->x + r->w > im->w || (uint64_t)r->y + r->h > im->h)
        return NXGPU_BAD_CALL;
    GLuint tmp = 0;
    Gl4Image src = *im;
    if (im->samples > 1) { tmp = gl4_resolve_tex(im); src.tex = tmp; src.target = GL_TEXTURE_2D; src.samples = 1; }
    if (!g4.blitFbo[0]) p_glCreateFramebuffers(2, g4.blitFbo);
    GLuint fbo = g4.blitFbo[0];
    p_glNamedFramebufferTexture(fbo, GL_COLOR_ATTACHMENT0, 0, 0);
    p_glNamedFramebufferTexture(fbo, GL_DEPTH_STENCIL_ATTACHMENT, 0, 0);
    p_glNamedFramebufferTexture(fbo, GL_DEPTH_ATTACHMENT, 0, 0);
    gl4_attach(fbo, im->isDepth ? (gl4_fmt(im->fmt).glds ? GL_DEPTH_STENCIL_ATTACHMENT : GL_DEPTH_ATTACHMENT) : GL_COLOR_ATTACHMENT0, &src, 0, 0);
    p_glNamedFramebufferReadBuffer(fbo, im->isDepth ? GL_NONE : GL_COLOR_ATTACHMENT0);
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo);
    pack_defaults();
    GLenum fmt = GL_RGBA, type = GL_UNSIGNED_BYTE;
    switch ((enum NxReadbackFormat)r->format) {
    case NxRb_RGBA8: break;
    case NxRb_RGBA32F: type = GL_FLOAT; break;
    case NxRb_Depth32F: fmt = GL_DEPTH_COMPONENT; type = GL_FLOAT; break;
    case NxRb_Stencil8: fmt = GL_STENCIL_INDEX; break;
    }
    while (p_glGetError()) {}
    p_glReadPixels((GLint)r->x, (GLint)r->y, (GLsizei)r->w, (GLsizei)r->h, fmt, type, nxgl4_guest_ptr(r->dst, r->dst_size));
    p_glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);
    p_glNamedFramebufferTexture(fbo, GL_COLOR_ATTACHMENT0, 0, 0);
    p_glNamedFramebufferTexture(fbo, GL_DEPTH_STENCIL_ATTACHMENT, 0, 0);
    p_glNamedFramebufferTexture(fbo, GL_DEPTH_ATTACHMENT, 0, 0);
    if (tmp) p_glDeleteTextures(1, &tmp);
    return p_glGetError() ? NXGPU_BAD_CALL : 0;
}
