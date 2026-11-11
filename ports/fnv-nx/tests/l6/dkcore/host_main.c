/* Host harness of test_dkcore.py: dk_core.c + dk_swap.c (the real product files) against a recording double of the deko3d / libnx calls they make.
 * The double fixes only the NAMES and signatures of the library calls; what the product does with them is checked here:
 * which calls, in which order, with which flags and sizes. The handlers of files not written yet (dk_image.c ...) are doubles that return a code
 * derived from the function number and the arguments they received, so a misrouted argument or arity is visible. */
#define _GNU_SOURCE
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dk.h"

/* ---- event log ---------------------------------------------------------------------------------------------------------------------------- */
static char evs[2048][48]; static int nev;
static void ev(const char* fmt, ...) { va_list ap; va_start(ap, fmt); if (nev < 2048) vsnprintf(evs[nev++], 48, fmt, ap); va_end(ap); }
static int pos(const char* name) { for (int i = 0; i < nev; i++) if (!strcmp(evs[i], name)) return i; return -1; }
static int cnt(const char* name) { int c = 0; for (int i = 0; i < nev; i++) c += !strcmp(evs[i], name); return c; }
static int last_of(const char* name) { int p = -1; for (int i = 0; i < nev; i++) if (!strcmp(evs[i], name)) p = i; return p; }
static void clear_log(void) { nev = 0; }
static int nchk;
#define CHK(name, cond) do { printf("CHK %s %d\n", name, (cond) ? 1 : 0); nchk++; } while (0)

/* ---- deko3d double --------------------------------------------------------------------------------------------------------------------- */
static int fail_device, live_mb, mb_seq, n_layout; static DkMemBlockMaker mbm[8192]; static int n_mbm;
static DkImageLayoutMaker lay[8]; static DkSwapchainMaker scm; static DkQueueMaker qm; static DkDeviceMaker dm;
static int obj[8]; static int made[8], killed[8];    /* 0 device 1 queue 2 cmdbuf 3 swapchain */
static DkImageView copy_src, copy_dst; static DkImageRect copy_rs, copy_rd; static uint32_t last_interval; static int last_slot = -1, acq_slot = 1;
static int fence_signalled;
DkDevice dkDeviceCreate(DkDeviceMaker const* m) { if (fail_device) return NULL; dm = *m; made[0]++; ev("device"); return (DkDevice)&obj[0]; }
void dkDeviceDestroy(DkDevice d) { (void)d; killed[0]++; ev("device_destroy"); }
static uint64_t mbh[8192];
DkMemBlock dkMemBlockCreate(DkMemBlockMaker const* m) {
    uint64_t h = 0x1000u * (uint64_t)++mb_seq;
    if (n_mbm < 8192) { mbh[n_mbm] = h; mbm[n_mbm++] = *m; }
    live_mb++; return (DkMemBlock)(uintptr_t)h; }
