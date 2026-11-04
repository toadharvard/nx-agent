/* fnv-nx P6n: libnx stand-ins for running render/native/host/nxgpu.c + libdeko3d (LP64, AArch64) under Unicorn
 * without a GPU (perf/dk/count.py). Every nvdrv / nwindow / kernel service deko3d or nxgpu.c calls answers
 * success at once: no GPU work is done, the CPU side (state capture, command recording, queue submission
 * bookkeeping) runs as on the console. Not counted: kernel time of the real ioctls (nvGpuChannelKickoff etc.). */
#include <stdlib.h>
#include <string.h>
#include <switch.h>

static nvioctl_gpu_characteristics g_chars;
static nvioctl_zcull_info g_zcull;
static u32 g_handle;
static u64 g_iova = 0x100000000ull;
static u32 g_sizes[65536];

Result nvInitialize(void) { return 0; }
void nvExit(void) {}
Result nvMapInit(void) { return 0; }
void nvMapExit(void) {}
Result nvFenceInit(void) { return 0; }
void nvFenceExit(void) {}
Result nvGpuInit(void) {
    g_chars.sm_arch_warp_count = 128; g_chars.num_gpc = 1; g_chars.num_tpc_per_gpc = 2; g_chars.big_page_size = 0x10000;
    g_zcull.width_align_pixels = 32; g_zcull.height_align_pixels = 16; g_zcull.pixel_squares_by_aliquots = 0x400;
    g_zcull.aliquot_total = 0x800; g_zcull.region_byte_multiplier = 0x20; g_zcull.region_header_size = 0x20;
    g_zcull.subregion_header_size = 0xc0; g_zcull.subregion_width_align_pixels = 0x20;
    g_zcull.subregion_height_align_pixels = 0x40; g_zcull.subregion_count = 0x10;
    return 0;
}
void nvGpuExit(void) {}
const nvioctl_gpu_characteristics* nvGpuGetCharacteristics(void) { nvGpuInit(); return &g_chars; }
const nvioctl_zcull_info* nvGpuGetZcullInfo(void) { nvGpuInit(); return &g_zcull; }
u32 nvGpuGetZcullCtxSize(void) { return 0x50000; }
Result nvGpuGetTimestamp(u64* ts) { *ts = 0; return 0; }

Result nvMapCreate(NvMap* m, void* cpu_addr, u32 size, u32 align, NvKind kind, bool is_cpu_cacheable) {
    (void)align;
    memset(m, 0, sizeof *m);
    m->handle = ++g_handle; m->id = m->handle; m->size = size; m->kind = kind; m->has_init = true;
    m->is_cpu_cacheable = is_cpu_cacheable;
    m->cpu_addr = cpu_addr ? cpu_addr : aligned_alloc(0x1000, (size + 0xFFF) & ~0xFFFu);
    if (!cpu_addr && m->cpu_addr) memset(m->cpu_addr, 0, size);
    g_sizes[m->handle & 0xFFFF] = size;
    return m->cpu_addr ? 0 : 1;
}
void nvMapClose(NvMap* m) { (void)m; }

Result nvAddressSpaceCreate(NvAddressSpace* a, u32 page_size) { memset(a, 0, sizeof *a); a->page_size = page_size; a->has_init = true; return 0; }
void nvAddressSpaceClose(NvAddressSpace* a) { (void)a; }
Result nvAddressSpaceAlloc(NvAddressSpace* a, bool sparse, u64 size, iova_t* iova_out) {
    (void)a; (void)sparse; *iova_out = g_iova; g_iova += (size + 0xFFFFF) & ~0xFFFFFull; return 0;
}
Result nvAddressSpaceFree(NvAddressSpace* a, iova_t iova, u64 size) { (void)a; (void)iova; (void)size; return 0; }
Result nvAddressSpaceMap(NvAddressSpace* a, u32 h, bool c, NvKind kind, iova_t* iova_out) {
    (void)a; (void)c; (void)kind; *iova_out = g_iova; g_iova += ((u64)g_sizes[h & 0xFFFF] + 0xFFFFF) & ~0xFFFFFull; return 0;
}
Result nvAddressSpaceMapFixed(NvAddressSpace* a, u32 h, bool c, NvKind kind, iova_t iova) { (void)a; (void)h; (void)c; (void)kind; (void)iova; return 0; }
Result nvAddressSpaceModify(NvAddressSpace* a, iova_t iova, u64 offset, u64 size, NvKind kind) { (void)a; (void)iova; (void)offset; (void)size; (void)kind; return 0; }
Result nvAddressSpaceUnmap(NvAddressSpace* a, iova_t iova) { (void)a; (void)iova; return 0; }

