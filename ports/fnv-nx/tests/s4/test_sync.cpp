/* test_sync.cpp - critical sections, Interlocked*, events, semaphores, mutexes, waits, threads, TLS. */
#include "s4_test.h"

#include "../../platform/os/os_clock.h"

static uint64_t now_ms(void) { return os_monotonic_ns() / 1000000ull; }

/* ---- critical sections */
static CRITICAL_SECTION g_cs;
static volatile LONG g_flag;

static DWORD try_enter(LPVOID) { return TryEnterCriticalSection(&g_cs) ? (LeaveCriticalSection(&g_cs), 1u) : 0u; }

TEST(cs_recursive)
{
    InitializeCriticalSection(&g_cs);
    EnterCriticalSection(&g_cs);
    EnterCriticalSection(&g_cs);
    CHECK(TryEnterCriticalSection(&g_cs)); /* recursion through TryEnter too */
    CHECK_EQ(g_cs.RecursionCount, 3);
    CHECK(g_cs.OwningThread == (HANDLE)(uintptr_t)GetCurrentThreadId());
    HANDLE t = test_CreateThread(NULL, 0, try_enter, NULL, 0, NULL);
    WaitForSingleObject(t, INFINITE);
    DWORD code = 99;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 0); /* held by us: another thread cannot enter */
    CloseHandle(t);
    LeaveCriticalSection(&g_cs);
    LeaveCriticalSection(&g_cs);
    t = test_CreateThread(NULL, 0, try_enter, NULL, 0, NULL);
    WaitForSingleObject(t, INFINITE);
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 0); /* still held once */
    CloseHandle(t);
    LeaveCriticalSection(&g_cs);
    CHECK(g_cs.OwningThread == NULL);
    t = test_CreateThread(NULL, 0, try_enter, NULL, 0, NULL);
    WaitForSingleObject(t, INFINITE);
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 1); /* released: now it can */
    CloseHandle(t);
    DeleteCriticalSection(&g_cs);
}

static CRITICAL_SECTION g_zero_cs; /* never initialised: zero storage must work */
static long g_counter;
enum { NTHREADS = 8, NITER = 50000 };

static DWORD cs_worker(LPVOID)
{
    for (int i = 0; i < NITER; i++) {
        EnterCriticalSection(&g_zero_cs);
        EnterCriticalSection(&g_zero_cs);
        long v = g_counter;
        g_counter = v + 1;
        LeaveCriticalSection(&g_zero_cs);
        LeaveCriticalSection(&g_zero_cs);
    }
    return 0;
}

TEST(cs_contention_zero_initialised)
{
    HANDLE t[NTHREADS];
    for (int i = 0; i < NTHREADS; i++)
        t[i] = test_CreateThread(NULL, 0, cs_worker, NULL, 0, NULL);
    CHECK_EQ(WaitForMultipleObjects(NTHREADS, t, TRUE, INFINITE), WAIT_OBJECT_0);
    for (int i = 0; i < NTHREADS; i++)
        CloseHandle(t[i]);
    CHECK_EQ(g_counter, (long)NTHREADS * NITER);
}

static volatile LONG g_ilk;
static DWORD ilk_worker(LPVOID)
{
    for (int i = 0; i < NITER; i++) {
        InterlockedIncrement(&g_ilk);
        InterlockedExchangeAdd(&g_ilk, 2);
        InterlockedDecrement(&g_ilk);
    }
    return 0;
}

