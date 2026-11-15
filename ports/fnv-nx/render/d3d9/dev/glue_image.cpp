// glue_image.cpp: the product definitions of the image hooks declared in res_tex.h (nxd3d_image_create / destroy / update / read, nxd3d_gen_mips) and of nxd3d_preload (obj.h), each as exactly the nxgpu4.h
// call or stream op it names, through glue_stream.h (flush rule R1-R4 there).
//  create   NXGPU_FN_IMAGE_CREATE4 (the descriptor is copied to scratch memory); 0 = the backend refused: out of image ids / video memory, or no memory for what the image's later operations need (glue_stream.h E1, E3: the
//           ring and, for a DEFAULT image, the read-back staging for its largest sub-resource are reserved HERE, so update / read / gen_mips below cannot run out of memory).
//  destroy  R2c: flush when the pending stream references the image, then NXGPU_FN_IMAGE_DESTROY.
//  update   NxOp_ImageUpdate (outside a pass, R1): the box's native texels, rows row_pitch apart, are copied into the stream segment; BCn boxes are in texels, the bytes are blocks. A box whose payload exceeds NXS_MAX_UPDATE
//           (or whose slices are not tight) goes out as several ops over sub-boxes (slices, then rows, then columns, on block boundaries): no op needs memory of its own (E4).
//  read     R3: flush, then NXGPU_FN_IMAGE_READ of the whole sub-resource into the reserved staging block (E3); rows go to dst (row_pitch / slice_pitch); a CONVERT format is converted back to its D3D layout
//           (nx_d3d9_unconvert: A8 alpha, L8 red, R8G8B8 drops the x byte, ...).
//  gen_mips NxOp_GenMips.
//  preload  MANAGED textures / surfaces / volumes: nx_tex_preload (upload of the dirty regions); vertex / index buffers: nx_buf_gpu_sync (glue_buf.cpp). Both paths are one switch on the resource type.
#include "glue_stream.h"
#include "res_tex.h"
#include "glue_buf.h"
#include <string.h>

namespace {
// geometry of the format: unit = texels per block side (1, or 4 for BCn), bytes per unit
struct Geo { uint32_t uw, uh, bytes; uint32_t rb(uint32_t w) const { return (w + uw - 1) / uw * bytes; } uint32_t rows(uint32_t h) const { return (h + uh - 1) / uh; } };
Geo geo(const NxImg* im) { bool c = (im->ni.flags & NXFMT_COMPRESSED) != 0; return {c ? 4u : 1u, c ? 4u : 1u, im->ni.bytes}; }
// E4: one box -> ops of at most NXS_MAX_UPDATE bytes. `data` = first texel of the box, rows `rp` apart, slices `sp` apart. Every piece is a box of the same layout (slices, else rows, else columns; cut on block boundaries).
void update(const NxImg* im, uint32_t image, uint32_t level, uint32_t layer, uint32_t x, uint32_t y, uint32_t z, uint32_t w, uint32_t h, uint32_t d, const uint8_t* data, uint32_t rp, uint32_t sp) {
    Geo g = geo(im); uint32_t rb = g.rb(w), rows = g.rows(h); if (rp < rb) rp = rb;
    uint32_t slice = rows * rp, size = (d - 1) * slice + (rows - 1) * rp + rb, asz = (size + 15u) & ~15u;   // the ABI's layout: slices rows * rowPitch apart
    if (d > 1 && (sp != slice || asz > NXS_MAX_UPDATE)) { for (uint32_t i = 0; i < d; i++) update(im, image, level, layer, x, y, z + i, w, h, 1, data + (size_t)i * sp, rp, sp); return; }
    if (asz > NXS_MAX_UPDATE) {
        if (rows > 1) { uint32_t r0 = rows / 2, h0 = r0 * g.uh; update(im, image, level, layer, x, y, z, w, h0, 1, data, rp, sp); update(im, image, level, layer, x, y + h0, z, w, h - h0, 1, data + (size_t)r0 * rp, rp, sp); return; }
        uint32_t u0 = ((w + g.uw - 1) / g.uw) / 2, w0 = u0 * g.uw;   // one row of more than NXS_MAX_UPDATE bytes: at least two units wide
        update(im, image, level, layer, x, y, z, w0, h, 1, data, rp, sp); update(im, image, level, layer, x + w0, y, z, w - w0, h, 1, data + (size_t)u0 * g.bytes, rp, sp); return;
    }
    if (!nxs_image_update(image, level, layer, x, y, z, w, h, d, data, size, rp)) nxs_error();   // E1: this image's creation reserved the ring; reaching here is a device bug, counted
}
}

