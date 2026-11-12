/* test_sync_conf.cpp - conformance of the thread and synchronisation APIs with documented Win32 behaviour: thread
 * creation/suspend/exit codes, events (auto/manual, named), semaphores, mutexes (recursion, ownership, abandonment),
 * WaitForSingleObject/WaitForMultipleObjects (any/all, lowest index, atomic acquire, timeouts, bad arguments),
 * critical sections, Interlocked*, TLS, Sleep/SwitchToThread, CloseHandle on these objects.
 * Every wait in here has a finite timeout (WAIT_MAX) so a regression fails a check instead of hanging the suite. */
#include "s4_test.h"

#include "../../platform/os/os_clock.h"

#define WAIT_MAX 5000u

static uint64_t ms_now(void) { return os_monotonic_ns() / 1000000ull; }
static bool signalled(HANDLE h, DWORD ms = WAIT_MAX) { return WaitForSingleObject(h, ms) == WAIT_OBJECT_0; }
static LONG ld(volatile LONG *p) { return InterlockedCompareExchange(p, 0, 0); }

/* ---- threads */
static volatile LONG c_tid_seen, c_ran;
static HANDLE c_gate;

static DWORD tid_reporter(LPVOID)
{
    InterlockedExchange(&c_tid_seen, (LONG)GetCurrentThreadId());
    return 0;
}

TEST(conf_thread_id_and_pseudo_handle)
{
    DWORD tid = 0;
    HANDLE t = test_CreateThread(NULL, 0, tid_reporter, NULL, 0, &tid);
    CHECK(t != NULL);
    CHECK(signalled(t));
    CHECK(tid != 0);
    CHECK_EQ((DWORD)ld(&c_tid_seen), tid);   /* lpThreadId is the id the thread sees */
    CHECK(tid != GetCurrentThreadId());
    HANDLE t2 = test_CreateThread(NULL, 0, tid_reporter, NULL, 0, NULL);   /* lpThreadId may be NULL */
    CHECK(t2 != NULL);
    CHECK(signalled(t2));
    CHECK(GetCurrentThread() != t && GetCurrentThread() != NULL);      /* a pseudo handle, not a real one */
    CHECK_EQ(GetCurrentThreadId(), GetCurrentThreadId());
    CHECK(CloseHandle(t));
    CHECK(CloseHandle(t2));
    CHECK(!CloseHandle(t));                                            /* already closed */
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
}

static DWORD gate_thread(LPVOID p)
{
    DWORD code = 0;
    BOOL got = GetExitCodeThread(GetCurrentThread(), &code);   /* a thread asking about itself: still active */
    InterlockedExchange(&c_ran, got && code == STILL_ACTIVE ? 1 : 2);
    return WaitForSingleObject(c_gate, WAIT_MAX) == WAIT_OBJECT_0 ? (DWORD)(uintptr_t)p : 0xDEAD;
}

TEST(conf_thread_still_active_and_exit_code)
{
    c_gate = CreateEventA(NULL, TRUE, FALSE, NULL);
    c_ran = 0;
    HANDLE t = test_CreateThread(NULL, 0, gate_thread, (LPVOID)(uintptr_t)4242, 0, NULL);
    for (int i = 0; i < 500 && !ld(&c_ran); i++)
        Sleep(2);
    CHECK_EQ(ld(&c_ran), 1);
    DWORD code = 0;
    CHECK(GetExitCodeThread(t, &code));
    CHECK_EQ(code, STILL_ACTIVE);
    CHECK_EQ(STILL_ACTIVE, 259);
    CHECK_EQ(WaitForSingleObject(t, 0), WAIT_TIMEOUT);   /* a running thread is not signalled */
    SetEvent(c_gate);
    CHECK(signalled(t));
    CHECK(GetExitCodeThread(t, &code));
    CHECK_EQ(code, 4242);
    CHECK_EQ(WaitForSingleObject(t, 0), WAIT_OBJECT_0);  /* stays signalled */
    CHECK(GetExitCodeThread(GetCurrentThread(), &code)); /* the main thread: running */
    CHECK_EQ(code, STILL_ACTIVE);
    CHECK(!GetExitCodeThread(NULL, &code));
    CloseHandle(t);
    CloseHandle(c_gate);
}

static DWORD set_ran(LPVOID)
{
    InterlockedIncrement(&c_ran);
    return 0;
}

TEST(conf_thread_suspend_counts)
{
    c_ran = 0;
    HANDLE t = test_CreateThread(NULL, 0, set_ran, NULL, CREATE_SUSPENDED, NULL);
    CHECK(t != NULL);
    CHECK_EQ(SuspendThread(t), 1);   /* previous suspend count: created suspended = 1 */
    CHECK_EQ(ResumeThread(t), 2);    /* previous count; still suspended at 1 */
    Sleep(40);
    CHECK_EQ(ld(&c_ran), 0);
    CHECK_EQ(ResumeThread(t), 1);    /* count reaches 0: runs */
    CHECK(signalled(t));
    CHECK_EQ(ld(&c_ran), 1);
    CHECK_EQ(ResumeThread(t), 0);    /* not suspended: previous count 0 */
    CHECK_EQ(ResumeThread(NULL), (DWORD)-1);
    CHECK_EQ(SuspendThread(NULL), (DWORD)-1);
    CHECK_EQ(ResumeThread(INVALID_HANDLE_VALUE), (DWORD)-1);
    CloseHandle(t);
}

TEST(conf_thread_stack_sizes)
{
    c_ran = 0;
    HANDLE t = test_CreateThread(NULL, 4096, set_ran, NULL, 0, NULL);   /* tiny reserve: the thread still runs */
    CHECK(t != NULL && signalled(t));
    CloseHandle(t);
    t = test_CreateThread(NULL, 8 * 1024 * 1024, set_ran, NULL, 0, NULL);
    CHECK(t != NULL && signalled(t));
    CloseHandle(t);
    CHECK_EQ(ld(&c_ran), 2);
}