TEST(interlocked)
{
    volatile LONG v = 5;
    CHECK_EQ(InterlockedIncrement(&v), 6);
    CHECK_EQ(InterlockedDecrement(&v), 5);
    CHECK_EQ(InterlockedExchange(&v, 9), 5);
    CHECK_EQ(InterlockedExchangeAdd(&v, 3), 9);
    CHECK_EQ(v, 12);
    CHECK_EQ(InterlockedCompareExchange(&v, 1, 7), 12); /* no match: unchanged */
    CHECK_EQ(v, 12);
    CHECK_EQ(InterlockedCompareExchange(&v, 1, 12), 12);
    CHECK_EQ(v, 1);
    /* a misaligned operand (packed field): x86's lock is atomic there; the split-lock path must give the same results */
    static unsigned char raw[16];
    volatile LONG *u = (volatile LONG *)(raw + 1);
    *u = 5;
    CHECK_EQ(InterlockedIncrement(u), 6);
    CHECK_EQ(InterlockedDecrement(u), 5);
    CHECK_EQ(InterlockedExchange(u, 9), 5);
    CHECK_EQ(InterlockedExchangeAdd(u, 3), 9);
    CHECK_EQ(InterlockedCompareExchange(u, 1, 7), 12);
    CHECK_EQ(InterlockedCompareExchange(u, 1, 12), 12);
    CHECK_EQ(*u, 1);
    void *volatile p = NULL;
    int x;
    CHECK(InterlockedCompareExchangePointer(&p, &x, NULL) == NULL);
    CHECK(p == &x);
    CHECK(InterlockedExchangePointer(&p, NULL) == &x);
    HANDLE t[NTHREADS];
    for (int i = 0; i < NTHREADS; i++)
        t[i] = test_CreateThread(NULL, 0, ilk_worker, NULL, 0, NULL);
    WaitForMultipleObjects(NTHREADS, t, TRUE, INFINITE);
    for (int i = 0; i < NTHREADS; i++)
        CloseHandle(t[i]);
    CHECK_EQ(InterlockedCompareExchange(&g_ilk, 0, 0), 2L * NTHREADS * NITER);
}

/* ---- events */
static HANDLE g_ev;
static volatile LONG g_woken;
static DWORD ev_waiter(LPVOID)
{
    if (WaitForSingleObject(g_ev, 2000) == WAIT_OBJECT_0)
        InterlockedIncrement(&g_woken);
    return 0;
}

TEST(event_auto_reset_wakes_one)
{
    g_ev = CreateEventA(NULL, FALSE, FALSE, NULL);
    g_woken = 0;
    HANDLE t[4];
    for (int i = 0; i < 4; i++)
        t[i] = test_CreateThread(NULL, 0, ev_waiter, NULL, 0, NULL);
    Sleep(100);
    SetEvent(g_ev);
    Sleep(200);
    CHECK_EQ(InterlockedCompareExchange(&g_woken, 0, 0), 1);
    SetEvent(g_ev);
    Sleep(200);
    CHECK_EQ(InterlockedCompareExchange(&g_woken, 0, 0), 2);
    WaitForMultipleObjects(4, t, TRUE, INFINITE);
    CHECK_EQ(InterlockedCompareExchange(&g_woken, 0, 0), 2); /* the other two timed out */
    for (int i = 0; i < 4; i++)
        CloseHandle(t[i]);
    CHECK_EQ(WaitForSingleObject(g_ev, 0), WAIT_TIMEOUT); /* consumed */
    CloseHandle(g_ev);
}

TEST(event_manual_reset_wakes_all)
{
    g_ev = CreateEventA(NULL, TRUE, FALSE, NULL);
    g_woken = 0;
    HANDLE t[4];
    for (int i = 0; i < 4; i++)
        t[i] = test_CreateThread(NULL, 0, ev_waiter, NULL, 0, NULL);
    Sleep(50);
    SetEvent(g_ev);
    WaitForMultipleObjects(4, t, TRUE, INFINITE);
    CHECK_EQ(InterlockedCompareExchange(&g_woken, 0, 0), 4);
    CHECK_EQ(WaitForSingleObject(g_ev, 0), WAIT_OBJECT_0); /* stays signalled */
    ResetEvent(g_ev);
    CHECK_EQ(WaitForSingleObject(g_ev, 0), WAIT_TIMEOUT);
    for (int i = 0; i < 4; i++)
        CloseHandle(t[i]);
    CloseHandle(g_ev);
}

