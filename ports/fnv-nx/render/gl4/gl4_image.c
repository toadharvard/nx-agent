/* fnv-nx L6 GL 4.5 backend v4: images. Ids are reused only after image_destroy; an image whose format has an sRGB
 * view occupies two consecutive ids (id, id + 1 = the view). Subresource = (level, layer): layer is the cube face
 * or, for a 3D image, addressed by z in ImageUpdate / blits (NxTarget.layer is the z slice there). */
#include "gl4.h"
#include <stdlib.h>
#include <string.h>

#ifndef GL_TEXTURE_MAX_LEVEL
#define GL_TEXTURE_MAX_LEVEL 0x813D
#endif

Gl4Image* gl4_img(uint32_t id) {
    if (!id || id > NX_MAX_IMAGES + 1 || !g4.img[id].used) return NULL;
    return &g4.img[id];
}

uint32_t gl4_view_of(uint32_t id) {
    const Gl4Image* v = id && id <= NX_MAX_IMAGES ? &g4.img[id + 1] : NULL;
    return v && v->used && v->isView && v->base == id ? id + 1 : id;
}

static uint32_t alloc_ids(uint32_t n) {
    for (uint32_t id = 1; id + n - 1 <= NX_MAX_IMAGES; id++) {
        uint32_t k = 0;
        while (k < n && !g4.img[id + k].used) k++;
        if (k == n) return id;
    }
    return 0;
}

typedef struct {
    uint32_t w, h, d, type, fmt, mips, samples, swizzle;
} Make;

static GLenum target_of(const Make* m) {
    switch ((enum NxImageType)m->type) {
    case NxImgType_2D: return m->samples > 1 ? GL_TEXTURE_2D_MULTISAMPLE : GL_TEXTURE_2D;
    case NxImgType_Cube: return GL_TEXTURE_CUBE_MAP;
    case NxImgType_3D: return GL_TEXTURE_3D;
    }
    return 0;
}

static uint32_t max_mips(const Make* m) {
    uint32_t e = m->w > m->h ? m->w : m->h, n = 1;
    if (m->type == NxImgType_3D && m->d > e) e = m->d;
    while (e >>= 1) n++;
    return n;
}

/* Uploads the box of one subresource from `src` (rows rowPitch bytes apart, slices rows * rowPitch apart). */
static void upload_box(const Gl4Image* im, uint32_t level, uint32_t layer, const uint32_t box[6], const uint8_t* src,
                       size_t rowPitch) {
    const Gl4Fmt f = gl4_fmt(im->fmt);
    uint32_t x = box[0], y = box[1], z = box[2], w = box[3], h = box[4], d = box[5];
    size_t rb = gl4_row_bytes(&f, w), rows = gl4_rows(&f, h);
    uint8_t* tight = NULL;
    if (rowPitch != rb) {
        tight = (uint8_t*)malloc(rb * rows * d);
        if (!tight) { gl4_err("image_update: out of memory"); return; }
        for (size_t s = 0; s < d; s++)
            for (size_t r = 0; r < rows; r++) memcpy(tight + (s * rows + r) * rb, src + (s * rows + r) * rowPitch, rb);
        src = tight;
    }
    size_t bytes = rb * rows * d;
    p_glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    p_glPixelStorei(GL_UNPACK_ROW_LENGTH, 0);
    p_glPixelStorei(GL_UNPACK_IMAGE_HEIGHT, 0);
    p_glPixelStorei(GL_UNPACK_SKIP_PIXELS, 0);
    p_glPixelStorei(GL_UNPACK_SKIP_ROWS, 0);
    p_glPixelStorei(GL_UNPACK_SKIP_IMAGES, 0);
    if (im->target == GL_TEXTURE_2D) {
        if (f.compressed) p_glCompressedTextureSubImage2D(im->tex, (GLint)level, (GLint)x, (GLint)y, (GLsizei)w,
                                                          (GLsizei)h, f.internal, (GLsizei)bytes, src);
        else p_glTextureSubImage2D(im->tex, (GLint)level, (GLint)x, (GLint)y, (GLsizei)w, (GLsizei)h, f.format, f.type, src);
    } else {
        GLint zo = im->target == GL_TEXTURE_CUBE_MAP ? (GLint)layer : (GLint)z;
        GLsizei depth = im->target == GL_TEXTURE_CUBE_MAP ? 1 : (GLsizei)d;
        if (f.compressed) p_glCompressedTextureSubImage3D(im->tex, (GLint)level, (GLint)x, (GLint)y, zo, (GLsizei)w,
                                                          (GLsizei)h, depth, f.internal, (GLsizei)bytes, src);
        else p_glTextureSubImage3D(im->tex, (GLint)level, (GLint)x, (GLint)y, zo, (GLsizei)w, (GLsizei)h, depth,
                                   f.format, f.type, src);
    }
    free(tight);
}