Result nvFenceWait(NvFence* f, s32 t) { (void)f; (void)t; return 0; }
Result nvMultiFenceWait(NvMultiFence* mf, s32 t) { (void)mf; (void)t; return 0; }

static u32 g_fenceValue;
Result nvGpuChannelCreate(NvGpuChannel* c, struct NvAddressSpace* as, NvChannelPriority prio) {
    (void)as; (void)prio; memset(c, 0, sizeof *c); return 0;
}
void nvGpuChannelClose(NvGpuChannel* c) { (void)c; }
Result nvGpuChannelAppendEntry(NvGpuChannel* c, iova_t start, size_t n, u32 flags, u32 thr) {
    (void)start; (void)n; (void)flags; (void)thr; c->num_entries++; return 0;
}
Result nvGpuChannelKickoff(NvGpuChannel* c) { c->fence_incr = 0; c->num_entries = 0; c->fence.value = ++g_fenceValue; return 0; }
Result nvGpuChannelZcullBind(NvGpuChannel* c, iova_t iova) { (void)c; (void)iova; return 0; }
Result nvGpuChannelGetErrorInfo(NvGpuChannel* c, NvError* e) { (void)c; memset(e, 0, sizeof *e); return 0; }
Result nvGpuChannelGetErrorNotification(NvGpuChannel* c, NvNotification* n) { (void)c; memset(n, 0, sizeof *n); return 0; }

static NWindow g_win;
NWindow* nwindowGetDefault(void) { return &g_win; }
static s32 g_slot;
Result nwindowConfigureBuffer(NWindow* nw, s32 slot, NvGraphicBuffer* buf) { (void)nw; (void)slot; (void)buf; return 0; }
Result nwindowDequeueBuffer(NWindow* nw, s32* out_slot, NvMultiFence* f) { (void)nw; *out_slot = g_slot++ % 3; if (f) memset(f, 0, sizeof *f); return 0; }
Result nwindowQueueBuffer(NWindow* nw, s32 slot, const NvMultiFence* f) { (void)nw; (void)slot; (void)f; return 0; }
Result nwindowReleaseBuffers(NWindow* nw) { (void)nw; return 0; }
Result nwindowSetCrop(NWindow* nw, s32 l, s32 t, s32 r, s32 b) { (void)nw; (void)l; (void)t; (void)r; (void)b; return 0; }
Result nwindowSetDimensions(NWindow* nw, u32 w, u32 h) { (void)nw; (void)w; (void)h; return 0; }
Result nwindowSetSwapInterval(NWindow* nw, u32 s) { (void)nw; (void)s; return 0; }
Result nwindowSetTransform(NWindow* nw, u32 t) { (void)nw; (void)t; return 0; }

void armDCacheFlush(void* addr, size_t size) { (void)addr; (void)size; }
void mutexLock(Mutex* m) { (void)m; }
void mutexUnlock(Mutex* m) { (void)m; }
bool mutexTryLock(Mutex* m) { (void)m; return true; }
Result condvarWaitTimeout(CondVar* c, Mutex* m, u64 t) { (void)c; (void)m; (void)t; return 0; }
Result threadCreate(Thread* t, ThreadFunc entry, void* arg, void* stack_mem, size_t stack_sz, int prio, int cpuid) {
    (void)entry; (void)arg; (void)stack_mem; (void)stack_sz; (void)prio; (void)cpuid; memset(t, 0, sizeof *t); return 0;
}
Result threadStart(Thread* t) { (void)t; return 0; }
Result threadWaitForExit(Thread* t) { (void)t; return 0; }
Result threadClose(Thread* t) { (void)t; return 0; }
void NX_NORETURN diagAbortWithResult(Result res) { (void)res; __builtin_trap(); }
/* deko3d is built with -mtp=soft: its (unused) TLS reads go through here */
static u8 g_tls[0x400];
void* __aarch64_read_tp(void) { return g_tls; }
void nxgpu_log(const char* line) { (void)line; }