TEST(wait_timeout)
{
    HANDLE e = CreateEventA(NULL, FALSE, FALSE, NULL);
    uint64_t t0 = now_ms();
    CHECK_EQ(WaitForSingleObject(e, 120), WAIT_TIMEOUT);
    uint64_t dt = now_ms() - t0;
    CHECK(dt >= 115 && dt < 1000);
    CHECK_EQ(WaitForSingleObject(NULL, 0), WAIT_FAILED);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CloseHandle(e);
}

TEST(wait_multiple_any_all)
{
    HANDLE e[3];
    for (int i = 0; i < 3; i++)
        e[i] = CreateEventA(NULL, TRUE, FALSE, NULL);
    SetEvent(e[2]);
    CHECK_EQ(WaitForMultipleObjects(3, e, FALSE, 0), WAIT_OBJECT_0 + 2);
    CHECK_EQ(WaitForMultipleObjects(3, e, TRUE, 10), WAIT_TIMEOUT);
    SetEvent(e[1]);
    CHECK_EQ(WaitForMultipleObjects(3, e, FALSE, 0), WAIT_OBJECT_0 + 1); /* lowest index wins */
    SetEvent(e[0]);
    CHECK_EQ(WaitForMultipleObjects(3, e, TRUE, 0), WAIT_OBJECT_0);
    for (int i = 0; i < 3; i++)
        CloseHandle(e[i]);
}

/* ---- semaphores and mutexes */
static HANDLE g_sem;
static volatile LONG g_consumed;
static DWORD consumer(LPVOID)
{
    while (WaitForSingleObject(g_sem, 500) == WAIT_OBJECT_0)
        InterlockedIncrement(&g_consumed);
    return 0;
}

TEST(semaphore)
{
    g_sem = CreateSemaphoreA(NULL, 0, 1000, NULL);
    CHECK(g_sem != NULL);
    CHECK(CreateSemaphoreA(NULL, 5, 2, NULL) == NULL);
    g_consumed = 0;
    HANDLE t[3];
    for (int i = 0; i < 3; i++)
        t[i] = test_CreateThread(NULL, 0, consumer, NULL, 0, NULL);
    LONG prev = -1;
    for (int i = 0; i < 100; i++)
        ReleaseSemaphore(g_sem, 3, i == 0 ? &prev : NULL);
    CHECK_EQ(prev, 0);
    WaitForMultipleObjects(3, t, TRUE, INFINITE);
    CHECK_EQ(InterlockedCompareExchange(&g_consumed, 0, 0), 300);
    CHECK(ReleaseSemaphore(g_sem, 999, NULL));
    CHECK(!ReleaseSemaphore(g_sem, 2, NULL));
    CHECK_EQ(GetLastError(), ERROR_TOO_MANY_POSTS);
    for (int i = 0; i < 3; i++)
        CloseHandle(t[i]);
    CloseHandle(g_sem);
}

static HANDLE g_mx;
static DWORD mutex_other(LPVOID)
{
    DWORD r = WaitForSingleObject(g_mx, 0);
    BOOL rel = ReleaseMutex(g_mx); /* not the owner unless it got it */
    return (r == WAIT_OBJECT_0 ? 1u : 0u) | (rel ? 2u : 0u);
}

TEST(mutex_ownership_and_names)
{
    g_mx = CreateMutexA(NULL, TRUE, "Local\\fnvnx-test");
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    CHECK_EQ(WaitForSingleObject(g_mx, 0), WAIT_OBJECT_0); /* recursive for the owner */
    HANDLE t = test_CreateThread(NULL, 0, mutex_other, NULL, 0, NULL);
    WaitForSingleObject(t, INFINITE);
    DWORD code;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 0);
    CloseHandle(t);
    HANDLE again = CreateMutexA(NULL, FALSE, "Local\\fnvnx-test");
    CHECK(again != NULL && again != g_mx); /* a second handle ... */
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK(ReleaseMutex(again));             /* ... to the same object */
    CHECK(ReleaseMutex(g_mx));
    CHECK(!ReleaseMutex(g_mx));
    CHECK_EQ(GetLastError(), ERROR_NOT_OWNER);
    t = test_CreateThread(NULL, 0, mutex_other, NULL, 0, NULL);
    WaitForSingleObject(t, INFINITE);
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 3);
    CloseHandle(t);
    CloseHandle(again);
    CloseHandle(g_mx);
}