void dkMemBlockDestroy(DkMemBlock b) { (void)b; live_mb--; ev("memblock_destroy"); }
DkGpuAddr dkMemBlockGetGpuAddr(DkMemBlock b) { return 0x100000000ull + (uint64_t)(uintptr_t)b; }
void dkImageLayoutInitialize(DkImageLayout* o, DkImageLayoutMaker const* m) { memset(o, 0, sizeof *o); if (n_layout < 8) lay[n_layout++] = *m; }
uint64_t dkImageLayoutGetSize(DkImageLayout const* o) { (void)o; return 0x384000; }
uint32_t dkImageLayoutGetAlignment(DkImageLayout const* o) { (void)o; return 0x1000; }
void dkImageInitialize(DkImage* o, DkImageLayout const* l, DkMemBlock b, uint32_t off) { (void)l; (void)b; (void)off; memset(o, 0, sizeof *o); ev("image_init"); }
static DkImage const* scimg[2];
DkSwapchain dkSwapchainCreate(DkSwapchainMaker const* m) { scm = *m; scimg[0] = m->pImages[0]; scimg[1] = m->pImages[1]; made[3]++; ev("sc_create"); return (DkSwapchain)&obj[3]; }
void dkSwapchainDestroy(DkSwapchain s) { (void)s; killed[3]++; ev("sc_destroy"); }
void dkSwapchainSetSwapInterval(DkSwapchain s, uint32_t i) { (void)s; last_interval = i; ev("interval"); }
DkQueue dkQueueCreate(DkQueueMaker const* m) { qm = *m; made[1]++; ev("queue"); return (DkQueue)&obj[1]; }
void dkQueueDestroy(DkQueue q) { (void)q; killed[1]++; ev("queue_destroy"); }
void dkQueueWaitIdle(DkQueue q) { (void)q; ev("waitidle"); }
int dkQueueAcquireImage(DkQueue q, DkSwapchain s) { (void)q; (void)s; ev("acquire"); return acq_slot; }
void dkQueueSubmitCommands(DkQueue q, DkCmdList l) { (void)q; (void)l; ev("submit"); }
void dkQueuePresentImage(DkQueue q, DkSwapchain s, int slot) { (void)q; (void)s; last_slot = slot; ev("present"); }
void dkQueueSignalFence(DkQueue q, DkFence* f, bool flush) { (void)q; (void)f; (void)flush; fence_signalled++; ev("signal"); }
DkResult dkFenceWait(DkFence* f, int64_t t) { (void)f; (void)t; ev("fencewait"); return DkResult_Success; }
DkCmdBuf dkCmdBufCreate(DkCmdBufMaker const* m) { (void)m; made[2]++; ev("cmdbuf"); return (DkCmdBuf)&obj[2]; }
void dkCmdBufDestroy(DkCmdBuf c) { (void)c; killed[2]++; ev("cmdbuf_destroy"); }
void dkCmdBufAddMemory(DkCmdBuf c, DkMemBlock m, uint32_t off, uint32_t size) { (void)c; (void)m; (void)off; (void)size; ev("addmem"); }
void dkCmdBufClear(DkCmdBuf c) { (void)c; ev("cmdclear"); }
void dkCmdBufBarrier(DkCmdBuf c, DkBarrier m, uint32_t f) { (void)c; (void)f; ev("barrier%d", (int)m); }
void dkCmdBufCopyImage(DkCmdBuf c, DkImageView const* s, DkImageRect const* rs, DkImageView const* d, DkImageRect const* rd, uint32_t fl) {
    (void)c; (void)fl; copy_src = *s; copy_dst = *d; copy_rs = *rs; copy_rd = *rd; ev("copy"); }
