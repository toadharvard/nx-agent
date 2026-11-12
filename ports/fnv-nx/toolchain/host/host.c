/* fnv-nx LP64 host module (NSO "main"): libnx process runtime that loads the ILP32 guest module
 * (guest.nso, linked at a fixed address) at its link address, hands it the host API table and runs it.
 * See include/fnvnx/hostapi.h and docs/TOOLCHAIN.md.
 *
 * It also reports the process layout (address space type, regions, where our own image is, whether the
 * reserved original-image range 0x00400000..0x02100000 is free) on the console and in sdmc:/switch/fnv/host.log,
 * which is what the hardware test reads back. Device integration (launcher override, applet pump, crash log, start
 * refusals): host_device.inc. Paths: fnvnx/sdlayout.h.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>
#include <sys/stat.h>
#include <switch.h>

#include "fnvnx/hostapi.h"
#include "fnvnx/sdlayout.h"
#include "../../startup/host/fnvnx_reserve.h"   /* S10: window reservation + relaunch (relative: callers need no -I) */

/* S10's reserve.c + reserve_nx.c are linked into the game host; referenced weakly so a host built without
 * them (an older render/nine test build) still links and runs, only without the reservation. */
#pragma weak fnvnx_reserve_early
#pragma weak fnvnx_reserve_late
#pragma weak fnvnx_reserve_release
#pragma weak fnvnx_reserve_contains
#pragma weak fnvnx_reserve_remap_code
#pragma weak fnvnx_reserve_address_space

/* Guest placement (docs/TOOLCHAIN.md section 9). Default: the guest is relocatable and placed after the
 * reservation, so only the data window is a required range (modelled relaunch rate ~6 %). A host for a
 * fixed-address guest is built with -DFNVNX_GUEST_RESERVE=<bytes> [-DFNVNX_GUEST_LINK_BASE=0x...]: that range is
 * claimed in __libnx_initheap too (adjacent to the data window by default: ~18 %). */
#ifndef FNVNX_GUEST_RESERVE
#define FNVNX_GUEST_RESERVE 0
#endif
#ifndef FNVNX_GUEST_LINK_BASE
#define FNVNX_GUEST_LINK_BASE 0x02100000
#endif
uint64_t fnvnx_guest_reserve_size = FNVNX_GUEST_RESERVE;   /* overrides reserve_nx.c's weak 64 MiB default */
uint64_t fnvnx_guest_link_base = FNVNX_GUEST_LINK_BASE;

/* Original FalloutNV.exe image: headers + .text range (S2: native code must not be here) and the data
 * window 0x00fdf000..0x02100000 (S3 maps the player's .rdata/.data there). */
#define FNVNX_RESERVED_START 0x00400000ull
#define FNVNX_RESERVED_END   0x02100000ull
#define FNVNX_WINDOW_START   0x00fdf000ull
#define FNVNX_SMALL_MAP_END  0x40000000ull
#define FNVNX_LOG_DIR        FNVNX_SD_DIR
#define FNVNX_LOG_PATH       FNVNX_SD_HOST_LOG

/* Host heap for libnx/newlib (services, console, file buffers, the guest file while loading); the rest of
 * the heap region goes to the guest. Weak so a host build can change the split. Multiple of 2 MiB. */
__attribute__((weak)) size_t fnvnx_host_heap_size = 64ull << 20;
/* Seconds the host waits for + after the guest returns before it exits on its own (automated tests). */
__attribute__((weak)) unsigned fnvnx_host_linger_s = 30;
/* Where the guest module is read from. */
__attribute__((weak)) const char *fnvnx_guest_path = FNVNX_SD_GUEST;

static u64 g_guest_heap_base, g_guest_heap_size;
static FILE *g_log;
static FnvnxHostApi g_api;
static PadState g_pad;
static Handle g_own_process = INVALID_HANDLE;