/* ---- threads */
static volatile LONG g_ran;
static DWORD record(LPVOID p)
{
    InterlockedExchange(&g_ran, 1);
    return (DWORD)(uintptr_t)p;
}

TEST(thread_suspended_start_and_exit_code)
{
    g_ran = 0;
    DWORD tid = 0;
    HANDLE t = test_CreateThread(NULL, 64 * 1024, record, (LPVOID)(uintptr_t)1234, CREATE_SUSPENDED, &tid);
    CHECK(t != NULL);
    CHECK(tid != 0 && tid % 4 == 0);
    Sleep(50);
    CHECK_EQ(InterlockedCompareExchange(&g_ran, 0, 0), 0);
    DWORD code = 0;
    CHECK(GetExitCodeThread(t, &code));
    CHECK_EQ(code, STILL_ACTIVE);
    CHECK_EQ(WaitForSingleObject(t, 0), WAIT_TIMEOUT);
    CHECK_EQ(ResumeThread(t), 1);
    CHECK_EQ(WaitForSingleObject(t, 2000), WAIT_OBJECT_0);
    CHECK_EQ(InterlockedCompareExchange(&g_ran, 0, 0), 1);
    CHECK(GetExitCodeThread(t, &code));
    CHECK_EQ(code, 1234);
    CHECK(SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_NORMAL));
    CHECK(CloseHandle(t));
}

static DWORD exit_early(LPVOID)
{
    ExitThread(77);
}

TEST(thread_exitthread)
{
    HANDLE t = test_CreateThread(NULL, 0, exit_early, NULL, 0, NULL);
    CHECK_EQ(WaitForSingleObject(t, 2000), WAIT_OBJECT_0);
    DWORD code = 0;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 77);
    CloseHandle(t);
}

/* ---- TLS */
static DWORD g_slot, g_slot_hi;
static DWORD tls_worker(LPVOID p)
{
    if (TlsGetValue(g_slot) != NULL || TlsGetValue(g_slot_hi) != NULL)
        return 1;
    TlsSetValue(g_slot, p);
    TlsSetValue(g_slot_hi, (char *)p + 1);
    Sleep(20);
    return TlsGetValue(g_slot) == p && TlsGetValue(g_slot_hi) == (char *)p + 1 ? 0 : 2;
}

TEST(tls_slots)
{
    g_slot = TlsAlloc();
    CHECK(g_slot < 64);
    /* use up the first 64 so one slot comes from the extended range */
    DWORD extra[70];
    int n = 0;
    while (n < 70 && (extra[n] = TlsAlloc()) < 64)
        n++;
    g_slot_hi = extra[n];
    CHECK(g_slot_hi >= 64 && g_slot_hi < 1088);
    TlsSetValue(g_slot, (void *)0x10);
    HANDLE t[4];
    for (int i = 0; i < 4; i++)
        t[i] = test_CreateThread(NULL, 0, tls_worker, (LPVOID)(uintptr_t)(0x1000 * (i + 1)), 0, NULL);
    WaitForMultipleObjects(4, t, TRUE, INFINITE);
    for (int i = 0; i < 4; i++) {
        DWORD c = 9;
        GetExitCodeThread(t[i], &c);
        CHECK_EQ(c, 0);
        CloseHandle(t[i]);
    }
    CHECK(TlsGetValue(g_slot) == (void *)0x10);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    for (int i = 0; i <= n; i++)
        TlsFree(extra[i]);
    CHECK(TlsFree(g_slot));
}

