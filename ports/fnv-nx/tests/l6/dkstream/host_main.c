/* Host harness of test_dkstream.py: dk_stream.c + nxgpu4_check.c (the real product files) against a recording double of the deko3d calls they make.
 * The double fixes only the NAMES and signatures of the library calls; what the product does with them is checked here: which calls, in which order,
 * with which arguments, and what every error path reports. The op handlers of the other stream files, dk_xfer.c / dk_query.c / dk_scale.c hooks and the
 * embedder hooks are recording doubles; a handler double reports the context it ran in (nxdk_s.cmd, stats, boundState, inPass, idxBlock). */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

NxDk nxdk;
static char evs[1024][64]; static int nev;
static void ev(const char* fmt, ...) { va_list ap; va_start(ap, fmt); if (nev < 1024) vsnprintf(evs[nev++], 64, fmt, ap); va_end(ap); }
static int pos(const char* s) { for (int i = 0; i < nev; i++) if (!strcmp(evs[i], s)) return i; return -1; }
static int cnt(const char* s) { int c = 0; for (int i = 0; i < nev; i++) c += !strcmp(evs[i], s); return c; }
static int cntp(const char* p) { int c = 0; for (int i = 0; i < nev; i++) c += !strncmp(evs[i], p, strlen(p)); return c; }
static int nchk;
#define CHK(name, cond) do { int ok_ = (cond) ? 1 : 0; printf("CHK %s %d\n", name, ok_); if (!ok_) fprintf(stderr, "FAILED %s: last error \"%s\" (%u)\n", name, nxdk.lastErr, nxdk.errCount); nchk++; } while (0)
static char nm[96];
#define CHKF(cond, ...) do { snprintf(nm, sizeof nm, __VA_ARGS__); CHK(nm, cond); } while (0)

/* ---- the checks' error sink (dk_core.c's) ------------------------------------------------------------------------------------------------ */
void nxchk_err(const char* fmt, ...) { va_list ap; va_start(ap, fmt); vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap); va_end(ap); nxdk.errCount++; ev("err"); }

/* ---- deko3d double ------------------------------------------------------------------------------------------------------------------------ */
static int obj_dev, obj_q, obj_cmd, fail_cmd, fail_mb, mb_seq, mb_live, cmd_live, n_mbm;
static DkCmdBufMaker cbm; static DkMemBlockMaker mbm[64];
DkCmdBuf dkCmdBufCreate(DkCmdBufMaker const* m) { if (fail_cmd) return NULL; cbm = *m; cmd_live++; ev("cmdbuf"); return (DkCmdBuf)&obj_cmd; }
void dkCmdBufDestroy(DkCmdBuf c) { (void)c; cmd_live--; ev("cmdbuf_destroy"); }
DkMemBlock dkMemBlockCreate(DkMemBlockMaker const* m) {
    if (fail_mb) return NULL;
    if (n_mbm < 64) mbm[n_mbm++] = *m;
    mb_live++; ev("mb%d", mb_seq + 1);
    return (DkMemBlock)(uintptr_t)++mb_seq;
}
void dkMemBlockDestroy(DkMemBlock b) { mb_live--; ev("mbdestroy%d", (int)(uintptr_t)b); }
void dkCmdBufAddMemory(DkCmdBuf c, DkMemBlock m, uint32_t off, uint32_t size) { (void)c; ev("addmem%d_%u_%u", (int)(uintptr_t)m, off, size); }
void dkCmdBufClear(DkCmdBuf c) { (void)c; ev("clear"); }
void dkCmdBufBarrier(DkCmdBuf c, DkBarrier m, uint32_t f) { (void)c; ev("barrier %d %u", (int)m, f); }
DkCmdList dkCmdBufFinishList(DkCmdBuf c) { (void)c; ev("finish"); return (DkCmdList)7; }
void dkQueueSubmitCommands(DkQueue q, DkCmdList l) { (void)q; ev("submit %d", (int)l); }
void dkQueueWaitIdle(DkQueue q) { (void)q; ev("waitidle"); }

/* ---- doubles of the other files ---------------------------------------------------------------------------------------------------------------- */
static int fail_scale; static uint64_t gp_addr, gp_len, gpa[8], gpl[8]; static int gn, box_fail;   /* gpa/gpl: the guest-pointer calls of one SUBMIT in order */
static NxDkImage im_front = { .w = 1280, .h = 720, .d = 1, .mips = 1, .samples = 1, .flags = NxImg_RenderTarget },
    im_col = { .w = 640, .h = 480, .d = 1, .mips = 1, .samples = 1, .flags = NxImg_RenderTarget },
    im_dep = { .w = 640, .h = 480, .d = 1, .mips = 1, .samples = 1, .flags = NxImg_Depth },
    im_view = { .w = 640, .h = 480, .d = 1, .mips = 1, .samples = 1, .isView = 1 },
    im_ms = { .w = 640, .h = 480, .d = 1, .mips = 1, .samples = 4 },
    im_odd = { .w = 64, .h = 32, .d = 3, .mips = 4, .samples = 2, .type = NxImgType_3D, .isView = 1, .flags = NxImg_Depth, .fmt = 99 };
