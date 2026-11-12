/* test_thread_contract.cpp - MSDN argument-domain contract of the thread/TLS functions: return value and
 * GetLastError on every error path (platform/win32/thread.cpp). */
#include "s4_test.h"

#ifndef ERROR_NOACCESS
#define ERROR_NOACCESS 998L
#endif
#ifndef ERROR_INVALID_PARAMETER
#define ERROR_INVALID_PARAMETER 87L
#endif
#ifndef STACK_SIZE_PARAM_IS_A_RESERVATION
#define STACK_SIZE_PARAM_IS_A_RESERVATION 0x00010000
#endif
#ifndef TLS_OUT_OF_INDEXES
#define TLS_OUT_OF_INDEXES 0xFFFFFFFFu
#endif

static HANDLE const BAD = (HANDLE)(uintptr_t)0x7ee4;
static volatile int g_go, g_stop;

static DWORD idle_thread(LPVOID)
{
    while (!g_stop)
        Sleep(1);
    return 5;
}

static DWORD quick(LPVOID) { return 3; }

static HANDLE spawn(void) { g_stop = 0; return CreateThread(NULL, 0, idle_thread, NULL, 0, NULL); }
static void reap(HANDLE h)
{
    g_stop = 1;
    WaitForSingleObject(h, 5000);
    CloseHandle(h);
}

/* ---- TLS */
static DWORD g_slot;
static volatile int g_phase;        /* 0: set, 1: read after realloc, 2: done */
static void *g_seen[3];
static volatile int g_ready[3];
static DWORD tls_worker_body(LPVOID p)
{
    int n = (int)(intptr_t)p;
    CHECK(TlsSetValue(g_slot, (LPVOID)(uintptr_t)(0x100 + n)));
    CHECK(TlsGetValue(g_slot) == (LPVOID)(uintptr_t)(0x100 + n));
    g_ready[n] = 1;
    while (g_phase < 1)
        Sleep(1);
    SetLastError(1234);
    g_seen[n] = TlsGetValue(g_slot);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    g_ready[n] = 2;
    return 0;
}
/* the CHECKs of this procedure end it, not the process: the harness guard (s4_test.h), the case fails at its join */
static DWORD tls_worker(LPVOID p)
{
    return s4_thread_guard([&]() -> DWORD { return tls_worker_body(p); }, (DWORD)0xFA11);
}

static void tls_reuse(DWORD slot_filler_count)
{
    (void)slot_filler_count;
    g_slot = TlsAlloc();
    CHECK(g_slot != TLS_OUT_OF_INDEXES);
    g_phase = 0;
    HANDLE h[3];
    for (int i = 0; i < 3; i++) {
        g_ready[i] = 0;
        g_seen[i] = (void *)1;
        h[i] = CreateThread(NULL, 0, tls_worker, (LPVOID)(intptr_t)i, 0, NULL);
    }
    CHECK(TlsSetValue(g_slot, (LPVOID)0x55));
    for (int i = 0; i < 3; i++)
        while (g_ready[i] < 1)
            Sleep(1);
    CHECK(TlsFree(g_slot));
    DWORD again = TlsAlloc();
    CHECK_EQ(again, g_slot);          /* lowest free slot is reused */
    CHECK(TlsGetValue(again) == NULL);
    g_phase = 1;
    for (int i = 0; i < 3; i++) {
        WaitForSingleObject(h[i], 5000);
        CloseHandle(h[i]);
        CHECK(g_seen[i] == NULL);
    }
    CHECK(TlsFree(again));
}

