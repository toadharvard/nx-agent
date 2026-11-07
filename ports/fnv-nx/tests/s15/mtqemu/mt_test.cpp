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
 *                   Not checked by hb (calls the __real_ Win32 functions).
 *   placement       PERF.md 6.3 on host threads: main on core 0, a game thread on mask 0b110, and
 *                   SetThreadAffinityMask / SetThreadIdealProcessor / SetThreadPriority on another thread's handle,
 *                   before its start (CREATE_SUSPENDED) and while it runs; each thread reads its own affinity with
 *                   sched_getaffinity (qemu-user passes it to the host kernel). Priorities are advisory under CFS:
 *                   only that the calls succeed is checked.
 *   ilk-count, ilk-refs   the inlined Interlocked* of fnvnx_win32.h (ilk_mt.cpp)
 *   ilk-cost        benchmark B9 (ilk_cost.cpp, uninstrumented): ns per Interlocked operation, plain / LL-SC / inline /
 *                   call / with a store in flight / shared by 2 threads; checks only the counts
 *   litmus-order    S12's litmus test (tests/s12/order/litmus.cpp via litmus_mt.cpp) built with the fnvnx_order
 *                   plugin: no bad outcome; mp2, lock and ref without a race. mp1's scratch store after the release is
 *                   a value race by design (either value is accepted), so its races are printed, not judged. ref is
 *                   S12's refcount protocol under the plugin's refcount=weak (relaxed increment, release decrement,
 *                   acquire fence on the path to 0): hb.c models the fence, so a missing acquire shows as a race.
 *   litmus-plain    the same without the plugin: the checker must report races in mp2 and lock (their volatile
 *                   hand-offs are plain accesses for GCC).
 *   race-pairs      D25 #7: a writer and a reader function race on one word; hb_dump prints the "hb-race" pair lines
 *                   that tools/threads/race_pairs.py groups per function pair (test_mtqemu.py checks the mapping)
 *   wg-clean        the wait-graph watchdog (platform/win32/waitgraph.cpp) on: the cs workload reports nothing
 *   wg-deadlock     thread A holds a section and waits on a mutex B holds while B enters A's section: one cycle
 *                   (A's mutex wait times out after 3 s and both finish)
 *   wg-hang         a thread waits on an event for 1.2 s with a 0.5 s limit: one long wait */
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
extern "C" void mtq_result(const char *name, int ok, const char *detail) { result(name, ok != 0, detail); }

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

/* ---- placement */
static unsigned my_affinity(void)
{
    unsigned long long m = 0;
    register long x0 __asm__("x0") = 0;                  /* this thread */
    register long x1 __asm__("x1") = sizeof m;
    register long x2 __asm__("x2") = (long)(uintptr_t)&m;
    register long x8 __asm__("x8") = 123;                /* sched_getaffinity */
    __asm__ volatile("svc 0" : "+r"(x0) : "r"(x1), "r"(x2), "r"(x8) : "memory");
    return x0 < 0 ? 0xFFFFFFFFu : (unsigned)(m & 0xF);
}
static HANDLE g_pl_moved, g_pl_seen;
static volatile LONG g_pl_first, g_pl_second;
static DWORD place_thread(LPVOID)
{
    InterlockedExchange(&g_pl_first, (LONG)my_affinity());
    SetEvent(g_pl_seen);
    WaitForSingleObject(g_pl_moved, INFINITE);           /* main changes this thread's mask from outside */
    InterlockedExchange(&g_pl_second, (LONG)my_affinity());
    return 0;
}
static void t_placement(void)
{
    char d[200];
    unsigned m_main = my_affinity();
    g_pl_seen = CreateEventA(NULL, FALSE, FALSE, NULL);
    g_pl_moved = CreateEventA(NULL, FALSE, FALSE, NULL);
    /* 1: a game thread as placed (row game: mask 0b110), then moved by main through its handle */
    HANDLE a = CreateThread(NULL, 0, place_thread, NULL, 0, NULL);
    WaitForSingleObject(g_pl_seen, INFINITE);
    unsigned game_first = (unsigned)g_pl_first;
    BOOL pr = SetThreadPriority(a, THREAD_PRIORITY_ABOVE_NORMAL);
    SetThreadAffinityMask(a, 0x4);
    SetEvent(g_pl_moved);
    WaitForSingleObject(a, INFINITE);
    unsigned game_moved = (unsigned)g_pl_second;
    CloseHandle(a);
    /* 2: created suspended: the mask set before the first ResumeThread holds from its first instruction */
    HANDLE b = CreateThread(NULL, 0, place_thread, NULL, CREATE_SUSPENDED, NULL);
    SetThreadAffinityMask(b, 0x2);
    SetThreadIdealProcessor(b, 1);
    BOOL pr2 = SetThreadPriority(b, THREAD_PRIORITY_BELOW_NORMAL);
    ResumeThread(b);
    WaitForSingleObject(g_pl_seen, INFINITE);
    unsigned susp_first = (unsigned)g_pl_first;
    SetThreadIdealProcessor(b, 2);                        /* an ideal core outside the mask joins it */
    SetEvent(g_pl_moved);
    WaitForSingleObject(b, INFINITE);
    unsigned susp_ideal = (unsigned)g_pl_second;
    CloseHandle(b);
    CloseHandle(g_pl_seen);
    CloseHandle(g_pl_moved);
    snprintf(d, sizeof d, "main 0x%x (want 0x1), game 0x%x (want 0x6), moved 0x%x (want 0x4), suspended 0x%x (want 0x2), "
             "ideal 2 joins 0x%x (want 0x6), priority calls %d %d", m_main, game_first, game_moved, susp_first,
             susp_ideal, pr, pr2);
    result("placement", m_main == 0x1 && game_first == 0x6 && game_moved == 0x4 && susp_first == 0x2 &&
                        susp_ideal == 0x6 && pr && pr2, d);
}

/* ---- litmus (S12) */
extern "C" {
extern int litmus_available;
extern char litmus_out[];
void litmus_begin(void);
int litmus_main_order(void);
int litmus_main_plain(void);
void ilk_tests(void);
void ilk_cost(void);
}
struct LitRow { unsigned rounds, bad, races; };
static bool lit_row(const char *out, const char *test, LitRow *r)
{
    for (const char *p = out; *p; ) {
        const char *t = test, *q = p;
        while (*t && *q == *t) { t++; q++; }
        if (!*t && *q == ' ') {
            unsigned v[3] = {0, 0, 0};
            for (int i = 0; i < 3; i++) {
                while (*q && *q != '=' && *q != '\n') q++;
                if (*q != '=') return false;
                q++;
                while (*q >= '0' && *q <= '9') v[i] = v[i] * 10 + (unsigned)(*q++ - '0');
            }
            *r = LitRow{v[0], v[1], v[2]};
            return true;
        }
        while (*p && *p != '\n') p++;
        if (*p) p++;
    }
    return false;
}
static void t_litmus(const char *name, int (*fn)(void), bool order)
{
    char d[160];
    if (!litmus_available) { printf("mtq: %s skip (no fnvnx_order plugin here)\n", name); return; }
    litmus_begin();
    fn();
    LitRow m1, m2, lk, rf;
    bool got = lit_row(litmus_out, "mp1", &m1) && lit_row(litmus_out, "mp2", &m2) && lit_row(litmus_out, "lock", &lk) &&
               lit_row(litmus_out, "ref", &rf);
    if (!got) { result(name, false, "no result lines"); return; }
    snprintf(d, sizeof d, "rounds %u: bad mp1 %u mp2 %u lock %u ref %u; races mp1 %u mp2 %u lock %u ref %u", m2.rounds,
             m1.bad, m2.bad, lk.bad, rf.bad, m1.races, m2.races, lk.races, rf.races);
    if (order)
        result(name, m1.bad == 0 && m2.bad == 0 && lk.bad == 0 && rf.bad == 0 && m2.races == 0 && lk.races == 0 &&
                     rf.races == 0, d);
    else
        result(name, m2.races > 0 && lk.races > 0, d);
}

/* ---- race-pairs */
extern "C" void hb_dump(void);
static int g_pair_word;
static volatile int g_pair_sink;
extern "C" __attribute__((noinline)) void mtq_pair_writer(void) { for (int i = 0; i < 50; i++) g_pair_word = i; }
extern "C" __attribute__((noinline)) void mtq_pair_reader(void) { for (int i = 0; i < 50; i++) g_pair_sink = g_pair_word; }
static DWORD pair_w(LPVOID) { mtq_pair_writer(); return 0; }
static DWORD pair_r(LPVOID) { mtq_pair_reader(); return 0; }
static void t_race_pairs(void)
{
    hb_reset();
    HANDLE h[2] = {CreateThread(NULL, 0, pair_w, NULL, 0, NULL), CreateThread(NULL, 0, pair_r, NULL, 0, NULL)};
    WaitForMultipleObjects(2, h, TRUE, INFINITE);
    for (int i = 0; i < 2; i++) CloseHandle(h[i]);
    hb_dump();
    char d[64];
    snprintf(d, sizeof d, "races %u (want > 0)", hb_races());
    result("race-pairs", hb_races() > 0, d);
}

/* ---- wait-graph watchdog */
static void wg_sink(const char *line) { printf("%s\n", line); }
static CRITICAL_SECTION g_wg_cs;
static HANDLE g_wg_mutex, g_wg_ev_a, g_wg_ev_b, g_wg_hang;
static DWORD g_wg_a_rc;
static DWORD wg_a(LPVOID)
{
    EnterCriticalSection(&g_wg_cs);
    WaitForSingleObject(g_wg_ev_b, INFINITE);         /* B holds the mutex */
    SetEvent(g_wg_ev_a);
    g_wg_a_rc = WaitForSingleObject(g_wg_mutex, 3000); /* A -> mutex -> B */
    LeaveCriticalSection(&g_wg_cs);
    return 0;
}
static DWORD wg_b(LPVOID)
{
    WaitForSingleObject(g_wg_mutex, INFINITE);
    SetEvent(g_wg_ev_b);
    WaitForSingleObject(g_wg_ev_a, INFINITE);
    Sleep(50);
    EnterCriticalSection(&g_wg_cs);                    /* B -> section -> A */
    LeaveCriticalSection(&g_wg_cs);
    ReleaseMutex(g_wg_mutex);
    return 0;
}
static DWORD wg_hang(LPVOID) { WaitForSingleObject(g_wg_hang, INFINITE); return 0; }
static void t_waitgraph(void)
{
    unsigned c0, l0, c1, l1, c2, l2, c3, l3, sc;
    fnvnx_waitgraph_start(1500, 100, wg_sink);
    /* clean: contention without a cycle, no wait near the limit */
    hb_reset();
    g_count = 0;
    HANDLE h[4];
    for (int i = 0; i < 4; i++) h[i] = CreateThread(NULL, 0, cs_thread, NULL, 0, NULL);
    WaitForMultipleObjects(4, h, TRUE, INFINITE);
    for (int i = 0; i < 4; i++) CloseHandle(h[i]);
    Sleep(300);
    fnvnx_waitgraph_counts(&c0, &l0, &sc);
    char d[128];
    snprintf(d, sizeof d, "cycles %u long-waits %u (want 0, 0), %u scans", c0, l0, sc);
    result("wg-clean", c0 == 0 && l0 == 0 && sc > 0, d);
    /* deadlock */
    InitializeCriticalSection(&g_wg_cs);
    g_wg_mutex = CreateMutexA(NULL, FALSE, NULL);
    g_wg_ev_a = CreateEventA(NULL, TRUE, FALSE, NULL);
    g_wg_ev_b = CreateEventA(NULL, TRUE, FALSE, NULL);
    HANDLE t[2] = {CreateThread(NULL, 0, wg_a, NULL, 0, NULL), CreateThread(NULL, 0, wg_b, NULL, 0, NULL)};
    WaitForMultipleObjects(2, t, TRUE, INFINITE);
    for (int i = 0; i < 2; i++) CloseHandle(t[i]);
    fnvnx_waitgraph_counts(&c1, &l1, &sc);
    snprintf(d, sizeof d, "cycles %u (want 1), A's mutex wait rc %u (want 0x102), long-waits %u", c1 - c0,
             (unsigned)g_wg_a_rc, l1 - l0);
    result("wg-deadlock", c1 - c0 == 1 && g_wg_a_rc == WAIT_TIMEOUT, d);
    /* hang: a wait over the limit */
    fnvnx_waitgraph_stop();
    fnvnx_waitgraph_start(500, 100, wg_sink);
    fnvnx_waitgraph_counts(&c2, &l2, &sc);
    g_wg_hang = CreateEventA(NULL, TRUE, FALSE, NULL);
    HANDLE th = CreateThread(NULL, 0, wg_hang, NULL, 0, NULL);
    Sleep(1200);
    SetEvent(g_wg_hang);
    WaitForSingleObject(th, INFINITE);
    CloseHandle(th);
    fnvnx_waitgraph_stop();
    fnvnx_waitgraph_counts(&c3, &l3, &sc);
    snprintf(d, sizeof d, "long-waits %u (want 1), cycles %u (want 0)", l3 - l2, c3 - c2);
    result("wg-hang", l3 - l2 == 1 && c3 == c2, d);
    fnvnx_waitgraph_summary();
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
    t_placement();
    ilk_tests();
    ilk_cost();
    t_litmus("litmus-order", litmus_main_order, true);
    t_litmus("litmus-plain", litmus_main_plain, false);
    t_race_pairs();
    t_waitgraph();
    printf("mtq: done, %d failed\n", g_fail);
    return g_fail;
}