TEST(conf_thread_handle_closed_while_running)
{
    c_gate = CreateEventA(NULL, TRUE, FALSE, NULL);
    c_ran = 0;
    for (int i = 0; i < 20; i++) {
        HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
            WaitForSingleObject(c_gate, WAIT_MAX);
            InterlockedIncrement(&c_ran);
            return 0;
        }, NULL, 0, NULL);
        CHECK(t != NULL && CloseHandle(t));   /* the thread outlives its handle */
    }
    SetEvent(c_gate);
    for (int i = 0; i < 1000 && ld(&c_ran) < 20; i++)
        Sleep(5);
    CHECK_EQ(ld(&c_ran), 20);
    CloseHandle(c_gate);
}

TEST(conf_thread_many_short_lived)
{
    c_ran = 0;
    for (int i = 0; i < 300; i++) {
        HANDLE t = test_CreateThread(NULL, 0, set_ran, NULL, 0, NULL);
        if (!t) {
            CHECK(t != NULL);
            break;
        }
        CloseHandle(t);
    }
    for (int i = 0; i < 1000 && ld(&c_ran) < 300; i++)
        Sleep(5);
    CHECK_EQ(ld(&c_ran), 300);
}

static DWORD exit_with(LPVOID p) { ExitThread((DWORD)(uintptr_t)p); }

TEST(conf_thread_exit_code_stillactive_and_zero)
{
    HANDLE t = test_CreateThread(NULL, 0, exit_with, (LPVOID)(uintptr_t)0, 0, NULL);
    DWORD code = 99;
    CHECK(signalled(t));
    CHECK(GetExitCodeThread(t, &code));
    CHECK_EQ(code, 0);
    CloseHandle(t);
    t = test_CreateThread(NULL, 0, exit_with, (LPVOID)(uintptr_t)0xFFFFFFFEu, 0, NULL);
    CHECK(signalled(t));
    CHECK(GetExitCodeThread(t, &code));
    CHECK_EQ(code, 0xFFFFFFFEu);
    CloseHandle(t);
}

TEST(conf_thread_priority_and_ideal_processor)
{
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD { return WaitForSingleObject(c_gate, WAIT_MAX); }, NULL,
                            CREATE_SUSPENDED, NULL);
    c_gate = CreateEventA(NULL, TRUE, FALSE, NULL);
    CHECK_EQ(GetThreadPriority(t), THREAD_PRIORITY_NORMAL);
    CHECK(SetThreadPriority(t, THREAD_PRIORITY_ABOVE_NORMAL));
    CHECK_EQ(GetThreadPriority(t), THREAD_PRIORITY_ABOVE_NORMAL);
    CHECK(SetThreadPriority(t, THREAD_PRIORITY_NORMAL));
    CHECK(SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_NORMAL));
    CHECK(!SetThreadPriority(NULL, THREAD_PRIORITY_NORMAL));
    DWORD prev = SetThreadIdealProcessor(t, 0);
    CHECK(prev != (DWORD)-1);                                      /* the previous ideal processor */
    CHECK_EQ(SetThreadIdealProcessor(t, 0), 0);                    /* set to 0 above */
    if (os_cpu_count() >= 2) {
        SetThreadIdealProcessor(t, 1);
        CHECK_EQ(SetThreadIdealProcessor(t, 0), 1);
    }
    CHECK(SetThreadIdealProcessor(GetCurrentThread(), 0) != (DWORD)-1);
    CHECK_EQ(SetThreadIdealProcessor(NULL, 0), (DWORD)-1);
    ResumeThread(t);
    SetEvent(c_gate);
    CHECK(signalled(t));
    CloseHandle(t);
    CloseHandle(c_gate);
}

/* ---- events */
TEST(conf_event_initial_state_and_modes)
{
    HANDLE a = CreateEventA(NULL, FALSE, TRUE, NULL);   /* auto-reset, initially signalled */
    CHECK(a != NULL);
    CHECK_EQ(WaitForSingleObject(a, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(a, 0), WAIT_TIMEOUT);  /* the wait reset it */
    HANDLE m = CreateEventA(NULL, TRUE, TRUE, NULL);    /* manual-reset, initially signalled */
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);
    CHECK(ResetEvent(m));
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_TIMEOUT);
    HANDLE n = CreateEventA(NULL, TRUE, FALSE, NULL);
    CHECK_EQ(WaitForSingleObject(n, 0), WAIT_TIMEOUT);
    CHECK(SetEvent(n) && SetEvent(n));                   /* setting twice is harmless */
    CHECK_EQ(WaitForSingleObject(n, 0), WAIT_OBJECT_0);
    CloseHandle(a);
    CloseHandle(m);
    CloseHandle(n);
}

