/* test_worldstop.cpp - fnvl_host_world_stop / fnvl_host_world_resume (platform/os/os_world.c: contract and proof).
 * Rule of every test here: nothing between stop and resume calls the platform or libc (no CHECK, no printf, no Sleep):
 * the window only reads or writes plain variables and spins; results are checked after resume. */
#include "s4_test.h"

#include "fnvl_host.h"

#define NW 4
struct alignas(64) padded { volatile unsigned long v; };
static padded g_cnt[NW];
static volatile int g_quit;

static DWORD counter_thread(LPVOID p)
{
    padded *c = (padded *)p;
    while (!__atomic_load_n(&g_quit, __ATOMIC_RELAXED))
        __atomic_add_fetch(&c->v, 1, __ATOMIC_RELAXED);
    return 0;
}

/* a real-time gap without a call: a spin the compiler cannot remove */
static void gap(void)
{
    for (volatile int i = 0; i < 200000; i++) {
    }
}

static void join_all(HANDLE *h, int n)
{
    for (int i = 0; i < n; i++) {
        CHECK_EQ(WaitForSingleObject(h[i], 20000), WAIT_OBJECT_0);
        CloseHandle(h[i]);
    }
}

/* (a) a stopped world does not advance, a resumed one does */
TEST(ws2_counters_frozen_in_window)
{
    HANDLE h[NW];
    g_quit = 0;
    for (int i = 0; i < NW; i++) {
        g_cnt[i].v = 0;
        h[i] = test_CreateThread(NULL, 0, counter_thread, &g_cnt[i], 0, NULL);
    }
    int moved_in_window = 0, stuck_after = 0;
    for (int round = 0; round < 1000; round++) {
        unsigned long a[NW], b[NW], c[NW];
        fnvl_host_world_stop();
        for (int i = 0; i < NW; i++) a[i] = g_cnt[i].v;
        gap();
        for (int i = 0; i < NW; i++) b[i] = g_cnt[i].v;
        gap();
        for (int i = 0; i < NW; i++) c[i] = g_cnt[i].v;
        fnvl_host_world_resume();
        for (int i = 0; i < NW; i++)
            if (a[i] != b[i] || b[i] != c[i]) moved_in_window++;
        for (int i = 0; i < NW; i++) {   /* every counter runs again */
            int n = 0;
            while (g_cnt[i].v == c[i] && n++ < 100000)
                Sleep(0);
            if (g_cnt[i].v == c[i]) stuck_after++;
        }
    }
    g_quit = 1;
    join_all(h, NW);
    CHECK_EQ(moved_in_window, 0);
    CHECK_EQ(stuck_after, 0);
}

/* (b)+(c) non-atomic RMW only inside windows, exclusion between several concurrent stoppers */
static volatile unsigned long g_shared;
static volatile int g_in_window;
static volatile int g_overlap;
static const int ROUNDS = 1500;

static void stopper_loop(void)
{
    for (int i = 0; i < ROUNDS; i++) {
        fnvl_host_world_stop();
        if (g_in_window) g_overlap++;
        g_in_window = 1;
        unsigned long v = g_shared;     /* a non-atomic read-modify-write, as a lifted unaligned lock/xchg does */
        for (volatile int k = 0; k < 20; k++) {
        }
        g_shared = v + 1;
        g_in_window = 0;
        fnvl_host_world_resume();
    }
}
static DWORD stopper_thread(LPVOID) { stopper_loop(); return 0; }

static void concurrent_stoppers(int others)
{
    g_shared = 0; g_in_window = 0; g_overlap = 0;
    HANDLE h[3];
    for (int i = 0; i < others; i++) h[i] = test_CreateThread(NULL, 0, stopper_thread, NULL, 0, NULL);
    stopper_loop();
    join_all(h, others);
    CHECK_EQ(g_shared, (unsigned long)(others + 1) * ROUNDS);
    CHECK_EQ(g_overlap, 0);
}
TEST(ws2_rmw_exact_two_stoppers) { concurrent_stoppers(1); }
TEST(ws2_rmw_exact_four_stoppers) { concurrent_stoppers(3); }

/* (d) a thread suspended by SuspendThread before a stop stays suspended after resume */
TEST(ws2_suspendthread_survives_stop)
{
    HANDLE h[NW];
    g_quit = 0;
    for (int i = 0; i < NW; i++) {
        g_cnt[i].v = 0;
        h[i] = test_CreateThread(NULL, 0, counter_thread, &g_cnt[i], 0, NULL);
    }
    while (g_cnt[0].v < 1000) Sleep(0);
    CHECK_EQ(SuspendThread(h[0]), 0);
    unsigned long frozen = g_cnt[0].v;
    for (int round = 0; round < 100; round++) {
        fnvl_host_world_stop();
        fnvl_host_world_resume();
    }
    Sleep(20);
    CHECK_EQ(g_cnt[0].v, frozen);                 /* still suspended */
    unsigned long other = g_cnt[1].v;
    for (int n = 0; n < 100000 && g_cnt[1].v == other; n++) Sleep(0);
    CHECK(g_cnt[1].v != other);                   /* the others run */
    CHECK_EQ(ResumeThread(h[0]), 1);
    for (int n = 0; n < 100000 && g_cnt[0].v == frozen; n++) Sleep(0);
    CHECK(g_cnt[0].v != frozen);                  /* ResumeThread brings it back */
    g_quit = 1;
    join_all(h, NW);
}