/* ---- heap ---------------------------------------------------------------------------------------------
 * libnx's default asks for all remaining application memory (~3.2 GiB on retail), which svcSetHeapSize
 * refuses in a 32-bit address space (heap region: 1 GiB for 32Bit, 2 GiB for 32BitNoReserved). Cap it at the
 * heap region and split it host/guest. */
void __libnx_initheap(void) {
    extern char *fake_heap_start, *fake_heap_end;
    u64 avail = 0, used = 0, region = 0;
    svcGetInfo(&avail, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&used, InfoType_UsedMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&region, InfoType_HeapRegionSize, CUR_PROCESS_HANDLE, 0);
    u64 size = avail > used + 0x200000 ? (avail - used - 0x200000) & ~0x1FFFFFull : 0;
    if (size > region) size = region & ~0x1FFFFFull;
    if (size < fnvnx_host_heap_size) size = fnvnx_host_heap_size;
    void *addr = NULL;
    if (R_FAILED(svcSetHeapSize(&addr, size)))
        diagAbortWithResult(MAKERESULT(Module_Libnx, LibnxError_HeapAllocFailed));
    fake_heap_start = (char *)addr;
    fake_heap_end = (char *)addr + fnvnx_host_heap_size;
    g_guest_heap_base = (u64)addr + fnvnx_host_heap_size;
    g_guest_heap_size = size - fnvnx_host_heap_size;
    /* S10: claim the fixed windows before anything else can be placed there; the backing comes off the top of
     * the guest heap. */
    if (fnvnx_reserve_early)
        g_guest_heap_size -= fnvnx_reserve_early(g_guest_heap_base, g_guest_heap_base + g_guest_heap_size);
}

/* ---- logging ------------------------------------------------------------------------------------------- */
/* Build with -DFNVNX_HOST_NO_CONSOLE when graphics owns the display (render/nine): no libnx text console;
 * output goes to sdmc:/switch/fnv/host.log and svcOutputDebugString only. */
#ifdef FNVNX_HOST_NO_CONSOLE
#define consoleInit(x) ((void)0)
#define consoleUpdate(x) ((void)0)
#define consoleExit(x) ((void)0)
#endif

static void out(const char *buf, size_t len) {
#ifndef FNVNX_HOST_NO_CONSOLE
    fwrite(buf, 1, len, stdout);
#endif
    if (g_log) { fwrite(buf, 1, len, g_log); fflush(g_log); }
}

static void logf_(const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if ((size_t)n >= sizeof buf) n = sizeof buf - 1;
    out(buf, (size_t)n);
    svcOutputDebugString(buf, (u64)n);
    consoleUpdate(NULL);
}

static void log_line(const char *line) { logf_("%s\n", line); }

#include "host_device.inc"

/* ---- host API implementations (LP64; every parameter and result is u64) -------------------------------- */
static u64 h_output_debug_string(u64 str, u64 len) { return svcOutputDebugString((const char *)str, len); }
static u64 h_sleep_ns(u64 ns) { svcSleepThread(ns > (u64)INT64_MAX ? INT64_MAX : (s64)ns); return 0; }   /* never a negative duration (those are the kernel's yields) */
static u64 h_exit(u64 code) {
    logf_("fnvnx host: guest called exit(%d)\n", (int)code);
    pump_stop();
    cleanup_for_exit("the game called exit");
    if (g_log) fclose(g_log);
    consoleExit(NULL);
    exit((int)code);
}
static u64 h_console_write(u64 buf, u64 len) { out((const char *)buf, len); return len; }
static u64 h_system_tick(void) { return armGetSystemTick(); }
static u64 h_system_tick_freq(void) { return armGetSystemTickFreq(); }

/* ---- platform config: sdmc:/switch/fnv/fnvnx.ini, `key=value` lines (# or ; starts a comment); the build defaults
 * apply when the file or a key is missing. Read once at startup, logged. */
