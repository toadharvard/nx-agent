/* hb.c - a small happens-before race checker for ILP32 guest code (no TSan runtime exists for ILP32).
 *
 * Code under test is compiled with GCC's -fsanitize=thread, which only inserts calls (__tsan_read4, __tsan_write4,
 * __tsan_atomic32_compare_exchange_strong, ...); this file is the runtime behind them. Model: vector clocks per thread
 * (FastTrack-style last write + per-thread last reads per 4-byte granule). Edges come from
 *   - C11 atomics in the code under test, by their memory order (an atomic release store / RMW publishes the thread's
 *     clock on that address, an acquire load / RMW joins it): a spin lock released with a plain store gives no edge,
 *     which is exactly the AArch64 hazard of the engine's RecursiveSpinLock (docs/PERF.md 6.2);
 *   - the Win32 API the code under test calls, wrapped at link time (-Wl,--wrap=EnterCriticalSection, ...), like
 *     TSan's interceptors: critical sections, events, semaphores and Interlocked* (full barriers, as on x86) give
 *     edges, CreateThread forks a clock, a wait on a thread handle joins it.
 * Fences (C11 rules, so S12's weakened refcounts can be checked): a relaxed atomic read takes the location's
 * release clock as pending, and an acquire fence joins what is pending (fence-acquire after reading a release
 * sequence); a release fence snapshots the thread's clock, and a later relaxed store / RMW publishes that snapshot.
 * The platform layer itself is not instrumented. Races are counted and the first ones printed with the code
 * address of the access (map it with nm). Not instrumented itself. docs/PERF.md section 6.6. */
#include <stdint.h>
#include <stddef.h>

/* C: the plain Interlocked* prototypes (fnvnx_win32.h's inline bodies are C++ gnu_inline; in C they would be
 * external definitions next to sync.cpp's) */
#define FNVNX_W32_IMPL 1
#include "fnvnx_win32_ext.h"

int printf(const char *f, ...);

#define MAXT 64   /* thread slots are never reused: every case of mt_test together creates ~40 */
#define NSYNC 4096
#define NSHADOW (1u << 17)

typedef struct { uint32_t c[MAXT]; } VC;
typedef struct { uint32_t key; uint8_t wt, used; uint32_t wc; uint32_t r[MAXT]; } Shadow;
typedef struct { uint32_t key; int used; VC vc; } Sync;

static VC g_vc[MAXT];
static VC g_pend[MAXT];               /* releases read by relaxed atomics since the last acquire fence */
static VC g_relf[MAXT];               /* the clock at the last release fence */
static int g_nthreads = 1;            /* slot 0: the main thread */
static Sync g_sync[NSYNC];
static Shadow g_shadow[NSHADOW];
static volatile uint32_t g_lock;
static __thread int t_me1;            /* thread slot + 1; 0: not registered yet (the main thread) */

static unsigned g_races, g_checks;
typedef struct { uint32_t addr, pc; uint8_t kind, a, b; } Report;
static Report g_rep[32];

static void lk(void) { while (__atomic_exchange_n(&g_lock, 1, __ATOMIC_ACQUIRE)) __asm__ volatile("yield"); }
static void ulk(void) { __atomic_store_n(&g_lock, 0, __ATOMIC_RELEASE); }

static int me(void) {
    if (!t_me1) { t_me1 = 1; g_vc[0].c[0] = g_vc[0].c[0] ? g_vc[0].c[0] : 1; }
    return t_me1 - 1;
}
static void join(VC *d, const VC *s) { for (int i = 0; i < MAXT; i++) if (s->c[i] > d->c[i]) d->c[i] = s->c[i]; }

static Sync *sync_of(uint32_t key) {
    uint32_t h = (key * 2654435761u) % NSYNC;
    for (unsigned n = 0; n < NSYNC; n++, h = (h + 1) % NSYNC)
        if (!g_sync[h].used || g_sync[h].key == key) {
            if (!g_sync[h].used) { g_sync[h].used = 1; g_sync[h].key = key; }
            return &g_sync[h];
        }
    return &g_sync[0];
}
/* caller holds the lock */
static void acq_l(int t, uint32_t key) { join(&g_vc[t], &sync_of(key)->vc); }
static void rel_l(int t, uint32_t key) { join(&sync_of(key)->vc, &g_vc[t]); g_vc[t].c[t]++; }
void hb_acquire(const volatile void *p) { lk(); acq_l(me(), (uint32_t)(uintptr_t)p); ulk(); }
void hb_release(const volatile void *p) { lk(); rel_l(me(), (uint32_t)(uintptr_t)p); ulk(); }
static void hb_acq_rel(const volatile void *p) { lk(); int t = me(); acq_l(t, (uint32_t)(uintptr_t)p); rel_l(t, (uint32_t)(uintptr_t)p); ulk(); }

