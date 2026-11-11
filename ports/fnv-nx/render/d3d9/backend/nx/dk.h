/* fnv-nx L6 deko3d backend (nxgpu v4.1, console side of rule RD8): the shared state, the dispatch lists and the contract between the files of
 * the backend. The GL backend (gl4.h, gl4_core.c) is the behavioural reference; this file mirrors its table so that both tables are total over
 * the same 20 functions and 28 ops.
 *
 * Files of the backend (each <= 300 lines):
 *   dk_core.c   device, queue, memory blocks, the ext_call dispatch, create / destroy / shutdown
 *   dk_swap.c   the swapchain, PRESENT and WAIT_VBLANK (rules P1-P5 of nxgpu4.h)
 *   to come, each one defines the symbols listed under NXDK_PENDING below (until then they are required symbols of the link):
 *   dk_image.c, dk_sampler.c, dk_shader.c, dk_stream.c (the 28 ops), dk_query.c, dk_present.c (the present compose passes)
 *
 * Embedder obligations (stated assumptions, the same as the GL backend's two hooks):
 *   - nxdk_guest_ptr(addr, len): guest address -> host pointer; the mapping is stable while the guest block lives (one shared address space).
 *     It is NOT defined here: the symbol stays undefined, a build without the embedder does not link.
 *   - nxdk_on_present(frame): called after every completed present.
 *   - libnx's vi service and the default native window are initialised before nxdk_create (viInitialize, nwindowGetDefault()).
 * Memory model: every memory block is caller backed (DkMemBlockMaker.storage) and 4 KiB aligned; the CPU cache of a block is flushed by the file
 * that makes the GPU read it (dkMemBlockFlushCpuCache at bind time), not here. */
#ifndef FNVNX_DK_H
#define FNVNX_DK_H
#include <stddef.h>
#include <stdint.h>
#include <deko3d.h>
#include <switch/kernel/event.h>
#include <switch/services/vi.h>
#include <switch/display/native_window.h>
#include "nxgpu4.h"
#include "nxgpu4_shader.h"