DkCmdList dkCmdBufFinishList(DkCmdBuf c) { (void)c; ev("finish"); return 0; }
/* ---- libnx double ------------------------------------------------------------------------------------------------------------------------ */
static NWindow the_window;
NWindow* nwindowGetDefault(void) { ev("nwindow"); return &the_window; }
static int vi_open_fail;
Result viOpenDisplay(const char* name, ViDisplay* d) { (void)name; memset(d, 0, sizeof *d); ev("vi_open"); return vi_open_fail ? 1 : 0; }
Result viCloseDisplay(ViDisplay* d) { (void)d; ev("vi_close"); return 0; }
Result viGetDisplayVsyncEvent(ViDisplay* d, Event* e) { (void)d; memset(e, 0, sizeof *e); e->revent = 77; ev("vi_vsync"); return 0; }
Result eventWait(Event* e, u64 t) { ev("eventwait%d_%s", (int)e->revent, t == UINT64_MAX ? "inf" : "fin"); return 0; }
Result eventClear(Event* e) { ev("eventclear%d", (int)e->revent); return 0; }
void eventClose(Event* e) { (void)e; ev("event_close"); }
/* ---- embedder hooks and the handlers of files not written yet -------------------------------------------------------------------------------------- */
void* nxdk_guest_ptr(uint64_t a, uint64_t len) { (void)len; return (void*)(uintptr_t)a; }
static uint32_t hook_frame, hook_n; void nxdk_on_present(uint32_t f) { hook_frame = f; hook_n++; ev("hook"); }
static NxDkImage img_ok = { .w = 1280, .h = 720, .fmt = NxFmt_RGBA8, .flags = NxImg_RenderTarget };
static NxDkImage img_depth = { .w = 1280, .h = 720, .fmt = NxFmt_Z24S8, .flags = NxImg_Depth };
const NxDkImage* nxdk_img(uint32_t id) { return id == 5 ? &img_ok : id == 6 ? &img_depth : NULL; }
static const NxPresent* comp_p; static const NxDkImage* comp_s; static const uint16_t* comp_g; static const uint8_t* comp_c;
void nxdk_present_compose(const NxPresent* p, const NxDkImage* s, const uint16_t* g, const uint8_t* c) { comp_p = p; comp_s = s; comp_g = g; comp_c = c; ev("compose"); }
static int hit[24];
uint32_t nxdk_img_init(void) { ev("img_init"); return 0; }
void nxdk_img_shutdown(void) { ev("img_shutdown"); }
uint32_t nxdk_stream_init(void) { return 0; }   /* dk_stream.c has its own test (tests/l6/dkstream) */
void nxdk_stream_shutdown(void) {}
void nxdk_img_describe(uint32_t id, const NxDkImage* im, int srgb) { (void)id; (void)im; (void)srgb; ev("img_describe"); }
#define H1(name, n) uint64_t name(uint64_t a) { hit[n]++; return 0x1000u + n + a * 0x10000; }
H1(nxdk_image_create, 3) H1(nxdk_sampler_create, 4) H1(nxdk_shader_create, 5) H1(nxdk_submit, 6) H1(nxdk_wait_frame, 7) H1(nxdk_readback, 8)
H1(nxdk_image_create4, 10) H1(nxdk_image_destroy, 11) H1(nxdk_shader_destroy, 13) H1(nxdk_image_read, 15) H1(nxdk_shader_create_d3d, 19)
uint64_t nxdk_sampler_update(uint64_t a, uint64_t b) { hit[14]++; return 0x1000u + 14 + a * 0x10000 + b * 0x100000; }
uint64_t nxdk_query_result(uint64_t a, uint64_t b, uint64_t c) { hit[16]++; return 0x1000u + 16 + a * 0x10000 + b * 0x100000 + c * 0x1000000; }

/* ---- scenarios ------------------------------------------------------------------------------------------------------------------------------ */
static uint64_t call(uint32_t fn, uint64_t a0, uint64_t a1, uint64_t a2, uint32_t n) { uint64_t a[3] = { a0, a1, a2 }; return nxdk_ext(fn, a, n); }
static uint64_t P(const void* p) { return (uint64_t)(uintptr_t)p; }
static void reset_doubles(void) { clear_log(); memset(made, 0, sizeof made); memset(killed, 0, sizeof killed); n_layout = 0; n_mbm = 0; hook_n = 0; memset(hit, 0, sizeof hit); }

static void scen_dispatch(void) {
    static const uint32_t argc[20] = { 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 2, 1, 3, 1, 1, 1 };
    static const int pending[] = { 3, 4, 5, 6, 7, 8, 10, 11, 13, 14, 15, 16, 19 };
    char nm[64]; uint64_t out[4] = { 0 }; NxGpuInfo info;
    CHK("fn_out_of_range", call(20, 1, 1, 1, 3) == NXGPU_BAD_CALL && call(0xFFFFFFFFu, 1, 1, 1, 3) == NXGPU_BAD_CALL);
    CHK("null_args_bad", nxdk_ext(NXGPU_FN_MEM_GPU_ADDR, NULL, 1) == NXGPU_BAD_CALL && nxdk_ext(NXGPU_FN_INFO, NULL, 1) == NXGPU_BAD_CALL);
    CHK("info_needs_arg", call(NXGPU_FN_INFO, P(&info), 0, 0, 0) == NXGPU_BAD_CALL);
    CHK("info_ok", call(NXGPU_FN_INFO, P(&info), 0, 0, 1) == 0 && info.magic == NXGPU_MAGIC && info.version == 4 && info.frames_in_flight == 2 &&
        info.width == 1280 && info.height == 720 && info.backend == 1);
    for (uint32_t fn = 1; fn < 20; fn++) {
        if (fn == NXGPU_FN_SHUTDOWN) continue;
        snprintf(nm, sizeof nm, "short_args_bad_%u", fn);
        CHK(nm, call(fn, 1, 2, 3, argc[fn] - 1) == NXGPU_BAD_CALL);
    }
    for (unsigned i = 0; i < sizeof pending / sizeof *pending; i++) {
        uint32_t fn = (uint32_t)pending[i]; uint64_t want = 0x1000u + fn + 1 * 0x10000 + (argc[fn] > 1 ? 2 * 0x100000 : 0) + (argc[fn] > 2 ? 3 * 0x1000000 : 0);
        snprintf(nm, sizeof nm, "routes_fn_%u", fn);
        CHK(nm, call(fn, 1, 2, 3, argc[fn]) == want && hit[fn] == 1);
        snprintf(nm, sizeof nm, "extra_args_ignored_%u", fn);
        CHK(nm, call(fn, 1, 2, 3, 3) == want);
    }
    (void)out;
    clear_log();
    CHK("shutdown_waits_idle", nxdk_ext(NXGPU_FN_SHUTDOWN, NULL, 0) == 0 && cnt("waitidle") == 1);
}

