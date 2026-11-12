/* test_sync_contract.cpp - the synchronization imports on their documented argument domain: results and GetLastError
 * (platform/win32/sync.cpp). Every blocking expectation has a bounded timeout. */
#include "s4_test.h"

#ifndef ERROR_NOT_OWNER
#define ERROR_NOT_OWNER 288
#endif
#ifndef ERROR_TOO_MANY_POSTS
#define ERROR_TOO_MANY_POSTS 298
#endif
#ifndef ERROR_ALREADY_EXISTS
#define ERROR_ALREADY_EXISTS 183
#endif
#ifndef ERROR_INVALID_HANDLE
#define ERROR_INVALID_HANDLE 6
#endif
#ifndef ERROR_INVALID_PARAMETER
#define ERROR_INVALID_PARAMETER 87
#endif

static DWORD wait_err(DWORD r, DWORD *err) { *err = GetLastError(); return r; }

TEST(sync_wait_invalid_and_nonwaitable)
{
    DWORD e;
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForSingleObject((HANDLE)0x7fff0, 10), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForSingleObject(NULL, 10), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_HANDLE);
    /* INVALID_HANDLE_VALUE is (HANDLE)-1, the same value as GetCurrentProcess(): waitable, below */

    HANDLE heap = HeapCreate(0, 0, 0);
    CHECK(heap != NULL);
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForSingleObject(heap, 10), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_HANDLE);
    HeapDestroy(heap);

    /* a non-waitable handle in a set fails the whole wait, even next to a signalled event */
    HANDLE ev = CreateEventA(NULL, TRUE, TRUE, NULL);
    HANDLE hs[2] = { ev, (HANDLE)0x7fff0 };
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForMultipleObjects(2, hs, FALSE, 10), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_HANDLE);
    CloseHandle(ev);
    /* a closed handle is invalid */
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForSingleObject(ev, 10), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_HANDLE);

    /* the process pseudo handle is waitable and not signalled while the process runs */
    CHECK_EQ(WaitForSingleObject(GetCurrentProcess(), 20), WAIT_TIMEOUT);
    CHECK_EQ(WaitForSingleObject(GetCurrentProcess(), 0), WAIT_TIMEOUT);
}

TEST(sync_wait_find_handle_not_waitable)
{
    WIN32_FIND_DATAA fd;
    HANDLE f = FindFirstFileA("C:\\*", &fd);
    if (f == INVALID_HANDLE_VALUE)
        return;   /* no such directory in this root: nothing to check */
    DWORD e;
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForSingleObject(f, 10), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_HANDLE);
    FindClose(f);
}

TEST(sync_wait_multiple_params)
{
    HANDLE a = CreateEventA(NULL, TRUE, TRUE, NULL);
    HANDLE b = CreateEventA(NULL, TRUE, FALSE, NULL);
    DWORD e;
    HANDLE hs[65];
    for (int i = 0; i < 65; i++)
        hs[i] = a;
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForMultipleObjects(0, hs, FALSE, 0), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForMultipleObjects(65, hs, FALSE, 0), &e), WAIT_FAILED);
    CHECK_EQ(e, ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK_EQ(wait_err(WaitForMultipleObjects(2, hs, TRUE, 0), &e), WAIT_FAILED);   /* same handle twice, wait-all */
    CHECK_EQ(e, ERROR_INVALID_PARAMETER);
    CHECK_EQ(WaitForMultipleObjects(2, hs, FALSE, 0), WAIT_OBJECT_0);              /* wait-any allows it */
    /* lowest signalled index */
    HANDLE ab[3] = { b, a, a };
    CHECK_EQ(WaitForMultipleObjects(3, ab, FALSE, 0), WAIT_OBJECT_0 + 1);
    /* 64 is the maximum */
    HANDLE many[64];
    for (int i = 0; i < 64; i++)
        many[i] = b;
    many[63] = a;
    CHECK_EQ(WaitForMultipleObjects(64, many, FALSE, 0), WAIT_OBJECT_0 + 63);
    CHECK_EQ(WaitForMultipleObjects(2, ab, TRUE, 0), WAIT_TIMEOUT);
    CHECK_EQ(WaitForMultipleObjects(2, ab + 1, TRUE, 0) == WAIT_FAILED, 1);        /* a, a: duplicate */
    CloseHandle(a);
    CloseHandle(b);
}