TEST(conf_event_auto_reset_signal_persists_without_waiter)
{
    HANDLE e = CreateEventA(NULL, FALSE, FALSE, NULL);
    CHECK(SetEvent(e));
    CHECK(SetEvent(e));   /* two signals with no waiter collapse into one */
    Sleep(20);
    CHECK_EQ(WaitForSingleObject(e, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(e, 0), WAIT_TIMEOUT);
    /* a waiter that arrives later still sees the earlier signal, also through WaitForMultipleObjects */
    HANDLE s = CreateSemaphoreA(NULL, 0, 4, NULL);
    SetEvent(e);
    HANDLE hs[2] = { s, e };
    CHECK_EQ(WaitForMultipleObjects(2, hs, FALSE, 0), WAIT_OBJECT_0 + 1);
    CHECK_EQ(WaitForMultipleObjects(2, hs, FALSE, 0), WAIT_TIMEOUT);
    CloseHandle(s);
    CloseHandle(e);
}

TEST(conf_event_wrong_handle_type)
{
    HANDLE s = CreateSemaphoreA(NULL, 1, 1, NULL);
    CHECK(!SetEvent(s));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(!ResetEvent(s));
    CHECK(!SetEvent(NULL));
    CHECK(!ResetEvent(NULL));
    HANDLE e = CreateEventA(NULL, TRUE, FALSE, NULL);
    CHECK(!ReleaseSemaphore(e, 1, NULL));
    CHECK(!ReleaseMutex(e));
    CloseHandle(e);
    CloseHandle(s);
}

TEST(conf_event_named)
{
    SetLastError(0);
    HANDLE a = CreateEventA(NULL, TRUE, FALSE, "Local\\s4conf-event");
    CHECK(a != NULL);
    CHECK_EQ(GetLastError(), 0);
    HANDLE b = CreateEventA(NULL, FALSE, TRUE, "Local\\s4conf-event");   /* other arguments are ignored */
    CHECK(b != NULL && b != a);
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK_EQ(WaitForSingleObject(b, 0), WAIT_TIMEOUT);                    /* the original, unsignalled, manual */
    CHECK(SetEvent(a));
    CHECK_EQ(WaitForSingleObject(b, 0), WAIT_OBJECT_0);                   /* one object behind both handles */
    CHECK_EQ(WaitForSingleObject(b, 0), WAIT_OBJECT_0);                   /* manual: not consumed */
    HANDLE c = CreateEventA(NULL, TRUE, FALSE, "Local\\S4CONF-event");    /* names are case-sensitive */
    CHECK(c != NULL);
    CHECK_EQ(WaitForSingleObject(c, 0), WAIT_TIMEOUT);
    CloseHandle(c);
    CHECK(CloseHandle(a));
    CHECK_EQ(WaitForSingleObject(b, 0), WAIT_OBJECT_0);                   /* alive while one handle remains */
    CHECK(CloseHandle(b));
    HANDLE d = CreateEventA(NULL, TRUE, FALSE, "Local\\s4conf-event");    /* all handles closed: a fresh object */
    CHECK_EQ(GetLastError(), 0);
    CHECK_EQ(WaitForSingleObject(d, 0), WAIT_TIMEOUT);
    CloseHandle(d);
    /* one namespace for all kinds of objects: a different type under a used name fails */
    HANDLE ev = CreateEventA(NULL, TRUE, FALSE, "Local\\s4conf-kind");
    CHECK(CreateMutexA(NULL, FALSE, "Local\\s4conf-kind") == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(CreateSemaphoreA(NULL, 0, 1, "Local\\s4conf-kind") == NULL);
    CloseHandle(ev);
}

/* a waiter's handle is closed while it waits: the object stays alive and the wait completes */
static HANDLE c_h1;
TEST(conf_close_handle_while_waited)
{
    c_h1 = CreateEventA(NULL, TRUE, FALSE, "Local\\s4conf-alive");
    HANDLE h2 = CreateEventA(NULL, TRUE, FALSE, "Local\\s4conf-alive");
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD { return WaitForSingleObject(c_h1, WAIT_MAX); }, NULL, 0,
                            NULL);
    Sleep(60);
    CHECK(CloseHandle(c_h1));   /* the waiter holds the object, not just the handle value */
    Sleep(20);
    CHECK(SetEvent(h2));
    CHECK(signalled(t));
    DWORD code = 99;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, WAIT_OBJECT_0);
    CloseHandle(h2);
    CloseHandle(t);
}

TEST(conf_close_handle_errors)
{
    CHECK(!CloseHandle(NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(!CloseHandle((HANDLE)(uintptr_t)0x7fff0));
    HANDLE e = CreateEventA(NULL, TRUE, FALSE, NULL);
    CHECK(CloseHandle(e));
    CHECK(!CloseHandle(e));   /* double close */
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK_EQ(WaitForSingleObject(e, 0), WAIT_FAILED);   /* a closed handle cannot be waited on */
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(!SetEvent(e));
    CHECK(CloseHandle(GetCurrentThread()));             /* a pseudo handle: closing it succeeds and does nothing */
}

/* ---- waits */
TEST(conf_wait_finite_timeout_and_early_wake)
{
    HANDLE e = CreateEventA(NULL, FALSE, FALSE, NULL);
    uint64_t t0 = ms_now();
    CHECK_EQ(WaitForSingleObject(e, 80), WAIT_TIMEOUT);
    uint64_t dt = ms_now() - t0;
    CHECK(dt >= 75 && dt < 1500);
    HANDLE hs[2] = { e, CreateEventA(NULL, TRUE, FALSE, NULL) };
    t0 = ms_now();
    CHECK_EQ(WaitForMultipleObjects(2, hs, FALSE, 80), WAIT_TIMEOUT);
    dt = ms_now() - t0;
    CHECK(dt >= 75 && dt < 1500);
    t0 = ms_now();
    CHECK_EQ(WaitForMultipleObjects(2, hs, TRUE, 80), WAIT_TIMEOUT);
    dt = ms_now() - t0;
    CHECK(dt >= 75 && dt < 1500);
    /* signalled from another thread: the wait returns long before its timeout */
    c_gate = hs[1];
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD { Sleep(30); SetEvent(c_gate); return 0; }, NULL, 0, NULL);
    t0 = ms_now();
    CHECK_EQ(WaitForSingleObject(hs[1], 4000), WAIT_OBJECT_0);
    CHECK(ms_now() - t0 < 2000);
    signalled(t);
    CloseHandle(t);
    CloseHandle(hs[1]);
    CloseHandle(e);
}

TEST(conf_wait_zero_timeout_never_blocks)
{
    HANDLE m = CreateMutexA(NULL, FALSE, NULL);
    HANDLE s = CreateSemaphoreA(NULL, 0, 1, NULL);
    uint64_t t0 = ms_now();
    for (int i = 0; i < 100; i++) {
        CHECK_EQ(WaitForSingleObject(s, 0), WAIT_TIMEOUT);
        HANDLE hs[2] = { s, s };
        CHECK_EQ(WaitForMultipleObjects(2, hs, FALSE, 0), WAIT_TIMEOUT);
    }
    CHECK(ms_now() - t0 < 500);
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);
    CloseHandle(m);
    CloseHandle(s);
}