#ifndef FNVNX_BOOST_LOADING_DEFAULT
#define FNVNX_BOOST_LOADING_DEFAULT 1
#endif
#include "placement.h"   /* FNVNX_CORE3_DEFAULT */
#define FNVNX_CONFIG_PATH FNVNX_SD_CONFIG
static struct {
    int core3;           /* placement.h: background roles may use core 3 */
    int boost_loading;   /* CPU boost (ApmCpuBoostMode_FastLoad) while the loading screen runs */
} g_cfg = {FNVNX_CORE3_DEFAULT, FNVNX_BOOST_LOADING_DEFAULT};

static void load_config(void) {
    FILE *f = fopen(FNVNX_CONFIG_PATH, "r");
    char line[160];
    while (f && fgets(line, sizeof line, f)) {
        char *c = strpbrk(line, "#;\r\n");
        if (c) *c = 0;
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = 0;
        char key[64];
        if (sscanf(line, " %63s", key) != 1) continue;
        int v = atoi(eq + 1);
        /* other keys are ignored: game_dir= is the guest's (S5-data: moves C:\FalloutNV off the default) */
        if (!strcmp(key, "core3")) g_cfg.core3 = v != 0;
        else if (!strcmp(key, "boost_loading")) g_cfg.boost_loading = v != 0;
    }
    if (f) fclose(f);
    logf_("fnvnx host: config %s%s: core3=%d boost_loading=%d\n", FNVNX_CONFIG_PATH, f ? "" : " (absent: defaults)",
          g_cfg.core3, g_cfg.boost_loading);
}

#include "host_ids.inc"
#include "host_thread_activity.inc"
#include "host_vm.inc"
#include "host_section.inc"
#include "host_ext.inc"
#include "host_v2.inc"
#include "host_v3.inc"
#include "host_message.inc"
#include "host_textentry.inc"
#include "host_sock.inc"   /* BSD sockets (FNVNX_PLAT_SOCK_*); host_sock_init/exit below */
#include "host_platform.inc"

/* ---- process layout ------------------------------------------------------------------------------------ */
/* In 32-bit spaces Mesosphere reports the ASLR region as [0x00200000, 4 GiB) (alias code region = small +
 * large map), so the type is told from the stack region (= the small map there), as S10's
 * fnvnx_resv_address_space_name does. */
static const char *as_name(void) {
    if (fnvnx_reserve_address_space) return fnvnx_reserve_address_space();
    u64 st = 0, st_size = 0, alias = 0;
    svcGetInfo(&st, InfoType_StackRegionAddress, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&st_size, InfoType_StackRegionSize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&alias, InfoType_AliasRegionSize, CUR_PROCESS_HANDLE, 0);
    if (st + st_size <= FNVNX_SMALL_MAP_END) return alias ? "32Bit" : "32BitNoReserved";
    if (st + st_size <= 0x80000000ull) return "64Bit36";
    return "64Bit39";
}

/* True iff [start, end) is one unmapped region. */
static bool range_free(u64 start, u64 end, MemoryInfo *mi) {
    u32 pi;
    return R_SUCCEEDED(svcQueryMemory(mi, &pi, start)) && mi->type == MemType_Unmapped &&
           mi->addr + mi->size >= end;
}

static void report_layout(void) {
    static const struct { u32 id; const char *name; } regions[] = {
        { InfoType_AslrRegionAddress, "aslr" }, { InfoType_HeapRegionAddress, "heap" },
        { InfoType_AliasRegionAddress, "alias" }, { InfoType_StackRegionAddress, "stack" },
    };
    extern char __start__[] __attribute__((visibility("hidden")));
    extern char __end__[] __attribute__((visibility("hidden")));
    logf_("fnvnx host: LP64 sizeof(void*)=%zu, address space %s, host image %p..%p\n", sizeof(void *),
          as_name(), (void *)__start__, (void *)__end__);
    for (size_t i = 0; i < sizeof regions / sizeof regions[0]; i++) {
        u64 a = 0, s = 0;
        svcGetInfo(&a, regions[i].id, CUR_PROCESS_HANDLE, 0);
        svcGetInfo(&s, regions[i].id + 1, CUR_PROCESS_HANDLE, 0);
        logf_("  region %-5s %#010lx..%#010lx (%lu MiB)\n", regions[i].name, a, a + s, s >> 20);
    }
    u64 total = 0, used = 0;
    svcGetInfo(&total, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&used, InfoType_UsedMemorySize, CUR_PROCESS_HANDLE, 0);
    logf_("  memory total %lu MiB, used %lu MiB, guest heap %#010lx (%lu MiB)\n", total >> 20, used >> 20,
          g_guest_heap_base, g_guest_heap_size >> 20);
    MemoryInfo mi;
    bool free_ = range_free(FNVNX_RESERVED_START, FNVNX_RESERVED_END, &mi);
    logf_("  original image range %#010llx..%#010llx (data window from %#010llx): %s"
          " (region at %#010lx type %u size %#lx)\n", FNVNX_RESERVED_START, FNVNX_RESERVED_END,
          FNVNX_WINDOW_START, free_ ? "free" : "OCCUPIED", mi.addr, mi.type, mi.size);
}

