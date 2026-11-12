// glue_stream.h: the device's way to the backend (nxgpu4.h): ONE call function and a command-stream writer. Product code of D6 (copy / clear), the image / query / buffer hooks (glue_*.cpp) and
// the later draw path all write through it. Single-threaded like the D3D9 device (no D3DCREATE_MULTITHREADED); ONE stream per process, because the backend is one global instance.
//
// ---- platform (declared here, defined by the platform / the tests; the product objects leave them undefined)
//   nxd3d_gpu_call(fn, args, n)  guest: ext_call(NXGPU_EXT_MODULE, fn, args, n); native tests: nxgl4_ext(fn, args, n). Result = the backend's uint64_t (NXGPU_BAD_CALL = 0xFFFFFFFFFFFFFF02).
//                                Every address in args / in a struct a call points to is an address of memory from nxd3d_gpu_alloc (the glue never passes a stack address).
//   nxd3d_gpu_alloc(bytes)       memory the backend may read and write: 4 KiB aligned, bytes a multiple of 4 KiB; NULL when the platform has none left (a result, like nxd3d_alloc: obj.h). nxd3d_gpu_free(p) releases it.
// ---- exhaustion (README "Allocation failure"). Backend memory is allocated where an object is MADE and nowhere an operation runs, so every operation of an object that exists cannot fail for lack of memory:
//   E1 the ring of three stream segments (with their tails) is allocated whole by nxs_reserve(), called by everything that creates the first user of the stream (nxd3d_image_create, nxd3d_query_create, nx_buf_gpu_sync);
//      afterwards acquiring a segment only waits. The op writers below return false ONLY while the ring is not reserved (nothing written, nothing changed): a caller that holds a GPU image / query / buffer mirror
//      never sees false, because that object's creation reserved the ring. Callers with an HRESULT return E_OUTOFMEMORY for false; the void backend hooks (nxd3d_image_update / gen_mips / query_issue) carry that proof.
//   E2 the descriptors a call needs (NxSubmit, NxMemBlockDesc, NxImageDesc4, NxImageRead, query result) live in the tail of a segment, never in memory of their own.
//   E3 read-back staging and the conversion buffer are reserved at nxd3d_image_create for the largest sub-resource of every DEFAULT image (nxs_reserve_readback); they only grow.
//   E4 an op's payload is at most NXS_MAX_UPDATE bytes (the glue splits bigger image updates into boxes), so no op needs a block of its own.
//   nxs_shutdown() returns all of it (platform teardown, tests).
#define NXS_TAIL 4096u                                // per segment: scratch (first 2 KiB, segment 0 only), NxSubmit, NxMemBlockDesc
#define NXS_MAX_UPDATE (NXS_SEGMENT / 2u)             // E4

// ---- the flush rule (D3D9 order is the order of the calls; the stream is a batch of that order and is invisible to the application)
//   R1 Passes: draws and clears live inside a pass (BeginPass4 .. EndPass). Every op that must be OUTSIDE a pass (Blit, GenMips, ImageUpdate; nxgpu4.h) first ends the open pass
//      (nxs_outside_pass): the end of a pass is not observable, so closing it early only splits the batch. nxs_pass_end also emits Barrier(0): the targets of every pass are sampleable afterwards.
//   R2 A stream is submitted (nxs_flush) when: (a) something observes completion: image_read, a query result poll, nxs_finish; (b) the segment is full (words and payloads share one 1 MiB block);
//      (c) an image that the pending stream references is destroyed (the destroy waits for submitted work only; the ops before it must still see the image); (d) the application asks
//      (D3DGETDATA_FLUSH, Present, GetData polling: the caller decides). An empty stream is never submitted.
//   R3 image_read observes every earlier op: it flushes first, and the backend's read waits for all submitted work.
//   R4 Memory: a segment (words from the front, payloads from the back, its tail in the last NXS_TAIL bytes) stays untouched until its frame completed (wait_frame), then it is reused; 3 segments rotate. Every op
//      fits a segment (E4); the ring and every descriptor are reserved when objects are made (E1-E3 above), never when an op is written.
#pragma once
#include "obj.h"
#include "nxgpu4.h"

uint64_t nxd3d_gpu_call(uint32_t fn, const uint64_t* args, uint32_t nargs);
void nxd3d_gpu_free(void* p);
void* nxd3d_gpu_alloc(size_t bytes) NX_ALLOCATOR(nxd3d_gpu_free);

enum { NXS_SEGMENT = 1 << 20, NXS_RING = 3 };
inline bool nxs_ok(uint64_t r) { return r != NXGPU_BAD_CALL; }

