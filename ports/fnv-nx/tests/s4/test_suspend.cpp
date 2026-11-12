/* test_suspend.cpp - SuspendThread / ResumeThread of running threads (platform/win32/thread.cpp over the os layer's
 * pause: os_posix's SIGUSR2 wait here, svcSetThreadActivity on the device), and a thread suspending itself. */
#include "s4_test.h"

#include <unistd.h>

static volatile long g_counter;
static volatile int g_stop;

static DWORD spin(LPVOID)
{
    while (!g_stop)
        __atomic_add_fetch(&g_counter, 1, __ATOMIC_RELAXED);
    return 7;
}

static volatile int g_self_state;
static DWORD self_suspender(LPVOID)
{
    g_self_state = 1;
    DWORD prev = SuspendThread(GetCurrentThread());   /* blocks until another thread resumes it */
    g_self_state = prev == 0 ? 2 : 3;
    return 0;
}

TEST(thread_suspend_running)
{
    g_counter = 0;
    g_stop = 0;
    HANDLE h = test_CreateThread(NULL, 0, spin, NULL, 0, NULL);
    CHECK(h != NULL);
    while (g_counter < 1000)
        Sleep(0);
    CHECK_EQ(SuspendThread(h), 0);
    CHECK_EQ(SuspendThread(h), 1);   /* counts nest */
    long a = g_counter;
    Sleep(30);
    long b = g_counter;
    CHECK_EQ(a, b);                  /* paused: no progress */
    CHECK_EQ(ResumeThread(h), 2);
    Sleep(10);
    CHECK_EQ(g_counter, b);          /* still suspended once */
    CHECK_EQ(ResumeThread(h), 1);
    for (int i = 0; i < 200 && g_counter == b; i++)
        Sleep(1);
    CHECK(g_counter > b);            /* runs again */
    CHECK_EQ(ResumeThread(h), 0);    /* not suspended: 0, nothing happens */
    g_stop = 1;
    CHECK_EQ(WaitForSingleObject(h, 5000), WAIT_OBJECT_0);
    DWORD code = 0;
    CHECK(GetExitCodeThread(h, &code) && code == 7);
    CloseHandle(h);
}

TEST(thread_suspend_self_and_created_suspended)
{
    g_self_state = 0;
    HANDLE h = test_CreateThread(NULL, 0, self_suspender, NULL, 0, NULL);
    while (g_self_state == 0)
        Sleep(1);
    Sleep(20);
    CHECK_EQ(g_self_state, 1);        /* still inside SuspendThread */
    CHECK_EQ(ResumeThread(h), 1);
    CHECK_EQ(WaitForSingleObject(h, 5000), WAIT_OBJECT_0);
    CHECK_EQ(g_self_state, 2);
    CloseHandle(h);

    g_counter = 0;
    g_stop = 0;
    HANDLE s = test_CreateThread(NULL, 0, spin, NULL, CREATE_SUSPENDED, NULL);
    Sleep(10);
    CHECK_EQ(g_counter, 0);
    CHECK_EQ(SuspendThread(s), 1);    /* created suspended: count 1 -> 2 */
    CHECK_EQ(ResumeThread(s), 2);
    CHECK_EQ(ResumeThread(s), 1);     /* starts it */
    for (int i = 0; i < 200 && !g_counter; i++)
        Sleep(1);
    CHECK(g_counter > 0);
    g_stop = 1;
    CHECK_EQ(WaitForSingleObject(s, 5000), WAIT_OBJECT_0);
    CloseHandle(s);
}