/* ---- own process handle (same technique as nx-hbloader): send CUR_PROCESS_HANDLE as a copy handle to
 * ourselves over a session, receiving it in a helper thread turns the pseudo-handle into a real one. ---- */
static void own_handle_thread(void *arg) {
    Handle session = (Handle)(uintptr_t)arg;
    s32 idx = 0;
    void *tls = armGetTls();
    hipcMakeRequestInline(tls);
    if (R_SUCCEEDED(svcReplyAndReceive(&idx, &session, 1, INVALID_HANDLE, UINT64_MAX))) {
        HipcParsedRequest r = hipcParseRequest(tls);
        if (r.meta.num_copy_handles == 1) g_own_process = r.data.copy_handles[0];
    }
    svcCloseHandle(session);
}

static Result get_own_process_handle(void) {
    Handle server, client;
    Result rc = svcCreateSession(&server, &client, 0, 0);
    if (R_FAILED(rc)) return rc;
    Thread t;
    rc = threadCreate(&t, own_handle_thread, (void *)(uintptr_t)server, NULL, 0x1000, 0x20, -2);
    if (R_SUCCEEDED(rc)) rc = threadStart(&t);
    if (R_FAILED(rc)) { svcCloseHandle(server); svcCloseHandle(client); return rc; }
    hipcMakeRequestInline(armGetTls(), .num_copy_handles = 1).copy_handles[0] = CUR_PROCESS_HANDLE;
    svcSendSyncRequest(client);
    svcCloseHandle(client);
    threadWaitForExit(&t);
    threadClose(&t);
    return g_own_process != INVALID_HANDLE ? 0 : MAKERESULT(Module_Libnx, LibnxError_NotFound);
}

/* ---- guest loader -------------------------------------------------------------------------------------- */
static bool lz4_block(const u8 *src, size_t n, u8 *dst, size_t out_size) {
    size_t i = 0, o = 0;
    while (i < n) {
        u8 tok = src[i++];
        size_t ll = tok >> 4;
        if (ll == 15) { u8 b; do { if (i >= n) return false; b = src[i++]; ll += b; } while (b == 255); }
        if (i + ll > n || o + ll > out_size) return false;
        memcpy(dst + o, src + i, ll);
        i += ll; o += ll;
        if (i >= n) break;
        if (i + 2 > n) return false;
        size_t off = src[i] | ((size_t)src[i + 1] << 8);
        i += 2;
        if (off == 0 || off > o) return false;
        size_t ml = tok & 15;
        if (ml == 15) { u8 b; do { if (i >= n) return false; b = src[i++]; ml += b; } while (b == 255); }
        ml += 4;
        if (o + ml > out_size) return false;
        for (size_t k = 0; k < ml; k++) dst[o + k] = dst[o - off + k];
        o += ml;
    }
    return o == out_size;
}

typedef struct { u32 file_off, dst, size, extra; } NsoSeg;

/* Relocatable guest: first free gap of the small map above the data window (after S10's reservation, so the
 * windows read as mapped and are skipped). Single-threaded here. */
