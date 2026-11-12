// glue_stream.cpp: the command-stream writer of glue_stream.h (rules R1-R4 and the exhaustion rules E1-E4 there). Words are the 32-bit stream of nx/gpu_abi.h (opcode | nargs << 16, then the arguments).
#include "glue_stream.h"
#include <string.h>

namespace {
enum { SCRATCH = 2048, OFF_SUBMIT = SCRATCH, OFF_DESC = SCRATCH + 64 };   // tail layout (E2): scratch (segment 0), then one NxSubmit, then one NxMemBlockDesc, per segment
struct Seg {
    uint8_t* mem = nullptr; uint32_t blk = 0, nw = 0, data = NXS_SEGMENT - NXS_TAIL, frame = 0; bool inflight = false;
};
struct G {
    Seg seg[NXS_RING]; int cur = -1; uint32_t frame = 0, errors = 0, epoch = 0, nc = 0; bool in_pass = false, has_d = false, ring = false; NxTarget col[4], dep; uint8_t img[NX_MAX_IMAGES / 8 + 1];
    uint8_t* stage = nullptr; uint32_t stage_size = 0; uint8_t* conv = nullptr; uint32_t conv_size = 0;
} g;
enum { HEADROOM = 1024 };   // bytes kept free between words and payloads: any single op (the largest is BeginPass4, 48 bytes) and the closing EndPass / Barrier / End always fit

uint8_t* tail(const Seg& s) { return s.mem + (NXS_SEGMENT - NXS_TAIL); }
bool mem_create(void* mem, uint32_t size, void* desc, uint32_t* blk) {   // NXGPU_FN_MEM_CREATE over mem[0, size); `desc` is memory of the backend (E2)
    NxMemBlockDesc* d = (NxMemBlockDesc*)desc; d->storage = FNVNX_ADDR_(mem); d->size = size; d->kind = NxMem_Generic;
    uint64_t a[1] = {FNVNX_ADDR_(d)}, r = nxd3d_gpu_call(NXGPU_FN_MEM_CREATE, a, 1);
    if (!nxs_ok(r)) return false;
    *blk = (uint32_t)r; return true;
}
void mem_destroy(uint32_t blk) { uint64_t a[1] = {blk}; if (!nxs_ok(nxd3d_gpu_call(NXGPU_FN_MEM_DESTROY, a, 1))) g.errors++; }
void wait_frame(uint32_t f) { uint64_t a[1] = {f}; if (nxd3d_gpu_call(NXGPU_FN_WAIT_FRAME, a, 1) != 0) g.errors++; }
void release(Seg& s) { s.inflight = false; s.nw = 0; s.data = NXS_SEGMENT - NXS_TAIL; }
Seg* oldest() { Seg* o = nullptr; for (Seg& s : g.seg) if (s.inflight && (!o || s.frame < o->frame)) o = &s; return o; }   // nxs_finish: null = nothing in flight
void acquire() {   // R4: a segment nobody uses; the oldest in-flight one after its frame completed when all three are busy. PRECONDITION g.ring (E1): every segment has its memory
    for (;;) {
        Seg* o = &g.seg[0];
        for (int i = 0; i < NXS_RING; i++) {
            Seg& s = g.seg[i];
            if (!s.inflight) { s.nw = 0; s.data = NXS_SEGMENT - NXS_TAIL; g.cur = i; return; }
            if (s.frame < o->frame) o = &s;
        }
        wait_frame(o->frame); release(*o);   // all in flight: the one with the smallest frame number completes first
    }
}
bool open_seg() {   // a current segment, from the reserved ring (E1)
    if (g.cur >= 0) return true;
    if (!nxs_reserve()) return false;
    acquire(); return true;
}
uint32_t* putf(uint32_t op, uint32_t n) {   // header + n argument words, no space check (callers keep HEADROOM in mind). PRECONDITION g.cur >= 0 or g.ring
    if (g.cur < 0) acquire();
    Seg& s = g.seg[g.cur]; uint32_t* w = (uint32_t*)s.mem + s.nw; w[0] = op | n << 16; s.nw += 1 + n; return w + 1;
}
void begin_raw() {
    uint32_t* w = putf(NxOp_BeginPass4, 1 + 2 * g.nc + 2); w[0] = g.nc;
    for (uint32_t i = 0; i < g.nc; i++) { w[1 + 2 * i] = g.col[i].image; w[2 + 2 * i] = g.col[i].level | (uint32_t)g.col[i].layer << 16; }
    w[1 + 2 * g.nc] = g.has_d ? g.dep.image : 0; w[2 + 2 * g.nc] = g.has_d ? (g.dep.level | (uint32_t)g.dep.layer << 16) : 0; g.in_pass = true; g.epoch++;
}
void end_raw() { putf(NxOp_EndPass, 0); putf(NxOp_Barrier, 1)[0] = 0; g.in_pass = false; }   // R1: Barrier(0) after every pass
void flush_raw() {
    if (g.cur < 0 || g.seg[g.cur].nw == 0) return;
    Seg& s = g.seg[g.cur]; putf(NxOp_End, 0);
    NxSubmit* sub = (NxSubmit*)(tail(s) + OFF_SUBMIT); memset(sub, 0, sizeof *sub);   // E2
    sub->stream = FNVNX_ADDR_(s.mem); sub->words = s.nw; sub->frame = ++g.frame;
    uint64_t a[1] = {FNVNX_ADDR_(sub)}; if (nxd3d_gpu_call(NXGPU_FN_SUBMIT, a, 1) != 0) g.errors++;
    s.frame = g.frame; s.inflight = true; g.cur = -1; memset(g.img, 0, sizeof g.img);
}
// an op of `n` argument words: when the segment cannot take it, a pass is ended, the stream submitted and the pass continued as a new pass with the same targets (epoch++). null = E1 (ring not reserved)
uint32_t* put(uint32_t op, uint32_t n) {
    if (!open_seg()) return nullptr;
    if (g.seg[g.cur].nw * 4 + (1 + n) * 4 + HEADROOM > g.seg[g.cur].data) { bool p = g.in_pass; if (p) end_raw(); flush_raw(); if (p) begin_raw(); }
    return putf(op, n);
}
void ref(uint32_t id) { if (id && id < NX_MAX_IMAGES) g.img[id >> 3] |= (uint8_t)(1u << (id & 7)); }
}   // namespace

