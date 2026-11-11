/* fnv-nx L6 deko3d backend: SUBMIT / WAIT_FRAME and the stream executor (rules S1-S7 of dk.h; the GL backend's gl4_stream.c is the behavioural
 * reference). SUBMIT validates the whole stream (opcode table, overrun, End, nxchk_stream through nxdk_chk_env) before anything is recorded, records
 * the ops into the one recording command buffer, submits the list and waits for the queue. An op that violates its contract stops the replay: what
 * was recorded before it is still submitted and waited for, and SUBMIT returns NXGPU_BAD_CALL. NxSubmit.present is not used: nxgpu4.h gives the
 * flip its own function (PRESENT, dk_swap.c), as in the reference. */
#include "dk.h"
#include <stdlib.h>
#include <string.h>
#include <time.h>

NxDkStream nxdk_s;

typedef struct StreamChunk { struct StreamChunk* next; DkMemBlock blk; } StreamChunk;
static DkCmdBuf rec;              /* the recording buffer; published as nxdk_s.cmd only while a SUBMIT runs */
static DkMemBlock firstBlk;       /* the first chunk: lives as long as the buffer, re-added after every clear */
static StreamChunk* extra;        /* chunks added by the callback during one SUBMIT: released once the queue is idle */
static int scaleInit;             /* nxdk_scale_init was called: nxdk_scale_shutdown releases whatever it made */
static int done;                  /* the End op ran */

/* ---- command memory ------------------------------------------------------------------------------------------------------------------------- */
static DkMemBlock cmd_block(uint32_t bytes) {
    DkMemBlockMaker mk;
    dkMemBlockMakerDefaults(&mk, nxdk.dev, bytes);
    mk.flags = DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached;
    return dkMemBlockCreate(&mk);
}

/* DkCmdBufMaker.cbAddMem (deko3d.h: there is no setter): called when the buffer needs `min` more bytes; adds one chunk of at least
 * NXDK_STREAM_CHUNK bytes. A refusal is reported once here; deko3d then raises its own OutOfMemory through the debug callback. */
static void add_mem(void* user, DkCmdBuf cmd, size_t min) {
    (void)user;
    if (min > UINT32_MAX - NXDK_STREAM_CHUNK) { nxdk_err("stream: %llu bytes of command memory requested", (unsigned long long)min); return; }
    const uint32_t bytes = min > NXDK_STREAM_CHUNK ? ((uint32_t)min + DK_MEMBLOCK_ALIGNMENT - 1) / DK_MEMBLOCK_ALIGNMENT * DK_MEMBLOCK_ALIGNMENT
                                                    : NXDK_STREAM_CHUNK;
    StreamChunk* c = (StreamChunk*)malloc(sizeof *c);
    if (!c) { nxdk_err("stream: no host memory for the command chunk list"); return; }
    c->blk = cmd_block(bytes);
    if (!c->blk) { free(c); nxdk_err("stream: deko3d refused %u bytes of command memory", bytes); return; }
    c->next = extra;
    extra = c;
    dkCmdBufAddMemory(cmd, c->blk, 0, bytes);
}

/* the queue is idle: the commands are consumed, so every chunk but the first goes and the buffer restarts on the first */
static void recycle(void) {
    dkCmdBufClear(rec);
    while (extra) {
        StreamChunk* c = extra;
        extra = c->next;
        dkMemBlockDestroy(c->blk);
        free(c);
    }
    dkCmdBufAddMemory(rec, firstBlk, 0, NXDK_STREAM_CHUNK);
}

uint32_t nxdk_stream_init(void) {
    memset(&nxdk_s, 0, sizeof nxdk_s);
    DkCmdBufMaker mk;
    dkCmdBufMakerDefaults(&mk, nxdk.dev);
    mk.cbAddMem = add_mem;
    rec = dkCmdBufCreate(&mk);
    if (!rec) { nxdk_err("stream: command buffer refused"); return 1; }
    firstBlk = cmd_block(NXDK_STREAM_CHUNK);
    if (!firstBlk) { nxdk_err("stream: deko3d refused the first command chunk"); return 1; }
    dkCmdBufAddMemory(rec, firstBlk, 0, NXDK_STREAM_CHUNK);
    scaleInit = 1;
    return nxdk_scale_init();
}

/* safe on any partial state (nxdk_destroy calls it after a failed nxdk_create too); the queue is idle */
void nxdk_stream_shutdown(void) {
    if (scaleInit) nxdk_scale_shutdown();
    if (rec) dkCmdBufDestroy(rec);
    while (extra) {
        StreamChunk* c = extra;
        extra = c->next;
        dkMemBlockDestroy(c->blk);
        free(c);
    }
    if (firstBlk) dkMemBlockDestroy(firstBlk);
    firstBlk = NULL; rec = NULL; scaleInit = 0;
    memset(&nxdk_s, 0, sizeof nxdk_s);
}