static uint32_t img_asked[16]; static int n_asked;
const NxDkImage* nxdk_img(uint32_t id) {
    if (n_asked < 16) img_asked[n_asked++] = id;
    return id == 0 ? &im_front : id == 5 ? &im_col : id == 6 ? &im_dep : id == 7 ? &im_view : id == 8 ? &im_ms : id == 11 ? &im_odd : NULL;
}
static NxDkMem mem3 = { .storage = 0x10000, .size = 0x4000 }, mem4 = { .storage = 0x20000, .size = 0x100 };
const NxDkMem* nxdk_mem(uint32_t id) { return id == 3 ? &mem3 : id == 4 ? &mem4 : NULL; }
void* nxdk_guest_ptr(uint64_t a, uint64_t len) { gp_addr = a; gp_len = len; if (gn < 8) { gpa[gn] = a; gpl[gn] = len; gn++; } return a == 0 || a == 0xBAD ? NULL : (void*)(uintptr_t)a; }
DkFmt dk_fmt(uint32_t f) { DkFmt r; memset(&r, 0, sizeof r); r.flags = f == 99 ? DKFMT_COMPRESSED : 0; return r; }
uint32_t nxdk_row_bytes(const DkFmt* f, uint32_t w) { (void)f; return w * 4; }
uint32_t nxdk_rows(const DkFmt* f, uint32_t h) { (void)f; return h; }
static uint32_t box_seen[6], box_level, box_layer;
uint32_t nxdk_box_check(const NxDkImage* im, const char* who, uint32_t level, uint32_t layer, const uint32_t box[6]) {
    (void)im; ev("boxcheck"); box_level = level; box_layer = layer; memcpy(box_seen, box, sizeof box_seen);
    if (box_fail) { nxchk_err("%s: box", who); return 1; }
    return 0;
}
static struct { DkCmdBuf cmd; uint32_t id, level, layer, box[6]; const uint8_t* src; uint64_t pitch; } up;
uint32_t nxdk_image_upload(DkCmdBuf cmd, uint32_t id, uint32_t level, uint32_t layer, const uint32_t box[6], const uint8_t* src, uint64_t pitch) {
    up.cmd = cmd; up.id = id; up.level = level; up.layer = layer; memcpy(up.box, box, sizeof up.box); up.src = src; up.pitch = pitch; ev("upload"); return 0;
}
void nxdk_bind_descriptors(DkCmdBuf c) { ev(c == (DkCmdBuf)&obj_cmd ? "bind_desc" : "bind_desc_wrong_cmd"); }
void nxdk_query_submit_begin(void) { ev("qbegin"); }
void nxdk_query_submit_end(void) { ev("qend"); }
void nxdk_query_collect(void) { ev("qcollect"); }
void nxdk_stage_free(void) { ev("stage_free"); }
uint32_t nxdk_scale_init(void) { ev("scale_init"); return fail_scale; }
void nxdk_scale_shutdown(void) { ev("scale_shutdown"); }
static int fail_msres, fail_storage, fail_malloc, img_live, img_seq, img_rel[64];
uint32_t nxdk_msres_init(void) { ev("msres_init"); return fail_msres; }
void nxdk_msres_shutdown(void) { ev("msres_shutdown"); }
/* storage double: blk = 1000 + n; the release double counts every release of block n (a second release of the same block is visible) */
uint32_t nxdk_img_storage(NxDkImage* im) {
    if (fail_storage) { nxchk_err("image: storage refused"); return 1; }
    im->blk = (DkMemBlock)(uintptr_t)(1000 + ++img_seq); img_live++; ev("storage%d", img_seq); return 0;
}
void nxdk_img_release(NxDkImage* im) {
    if (im->blk) { const int n = (int)((uintptr_t)im->blk - 1000); img_live--; if (n < 64) img_rel[n]++; ev("imgrel%d", n); }
    memset(im, 0, sizeof *im);
}
void* __real_malloc(size_t n);
void* __wrap_malloc(size_t n) { return fail_malloc ? NULL : __real_malloc(n); }

/* handler doubles: record the context of every call */
typedef struct { int op; uint32_t argc; const uint32_t* a; DkCmdBuf cmd; NxFrameStats* stats; uint32_t bound, inPass, idx; } Hit;
static Hit hits[256]; static int nhits, fail_op; static void (*draw_hook)(void);
static void hit(int op, const uint32_t* a, uint32_t argc) {
    if (nhits < 256) hits[nhits++] = (Hit){ op, argc, a, nxdk_s.cmd, nxdk_s.stats, nxdk_s.boundState, nxdk_s.inPass, nxdk_s.idxBlock };
    ev("op%d", op);
    if (op == fail_op) nxchk_err("double: op %d fails", op);
    if (op == NxOp_Draw) { nxdk_s.stats->draws++; if (draw_hook) draw_hook(); }
}
#define HD(snake, Name) void nxdk_op_##snake(const uint32_t* a, uint32_t argc) { hit(NxOp_##Name, a, argc); }
HD(define_state, DefineState) HD(define_layout, DefineLayout) HD(begin_pass, BeginPass) HD(end_pass, EndPass) HD(bind_state, BindState) HD(bind_ubo, BindUbo)
HD(bind_vtx, BindVtx) HD(bind_idx, BindIdx) HD(bind_textures, BindTextures) HD(draw, Draw) HD(draw_indexed, DrawIndexed) HD(viewport, Viewport)
HD(scissor, Scissor) HD(stencil_ref, StencilRef) HD(blend_const, BlendConst) HD(depth_bias, DepthBias) HD(clear, Clear) HD(begin_pass4, BeginPass4)
HD(define_layout4, DefineLayout4) HD(bind_vtxn, BindVtxN) HD(blit, Blit) HD(gen_mips, GenMips) HD(query_begin, QueryBegin) HD(query_end, QueryEnd)

/* ---- scenario helpers --------------------------------------------------------------------------------------------------------------------------- */
#define HDR(op, argc) ((uint32_t)(op) | (uint32_t)(argc) << 16)
static uint64_t P(const void* p) { return (uint64_t)(uintptr_t)p; }
static NxFrameStats st;
static uint64_t submit_with(const uint32_t* w, uint32_t n, uint32_t frame, uint32_t present, uint64_t stats) {
    NxSubmit s; memset(&s, 0, sizeof s); s.stream = P(w); s.words = n; s.frame = frame; s.stats = stats; s.present = present;
    memset(&st, 0xAA, sizeof st); gn = 0;
    return nxdk_submit(P(&s));
}
static uint64_t sub(const uint32_t* w, uint32_t n, uint32_t frame) { return submit_with(w, n, frame, 0, P(&st)); }
static void reset(void) {
    nev = 0; nhits = 0; n_mbm = 0; mb_seq = 0; mb_live = 0; cmd_live = 0; fail_cmd = fail_mb = fail_scale = box_fail = fail_msres = fail_storage = fail_malloc = img_live = img_seq = 0; memset(img_rel, 0, sizeof img_rel); fail_op = -1; draw_hook = NULL; n_asked = 0;
    nxdk.errCount = 0; nxdk.lastErr[0] = 0; nxdk.dev = (DkDevice)&obj_dev; nxdk.q = (DkQueue)&obj_q; memset(&up, 0, sizeof up);
}
static int errs_is(const char* s) { return !strcmp(nxdk.lastErr, s); }
static void up_mb(int n_chunk, int extra) { (void)n_chunk; (void)extra; }
static int run_init(void) { reset(); return (int)nxdk_stream_init(); }

