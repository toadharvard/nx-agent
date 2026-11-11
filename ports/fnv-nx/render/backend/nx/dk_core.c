/* fnv-nx L6 deko3d backend: device, queue, memory blocks, the NxGpuApi4 table and the ext_call dispatch (the table of gl4_core.c, entry for entry).
 * Conventions: device flags DepthZeroToOne | OriginUpperLeft | YAxisPointsUp (the shader contract of dk_layout: clip z in 0..1, y up is the top
 * row as in the GL backend's glClipControl + y negation); one graphics queue; memory blocks are caller backed (storage = the guest's own bytes). */
#include "dk.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

NxDk nxdk;

/* ---- the tables are total ----------------------------------------------------------------------------------------------- */
#define CNT1(a) +1
#define CNT4(a, b, c, d) +1
#define CNT3(a, b, c) +1
enum { N_OPS = 0 NXDK_OP_LIST(CNT1), N_FNS = 2 NXDK_FN_LIST(CNT4) NXDK_FN_EXT_LIST(CNT3) };
_Static_assert((int)N_OPS == (int)NxOp_Count4 && (int)N_FNS == (int)NXGPU_FN_SHADER_CREATE_D3D + 1 && (int)NXGPU_FN_SHADER_CREATE_D3D == (int)NXGPU_FN_COUNT4,
               "dk: the op/function tables cover ABI v4 and the function of v4.1 exactly");
_Static_assert(NXDK_MAX_MEM == 4096, "dk: memory block ids are 1..4096 as in the GL backend");

void nxchk_err(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(nxdk.lastErr, sizeof nxdk.lastErr, fmt, ap);
    va_end(ap);
    nxdk.errCount++;
}

/* ---- memory blocks ---------------------------------------------------------------------------------------------------------- */
static const NxDkMem* live(uint64_t id) { return id && id <= NXDK_MAX_MEM && nxdk.mem[id].used ? &nxdk.mem[id] : NULL; }

uint64_t nxdk_mem_create(uint64_t a) {
    const NxMemBlockDesc* d = (const NxMemBlockDesc*)nxdk_guest_ptr(a, sizeof *d);
    if (!d) { nxdk_err("mem_create: descriptor %llx is not readable", (unsigned long long)a); return 0; }
    if (!d->size || (d->size & (DK_MEMBLOCK_ALIGNMENT - 1))) { nxdk_err("mem_create: size %u is not a non-zero multiple of 4 KiB", d->size); return 0; }
    if (d->kind > NxMem_Image) { nxdk_err("mem_create: kind %u", d->kind); return 0; }
    void* host = d->storage ? nxdk_guest_ptr(d->storage, d->size) : NULL;
    if (!host || ((uintptr_t)host & (DK_MEMBLOCK_ALIGNMENT - 1))) { nxdk_err("mem_create: storage %llx is not 4 KiB aligned memory", (unsigned long long)d->storage); return 0; }
    for (uint32_t id = 1; id <= NXDK_MAX_MEM; id++) {
        if (nxdk.mem[id].used) continue;
        DkMemBlockMaker mk;
        dkMemBlockMakerDefaults(&mk, nxdk.dev, d->size);
        mk.flags = DkMemBlockFlags_CpuCached | DkMemBlockFlags_GpuCached |
                   (d->kind == NxMem_Code ? DkMemBlockFlags_Code : d->kind == NxMem_Image ? DkMemBlockFlags_Image : 0);
        mk.storage = host;
        DkMemBlock b = dkMemBlockCreate(&mk);
        if (!b) { nxdk_err("mem_create: deko3d refused a block of %u bytes", d->size); return 0; }
        nxdk.mem[id] = (NxDkMem){ .storage = d->storage, .size = d->size, .kind = d->kind, .used = 1, .blk = b };
        return id;
    }
    nxdk_err("mem_create: all %d block ids are live", NXDK_MAX_MEM);
    return 0;
}

uint64_t nxdk_mem_gpu_addr(uint64_t id) {
    const NxDkMem* m = live(id);
    if (!m) { nxdk_err("mem_gpu_addr: %llu is not a live block", (unsigned long long)id); return NXGPU_BAD_CALL; }
    return dkMemBlockGetGpuAddr(m->blk);
}

uint64_t nxdk_mem_destroy(uint64_t id) {
    const NxDkMem* m = live(id);
    if (!m) { nxdk_err("mem_destroy: %llu is not a live block", (unsigned long long)id); return NXGPU_BAD_CALL; }
    dkMemBlockDestroy(m->blk);
    memset(&nxdk.mem[id], 0, sizeof nxdk.mem[id]);
    return 0;
}

/* ---- ext_call dispatch ---------------------------------------------------------------------------------------------------- */
static uint64_t api_info(uint64_t out) {
    void* p = nxdk_guest_ptr(out, sizeof nxdk.api.v3.info);
    if (!p) return NXGPU_BAD_CALL;
    memcpy(p, &nxdk.api.v3.info, sizeof nxdk.api.v3.info);
    return 0;
}

