/* reserve_nx.c - libnx backend of the address window reservation and the entry points for the host
 * (stream S10, docs/STARTUP.md "Address window reservation"). LP64, links with libnx.
 *
 * Call order in the host (toolchain/host/host.c, S5):
 *   __libnx_initheap:  after svcSetHeapSize, before anything else maps memory:
 *                          used = fnvnx_reserve_early(guest_heap_lo, guest_heap_hi);  guest heap -= used
 *   main, services up: if (fnvnx_reserve_late(log_line) != 0) -> message, exit (it relaunches by itself
 *                      while attempts remain; it returns only when the windows are usable or it gave up)
 *   S3 map_fixed:      fnvnx_reserve_map_fixed(addr, size) (zeroed RW memory inside the data window)
 *   guest loader:      decompress the NSO straight to its link address (RW, inside the "guest" range),
 *                      then fnvnx_reserve_remap_code("guest", own_process, image_size) and set the segment
 *                      permissions with svcSetProcessMemoryPermission as today.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <switch.h>

#include "fnvnx_reserve.h"

/* Knobs, weak so the host build can change them. */
__attribute__((weak)) uint64_t fnvnx_guest_link_base = FNVNX_RESV_GUEST_BASE;
/* Bytes reserved (and backed) at the guest link address: an upper bound of the guest image incl. .bss;
 * 0 = do not reserve the guest range. */
__attribute__((weak)) uint64_t fnvnx_guest_reserve_size = 64ull << 20;
/* Header page + original .text range (0x00400000..0x00fdf000): optional (nothing is mapped there until the
 * trampoline island, S3b); BACKED by default so that later TLS pages cannot land there. 0 = leave it. */
__attribute__((weak)) uint32_t fnvnx_reserve_text_flags = FNVNX_RESV_BACKED;
__attribute__((weak)) uint32_t fnvnx_reserve_max_relaunch = 8;

enum { R_TEXT, R_WINDOW, R_GUEST, R_COUNT };
static FnvnxResvRange g_ranges[R_COUNT];
static FnvnxResvResult g_res[R_COUNT];
static uint32_t g_n;
static FnvnxRelaunchRecord g_prev;

static int nx_query(void *ctx, uint64_t addr, FnvnxResvRegion *out) {
    (void)ctx;
    MemoryInfo mi;
    u32 pi;
    if (R_FAILED(svcQueryMemory(&mi, &pi, addr))) return -1;
    out->addr = mi.addr;
    out->size = mi.size;
    out->type = mi.type & 0xFFu;
    out->perm = mi.perm;
    return 0;
}

static uint32_t nx_map(void *ctx, uint64_t dst, uint64_t src, uint64_t size) {
    (void)ctx;
    return svcMapMemory((void *)dst, (void *)src, size);
}

static uint32_t nx_unmap(void *ctx, uint64_t dst, uint64_t src, uint64_t size) {
    (void)ctx;
    return svcUnmapMemory((void *)dst, (void *)src, size);
}

static uint32_t nx_virtmem(void *ctx, uint64_t addr, uint64_t size, void **handle) {
    (void)ctx;
    virtmemLock();
    VirtmemReservation *rv = virtmemAddReservation((void *)addr, size);
    virtmemUnlock();
    if (!rv) return FNVNX_RESV_RC_NO_VIRTMEM;
    *handle = rv;
    return 0;
}

static const FnvnxResvOps g_ops = { NULL, nx_query, nx_map, nx_unmap, nx_virtmem };

const FnvnxResvOps *fnvnx_reserve_nx_ops(void) { return &g_ops; }

size_t fnvnx_reserve_early(uint64_t heap_lo, uint64_t heap_hi) {
    g_n = 0;
    if (fnvnx_reserve_text_flags)
        g_ranges[g_n++] = (FnvnxResvRange){ FNVNX_RESV_IMAGE_LO, FNVNX_RESV_WINDOW_LO,
                                            fnvnx_reserve_text_flags & ~FNVNX_RESV_REQUIRED, "image+text" };
    g_ranges[g_n++] = (FnvnxResvRange){ FNVNX_RESV_WINDOW_LO, FNVNX_RESV_WINDOW_HI,
                                        FNVNX_RESV_REQUIRED | FNVNX_RESV_BACKED, "data-window" };
    if (fnvnx_guest_reserve_size) {
        uint64_t end = (fnvnx_guest_link_base + fnvnx_guest_reserve_size + FNVNX_RESV_PAGE - 1) & ~(FNVNX_RESV_PAGE - 1);
        g_ranges[g_n++] = (FnvnxResvRange){ fnvnx_guest_link_base, end, FNVNX_RESV_REQUIRED | FNVNX_RESV_BACKED, "guest" };
    }
    uint64_t used = 0;
    fnvnx_resv_claim(&g_ops, g_ranges, g_n, heap_lo, heap_hi, &used, g_res);
    return (size_t)used;
}

static void read_relaunch_record(void) {
    AppletStorage s;
    memset(&g_prev, 0, sizeof g_prev);
    if (R_FAILED(appletPopLaunchParameter(&s, AppletLaunchParameterKind_UserChannel))) return;
    s64 size = 0;
    FnvnxRelaunchRecord rec;
    if (R_SUCCEEDED(appletStorageGetSize(&s, &size)) && size >= (s64)sizeof rec &&
        R_SUCCEEDED(appletStorageRead(&s, 0, &rec, sizeof rec)))
        g_prev = fnvnx_relaunch_decode(&rec, sizeof rec);
    appletStorageClose(&s);
}

