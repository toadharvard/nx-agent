/* mt_test.cpp - the mtqemu guest test (ILP32, -fsanitize=thread with hb.c as the runtime).
 *
 * Runs on the real platform layer (platform/win32 sync.cpp, thread.cpp, handles.cpp; platform/os/os_guest.c;
 * toolchain/guest/threads.c) over linhost's FnvnxHostApi, so every Win32 wait is a futex and every thread a host
 * thread. Each case prints "mtq: <case> ok|FAIL ..." (test_mtqemu.py checks them):
 *   parallel        main + 2 game threads (placement rows main / game: cores 0, 1-2) run the same work: speedup
 *                   (best of up to 5 tries; a shared host can still fail it, test_mtqemu.py reruns)
 *   cs              CRITICAL_SECTION counter under contention: exact, no race
 *   stages          the Rendering Task Thread 2 protocol (0x00ba3390 records, 0x00ba30f0 / 0x00ba3130 stage
 *                   semaphores, 23 stages a frame): results right, no race
 *   spin-decomp     the engine's RecursiveSpinLock (0x0040fbf0 / 0x0040fba0 as built: plain store release) guarding
 *                   a pool free list (the NiTList pool lock 0x011c5f80 pattern): the checker must report races
 *   spin-override   the same with tools/link/overrides/spinlock_0040fba0.c: no race
 *   unsync          a plain shared counter (checker sanity): races
 *   detach          the engine's CreateThread + CloseHandle at once: 3 batches of 40 running threads whose handles are
 *                   closed while they run, so each drops its object's last reference itself (a self thread_free);
 *                   the host must free those records at their end (J-0129: linhost self-joined, host_v2.inc freed
 *                   the libnx Thread under the running thread), and 120 threads need the 64 slots reused.
 *                   Not checked by hb (calls the __real_ Win32 functions). */
#include "fnvnx_win32_ext.h"
#include "fnvnx/hostapi.h"

extern "C" {
int printf(const char *f, ...);
int snprintf(char *b, size_t n, const char *f, ...);
void hb_reset(void);
unsigned hb_races(void);
unsigned hb_checks(void);
void hb_print(const char *what);
uint64_t mtq_ticks_ns(void);
uint64_t mtq_spin(uint64_t n);
struct fnvnx_recursive_spin_lock;
void fnvnx_spin_unlock_0040fba0(struct fnvnx_recursive_spin_lock *l);
}

static int g_fail;
static void result(const char *name, bool ok, const char *detail)
{
    printf("mtq: %s %s %s\n", name, ok ? "ok" : "FAIL", detail);
    if (!ok) g_fail++;
}

/* ---- parallel */
static const uint64_t WORK = 40000000;
static volatile uint64_t g_sink;
static DWORD spin_thread(LPVOID) { g_sink = mtq_spin(WORK); return 0; }

/* one try: 3 units of work on the main thread alone, then 1 unit on each of 3 threads; speedup x100 */
static unsigned parallel_try(uint64_t *serial, uint64_t *par)
{
    uint64_t t0 = mtq_ticks_ns();
    g_sink = mtq_spin(3 * WORK);
    uint64_t t1 = mtq_ticks_ns();
    HANDLE h[2];
    for (int i = 0; i < 2; i++) h[i] = CreateThread(NULL, 0, spin_thread, NULL, 0, NULL);
    g_sink = mtq_spin(WORK);
    WaitForMultipleObjects(2, h, TRUE, INFINITE);
    uint64_t t2 = mtq_ticks_ns();
    for (int i = 0; i < 2; i++) CloseHandle(h[i]);
    *serial = t1 - t0;
    *par = t2 - t1;
    return (unsigned)((t1 - t0) * 100 / (t2 - t1 ? t2 - t1 : 1));
}

/* The host may be shared (other processes on the CPUs the placement uses), so the best of up to 5 tries counts. */
static void t_parallel(void)
{
    unsigned best = 0, tries = 0;
    uint64_t bs = 0, bp = 0;
    while (tries < 5 && best < 200) {
        uint64_t s, p;
        unsigned x = parallel_try(&s, &p);
        tries++;
        if (x > best) { best = x; bs = s; bp = p; }
    }
    char d[128];
    snprintf(d, sizeof d, "serial %u ms, 3 threads %u ms, speedup %u.%02u (best of %u)", (unsigned)(bs / 1000000),
             (unsigned)(bp / 1000000), best / 100, best % 100, tries);
    result("parallel", best >= 150, d);
}