/* ---- the checks of nxgpu4_check.c over this backend's objects ---------------------------------------------------------------------------------- */
/* Colour target id 0 is the swapchain image (nxgpu4.h) = nxdk.front = image slot 0, but NxChkEnv.swapImage = 0 means "no swapchain". The checker
 * therefore substitutes NXDK_SWAP_ID, the first id past the image table (no image has it), and chk_image resolves it to the front image; dk_pass.c
 * resolves id 0 itself. CONTRACT-REQUEST: the same id written explicitly by a stream passes the check and is refused by the pass op (nxdk_img: no image). */
enum { NXDK_SWAP_ID = NXDK_IMG_SLOTS, NXDK_MAX_VTX_STRIDE = 0xFFF, NXDK_MAX_ATTR_OFFSET = 0x3FFF };

static int chk_image(void* ctx, uint32_t id, NxChkImage* out) {
    (void)ctx;
    const NxDkImage* im = nxdk_img(id == NXDK_SWAP_ID ? 0 : id);
    if (!im) return 0;
    out->w = im->w; out->h = im->h; out->d = im->d; out->mips = im->mips; out->samples = im->samples;
    out->type = (uint8_t)im->type; out->isView = (uint8_t)im->isView; out->isDepth = (im->flags & NxImg_Depth) != 0;
    out->compressed = (dk_fmt(im->fmt).flags & DKFMT_COMPRESSED) != 0;
    return 1;
}

/* limits: VertexArray::Config::Stride is 12 bits (deko3d engine_3d.h), DkVtxAttribState.offset 14 bits (deko3d.h) */
const NxChkEnv* nxdk_chk_env(void) {
    static NxChkEnv e;
    e.image = chk_image;
    e.swapImage = NXDK_SWAP_ID;
    e.maxVtxStride = NXDK_MAX_VTX_STRIDE;
    e.maxAttrOffset = NXDK_MAX_ATTR_OFFSET;
    return &e;
}

/* ---- ops of this file ------------------------------------------------------------------------------------------------------------------------ */
void nxdk_op_end(const uint32_t* a, uint32_t argc) { (void)a; (void)argc; done = 1; }

/* kind 0: the render targets written so far become sampleable: the fragments are ordered and the texture cache is invalidated */
void nxdk_op_barrier(const uint32_t* a, uint32_t argc) {
    if (argc < 1) { nxdk_err("Barrier: %u words, need 1", argc); return; }
    if (a[0] != 0) { nxdk_err("Barrier: kind %u", a[0]); return; }
    dkCmdBufBarrier(nxdk_s.cmd, DkBarrier_Fragments, DkInvalidateFlags_Image);
}

/* tag, a, b: tags for captures, no effect on rendering (ABI) */
void nxdk_op_marker(const uint32_t* a, uint32_t argc) {
    (void)a;
    if (argc < 3) nxdk_err("Marker: %u words, need 3", argc);
}

/* image, level | layer << 16, x, y, z, w, h, d, memBlock, offset, size, rowPitch */
void nxdk_op_image_update(const uint32_t* a, uint32_t argc) {
    if (argc < 12) { nxdk_err("ImageUpdate: %u words", argc); return; }
    const NxDkImage* im = a[0] ? nxdk_img(a[0]) : NULL;
    const uint32_t level = a[1] & 0xFFFF, layer = a[1] >> 16, box[6] = { a[2], a[3], a[4], a[5], a[6], a[7] };
    if (!im || im->isView || im->samples != 1) { nxdk_err("ImageUpdate: bad image %u level %u", a[0], level); return; }
    if (nxdk_box_check(im, "ImageUpdate", level, layer, box)) return;
    const DkFmt f = dk_fmt(im->fmt);
    const uint32_t rb = nxdk_row_bytes(&f, box[3]), rows = nxdk_rows(&f, box[4]);
    const uint64_t pitch = a[11], need = ((uint64_t)box[5] * rows - 1) * pitch + rb;   /* the box is inside the image: no overflow */
    const NxDkMem* m = nxdk_mem(a[8]);
    const uint8_t* src = m && (uint64_t)a[9] + a[10] <= m->size && pitch >= rb && need <= a[10]
                             ? (const uint8_t*)nxdk_guest_ptr(m->storage + a[9], a[10]) : NULL;
    if (!src) { nxdk_err("ImageUpdate: bad source"); return; }
    nxdk_image_upload(nxdk_s.cmd, a[0], level, layer, box, src, pitch);
}