TEST(conf_wait_bad_arguments)
{
    HANDLE e = CreateEventA(NULL, TRUE, TRUE, NULL);
    HANDLE hs[MAXIMUM_WAIT_OBJECTS + 1];
    for (int i = 0; i < MAXIMUM_WAIT_OBJECTS + 1; i++)
        hs[i] = e;
    CHECK_EQ(WaitForMultipleObjects(0, hs, FALSE, 0), WAIT_FAILED);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(WaitForMultipleObjects(MAXIMUM_WAIT_OBJECTS + 1, hs, FALSE, 0), WAIT_FAILED);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(WaitForMultipleObjects(MAXIMUM_WAIT_OBJECTS, hs, FALSE, 0), WAIT_OBJECT_0);   /* 64 is the limit */
    CHECK_EQ(WaitForMultipleObjects(1, NULL, FALSE, 0), WAIT_FAILED);
    HANDLE bad[2] = { e, (HANDLE)(uintptr_t)0x7fff0 };
    CHECK_EQ(WaitForMultipleObjects(2, bad, FALSE, 0), WAIT_FAILED);   /* even though the first is signalled */
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK_EQ(WaitForMultipleObjects(2, bad, TRUE, 0), WAIT_FAILED);
    HANDLE dup[2] = { e, e };
    CHECK_EQ(WaitForMultipleObjects(2, dup, TRUE, 0), WAIT_FAILED);   /* duplicates are invalid for bWaitAll */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(WaitForMultipleObjects(2, dup, FALSE, 0), WAIT_OBJECT_0);
    CloseHandle(e);
}

TEST(conf_wait_any_lowest_index_and_consumption)
{
    HANDLE hs[3];
    for (int i = 0; i < 3; i++)
        hs[i] = CreateEventA(NULL, FALSE, TRUE, NULL);   /* all signalled, auto-reset */
    CHECK_EQ(WaitForMultipleObjects(3, hs, FALSE, 0), WAIT_OBJECT_0);       /* the lowest index */
    CHECK_EQ(WaitForMultipleObjects(3, hs, FALSE, 0), WAIT_OBJECT_0 + 1);   /* only that one was consumed */
    CHECK_EQ(WaitForMultipleObjects(3, hs, FALSE, 0), WAIT_OBJECT_0 + 2);
    CHECK_EQ(WaitForMultipleObjects(3, hs, FALSE, 0), WAIT_TIMEOUT);
    for (int i = 0; i < 3; i++)
        CloseHandle(hs[i]);
}

static HANDLE c_set[3];
TEST(conf_wait_any_wakes_on_any_object)
{
    for (int round = 0; round < 3; round++) {
        c_set[0] = CreateEventA(NULL, FALSE, FALSE, NULL);
        c_set[1] = CreateSemaphoreA(NULL, 0, 5, NULL);
        c_set[2] = CreateMutexA(NULL, TRUE, NULL);   /* owned by us, so unavailable to the waiter */
        HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD { return WaitForMultipleObjects(3, c_set, FALSE, WAIT_MAX); },
                                NULL, 0, NULL);
        Sleep(40);
        if (round == 0)
            SetEvent(c_set[0]);
        else if (round == 1)
            ReleaseSemaphore(c_set[1], 1, NULL);
        else
            ReleaseMutex(c_set[2]);
        CHECK(signalled(t));
        DWORD code = 99;
        GetExitCodeThread(t, &code);
        CHECK_EQ(code, WAIT_OBJECT_0 + round);
        CloseHandle(t);
        for (int i = 0; i < 3; i++)
            CloseHandle(c_set[i]);
    }
}

TEST(conf_wait_all_is_atomic)
{
    HANDLE ev = CreateEventA(NULL, FALSE, TRUE, NULL);       /* auto-reset, signalled */
    HANDLE sem = CreateSemaphoreA(NULL, 0, 4, NULL);         /* not signalled */
    HANDLE hs[2] = { ev, sem };
    CHECK_EQ(WaitForMultipleObjects(2, hs, TRUE, 30), WAIT_TIMEOUT);
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_OBJECT_0);     /* the event was not consumed by the failed wait-all */
    SetEvent(ev);
    ReleaseSemaphore(sem, 2, NULL);
    CHECK_EQ(WaitForMultipleObjects(2, hs, TRUE, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_TIMEOUT);      /* all of them were acquired: event reset ... */
    CHECK_EQ(WaitForSingleObject(sem, 0), WAIT_OBJECT_0);    /* ... semaphore decremented once (2 -> 1) */
    CHECK_EQ(WaitForSingleObject(sem, 0), WAIT_TIMEOUT);
    CloseHandle(ev);
    CloseHandle(sem);
}

static HANDLE c_all[2];
TEST(conf_wait_all_blocks_until_last_object)
{
    c_all[0] = CreateEventA(NULL, TRUE, FALSE, NULL);
    c_all[1] = CreateEventA(NULL, FALSE, FALSE, NULL);
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD { return WaitForMultipleObjects(2, c_all, TRUE, WAIT_MAX); },
                            NULL, 0, NULL);
    Sleep(40);
    SetEvent(c_all[0]);
    Sleep(40);
    CHECK_EQ(WaitForSingleObject(t, 0), WAIT_TIMEOUT);       /* one of two is not enough */
    SetEvent(c_all[1]);
    CHECK(signalled(t));
    DWORD code = 99;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(c_all[1], 0), WAIT_TIMEOUT);   /* the auto-reset one was consumed */
    CloseHandle(t);
    CloseHandle(c_all[0]);
    CloseHandle(c_all[1]);
}

