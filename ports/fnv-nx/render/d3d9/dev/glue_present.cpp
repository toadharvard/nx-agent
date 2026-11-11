// glue_present.cpp: the product definitions of the presentation hooks declared in dev_hooks.h (nxd3d_present, nxd3d_wait_idle, nxd3d_frontbuffer_read, nxd3d_wait_vblank), each as exactly the nxgpu4.h call it
// names, through glue_stream.h (flush rule R1-R4, exhaustion rules E1-E3 there). The rules P1-P5 of the backend side are written at NxPresent in nxgpu4.h.
//  present      R2d: the pending stream is submitted (nxs_flush), then NXGPU_FN_PRESENT(NxPresent). P1: the call is ordered after every submitted stream and consumes all its memory before it returns, so no
//               NXGPU_FN_WAIT_FRAME is needed (the ring bounds the frames in flight, R4) and nothing outlives the call. The NxPresent is copied into the scratch (E2) with the gamma ramp behind it
//               (the chain's ramp is CPU memory: the backend reads only nxd3d_gpu_alloc memory); the software cursor pixels (P2: the cursor is blended onto the PRESENTED image by the backend, the back
//               buffer is never touched, so no stream op can express it) are copied into a block that lives for this call only. Exhaustion: that block is the one allocation a present makes, and only
//               while a cursor is visible; null = NXD3D_HOOK_NOMEM before anything was done (D3DERR_OUTOFVIDEOMEMORY at the device); the ring and the scratch exist because the back buffer's creation reserved them (E1).
//  wait_idle    nxs_finish: flush and wait for every submitted frame.
//  frontbuffer  R3: flush, NXGPU_FN_INFO for the swapchain size (an image the size of the window's client area), NXGPU_FN_IMAGE_READ with src.image 0 (P4) of the part of the w x h rectangle that lies inside
//               the swapchain image into the read-back staging of E3 (grown here when the back buffer's own formats were narrower: false = NXD3D_HOOK_NOMEM); the rest of the rectangle is zero bytes
//               (pixels that do not exist: the window's client area is smaller than the chain's size, or there is no swapchain).
//  wait_vblank  NXGPU_FN_WAIT_VBLANK(0) (P5): the only output; no flush (a pure wait).
#include "glue_stream.h"
#include "dev_hooks.h"
#include <string.h>

namespace {
enum { RAMP_BYTES = 3 * 256 * 2, RAMP_OFF = 128, SCRATCH_NEED = RAMP_OFF + RAMP_BYTES, MAX_CURSOR_DIM = 4096 };   // MAX_CURSOR_DIM: the bound of NxPresent.cw / ch (nxgpu4.h)
static_assert(sizeof(NxPresent) <= RAMP_OFF, "the NxPresent and the ramp share the scratch");
static_assert(sizeof(D3DGAMMARAMP) == RAMP_BYTES, "D3DGAMMARAMP is 3 x 256 u16: red, green, blue");
inline uint32_t umin(uint32_t a, uint32_t b) { return a < b ? a : b; }
inline int result(uint64_t r) { return r == 0 ? 0 : 1; }
}

int nxd3d_present(NxObj*, const NxPresent* p) {
    if (!nxs_reserve()) return NXD3D_HOOK_NOMEM;
    if (p->cursor && (!p->cw || !p->ch || p->cw > MAX_CURSOR_DIM || p->ch > MAX_CURSOR_DIM)) return 1;   // outside the ABI's bound: refused like the backend would, before anything is allocated
    size_t cbytes = p->cursor ? (size_t)p->cw * p->ch * 4 : 0; void* cur = nullptr;
    if (cbytes && !(cur = nxd3d_gpu_alloc((cbytes + 4095u) & ~(size_t)4095u))) return NXD3D_HOOK_NOMEM;
    nxs_flush();                                                                                   // R2d: the stream of everything the application did so far, before the present
    NxPresent* q = (NxPresent*)nxs_scratch(SCRATCH_NEED);                                          // E2 (after the flush: the flush itself uses the scratch area for its NxSubmit)
    if (!q) { if (cur) nxd3d_gpu_free(cur); return NXD3D_HOOK_NOMEM; }                                      // the ring is reserved above: not reachable
    *q = *p;
    if (p->gamma) { uint8_t* ramp = (uint8_t*)q + RAMP_OFF; memcpy(ramp, (const void*)(uintptr_t)p->gamma, RAMP_BYTES); q->gamma = FNVNX_ADDR_(ramp); }
    if (cur) { memcpy(cur, (const void*)(uintptr_t)p->cursor, cbytes); q->cursor = FNVNX_ADDR_(cur); }
    uint64_t a[1] = {FNVNX_ADDR_(q)}, r = nxd3d_gpu_call(NXGPU_FN_PRESENT, a, 1);
    if (cur) nxd3d_gpu_free(cur);                                                                  // P1: consumed
    return result(r);
}

void nxd3d_wait_idle(NxObj*) { nxs_finish(); }

int nxd3d_frontbuffer_read(NxObj*, uint32_t w, uint32_t h, void* dst, uint32_t pitch) {
    if (!nxs_reserve()) return NXD3D_HOOK_NOMEM;
    nxs_flush();                                                                                   // R3
    NxGpuInfo* info = (NxGpuInfo*)nxs_scratch(sizeof *info); if (!info) return NXD3D_HOOK_NOMEM;
    uint64_t a[1] = {FNVNX_ADDR_(info)};
    if (nxd3d_gpu_call(NXGPU_FN_INFO, a, 1) != 0) return 1;
    uint32_t cw = umin(w, info->width), ch = umin(h, info->height), need = cw * ch * 4;           // the swapchain image's size is at most 16-bit wide: no overflow
    if (need) {
        if (nxs_staging_size() < need && !nxs_reserve_readback(need, 0)) return NXD3D_HOOK_NOMEM;
        uint8_t* st = nxs_staging(); NxImageRead* rd = (NxImageRead*)nxs_scratch(sizeof *rd); if (!st || !rd) return NXD3D_HOOK_NOMEM;
        memset(rd, 0, sizeof *rd);
        rd->src.image = 0; rd->w = cw; rd->h = ch; rd->d = 1; rd->dst = FNVNX_ADDR_(st); rd->dst_size = need; rd->rowPitch = cw * 4;   // P4
        a[0] = FNVNX_ADDR_(rd);
        if (nxd3d_gpu_call(NXGPU_FN_IMAGE_READ, a, 1) != 0) return 1;
        for (uint32_t y = 0; y < ch; y++) memcpy((uint8_t*)dst + (size_t)y * pitch, st + (size_t)y * cw * 4, (size_t)cw * 4);
    }
    for (uint32_t y = 0; y < h; y++) { uint32_t from = y < ch ? cw * 4 : 0; memset((uint8_t*)dst + (size_t)y * pitch + from, 0, (size_t)w * 4 - from); }   // outside the swapchain image: zero bytes
    return 0;
}

int nxd3d_wait_vblank(NxObj*) { uint64_t a[1] = {0}; return result(nxd3d_gpu_call(NXGPU_FN_WAIT_VBLANK, a, 1)); }   // P5
