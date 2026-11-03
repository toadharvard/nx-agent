/* fnv-nx: heap statistics, host side (stream S6c-nine). The guest (render/nine/heaplog.c) posts its numbers through
 * NINE_FN_HEAPSTAT; this adds the host heap (newlib mallinfo, fnvnx_host_heap_size) and the process memory
 * (svcGetInfo used/total) and logs one line:
 *   heap #3 "periodic" t=120.0s guest used 612.4 / arena 640.0 / heap 1536.0 MiB | host used 301.2 / arena 330.0 /
 *   heap 512.0 MiB | process 1210.5 / 3264.0 MiB
 * On the host gate (x86-64) the host part is glibc's mallinfo2 and the gate arena; the process part is omitted. */
#include <inttypes.h>
#include <malloc.h>
#include <stdio.h>
#include <string.h>
#include "nine_host.h"

static unsigned g_lines;

#ifdef __SWITCH__
extern size_t fnvnx_host_heap_size;   /* nine_host_call.c (overrides host.c's weak default) */

void nx_host_log(const char* line) {   /* host.c's log handle is private to it: append to the same file */
    svcOutputDebugString(line, strlen(line));
    FILE* f = fopen("sdmc:/fnvnx/host.log", "a");
    if (f) { fputs(line, f); fclose(f); }
}
#else
uint64_t gate_arena_used(void);       /* gate/gate_alloc.c */
void nx_host_log(const char* line) { fputs(line, stderr); }
__attribute__((visibility("default"))) unsigned gate_heaplog_lines(void) { return g_lines; }
#endif

#define MIB(x) ((double)(x) / 1048576.0)

uint64_t nx_heaplog_post(const NxHeapStat* g) {
    char tag[sizeof g->tag + 1];
    memcpy(tag, g->tag, sizeof g->tag);
    tag[sizeof g->tag] = 0;
    char line[320];
    int n = snprintf(line, sizeof line, "heap #%u \"%s\" t=%.1fs guest used %.2f / arena %.2f / heap %.1f MiB",
                     g->seq, tag, (double)g->uptime_ms / 1000.0, MIB(g->guest_used), MIB(g->guest_arena),
                     MIB(g->guest_heap_size));
#ifdef __SWITCH__
    struct mallinfo mi = mallinfo();
    u64 used = 0, total = 0;
    svcGetInfo(&used, InfoType_UsedMemorySize, CUR_PROCESS_HANDLE, 0);
    svcGetInfo(&total, InfoType_TotalMemorySize, CUR_PROCESS_HANDLE, 0);
    snprintf(line + n, sizeof line - n, " | host used %.2f / arena %.2f / heap %.1f MiB | process %.1f / %.1f MiB\n",
             MIB(mi.uordblks), MIB(mi.arena), MIB(fnvnx_host_heap_size), MIB(used), MIB(total));
#else
    struct mallinfo2 mi = mallinfo2();
    snprintf(line + n, sizeof line - n, " | host used %.2f MiB (glibc) + gate arena %.2f MiB\n",
             MIB(mi.uordblks), MIB(gate_arena_used()));
#endif
    nx_host_log(line);
    g_lines++;
    return 0;
}