/* ---- scenarios ------------------------------------------------------------------------------------------------------------------------------------ */
static void scen_init(void) {
    CHK("init_ok", run_init() == 0 && nxdk.errCount == 0 && nxdk_s.cmd == NULL);
    CHK("init_cmdbuf_maker", cmd_live == 1 && cbm.device == (DkDevice)&obj_dev && cbm.cbAddMem != NULL);
    CHK("init_first_chunk", n_mbm == 1 && mbm[0].size == NXDK_STREAM_CHUNK && mbm[0].flags == (DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached) &&
        mbm[0].device == (DkDevice)&obj_dev && mbm[0].storage == NULL);
    char want[64]; snprintf(want, sizeof want, "addmem1_0_%u", (unsigned)NXDK_STREAM_CHUNK);
    CHK("init_adds_first_chunk", cnt(want) == 1 && pos("cmdbuf") < pos("mb1") && pos("mb1") < pos(want) && pos(want) < pos("scale_init"));
    CHK("init_scale_called_once", cnt("scale_init") == 1);
    CHK("init_msres_after_scale_once", cnt("msres_init") == 1 && pos("scale_init") < pos("msres_init"));
    nxdk_stream_shutdown();
    CHK("shutdown_msres_before_scale", cnt("msres_shutdown") == 1 && pos("msres_shutdown") < pos("scale_shutdown"));
    CHK("shutdown_order", pos("scale_shutdown") >= 0 && pos("scale_shutdown") < pos("cmdbuf_destroy") && pos("cmdbuf_destroy") < pos("mbdestroy1"));
    CHK("shutdown_balanced", cmd_live == 0 && mb_live == 0 && cnt("scale_shutdown") == 1);
    int n = nev; nxdk_stream_shutdown();
    CHK("shutdown_twice_safe", nev == n && cmd_live == 0 && mb_live == 0);
    reset(); fail_cmd = 1;
    CHK("init_cmdbuf_fail_reported", nxdk_stream_init() == 1 && nxdk.errCount == 1 && errs_is("stream: command buffer refused") && cnt("scale_init") == 0);
    nxdk_stream_shutdown();
    CHK("init_cmdbuf_fail_balanced", cmd_live == 0 && mb_live == 0 && cnt("scale_shutdown") == 0);
    reset(); fail_mb = 1;
    CHK("init_block_fail_reported", nxdk_stream_init() == 1 && nxdk.errCount == 1 && errs_is("stream: deko3d refused the first command chunk") && cnt("scale_init") == 0);
    nxdk_stream_shutdown();
    CHK("init_block_fail_balanced", cmd_live == 0 && mb_live == 0 && cnt("scale_shutdown") == 0);
    reset(); fail_scale = 1;
    CHK("init_scale_fail_returned", nxdk_stream_init() == 1 && cnt("scale_init") == 1);
    nxdk_stream_shutdown();
    CHK("init_scale_fail_balanced", cmd_live == 0 && mb_live == 0 && cnt("msres_init") == 0 && cnt("msres_shutdown") == 0);
    reset(); fail_msres = 1;
    CHK("init_msres_fail_returned", nxdk_stream_init() == 1 && cnt("scale_init") == 1 && cnt("msres_init") == 1);
    nxdk_stream_shutdown();
    CHK("init_msres_fail_balanced", cmd_live == 0 && mb_live == 0 && cnt("msres_shutdown") == 1 && cnt("scale_shutdown") == 1 && cnt("msres_init") == 1);
    int m = nev; nxdk_stream_shutdown();
    CHK("init_msres_fail_shutdown_twice", nev == m && cnt("msres_shutdown") == 1);
    reset(); fail_cmd = 1; nxdk_stream_init(); nxdk_stream_shutdown();
    CHK("init_early_fail_no_msres", cnt("msres_init") == 0 && cnt("msres_shutdown") == 0);
    (void)up_mb;
}

static void scen_basic(void) {
    static const uint32_t simple[] = { HDR(NxOp_Marker, 3), 1, 2, 3, HDR(NxOp_Barrier, 1), 0, HDR(NxOp_End, 0) };
    run_init(); nev = 0;
    uint64_t r = sub(simple, 7, 5);
    CHK("submit_ok", r == 0 && nxdk.errCount == 0);
    char full[64], frag[64], first[64]; snprintf(full, sizeof full, "barrier %d %u", (int)DkBarrier_Full, DkInvalidateFlags_Descriptors | DkInvalidateFlags_Shader | DkInvalidateFlags_Image);
    snprintf(frag, sizeof frag, "barrier %d %u", (int)DkBarrier_Fragments, DkInvalidateFlags_Image); snprintf(first, sizeof first, "addmem1_0_%u", (unsigned)NXDK_STREAM_CHUNK);
    CHK("submit_start_barrier", cnt(full) == 1 && cnt("bind_desc") == 1 && cnt("bind_desc_wrong_cmd") == 0);
    CHK("submit_barrier_op", cnt(frag) == 1 && cntp("barrier") == 2);
    CHK("submit_order", pos("bind_desc") < pos(full) && pos(full) < pos("qbegin") && pos("qbegin") < pos(frag) && pos(frag) < pos("qend") && pos("qend") < pos("finish") &&
        pos("finish") < pos("submit 7") && pos("submit 7") < pos("waitidle") && pos("waitidle") < pos("qcollect") && pos("qcollect") < pos("stage_free"));
    CHK("submit_recycles_buffer", pos("stage_free") < pos("clear") && pos("clear") < pos(first) && cnt("clear") == 1 && cnt(first) == 1);
    CHK("submit_once_each", cnt("finish") == 1 && cnt("submit 7") == 1 && cnt("waitidle") == 1 && cnt("qbegin") == 1 && cnt("qend") == 1 && cnt("qcollect") == 1);
    CHK("stats_written", st.frame == 5 && st.cmd_words == 7 && st.draws == 0 && st.state_binds == 0 && st.block_binds == 0 && st.gpu_us == 0 &&
        st.present_interval_us == 0 && st.record_us != 0xAAAAAAAAu);
    CHK("stream_ptr_len", gpl[1] == 28);
    CHK("cmd_published_only_inside", nxdk_s.cmd == NULL && nxdk_s.stats == NULL);
    int n = nev; nev = 0; r = submit_with(simple, 7, 5, 1, P(&st));
    CHK("present_flag_ignored", r == 0 && nev == n);
    nev = 0; r = submit_with(simple, 7, 6, 0, 0);
    CHK("stats_null_not_written", r == 0 && st.frame == 0xAAAAAAAAu && st.cmd_words == 0xAAAAAAAAu);
    CHK("wait_frame_zero", nxdk_wait_frame(0) == 0 && nxdk_wait_frame(12345) == 0 && nxdk_wait_frame(~0ull) == 0);
    nev = 0; r = submit_with(simple, 7, 6, 0, 0xBAD);
    CHK("stats_bad_address", r == NXGPU_BAD_CALL && errs_is("submit: stats address") && gp_len == sizeof st);
    nxdk.errCount = 0; nev = 0;
    nxdk_stream_shutdown();
}

