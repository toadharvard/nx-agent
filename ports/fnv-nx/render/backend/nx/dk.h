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
 *   dk_shader.c SHADER_CREATE / SHADER_CREATE_D3D / SHADER_DESTROY (DKSH blobs, the install-time pack)
 *   the stream (contract: the section "The stream" below; until a file exists its symbols are required symbols of the link):
 *   dk_stream.c SUBMIT, WAIT_FRAME, the 28-entry op table, the recording command buffer, ops End Barrier Marker ImageUpdate
 *   dk_draw.c   ops DefineState BindState BindUbo BindIdx BindTextures Draw DrawIndexed Viewport Scissor StencilRef BlendConst DepthBias
 *   dk_vtx.c    ops DefineLayout DefineLayout4 BindVtx BindVtxN, the layout bind and the pre-draw flush
 *   dk_pass.c   ops BeginPass BeginPass4 EndPass Clear, the sRGB-write rebind
 *   dk_scale.c  the point-scale primitive (internal DKSH programs), dk_present.c the compose passes of PRESENT
 *   dk_blit.c   ops Blit GenMips;  dk_query.c  ops QueryBegin QueryEnd, QUERY_RESULT, the submit hooks
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
#include "../nxgpu4.h"
#include "../nxgpu4_shader.h"
#include "../nxgpu4_check.h"
#include "dk_fmt.h"
#include "dk_state.h"

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

/* ---- dk_shader.c ------------------------------------------------------------------------------------------------------------- */
uint64_t nxdk_shader_create(uint64_t a);
uint64_t nxdk_shader_destroy(uint64_t a);
uint64_t nxdk_shader_create_d3d(uint64_t a);
const DkShader* nxdk_shader(uint32_t id);   /* NULL = not a live shader */
void nxdk_shader_release_all(void);        /* destroys every code block; nxdk_destroy calls it before the device goes */
/* image by id (any slot incl. views); id 0 is &nxdk.front; NULL = not a live image (dk_image.c) */
const NxDkImage* nxdk_img(uint32_t id);

/* ==== The stream ================================================================================================================
 * Rules (the GL backend's gl4_stream.c and its op files are the behavioural reference: same acceptance, same messages where the check is
 * backend neutral, same pixels):
 *  S1 SUBMIT is synchronous, like the reference (glFinish): dk_stream.c validates the whole stream (opcode table, overrun, End, then
 *     nxchk_stream with nxdk_chk_env) before anything is recorded, records the ops into nxdk.s.cmd, submits the list, waits for the queue
 *     (dkQueueWaitIdle), then nxdk_query_collect, nxdk_stage_free, the stats. WAIT_FRAME therefore returns 0. An op that reports through
 *     nxdk_err stops the replay; what was recorded before it is still submitted and waited for (the reference executed it), and SUBMIT
 *     returns NXGPU_BAD_CALL.
 *  S2 Only dk_stream.c creates, finishes and submits nxdk.s.cmd. Its memory grows by the command buffer's add-memory callback
 *     (DkCmdBufMaker.cbAddMem) in chunks of NXDK_STREAM_CHUNK listed for release after the wait: no stream is refused for its length.
 *     Every command buffer starts with nxdk_bind_descriptors and a DkInvalidateFlags_Descriptors barrier.
 *     The swapchain image has two ids: colour id 0 of a pass, and NxChkEnv.swapImage of nxdk_chk_env (the first id past the image table,
 *     because swapImage 0 means "no swapchain"). Both name nxdk.front in every op; a file compares against nxdk_chk_env()->swapImage.
 *  S3 An op handler is  void nxdk_op_<snake>(const uint32_t* a, uint32_t argc)  (a = the words after the header word). It checks argc with
 *     the reference's message, records dkCmdBuf* calls into nxdk.s.cmd and updates nxdk.s; it never submits, waits or reads GPU results.
 *     A violated contract is one nxdk_err call and a return; nothing is approximated, skipped or deferred.
 *  S4 State shared between the op files lives in nxdk.s (below) and is written only by the file named next to the field. File-private
 *     tables (layouts, queries, internal programs) are static in their file and reached through the functions below.
 *  S5 Memory blocks are caller backed: the GPU reads guest bytes in place. The file that binds a range (ubo, vertex, index, upload source)
 *     flushes it first with dkMemBlockFlushCpuCache(blk, offset, size) and checks the range against NxDkMem.size.
 *  S6 Pass state is reset by EndPass at the start of every SUBMIT and after its queue wait: inPass = 0, boundState = 0, the index binding stays (reference).
 *  S7 A render-state property that dk_state.h reports as derived (NxDkDerived) is applied by the file named there; a combination this
 *     backend has no rule for yet is rejected with nxdk_err naming the property (never dropped) and listed in the worker's report. */
