/* fnv-nx: periodic heap statistics, guest side (stream S6c-nine; ILP32, C).
 *
 * fnvnx_heaplog_report(tag) posts the guest heap's numbers (newlib mallinfo, the heap block size) to the host
 * module through ext_call(NINE_EXT_MODULE, NINE_FN_HEAPSTAT); the host adds its own heap and the process memory
 * (host/nine_heaplog.c) and appends one line to sdmc:/fnvnx/host.log. fnvnx_heaplog_start(period_s) starts a
 * thread that reports every period_s seconds. The game enables it with the build switch HEAPLOG_S=<seconds>
 * (render/nine/game/build.sh -> -DFNVNX_HEAPLOG_S, started by nine_register.cpp); off by default. */
#include <malloc.h>
#include <pthread.h>
#include <string.h>
#include <time.h>
#include "fnvnx/hostapi.h"
#include "nine_ext.h"

static uint32_t g_seq;
static uint64_t g_t0;

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

void fnvnx_heaplog_report(const char* tag) {
    if (!fnvnx_host || fnvnx_host->version < 3 || !fnvnx_host->ext_call) return;
    struct mallinfo mi = mallinfo();
    NxHeapStat st;
    memset(&st, 0, sizeof st);
    strncpy(st.tag, tag ? tag : "", sizeof st.tag - 1);
    uint64_t t = now_ms();
    if (!g_t0) g_t0 = t;
    st.seq = __atomic_add_fetch(&g_seq, 1, __ATOMIC_RELAXED);
    st.uptime_ms = t - g_t0;
    st.guest_heap_size = fnvnx_host->heap_size;
    st.guest_arena = mi.arena;
    st.guest_used = mi.uordblks;
    st.guest_free = mi.fordblks;
    uint64_t a[1] = {FNVNX_ADDR(&st)};
    uint64_t blk[2] = {0, FNVNX_ADDR(a)};
    fnvnx_hcall4(fnvnx_host->ext_call, NINE_EXT_MODULE, NINE_FN_HEAPSTAT, FNVNX_ADDR(blk), 2);
}

static void* heaplog_thread(void* arg) {
    unsigned period = (unsigned)(uintptr_t)arg;
    for (;;) {
        struct timespec ts = {(time_t)period, 0};
        nanosleep(&ts, NULL);
        fnvnx_heaplog_report("periodic");
    }
    return NULL;
}

int fnvnx_heaplog_start(unsigned period_s) {
    static int started;
    if (!period_s || __atomic_exchange_n(&started, 1, __ATOMIC_ACQ_REL)) return 0;
    fnvnx_heaplog_report("start");
    pthread_t t;
    if (pthread_create(&t, NULL, heaplog_thread, (void*)(uintptr_t)period_s)) return -1;
    pthread_detach(t);
    return 0;
}
