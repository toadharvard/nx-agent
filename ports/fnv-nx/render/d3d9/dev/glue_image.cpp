// glue_image.cpp: the product definitions of the image hooks declared in res_tex.h (nxd3d_image_create / destroy / update / read, nxd3d_gen_mips) and of nxd3d_preload (obj.h), each as exactly the nxgpu4.h
// call or stream op it names, through glue_stream.h (flush rule R1-R4 there).
//  create   NXGPU_FN_IMAGE_CREATE4 (the descriptor is copied to scratch memory); 0 = the backend refused (a defect: the env table says the format exists).
//  destroy  R2c: flush when the pending stream references the image, then NXGPU_FN_IMAGE_DESTROY.
//  update   NxOp_ImageUpdate (outside a pass, R1): the box's native texels, rows row_pitch apart, are copied into the stream segment; BCn boxes are in texels, the bytes are blocks.
//  read     R3: flush, then NXGPU_FN_IMAGE_READ of the whole sub-resource into a staging block; rows go to dst (row_pitch / slice_pitch); a CONVERT format is converted back to its D3D layout
//           (nx_d3d9_unconvert: A8 alpha, L8 red, R8G8B8 drops the x byte, ...).
//  gen_mips NxOp_GenMips.
//  preload  MANAGED textures / surfaces / volumes: nx_tex_preload (upload of the dirty regions); vertex / index buffers: nx_buf_gpu_sync (glue_buf.cpp). Both paths are one switch on the resource type.
#include "glue_stream.h"
#include "res_tex.h"
#include "glue_buf.h"
#include <string.h>

namespace {
struct Geo { uint32_t rb, rows; };
Geo geo(const NxImg* im, uint32_t w, uint32_t h) { bool c = (im->ni.flags & NXFMT_COMPRESSED) != 0; return {c ? ((w + 3) / 4) * im->ni.bytes : w * im->ni.bytes, c ? (h + 3) / 4 : h}; }
}

uint32_t nxd3d_image_create(NxObj*, const NxImageDesc4* d) {
    NxImageDesc4* s = (NxImageDesc4*)nxs_scratch(sizeof *s); *s = *d;
    uint64_t a[1] = {FNVNX_ADDR_(s)}, r = nxd3d_gpu_call(NXGPU_FN_IMAGE_CREATE4, a, 1);
    return nxs_ok(r) && r < NX_MAX_IMAGES ? (uint32_t)r : 0;
}
void nxd3d_image_destroy(NxObj*, uint32_t image) {
    nxs_image_destroyed(image); uint64_t a[1] = {image}; nxd3d_gpu_call(NXGPU_FN_IMAGE_DESTROY, a, 1);
}
void nxd3d_image_update(NxObj* res, uint32_t image, uint32_t level, uint32_t layer, const NxBox3* b, const void* data, uint32_t row_pitch, uint32_t slice_pitch) {
    const NxImg* im = nx_img_of(res); uint32_t w = b->r - b->l, h = b->b - b->t, d = b->k - b->f; Geo g = geo(im, w, h);
    if (row_pitch < g.rb) row_pitch = g.rb;
    uint32_t slice = g.rows * row_pitch, size = (d - 1) * slice + (g.rows - 1) * row_pitch + g.rb;       // the ABI's layout: slices rows * rowPitch apart
    if (d > 1 && slice_pitch != slice) {                                                                  // repack slices that are further apart
        uint8_t* t = (uint8_t*)nxd3d_alloc((size_t)slice * d); memset(t, 0, (size_t)slice * d);
        for (uint32_t z = 0; z < d; z++) memcpy(t + (size_t)z * slice, (const uint8_t*)data + (size_t)z * slice_pitch, slice < slice_pitch ? slice : slice_pitch);
        nxs_image_update(image, level, layer, b->l, b->t, b->f, w, h, d, t, size, row_pitch); nxd3d_free(t); return;
    }
    nxs_image_update(image, level, layer, b->l, b->t, b->f, w, h, d, data, size, row_pitch);
}
void nxd3d_image_read(NxObj* res, uint32_t image, uint32_t level, uint32_t layer, void* dst, uint32_t row_pitch, uint32_t slice_pitch) {
    NxImg* im = nx_img_of(res); const NxSub* s = im->at(level, im->kind == NXK_VOLUME ? 0 : layer); Geo g = geo(im, s->w, s->h);
    uint32_t bytes = g.rb * g.rows * s->d; void* st = nxd3d_gpu_alloc((bytes + 4095u) & ~4095u);
    nxs_flush();                                                                                          // R3
    NxImageRead* rd = (NxImageRead*)nxs_scratch(sizeof *rd); memset(rd, 0, sizeof *rd);
    rd->src.image = image; rd->src.level = (uint16_t)level; rd->src.layer = (uint16_t)(im->kind == NXK_VOLUME ? 0 : layer); rd->w = s->w; rd->h = s->h; rd->d = s->d;
    rd->dst = FNVNX_ADDR_(st); rd->dst_size = bytes; rd->rowPitch = g.rb;
    uint64_t a[1] = {FNVNX_ADDR_(rd)}; nxd3d_gpu_call(NXGPU_FN_IMAGE_READ, a, 1);
    bool conv = (im->ni.flags & NXFMT_CONVERT) != 0; uint32_t ob = conv ? s->w * im->ni.srcBytes : g.rb, orows = conv ? s->h : g.rows;
    uint8_t* t = conv ? (uint8_t*)nxd3d_alloc((size_t)ob * orows + 1) : nullptr;
    for (uint32_t z = 0; z < s->d; z++) {
        const uint8_t* src = (const uint8_t*)st + (size_t)z * g.rb * g.rows; if (conv) { nx_d3d9_unconvert(im->fmt, s->w, s->h, src, g.rb * g.rows, t, ob * orows); src = t; }
        for (uint32_t r = 0; r < orows; r++) memcpy((uint8_t*)dst + (size_t)z * slice_pitch + (size_t)r * row_pitch, src + (size_t)r * ob, ob);
    }
    nxd3d_free(t); nxd3d_gpu_free(st);
}
void nxd3d_gen_mips(NxObj*, uint32_t image, uint32_t filter) { nxs_gen_mips(image, filter); }
void nxd3d_preload(NxObj* res) {
    switch (res->ci->rtype) {
    case D3DRTYPE_VERTEXBUFFER: case D3DRTYPE_INDEXBUFFER: nx_buf_gpu_sync(static_cast<NxBufCore*>(res)); break;
    default: nx_tex_preload(res); break;
    }
}