TEST(conf_wait_all_with_thread_handles)
{
    HANDLE t[3];
    for (int i = 0; i < 3; i++)
        t[i] = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD { Sleep(20); return 0; }, NULL, 0, NULL);
    CHECK_EQ(WaitForMultipleObjects(3, t, TRUE, WAIT_MAX), WAIT_OBJECT_0);
    CHECK_EQ(WaitForMultipleObjects(3, t, FALSE, 0), WAIT_OBJECT_0);   /* handles stay signalled */
    for (int i = 0; i < 3; i++)
        CloseHandle(t[i]);
}

/* ---- semaphores */
TEST(conf_semaphore_counts_and_limits)
{
    CHECK(CreateSemaphoreA(NULL, 0, 0, NULL) == NULL);    /* maximum must be positive */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(CreateSemaphoreA(NULL, -1, 5, NULL) == NULL);   /* negative initial count */
    CHECK(CreateSemaphoreA(NULL, 3, 2, NULL) == NULL);    /* initial above maximum */
    HANDLE s = CreateSemaphoreA(NULL, 2, 3, NULL);
    CHECK(s != NULL);
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_OBJECT_0);   /* initial count 2: two waits succeed */
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_TIMEOUT);
    LONG prev = -7;
    CHECK(ReleaseSemaphore(s, 3, &prev));
    CHECK_EQ(prev, 0);                                    /* the count before the release */
    prev = -7;
    CHECK(!ReleaseSemaphore(s, 1, &prev));                /* 3 + 1 > 3 */
    CHECK_EQ(GetLastError(), ERROR_TOO_MANY_POSTS);
    CHECK_EQ(prev, -7);                                   /* untouched on failure */
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_OBJECT_0);   /* count 3 -> 2 */
    prev = -7;
    CHECK(ReleaseSemaphore(s, 1, &prev));
    CHECK_EQ(prev, 2);
    CHECK(!ReleaseSemaphore(s, 2, NULL));                 /* a release that would pass the maximum changes nothing */
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_TIMEOUT);    /* exactly 3 were left */
    CHECK(!ReleaseSemaphore(s, 0, NULL));                 /* a zero release count is invalid */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(!ReleaseSemaphore(s, -1, NULL));
    CHECK(!ReleaseSemaphore(NULL, 1, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CloseHandle(s);
}

static HANDLE c_sem;
static volatile LONG c_got;
TEST(conf_semaphore_wakes_exactly_count_waiters)
{
    c_sem = CreateSemaphoreA(NULL, 0, 10, NULL);
    c_got = 0;
    HANDLE t[4];
    for (int i = 0; i < 4; i++)
        t[i] = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
            if (WaitForSingleObject(c_sem, 700) == WAIT_OBJECT_0)
                InterlockedIncrement(&c_got);
            return 0;
        }, NULL, 0, NULL);
    Sleep(100);
    CHECK(ReleaseSemaphore(c_sem, 2, NULL));
    Sleep(250);
    CHECK_EQ(ld(&c_got), 2);   /* two units, two waiters */
    CHECK_EQ(WaitForMultipleObjects(4, t, TRUE, WAIT_MAX), WAIT_OBJECT_0);
    CHECK_EQ(ld(&c_got), 2);   /* the others timed out */
    for (int i = 0; i < 4; i++)
        CloseHandle(t[i]);
    CloseHandle(c_sem);
}

TEST(conf_semaphore_named)
{
    HANDLE a = CreateSemaphoreA(NULL, 1, 4, "Local\\s4conf-sem");
    HANDLE b = CreateSemaphoreA(NULL, 0, 9, "Local\\s4conf-sem");
    CHECK(a != NULL && b != NULL && a != b);
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK_EQ(WaitForSingleObject(b, 0), WAIT_OBJECT_0);   /* the count of the first creation */
    CHECK_EQ(WaitForSingleObject(a, 0), WAIT_TIMEOUT);
    CHECK(ReleaseSemaphore(b, 4, NULL));
    CHECK(!ReleaseSemaphore(a, 1, NULL));                 /* the maximum is 4, not 9 */
    CHECK_EQ(GetLastError(), ERROR_TOO_MANY_POSTS);
    CloseHandle(a);
    CloseHandle(b);
}

/* ---- mutexes */
static HANDLE c_mx;
static DWORD mx_try(LPVOID)
{
    DWORD r = WaitForSingleObject(c_mx, 0);
    if (r == WAIT_OBJECT_0 || r == WAIT_ABANDONED)
        ReleaseMutex(c_mx);
    return r;
}
static DWORD run_try(void)
{
    HANDLE t = test_CreateThread(NULL, 0, mx_try, NULL, 0, NULL);
    DWORD code = 99;
    signalled(t);
    GetExitCodeThread(t, &code);
    CloseHandle(t);
    return code;
}