TEST(thread_contract_tls)
{
    tls_reuse(0);
    /* a slot above the first 64 */
    DWORD fill[70];
    int n = 0;
    for (; n < 70; n++)
        fill[n] = TlsAlloc();
    CHECK(fill[69] >= 64);
    DWORD hi = fill[69];
    CHECK(TlsSetValue(hi, (LPVOID)0x77));
    CHECK(TlsFree(hi));
    CHECK_EQ(TlsAlloc(), hi);
    CHECK(TlsGetValue(hi) == NULL);
    for (int i = 0; i < 70; i++)
        TlsFree(fill[i]);

    /* error paths */
    SetLastError(0);
    CHECK(TlsGetValue(5000) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(TlsGetValue(TLS_OUT_OF_INDEXES) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(!TlsSetValue(5000, (LPVOID)1));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(!TlsFree(5000));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    DWORD s = TlsAlloc();
    TlsFree(s);
    SetLastError(0);
    CHECK(!TlsFree(s));               /* double free */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(!TlsSetValue(s, (LPVOID)1)); /* unallocated */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    s = TlsAlloc();
    TlsSetValue(s, (LPVOID)9);
    SetLastError(777);
    CHECK(TlsGetValue(s) == (LPVOID)9);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    TlsFree(s);

    /* exhaustion: at least TLS_MINIMUM_AVAILABLE (64) slots */
    static DWORD all[4096];
    int cnt = 0;
    for (;; cnt++) {
        DWORD d = TlsAlloc();
        if (d == TLS_OUT_OF_INDEXES)
            break;
        all[cnt] = d;
        if (cnt == 4095)
            break;
    }
    CHECK(cnt >= 64);
    CHECK(cnt < 4095);
    CHECK(GetLastError() != ERROR_SUCCESS);
    for (int i = 0; i < cnt; i++)
        CHECK(TlsFree(all[i]));
}

/* ---- GetExitCodeThread */
TEST(thread_contract_exit_code)
{
    HANDLE h = spawn();
    DWORD c = 0;
    CHECK(GetExitCodeThread(h, &c));
    CHECK_EQ(c, STILL_ACTIVE);
    SetLastError(0);
    CHECK(!GetExitCodeThread(h, NULL));
    CHECK_EQ(GetLastError(), ERROR_NOACCESS);
    SetLastError(0);
    CHECK(!GetExitCodeThread(BAD, &c));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK(!GetExitCodeThread(NULL, &c));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    reap(h);
    h = CreateThread(NULL, 0, quick, NULL, 0, NULL);
    WaitForSingleObject(h, 5000);
    CHECK(GetExitCodeThread(h, &c) && c == 3);
    CloseHandle(h);
    c = 0;
    CHECK(GetExitCodeThread(GetCurrentThread(), &c));   /* the calling (main) thread is running */
    CHECK_EQ(c, STILL_ACTIVE);
}

/* ---- Suspend / Resume handle resolution */
TEST(thread_contract_suspend_handles)
{
    SetLastError(0);
    CHECK_EQ(ResumeThread(BAD), (DWORD)-1);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK_EQ(SuspendThread(BAD), (DWORD)-1);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK_EQ(ResumeThread(NULL), (DWORD)-1);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK_EQ(ResumeThread(GetCurrentThread()), 0);   /* main thread: not suspended */
    CHECK_EQ(GetLastError(), 0);
}

/* ---- priority */
TEST(thread_contract_priority)
{
    HANDLE h = spawn();
    static const int ok[] = { -15, -2, -1, 0, 1, 2, 15 };
    for (unsigned i = 0; i < sizeof ok / sizeof *ok; i++) {
        CHECK(SetThreadPriority(h, ok[i]));
        CHECK_EQ(GetThreadPriority(h), ok[i]);
    }
    static const int bad[] = { -14, -3, 3, 14, 16, -16, 100, (int)0x80000000 };
    CHECK(SetThreadPriority(h, 1));
    for (unsigned i = 0; i < sizeof bad / sizeof *bad; i++) {
        SetLastError(0);
        CHECK(!SetThreadPriority(h, bad[i]));
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
        CHECK_EQ(GetThreadPriority(h), 1);
    }
    SetLastError(0);
    CHECK(!SetThreadPriority(BAD, 0));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK(!SetThreadPriority(GetCurrentThread(), 5));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(SetThreadPriority(GetCurrentThread(), 0));
    reap(h);
}

/* ---- ideal processor */
TEST(thread_contract_ideal_processor)
{
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    DWORD n = si.dwNumberOfProcessors;
    HANDLE h = spawn();
    DWORD q = SetThreadIdealProcessor(h, 32);          /* query */
    CHECK(q < n);
    CHECK_EQ(SetThreadIdealProcessor(h, 32), q);       /* unchanged */
    if (n > 1) {
        DWORD t = (q + 1) % n;
        CHECK_EQ(SetThreadIdealProcessor(h, t), q);    /* returns previous */
        CHECK_EQ(SetThreadIdealProcessor(h, 32), t);
        CHECK_EQ(SetThreadIdealProcessor(h, q), t);
    }
    DWORD cur = SetThreadIdealProcessor(h, 32);
    SetLastError(0);
    CHECK_EQ(SetThreadIdealProcessor(h, n), (DWORD)-1);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK_EQ(SetThreadIdealProcessor(h, 33), (DWORD)-1);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK_EQ(SetThreadIdealProcessor(h, 0xFFFFFFFFu), (DWORD)-1);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(SetThreadIdealProcessor(h, 32), cur);     /* failures changed nothing */
    SetLastError(0);
    CHECK_EQ(SetThreadIdealProcessor(BAD, 0), (DWORD)-1);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    DWORD mq = SetThreadIdealProcessor(GetCurrentThread(), 32);
    CHECK(mq < n);
    reap(h);
}

/* ---- CreateThread flags */
TEST(thread_contract_create_flags)
{
    static const DWORD bad[] = { 1, 2, 8, 0x20, 0x8000, 0x20000, 0x80000000u, CREATE_SUSPENDED | 1 };
    for (unsigned i = 0; i < sizeof bad / sizeof *bad; i++) {
        SetLastError(0);
        DWORD id = 0xdead;
        CHECK(CreateThread(NULL, 0, quick, NULL, bad[i], &id) == NULL);
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
        CHECK_EQ(id, 0xdead);
    }
    HANDLE h = CreateThread(NULL, 4096, quick, NULL, STACK_SIZE_PARAM_IS_A_RESERVATION, NULL);   /* tid NULL ok */
    CHECK(h != NULL);
    CHECK_EQ(WaitForSingleObject(h, 5000), WAIT_OBJECT_0);
    CloseHandle(h);
    DWORD id = 0;
    h = CreateThread(NULL, 0, quick, NULL, CREATE_SUSPENDED | STACK_SIZE_PARAM_IS_A_RESERVATION, &id);
    CHECK(h != NULL && id != 0);
    CHECK_EQ(ResumeThread(h), 1);
    CHECK_EQ(WaitForSingleObject(h, 5000), WAIT_OBJECT_0);
    CloseHandle(h);
}