bool nxs_reserve() {
    if (g.ring) return true;
    for (Seg& s : g.seg) {   // idempotent: a segment made by an earlier, failed call stays
        if (s.mem) continue;
        uint8_t* m = (uint8_t*)nxd3d_gpu_alloc(NXS_SEGMENT); if (!m) return false;
        uint32_t blk; if (!mem_create(m, NXS_SEGMENT, m + (NXS_SEGMENT - NXS_TAIL) + OFF_DESC, &blk)) { nxd3d_gpu_free(m); return false; }
        s.mem = m; s.blk = blk;
    }
    g.ring = true; return true;
}
bool nxs_reserve_readback(uint32_t bytes, uint32_t conv) {
    uint32_t want = (bytes + 4095u) & ~4095u; if (!want) want = 4096;
    if (conv > g.conv_size) {
        uint8_t* c = (uint8_t*)nxd3d_alloc(conv); if (!c) return false;
        nxd3d_free(g.conv); g.conv = c; g.conv_size = conv;
    }
    if (want > g.stage_size) {   // nothing is in flight on the old staging (reads are synchronous); a failed allocation keeps it
        uint8_t* st = (uint8_t*)nxd3d_gpu_alloc(want); if (!st) return false;
        if (g.stage) nxd3d_gpu_free(g.stage);
        g.stage = st; g.stage_size = want;
    }
    return true;
}
uint8_t* nxs_staging() { return g.stage; }
uint32_t nxs_staging_size() { return g.stage_size; }
uint8_t* nxs_conv() { return g.conv; }
void* nxs_scratch(uint32_t bytes) { if (!g.ring || bytes > SCRATCH) return nullptr; return tail(g.seg[0]); }
void nxs_shutdown() {
    nxs_finish();
    for (Seg& s : g.seg) { if (!s.mem) continue; mem_destroy(s.blk); nxd3d_gpu_free(s.mem); s = Seg(); }
    g.ring = false; g.cur = -1; if (g.stage) nxd3d_gpu_free(g.stage); g.stage = nullptr; g.stage_size = 0; nxd3d_free(g.conv); g.conv = nullptr; g.conv_size = 0;
}
void nxs_error() { g.errors++; }
uint32_t nxs_errors() { return g.errors; }
uint32_t nxs_in_flight() { uint32_t n = 0; for (Seg& s : g.seg) n += s.inflight; return n; }
void nxs_ref_image(uint32_t id) { ref(id); }
void nxs_image_destroyed(uint32_t id) { if (id && id < NX_MAX_IMAGES && (g.img[id >> 3] >> (id & 7) & 1)) nxs_flush(); }
void nxs_outside_pass() { if (g.in_pass) nxs_pass_end(); }
bool nxs_pass_begin(uint32_t nc, const NxTarget* c, const NxTarget* d) {
    nxs_outside_pass(); if (!open_seg()) return false;
    if (nc > 4) { g.errors++; nc = 4; }
    g.nc = nc; for (uint32_t i = 0; i < nc; i++) { g.col[i] = c[i]; ref(c[i].image); }
    g.has_d = d != nullptr; if (d) { g.dep = *d; ref(d->image); }
    if (g.seg[g.cur].nw * 4 + 64 + HEADROOM > g.seg[g.cur].data) flush_raw();
    begin_raw(); return true;
}
void nxs_pass_end() { if (g.in_pass) end_raw(); }
uint32_t nxs_pass_epoch() { return g.epoch; }
bool nxs_clear(uint32_t flags, uint32_t rgba8, float depth, uint32_t stencil, uint32_t x, uint32_t y, uint32_t w_, uint32_t h) {
    uint32_t* w = put(NxOp_Clear, 8); if (!w) return false;
    memcpy(&w[2], &depth, 4); w[0] = flags; w[1] = rgba8; w[3] = stencil; w[4] = x; w[5] = y; w[6] = w_; w[7] = h; return true;
}
bool nxs_blit(const NxTarget& s, int sx, int sy, int sw, int sh, const NxTarget& d, int dx, int dy, int dw, int dh, uint32_t filter) {
    nxs_outside_pass(); uint32_t* w = put(NxOp_Blit, 13); if (!w) return false;
    w[0] = s.image; w[1] = s.level | (uint32_t)s.layer << 16; w[2] = (uint32_t)sx; w[3] = (uint32_t)sy; w[4] = (uint32_t)sw; w[5] = (uint32_t)sh;
    w[6] = d.image; w[7] = d.level | (uint32_t)d.layer << 16; w[8] = (uint32_t)dx; w[9] = (uint32_t)dy; w[10] = (uint32_t)dw; w[11] = (uint32_t)dh; w[12] = filter;
    ref(s.image); ref(d.image); return true;
}
bool nxs_gen_mips(uint32_t image, uint32_t filter) { nxs_outside_pass(); uint32_t* w = put(NxOp_GenMips, 2); if (!w) return false; w[0] = image; w[1] = filter; ref(image); return true; }
bool nxs_image_update(uint32_t image, uint32_t level, uint32_t layer, uint32_t x, uint32_t y, uint32_t z, uint32_t w_, uint32_t h, uint32_t d, const void* data, uint32_t size, uint32_t rowPitch) {
    nxs_outside_pass(); uint32_t asz = (size + 15u) & ~15u;
    if (asz > NXS_MAX_UPDATE || !open_seg()) return false;   // E4 / E1
    // the payload and the op words must land in the same segment: room for both is checked, then nothing can flush in between
    if (g.seg[g.cur].nw * 4 + 64 + HEADROOM + asz > g.seg[g.cur].data) flush_raw();
    if (g.cur < 0) acquire();
    Seg& s = g.seg[g.cur]; s.data -= asz; memcpy(s.mem + s.data, data, size); uint32_t blk = s.blk, off = s.data; uint32_t* w = putf(NxOp_ImageUpdate, 12);
    w[0] = image; w[1] = level | layer << 16; w[2] = x; w[3] = y; w[4] = z; w[5] = w_; w[6] = h; w[7] = d; w[8] = blk; w[9] = off; w[10] = size; w[11] = rowPitch; ref(image); return true;
}
bool nxs_query_begin(uint32_t id, uint32_t kind) { uint32_t* w = put(NxOp_QueryBegin, 2); if (!w) return false; w[0] = id; w[1] = kind; return true; }
bool nxs_query_end(uint32_t id) { uint32_t* w = put(NxOp_QueryEnd, 1); if (!w) return false; w[0] = id; return true; }
void nxs_flush() { if (g.in_pass) end_raw(); flush_raw(); }
void nxs_finish() { nxs_flush(); while (Seg* o = oldest()) { wait_frame(o->frame); release(*o); } }
// the draw path's writers (glue_stream.h)
bool nxs_room(uint32_t op_bytes, uint32_t payload) {
    if (!open_seg()) return false;
    if (g.seg[g.cur].nw * 4 + op_bytes + HEADROOM + payload > g.seg[g.cur].data) { bool p = g.in_pass; if (p) end_raw(); flush_raw(); if (p) begin_raw(); }
    if (g.cur < 0) acquire();
    return true;
}
uint32_t* nxs_op(uint32_t op, uint32_t nargs) { return put(op, nargs); }
void nxs_payload(const void* data, uint32_t size, uint32_t* blk, uint32_t* off) {
    if (g.cur < 0) acquire();
    Seg& s = g.seg[g.cur]; uint32_t asz = (size + 255u) & ~255u; s.data = (s.data - asz) & ~255u; if (size) memcpy(s.mem + s.data, data, size); *blk = s.blk; *off = s.data;
}
bool nxs_pass_open() { return g.in_pass; }