/* ---- implicit TLS (image .tls) and the fake TEB */
static const unsigned char g_tls_template[16] = { 1, 2, 3, 4, 5, 6, 7, 8 };
static DWORD teb_worker(LPVOID)
{
    unsigned char *blk = (unsigned char *)fnvnx_tls_array()[0];
    if (!blk || blk[0] != 1 || blk[7] != 8 || blk[12] != 0)
        return 1;
    blk[0] = 42; /* private to this thread */
    return __readfsdword(0x24) == GetCurrentThreadId() ? 0 : 2;
}

TEST(implicit_tls_and_teb)
{
    fnvnx_tls_set_template(g_tls_template, 8, 32);
    unsigned char *mine = (unsigned char *)fnvnx_tls_block();
    CHECK(mine && mine[3] == 4);
    CHECK(fnvnx_tls_array()[0] == mine);
    HANDLE t = test_CreateThread(NULL, 0, teb_worker, NULL, 0, NULL);
    WaitForSingleObject(t, INFINITE);
    DWORD c = 9;
    GetExitCodeThread(t, &c);
    CHECK_EQ(c, 0);
    CloseHandle(t);
    CHECK_EQ(mine[0], 1);
    CHECK_EQ(__readfsdword(0x24), GetCurrentThreadId());
    CHECK_EQ(__readfsdword(0x00), 0xFFFFFFFFul);
    SetLastError(1234);
    CHECK_EQ(__readfsdword(0x34), 1234);
    if (sizeof(void *) == 4) /* the fs:[0x2c] pointer only round-trips with 32-bit pointers */
        CHECK(__readfsdword(0x2c) == (unsigned long)(uintptr_t)fnvnx_tls_array());
}

/* fs:[0x2c][0] is the game layer's image TLS block (fnvnx_image_tls_hook, toolchain/game/boot.cpp), the same
 * per-thread block __declspec(thread) data is bound to: one copy, not two (docs/PERF.md 6.3) */
extern "C" void *(*fnvnx_image_tls_hook)(void);
static thread_local unsigned char t_image_tls[64];
static void *image_tls(void) { return t_image_tls; }
static DWORD tls_identity_worker(LPVOID)
{
    void *blk = ((void **)fnvnx_tls_array())[0];
    if (blk != (void *)t_image_tls)
        return 1;
    t_image_tls[3] = 77;   /* through the __declspec(thread) path ... */
    return ((unsigned char *)fnvnx_tls_block())[3] == 77 ? 0 : 2;   /* ... seen through fs:[0x2c] */
}

TEST(implicit_tls_identity)
{
    void *(*saved)(void) = fnvnx_image_tls_hook;
    fnvnx_image_tls_hook = image_tls;
    HANDLE t = test_CreateThread(NULL, 0, tls_identity_worker, NULL, 0, NULL);
    WaitForSingleObject(t, INFINITE);
    DWORD c = 9;
    GetExitCodeThread(t, &c);
    CHECK_EQ(c, 0);
    CloseHandle(t);
    CHECK_EQ(t_image_tls[3], 0);   /* the worker wrote its own block */
    fnvnx_image_tls_hook = saved;
}

/* the placement role hook sees CreateThread's start routine and argument; priority and affinity apply to
 * another thread's handle (suspended or running) */
extern "C" int (*fnvnx_thread_role_hook)(void *start, void *arg);
static void *g_role_start, *g_role_arg;
static int role_hook(void *start, void *arg) { g_role_start = start; g_role_arg = arg; return OS_THREAD_ROLE(2); }
static DWORD role_worker(LPVOID) { return 5; }

