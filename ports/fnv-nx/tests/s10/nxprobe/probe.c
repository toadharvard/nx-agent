/* tests/s10/nxprobe/probe.c - hardware probe of the address window reservation (stream S10).
 *
 * A host-only exefs override (no guest): claims the windows in __libnx_initheap exactly as the real host
 * will (startup/host/reserve_nx.c), then in main runs the late phase (relaunching on a collision), prints
 * the result and the small-map layout, and appends one line per launch to sdmc:/fnvnx/reserve.log, so a
 * series of launches gives the relaunch rate. Exits after 15 s or on +.
 */
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <switch.h>

#include "fnvnx_reserve.h"

#define HOST_HEAP (64ull << 20)

static u64 g_heap_base, g_heap_size, g_resv_used;
static FILE *g_log;

void __libnx_initheap(void) {
    extern char *fake_heap_start, *fake_heap_end;
    u64 avail = 0, used = 0, region = 0;
    svcGetInfo(&avail, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&used, InfoType_UsedMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&region, InfoType_HeapRegionSize, CUR_PROCESS_HANDLE, 0);
    u64 size = avail > used + 0x200000 ? (avail - used - 0x200000) & ~0x1FFFFFull : 0;
    if (size > region) size = region & ~0x1FFFFFull;
    if (size < HOST_HEAP + (256ull << 20)) size = HOST_HEAP + (256ull << 20);
    void *addr = NULL;
    if (R_FAILED(svcSetHeapSize(&addr, size))) diagAbortWithResult(MAKERESULT(Module_Libnx, LibnxError_HeapAllocFailed));
    fake_heap_start = (char *)addr;
    fake_heap_end = (char *)addr + HOST_HEAP;
    g_heap_base = (u64)addr;
    g_heap_size = size;
    /* first thing after the heap exists: nothing but the kernel and ldr has placed anything yet */
    g_resv_used = fnvnx_reserve_early((u64)addr + HOST_HEAP, (u64)addr + size);
}

static void out(const char *s) {
    fputs(s, stdout);
    if (g_log) fputs(s, g_log);
    svcOutputDebugString(s, strlen(s));
    consoleUpdate(NULL);
}

static void small_map(void) {
    char line[160];
    u64 a = FNVNX_RESV_SMALL_LO;
    while (a < FNVNX_RESV_SMALL_HI) {
        MemoryInfo mi;
        u32 pi;
        if (R_FAILED(svcQueryMemory(&mi, &pi, a)) || !mi.size) break;
        if (mi.type != MemType_Unmapped) {
            snprintf(line, sizeof line, "  %#010lx..%#010lx type %#04x perm %u %s\n", mi.addr, mi.addr + mi.size,
                     mi.type, mi.perm, fnvnx_resv_type_name(mi.type & 0xFF));
            out(line);
        }
        a = mi.addr + mi.size;
    }
}

int main(void) {
    consoleInit(NULL);
    PadState pad;
    padConfigureInput(1, HidNpadStyleSet_NpadStandard);
    padInitializeDefault(&pad);
    mkdir("sdmc:/fnvnx", 0777);
    g_log = fopen("sdmc:/fnvnx/reserve.log", "a");
    char line[256];
    extern char __start__[] __attribute__((visibility("hidden")));
    extern char __end__[] __attribute__((visibility("hidden")));
    snprintf(line, sizeof line, "== fnvnx reserve probe: %s, host image %p..%p, heap %#lx +%lu MiB, %lu MiB backed\n",
             fnvnx_reserve_address_space(), (void *)__start__, (void *)__end__, g_heap_base, g_heap_size >> 20,
             g_resv_used >> 20);
    out(line);
    int rc = fnvnx_reserve_late(out); /* relaunches by itself on a collision of a required window */
    small_map();
    snprintf(line, sizeof line, "fnvnx reserve probe: %s\n", rc == 0 ? "windows usable" : "GAVE UP");
    out(line);
    if (g_log) fflush(g_log);
    u64 deadline = armGetSystemTick() + 15 * armGetSystemTickFreq();
    while (appletMainLoop() && armGetSystemTick() < deadline) {
        padUpdate(&pad);
        if (padGetButtonsDown(&pad) & HidNpadButton_Plus) break;
        consoleUpdate(NULL);
        svcSleepThread(16000000);
    }
    if (g_log) fclose(g_log);
    consoleExit(NULL);
    return 0;
}