/* (e) threads created and ended continuously during repeated stops; none starts running during a stop */
static volatile unsigned long g_ran;
static volatile int g_creating;
static DWORD short_thread(LPVOID)
{
    __atomic_add_fetch(&g_ran, 1, __ATOMIC_RELAXED);
    return 0;
}
static DWORD creator_thread(LPVOID)
{
    while (g_creating) {
        HANDLE t[3];
        for (int i = 0; i < 3; i++) t[i] = test_CreateThread(NULL, 0, short_thread, NULL, 0, NULL);
        for (int i = 0; i < 3; i++) {
            WaitForSingleObject(t[i], INFINITE);
            CloseHandle(t[i]);
        }
    }
    return 0;
}
TEST(ws2_create_exit_during_stops)
{
    g_ran = 0; g_creating = 1;
    HANDLE c[2];
    for (int i = 0; i < 2; i++) c[i] = test_CreateThread(NULL, 0, creator_thread, NULL, 0, NULL);
    int moved = 0;
    /* The invariant is checked only against stops that overlap thread creation and exit, so the run is progress-bound,
     * not a fixed round count: a fixed count can finish before the creators have started a single thread (stop and
     * resume are faster than thread start), which proves nothing. Stops continue until at least MIN_RAN short threads
     * have run inside the stopped-and-resumed sequence, and at least 400 rounds have been made. */
    const unsigned long MIN_RAN = 200;
    for (int round = 0; round < 400 || __atomic_load_n(&g_ran, __ATOMIC_RELAXED) < MIN_RAN; round++) {
        fnvl_host_world_stop();
        unsigned long a = g_ran;
        gap();
        unsigned long b = g_ran;
        fnvl_host_world_resume();
        if (a != b) moved++;
    }
    g_creating = 0;
    join_all(c, 2);
    CHECK_EQ(moved, 0);
    CHECK(g_ran >= MIN_RAN);
}

/* (f) threads blocked in a wait, asleep, or holding a critical section continue correctly after the stops */
static CRITICAL_SECTION g_cs;
static HANDLE g_ev;
static volatile int g_cs_held, g_waiter_done, g_sleeps, g_cs_released;
static DWORD waiter_thread(LPVOID)
{
    g_waiter_done = WaitForSingleObject(g_ev, 20000) == WAIT_OBJECT_0 ? 1 : 2;
    return 0;
}
static DWORD sleeper_thread(LPVOID)
{
    for (int i = 0; i < 50; i++) {
        Sleep(1);
        g_sleeps++;
    }
    return 0;
}
static DWORD holder_thread(LPVOID)
{
    EnterCriticalSection(&g_cs);
    g_cs_held = 1;
    for (int i = 0; i < 50; i++) Sleep(1);
    g_cs_released = 1;
    LeaveCriticalSection(&g_cs);
    return 0;
}
TEST(ws2_blocked_threads_continue)
{
    InitializeCriticalSection(&g_cs);
    g_ev = CreateEventA(NULL, TRUE, FALSE, NULL);
    g_waiter_done = g_sleeps = g_cs_held = g_cs_released = 0;
    HANDLE h[3];
    h[0] = test_CreateThread(NULL, 0, waiter_thread, NULL, 0, NULL);
    h[1] = test_CreateThread(NULL, 0, sleeper_thread, NULL, 0, NULL);
    h[2] = test_CreateThread(NULL, 0, holder_thread, NULL, 0, NULL);
    while (!g_cs_held) Sleep(0);
    for (int round = 0; round < 300; round++) {
        fnvl_host_world_stop();
        gap();
        fnvl_host_world_resume();
    }
    CHECK_EQ(g_waiter_done, 0);       /* still waiting: nothing signalled it */
    SetEvent(g_ev);
    join_all(h, 3);
    CHECK_EQ(g_waiter_done, 1);
    CHECK_EQ(g_sleeps, 50);
    CHECK_EQ(g_cs_released, 1);
    EnterCriticalSection(&g_cs);      /* the holder's section is free again */
    LeaveCriticalSection(&g_cs);
    CloseHandle(g_ev);
    DeleteCriticalSection(&g_cs);
}