static void scen_map(void) {
    static uint32_t w[1024]; static const uint32_t* exa[28]; static uint32_t exargc[28];
    static const uint32_t argc_of[28] = { 0, 12, 1, 1, 0, 1, 2, 2, 2, 2, 2, 2, 2, 4, 4, 1, 4, 2, 3, 3, 3, 51, 5, 12, 13, 2, 2, 1 };
    uint32_t n = 0;
    run_init(); nev = 0;
    nxdk_s.boundState = 9; nxdk_s.inPass = 1; nxdk_s.idxBlock = 3;
    for (uint32_t op = 1; op < 28; op++) {
        const uint32_t ac = argc_of[op];
        w[n] = HDR(op, ac); exa[op] = w + n + 1; exargc[op] = ac; memset(w + n + 1, 0, ac * 4);
        if (op == NxOp_DefineState) w[n + 1] = 1;
        if (op == NxOp_BeginPass4) { w[n + 1] = 0; w[n + 2] = 6; w[n + 3] = 0; }
        if (op == NxOp_DefineLayout4) w[n + 1] = 1;
        if (op == NxOp_ImageUpdate) { static const uint32_t u[12] = { 5, 0x00020001, 1, 2, 0, 16, 16, 1, 3, 0x100, 0x1000, 64 }; memcpy(w + n + 1, u, sizeof u); }
        n += 1 + ac;
    }
    w[n++] = HDR(NxOp_End, 0); w[n++] = HDR(NxOp_Draw, 0);   /* nothing after End runs */
    uint64_t r = sub(w, n, 1);
    CHK("map_submit_ok", r == 0 && nxdk.errCount == 0);
    int ok = 1, order = 1, k = 0;
    for (uint32_t op = 1; op < 28; op++) {
        if (op == NxOp_Barrier || op == NxOp_Marker || op == NxOp_ImageUpdate) continue;
        ok &= k < nhits && hits[k].op == (int)op && hits[k].a == exa[op] && hits[k].argc == exargc[op];
        k++;
        snprintf(nm, sizeof nm, "map_op_%u", op); CHK(nm, ok); ok = 1;
    }
    CHK("map_hit_count", nhits == 24 && order);
    CHK("map_nothing_after_end", cnt("op11") == 1);
    int ctx = 1; for (int i = 0; i < nhits; i++) ctx &= hits[i].cmd == (DkCmdBuf)&obj_cmd && hits[i].stats != NULL;
    CHK("map_ctx_cmd_stats", ctx);
    CHK("map_s6_reset", hits[0].bound == 0 && hits[0].inPass == 0 && hits[0].idx == 3);
    CHK("map_stats_seen", st.draws == 1);
    CHK("map_image_update_routed", up.cmd == (DkCmdBuf)&obj_cmd && up.id == 5 && up.level == 1 && up.layer == 2 && up.pitch == 64 && up.src == (const uint8_t*)(uintptr_t)(0x10000 + 0x100) &&
        up.box[0] == 1 && up.box[1] == 2 && up.box[2] == 0 && up.box[3] == 16 && up.box[4] == 16 && up.box[5] == 1 && box_level == 1 && box_layer == 2 && cnt("upload") == 1);
    CHK("map_stream_in_one_buffer", cnt("finish") == 1 && cnt("waitidle") == 1);
    nxdk_stream_shutdown();
}

static uint64_t bad_stream(const uint32_t* w, uint32_t n, const char* msg, const char* tag) {
    run_init(); nev = 0; nxdk.errCount = 0;
    uint64_t r = sub(w, n, 1);
    CHKF(r == NXGPU_BAD_CALL && nxdk.errCount == 1 && errs_is(msg), "%s_reported", tag);
    CHKF(nhits == 0 && cntp("barrier") == 0 && cnt("finish") == 0 && cnt("submit 7") == 0 && cnt("waitidle") == 0 && cnt("bind_desc") == 0 && cnt("qbegin") == 0 && cnt("clear") == 0,
         "%s_nothing_recorded", tag);
    CHKF(st.frame == 1 && st.cmd_words == n, "%s_stats_written", tag);
    nxdk_stream_shutdown();
    return r;
}