#ifdef __cplusplus
extern "C" {
#endif

enum { NXDK_MAX_MEM = 4096, NXDK_FRAMES = 2, NXDK_PRESENT_CMD = 0x2000 };

void nxchk_err(const char* fmt, ...);   /* the backend-neutral checks report through this sink (nxgpu4_check.h); defined by dk_core.c */
#define nxdk_err nxchk_err

/* function list: X(FN id suffix, NxGpuApi4 field, implementation, argument count). INFO and SHUTDOWN are handled by the core */
#define NXDK_FN_LIST(X) \
    X(MEM_CREATE, v3.mem_create, nxdk_mem_create, 1) X(MEM_GPU_ADDR, v3.mem_gpu_addr, nxdk_mem_gpu_addr, 1) \
    X(IMAGE_CREATE, v3.image_create, nxdk_image_create, 1) \
    X(SAMPLER_CREATE, v3.sampler_create, nxdk_sampler_create, 1) \
    X(SHADER_CREATE, v3.shader_create, nxdk_shader_create, 1) X(SUBMIT, v3.submit, nxdk_submit, 1) \
    X(WAIT_FRAME, v3.wait_frame, nxdk_wait_frame, 1) X(READBACK, v3.readback, nxdk_readback, 1) \
    X(IMAGE_CREATE4, image_create4, nxdk_image_create4, 1) X(IMAGE_DESTROY, image_destroy, nxdk_image_destroy, 1) \
    X(MEM_DESTROY, mem_destroy, nxdk_mem_destroy, 1) X(SHADER_DESTROY, shader_destroy, nxdk_shader_destroy, 1) \
    X(SAMPLER_UPDATE, sampler_update, nxdk_sampler_update, 2) X(IMAGE_READ, image_read, nxdk_image_read, 1) \
    X(QUERY_RESULT, query_result, nxdk_query_result, 3) X(PRESENT, present, nxdk_present, 1) \
    X(WAIT_VBLANK, wait_vblank, nxdk_wait_vblank, 1)
/* ABI 4.1 (nxgpu4_shader.h): number NXGPU_FN_COUNT4, no NxGpuApi4 field, dispatched by nxdk_ext only */
#define NXDK_FN_EXT_LIST(X) X(SHADER_CREATE_D3D, nxdk_shader_create_d3d, 1)
/* the 28 ops of the stream (v3 ops 0..19, v4 ops 20..27) in enum order; the table of handlers belongs to dk_stream.c */
#define NXDK_OP_LIST(X) \
    X(End) X(DefineState) X(DefineLayout) X(BeginPass) X(EndPass) X(Barrier) X(BindState) X(BindUbo) X(BindVtx) \
    X(BindIdx) X(BindTextures) X(Draw) X(DrawIndexed) X(Viewport) X(Scissor) X(StencilRef) X(BlendConst) \
    X(DepthBias) X(Clear) X(Marker) X(BeginPass4) X(DefineLayout4) X(BindVtxN) X(ImageUpdate) X(Blit) X(GenMips) \
    X(QueryBegin) X(QueryEnd)

typedef struct NxDkMem {
    uint64_t storage;              /* guest address of the caller's bytes */
    uint32_t size, kind, used;
    DkMemBlock blk;
} NxDkMem;

typedef struct NxDkImage {
    DkImage dk;
    uint32_t w, h, fmt, flags;     /* fmt: NxFormat, flags: NxImageFlags */
} NxDkImage;

typedef struct NxDk {
    NxGpuApi4 api;
    DkDevice dev;
    DkQueue q;
    DkCmdBuf cmd;                  /* the present's own command buffer: two regions of NXDK_PRESENT_CMD bytes, one per swapchain slot */
    DkMemBlock cmdMem;
    DkFence fence[NXDK_FRAMES];    /* signalled after the present's commands of that region */
    uint32_t fenceLive[NXDK_FRAMES], slot;
    DkSwapchain sc;
    DkMemBlock frontMem, swapMem;
    DkImage swap[NXDK_FRAMES];
    NxDkImage front;               /* image id 0: the swapchain image of rule P3 (RGBA8, zero at start), copied to the display image by PRESENT */
    ViDisplay vi;
    Event vsync;
    uint32_t haveVi, haveVsync;
    NxDkMem mem[NXDK_MAX_MEM + 1];
    uint32_t errCount;
    char lastErr[160];
} NxDk;
extern NxDk nxdk;

/* ---- provided by the embedder ---------------------------------------------------------------------------------------------- */
void* nxdk_guest_ptr(uint64_t addr, uint64_t len);
void nxdk_on_present(uint32_t frame);

/* ---- dk_core.c ---------------------------------------------------------------------------------------------------------------- */
const NxGpuApi4* nxdk_create(uint32_t width, uint32_t height, char* err, size_t errlen);   /* NULL = failed, nothing stays allocated */
void nxdk_destroy(void);
uint64_t nxdk_ext(uint32_t fn, const uint64_t* a, uint32_t n);
uint64_t nxdk_shutdown(void);
uint64_t nxdk_mem_create(uint64_t desc);
uint64_t nxdk_mem_gpu_addr(uint64_t id);
uint64_t nxdk_mem_destroy(uint64_t id);
/* ---- dk_swap.c ---------------------------------------------------------------------------------------------------------------- */
uint32_t nxdk_swap_create(uint32_t width, uint32_t height);   /* 0 = ok; on failure nxdk_swap_destroy still releases the rest */
void nxdk_swap_destroy(void);
uint64_t nxdk_present(uint64_t p);
uint64_t nxdk_wait_vblank(uint64_t output);

/* ---- NXDK_PENDING: required symbols of files not written yet (the link names them, nothing stands in for them) ------------------------------ */
uint64_t nxdk_image_create(uint64_t a);          /* dk_image.c */
uint64_t nxdk_image_create4(uint64_t a);
uint64_t nxdk_image_destroy(uint64_t a);
uint64_t nxdk_image_read(uint64_t a);
uint64_t nxdk_readback(uint64_t a);
uint64_t nxdk_sampler_create(uint64_t a);        /* dk_sampler.c */
uint64_t nxdk_sampler_update(uint64_t a, uint64_t b);
uint64_t nxdk_shader_create(uint64_t a);         /* dk_shader.c */
uint64_t nxdk_shader_destroy(uint64_t a);
uint64_t nxdk_shader_create_d3d(uint64_t a);
uint64_t nxdk_submit(uint64_t a);                /* dk_stream.c */
uint64_t nxdk_wait_frame(uint64_t a);
uint64_t nxdk_query_result(uint64_t a, uint64_t b, uint64_t c);   /* dk_query.c */
/* image by id; id 0 is &nxdk.front; NULL = not a live image (dk_image.c) */
const NxDkImage* nxdk_img(uint32_t id);
/* the compose passes of PRESENT: writes the source rect of `src` into the destination rect of nxdk.front (gamma ramp: 3 x 256 u16 or NULL), then blends
 * the cursor (cw x ch BGRA or NULL), all queued on nxdk.q ahead of the flip (dk_present.c; rule P1: it consumes the memory it reads before it returns) */
void nxdk_present_compose(const NxPresent* p, const NxDkImage* src, const uint16_t* gamma, const uint8_t* cursor);

#ifdef __cplusplus
}
#endif
#endif