static void release(uint32_t id) {
    for (uint32_t v = id; v <= id + 1 && v <= NX_MAX_IMAGES + 1; v++) {
        if (v == id + 1 && !(g4.img[v].used && g4.img[v].isView && g4.img[v].base == id)) break;
        if (g4.img[v].tex) p_glDeleteTextures(1, &g4.img[v].tex);
        memset(&g4.img[v], 0, sizeof g4.img[v]);
    }
}

/* data: NULL, or tightly packed mips (face-major for a cube); returns the image id, 0 on failure */
static uint32_t create(const Make* m, const uint8_t* data, size_t data_size) {
    Gl4Fmt f = gl4_fmt(m->fmt);
    GLenum target = target_of(m);
    if (!f.internal || !target || !m->w || !m->h || !m->d || m->mips < 1 || m->mips > max_mips(m)) return 0;
    if (m->type != NxImgType_3D && m->d != 1) return 0;
    if (m->type == NxImgType_Cube && m->w != m->h) return 0;
    if (m->samples != 1 && m->samples != 2 && m->samples != 4 && m->samples != 8) return 0;
    if (m->samples > 1 && (m->type != NxImgType_2D || m->mips != 1 || f.compressed)) return 0;
    for (int i = 0; i < 4; i++) if ((m->swizzle >> (4 * i) & 15) > NxSwz_1) return 0;
    int srgb = nx_format_has_srgb(m->fmt) && f.srgb;
    uint32_t id = alloc_ids(srgb ? 2 : 1);
    if (!id) return 0;
    uint32_t layers = m->type == NxImgType_Cube ? 6 : 1;
    if (data) {   /* the whole upload must fit before anything is created */
        size_t need = 0;
        for (uint32_t l = 0; l < m->mips; l++) need += gl4_row_bytes(&f, gl4_lvl(m->w, l)) * gl4_rows(&f, gl4_lvl(m->h, l)) * gl4_lvl(m->d, l);
        if (need * layers > data_size) return 0;
    }
    Gl4Image* im = &g4.img[id];
    memset(im, 0, sizeof *im);
    im->target = target; im->fmt = (uint8_t)m->fmt; im->type = (uint8_t)m->type; im->samples = (uint8_t)m->samples;
    im->mips = (uint8_t)m->mips; im->w = (uint16_t)m->w; im->h = (uint16_t)m->h; im->d = (uint16_t)m->d; im->used = 1;
    im->isDepth = f.depth; im->hasStencil = f.stencil;
    while (p_glGetError()) {}
    p_glCreateTextures(target, 1, &im->tex);
    if (m->samples > 1) p_glTextureStorage2DMultisample(im->tex, (GLsizei)m->samples, f.internal, (GLsizei)m->w, (GLsizei)m->h, GL_TRUE);
    else if (m->type == NxImgType_3D) p_glTextureStorage3D(im->tex, (GLsizei)m->mips, f.internal, (GLsizei)m->w, (GLsizei)m->h, (GLsizei)m->d);
    else p_glTextureStorage2D(im->tex, (GLsizei)m->mips, f.internal, (GLsizei)m->w, (GLsizei)m->h);
    if (p_glGetError()) { release(id); return 0; }
    if (m->samples == 1) p_glTextureParameteri(im->tex, GL_TEXTURE_MAX_LEVEL, (GLint)m->mips - 1);
    uint16_t sw = m->swizzle ? (uint16_t)m->swizzle : NXIMG_SWIZZLE_IDENTITY;
    GLint swz[4] = {gl4_swz(sw & 15), gl4_swz(sw >> 4 & 15), gl4_swz(sw >> 8 & 15), gl4_swz(sw >> 12 & 15)};
    if (!f.depth) p_glTextureParameteriv(im->tex, GL_TEXTURE_SWIZZLE_RGBA, swz);
    for (uint32_t face = 0; data && face < layers; face++)
        for (uint32_t l = 0; l < m->mips; l++) {
            uint32_t box[6] = {0, 0, 0, gl4_lvl(m->w, l), gl4_lvl(m->h, l), gl4_lvl(m->d, l)};
            upload_box(im, l, face, box, data, gl4_row_bytes(&f, box[3]));
            data += gl4_row_bytes(&f, box[3]) * gl4_rows(&f, box[4]) * box[5];
        }
    if (srgb) {
        Gl4Image* v = &g4.img[id + 1];
        *v = *im;
        v->isView = 1; v->base = id;
        p_glGenTextures(1, &v->tex);
        p_glTextureView(v->tex, target, im->tex, f.srgb, 0, m->mips, 0, layers);
        p_glTextureParameteriv(v->tex, GL_TEXTURE_SWIZZLE_RGBA, swz);
    }
    if (p_glGetError()) { release(id); return 0; }
    return id;
}