static void scen_bad(void) {
    static const uint32_t unk[] = { HDR(28, 0), HDR(NxOp_End, 0) }, unk2[] = { HDR(0xFFFF, 1), 0, HDR(NxOp_End, 0) }, over[] = { HDR(NxOp_Draw, 5), 1, 2 },
        noend[] = { HDR(NxOp_Draw, 0), HDR(NxOp_Draw, 1), 7 }, overlate[] = { HDR(NxOp_Marker, 3), 1, 2 },
        badpass[] = { HDR(NxOp_BeginPass4, 3), 0, 99, 0, HDR(NxOp_End, 0) }, nopass[] = { HDR(NxOp_BeginPass4, 3), 0, 0, 0, HDR(NxOp_End, 0) },
        colsw[] = { HDR(NxOp_BeginPass4, 5), 1, 0, 0, 0, 0, HDR(NxOp_End, 0) }, shortpass[] = { HDR(NxOp_BeginPass4, 2), 0, 6, HDR(NxOp_End, 0) },
        w3[] = { HDR(NxOp_DefineState, 12), 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x10, 0, HDR(NxOp_End, 0) },
        stride[] = { HDR(NxOp_BindVtxN, 5), 0, 0, 0, 0, 0x1000, HDR(NxOp_End, 0) }, stream16[] = { HDR(NxOp_BindVtxN, 5), 16, 0, 0, 0, 0, HDR(NxOp_End, 0) };
    static uint32_t lay[60];
    bad_stream(unk, 2, "stream: opcode 28 is not an op of this build", "opcode_28");
    bad_stream(unk2, 3, "stream: opcode 65535 is not an op of this build", "opcode_ffff");
    bad_stream(over, 3, "stream: op 11 overruns the stream", "overrun");
    bad_stream(noend, 3, "stream: no End op", "no_end");
    bad_stream(overlate, 3, "stream: op 19 overruns the stream", "overrun_arg_count");
    bad_stream(unk, 0, "stream: no End op", "empty_stream");
    bad_stream(badpass, 5, "pass: target 0 (image 99) is not a depth-stencil image", "chk_pass_unknown_image");
    bad_stream(nopass, 5, "pass: no target", "chk_pass_no_target");
    bad_stream(shortpass, 4, "BeginPass4: 2 words for 0 colour targets", "chk_pass_short");
    bad_stream(w3, 14, "DefineState 1: reserved w[3] bits 0x10", "chk_reserved_bits");
    bad_stream(stride, 7, "BindVtxN: stream 0 stride 4096 (max 4095)", "chk_stride_limit");
    bad_stream(stream16, 7, "BindVtxN: stream 16 stride 0 (max 4095)", "chk_stream_limit");
    memset(lay, 0, sizeof lay); lay[0] = HDR(NxOp_DefineLayout4, 51); lay[1] = 1; lay[2] = 1;   /* one attribute: Float1, stream 0, location 0, offset 0x4000 */
    lay[2 + 0] = 1; ((uint8_t*)&lay[2])[0] = 1;   /* attrCount = 1 */
    NxVtxAttr4* at = (NxVtxAttr4*)&lay[2 + 1 + 1 + 16]; at->offset = 0x4000; at->type = NxAttr4_Float1; lay[52] = HDR(NxOp_End, 0);
    bad_stream(lay, 53, "DefineLayout4 1: attribute 0 (type 0 stream 0 location 0 offset 16384) is invalid", "chk_attr_offset_limit");
    /* a colour target of id 0 is the swapchain = image slot 0: accepted by the check, resolved through nxdk_img(0) */
    run_init(); nev = 0; n_asked = 0;
    CHK("chk_swap_colour_ok", sub(colsw, 7, 1) == 0 && nxdk.errCount == 0 && nhits == 1);
    int asked0 = 0; for (int i = 0; i < n_asked; i++) asked0 += img_asked[i] == 0;
    CHK("chk_swap_resolved_as_slot0", asked0 >= 1);
    nxdk_stream_shutdown();
}

static void scen_env(void) {
    const NxChkEnv* e = nxdk_chk_env();
    NxChkImage o; memset(&o, 0xFF, sizeof o);
    CHK("env_limits", e->maxVtxStride == 0xFFF && e->maxAttrOffset == 0x3FFF);
    CHK("env_swap_id_is_past_table", e->swapImage == NXDK_IMG_SLOTS && nxdk_img(e->swapImage) == NULL);
    n_asked = 0;
    CHK("env_swap_resolves_front", e->image(e->ctx, e->swapImage, &o) == 1 && img_asked[0] == 0 && o.w == 1280 && o.h == 720 && o.d == 1 && o.mips == 1 && o.samples == 1 &&
        o.type == 0 && o.isView == 0 && o.isDepth == 0 && o.compressed == 0);
    CHK("env_fields_mapped", e->image(e->ctx, 11, &o) == 1 && o.w == 64 && o.h == 32 && o.d == 3 && o.mips == 4 && o.samples == 2 && o.type == NxImgType_3D && o.isView == 1 &&
        o.isDepth == 1 && o.compressed == 1);
    CHK("env_depth_flag", e->image(e->ctx, 6, &o) == 1 && o.isDepth == 1 && o.compressed == 0 && e->image(e->ctx, 5, &o) == 1 && o.isDepth == 0);
    CHK("env_unknown_image", e->image(e->ctx, 9, &o) == 0 && e->image(e->ctx, 0x7FFFFFFF, &o) == 0);
    CHK("env_same_pointer", nxdk_chk_env() == e);
}

static void scen_stop(void) {
    static const uint32_t s[] = { HDR(NxOp_BindState, 0), HDR(NxOp_Draw, 0), HDR(NxOp_Viewport, 0), HDR(NxOp_Barrier, 1), 0, HDR(NxOp_End, 0) };
    run_init(); nev = 0; fail_op = NxOp_Draw;
    uint64_t r = sub(s, 6, 3);
    CHK("stop_bad_call", r == NXGPU_BAD_CALL && nxdk.errCount == 1);
    CHK("stop_later_ops_skipped", cnt("op6") == 1 && cnt("op11") == 1 && cnt("op13") == 0 && cntp("barrier") == 1);
    CHK("stop_recorded_still_submitted", cnt("finish") == 1 && cnt("submit 7") == 1 && cnt("waitidle") == 1 && cnt("qcollect") == 1 && cnt("stage_free") == 1 && cnt("qend") == 1);
    CHK("stop_stats_counted", st.frame == 3 && st.cmd_words == 6 && st.draws == 1);
    fail_op = -1; nxdk.errCount = 0; nev = 0;
    r = sub(s, 6, 4);
    CHK("after_stop_next_submit_ok", r == 0 && cnt("op13") == 1 && cnt("clear") == 1 && nxdk_s.cmd == NULL);
    fail_op = NxOp_BindState; nev = 0; nhits = 0;
    r = sub(s, 6, 4);
    CHK("stop_first_op", r == NXGPU_BAD_CALL && nhits == 1 && cnt("finish") == 1);
    fail_op = -1;
    nxdk_stream_shutdown();
}

