// draw_bind.cpp: vertex streams, the index buffer and the data of UP draws.
//  B1 a stream with a vertex buffer binds the buffer's GPU mirror (glue_buf.h): BindVtxN(stream, block, offset = the SetStreamSource offset, size = length - offset, stride = the SetStreamSource stride). The
//     data of an UP draw is copied into the stream segment (nxs_payload) and bound as stream 0 with its stride; it is never tracked (the next draw rebinds stream 0).
//  B2 hazard rule (glue_buf.h): the backend reads a mirror when the stream is replayed, so a buffer with CPU-dirty bytes that a still pending stream may bind is not updated before the stream is submitted. The draw path
//     remembers the buffers it bound since its last nxs_flush (a table of 64: when it is full it flushes and starts again); an internal flush of the glue only makes the flush here superfluous, never wrong.
//  B3 the unbound-input constant stream (draw_layout.cpp L3): BindVtxN(stream, zero block, 0, 16, 0) over a process-global block of 16 zero bytes made with the first use.
//  B5 UP data above NXS_MAX_UPDATE / 4 (128 KiB; glue E4: no op carries more) goes into a block of its own (backend memory made for this draw): after the draw op the stream is finished (nxs_finish: submitted and
//     completed) and the block destroyed (nx_bind_done), so the class "UP data of any size" is closed; no memory for it = D3DERR_OUTOFVIDEOMEMORY, nothing recorded.
//  B4 index buffer: BindIdx(block, 0, length, type) with type 1 for D3DFMT_INDEX32 and 0 otherwise; UP indices go through the segment payload with the format's type.
#include "draw_core.h"

namespace {
NxBufCore* g_seen[64]; uint32_t g_nseen = 0; void* g_zmem = nullptr; uint32_t g_zblk = 0;
uint32_t mirror(NxBufCore* b) {
    bool dirty = b->dirty_lo < b->dirty_hi, seen = false; for (uint32_t i = 0; i < g_nseen; i++) seen |= g_seen[i] == b;
    if ((dirty && b->gblock && seen) || (!seen && g_nseen == 64)) { nxs_flush(); g_nseen = 0; seen = false; }   // B2
    uint32_t blk = nx_buf_gpu_sync(b); if (!seen) g_seen[g_nseen++] = b; return blk;
}
uint32_t zero_block() {
    if (g_zblk) return g_zblk;
    void* z = nxd3d_gpu_alloc(4096); if (!z) return 0; memset(z, 0, 4096);
    NxMemBlockDesc* m = (NxMemBlockDesc*)nxs_scratch(sizeof *m); m->storage = FNVNX_ADDR_(z); m->size = 4096; m->kind = NxMem_Generic;
    uint64_t a[1] = {FNVNX_ADDR_(m)}, r = nxd3d_gpu_call(NXGPU_FN_MEM_CREATE, a, 1);
    if (!nxs_ok(r) || !r) { nxd3d_gpu_free(z); return 0; }
    g_zmem = z; g_zblk = (uint32_t)r; return g_zblk;
}
bool big_block(const void* data, uint32_t size, void** mem, uint32_t* blk) {   // B5
    uint32_t bytes = (size + 4095u) & ~4095u; void* m = nxd3d_gpu_alloc(bytes); if (!m) return false; memset(m, 0, bytes); memcpy(m, data, size);
    NxMemBlockDesc* d = (NxMemBlockDesc*)nxs_scratch(sizeof *d); d->storage = FNVNX_ADDR_(m); d->size = bytes; d->kind = NxMem_Generic;
    uint64_t a[1] = {FNVNX_ADDR_(d)}, r = nxd3d_gpu_call(NXGPU_FN_MEM_CREATE, a, 1);
    if (!nxs_ok(r) || !r) { nxd3d_gpu_free(m); return false; }
    *mem = m; *blk = (uint32_t)r; return true;
}
bool is_big(uint32_t bytes) { return bytes > NXS_MAX_UPDATE / 4; }
void bind_vtx(uint32_t s, uint32_t blk, uint32_t off, uint32_t size, uint32_t stride, bool track) {
    uint32_t v[4] = {blk, off, size, stride}; NxBound& b = g_nxb;
    if (track && b.vtx_ok[s] && !memcmp(b.vtx[s], v, 16)) return;
    uint32_t* w = nxs_op(NxOp_BindVtxN, 5); w[0] = s; w[1] = blk; w[2] = off; w[3] = size; w[4] = stride;
    if (track) { memcpy(b.vtx[s], v, 16); b.vtx_ok[s] = 1; } else b.vtx_ok[s] = 0;
}
}