/* Ends this process and starts the application again; returns only on failure. */
static Result relaunch(const FnvnxRelaunchRecord *rec) {
    Result rc = appletRestartProgram(rec, sizeof *rec); /* [5.0.0+]: AM ends us and launches us again */
    if (R_SUCCEEDED(rc)) {
        for (int i = 0; i < 500; i++) svcSleepThread(20000000ll); /* AM terminates the process meanwhile */
        svcExitProcess();
    }
    AppletStorage s; /* fallback: queue a launch of the current application (id 0), then exit normally */
    Result rc2 = appletCreateStorage(&s, sizeof *rec);
    if (R_SUCCEEDED(rc2)) rc2 = appletStorageWrite(&s, 0, rec, sizeof *rec);
    if (R_SUCCEEDED(rc2)) {
        rc2 = appletRequestLaunchApplication(0, &s); /* closes s */
        if (R_SUCCEEDED(rc2)) exit(0);
    } else {
        appletStorageClose(&s);
    }
    return R_FAILED(rc) ? rc : rc2;
}

static void log_report(void (*log)(const char *line)) {
    if (!log) return;
    char buf[1024];
    fnvnx_resv_format(g_ranges, g_n, g_res, buf, sizeof buf);
    log(buf);
}

int fnvnx_reserve_late(void (*log)(const char *line)) {
    char line[256];
    fnvnx_resv_late(&g_ops, g_ranges, g_n, g_res);
    read_relaunch_record();
    if (log && g_prev.attempts) {
        snprintf(line, sizeof line, "fnvnx reserve: relaunch %u (last conflict %#010llx type %u in range %#010llx)\n",
                 g_prev.attempts, (unsigned long long)g_prev.last_addr, g_prev.last_type,
                 (unsigned long long)g_prev.last_range);
        log(line);
    }
    log_report(log);
    uint32_t collisions = 0;
    if (!fnvnx_resv_unusable(g_ranges, g_n, g_res, &collisions)) return 0;
    FnvnxRelaunchRecord next;
    if (fnvnx_relaunch_decide(&g_prev, fnvnx_reserve_max_relaunch, g_ranges, g_n, g_res, &next)) {
        if (log) {
            snprintf(line, sizeof line, "fnvnx reserve: required window occupied, relaunching (attempt %u of %u)\n",
                     next.attempts, fnvnx_reserve_max_relaunch);
            log(line);
        }
        Result rc = relaunch(&next);
        if (log) {
            snprintf(line, sizeof line, "fnvnx reserve: relaunch failed (%#x); close and start the game again\n", rc);
            log(line);
        }
        return -1;
    }
    if (log)
        log(collisions ? "fnvnx reserve: required window still occupied after the last relaunch; giving up\n"
                       : "fnvnx reserve: could not back a required window (see rc above); giving up\n");
    return -1;
}

int fnvnx_reserve_contains(uint64_t addr, uint64_t size) {
    return fnvnx_resv_contains(g_ranges, g_n, g_res, addr, size);
}

int fnvnx_reserve_map_fixed(uint64_t addr, uint64_t size) {
    if (!fnvnx_resv_contains(g_ranges, g_n, g_res, addr, size)) return -1;
    memset((void *)addr, 0, size);
    return 0;
}

static int find(const char *name) {
    for (uint32_t i = 0; i < g_n; i++)
        if (g_ranges[i].name && !strcmp(g_ranges[i].name, name)) return (int)i;
    return -1;
}

uint32_t fnvnx_reserve_remap_code(const char *name, Handle process, uint64_t size) {
    int i = find(name);
    if (i < 0 || g_res[i].state != FNVNX_RESV_CLAIMED) return MAKERESULT(Module_Libnx, LibnxError_NotFound);
    uint64_t start = g_ranges[i].start, total = g_ranges[i].end - start, src = g_res[i].backing;
    size = (size + FNVNX_RESV_PAGE - 1) & ~(FNVNX_RESV_PAGE - 1);
    if (!size || size > total) return MAKERESULT(Module_Libnx, LibnxError_BadInput);
    armDCacheFlush((void *)start, size);
    Result rc = svcUnmapMemory((void *)start, (void *)src, total);
    if (R_FAILED(rc)) return rc;
    /* Single thread here: nothing can take the range between the unmap and the code mapping. */
    rc = svcMapProcessCodeMemory(process, start, src, size);
    if (R_FAILED(rc)) {
        svcMapMemory((void *)start, (void *)src, total); /* put the reservation back */
        return rc;
    }
    if (size < total) svcMapMemory((void *)(start + size), (void *)(src + size), total - size); /* keep the tail */
    g_res[i].state = FNVNX_RESV_CODE;
    return 0;
}

uint32_t fnvnx_reserve_release(const char *name, uint64_t *backing) {
    int i = find(name);
    if (i < 0) return MAKERESULT(Module_Libnx, LibnxError_NotFound);
    return fnvnx_resv_release(&g_ops, &g_ranges[i], &g_res[i], backing);
}

size_t fnvnx_reserve_format(char *buf, size_t cap) { return fnvnx_resv_format(g_ranges, g_n, g_res, buf, cap); }

const char *fnvnx_reserve_address_space(void) {
    u64 stack = 0, stack_size = 0, alias_size = 0;
    svcGetInfo(&stack, InfoType_StackRegionAddress, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&stack_size, InfoType_StackRegionSize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&alias_size, InfoType_AliasRegionSize, CUR_PROCESS_HANDLE, 0);
    return fnvnx_resv_address_space_name(stack, stack + stack_size, alias_size);
}