TEST(sync_wait_all_no_partial_acquisition)
{
    HANDLE sem = CreateSemaphoreA(NULL, 1, 1, NULL);
    HANDLE aev = CreateEventA(NULL, FALSE, TRUE, NULL);   /* auto-reset, signalled */
    HANDLE mtx = CreateMutexA(NULL, FALSE, NULL);
    HANDLE never = CreateEventA(NULL, TRUE, FALSE, NULL);
    HANDLE hs[4] = { sem, aev, mtx, never };
    CHECK_EQ(WaitForMultipleObjects(4, hs, TRUE, 20), WAIT_TIMEOUT);
    /* nothing was taken */
    CHECK_EQ(WaitForSingleObject(sem, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(aev, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(aev, 0), WAIT_TIMEOUT);   /* auto-reset consumed once */
    CHECK(ReleaseSemaphore(sem, 1, NULL));
    SetEvent(aev);
    SetEvent(never);
    CHECK_EQ(WaitForMultipleObjects(4, hs, TRUE, 100), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(aev, 0), WAIT_TIMEOUT);   /* acquired by the wait-all */
    CHECK_EQ(WaitForSingleObject(sem, 0), WAIT_TIMEOUT);
    CHECK(ReleaseMutex(mtx));
    CloseHandle(sem); CloseHandle(aev); CloseHandle(mtx); CloseHandle(never);
}

static DWORD exit_holding(LPVOID m)
{
    WaitForSingleObject((HANDLE)m, 1000);
    return 0;   /* never releases */
}

TEST(sync_abandoned_mutex)
{
    HANDLE m = CreateMutexA(NULL, FALSE, NULL);
    HANDLE ev = CreateEventA(NULL, TRUE, FALSE, NULL);
    HANDLE t = test_CreateThread(NULL, 0, exit_holding, m, 0, NULL);
    CHECK_EQ(WaitForSingleObject(t, 5000), WAIT_OBJECT_0);   /* thread handle: signalled when exited */
    HANDLE hs[2] = { ev, m };
    CHECK_EQ(WaitForMultipleObjects(2, hs, FALSE, 1000), WAIT_ABANDONED + 1);
    CHECK(ReleaseMutex(m));
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);       /* abandoned state reported once */
    CHECK(ReleaseMutex(m));
    CloseHandle(t); CloseHandle(m); CloseHandle(ev);
}

TEST(sync_file_handle_waitable)
{
    s4_write_file("C:\\sync_contract.tmp", "x");
    HANDLE f = CreateFileA("C:\\sync_contract.tmp", GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(f != INVALID_HANDLE_VALUE);
    CHECK_EQ(WaitForSingleObject(f, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(f, 0), WAIT_OBJECT_0);
    CloseHandle(f);
}

static HANDLE g_ev;
static volatile int g_woken;
static DWORD waiter(LPVOID)
{
    if (WaitForSingleObject(g_ev, 3000) == WAIT_OBJECT_0)
        __atomic_add_fetch(&g_woken, 1, __ATOMIC_SEQ_CST);
    return 0;
}

TEST(sync_event_auto_releases_one)
{
    g_ev = CreateEventA(NULL, FALSE, FALSE, NULL);
    g_woken = 0;
    HANDLE t[3];
    for (int i = 0; i < 3; i++)
        t[i] = test_CreateThread(NULL, 0, waiter, NULL, 0, NULL);
    Sleep(100);
    SetEvent(g_ev);
    Sleep(150);
    CHECK_EQ(g_woken, 1);
    SetEvent(g_ev);
    SetEvent(g_ev);
    for (int i = 0; i < 3; i++) {
        WaitForSingleObject(t[i], 5000);
        CloseHandle(t[i]);
    }
    CHECK(g_woken >= 2);
    CloseHandle(g_ev);
    /* manual reset: stays set, ResetEvent clears it */
    HANDLE m = CreateEventA(NULL, TRUE, TRUE, NULL);
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);
    CHECK(ResetEvent(m));
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_TIMEOUT);
    CloseHandle(m);
    SetLastError(0);
    CHECK(!SetEvent((HANDLE)0x7fff0));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(!ResetEvent((HANDLE)0x7fff0));
}

TEST(sync_named_objects)
{
    SetLastError(99);
    HANDLE e1 = CreateEventA(NULL, TRUE, FALSE, "sync_contract_obj");
    CHECK(e1 != NULL);
    CHECK_EQ(GetLastError(), 0);
    HANDLE e2 = CreateEventA(NULL, TRUE, FALSE, "sync_contract_obj");
    CHECK(e2 != NULL && e2 != e1);
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    SetEvent(e1);
    CHECK_EQ(WaitForSingleObject(e2, 0), WAIT_OBJECT_0);   /* the same object */
    SetLastError(0);
    CHECK(CreateMutexA(NULL, FALSE, "sync_contract_obj") == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK(CreateSemaphoreA(NULL, 0, 1, "sync_contract_obj") == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    HANDLE e3 = CreateEventA(NULL, TRUE, FALSE, "SYNC_contract_obj");   /* case-sensitive */
    CHECK(e3 != NULL);
    CHECK_EQ(GetLastError(), 0);
    CHECK_EQ(WaitForSingleObject(e3, 0), WAIT_TIMEOUT);
    /* the object lives while any handle does; the name dies with the last */
    CloseHandle(e1);
    CHECK_EQ(WaitForSingleObject(e2, 0), WAIT_OBJECT_0);
    CloseHandle(e2);
    HANDLE m = CreateMutexA(NULL, FALSE, "sync_contract_obj");
    CHECK(m != NULL);
    CHECK_EQ(GetLastError(), 0);
    HANDLE m2 = CreateMutexA(NULL, TRUE, "sync_contract_obj");
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);   /* bInitialOwner ignored for an existing mutex */
    CHECK(ReleaseMutex(m));
    CloseHandle(m); CloseHandle(m2); CloseHandle(e3);
    HANDLE s = CreateSemaphoreA(NULL, 1, 2, "sync_contract_sem");
    HANDLE s2 = CreateSemaphoreA(NULL, 0, 5, "sync_contract_sem");
    CHECK_EQ(GetLastError(), ERROR_ALREADY_EXISTS);
    CHECK_EQ(WaitForSingleObject(s2, 0), WAIT_OBJECT_0);
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_TIMEOUT);
    CloseHandle(s); CloseHandle(s2);
}

static DWORD release_other(LPVOID m)
{
    SetLastError(0);
    return (!ReleaseMutex((HANDLE)m) && GetLastError() == ERROR_NOT_OWNER) ? 1 : 0;
}

TEST(sync_mutex_semantics)
{
    HANDLE m = CreateMutexA(NULL, TRUE, NULL);
    CHECK(m != NULL);
    CHECK_EQ(WaitForSingleObject(m, 0), WAIT_OBJECT_0);   /* recursive: count 2 */
    HANDLE t = test_CreateThread(NULL, 0, release_other, m, 0, NULL);
    CHECK_EQ(WaitForSingleObject(t, 5000), WAIT_OBJECT_0);
    DWORD code = 0;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 1);
    CloseHandle(t);
    CHECK(ReleaseMutex(m));
    CHECK(ReleaseMutex(m));
    SetLastError(0);
    CHECK(!ReleaseMutex(m));                              /* unowned */
    CHECK_EQ(GetLastError(), ERROR_NOT_OWNER);
    SetLastError(0);
    CHECK(!ReleaseMutex((HANDLE)0x7fff0));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CloseHandle(m);
}