TEST(conf_mutex_recursion_and_release_counts)
{
    c_mx = CreateMutexA(NULL, FALSE, NULL);
    CHECK_EQ(run_try(), WAIT_OBJECT_0);                   /* unowned: anybody can take it */
    CHECK_EQ(WaitForSingleObject(c_mx, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(c_mx, 0), WAIT_OBJECT_0);   /* recursive */
    CHECK_EQ(WaitForSingleObject(c_mx, 0), WAIT_OBJECT_0);
    CHECK_EQ(run_try(), WAIT_TIMEOUT);
    CHECK(ReleaseMutex(c_mx));
    CHECK(ReleaseMutex(c_mx));
    CHECK_EQ(run_try(), WAIT_TIMEOUT);                    /* still owned: 3 acquisitions, 2 releases */
    CHECK(ReleaseMutex(c_mx));
    CHECK_EQ(run_try(), WAIT_OBJECT_0);                   /* released as often as acquired */
    CHECK(!ReleaseMutex(c_mx));
    CHECK_EQ(GetLastError(), ERROR_NOT_OWNER);
    CHECK(!ReleaseMutex(NULL));
    CloseHandle(c_mx);
}

TEST(conf_mutex_initial_owner)
{
    c_mx = CreateMutexA(NULL, TRUE, NULL);
    CHECK_EQ(run_try(), WAIT_TIMEOUT);                    /* bInitialOwner: the creator owns it */
    CHECK(ReleaseMutex(c_mx));                            /* once */
    CHECK(!ReleaseMutex(c_mx));
    CHECK_EQ(run_try(), WAIT_OBJECT_0);
    CloseHandle(c_mx);
    /* a name that exists: bInitialOwner does not apply to the existing mutex */
    HANDLE a = CreateMutexA(NULL, FALSE, "Local\\s4conf-mx");
    HANDLE b = CreateMutexA(NULL, TRUE, "Local\\s4conf-mx");
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK(!ReleaseMutex(b));
    CHECK_EQ(GetLastError(), ERROR_NOT_OWNER);
    CHECK_EQ(WaitForSingleObject(a, 0), WAIT_OBJECT_0);
    CHECK(ReleaseMutex(b));                               /* ownership belongs to the thread, not the handle */
    CloseHandle(a);
    CloseHandle(b);
}

static DWORD mx_take_and_return(LPVOID)
{
    return WaitForSingleObject(c_mx, WAIT_MAX) == WAIT_OBJECT_0 ? 0 : 1;   /* returns owning the mutex */
}
static DWORD mx_take_and_exit(LPVOID)
{
    WaitForSingleObject(c_mx, WAIT_MAX);
    ExitThread(5);
}
static DWORD mx_create_owned_and_return(LPVOID)
{
    c_mx = CreateMutexA(NULL, TRUE, NULL);
    return 0;
}

TEST(conf_mutex_abandoned_by_exiting_owner)
{
    for (int mode = 0; mode < 3; mode++) {
        c_mx = mode == 2 ? NULL : CreateMutexA(NULL, FALSE, NULL);
        HANDLE t = test_CreateThread(NULL, 0, mode == 0 ? mx_take_and_return : mode == 1 ? mx_take_and_exit : mx_create_owned_and_return,
                                NULL, 0, NULL);
        CHECK(signalled(t));
        CloseHandle(t);
        CHECK(c_mx != NULL);
        CHECK_EQ(WaitForSingleObject(c_mx, 0), WAIT_ABANDONED);   /* the owner ended without releasing it */
        CHECK(ReleaseMutex(c_mx));                                /* we own it now */
        CHECK_EQ(WaitForSingleObject(c_mx, 0), WAIT_OBJECT_0);    /* abandonment is reported once */
        CHECK(ReleaseMutex(c_mx));
        CloseHandle(c_mx);
    }
}

static HANDLE c_mx2;
TEST(conf_mutex_abandoned_wakes_blocked_waiter)
{
    c_mx = CreateMutexA(NULL, FALSE, NULL);
    c_gate = CreateEventA(NULL, TRUE, FALSE, NULL);
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
        WaitForSingleObject(c_mx, WAIT_MAX);
        WaitForSingleObject(c_gate, WAIT_MAX);   /* holds the mutex until told to end */
        return 0;
    }, NULL, 0, NULL);
    Sleep(60);
    HANDLE w = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
        return WaitForSingleObject(c_mx, WAIT_MAX);   /* blocks: the first thread owns the mutex */
    }, NULL, 0, NULL);
    Sleep(60);
    CHECK_EQ(WaitForSingleObject(w, 0), WAIT_TIMEOUT);
    SetEvent(c_gate);                          /* the owner ends: the blocked waiter is woken with WAIT_ABANDONED */
    CHECK(signalled(w));
    DWORD code = 0;
    GetExitCodeThread(w, &code);
    CHECK_EQ(code, WAIT_ABANDONED);
    CloseHandle(w);
    CloseHandle(t);
    CloseHandle(c_gate);
    CloseHandle(c_mx);
    /* WaitForMultipleObjects: the index of the abandoned mutex, offset from WAIT_ABANDONED_0 */
    c_mx = CreateMutexA(NULL, FALSE, NULL);
    c_mx2 = CreateEventA(NULL, TRUE, FALSE, NULL);
    t = test_CreateThread(NULL, 0, mx_take_and_return, NULL, 0, NULL);
    CHECK(signalled(t));
    CloseHandle(t);
    HANDLE hs[2] = { c_mx2, c_mx };
    CHECK_EQ(WaitForMultipleObjects(2, hs, FALSE, 0), WAIT_ABANDONED + 1);
    ReleaseMutex(c_mx);
    t = test_CreateThread(NULL, 0, mx_take_and_return, NULL, 0, NULL);
    CHECK(signalled(t));
    CloseHandle(t);
    SetEvent(c_mx2);
    CHECK_EQ(WaitForMultipleObjects(2, hs, TRUE, 0), WAIT_ABANDONED + 1);   /* wait-all: index of the abandoned member */
    CHECK(ReleaseMutex(c_mx));
    CloseHandle(c_mx);
    CloseHandle(c_mx2);
}

TEST(conf_mutex_wait_all_with_event)
{
    c_mx = CreateMutexA(NULL, FALSE, NULL);
    HANDLE ev = CreateEventA(NULL, FALSE, FALSE, NULL);
    HANDLE hs[2] = { c_mx, ev };
    CHECK_EQ(WaitForMultipleObjects(2, hs, TRUE, 20), WAIT_TIMEOUT);
    CHECK(!ReleaseMutex(c_mx));                       /* the failed wait-all took nothing */
    SetEvent(ev);
    CHECK_EQ(WaitForMultipleObjects(2, hs, TRUE, 0), WAIT_OBJECT_0);
    CHECK(ReleaseMutex(c_mx));                        /* the mutex is ours now */
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_TIMEOUT);
    CloseHandle(c_mx);
    CloseHandle(ev);
}