enum { NXDK_MAX_STATES = 4096, NXDK_MAX_LAYOUTS = 256, NXDK_MAX_QUERIES = 4096, NXDK_STREAM_CHUNK = 0x40000 };
typedef struct NxDkStateRec { NxRenderState rs; uint16_t vs, ps, layout, defined; } NxDkStateRec;
typedef struct NxDkSub { const NxDkImage* im; uint32_t id, level, layer, x, y, w, h; } NxDkSub;   /* a rect of one subresource (blit, scale, present) */
typedef struct NxDkStream {
    DkCmdBuf cmd;                  /* dk_stream.c: the recording buffer; NULL outside SUBMIT */
    NxFrameStats* stats;           /* dk_stream.c: the stats of the running SUBMIT (never NULL inside one); dk_draw.c counts into it */
    NxDkStateRec st[NXDK_MAX_STATES + 1];   /* dk_draw.c (DefineState) */
    uint32_t boundState;           /* dk_draw.c; 0 = none. dk_pass.c and dk_stream.c reset it (S6) */
    uint32_t idxBlock, idxOffset, idxType;   /* dk_draw.c (BindIdx) */
    uint32_t stencilRef;           /* dk_draw.c */
    int32_t scissor[4];            /* dk_draw.c: the last Scissor op (x y w h); dk_pass.c sets it to the full target at BeginPass */
    uint32_t inPass, passW, passH, passSamples, passColors, passSrgb;   /* dk_pass.c: the open pass; passSrgb = colour views are the sRGB ones */
    NxDkSub passColor[4], passDepth;   /* dk_pass.c: the targets of the open pass (im NULL = none) */
    uint32_t frameDraws; uint64_t drawsFrame;   /* dk_stream.c resets per frame number, dk_draw.c counts (reference: g4.frameDraws) */
} NxDkStream;
extern NxDkStream nxdk_s;          /* defined by dk_stream.c; "nxdk.s" in the rules above */

/* ---- dk_stream.c */
uint32_t nxdk_stream_init(void);   /* 0 = ok; called by nxdk_create after nxdk_img_init */
void nxdk_stream_shutdown(void);   /* called by nxdk_destroy while the device lives; the queue is idle */
uint64_t nxdk_submit(uint64_t a);
uint64_t nxdk_wait_frame(uint64_t a);
const NxChkEnv* nxdk_chk_env(void);   /* the neutral view of this backend for nxgpu4_check.c (reference: gl4_check.c) */
const NxDkMem* nxdk_mem(uint32_t id);   /* a live memory block, else NULL (dk_core.c) */
void nxdk_op_end(const uint32_t* a, uint32_t argc);
void nxdk_op_barrier(const uint32_t* a, uint32_t argc);
void nxdk_op_marker(const uint32_t* a, uint32_t argc);
void nxdk_op_image_update(const uint32_t* a, uint32_t argc);   /* through nxdk_image_upload */
/* ---- dk_draw.c */
void nxdk_op_define_state(const uint32_t* a, uint32_t argc);
void nxdk_op_bind_state(const uint32_t* a, uint32_t argc);     /* nxdk_state + nxdk_dyn (dk_state.h), shaders, nxdk_vtx_bind_layout, nxdk_pass_srgb */
void nxdk_op_bind_ubo(const uint32_t* a, uint32_t argc);
void nxdk_op_bind_idx(const uint32_t* a, uint32_t argc);
void nxdk_op_bind_textures(const uint32_t* a, uint32_t argc);  /* handle rule: "Descriptor sets" above */
void nxdk_op_draw(const uint32_t* a, uint32_t argc);
void nxdk_op_draw_indexed(const uint32_t* a, uint32_t argc);
void nxdk_op_viewport(const uint32_t* a, uint32_t argc);
void nxdk_op_scissor(const uint32_t* a, uint32_t argc);
void nxdk_op_stencil_ref(const uint32_t* a, uint32_t argc);
void nxdk_op_blend_const(const uint32_t* a, uint32_t argc);
void nxdk_op_depth_bias(const uint32_t* a, uint32_t argc);
void nxdk_draw_neutral(DkCmdBuf cmd, uint32_t w, uint32_t h);   /* known state for an internal draw onto the w x h single-sample target bound outside any pass: no blend, no tests, all channels, cull none, scissor (0,0,w,h); one error inside a pass; forces the next BindState to re-apply */
/* ---- dk_vtx.c */
void nxdk_op_define_layout(const uint32_t* a, uint32_t argc);
void nxdk_op_define_layout4(const uint32_t* a, uint32_t argc);
void nxdk_op_bind_vtx(const uint32_t* a, uint32_t argc);
void nxdk_op_bind_vtxn(const uint32_t* a, uint32_t argc);
uint32_t nxdk_vtx_bind_layout(uint32_t layout);   /* records the attribute and buffer state of a defined layout; 0 = ok, 1 = nxdk_err'd */
uint32_t nxdk_vtx_flush(void);                    /* before a draw: binds (and flushes, S5) the streams the bound layout reads; 0 = ok */
void nxdk_vtx_shutdown(void);                   /* nxdk_destroy: frees the host copies of the streams and returns every layout and binding to the state before the first op */
/* ---- dk_pass.c */
void nxdk_op_begin_pass(const uint32_t* a, uint32_t argc);
void nxdk_op_begin_pass4(const uint32_t* a, uint32_t argc);
void nxdk_op_end_pass(const uint32_t* a, uint32_t argc);
void nxdk_op_clear(const uint32_t* a, uint32_t argc);
void nxdk_pass_srgb(uint32_t on);                 /* rebinds the colour targets of the open pass with (on) or without their sRGB views; no-change calls record nothing */
/* binds one subresource as the only colour target of `cmd` with a full viewport and scissor (internal draws: scale, compose, mip generation) */
void nxdk_target_bind(DkCmdBuf cmd, const NxDkSub* dst);
/* ---- dk_scale.c: point-filtered scaling by rule (source texel floor((d + 0.5) * sw / dw); reference gl4_scale.c), internal DKSH programs */
uint32_t nxdk_scale_init(void);                   /* 0 = ok; called by nxdk_stream_init */
void nxdk_scale_shutdown(void);
/* ---- dk_msres.c: multisample resolve by shader (texelFetch on the multisampled source), internal DKSH programs; fragment UBO slot 15 and texture slot 31 */
#define NXDK_HAVE_MSRES 1
enum { NXDK_MSRES_AVG = 0, NXDK_MSRES_S0_DEPTH = 1, NXDK_MSRES_S0_STENCIL = 2 };
/* layer `layer` of the 2/4/8-sample 2D image src into the single-sample dst of the same format and size; 0 = recorded, 1 = one nxdk_err */
uint32_t nxdk_ms_resolve(const NxDkImage* src, uint32_t layer, const NxDkImage* dst, uint32_t mode);
/* As nxdk_ms_resolve, recording into `cmd`: either nxdk_s.cmd (inside a SUBMIT, no open pass) or the command buffer of a live NxDkSync outside any SUBMIT
 * (no query slice is open there, so no query pause/resume is recorded, and the stale nxdk_s.inPass is not consulted); 0 = recorded, 1 = one nxdk_err */
