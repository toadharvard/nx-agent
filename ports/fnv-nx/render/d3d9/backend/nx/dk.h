/* fnv-nx L6 deko3d backend (nxgpu v4.1, console side of rule RD8): the shared state, the dispatch lists and the contract between the files of
 * the backend. The GL backend (gl4.h, gl4_core.c) is the behavioural reference; this file mirrors its table so that both tables are total over
 * the same 20 functions and 28 ops.
 *
 * Files of the backend (each <= 300 lines):
 *   dk_core.c   device, queue, memory blocks, the ext_call dispatch, create / destroy / shutdown
 *   dk_swap.c   the swapchain, PRESENT and WAIT_VBLANK (rules P1-P5 of nxgpu4.h)
 *   dk_image.c  IMAGE_CREATE / CREATE4 / DESTROY, the image table, the descriptor sets (rules in the comment "Descriptor sets" below)
 *   dk_xfer.c   the shared transfer rules: box validation, staging memory, row conversion, the upload primitive, the synchronous submit
 *   dk_sampler.c SAMPLER_CREATE / SAMPLER_UPDATE (the ABI sampler word -> DkSampler; dk_state.c is not on this base)
 *   dk_read.c   IMAGE_READ and v3 READBACK
 *   to come, each one defines the symbols listed under NXDK_PENDING below (until then they are required symbols of the link):
 *   dk_shader.c, dk_stream.c (the 28 ops), dk_query.c, dk_present.c (the present compose passes)
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
#include "dk_fmt.h"

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

/* One image-table slot. Slot = image id; the sRGB view of image n is slot n + 1 (isView, base = n, no storage of its own). Slot 0 is nxdk.front. */
typedef struct NxDkImage {
    DkImage dk;
    DkImageLayout lay;
    DkMemBlock blk;                /* the image's own memory block (GpuCached | Image); NULL for a view and for the front image */
    uint32_t w, h, d, fmt, flags;  /* fmt: NxFormat; flags: NxImg_Depth (the format is a depth format), NxImg_Cube (type is cube) */
    uint32_t type, mips, samples, layers, used, isView, base, swizzle;   /* type: NxImageType; layers: 6 for a cube else 1; swizzle: the user nibbles (0 = identity) */
} NxDkImage;

/* Descriptor sets (deko3d example graphics/deko3d, gpu_console.c: the descriptors are written to a CPU-mapped block with dkImageDescriptorInitialize,
 * the set is bound once per command buffer with dkCmdBufBind{Image,Sampler}DescriptorSet, a texture is dkMakeTextureHandle(imageId, samplerId)).
 *   image table   NXDK_IMG_SLOTS = NX_MAX_IMAGES + 2 descriptors, descriptor n belongs to image slot n (an sRGB view is its own slot n + 1)
 *   sampler table NX_MAX_SAMPLERS descriptors, descriptor n belongs to sampler id n
 * so NX_TEX_HANDLE(image, sampler) of the ABI IS the deko3d texture handle (image | sampler << 20, asserted in dk_image.c); dk_stream.c's BindTextures
 * resolves the sRGB choice (NXSMP_SRGB of nxdk_smp(sampler)) with nxdk_img_view_id and calls dkMakeTextureHandle. Both sets are bound by
 * nxdk_bind_descriptors at the start of every command buffer; descriptors are written by the CPU when an image or sampler is created, so dk_stream.c
 * starts each command buffer with a DkInvalidateFlags_Descriptors barrier (the example does the same after writing descriptors). */
enum { NXDK_IMG_SLOTS = NX_MAX_IMAGES + 2, NXDK_MAX_DIM = 16384 };
typedef struct NxDkStage { struct NxDkStage* next; DkMemBlock blk; } NxDkStage;
typedef struct NxDkSync { DkMemBlock mem; DkCmdBuf cmd; } NxDkSync;

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
    NxDkImage img[NXDK_IMG_SLOTS];
    DkMemBlock descMem;            /* image descriptors, then sampler descriptors */
    DkImageDescriptor* imgDesc;
    DkSamplerDescriptor* smpDesc;
    DkGpuAddr imgDescAddr, smpDescAddr;
    NxSamplerState smp[NX_MAX_SAMPLERS];
    uint32_t nSmp;
    NxDkStage* stage;              /* staging blocks of uploads recorded and not yet waited for */
    uint32_t errCount;
    char lastErr[160];
} NxDk;
extern NxDk nxdk;

/* ---- provided by the embedder ---------------------------------------------------------------------------------------------- */
void* nxdk_guest_ptr(uint64_t addr, uint64_t len);
void nxdk_on_present(uint32_t frame);
/* the install-time shader pack (translated.nxsp, NXSP v1) read into memory: the bytes and their size, NULL when none is installed. No default: a build without the embedder does not link (dk_shader.c). */
const void* nxdk_shader_pack(uint64_t* size);

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