uint32_t nxd3d_image_create(NxObj* res, const NxImageDesc4* d) {
    NxImg* im = nx_img_of(res); if (!im || !nxs_reserve()) return 0;                                             // E1
    if (im->pool == D3DPOOL_DEFAULT) {                                                                             // E3: only DEFAULT images are written by the GPU, so only they are read back
        Geo g = geo(im); bool conv = (im->ni.flags & NXFMT_CONVERT) != 0; uint32_t bytes = 0, cb = 0;
        for (uint32_t i = 0; i < im->levels * im->faces; i++) {
            const NxSub* s = &im->sub[i]; uint32_t n = g.rb(s->w) * g.rows(s->h) * s->d; if (n > bytes) bytes = n;
            if (conv) { uint32_t c = s->w * im->ni.srcBytes * s->h + 1; if (c > cb) cb = c; }
        }
        if (!nxs_reserve_readback(bytes, cb)) return 0;
    }
    NxImageDesc4* s = (NxImageDesc4*)nxs_scratch(sizeof *s); if (!s) return 0; *s = *d;
    uint64_t a[1] = {FNVNX_ADDR_(s)}, r = nxd3d_gpu_call(NXGPU_FN_IMAGE_CREATE4, a, 1);
    return nxs_ok(r) && r < NX_MAX_IMAGES ? (uint32_t)r : 0;
}
void nxd3d_image_destroy(NxObj*, uint32_t image) {
    nxs_image_destroyed(image); uint64_t a[1] = {image}; nxd3d_gpu_call(NXGPU_FN_IMAGE_DESTROY, a, 1);
}
void nxd3d_image_update(NxObj* res, uint32_t image, uint32_t level, uint32_t layer, const NxBox3* b, const void* data, uint32_t row_pitch, uint32_t slice_pitch) {
    update(nx_img_of(res), image, level, layer, b->l, b->t, b->f, b->r - b->l, b->b - b->t, b->k - b->f, (const uint8_t*)data, row_pitch, slice_pitch);
}
void nxd3d_image_read(NxObj* res, uint32_t image, uint32_t level, uint32_t layer, void* dst, uint32_t row_pitch, uint32_t slice_pitch) {
    NxImg* im = nx_img_of(res); const NxSub* s = im->at(level, im->kind == NXK_VOLUME ? 0 : layer); Geo g = geo(im);
    uint32_t rb = g.rb(s->w), rws = g.rows(s->h), bytes = rb * rws * s->d;
    bool conv = (im->ni.flags & NXFMT_CONVERT) != 0; uint32_t ob = conv ? s->w * im->ni.srcBytes : rb, orows = conv ? s->h : rws;
    uint8_t* st = nxs_staging(); NxImageRead* rd = (NxImageRead*)nxs_scratch(sizeof *rd); uint8_t* t = nxs_conv();
    if (!st || !rd || (conv && !t) || (((bytes + 4095u) & ~4095u) > nxs_staging_size())) { nxs_error(); return; }   // E3 / E1: reserved by nxd3d_image_create of every DEFAULT image; reaching here is a device bug, counted
    nxs_flush();                                                                                          // R3
    memset(rd, 0, sizeof *rd);
    rd->src.image = image; rd->src.level = (uint16_t)level; rd->src.layer = (uint16_t)(im->kind == NXK_VOLUME ? 0 : layer); rd->w = s->w; rd->h = s->h; rd->d = s->d;
    rd->dst = FNVNX_ADDR_(st); rd->dst_size = bytes; rd->rowPitch = rb;
    uint64_t a[1] = {FNVNX_ADDR_(rd)}; nxd3d_gpu_call(NXGPU_FN_IMAGE_READ, a, 1);
    for (uint32_t z = 0; z < s->d; z++) {
        const uint8_t* src = (const uint8_t*)st + (size_t)z * rb * rws; if (conv) { nx_d3d9_unconvert(im->fmt, s->w, s->h, src, rb * rws, t, ob * orows); src = t; }
        for (uint32_t r = 0; r < orows; r++) memcpy((uint8_t*)dst + (size_t)z * slice_pitch + (size_t)r * row_pitch, src + (size_t)r * ob, ob);
    }
}
void nxd3d_gen_mips(NxObj*, uint32_t image, uint32_t filter) { if (!nxs_gen_mips(image, filter)) nxs_error(); }   // E1
void nxd3d_preload(NxObj* res) {
    switch (res->ci->rtype) {
    case D3DRTYPE_VERTEXBUFFER: case D3DRTYPE_INDEXBUFFER: nx_ignore(nx_buf_gpu_sync(static_cast<NxBufCore*>(res))); break;   // PreLoad is void: a mirror that could not be made (0) leaves the dirty range, the next use retries and reports
    default: nx_tex_preload(res); break;
    }
}
