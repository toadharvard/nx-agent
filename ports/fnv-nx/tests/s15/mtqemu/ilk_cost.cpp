/* ilk_cost.cpp - benchmark B9 (docs/PERF.md 9): the cost of one Interlocked operation, uninstrumented. Prints ns per
 * operation for
 *   plain    a volatile ++ (the baseline: load, add, store)
 *   llsc     a SEQ_CST ldaxr/stlxr loop without the trailing barrier
 *   inline   InterlockedIncrement as game units get it (fnvnx_win32.h: llsc + dmb ish)
 *   call     the out-of-line sync.cpp function (bl + the same body)
 *   inline+st  inline after a store to another cache line (a store in flight: what the dmb waits for)
 *   shared   inline, 2 threads on one counter (cache line ping-pong)
 * Under qemu on x86 the numbers are the host's (a dmb becomes an mfence); on an A57 (the Switch under Linux, or a
 * Jetson TX1/Nano: the same Tegra X1) linhost and guest.elf run natively and give the device numbers.
 * C++: like every unit but sync.cpp it gets S12's inline bodies (in C, gnu_inline `inline` would be an external
 * definition next to sync.cpp's). */
#include <stdint.h>
#include <stddef.h>

#include "fnvnx_win32_ext.h"
#if defined(FNVNX_W32_IMPL) || !defined(FNVNX_ILK)
#error "the Interlocked functions are not inlined here"
#endif

extern "C" {
int snprintf(char *b, size_t n, const char *f, ...);
uint64_t mtq_ticks_ns(void);
void mtq_result(const char *name, int ok, const char *detail);
LONG __real_InterlockedIncrement(volatile LONG *);
void ilk_cost(void);
}

enum { N = 200000 };
static volatile LONG g_c __attribute__((aligned(64)));
static volatile LONG g_other[16] __attribute__((aligned(64)));
static volatile LONG g_shared __attribute__((aligned(64)));
static volatile int g_go, g_done;

static unsigned per_op_x10(uint64_t ns) { return (unsigned)(ns * 10 / N); }

static DWORD shared_thread(LPVOID p)
{
    (void)p;
    while (!g_go) {}
    for (int i = 0; i < N; i++) InterlockedIncrement(&g_shared);
    __atomic_add_fetch(&g_done, 1, __ATOMIC_SEQ_CST);
    return 0;
}

void ilk_cost(void)
{
    uint64_t t[7];
    t[0] = mtq_ticks_ns();
    for (int i = 0; i < N; i++) g_c = g_c + 1;
    t[1] = mtq_ticks_ns();
    for (int i = 0; i < N; i++) __atomic_add_fetch(&g_c, 1, __ATOMIC_SEQ_CST);
    t[2] = mtq_ticks_ns();
    for (int i = 0; i < N; i++) InterlockedIncrement(&g_c);
    t[3] = mtq_ticks_ns();
    for (int i = 0; i < N; i++) __real_InterlockedIncrement(&g_c);
    t[4] = mtq_ticks_ns();
    for (int i = 0; i < N; i++) { g_other[i & 15] = i; InterlockedIncrement(&g_c); }
    t[5] = mtq_ticks_ns();
    g_shared = 0;
    g_done = 0;
    HANDLE h = CreateThread(nullptr, 0, shared_thread, NULL, 0, nullptr);
    g_go = 1;
    for (int i = 0; i < N; i++) InterlockedIncrement(&g_shared);
    while (g_done != 1) {}
    t[6] = mtq_ticks_ns();
    WaitForSingleObject(h, INFINITE);
    CloseHandle(h);
    g_go = 0;
    unsigned v[6];
    for (int k = 0; k < 6; k++) v[k] = per_op_x10(t[k + 1] - t[k]);
    char d[200];
    snprintf(d, sizeof d, "ns per op: plain %u.%u, llsc %u.%u, inline %u.%u, call %u.%u, inline+st %u.%u, shared %u.%u; "
             "count %ld (want %d)", v[0] / 10, v[0] % 10, v[1] / 10, v[1] % 10, v[2] / 10, v[2] % 10, v[3] / 10,
             v[3] % 10, v[4] / 10, v[4] % 10, v[5] / 10, v[5] % 10, (long)g_shared, 2 * N);
    mtq_result("ilk-cost", g_shared == 2 * N && g_c == 5 * N, d);
}