static u64 pick_guest_base(u64 size) {
    u64 a = FNVNX_RESERVED_END;
    while (a + size <= FNVNX_SMALL_MAP_END) {
        MemoryInfo mi; u32 pi;
        if (R_FAILED(svcQueryMemory(&mi, &pi, a))) return 0;
        if (mi.type == MemType_Unmapped && mi.addr + mi.size >= a + size) return a;
        a = (mi.addr + mi.size + 0xFFF) & ~0xFFFull;
    }
    return 0;
}

#define FAIL(...) do { logf_("fnvnx host: guest load failed: " __VA_ARGS__); goto fail; } while (0)

/* Reads an NSO, decompresses and hash-checks it into page-aligned heap memory, then maps that memory as
 * code at the address stored in the guest header (+16) with per-segment permissions, like ldr/hbloader. */
static bool load_guest(const char *path, u64 *out_base, u64 *out_size) {
    u8 *file = NULL, *img = NULL;
    FILE *f = fopen(path, "rb");
    if (!f) FAIL("cannot open %s\n", path);
    fseek(f, 0, SEEK_END);
    long fsz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (fsz < 0x100 || !(file = malloc(fsz)) || fread(file, 1, fsz, f) != (size_t)fsz) {
        fclose(f);
        FAIL("cannot read %s\n", path);
    }
    fclose(f);
    if (memcmp(file, "NSO0", 4) != 0) FAIL("%s is not an NSO\n", path);
    u32 flags;
    NsoSeg seg[3];
    u32 comp[3];
    memcpy(&flags, file + 0xC, 4);
    memcpy(seg, file + 0x10, sizeof seg);
    memcpy(comp, file + 0x60, sizeof comp);
    u64 img_size = ((u64)seg[2].dst + seg[2].size + seg[2].extra + 0xFFF) & ~0xFFFull;
    if (seg[0].dst != 0 || seg[0].dst + seg[0].size > seg[1].dst || seg[1].dst + seg[1].size > seg[2].dst)
        FAIL("bad segment layout\n");
    /* backing of the guest image: off the top of the guest heap (the host heap is small) */
    if (img_size > g_guest_heap_size) FAIL("guest image %#lx larger than the guest heap\n", img_size);
    g_guest_heap_size -= img_size;
    img = (u8 *)(g_guest_heap_base + g_guest_heap_size);
    memset(img, 0, img_size);
    for (int i = 0; i < 3; i++) {
        if ((u64)seg[i].file_off + comp[i] > (u64)fsz) FAIL("segment %d outside file\n", i);
        if (flags & (1u << i)) {
            if (!lz4_block(file + seg[i].file_off, comp[i], img + seg[i].dst, seg[i].size))
                FAIL("segment %d: bad LZ4 data\n", i);
        } else {
            if (comp[i] != seg[i].size) FAIL("segment %d: size mismatch\n", i);
            memcpy(img + seg[i].dst, file + seg[i].file_off, seg[i].size);
        }
        if (flags & (8u << i)) {
            u8 h[SHA256_HASH_SIZE];
            sha256CalculateHash(h, img + seg[i].dst, seg[i].size);
            if (memcmp(h, file + 0xA0 + 0x20 * i, sizeof h) != 0) FAIL("segment %d: SHA-256 mismatch\n", i);
        }
    }
    free(file);
    file = NULL;

    const u32 *w = (const u32 *)img;
    if (w[2] != FNVNX_GUEST_MAGIC || w[3] != FNVNX_HOSTAPI_VERSION) FAIL("not an fnv-nx guest (ABI %u)\n", w[3]);
    if (w[5] != img_size) FAIL("guest header size %#x != image %#lx\n", w[5], img_size);
    bool relocatable = w[6] & 1;
    /* bit 1, pinned: the guest relocates itself but also holds position-dependent references (PC-relative ones to
     * absolute image symbols, toolchain/game/build.sh counts them), so it goes at its link base or nowhere */
    bool pinned = relocatable && (w[6] & 2);
    u64 base = relocatable && !pinned ? pick_guest_base(img_size) : w[4];
    if (!base) FAIL("no free %#lx bytes in the small map for the relocatable guest\n", img_size);
    if (base & 0xFFF || base < 0x200000 || base + img_size > FNVNX_SMALL_MAP_END ||
        (base < FNVNX_RESERVED_END && base + img_size > FNVNX_RESERVED_START))
        FAIL("guest address %#lx..%#lx not allowed\n", base, base + img_size);

    Result rc = get_own_process_handle();
    if (R_FAILED(rc)) FAIL("own process handle: %#x\n", rc);
    if (!relocatable && fnvnx_reserve_contains && fnvnx_reserve_contains(base, img_size)) {
        /* fixed guest inside its claimed range (S10): copy there, convert the same heap pages to code */
        memcpy((void *)base, img, img_size);
        g_guest_heap_size += img_size;   /* the staging copy goes back to the guest heap */
        img = NULL;
        rc = fnvnx_reserve_remap_code("guest", g_own_process, img_size);
        if (R_FAILED(rc)) FAIL("fnvnx_reserve_remap_code: %#x\n", rc);
    } else {
        MemoryInfo mi;
        if (!range_free(base, base + img_size, &mi))
            FAIL("guest range %#lx..%#lx is occupied (region %#lx type %u); relaunch\n", base, base + img_size,
                 mi.addr, mi.type);
        armDCacheFlush(img, img_size);
        rc = svcMapProcessCodeMemory(g_own_process, base, (u64)img, img_size);
        if (R_FAILED(rc)) FAIL("svcMapProcessCodeMemory: %#x\n", rc);
        img = NULL; /* now backs the mapping, its heap alias is inaccessible: never reuse it */
    }
    u64 text_end = ((u64)seg[0].size + 0xFFF) & ~0xFFFull, ro_end = ((u64)seg[1].dst + seg[1].size + 0xFFF) & ~0xFFFull;
    rc = svcSetProcessMemoryPermission(g_own_process, base, text_end, Perm_Rx);
    if (R_SUCCEEDED(rc) && ro_end > seg[1].dst)
        rc = svcSetProcessMemoryPermission(g_own_process, base + seg[1].dst, ro_end - seg[1].dst, Perm_R);
    if (R_SUCCEEDED(rc))
        rc = svcSetProcessMemoryPermission(g_own_process, base + seg[2].dst, img_size - seg[2].dst, Perm_Rw);
    if (R_FAILED(rc)) FAIL("svcSetProcessMemoryPermission: %#x\n", rc);
    *out_base = base;
    *out_size = img_size;
    return true;
fail:
    free(file);
    return false;
}