uint64_t gl4_api_image_create(uint64_t desc) {
    const NxImageDesc* d = (const NxImageDesc*)nxgl4_guest_ptr(desc, sizeof *d);
    if (!d) return 0;
    Make m = {d->width, d->height, 1, d->flags & NxImg_Cube ? NxImgType_Cube : NxImgType_2D, d->format,
              d->mips ? d->mips : 1u, 1, d->swizzle};
    const uint8_t* data = d->data ? (const uint8_t*)nxgl4_guest_ptr(d->data, d->data_size) : NULL;
    return create(&m, data, d->data_size);
}

uint32_t gl4_image_make(const NxImageDesc4* d) {
    Make m = {d->width, d->height, d->depth, d->type, d->format, d->mips, d->samples, d->swizzle};
    return create(&m, NULL, 0);
}

uint64_t gl4_api_image_create4(uint64_t desc) {
    const NxImageDesc4* d = (const NxImageDesc4*)nxgl4_guest_ptr(desc, sizeof *d);
    return d ? gl4_image_make(d) : 0;
}

uint64_t gl4_api_image_destroy(uint64_t id) {
    const Gl4Image* im = id <= NX_MAX_IMAGES ? gl4_img((uint32_t)id) : NULL;
    if (!im || im->isView || id == g4.swapImage) { gl4_err("image_destroy: %llu is not a live image of the device", (unsigned long long)id); return NXGPU_BAD_CALL; }
    gl4_fbo_forget((uint32_t)id);
    release((uint32_t)id);
    return 0;
}

/* NxOp_ImageUpdate: image, level | layer << 16, x, y, z, w, h, d, memBlock, offset, size, rowPitch */
void gl4_op_image_update(const uint32_t* a, uint32_t argc) {
    if (argc < 12) { gl4_err("ImageUpdate: %u words", argc); return; }
    const Gl4Image* im = gl4_img(a[0]);
    uint32_t level = a[1] & 0xFFFF, layer = a[1] >> 16;
    const uint32_t* b = a + 2;   /* x y z w h d */
    if (!im || im->isView || im->samples > 1 || level >= im->mips) { gl4_err("ImageUpdate: bad image %u level %u", a[0], level); return; }
    Gl4Fmt f = gl4_fmt(im->fmt);
    uint32_t lw = gl4_lvl(im->w, level), lh = gl4_lvl(im->h, level), ld = gl4_lvl(im->d, level);
    int is3d = im->type == NxImgType_3D;
    if (layer >= (im->type == NxImgType_Cube ? 6u : 1u) || (!is3d && (b[2] != 0 || b[5] != 1)) || !b[3] || !b[4] || !b[5] ||
        (uint64_t)b[0] + b[3] > lw || (uint64_t)b[1] + b[4] > lh || (uint64_t)b[2] + b[5] > ld) {
        gl4_err("ImageUpdate: box outside image %u level %u layer %u", a[0], level, layer);
        return;
    }
    if (f.compressed && ((b[0] | b[1]) & 3 || ((b[3] & 3) && b[0] + b[3] != lw) || ((b[4] & 3) && b[1] + b[4] != lh))) {
        gl4_err("ImageUpdate: box not block aligned");
        return;
    }
    const Gl4Mem* mem = gl4_mem(a[8]);
    size_t rb = gl4_row_bytes(&f, b[3]), rows = gl4_rows(&f, b[4]);
    uint64_t pitch = a[11], need = ((uint64_t)b[5] * rows - 1) * pitch + rb;
    if (!mem || (uint64_t)a[9] + a[10] > mem->size || pitch < rb || need > a[10]) { gl4_err("ImageUpdate: bad source"); return; }
    const uint8_t* src = (const uint8_t*)nxgl4_guest_ptr(mem->storage + a[9], a[10]);
    uint32_t box[6] = {b[0], b[1], b[2], b[3], b[4], b[5]};
    upload_box(im, level, layer, box, src, pitch);
    if (p_glGetError()) gl4_err("ImageUpdate: GL error");
}