static void scen_mem(void) {
    void* st; NxMemBlockDesc d; uint64_t id[4100]; int n = 0; int base_mb = live_mb;
    CHK("device_blocks_three", base_mb == 3);
    if (posix_memalign(&st, 0x1000, 0x4000)) exit(2);
    d.storage = P(st); d.size = 0x2000; d.kind = NxMem_Generic;
    n_mbm = 0;
    id[0] = call(NXGPU_FN_MEM_CREATE, P(&d), 0, 0, 1); d.kind = NxMem_Code; id[1] = call(NXGPU_FN_MEM_CREATE, P(&d), 0, 0, 1);
    d.kind = NxMem_Image; id[2] = call(NXGPU_FN_MEM_CREATE, P(&d), 0, 0, 1);
    CHK("mem_ids_dense", id[0] == 1 && id[1] == 2 && id[2] == 3);
    const uint32_t cpu = DkMemBlockFlags_CpuCached | DkMemBlockFlags_GpuCached;
    CHK("mem_flags_generic", n_mbm == 3 && mbm[0].flags == cpu && mbm[0].storage == st && mbm[0].size == 0x2000 && mbm[0].device == (DkDevice)&obj[0]);
    CHK("mem_flags_code", mbm[1].flags == (cpu | DkMemBlockFlags_Code) && mbm[1].storage == st);
    CHK("mem_flags_image", mbm[2].flags == (cpu | DkMemBlockFlags_Image) && mbm[2].storage == st);
    CHK("mem_gpu_addr", call(NXGPU_FN_MEM_GPU_ADDR, 1, 0, 0, 1) == 0x100000000ull + mbh[0] && call(NXGPU_FN_MEM_GPU_ADDR, 2, 0, 0, 1) == 0x100000000ull + mbh[1] &&
        call(NXGPU_FN_MEM_GPU_ADDR, 3, 0, 0, 1) == 0x100000000ull + mbh[2]);
    CHK("mem_gpu_addr_distinct", mbh[0] != mbh[1] && call(NXGPU_FN_MEM_GPU_ADDR, 1, 0, 0, 1) != call(NXGPU_FN_MEM_GPU_ADDR, 2, 0, 0, 1));
    CHK("mem_gpu_addr_bad", call(NXGPU_FN_MEM_GPU_ADDR, 0, 0, 0, 1) == NXGPU_BAD_CALL && call(NXGPU_FN_MEM_GPU_ADDR, 9, 0, 0, 1) == NXGPU_BAD_CALL &&
        call(NXGPU_FN_MEM_GPU_ADDR, 5000, 0, 0, 1) == NXGPU_BAD_CALL);
    uint32_t e0 = nxdk.errCount; n_mbm = 0;
    NxMemBlockDesc b1 = { P((char*)st + 0x10), 0x1000, 0 }, b2 = { P(st), 0x1001, 0 }, b3 = { P(st), 0, 0 }, b4 = { 0, 0x1000, 0 }, b5 = { P(st), 0x1000, 3 };
    CHK("mem_create_rejects", call(1, P(&b1), 0, 0, 1) == 0 && call(1, P(&b2), 0, 0, 1) == 0 && call(1, P(&b3), 0, 0, 1) == 0 && call(1, P(&b4), 0, 0, 1) == 0 &&
        call(1, P(&b5), 0, 0, 1) == 0 && n_mbm == 0);
    CHK("mem_create_errors_counted", nxdk.errCount == e0 + 5 && nxdk.lastErr[0]);
    CHK("mem_destroy_ok", call(NXGPU_FN_MEM_DESTROY, 1, 0, 0, 1) == 0 && cnt("memblock_destroy") >= 1);
    CHK("mem_destroy_twice_bad", call(NXGPU_FN_MEM_DESTROY, 1, 0, 0, 1) == NXGPU_BAD_CALL && call(NXGPU_FN_MEM_DESTROY, 0, 0, 0, 1) == NXGPU_BAD_CALL &&
        call(NXGPU_FN_MEM_DESTROY, 4097, 0, 0, 1) == NXGPU_BAD_CALL && call(NXGPU_FN_MEM_DESTROY, 0x100000001ull, 0, 0, 1) == NXGPU_BAD_CALL);
    d.kind = NxMem_Generic;
    CHK("mem_id_reused", call(NXGPU_FN_MEM_CREATE, P(&d), 0, 0, 1) == 1);
    call(NXGPU_FN_MEM_DESTROY, 1, 0, 0, 1); call(NXGPU_FN_MEM_DESTROY, 2, 0, 0, 1); call(NXGPU_FN_MEM_DESTROY, 3, 0, 0, 1);
    for (n = 0; n < 4100; n++) { id[n] = call(NXGPU_FN_MEM_CREATE, P(&d), 0, 0, 1); if (!id[n]) break; }
    CHK("mem_capacity_4096", n == 4096 && id[0] == 1 && id[4095] == 4096);
    int destroyed = 0; for (int i = 0; i < n; i++) destroyed += call(NXGPU_FN_MEM_DESTROY, id[i], 0, 0, 1) == 0;
    CHK("mem_all_destroyed", destroyed == 4096 && live_mb == base_mb);   /* only the device's own blocks (front, swap, present commands) remain */
    free(st);
}