/* The windows S10 claimed (original image + text, data window) are state Stack, which the kernel cannot reprotect (host_vm.inc K1):
 * each claimed one is released and mapped again by host_win_adopt as AliasCodeData, so VM_PROTECT works on the loader's image
 * sections. A range that was not claimed (the text range is optional; a host built without S10) is left to map_fixed's own path.
 * Needs the own-process handle (load_guest made it); single-threaded, before the guest runs. */
static bool adopt_windows(void) {
    static const struct { const char *name; u64 lo, hi; } w[] = {
        { "image+text", FNVNX_RESV_IMAGE_LO, FNVNX_RESV_WINDOW_LO },   /* the names fnvnx_reserve_early gives (S10) */
        { "data-window", FNVNX_RESV_WINDOW_LO, FNVNX_RESV_WINDOW_HI },
    };
    if (!fnvnx_reserve_release) return true;
    for (size_t i = 0; i < sizeof w / sizeof w[0]; i++) {
        u64 backing = 0;
        Result rc = fnvnx_reserve_release(w[i].name, &backing);
        if (rc == FNVNX_RESV_RC_NO_HEAP || rc == MAKERESULT(Module_Libnx, LibnxError_NotFound)) continue;   /* not claimed */
        if (R_SUCCEEDED(rc)) rc = host_win_adopt(w[i].lo, w[i].hi, backing);
        if (R_FAILED(rc)) {
            logf_("fnvnx host: window %s: %#x\n", w[i].name, rc);
            return false;
        }
    }
    return true;
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    consoleInit(NULL);
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    padInitializeDefault(&g_pad);
    mkdir("sdmc:/switch", 0777);
    mkdir(FNVNX_LOG_DIR, 0777);
    g_log = fopen(FNVNX_LOG_PATH, "w");
    crash_handler_arm(0, 0);
    if (device_preflight() != 0) {
        remove_launcher_override();
        svcSleepThread(8000000000ll);
        if (g_log) fclose(g_log);
        consoleExit(NULL);
        return 1;
    }

    if (fnvnx_reserve_late && fnvnx_reserve_late(log_line) != 0) {   /* relaunches by itself on a collision */
        logf_("fnvnx host: STOP: the fixed address windows are unusable (see above)\n");
        svcSleepThread(5000000000ll);
        if (g_log) fclose(g_log);
        consoleExit(NULL);
        return 1;
    }
    if (!fnvnx_reserve_late) logf_("fnvnx host: built without startup/host/reserve*.c: windows NOT reserved\n");
    remove_launcher_override();   /* past S10's only relaunch point: the console is back as it was */
    mount_shader_pack();
    report_layout();
    load_config();
    {   /* the socket service (libnx bsd:u); released in cleanup_for_exit */
        unsigned src = host_sock_init();
        if (src) {
            logf_("fnvnx host: STOP: socket service initialisation failed: %#x\n", src);
            svcSleepThread(5000000000ll);
            if (g_log) fclose(g_log);
            consoleExit(NULL);
            return 1;
        }
        g_sock_up = 1;
    }
    fnvnx_host_register_ext(FNVNX_EXT_PLATFORM, platform_ext);

    u64 gbase = 0, gsize = 0, rc;
    if (!load_guest(fnvnx_guest_path, &gbase, &gsize) || !adopt_windows()) {
        rc = 0xF00000FFu;
    } else {
        logf_("fnvnx host: guest %s mapped at %#010lx, %#lx bytes\n", fnvnx_guest_path, gbase, gsize);
        crash_handler_arm(gbase, gsize);
        pump_start();
        g_api = (FnvnxHostApi){
            .magic = FNVNX_HOSTAPI_MAGIC, .version = FNVNX_HOSTAPI_VERSION, .size = sizeof(FnvnxHostApi),
            .guest_base = gbase, .guest_size = gsize,
            .heap_base = g_guest_heap_base, .heap_size = g_guest_heap_size,
            .output_debug_string = (u64)h_output_debug_string, .sleep_ns = (u64)h_sleep_ns,
            .exit = (u64)h_exit, .console_write = (u64)h_console_write,
            .system_tick = (u64)h_system_tick, .system_tick_freq = (u64)h_system_tick_freq,
            FNVNX_HOSTAPI_V2_SLOTS FNVNX_HOSTAPI_V3_SLOTS
        };
        u64 (*entry)(u64) = (u64 (*)(u64))gbase;
        rc = entry((u64)&g_api);
        logf_("fnvnx host: guest returned %#lx\n", rc);
    }

    pump_stop();
    set_cpu_boost(0);   /* never leave the console boosted */
    logf_("fnvnx host: press + to exit (auto exit in %u s)\n", fnvnx_host_linger_s);
    u64 deadline = armGetSystemTick() + (u64)fnvnx_host_linger_s * armGetSystemTickFreq();
    while (appletMainLoop() && armGetSystemTick() < deadline) {
        padUpdate(&g_pad);
        if (padGetButtonsDown(&g_pad) & HidNpadButton_Plus) break;
        consoleUpdate(NULL);
        svcSleepThread(16000000);
    }
    cleanup_for_exit("the game returned");
    if (g_log) fclose(g_log);
    consoleExit(NULL);
    return (int)(rc & 0xFF);
}