static void report(int kind, uint32_t addr, int a, int b, uint32_t pc) {
    if (g_races < sizeof g_rep / sizeof g_rep[0])
        g_rep[g_races] = (Report){addr, pc, (uint8_t)kind, (uint8_t)a, (uint8_t)b};
    g_races++;
}

static void access(uint32_t addr, int write, uint32_t pc) {
    lk();
    int t = me();
    uint32_t key = addr >> 2, h = (key * 2654435761u) & (NSHADOW - 1);
    Shadow *s = 0;
    for (unsigned n = 0; n < 64; n++, h = (h + 1) & (NSHADOW - 1))
        if (!g_shadow[h].used || g_shadow[h].key == key) { s = &g_shadow[h]; break; }
    if (!s) { ulk(); return; }   /* table full around here: skip (tests stay far below) */
    if (!s->used) { s->used = 1; s->key = key; s->wt = 0; s->wc = 0; for (int i = 0; i < MAXT; i++) s->r[i] = 0; }
    g_checks++;
    const VC *v = &g_vc[t];
    if (s->wc && s->wt != t && s->wc > v->c[s->wt])
        report(write ? 0 : 1, addr, s->wt, t, pc);        /* write-write / write-read */
    if (write) {
        for (int u = 0; u < MAXT; u++)
            if (u != t && s->r[u] > v->c[u]) { report(2, addr, u, t, pc); break; }   /* read-write */
        s->wt = (uint8_t)t; s->wc = v->c[t];
        for (int u = 0; u < MAXT; u++) s->r[u] = 0;
    } else {
        s->r[t] = v->c[t];
    }
    ulk();
}

/* ---- the TSan ABI GCC emits */
#define PC ((uint32_t)(uintptr_t)__builtin_return_address(0))
void __tsan_init(void) {}
void __tsan_func_entry(void *p) { (void)p; }
void __tsan_func_exit(void) {}
void __tsan_vptr_update(void **p, void *v) { (void)p; (void)v; }
void __tsan_vptr_read(void **p) { (void)p; }
#define RW(n) \
    void __tsan_read##n(void *p) { access((uint32_t)(uintptr_t)p, 0, PC); if (n > 4) access((uint32_t)(uintptr_t)p + 4, 0, PC); } \
    void __tsan_write##n(void *p) { access((uint32_t)(uintptr_t)p, 1, PC); if (n > 4) access((uint32_t)(uintptr_t)p + 4, 1, PC); } \
    void __tsan_unaligned_read##n(void *p) { __tsan_read##n(p); } \
    void __tsan_unaligned_write##n(void *p) { __tsan_write##n(p); }
RW(1) RW(2) RW(4) RW(8) RW(16)
void __tsan_read_range(void *p, size_t n) { for (size_t i = 0; i < n; i += 4) access((uint32_t)(uintptr_t)p + (uint32_t)i, 0, PC); }
void __tsan_write_range(void *p, size_t n) { for (size_t i = 0; i < n; i += 4) access((uint32_t)(uintptr_t)p + (uint32_t)i, 1, PC); }

/* memory orders as GCC passes them: 0 relaxed, 1 consume, 2 acquire, 3 release, 4 acq_rel, 5 seq_cst */
static int is_acq(int mo) { mo &= 0xFFFF; return mo == 1 || mo == 2 || mo >= 4; }
static int is_rel(int mo) { mo &= 0xFFFF; return mo >= 3; }
static void pre(const volatile void *p, int mo) {
    if (is_rel(mo)) { hb_release(p); return; }
    lk(); int t = me(); join(&sync_of((uint32_t)(uintptr_t)p)->vc, &g_relf[t]); ulk();   /* after a release fence */
}
static void post(const volatile void *p, int mo) {
    if (is_acq(mo)) { hb_acquire(p); return; }
    lk(); int t = me(); join(&g_pend[t], &sync_of((uint32_t)(uintptr_t)p)->vc); ulk();  /* for an acquire fence */
}