// ---- reservations (E1, E3)
NX_MUST_USE bool nxs_reserve();                                          // E1: the ring; idempotent; false = no backend memory (nothing changed that matters: a later call retries the missing segments)
NX_MUST_USE bool nxs_reserve_readback(uint32_t bytes, uint32_t conv);    // E3: staging >= bytes (backend memory) and conversion buffer >= conv (CPU); false = out of memory, the old reservation is kept
uint8_t* nxs_staging(); uint32_t nxs_staging_size();                                                  // the read-back staging (null until reserved); nxs_conv(): the conversion buffer
uint8_t* nxs_conv();
void nxs_shutdown();                                                     // returns the ring, the staging and the conversion buffer (waits for submitted frames first)

// ---- scratch: the one place structs for a call live (call-sized, valid until the next call of the glue): the tail of segment 0. Null when the ring is not reserved or bytes > 2 KiB (E2)
void* nxs_scratch(uint32_t bytes);

// ---- the stream
void nxs_ref_image(uint32_t image);                   // the next op uses `image` (recorded for R2c; every op writer below does it for its images)
void nxs_image_destroyed(uint32_t image);             // R2c: flush if the pending stream references `image`; the caller destroys it afterwards
void nxs_flush();                                     // R2: submit the pending stream (EndPass first); no wait
void nxs_finish();                                    // flush and wait until every submitted frame completed
void nxs_outside_pass();                              // R1
NX_MUST_USE bool nxs_pass_begin(uint32_t ncolor, const NxTarget* color, const NxTarget* depth /* null = no depth target */);   // false: E1 (ring not reserved); no pass was begun
void nxs_pass_end();
uint32_t nxs_pass_epoch();                            // +1 at every BeginPass4 written (also the continuation of a pass that outgrew its segment: it is split, ended + submitted + begun again with the same
                                                      // targets, and starts as BeginPass4 does, no state bound); the draw path rebinds its state when the epoch changed
void nxs_error();                                     // records a violated exhaustion invariant (E1 / E3: an op of an object whose creation did not reserve): a device bug, counted with the failed backend calls
uint32_t nxs_errors();                                // backend calls that failed (a malformed stream is a device bug): 0 in a correct run
NX_MUST_USE bool nxs_clear(uint32_t flags /* NxClearFlags */, uint32_t rgba8, float depth, uint32_t stencil, uint32_t x, uint32_t y, uint32_t w, uint32_t h);   // inside a pass; false: E1, nothing written
NX_MUST_USE bool nxs_blit(const NxTarget& src, int sx, int sy, int sw, int sh, const NxTarget& dst, int dx, int dy, int dw, int dh, uint32_t filter);
NX_MUST_USE bool nxs_gen_mips(uint32_t image, uint32_t filter);
// ImageUpdate of the box (x, y, z, w, h, d) of (level, layer); `data` = `size` bytes laid out as nxgpu4.h says (rows rowPitch apart, slices rows * rowPitch apart); the bytes are copied.
// false: E1, or size above NXS_MAX_UPDATE (E4: the caller splits); nothing written.
NX_MUST_USE bool nxs_image_update(uint32_t image, uint32_t level, uint32_t layer, uint32_t x, uint32_t y, uint32_t z, uint32_t w, uint32_t h, uint32_t d, const void* data, uint32_t size, uint32_t rowPitch);
NX_MUST_USE bool nxs_query_begin(uint32_t id, uint32_t kind);
NX_MUST_USE bool nxs_query_end(uint32_t id);
// ---- the draw path's writers (draw_*.cpp). nxs_room(op_bytes, payload_bytes) first: afterwards the next ops (together <= op_bytes) and payloads (together <= payload_bytes, each <= NXS_MAX_UPDATE) land in ONE segment and ONE pass
// epoch (when the segment cannot take them, a pass is ended, the stream submitted and the pass continued: epoch++ as for every op). false = E1 (ring not reserved). nxs_op writes a raw op (opcode | nargs << 16, then `nargs`
// words the caller fills) and nxs_payload copies bytes into the segment (256 byte aligned, the BindUbo / BindVtxN / BindIdx alignment): block id and offset of the copy. Both PRECONDITION a successful nxs_room.
NX_MUST_USE bool nxs_room(uint32_t op_bytes, uint32_t payload_bytes);
uint32_t* nxs_op(uint32_t op, uint32_t nargs);
void nxs_payload(const void* data, uint32_t size, uint32_t* blk, uint32_t* off);
bool nxs_pass_open();                                 // a pass is open (begun and not ended): the draw path begins one when it is not its own
uint32_t nxs_in_flight();                             // submitted frames not yet known complete (tests)