/* ---- critical sections */
static CRITICAL_SECTION c_cs;
static HANDLE c_have, c_go;
TEST(conf_critical_section_blocks_and_hands_off)
{
    InitializeCriticalSection(&c_cs);
    c_have = CreateEventA(NULL, FALSE, FALSE, NULL);
    c_go = CreateEventA(NULL, FALSE, FALSE, NULL);
    c_ran = 0;
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
        EnterCriticalSection(&c_cs);
        SetEvent(c_have);
        WaitForSingleObject(c_go, WAIT_MAX);
        LeaveCriticalSection(&c_cs);
        return 0;
    }, NULL, 0, NULL);
    CHECK(signalled(c_have));
    CHECK(!TryEnterCriticalSection(&c_cs));       /* held by the other thread */
    HANDLE u = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
        EnterCriticalSection(&c_cs);              /* blocks until the first thread leaves */
        InterlockedExchange(&c_ran, 1);
        LeaveCriticalSection(&c_cs);
        return 0;
    }, NULL, 0, NULL);
    Sleep(60);
    CHECK_EQ(ld(&c_ran), 0);
    SetEvent(c_go);
    CHECK(signalled(u));
    CHECK_EQ(ld(&c_ran), 1);
    CHECK(signalled(t));
    CHECK(TryEnterCriticalSection(&c_cs));
    CHECK_EQ(c_cs.RecursionCount, 1);
    LeaveCriticalSection(&c_cs);
    DeleteCriticalSection(&c_cs);
    CloseHandle(t);
    CloseHandle(u);
    CloseHandle(c_have);
    CloseHandle(c_go);
}

TEST(conf_critical_section_spin_count_and_recursion)
{
    CRITICAL_SECTION cs;
    CHECK(InitializeCriticalSectionAndSpinCount(&cs, 4000));
    for (int i = 1; i <= 5; i++) {
        EnterCriticalSection(&cs);
        CHECK_EQ(cs.RecursionCount, i);
    }
    for (int i = 4; i >= 0; i--) {
        LeaveCriticalSection(&cs);
        CHECK_EQ(cs.RecursionCount, i);
    }
    CHECK(cs.OwningThread == NULL);
    CHECK(TryEnterCriticalSection(&cs));
    CHECK(TryEnterCriticalSection(&cs));
    LeaveCriticalSection(&cs);
    LeaveCriticalSection(&cs);
    DeleteCriticalSection(&cs);
}

/* ---- Interlocked */
TEST(conf_interlocked_values)
{
    volatile LONG v = (LONG)0x7fffffff;
    CHECK_EQ(InterlockedIncrement(&v), (LONG)0x80000000);   /* wraps like the x86 instruction; returns the new value */
    CHECK_EQ(InterlockedDecrement(&v), (LONG)0x7fffffff);
    v = 10;
    CHECK_EQ(InterlockedExchangeAdd(&v, -4), 10);            /* returns the old value */
    CHECK_EQ(v, 6);
    CHECK_EQ(InterlockedExchange(&v, -1), 6);
    CHECK_EQ(v, -1);
    CHECK_EQ(InterlockedCompareExchange(&v, 5, -1), -1);     /* match: stored, returns the old value */
    CHECK_EQ(v, 5);
    CHECK_EQ(InterlockedCompareExchange(&v, 9, 4), 5);       /* no match: untouched, returns the current value */
    CHECK_EQ(v, 5);
    CHECK_EQ(InterlockedIncrement(&v), 6);
    CHECK_EQ(InterlockedDecrement(&v), 5);
    CHECK_EQ(InterlockedDecrement(&v), 4);
}

static volatile LONG c_spin, c_inside, c_violations, c_total;
TEST(conf_interlocked_cas_spinlock)
{
    c_spin = c_inside = c_violations = c_total = 0;
    HANDLE t[4];
    for (int i = 0; i < 4; i++)
        t[i] = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
            for (int n = 0; n < 20000; n++) {
                while (InterlockedCompareExchange(&c_spin, 1, 0) != 0)
                    SwitchToThread();
                if (InterlockedIncrement(&c_inside) != 1)
                    InterlockedIncrement(&c_violations);
                InterlockedDecrement(&c_inside);
                InterlockedIncrement(&c_total);
                InterlockedExchange(&c_spin, 0);
            }
            return 0;
        }, NULL, 0, NULL);
    CHECK_EQ(WaitForMultipleObjects(4, t, TRUE, 30000), WAIT_OBJECT_0);
    CHECK_EQ(ld(&c_violations), 0);
    CHECK_EQ(ld(&c_total), 80000);
    for (int i = 0; i < 4; i++)
        CloseHandle(t[i]);
}