#define ATOMICS(n, T) \
    T __tsan_atomic##n##_load(const volatile T *a, int mo) { T v = __atomic_load_n(a, __ATOMIC_SEQ_CST); post(a, mo); return v; } \
    void __tsan_atomic##n##_store(volatile T *a, T v, int mo) { pre(a, mo); __atomic_store_n(a, v, __ATOMIC_SEQ_CST); } \
    T __tsan_atomic##n##_exchange(volatile T *a, T v, int mo) { pre(a, mo); T r = __atomic_exchange_n(a, v, __ATOMIC_SEQ_CST); post(a, mo); return r; } \
    T __tsan_atomic##n##_fetch_add(volatile T *a, T v, int mo) { pre(a, mo); T r = __atomic_fetch_add(a, v, __ATOMIC_SEQ_CST); post(a, mo); return r; } \
    T __tsan_atomic##n##_fetch_sub(volatile T *a, T v, int mo) { pre(a, mo); T r = __atomic_fetch_sub(a, v, __ATOMIC_SEQ_CST); post(a, mo); return r; } \
    T __tsan_atomic##n##_fetch_and(volatile T *a, T v, int mo) { pre(a, mo); T r = __atomic_fetch_and(a, v, __ATOMIC_SEQ_CST); post(a, mo); return r; } \
    T __tsan_atomic##n##_fetch_or(volatile T *a, T v, int mo) { pre(a, mo); T r = __atomic_fetch_or(a, v, __ATOMIC_SEQ_CST); post(a, mo); return r; } \
    T __tsan_atomic##n##_fetch_xor(volatile T *a, T v, int mo) { pre(a, mo); T r = __atomic_fetch_xor(a, v, __ATOMIC_SEQ_CST); post(a, mo); return r; } \
    int __tsan_atomic##n##_compare_exchange_strong(volatile T *a, T *c, T v, int mo, int fmo) { \
        (void)fmo; pre(a, mo); int ok = __atomic_compare_exchange_n(a, c, v, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST); post(a, mo); return ok; } \
    int __tsan_atomic##n##_compare_exchange_weak(volatile T *a, T *c, T v, int mo, int fmo) { \
        return __tsan_atomic##n##_compare_exchange_strong(a, c, v, mo, fmo); } \
    T __tsan_atomic##n##_compare_exchange_val(volatile T *a, T c, T v, int mo, int fmo) { \
        __tsan_atomic##n##_compare_exchange_strong(a, &c, v, mo, fmo); return c; }
ATOMICS(8, uint8_t) ATOMICS(16, uint16_t) ATOMICS(32, uint32_t) ATOMICS(64, uint64_t)
void __tsan_atomic_thread_fence(int mo) {
    lk();
    int t = me();
    if (is_acq(mo)) join(&g_vc[t], &g_pend[t]);
    if (is_rel(mo)) { g_relf[t] = g_vc[t]; g_vc[t].c[t]++; }
    ulk();
}
void __tsan_atomic_signal_fence(int mo) { (void)mo; }

/* ---- Win32 interceptors (the test links with -Wl,--wrap=<name> for each) */
void __real_EnterCriticalSection(LPCRITICAL_SECTION);
void __real_LeaveCriticalSection(LPCRITICAL_SECTION);
BOOL __real_TryEnterCriticalSection(LPCRITICAL_SECTION);
BOOL __real_SetEvent(HANDLE);
BOOL __real_ReleaseSemaphore(HANDLE, LONG, LPLONG);
DWORD __real_WaitForSingleObject(HANDLE, DWORD);
DWORD __real_WaitForMultipleObjects(DWORD, const HANDLE *, BOOL, DWORD);
LONG __real_InterlockedIncrement(volatile LONG *);
LONG __real_InterlockedDecrement(volatile LONG *);
LONG __real_InterlockedExchange(volatile LONG *, LONG);
LONG __real_InterlockedExchangeAdd(volatile LONG *, LONG);
LONG __real_InterlockedCompareExchange(volatile LONG *, LONG, LONG);
HANDLE __real_CreateThread(void *, SIZE_T, LPTHREAD_START_ROUTINE, LPVOID, DWORD, LPDWORD);

void __wrap_EnterCriticalSection(LPCRITICAL_SECTION cs) { __real_EnterCriticalSection(cs); hb_acquire(cs); }
void __wrap_LeaveCriticalSection(LPCRITICAL_SECTION cs) { hb_release(cs); __real_LeaveCriticalSection(cs); }
BOOL __wrap_TryEnterCriticalSection(LPCRITICAL_SECTION cs) {
    BOOL r = __real_TryEnterCriticalSection(cs);
    if (r) hb_acquire(cs);
    return r;
}
BOOL __wrap_SetEvent(HANDLE h) { hb_release(h); return __real_SetEvent(h); }
BOOL __wrap_ReleaseSemaphore(HANDLE h, LONG n, LPLONG prev) { hb_release(h); return __real_ReleaseSemaphore(h, n, prev); }

