// glue_buf.cpp: the GPU mirror of buffers (glue_buf.h) and the destroy hook of res_core.h.
#include "glue_stream.h"
#include "glue_buf.h"
#include <string.h>

uint32_t nx_buf_gpu_sync(NxBufCore* b) {
    if (!b->gblock) {
        // 0 = the backend has no memory for the mirror (or for the ring): nothing of the buffer changed (the dirty range stays), the next sync retries
        if (!nxs_reserve()) return 0;
        uint32_t gsize = (b->length + 4095u) & ~4095u; if (!gsize) gsize = 4096;
        void* gmem = nxd3d_gpu_alloc(gsize); if (!gmem) return 0;
        memset(gmem, 0, gsize);
        NxMemBlockDesc* d = (NxMemBlockDesc*)nxs_scratch(sizeof *d); if (!d) { nxd3d_gpu_free(gmem); return 0; }   // E2: the ring's scratch
        d->storage = FNVNX_ADDR_(gmem); d->size = gsize; d->kind = NxMem_Generic;
        uint64_t a[1] = {FNVNX_ADDR_(d)}, r = nxd3d_gpu_call(NXGPU_FN_MEM_CREATE, a, 1);
        if (!nxs_ok(r)) { nxd3d_gpu_free(gmem); return 0; }
        b->gmem = gmem; b->gsize = gsize; b->gblock = (uint32_t)r; b->dirty_lo = 0; b->dirty_hi = b->length;   // a new mirror holds zeros: the whole buffer is dirty
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