HRESULT nx_bind_plan(NxDeviceCore* d, const NxDrawReq& r, const NxLayout& lay, NxBindPlan* bp) {
    const NxDeviceState& s = d->cur; memset(bp, 0, sizeof *bp);
    for (uint32_t i = 0; i < NX4_MAX_STREAMS; i++) {
        if (!(lay.streams >> i & 1) || (r.up && i == 0)) continue;
        NxBufCore* vb = static_cast<NxBufCore*>(nx_obj_of((IUnknown*)s.stream[i].vb)); uint32_t off = s.stream[i].offset;
        bp->vtx[i][0] = mirror(vb); if (!bp->vtx[i][0]) { nx_bind_done(bp); return D3DERR_OUTOFVIDEOMEMORY; }          // no backend memory for the mirror
        bp->vtx[i][1] = off; bp->vtx[i][2] = vb->length - off; bp->vtx[i][3] = s.stream[i].stride; bp->nvtx_mask |= 1u << i;      // B1 (the caller checked off < length)
    }
    if (lay.zero_stream >= 0) { bp->zero_blk = zero_block(); if (!bp->zero_blk) return D3DERR_OUTOFVIDEOMEMORY; }
    if (r.up && is_big(r.up->vbytes)) { if (!big_block(r.up->vtx, r.up->vbytes, &bp->big_mem[0], &bp->big_blk[0])) return D3DERR_OUTOFVIDEOMEMORY; }
    if (r.up && r.indexed && is_big(r.up->ibytes)) { if (!big_block(r.up->idx, r.up->ibytes, &bp->big_mem[1], &bp->big_blk[1])) { nx_bind_done(bp); return D3DERR_OUTOFVIDEOMEMORY; } }
    if (r.indexed && !r.up) { NxBufCore* ib = static_cast<NxBufCore*>(nx_obj_of((IUnknown*)s.ib[0])); bp->idx[0] = mirror(ib); if (!bp->idx[0]) { nx_bind_done(bp); return D3DERR_OUTOFVIDEOMEMORY; } bp->idx[1] = 0; bp->idx[2] = ib->length; bp->idx[3] = ib->fmt == D3DFMT_INDEX32; bp->has_idx = true; }
    return D3D_OK;
}
void nx_bind_done(NxBindPlan* bp) {
    if (!bp->big_mem[0] && !bp->big_mem[1]) return;
    nxs_finish();                                                                                                                                      // B5: the replay and the GPU are done with the blocks
    for (int i = 0; i < 2; i++) if (bp->big_mem[i]) { uint64_t a[1] = {bp->big_blk[i]}; nxd3d_gpu_call(NXGPU_FN_MEM_DESTROY, a, 1); nxd3d_gpu_free(bp->big_mem[i]); bp->big_mem[i] = nullptr; }
}
void nx_bind_flush(const NxDrawReq& r, const NxLayout& lay, const NxBindPlan& bp, bool) {
    NxBound& b = g_nxb;
    for (uint32_t i = 0; i < NX4_MAX_STREAMS; i++) {
        if (!(lay.streams >> i & 1)) continue;
        if (r.up && i == 0) { uint32_t blk = bp.big_blk[0], off = 0; if (!bp.big_mem[0]) nxs_payload(r.up->vtx, r.up->vbytes, &blk, &off); bind_vtx(0, blk, off, r.up->vbytes, r.up->vstride, false); }
        else bind_vtx(i, bp.vtx[i][0], bp.vtx[i][1], bp.vtx[i][2], bp.vtx[i][3], true);
    }
    if (lay.zero_stream >= 0) bind_vtx((uint32_t)lay.zero_stream, bp.zero_blk, 0, 16, 0, true);                                                       // B3
    if (!r.indexed) return;
    uint32_t v[4] = {bp.idx[0], bp.idx[1], bp.idx[2], bp.idx[3]};
    if (r.up) { if (bp.big_mem[1]) { v[0] = bp.big_blk[1]; v[1] = 0; } else nxs_payload(r.up->idx, r.up->ibytes, &v[0], &v[1]); v[2] = r.up->ibytes; v[3] = r.up->itype; b.idx_ok = 0; }
    else if (b.idx_ok && !memcmp(b.idx, v, 16)) return;
    else { memcpy(b.idx, v, 16); b.idx_ok = 1; }
    uint32_t* w = nxs_op(NxOp_BindIdx, 4); w[0] = v[0]; w[1] = v[1]; w[2] = v[2]; w[3] = v[3];                                                         // B4
}
void nx_bind_shutdown() {   // the zero block: its backend memory block, then its memory (nxs_shutdown does not know it)
    if (g_zblk) { uint64_t a[1] = {g_zblk}; nx_ignore(nxd3d_gpu_call(NXGPU_FN_MEM_DESTROY, a, 1)); }
    if (g_zmem) nxd3d_gpu_free(g_zmem);
    g_zmem = nullptr; g_zblk = 0; g_nseen = 0;
}