typedef struct { LPTHREAD_START_ROUTINE fn; LPVOID arg; int slot; volatile int done; VC start, final; HANDLE h; } HbThread;
static HbThread g_threads[MAXT];

static void joined(HANDLE h) {
    lk();
    for (int i = 1; i < g_nthreads; i++)
        if (g_threads[i].h == h && g_threads[i].done) join(&g_vc[me()], &g_threads[i].final);
    ulk();
}
DWORD __wrap_WaitForSingleObject(HANDLE h, DWORD ms) {
    DWORD r = __real_WaitForSingleObject(h, ms);
    if (r == WAIT_OBJECT_0) { hb_acquire(h); joined(h); }
    return r;
}
DWORD __wrap_WaitForMultipleObjects(DWORD n, const HANDLE *hs, BOOL all, DWORD ms) {
    DWORD r = __real_WaitForMultipleObjects(n, hs, all, ms);
    if (r < WAIT_OBJECT_0 + n)
        for (DWORD i = 0; i < n; i++)
            if (all || i == r - WAIT_OBJECT_0) { hb_acquire(hs[i]); joined(hs[i]); }
    return r;
}
#define ILK(name, sig, call) LONG __wrap_##name sig { hb_acq_rel(p); LONG r = call; hb_acq_rel(p); return r; }
ILK(InterlockedIncrement, (volatile LONG *p), __real_InterlockedIncrement(p))
ILK(InterlockedDecrement, (volatile LONG *p), __real_InterlockedDecrement(p))
ILK(InterlockedExchange, (volatile LONG *p, LONG v), __real_InterlockedExchange(p, v))
ILK(InterlockedExchangeAdd, (volatile LONG *p, LONG v), __real_InterlockedExchangeAdd(p, v))
ILK(InterlockedCompareExchange, (volatile LONG *p, LONG x, LONG c), __real_InterlockedCompareExchange(p, x, c))
/* the inline Interlocked* of fnvnx_win32.h hand a misaligned operand to sync.cpp's split lock */
LONG __real_fnvnx_interlocked_slow(int op, volatile LONG *p, LONG a, LONG b);
ILK(fnvnx_interlocked_slow, (int op, volatile LONG *p, LONG a, LONG b), __real_fnvnx_interlocked_slow(op, p, a, b))

static DWORD hb_trampoline(LPVOID p) {
    HbThread *t = p;
    t_me1 = t->slot + 1;
    lk();
    join(&g_vc[t->slot], &t->start);
    g_vc[t->slot].c[t->slot]++;
    ulk();
    DWORD r = t->fn(t->arg);
    lk();
    t->final = g_vc[t->slot];
    t->done = 1;
    ulk();
    return r;
}
HANDLE __wrap_CreateThread(void *sa, SIZE_T stack, LPTHREAD_START_ROUTINE fn, LPVOID arg, DWORD flags, LPDWORD tid) {
    lk();
    int parent = me();
    if (g_nthreads >= MAXT) { ulk(); return NULL; }
    HbThread *t = &g_threads[g_nthreads];
    t->slot = g_nthreads++;
    t->fn = fn; t->arg = arg; t->done = 0;
    t->start = g_vc[parent];
    g_vc[parent].c[parent]++;
    ulk();
    HANDLE h = __real_CreateThread(sa, stack, hb_trampoline, t, flags, tid);
    t->h = h;
    return h;
}

/* ---- results */
void hb_reset(void) {
    lk();
    for (unsigned i = 0; i < NSHADOW; i++) g_shadow[i].used = 0;
    g_races = 0;
    g_checks = 0;
    ulk();
}
unsigned hb_races(void) { return g_races; }
unsigned hb_checks(void) { return g_checks; }
void hb_print(const char *what) {
    static const char *kinds[] = {"write-write", "write-read", "read-write"};
    unsigned n = g_races < 4 ? g_races : 4;
    for (unsigned i = 0; i < n; i++)
        printf("hb: %s: race %s on 0x%08x between threads %u and %u at pc 0x%08x\n", what, kinds[g_rep[i].kind],
               g_rep[i].addr, g_rep[i].a, g_rep[i].b, g_rep[i].pc);
}