static NxPresent mkp(void) { NxPresent p; memset(&p, 0, sizeof p); p.image = 5; p.sw = 640; p.sh = 360; p.dx = 100; p.dy = 100; p.dw = 640; p.dh = 360; p.interval = 1; p.frame = 7; return p; }
static uint64_t pres(const NxPresent* p) { clear_log(); hook_n = 0; comp_p = NULL; return call(NXGPU_FN_PRESENT, P(p), 0, 0, 1); }
static int untouched(void) { return nev == 0 && hook_n == 0 && comp_p == NULL; }

static void scen_present(void) {
    NxPresent p = mkp(); uint16_t ramp[768]; uint8_t cur[16 * 16 * 4]; memset(ramp, 0, sizeof ramp); memset(cur, 0, sizeof cur);
    CHK("present_ok", pres(&p) == 0 && hook_n == 1 && hook_frame == 7);
    int pc = pos("compose"), pa = pos("acquire"), pcp = pos("copy"), ps = last_of("submit"), pi = pos("interval"), pp = pos("present"), ph = pos("hook");
    CHK("present_order", pc >= 0 && pc < pa && pa < pcp && pcp < ps && ps < pi && pi < pp && pp < ph && last_interval == 1);
    CHK("present_args_to_compose", comp_p == &p && comp_s == &img_ok && comp_g == NULL && comp_c == NULL);
    CHK("present_copy_front_to_slot", last_slot == acq_slot && copy_rs.width == 1280 && copy_rs.height == 720 && copy_rd.width == 1280 && copy_rd.height == 720 &&
        copy_rs.x == 0 && copy_rs.y == 0 && copy_rd.x == 0 && copy_rd.y == 0 && copy_rs.depth == 1 && copy_rd.depth == 1 &&
        copy_src.pImage == &nxdk.front.dk && copy_dst.pImage == &nxdk.swap[acq_slot]);
    CHK("present_barrier_before_copy", pos("barrier4") >= 0 && pos("barrier4") < pcp && pos("cmdclear") < pos("addmem") && pos("addmem") < pcp);
    CHK("present_signals_slot_fence", cnt("signal") == 1 && pos("signal") > pp);
    pres(&p); CHK("present_fence_waited_on_reuse", cnt("fencewait") == 0 || pos("fencewait") < pos("cmdclear"));
    pres(&p); CHK("present_fence_waited_second_round", cnt("fencewait") == 1 && pos("fencewait") < pos("cmdclear"));
    p = mkp(); p.gamma = P(ramp); p.cursor = P(cur); p.cw = 16; p.ch = 16; p.cx = -4; p.cy = 9;
    CHK("present_gamma_cursor_ok", pres(&p) == 0 && comp_g == ramp && comp_c == cur);
    for (uint32_t iv = 0; iv <= 4; iv++) { p = mkp(); p.interval = iv; CHK(iv == 0 ? "interval_0" : iv == 1 ? "interval_1" : iv == 2 ? "interval_2" : iv == 3 ? "interval_3" : "interval_4",
        pres(&p) == 0 && last_interval == iv); }
    p = mkp(); p.interval = 5; CHK("interval_5_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.image = 7; CHK("bad_image", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.image = 6; CHK("depth_image_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.sw = 0; CHK("empty_src_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.dh = -1; CHK("neg_dst_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.sx = 700; CHK("src_outside_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.sx = -1; CHK("src_negative_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.dx = 1000; CHK("dst_outside_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.sx = 0x7FFFFFFF; p.sw = 2; CHK("src_overflow_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.dx = 1; p.dw = 1280; CHK("dst_edge_overflow_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.cursor = P(cur); p.cw = 0; p.ch = 4; CHK("cursor_zero_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.cursor = P(cur); p.cw = 4097; p.ch = 4; CHK("cursor_big_bad", pres(&p) == NXGPU_BAD_CALL && untouched());
    p = mkp(); p.cursor = P(cur); p.cw = 4096; p.ch = 4096; CHK("cursor_4096_ok", pres(&p) == 0);
    p = mkp(); p.cursor = 0; p.cw = 99999; CHK("no_cursor_ignores_size", pres(&p) == 0 && comp_c == NULL);
    CHK("present_null_struct_bad", pres(NULL) == NXGPU_BAD_CALL && untouched());
    CHK("wait_vblank_ok", (clear_log(), call(NXGPU_FN_WAIT_VBLANK, 0, 0, 0, 1)) == 0 && pos("eventclear77") >= 0 && pos("eventclear77") < pos("eventwait77_inf") && cnt("acquire") == 0);
    CHK("wait_vblank_bad_output", call(NXGPU_FN_WAIT_VBLANK, 1, 0, 0, 1) == NXGPU_BAD_CALL && call(NXGPU_FN_WAIT_VBLANK, 0x100000000ull, 0, 0, 1) == NXGPU_BAD_CALL);
}

static void scen_create(void) {
    char err[128] = "";
    reset_doubles(); mb_seq = 0; live_mb = 0;
    const NxGpuApi4* api = nxdk_create(1280, 720, err, sizeof err);
    CHK("create_ok", api != NULL && api == &nxdk.api);
    if (!api) return;
    CHK("device_flags", dm.flags == (DkDeviceFlags_DepthZeroToOne | DkDeviceFlags_OriginUpperLeft | DkDeviceFlags_YAxisPointsUp) && dm.cbDebug != NULL);
    CHK("queue_graphics", (qm.flags & DkQueueFlags_Graphics) != 0 && made[1] == 1);
    CHK("swap_layout", n_layout == 2 && lay[0].flags == (DkImageFlags_UsageRender | DkImageFlags_UsagePresent) && lay[0].format == DkImageFormat_RGBA8_Unorm &&
        lay[0].dimensions[0] == 1280 && lay[0].dimensions[1] == 720);
    CHK("front_layout", lay[1].flags == (DkImageFlags_UsageRender | DkImageFlags_Usage2DEngine) && lay[1].format == DkImageFormat_RGBA8_Unorm &&
        lay[1].dimensions[0] == 1280 && lay[1].dimensions[1] == 720);
    CHK("swap_no_hwcompression", !((lay[0].flags | lay[1].flags) & DkImageFlags_HwCompression));
    CHK("swapchain_made", made[3] == 1 && scm.numImages == 2 && scm.nativeWindow == &the_window && scimg[0] == &nxdk.swap[0] && scimg[1] == &nxdk.swap[1]);
    int zf = 0, zi = 0; for (int i = 0; i < n_mbm; i++) if (mbm[i].flags & DkMemBlockFlags_ZeroFillInit) { zf++; zi += (mbm[i].flags & DkMemBlockFlags_Image) != 0; }
    CHK("front_zero_filled", zf == 1 && zi == 1);
    CHK("sizes_aligned", ({ int ok = 1; for (int i = 0; i < n_mbm; i++) ok &= mbm[i].size && !(mbm[i].size & 0xFFF); ok; }));
    CHK("api_slots", api->v3.mem_create == P(nxdk_mem_create) && api->v3.mem_gpu_addr == P(nxdk_mem_gpu_addr) && api->v3.image_create == P(nxdk_image_create) &&
        api->v3.sampler_create == P(nxdk_sampler_create) && api->v3.shader_create == P(nxdk_shader_create) && api->v3.submit == P(nxdk_submit) &&
        api->v3.wait_frame == P(nxdk_wait_frame) && api->v3.readback == P(nxdk_readback) && api->v3.shutdown == P(nxdk_shutdown) &&
        api->image_create4 == P(nxdk_image_create4) && api->image_destroy == P(nxdk_image_destroy) && api->mem_destroy == P(nxdk_mem_destroy) &&
        api->shader_destroy == P(nxdk_shader_destroy) && api->sampler_update == P(nxdk_sampler_update) && api->image_read == P(nxdk_image_read) &&
        api->query_result == P(nxdk_query_result) && api->present == P(nxdk_present) && api->wait_vblank == P(nxdk_wait_vblank));
    CHK("front_is_image0", nxdk.front.w == 1280 && nxdk.front.h == 720 && nxdk.front.fmt == NxFmt_RGBA8 && (nxdk.front.flags & NxImg_RenderTarget));
    scen_dispatch(); scen_mem(); scen_present();
    reset_doubles(); live_mb += 0;
    nxdk_destroy();
    int wi = pos("waitidle"), dd = last_of("device_destroy");
    CHK("destroy_order", wi == 0 && dd == nev - 1 && pos("queue_destroy") < dd && pos("sc_destroy") < dd && pos("cmdbuf_destroy") < dd);
    CHK("destroy_balanced", made[0] == 0 && killed[0] == 1 && killed[1] == 1 && killed[2] == 1 && killed[3] == 1 && live_mb == 0);
    /* no swapchain */
    reset_doubles(); mb_seq = 0; live_mb = 0;
    api = nxdk_create(0, 0, err, sizeof err);
    NxGpuInfo info; NxPresent p = mkp();
    CHK("noswap_info", api && call(NXGPU_FN_INFO, P(&info), 0, 0, 1) == 0 && info.width == 0 && info.height == 0 && made[3] == 0 && pos("nwindow") < 0);
    CHK("noswap_present_bad", call(NXGPU_FN_PRESENT, P(&p), 0, 0, 1) == NXGPU_BAD_CALL && call(NXGPU_FN_WAIT_VBLANK, 0, 0, 0, 1) == NXGPU_BAD_CALL);
    CHK("noswap_queue_exists", made[1] == 1);
    nxdk_destroy();
    CHK("noswap_balanced", killed[0] == 1 && killed[1] == 1 && live_mb == 0 && killed[3] == 0);
    /* failures during creation release what was made and report */
    reset_doubles(); mb_seq = 0; live_mb = 0; fail_device = 1; err[0] = 0;
    CHK("device_fail_reported", nxdk_create(1280, 720, err, sizeof err) == NULL && err[0] != 0 && live_mb == 0 && made[3] == 0);
    fail_device = 0; reset_doubles(); mb_seq = 0; live_mb = 0; vi_open_fail = 1; err[0] = 0;
    CHK("vi_fail_reported", nxdk_create(1280, 720, err, sizeof err) == NULL && err[0] != 0 && live_mb == 0 && made[0] == killed[0] && made[1] == killed[1] &&
        made[2] == killed[2] && made[3] == killed[3]);
    vi_open_fail = 0;
}

int main(void) { scen_create(); printf("NCHK %d\n", nchk); return 0; }