/* ---- cs */
static CRITICAL_SECTION g_cs;
static int g_count;
static DWORD cs_thread(LPVOID)
{
    for (int i = 0; i < 2000; i++) {
        EnterCriticalSection(&g_cs);
        g_count++;
        LeaveCriticalSection(&g_cs);
    }
    return 0;
}
static void t_cs(void)
{
    hb_reset();
    InitializeCriticalSectionAndSpinCount(&g_cs, 4000);
    HANDLE h[4];
    for (int i = 0; i < 4; i++) h[i] = CreateThread(NULL, 0, cs_thread, NULL, 0, NULL);
    WaitForMultipleObjects(4, h, TRUE, INFINITE);
    for (int i = 0; i < 4; i++) CloseHandle(h[i]);
    int c = g_count;   /* read after the join: ordered */
    char d[96];
    snprintf(d, sizeof d, "count %d (want 8000), races %u, %u checks", c, hb_races(), hb_checks());
    if (hb_races()) hb_print("cs");
    result("cs", c == 8000 && hb_races() == 0, d);
}

/* ---- stages (Rendering Task Thread 2: records 1..23, stage semaphore, completion semaphore) */
enum { STAGES = 24, FRAMES = 3 };
struct Rec { int n; int in[8]; int out; };
static Rec g_rec[STAGES];
static HANDLE g_stage, g_done;
static volatile LONG g_stage_count, g_done_count;
static DWORD stage_thread(LPVOID)
{
    for (int f = 0; f < FRAMES; f++)
        for (int i = 1; i < STAGES; i++) {
            WaitForSingleObject(g_stage, INFINITE);   /* 0x00ba3130(0, i): Acquire, then ++completed */
            InterlockedDecrement(&g_stage_count);
            if (g_rec[i].n) {                          /* the culling of record i */
                int s = 0;
                for (int k = 0; k < g_rec[i].n; k++) s += g_rec[i].in[k];
                g_rec[i].out = s;
                g_rec[i].n = 0;
            }
            ReleaseSemaphore(g_done, 1, NULL);         /* 0x00ba3190: ReleaseSemaphore + InterlockedIncrement */
            InterlockedIncrement(&g_done_count);
        }
    return 0;
}
static void t_stages(void)
{
    hb_reset();
    g_stage = CreateSemaphoreA(NULL, 0, 64, NULL);
    g_done = CreateSemaphoreA(NULL, 0, 64, NULL);
    HANDLE w = CreateThread(NULL, 0x20000, stage_thread, NULL, 0, NULL);
    int bad = 0;
    for (int f = 0; f < FRAMES; f++) {
        for (int i = 1; i < STAGES; i++) {
            if (i % 3 == 0) {                          /* 0x00ba3390: fill the record, then signal its stage */
                g_rec[i].n = 8;
                for (int k = 0; k < 8; k++) g_rec[i].in[k] = f * 100 + i + k;
            }
            ReleaseSemaphore(g_stage, 1, NULL);        /* 0x00ba30f0(0, i) */
            InterlockedIncrement(&g_stage_count);
        }
        for (int i = 1; i < STAGES; i++) {
            WaitForSingleObject(g_done, INFINITE);     /* 0x00ba3130(1, i) */
            InterlockedDecrement(&g_done_count);
            if (i % 3 == 0 && g_rec[i].out != 8 * (f * 100 + i) + 28) bad++;
        }
    }
    WaitForSingleObject(w, INFINITE);
    CloseHandle(w);
    char d[96];
    snprintf(d, sizeof d, "%d frames x %d stages, %d wrong, races %u, %u checks", FRAMES, STAGES - 1, bad, hb_races(),
             hb_checks());
    if (hb_races()) hb_print("stages");
    result("stages", bad == 0 && hb_races() == 0, d);
}

/* ---- the engine's RecursiveSpinLock (decomp 0x0040fbf0 Lock, 0x0040fba0 Unlock) over a pool free list */
struct RSL { volatile unsigned owner; unsigned count; unsigned char pad[24]; };
struct Node { Node *next; int payload; };
static RSL g_rsl;
static Node g_nodes[64];
static Node *g_free;
static int g_override;