/* ---- TLS */
static DWORD c_slot;
TEST(conf_tls_semantics)
{
    c_slot = TlsAlloc();
    CHECK(c_slot != 0xFFFFFFFFu);
    DWORD s2 = TlsAlloc();
    CHECK(s2 != c_slot && s2 != 0xFFFFFFFFu);
    SetLastError(1234);
    CHECK(TlsGetValue(c_slot) == NULL);                    /* a new slot reads as 0 ... */
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);               /* ... and success clears the last error */
    CHECK(TlsSetValue(c_slot, (LPVOID)0x1234));
    SetLastError(1234);
    CHECK(TlsGetValue(c_slot) == (LPVOID)0x1234);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    CHECK(TlsGetValue(s2) == NULL);                        /* slots are independent */
    c_ran = 0;
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
        InterlockedExchange(&c_ran, TlsGetValue(c_slot) == NULL ? 1 : 2);   /* another thread starts with 0 */
        TlsSetValue(c_slot, (LPVOID)0x9999);
        return TlsGetValue(c_slot) == (LPVOID)0x9999 ? 0 : 1;
    }, NULL, 0, NULL);
    CHECK(signalled(t));
    DWORD code = 99;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 0);
    CHECK_EQ(ld(&c_ran), 1);
    CHECK(TlsGetValue(c_slot) == (LPVOID)0x1234);          /* the other thread's store did not leak here */
    CloseHandle(t);
    CHECK(TlsGetValue(0xFFFFFFFFu) == NULL);               /* an invalid index */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(!TlsSetValue(0xFFFFFFFFu, NULL));
    CHECK(TlsFree(c_slot));
    CHECK(!TlsFree(c_slot));                               /* not allocated any more */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(!TlsFree(0xFFFFFFFFu));
    DWORD again = TlsAlloc();                              /* the lowest free slot is handed out again, zeroed */
    CHECK_EQ(again, c_slot);
    CHECK(TlsGetValue(again) == NULL);
    CHECK(TlsFree(again));
    CHECK(TlsFree(s2));
}

/* ---- Sleep, SwitchToThread */
TEST(conf_sleep_and_switch)
{
    uint64_t t0 = ms_now();
    for (int i = 0; i < 200; i++)
        Sleep(0);
    CHECK(ms_now() - t0 < 1000);   /* Sleep(0) yields, it does not sleep */
    t0 = ms_now();
    Sleep(60);
    uint64_t dt = ms_now() - t0;
    CHECK(dt >= 55 && dt < 1500);
    for (int i = 0; i < 100; i++)
        SwitchToThread();          /* TRUE or FALSE (nothing else to run) are both valid */
    CHECK(true);
}

/* ---- deadlock hunting: handshakes whose lost wakeup would hang */
static HANDLE pp_a, pp_b;
static volatile LONG pp_fail;
TEST(conf_stress_auto_reset_ping_pong)
{
    pp_a = CreateEventA(NULL, FALSE, FALSE, NULL);
    pp_b = CreateEventA(NULL, FALSE, FALSE, NULL);
    pp_fail = 0;
    HANDLE t = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
        for (int i = 0; i < 3000; i++) {
            if (WaitForSingleObject(pp_a, WAIT_MAX) != WAIT_OBJECT_0) {
                InterlockedIncrement(&pp_fail);
                return 1;
            }
            SetEvent(pp_b);
        }
        return 0;
    }, NULL, 0, NULL);
    for (int i = 0; i < 3000; i++) {
        SetEvent(pp_a);
        if (WaitForSingleObject(pp_b, WAIT_MAX) != WAIT_OBJECT_0) {
            InterlockedIncrement(&pp_fail);
            break;
        }
    }
    CHECK(signalled(t));
    CHECK_EQ(ld(&pp_fail), 0);
    CloseHandle(t);
    CloseHandle(pp_a);
    CloseHandle(pp_b);
}

static HANDLE q_stop, q_sem;
static volatile LONG q_done;
TEST(conf_stress_work_queue_stop_and_semaphore)
{
    q_stop = CreateEventA(NULL, TRUE, FALSE, NULL);
    q_sem = CreateSemaphoreA(NULL, 0, 100000, NULL);
    q_done = 0;
    HANDLE t[3];
    for (int i = 0; i < 3; i++)
        t[i] = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
            HANDLE hs[2] = { q_stop, q_sem };   /* stop first: it wins when both are signalled */
            for (;;) {
                DWORD r = WaitForMultipleObjects(2, hs, FALSE, WAIT_MAX);
                if (r == WAIT_OBJECT_0 + 1)
                    InterlockedIncrement(&q_done);
                else
                    return r == WAIT_OBJECT_0 ? 0 : 1;
            }
        }, NULL, 0, NULL);
    for (int burst = 0; burst < 100; burst++) {
        ReleaseSemaphore(q_sem, 10, NULL);
        if (burst % 7 == 0)
            Sleep(1);
    }
    for (int i = 0; i < 2000 && ld(&q_done) < 1000; i++)
        Sleep(2);
    CHECK_EQ(ld(&q_done), 1000);
    SetEvent(q_stop);
    CHECK_EQ(WaitForMultipleObjects(3, t, TRUE, WAIT_MAX), WAIT_OBJECT_0);
    for (int i = 0; i < 3; i++) {
        DWORD code = 99;
        GetExitCodeThread(t[i], &code);
        CHECK_EQ(code, 0);
        CloseHandle(t[i]);
    }
    CloseHandle(q_stop);
    CloseHandle(q_sem);
}

static HANDLE m_mx;
static volatile LONG m_count;
TEST(conf_stress_mutex_exclusion)
{
    m_mx = CreateMutexA(NULL, FALSE, NULL);
    m_count = 0;
    HANDLE t[4];
    static long plain;
    plain = 0;
    for (int i = 0; i < 4; i++)
        t[i] = test_CreateThread(NULL, 0, [](LPVOID) -> DWORD {
            for (int n = 0; n < 5000; n++) {
                if (WaitForSingleObject(m_mx, WAIT_MAX) != WAIT_OBJECT_0)
                    return 1;
                WaitForSingleObject(m_mx, 0);   /* recursive */
                long v = plain;
                plain = v + 1;
                ReleaseMutex(m_mx);
                ReleaseMutex(m_mx);
            }
            return 0;
        }, NULL, 0, NULL);
    CHECK_EQ(WaitForMultipleObjects(4, t, TRUE, 30000), WAIT_OBJECT_0);
    CHECK_EQ(plain, 20000);
    for (int i = 0; i < 4; i++)
        CloseHandle(t[i]);
    CloseHandle(m_mx);
}