static void scen_ops(void) {
    char full[64]; run_init();
    static const uint32_t bar0[] = { HDR(NxOp_Barrier, 0), HDR(NxOp_End, 0) }, bar1[] = { HDR(NxOp_Barrier, 1), 1, HDR(NxOp_End, 0) },
        mk2[] = { HDR(NxOp_Marker, 2), 1, 2, HDR(NxOp_End, 0) }, mk3[] = { HDR(NxOp_Marker, 3), 1, 2, 3, HDR(NxOp_End, 0) }, mk4[] = { HDR(NxOp_Marker, 4), 1, 2, 3, 4, HDR(NxOp_End, 0) };
    nev = 0; CHK("barrier_no_arg", sub(bar0, 2, 1) == NXGPU_BAD_CALL && errs_is("Barrier: 0 words, need 1") && nxdk.errCount == 1 && cntp("barrier") == 1);
    nxdk.errCount = 0; nev = 0; CHK("barrier_kind1", sub(bar1, 3, 1) == NXGPU_BAD_CALL && errs_is("Barrier: kind 1") && nxdk.errCount == 1 && cntp("barrier") == 1);
    nxdk.errCount = 0; nev = 0; CHK("marker_short", sub(mk2, 4, 1) == NXGPU_BAD_CALL && errs_is("Marker: 2 words, need 3") && nxdk.errCount == 1);
    nxdk.errCount = 0; nev = 0; CHK("marker_ok", sub(mk3, 5, 1) == 0 && cntp("barrier") == 1 && nxdk.errCount == 0);
    nev = 0; CHK("marker_extra_words_ok", sub(mk4, 6, 1) == 0 && nxdk.errCount == 0);
    (void)full;
    /* ImageUpdate: image, level | layer << 16, x, y, z, w, h, d, memBlock, offset, size, rowPitch */
    static uint32_t u[1 + 12 + 1]; u[0] = HDR(NxOp_ImageUpdate, 12); u[13] = HDR(NxOp_End, 0);
    static const uint32_t good[12] = { 5, 0, 0, 0, 0, 16, 16, 1, 3, 0x100, 0x1000, 64 };
#define IU(desc, field, val, msg) do { memcpy(u + 1, good, sizeof good); u[1 + (field)] = (val); nxdk.errCount = 0; nev = 0; up.cmd = NULL; \
        CHK("iu_" desc, sub(u, 14, 1) == NXGPU_BAD_CALL && nxdk.errCount == 1 && errs_is(msg) && up.cmd == NULL && cnt("upload") == 0); } while (0)
    memcpy(u + 1, good, sizeof good); nxdk.errCount = 0; nev = 0;
    CHK("iu_good", sub(u, 14, 1) == 0 && nxdk.errCount == 0 && cnt("upload") == 1 && gpa[2] == 0x10100 && gpl[2] == 0x1000);
    u[0] = HDR(NxOp_ImageUpdate, 11); nxdk.errCount = 0; nev = 0; memcpy(u + 1, good, sizeof good); u[12] = HDR(NxOp_End, 0);
    CHK("iu_short", sub(u, 13, 1) == NXGPU_BAD_CALL && errs_is("ImageUpdate: 11 words") && cnt("upload") == 0 && cnt("boxcheck") == 0);
    u[0] = HDR(NxOp_ImageUpdate, 12); u[13] = HDR(NxOp_End, 0);
    IU("image_zero", 0, 0, "ImageUpdate: bad image 0 level 0");
    IU("image_unknown", 0, 9, "ImageUpdate: bad image 9 level 0");
    IU("image_view", 0, 7, "ImageUpdate: bad image 7 level 0");
    IU("image_multisampled", 0, 8, "ImageUpdate: bad image 8 level 0");
    IU("memory_unknown", 8, 99, "ImageUpdate: bad source");
    IU("memory_zero", 8, 0, "ImageUpdate: bad source");
    IU("range_past_block", 9, 0x3F01, "ImageUpdate: bad source");
    IU("range_overflow_32", 9, 0xFFFFFFFFu, "ImageUpdate: bad source");
    IU("pitch_below_row", 11, 63, "ImageUpdate: bad source");
    IU("size_below_need", 10, 1023, "ImageUpdate: bad source");
    memcpy(u + 1, good, sizeof good); u[1 + 10] = 1024; nxdk.errCount = 0; nev = 0;
    CHK("iu_size_exact_need_ok", sub(u, 14, 1) == 0 && cnt("upload") == 1 && gpl[2] == 1024);
    memcpy(u + 1, good, sizeof good); u[1 + 8] = 4; u[1 + 9] = 0; u[1 + 10] = 0x100; u[1 + 11] = 0x1000; u[1 + 5] = 1; u[1 + 6] = 1; nxdk.errCount = 0; nev = 0;
    CHK("iu_one_texel_end_of_block_ok", sub(u, 14, 1) == 0 && gpa[2] == 0x20000 && cnt("upload") == 1);
    memcpy(u + 1, good, sizeof good); box_fail = 1; nxdk.errCount = 0; nev = 0; up.cmd = NULL;
    CHK("iu_box_rejected_once", sub(u, 14, 1) == NXGPU_BAD_CALL && nxdk.errCount == 1 && errs_is("ImageUpdate: box") && cnt("upload") == 0);
    box_fail = 0;
    memcpy(u + 1, good, sizeof good); u[1 + 1] = 0xFFFF0003u; nxdk.errCount = 0; nev = 0;
    CHK("iu_level_layer_split", sub(u, 14, 1) == 0 && box_level == 3 && box_layer == 0xFFFF && up.level == 3 && up.layer == 0xFFFF);
    nxdk_stream_shutdown();
}