static void rsl_lock(RSL *l)
{
    unsigned me = (unsigned)GetCurrentThreadId();
    if (l->owner == me) { l->count++; return; }
    unsigned spins = 0;
    while (InterlockedCompareExchange((volatile LONG *)&l->owner, (LONG)me, 0) != 0)
        Sleep(++spins > 10000 ? 1 : 0);
    l->count = 1;
}
static void rsl_unlock(RSL *l)
{
    if (g_override) {
        fnvnx_spin_unlock_0040fba0((struct fnvnx_recursive_spin_lock *)l);
        return;
    }
    if (--l->count == 0)
        l->owner = 0;   /* what our build of 0x0040fba0 does: str wzr, [x19] */
}
static DWORD pool_thread(LPVOID)
{
    for (int i = 0; i < 300; i++) {
        rsl_lock(&g_rsl);
        Node *n = g_free;
        if (n) { g_free = n->next; n->payload++; n->next = g_free; g_free = n; }
        rsl_unlock(&g_rsl);
    }
    return 0;
}
static void t_spin(const char *name, int override_)
{
    hb_reset();
    g_override = override_;
    g_free = NULL;
    for (int i = 0; i < 64; i++) { g_nodes[i].next = g_free; g_nodes[i].payload = 0; g_free = &g_nodes[i]; }
    HANDLE h[3];
    for (int i = 0; i < 3; i++) h[i] = CreateThread(NULL, 0, pool_thread, NULL, 0, NULL);
    WaitForMultipleObjects(3, h, TRUE, INFINITE);
    for (int i = 0; i < 3; i++) CloseHandle(h[i]);
    int sum = 0;
    for (int i = 0; i < 64; i++) sum += g_nodes[i].payload;
    unsigned r = hb_races();
    char d[96];
    snprintf(d, sizeof d, "payload sum %d (want 900), races %u, %u checks", sum, r, hb_checks());
    hb_print(name);
    result(name, sum == 900 && (override_ ? r == 0 : r > 0), d);
}

/* ---- unsync */
static int g_plain;
static DWORD unsync_thread(LPVOID) { for (int i = 0; i < 100; i++) g_plain++; return 0; }
static void t_unsync(void)
{
    hb_reset();
    HANDLE h[2];
    for (int i = 0; i < 2; i++) h[i] = CreateThread(NULL, 0, unsync_thread, NULL, 0, NULL);
    WaitForMultipleObjects(2, h, TRUE, INFINITE);
    for (int i = 0; i < 2; i++) CloseHandle(h[i]);
    char d[64];
    snprintf(d, sizeof d, "races %u (want > 0)", hb_races());
    result("unsync", hb_races() > 0, d);
}

/* ---- detach */
extern "C" {
HANDLE __real_CreateThread(void *, SIZE_T, LPTHREAD_START_ROUTINE, LPVOID, DWORD, LPDWORD);
BOOL __real_SetEvent(HANDLE);
DWORD __real_WaitForSingleObject(HANDLE, DWORD);
LONG __real_InterlockedIncrement(volatile LONG *);
}
enum { DETACH_BATCH = 40, DETACH_BATCHES = 3 };
static HANDLE g_go, g_all;
static volatile LONG g_detached;
static DWORD detach_thread(LPVOID)
{
    __real_WaitForSingleObject(g_go, INFINITE);
    if (__real_InterlockedIncrement(&g_detached) % DETACH_BATCH == 0) __real_SetEvent(g_all);
    return 0;
}
static void t_detach(void)
{
    int made = 0, ended = 0;
    for (int b = 0; b < DETACH_BATCHES; b++) {
        g_go = CreateEventA(NULL, TRUE, FALSE, NULL);
        g_all = CreateEventA(NULL, FALSE, FALSE, NULL);
        for (int i = 0; i < DETACH_BATCH; i++) {
            HANDLE h = __real_CreateThread(NULL, 0, detach_thread, NULL, 0, NULL);
            if (!h) break;
            made++;
            CloseHandle(h);                                /* while it runs: its own unref frees the record */
        }
        __real_SetEvent(g_go);
        if (__real_WaitForSingleObject(g_all, 20000) == WAIT_OBJECT_0) ended += DETACH_BATCH;
        CloseHandle(g_go);
        CloseHandle(g_all);
        Sleep(50);                                         /* let the last ones reach their exit */
    }
    char d[96];
    snprintf(d, sizeof d, "%d threads created, %d ended (want %d)", made, ended, DETACH_BATCH * DETACH_BATCHES);
    result("detach", made == DETACH_BATCH * DETACH_BATCHES && ended == made, d);
}

extern "C" int mtq_main(void)
{
    printf("mtq: guest up, %u cores reported\n", (unsigned)fnvnx_hcall0(fnvnx_host->cpu_count));
    t_parallel();
    t_cs();
    t_stages();
    t_spin("spin-decomp", 0);
    t_spin("spin-override", 1);
    t_unsync();
    t_detach();
    printf("mtq: done, %d failed\n", g_fail);
    return g_fail;
}
