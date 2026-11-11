// glue_stream.h: the device's way to the backend (nxgpu4.h): ONE call function and a command-stream writer. Product code of D6 (copy / clear), the image / query / buffer hooks (glue_*.cpp) and
// the later draw path all write through it. Single-threaded like the D3D9 device (no D3DCREATE_MULTITHREADED); ONE stream per process, because the backend is one global instance.
//
// ---- platform (declared here, defined by the platform / the tests; the product objects leave them undefined)
//   nxd3d_gpu_call(fn, args, n)  guest: ext_call(NXGPU_EXT_MODULE, fn, args, n); native tests: nxgl4_ext(fn, args, n). Result = the backend's uint64_t (NXGPU_BAD_CALL = 0xFFFFFFFFFFFFFF02).
//                                Every address in args / in a struct a call points to is an address of memory from nxd3d_gpu_alloc (the glue never passes a stack address).
//   nxd3d_gpu_alloc(bytes)       memory the backend may read and write: 4 KiB aligned, bytes a multiple of 4 KiB, never null (the platform aborts on exhaustion). nxd3d_gpu_free(p) releases it.
// ---- the flush rule (D3D9 order is the order of the calls; the stream is a batch of that order and is invisible to the application)
//   R1 Passes: draws and clears live inside a pass (BeginPass4 .. EndPass). Every op that must be OUTSIDE a pass (Blit, GenMips, ImageUpdate; nxgpu4.h) first ends the open pass
//      (nxs_outside_pass): the end of a pass is not observable, so closing it early only splits the batch. nxs_pass_end also emits Barrier(0): the targets of every pass are sampleable afterwards.
//   R2 A stream is submitted (nxs_flush) when: (a) something observes completion: image_read, a query result poll, nxs_finish; (b) the segment is full (words and payloads share one 1 MiB block);
//      (c) an image that the pending stream references is destroyed (the destroy waits for submitted work only; the ops before it must still see the image); (d) the application asks
//      (D3DGETDATA_FLUSH, Present, GetData polling: the caller decides). An empty stream is never submitted.
//   R3 image_read observes every earlier op: it flushes first, and the backend's read waits for all submitted work.
//   R4 Memory: a segment (words from the front, payloads from the back) stays untouched until its frame completed (wait_frame), then it is reused; 3 segments rotate. A payload larger than a segment gets a
//      block of its own that is destroyed with the frame that used it.
#pragma once
#include "obj.h"
#include "nxgpu4.h"

uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t* args, uint32_t nargs);
void* nxd3d_gpu_alloc(size_t bytes);
void nxd3d_gpu_free(void* p);

enum { NXS_SEGMENT = 1 << 20, NXS_RING = 3 };
inline bool nxs_ok(uint64_t r) { return r != NXGPU_BAD_CALL; }

// ---- scratch: the one place structs for a call live (call-sized, valid until the next call of the glue)
void* nxs_scratch(uint32_t bytes);

// ---- the stream
void nxs_ref_image(uint32_t image);                   // the next op uses `image` (recorded for R2c; every op writer below does it for its images)
void nxs_image_destroyed(uint32_t image);             // R2c: flush if the pending stream references `image`; the caller destroys it afterwards
void nxs_flush();                                     // R2: submit the pending stream (EndPass first); no wait
void nxs_finish();                                    // flush and wait until every submitted frame completed
void nxs_outside_pass();                              // R1
void nxs_pass_begin(uint32_t ncolor, const NxTarget* color, const NxTarget* depth /* null = no depth target */);
void nxs_pass_end();
uint32_t nxs_pass_epoch();                            // +1 at every BeginPass4 written (also the continuation of a pass that outgrew its segment: it is split, ended + submitted + begun again with the same
                                                      // targets, and starts as BeginPass4 does, no state bound); the draw path rebinds its state when the epoch changed
uint32_t nxs_errors();                                // backend calls that failed (a malformed stream is a device bug): 0 in a correct run
void nxs_clear(uint32_t flags /* NxClearFlags */, uint32_t rgba8, float depth, uint32_t stencil, uint32_t x, uint32_t y, uint32_t w, uint32_t h);   // inside a pass
void nxs_blit(const NxTarget& src, int sx, int sy, int sw, int sh, const NxTarget& dst, int dx, int dy, int dw, int dh, uint32_t filter);
void nxs_gen_mips(uint32_t image, uint32_t filter);
// ImageUpdate of the box (x, y, z, w, h, d) of (level, layer); `data` = `size` bytes laid out as nxgpu4.h says (rows rowPitch apart, slices rows * rowPitch apart); the bytes are copied.
void nxs_image_update(uint32_t image, uint32_t level, uint32_t layer, uint32_t x, uint32_t y, uint32_t z, uint32_t w, uint32_t h, uint32_t d, const void* data, uint32_t size, uint32_t rowPitch);
void nxs_query_begin(uint32_t id, uint32_t kind);
void nxs_query_end(uint32_t id);
uint32_t nxs_in_flight();                             // submitted frames not yet known complete (tests)