/* ---- dk_image.c ----------------------------------------------------------------------------------------------------------- */
uint32_t nxdk_img_init(void);                    /* 0 = ok: the descriptor memory; before nxdk_swap_create */
void nxdk_img_shutdown(void);                    /* destroys every image and the descriptor memory (the queue is idle) */
uint64_t nxdk_image_create(uint64_t a);
uint64_t nxdk_image_create4(uint64_t a);
uint64_t nxdk_image_destroy(uint64_t a);
/* writes the sampled descriptor of `im` into image-table slot `slot`; srgb = the view of the format's sRGB variant (see Descriptor sets) */
void nxdk_img_describe(uint32_t slot, const NxDkImage* im, int srgb);
/* the image id whose descriptor a texture binding samples: id + 1 when image `id` has a live sRGB view and srgb is set, else id */
uint32_t nxdk_img_view_id(uint32_t id, int srgb);
/* layout + memory block for the fields w h d fmt type mips samples of *im (usage flags by the rule in dk_image.c); 0 = ok, else nxdk_err'd */
uint32_t nxdk_img_storage(NxDkImage* im);
void nxdk_img_release(NxDkImage* im);
/* binds both descriptor sets to `cmd` (the first thing of every command buffer that samples) */
void nxdk_bind_descriptors(DkCmdBuf cmd);
/* ---- dk_sampler.c ----------------------------------------------------------------------------------------------------------- */
uint64_t nxdk_sampler_create(uint64_t a);
uint64_t nxdk_sampler_update(uint64_t a, uint64_t b);
const NxSamplerState* nxdk_smp(uint32_t id);     /* NULL = not a sampler id */
/* ---- dk_xfer.c: the transfer rules shared by upload and read-back ------------------------------------------------------------- */
/* 0 = the box (x y z w h d) of subresource (level, layer) is inside the image and block aligned (GL's rules of ImageUpdate / image_read), else
 * nxdk_err("<who>: ...") and 1 */
uint32_t nxdk_box_check(const NxDkImage* im, const char* who, uint32_t level, uint32_t layer, const uint32_t box[6]);
uint32_t nxdk_row_bytes(const DkFmt* f, uint32_t w);
uint32_t nxdk_rows(const DkFmt* f, uint32_t h);
/* rows of `rb` bytes from src (pitch sp) to dst (pitch dp), n rows; to_dk: Nx layout -> deko3d layout (ROT8: rotr32 per word), else back (rotl32) */
void nxdk_rows_convert(const DkFmt* f, int to_dk, uint8_t* dst, uint64_t dp, const uint8_t* src, uint64_t sp, uint32_t rb, uint64_t n);
/* the view + rect of a box of (level, layer): a cube face is a 2DArray view with z = layer, d = 1; a 3D image uses z / d */
void nxdk_box_view(const NxDkImage* im, uint32_t level, uint32_t layer, const uint32_t box[6], DkImageView* v, DkImageRect* r);
/* Records the upload of the box of (level, layer) of image `id` into `cmd`: validation (nxdk_box_check, image live, not a view, samples == 1,
 * pitch >= row bytes), a staging block with the converted rows (flushed), then dkCmdBufCopyBufferToImage. 0 = recorded, 1 = rejected (nxdk_err).
 * The staging block lives until nxdk_stage_free, which dk_stream.c calls once the queue passed the fence behind `cmd`.
 * src: rows `pitch` bytes apart, slices rows * pitch apart (the guest layout of ImageUpdate). */
uint32_t nxdk_image_upload(DkCmdBuf cmd, uint32_t id, uint32_t level, uint32_t layer, const uint32_t box[6], const uint8_t* src, uint64_t pitch);
void nxdk_stage_free(void);
DkMemBlock nxdk_stage_alloc(uint32_t bytes);     /* CpuUncached | GpuCached, listed for nxdk_stage_free; NULL = refused (nxdk_err) */
uint32_t nxdk_sync_begin(NxDkSync* s);           /* a private command buffer; 0 = ok */
uint32_t nxdk_sync_end(NxDkSync* s);             /* submit, signal, wait, destroy; then nxdk_stage_free; 0 = ok */
/* ---- dk_read.c: nxdk_image_read, nxdk_readback ------------------------------------------------------------------------------- */
uint64_t nxdk_image_read(uint64_t a);
uint64_t nxdk_readback(uint64_t a);

/* ---- NXDK_PENDING: required symbols of files not written yet (the link names them, nothing stands in for them) ------------------------------ */
uint64_t nxdk_shader_create(uint64_t a);         /* dk_shader.c */
uint64_t nxdk_shader_destroy(uint64_t a);
uint64_t nxdk_shader_create_d3d(uint64_t a);
const DkShader* nxdk_shader(uint32_t id);   /* NULL = not a live shader (dk_shader.c) */
void nxdk_shader_release_all(void);        /* destroys every code block; nxdk_destroy must call it before the device goes (dkDeviceDestroy) */
uint64_t nxdk_submit(uint64_t a);                /* dk_stream.c */
uint64_t nxdk_wait_frame(uint64_t a);
uint64_t nxdk_query_result(uint64_t a, uint64_t b, uint64_t c);   /* dk_query.c */
/* image by id (any slot incl. views); id 0 is &nxdk.front; NULL = not a live image (dk_image.c). dk_stream.c fills NxChkImage from it */
const NxDkImage* nxdk_img(uint32_t id);
/* the compose passes of PRESENT: writes the source rect of `src` into the destination rect of nxdk.front (gamma ramp: 3 x 256 u16 or NULL), then blends
 * the cursor (cw x ch BGRA or NULL), all queued on nxdk.q ahead of the flip (dk_present.c; rule P1: it consumes the memory it reads before it returns) */
void nxdk_present_compose(const NxPresent* p, const NxDkImage* src, const uint16_t* gamma, const uint8_t* cursor);

#ifdef __cplusplus
}
#endif
#endif