static const NxDkMem* gm;
static int hook_n; static uint32_t hook_min[4];
static void grow(void) { for (int i = 0; i < hook_n; i++) cbm.cbAddMem(cbm.userData, (DkCmdBuf)&obj_cmd, hook_min[i]); }

/* temporary images (nxdk_tmp_image): created inside a SUBMIT by the Draw op double, released after its wait, or at shutdown */
static int tmp_n, tmp_rc[4]; static NxDkImage tmp_im[4];
static void tmp_hook(void) { for (int i = 0; i < tmp_n; i++) { memset(&tmp_im[i], 0, sizeof tmp_im[i]); tmp_rc[i] = (int)nxdk_tmp_image(&tmp_im[i]); } }
static void scen_tmp(void) {
    static const uint32_t s[] = { HDR(NxOp_Draw, 0), HDR(NxOp_End, 0) }, e[] = { HDR(NxOp_End, 0) };
    run_init(); nev = 0; draw_hook = tmp_hook; tmp_n = 1;
    CHK("tmp_submit_ok", sub(s, 2, 1) == 0 && tmp_rc[0] == 0 && tmp_im[0].blk == (DkMemBlock)(uintptr_t)1001 && img_seq == 1);
    CHK("tmp_released_after_wait", cnt("imgrel1") == 1 && pos("submit 7") < pos("waitidle") && pos("waitidle") < pos("imgrel1") && img_live == 0);
    CHK("tmp_not_before_wait", pos("storage1") < pos("finish") && pos("finish") < pos("submit 7") && pos("op6") < pos("imgrel1"));
    nev = 0; draw_hook = NULL;
    CHK("tmp_not_released_twice", sub(e, 1, 2) == 0 && cntp("imgrel") == 0 && img_rel[1] == 1);
    nxdk_stream_shutdown();
    CHK("tmp_not_released_again_at_shutdown", img_rel[1] == 1 && img_live == 0);
    run_init(); nev = 0; draw_hook = tmp_hook; tmp_n = 3;
    CHK("tmp_several_per_submit", sub(s, 2, 1) == 0 && tmp_rc[0] == 0 && tmp_rc[1] == 0 && tmp_rc[2] == 0 && img_seq == 3 && cntp("storage") == 3);
    CHK("tmp_several_released_once_after_wait", cntp("imgrel") == 3 && img_rel[1] == 1 && img_rel[2] == 1 && img_rel[3] == 1 && img_live == 0 &&
        pos("waitidle") < pos("imgrel1") && pos("waitidle") < pos("imgrel2") && pos("waitidle") < pos("imgrel3"));
    nev = 0; draw_hook = NULL;
    nxdk_stream_shutdown();
    /* no submit came: released at shutdown, after the queue is idle by contract, exactly once */
    run_init(); nev = 0; tmp_n = 2; tmp_hook();
    CHK("tmp_listed_outside_submit", tmp_rc[0] == 0 && tmp_rc[1] == 0 && img_live == 2 && cntp("imgrel") == 0);
    nxdk_stream_shutdown();
    CHK("tmp_released_at_shutdown", img_live == 0 && img_rel[1] == 1 && img_rel[2] == 1 && cntp("imgrel") == 2 && cmd_live == 0 && mb_live == 0);
    nxdk_stream_shutdown();
    CHK("tmp_shutdown_twice_safe", img_rel[1] == 1 && img_rel[2] == 1 && img_live == 0);
    /* a refusal returns 1, the error is the storage's own (one), nothing is listed */
    run_init(); nev = 0; tmp_n = 1; fail_storage = 1; tmp_hook();
    CHK("tmp_refusal_returns_1", tmp_rc[0] == 1 && nxdk.errCount == 1 && errs_is("image: storage refused") && img_live == 0);
    nxdk_stream_shutdown();
    CHK("tmp_refusal_lists_nothing", cntp("imgrel") == 0 && img_live == 0);
    /* a list node that cannot be allocated: storage released, one error from dk_stream, 1 */
    run_init(); nev = 0; tmp_n = 1; fail_malloc = 1; tmp_hook(); fail_malloc = 0;
    CHK("tmp_node_alloc_fail", tmp_rc[0] == 1 && nxdk.errCount == 1 && errs_is("stream: no host memory for the temporary image list") && img_live == 0 &&
        cnt("imgrel1") == 1 && tmp_im[0].blk == NULL);
    nxdk_stream_shutdown();
    CHK("tmp_node_alloc_fail_balanced", cntp("imgrel") == 1 && img_live == 0 && mb_live == 0 && cmd_live == 0);
    /* a stopped stream is still submitted and waited for: its temporary images go after that wait */
    run_init(); nev = 0; draw_hook = tmp_hook; tmp_n = 1; fail_op = NxOp_Draw;
    CHK("tmp_stopped_submit_released", sub(s, 2, 1) == NXGPU_BAD_CALL && cnt("imgrel1") == 1 && pos("waitidle") < pos("imgrel1") && img_live == 0);
    fail_op = -1; draw_hook = NULL;
    nxdk_stream_shutdown();
}