uint64_t nxdk_shutdown(void) { dkQueueWaitIdle(nxdk.q); return 0; }

typedef uint64_t (*Fn1)(uint64_t);
typedef uint64_t (*Fn2)(uint64_t, uint64_t);
typedef uint64_t (*Fn3)(uint64_t, uint64_t, uint64_t);
typedef struct { uint32_t argc; Fn1 f1; Fn2 f2; Fn3 f3; } FnEnt;   /* exactly the member of the arity is set */
#define FNENT(id, field, impl, n) [NXGPU_FN_##id] = {n, FNSET##n(impl)},
#define FNENT_EXT(id, impl, n) [NXGPU_FN_##id] = {n, FNSET##n(impl)},
#define FNSET1(f) f, 0, 0
#define FNSET2(f) 0, f, 0
#define FNSET3(f) 0, 0, f
static const FnEnt k_fn[NXGPU_FN_SHADER_CREATE_D3D + 1] = { NXDK_FN_LIST(FNENT) NXDK_FN_EXT_LIST(FNENT_EXT) };

uint64_t nxdk_ext(uint32_t fn, const uint64_t* a, uint32_t n) {
    if (fn == NXGPU_FN_SHUTDOWN) return nxdk_shutdown();
    if (fn > NXGPU_FN_SHADER_CREATE_D3D) return NXGPU_BAD_CALL;
    if (fn == NXGPU_FN_INFO) return n >= 1 && a ? api_info(a[0]) : NXGPU_BAD_CALL;
    const FnEnt* e = &k_fn[fn];
    if (n < e->argc || !a) return NXGPU_BAD_CALL;
    switch (e->argc) {
    case 1: return e->f1(a[0]);
    case 2: return e->f2(a[0], a[1]);
    case 3: return e->f3(a[0], a[1], a[2]);
    }
    return NXGPU_BAD_CALL;   /* argc is 1..3 for every entry of the list */
}

/* ---- create / destroy ------------------------------------------------------------------------------------------------------- */
static void dk_debug(void* user, const char* context, DkResult result, const char* message) {
    (void)user;
    nxdk_err("deko3d %s: result %d: %s", context, (int)result, message);
}

static const NxGpuApi4* fail(char* err, size_t errlen, const char* what) {
    if (err && errlen) snprintf(err, errlen, "%s%s%s", what, nxdk.lastErr[0] ? ": " : "", nxdk.lastErr);
    nxdk_destroy();
    return NULL;
}

const NxGpuApi4* nxdk_create(uint32_t width, uint32_t height, char* err, size_t errlen) {
    memset(&nxdk, 0, sizeof nxdk);
    DkDeviceMaker dm;
    dkDeviceMakerDefaults(&dm);
    dm.cbDebug = dk_debug;
    dm.flags = DkDeviceFlags_DepthZeroToOne | DkDeviceFlags_OriginUpperLeft | DkDeviceFlags_YAxisPointsUp;
    nxdk.dev = dkDeviceCreate(&dm);
    if (!nxdk.dev) return fail(err, errlen, "dkDeviceCreate");
    DkQueueMaker qm;
    dkQueueMakerDefaults(&qm, nxdk.dev);
    qm.flags = DkQueueFlags_Graphics;
    nxdk.q = dkQueueCreate(&qm);
    if (!nxdk.q) return fail(err, errlen, "dkQueueCreate");
    if (width && height && nxdk_swap_create(width, height)) return fail(err, errlen, "swapchain");
    NxGpuApi4* api = &nxdk.api;
    api->v3.info.magic = NXGPU_MAGIC; api->v3.info.version = NXGPU_VERSION4; api->v3.info.frames_in_flight = NXDK_FRAMES;
    api->v3.info.width = width; api->v3.info.height = height; api->v3.info.backend = 1;
#define SLOT(id, field, impl, n) api->field = (uint64_t)(uintptr_t)impl;
    NXDK_FN_LIST(SLOT)
#undef SLOT
    api->v3.shutdown = (uint64_t)(uintptr_t)nxdk_shutdown;
    return api;
}

/* safe on any partial state: only what exists is released; the queue is idle before anything it may use goes away, the device goes last */
void nxdk_destroy(void) {
    if (nxdk.q) dkQueueWaitIdle(nxdk.q);
    nxdk_swap_destroy();
    for (uint32_t id = 1; id <= NXDK_MAX_MEM; id++)
        if (nxdk.mem[id].used) dkMemBlockDestroy(nxdk.mem[id].blk);
    if (nxdk.q) dkQueueDestroy(nxdk.q);
    if (nxdk.dev) dkDeviceDestroy(nxdk.dev);
    memset(&nxdk, 0, sizeof nxdk);
}