TEST(sync_semaphore_semantics)
{
    SetLastError(0);
    CHECK(CreateSemaphoreA(NULL, -1, 5, NULL) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(CreateSemaphoreA(NULL, 0, 0, NULL) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(CreateSemaphoreA(NULL, 0, -3, NULL) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(CreateSemaphoreA(NULL, 3, 2, NULL) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    HANDLE s = CreateSemaphoreA(NULL, 1, 3, NULL);
    CHECK(s != NULL);
    LONG prev = -7;
    CHECK(ReleaseSemaphore(s, 2, &prev));
    CHECK_EQ(prev, 1);
    SetLastError(0);
    prev = -7;
    CHECK(!ReleaseSemaphore(s, 1, &prev));
    CHECK_EQ(GetLastError(), ERROR_TOO_MANY_POSTS);
    CHECK_EQ(prev, -7);
    SetLastError(0);
    CHECK(!ReleaseSemaphore(s, 0, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(!ReleaseSemaphore(s, -1, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    for (int i = 0; i < 3; i++)
        CHECK_EQ(WaitForSingleObject(s, 0), WAIT_OBJECT_0);   /* count was unchanged by the failures: 3 */
    CHECK_EQ(WaitForSingleObject(s, 0), WAIT_TIMEOUT);
    CHECK(ReleaseSemaphore(s, 3, NULL));
    SetLastError(0);
    CHECK(!ReleaseSemaphore((HANDLE)0x7fff0, 1, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CloseHandle(s);
}

static CRITICAL_SECTION g_cs;
static DWORD try_other(LPVOID)
{
    BOOL got = TryEnterCriticalSection(&g_cs);
    if (got)
        LeaveCriticalSection(&g_cs);
    return got ? 1 : 0;
}

TEST(sync_critical_section)
{
    InitializeCriticalSection(&g_cs);
    EnterCriticalSection(&g_cs);
    EnterCriticalSection(&g_cs);
    CHECK(TryEnterCriticalSection(&g_cs));
    HANDLE t = test_CreateThread(NULL, 0, try_other, NULL, 0, NULL);
    CHECK_EQ(WaitForSingleObject(t, 5000), WAIT_OBJECT_0);
    DWORD code = 9;
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 0);                      /* owned by us: the other thread does not get it, and does not block */
    CloseHandle(t);
    LeaveCriticalSection(&g_cs);
    LeaveCriticalSection(&g_cs);
    LeaveCriticalSection(&g_cs);
    t = test_CreateThread(NULL, 0, try_other, NULL, 0, NULL);
    CHECK_EQ(WaitForSingleObject(t, 5000), WAIT_OBJECT_0);
    GetExitCodeThread(t, &code);
    CHECK_EQ(code, 1);                      /* free again */
    CloseHandle(t);
    DeleteCriticalSection(&g_cs);
    CHECK(InitializeCriticalSectionAndSpinCount(&g_cs, 4000));
    CHECK(TryEnterCriticalSection(&g_cs));
    LeaveCriticalSection(&g_cs);
    CHECK(InitializeCriticalSectionAndSpinCount(&g_cs, 0x80000000u));
    DeleteCriticalSection(&g_cs);
    CHECK_EQ(sizeof(CRITICAL_SECTION), 24 * (sizeof(void *) / 4 == 2 ? 2 : 1));
}

TEST(sync_sleep_and_timeouts)
{
    Sleep(0);
    DWORD t0 = GetTickCount();
    Sleep(30);
    CHECK(GetTickCount() - t0 >= 25);
    HANDLE ev = CreateEventA(NULL, TRUE, FALSE, NULL);
    t0 = GetTickCount();
    CHECK_EQ(WaitForSingleObject(ev, 40), WAIT_TIMEOUT);
    CHECK(GetTickCount() - t0 >= 35);
    CHECK_EQ(WaitForSingleObject(ev, 0), WAIT_TIMEOUT);
    CloseHandle(ev);
}

static DWORD inf_sleeper(LPVOID flag)
{
    Sleep(INFINITE);
    *(volatile int *)flag = 1;
    return 0;
}
static DWORD inf_waiter(LPVOID flag)
{
    HANDLE ev = CreateEventA(NULL, TRUE, FALSE, NULL);
    WaitForSingleObject(ev, INFINITE);
    *(volatile int *)flag = 1;
    return 0;
}

TEST(sync_infinite_blocks)
{
    static volatile int f1, f2;
    HANDLE a = test_CreateThread(NULL, 0, inf_sleeper, (void *)&f1, 0, NULL);
    HANDLE b = test_CreateThread(NULL, 0, inf_waiter, (void *)&f2, 0, NULL);
    CHECK_EQ(WaitForSingleObject(a, 100), WAIT_TIMEOUT);
    CHECK_EQ(WaitForSingleObject(b, 100), WAIT_TIMEOUT);
    CHECK_EQ(f1, 0);
    CHECK_EQ(f2, 0);
    /* the threads stay parked until the process ends */
    CloseHandle(a);
    CloseHandle(b);
}