static void scen_grow(void) {
    static const uint32_t s[] = { HDR(NxOp_Draw, 0), HDR(NxOp_End, 0) };
    char a1[64]; (void)gm;
    run_init(); nev = 0; n_mbm = 0; draw_hook = grow; hook_n = 2; hook_min[0] = 0x100; hook_min[1] = 0x80000;
    CHK("grow_submit_ok", sub(s, 2, 1) == 0 && nxdk.errCount == 0);
    CHK("grow_chunk_sizes", n_mbm == 2 && mbm[0].size == NXDK_STREAM_CHUNK && mbm[1].size == 0x80000);
    CHK("grow_chunk_flags", mbm[0].flags == (DkMemBlockFlags_CpuUncached | DkMemBlockFlags_GpuCached) && mbm[1].flags == mbm[0].flags && mbm[0].device == (DkDevice)&obj_dev);
    snprintf(a1, sizeof a1, "addmem2_0_%u", (unsigned)NXDK_STREAM_CHUNK);
    CHK("grow_added_to_buffer", cnt(a1) == 1 && cnt("addmem3_0_524288") == 1 && pos("op11") < pos(a1) && pos(a1) < pos("addmem3_0_524288") && pos("addmem3_0_524288") < pos("finish"));
    CHK("grow_released_after_wait", pos("waitidle") < pos("mbdestroy2") && pos("waitidle") < pos("mbdestroy3") && cnt("mbdestroy1") == 0 && mb_live == 1);
    char first[64]; snprintf(first, sizeof first, "addmem1_0_%u", (unsigned)NXDK_STREAM_CHUNK);
    CHK("grow_clear_before_release_then_first", pos("clear") < pos("mbdestroy2") && pos("mbdestroy3") < pos(first) && cnt(first) == 1);
    nev = 0; n_mbm = 0; hook_n = 1; hook_min[0] = 0x40001;
    CHK("grow_rounds_to_4k", sub(s, 2, 2) == 0 && n_mbm == 1 && mbm[0].size == 0x41000);
    nev = 0; n_mbm = 0; hook_min[0] = NXDK_STREAM_CHUNK;
    CHK("grow_exact_chunk", sub(s, 2, 3) == 0 && mbm[0].size == NXDK_STREAM_CHUNK);
    nev = 0; n_mbm = 0; hook_min[0] = 0xFFFFF000u;
    CHK("grow_too_big_reported", sub(s, 2, 4) == NXGPU_BAD_CALL && nxdk.errCount == 1 && errs_is("stream: 4294963200 bytes of command memory requested") && n_mbm == 0 &&
        cntp("addmem") == 1 && mb_live == 1 && cnt("finish") == 1);
    nxdk.errCount = 0; nev = 0; fail_mb = 1; hook_min[0] = 0x100;
    CHK("grow_block_refused_reported", sub(s, 2, 5) == NXGPU_BAD_CALL && nxdk.errCount == 1 && errs_is("stream: deko3d refused 262144 bytes of command memory") && cntp("addmem") == 1);
    fail_mb = 0; nxdk.errCount = 0; nev = 0; draw_hook = NULL;
    CHK("grow_next_submit_plain", sub(s, 2, 6) == 0 && n_mbm == 0 && mb_live == 1);
    nxdk_stream_shutdown();
    CHK("grow_shutdown_balanced", mb_live == 0 && cmd_live == 0);
    /* chunks still listed at shutdown (a callback ran, the submit did not recycle) are released too */
    run_init(); nev = 0; draw_hook = NULL;
    cbm.cbAddMem(cbm.userData, (DkCmdBuf)&obj_cmd, 0x10);
    CHK("grow_outside_submit_listed", mb_live == 2);
    nxdk_stream_shutdown();
    CHK("grow_listed_released_at_shutdown", mb_live == 0 && cmd_live == 0);
}

static void scen_frame(void) {
    static const uint32_t s[] = { HDR(NxOp_Draw, 0), HDR(NxOp_End, 0) };
    run_init(); nev = 0;
    nxdk_s.frameDraws = 7; nxdk_s.drawsFrame = 0;
    sub(s, 2, 5);
    CHK("frame_draws_reset_new_frame", nxdk_s.frameDraws == 0 && nxdk_s.drawsFrame == 6);
    nxdk_s.frameDraws = 3; sub(s, 2, 5);
    CHK("frame_draws_kept_same_frame", nxdk_s.frameDraws == 3 && nxdk_s.drawsFrame == 6);
    sub(s, 2, 6);
    CHK("frame_draws_reset_next_frame", nxdk_s.frameDraws == 0 && nxdk_s.drawsFrame == 7);
    nxdk_s.drawsFrame = 0; nxdk_s.frameDraws = 5; sub(s, 2, 0xFFFFFFFFu);
    CHK("frame_number_max_no_wrap", nxdk_s.frameDraws == 0 && nxdk_s.drawsFrame == 0x100000000ull);
    nxdk_s.frameDraws = 5; sub(s, 2, 0xFFFFFFFFu);
    CHK("frame_number_max_kept", nxdk_s.frameDraws == 5);
    nxdk_stream_shutdown();
}

static void scen_args(void) {
    static const uint32_t s[] = { HDR(NxOp_End, 0) };
    run_init(); nev = 0; nxdk.errCount = 0;
    CHK("submit_null_struct", nxdk_submit(0) == NXGPU_BAD_CALL && nxdk.errCount == 1 && errs_is("submit: struct address") && gp_len == sizeof(NxSubmit) && nev == 1);
    nxdk.errCount = 0; nev = 0;
    CHK("submit_bad_stream_address", submit_with((const uint32_t*)0, 1, 1, 0, P(&st)) == NXGPU_BAD_CALL && nxdk.errCount == 1 && errs_is("submit: stream address") && gpl[1] == 4 &&
        cnt("finish") == 0 && cnt("bind_desc") == 0 && st.cmd_words == 1);
    nxdk.errCount = 0; nev = 0;
    CHK("submit_stream_len_bytes", submit_with(s, 1, 9, 0, 0) == 0 && gpl[1] == 4 && gpa[1] == P(s));
    uint32_t big = 0x7FFFFFFF; nxdk.errCount = 0; nev = 0;
    NxSubmit sb; gn = 0; memset(&sb, 0, sizeof sb); sb.stream = 0xBAD; sb.words = big; sb.frame = 1;
    CHK("submit_len_no_overflow", nxdk_submit(P(&sb)) == NXGPU_BAD_CALL && gpl[1] == (uint64_t)big * 4);
    nxdk_stream_shutdown();
}

int main(void) {
    scen_init(); scen_basic(); scen_map(); scen_bad(); scen_env(); scen_stop(); scen_ops(); scen_tmp(); scen_grow(); scen_frame(); scen_args();
    printf("NCHK %d\n", nchk);
    return 0;
}
