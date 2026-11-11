// glue_buf.cpp: the GPU mirror of buffers (glue_buf.h) and the destroy hook of res_core.h.
#include "glue_stream.h"
#include "glue_buf.h"
#include <string.h>

uint32_t nx_buf_gpu_sync(NxBufCore* b) {
    if (!b->gblock) {
        b->gsize = (b->length + 4095u) & ~4095u; if (!b->gsize) b->gsize = 4096;
        b->gmem = nxd3d_gpu_alloc(b->gsize); memset(b->gmem, 0, b->gsize);
        NxMemBlockDesc* d = (NxMemBlockDesc*)nxs_scratch(sizeof *d); d->storage = FNVNX_ADDR_(b->gmem); d->size = b->gsize; d->kind = NxMem_Generic;
        uint64_t a[1] = {FNVNX_ADDR_(d)}, r = nxd3d_gpu_call(NXGPU_FN_MEM_CREATE, a, 1);
        b->gblock = nxs_ok(r) ? (uint32_t)r : 0; b->dirty_lo = 0; b->dirty_hi = b->length;                    // a new mirror holds zeros: the whole buffer is dirty
    }
    uint32_t lo, hi; if (b->take_dirty(&lo, &hi)) memcpy((uint8_t*)b->gmem + lo, b->mem + lo, hi - lo);
    return b->gblock;
}
void nxd3d_buf_destroy(NxBufCore* b) {
    if (!b->gmem) return;
    nxs_flush();                                       // the pending stream may bind the block
    if (b->gblock) { uint64_t a[1] = {b->gblock}; nxd3d_gpu_call(NXGPU_FN_MEM_DESTROY, a, 1); }
    nxd3d_gpu_free(b->gmem); b->gmem = nullptr; b->gblock = 0;
}