TEST(thread_role_and_other_thread_placement)
{
    int (*saved)(void *, void *) = fnvnx_thread_role_hook;
    fnvnx_thread_role_hook = role_hook;
    HANDLE t = test_CreateThread(NULL, 0, role_worker, (LPVOID)0x1234, CREATE_SUSPENDED, NULL);
    fnvnx_thread_role_hook = saved;
    CHECK(t != NULL);
    CHECK(test_threads::win_start_is(g_role_start, g_role_arg, role_worker, (LPVOID)0x1234));   /* the thread is still suspended: its start record is alive */
    CHECK(SetThreadPriority(t, THREAD_PRIORITY_BELOW_NORMAL));
    CHECK_EQ(GetThreadPriority(t), THREAD_PRIORITY_BELOW_NORMAL);
    CHECK(SetThreadAffinityMask(t, 0x6) != 0);
    CHECK(SetThreadIdealProcessor(t, 1) != (DWORD)-1);
    CHECK(SetThreadAffinityMask(t, 0) == 0);   /* an empty mask is refused */
    ResumeThread(t);
    WaitForSingleObject(t, INFINITE);
    DWORD c = 0;
    GetExitCodeThread(t, &c);
    CHECK_EQ(c, 5);
    CloseHandle(t);
}

/* SpinCount: contended sections still exclude each other and count right with spinning on */
static CRITICAL_SECTION g_spin_cs;
static long g_spin_counter;
static DWORD spin_worker(LPVOID)
{
    for (int i = 0; i < 20000; i++) {
        EnterCriticalSection(&g_spin_cs);
        g_spin_counter++;
        LeaveCriticalSection(&g_spin_cs);
    }
    return 0;
}

TEST(critical_section_spin_count)
{
    CHECK(InitializeCriticalSectionAndSpinCount(&g_spin_cs, 0x80001000));
    CHECK_EQ(g_spin_cs.SpinCount & 0xFFFFFF, 0x1000);
    g_spin_counter = 0;
    HANDLE t[3];
    for (int i = 0; i < 3; i++)
        t[i] = test_CreateThread(NULL, 0, spin_worker, NULL, 0, NULL);
    WaitForMultipleObjects(3, t, TRUE, INFINITE);
    for (int i = 0; i < 3; i++)
        CloseHandle(t[i]);
    CHECK_EQ(g_spin_counter, 60000);
    DeleteCriticalSection(&g_spin_cs);
}

/* RaiseException is w32_raise: with the lifter runtime's backend installed the SEH dispatch decides; the
 * thread-name exception 0x406D1388 is continued by the game's own handler, so the call returns. */
extern "C" void fnvnx_win32_set_raise_backend(void (*)(uint32_t, uint32_t, uint32_t, const uintptr_t *));
static uint32_t g_raised_code, g_raised_flags, g_raised_nargs;
static uintptr_t g_raised_args[15];
static void record_raise(uint32_t code, uint32_t flags, uint32_t nargs, const uintptr_t *args)
{
    g_raised_code = code; g_raised_flags = flags; g_raised_nargs = nargs;
    for (uint32_t i = 0; i < nargs; i++) g_raised_args[i] = args[i];
}
TEST(set_thread_name_exception)
{
    struct { DWORD type; const char *name; DWORD tid; DWORD flags; } info = { 0x1000, "S4Worker", (DWORD)-1, 0 };
    ULONG_PTR args[4] = { info.type, (ULONG_PTR)info.name, info.tid, info.flags };
    fnvnx_win32_set_raise_backend(record_raise);
    RaiseException(MS_VC_EXCEPTION, 0, 4, args); /* returns: the backend continued it */
    CHECK_EQ(g_raised_code, 0x406D1388u);
    CHECK_EQ(g_raised_nargs, 4u);
    CHECK_EQ(g_raised_args[1], (uintptr_t)info.name);
    /* the documented normalisation: flags masked to NONCONTINUABLE, nargs clamped to 15, NULL args -> 0 */
    ULONG_PTR big[20] = { 0 };
    RaiseException(0xE0000001u, 0xFFFFFFFEu, 20, big);
    CHECK_EQ(g_raised_flags, 0u);
    CHECK_EQ(g_raised_nargs, 15u);
    RaiseException(0xE0000002u, 1, 3, NULL);
    CHECK_EQ(g_raised_flags, 1u);
    CHECK_EQ(g_raised_nargs, 0u);
    fnvnx_win32_set_raise_backend(NULL);
}