/* ---- the op table ------------------------------------------------------------------------------------------------------------------------------ */
typedef void (*OpFn)(const uint32_t* a, uint32_t argc);
/* one line per op of NXDK_OP_LIST: an op without a handler here is a compile error (undeclared OPFN_<name>) */
#define OPFN_End nxdk_op_end
#define OPFN_DefineState nxdk_op_define_state
#define OPFN_DefineLayout nxdk_op_define_layout
#define OPFN_BeginPass nxdk_op_begin_pass
#define OPFN_EndPass nxdk_op_end_pass
#define OPFN_Barrier nxdk_op_barrier
#define OPFN_BindState nxdk_op_bind_state
#define OPFN_BindUbo nxdk_op_bind_ubo
#define OPFN_BindVtx nxdk_op_bind_vtx
#define OPFN_BindIdx nxdk_op_bind_idx
#define OPFN_BindTextures nxdk_op_bind_textures
#define OPFN_Draw nxdk_op_draw
#define OPFN_DrawIndexed nxdk_op_draw_indexed
#define OPFN_Viewport nxdk_op_viewport
#define OPFN_Scissor nxdk_op_scissor
#define OPFN_StencilRef nxdk_op_stencil_ref
#define OPFN_BlendConst nxdk_op_blend_const
#define OPFN_DepthBias nxdk_op_depth_bias
#define OPFN_Clear nxdk_op_clear
#define OPFN_Marker nxdk_op_marker
#define OPFN_BeginPass4 nxdk_op_begin_pass4
#define OPFN_DefineLayout4 nxdk_op_define_layout4
#define OPFN_BindVtxN nxdk_op_bind_vtxn
#define OPFN_ImageUpdate nxdk_op_image_update
#define OPFN_Blit nxdk_op_blit
#define OPFN_GenMips nxdk_op_gen_mips
#define OPFN_QueryBegin nxdk_op_query_begin
#define OPFN_QueryEnd nxdk_op_query_end
#define OPENT(name) [NxOp_##name] = OPFN_##name,
static const OpFn k_op[NxOp_Count4] = { NXDK_OP_LIST(OPENT) };

/* An opcode outside the table or an op overrunning the stream makes the whole stream malformed (messages of the reference). */
static int stream_ok(const uint32_t* w, const uint32_t* end) {
    while (w < end) {
        const uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16;
        if (op >= NxOp_Count4 || !k_op[op]) { nxdk_err("stream: opcode %u is not an op of this build", op); return 0; }
        if (w + 1 + argc > end) { nxdk_err("stream: op %u overruns the stream", op); return 0; }
        if (op == NxOp_End) return 1;
        w += 1 + argc;
    }
    nxdk_err("stream: no End op");
    return 0;
}

/* Records until End or the first op that violated its contract (no later op runs after a violation); the stream was validated. */
static void replay(const uint32_t* w) {
    done = 0;
    while (!done) {
        const uint32_t op = w[0] & 0xFFFF, argc = w[0] >> 16, errs = nxdk.errCount;
        k_op[op](w + 1, argc);
        if (nxdk.errCount != errs) return;
        w += 1 + argc;
    }
}

/* S1: record, submit, wait for the queue, then the query results, the staging blocks, the command chunks */
static void execute(const uint32_t* w) {
    nxdk_s.cmd = rec;
    nxdk_s.boundState = 0; nxdk_s.inPass = 0;   /* S6; the index binding stays */
    /* the CPU wrote descriptors, shader code and images since the last list: invalidate the caches that read them */
    nxdk_bind_descriptors(rec);
    dkCmdBufBarrier(rec, DkBarrier_Full, DkInvalidateFlags_Descriptors | DkInvalidateFlags_Shader | DkInvalidateFlags_Image);
    nxdk_query_submit_begin();
    replay(w);
    nxdk_query_submit_end();
    dkQueueSubmitCommands(nxdk.q, dkCmdBufFinishList(rec));
    dkQueueWaitIdle(nxdk.q);
    nxdk_query_collect();
    nxdk_stage_free();
    nxdk_s.cmd = NULL;
    recycle();
}

uint64_t nxdk_submit(uint64_t a) {
    const NxSubmit* s = (const NxSubmit*)nxdk_guest_ptr(a, sizeof *s);
    if (!s) { nxdk_err("submit: struct address"); return NXGPU_BAD_CALL; }
    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    NxFrameStats st;
    memset(&st, 0, sizeof st);
    st.frame = s->frame; st.cmd_words = s->words;
    const uint32_t errs = nxdk.errCount;
    if (nxdk_s.drawsFrame != (uint64_t)s->frame + 1) { nxdk_s.frameDraws = 0; nxdk_s.drawsFrame = (uint64_t)s->frame + 1; }
    const uint32_t* w = (const uint32_t*)nxdk_guest_ptr(s->stream, (uint64_t)s->words * 4);
    if (!w) nxdk_err("submit: stream address");
    else if (stream_ok(w, w + s->words) && nxchk_stream(nxdk_chk_env(), w, w + s->words)) {
        nxdk_s.stats = &st;
        execute(w);
        nxdk_s.stats = NULL;
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    st.record_us = (uint32_t)((t1.tv_sec - t0.tv_sec) * 1000000 + (t1.tv_nsec - t0.tv_nsec) / 1000);
    if (s->stats) {
        void* out = nxdk_guest_ptr(s->stats, sizeof st);
        if (out) memcpy(out, &st, sizeof st); else nxdk_err("submit: stats address");
    }
    return nxdk.errCount != errs ? NXGPU_BAD_CALL : 0;
}

uint64_t nxdk_wait_frame(uint64_t f) { (void)f; return 0; }   /* SUBMIT is synchronous: the queue is idle before it returns (S1) */