uint32_t nxdk_ms_resolve_on(DkCmdBuf cmd, const NxDkImage* src, uint32_t layer, const NxDkImage* dst, uint32_t mode);
uint32_t nxdk_msres_init(void);                   /* 0 = ok; called by nxdk_stream_init after nxdk_scale_init */
void nxdk_msres_shutdown(void);
/* dk_stream.c: nxdk_img_storage for a temporary image; dk_stream.c releases it after the wait of the SUBMIT that covers every command recorded up to
 * that SUBMIT (and at nxdk_stream_shutdown); the caller never releases it; 0 = ok, 1 = one nxdk_err */
uint32_t nxdk_tmp_image(NxDkImage* im);
void nxdk_query_shutdown(void);    /* dk_query.c: frees the query tables; nxdk_destroy calls it while the device lives */
void nxdk_scale_point(DkCmdBuf cmd, const NxDkSub* s, const NxDkSub* d, const uint16_t* ramp);   /* ramp: 3 x 256 u16 or NULL */
/* ---- dk_present.c: the compose passes of PRESENT: writes the source rect of `src` into the destination rect of nxdk.front (gamma ramp: 3 x 256 u16 or
 * NULL), then blends the cursor (cw x ch BGRA or NULL), all queued on nxdk.q ahead of the flip (rule P1: it consumes the memory it reads before it returns).
 * Result: 0 = composed (a NULL or fully outside cursor is composed), 1 = refused or failed with exactly one nxdk_err; the caller then presents nothing */
uint32_t nxdk_present_compose(const NxPresent* p, const NxDkImage* src, const uint16_t* gamma, const uint8_t* cursor);   /* 0 = composed, 1 = refused/failed (one nxdk_err issued) */
/* ---- dk_blit.c */
void nxdk_op_blit(const uint32_t* a, uint32_t argc);
void nxdk_op_gen_mips(const uint32_t* a, uint32_t argc);
/* ---- dk_query.c: occlusion queries counted in slices (reference gl4_query.c: one counter interval per constant set of active queries) */
void nxdk_op_query_begin(const uint32_t* a, uint32_t argc);
void nxdk_op_query_end(const uint32_t* a, uint32_t argc);
uint64_t nxdk_query_result(uint64_t a, uint64_t b, uint64_t c);
void nxdk_query_submit_begin(void);   /* dk_stream.c calls these around the replay, collect after the wait */
void nxdk_query_submit_end(void);
void nxdk_query_collect(void);
void nxdk_query_pause(void);          /* internal draws must not count */
void nxdk_query_resume(void);

#ifdef __cplusplus
}
#endif
#endif
