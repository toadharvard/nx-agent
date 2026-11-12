/* os_world.c - the world registry and fnvl_host_world_stop / fnvl_host_world_resume (lifter hook, rule R9).
 *
 * CONTRACT
 *   fnvl_host_world_stop():   when it returns, every other registered thread (below) is stopped: it executes no
 *       instruction until fnvl_host_world_resume(). Synchronous: the backend primitive returns only for a parked target.
 *       The caller holds the one global world lock from stop to resume.
 *   fnvl_host_world_resume(): lets those threads run again and releases the lock. Same thread as the stop.
 *   Caller's obligations: between stop and resume nothing calls into the platform (no os_*, no Win32 import, no libc that
 *   locks or allocates) or into the game; no nesting. The window is a host-instruction RMW plus barrier.
 *
 * THE REGISTRY. Threads made by os_thread_create register themselves in their entry routine before their function runs
 * and unregister after it returns (or in os_thread_exit); the process's first thread, which the platform did not make, is
 * adopted by the constructor at the end of this file. Every game thread and every platform service thread (audio pump,
 * clock pump, scanners, flushers, winsock pump) is made by os_thread_create (grep: platform/win32, platform/audio,
 * platform/clock, toolchain/guest/platform_glue.cpp), so there is one registry. Registration and unregistration take
 * the world lock, which the stopper holds. Hence (a) a thread that starts during a stop blocks in os_world_register
 * before it runs any game code, a thread that ends during a stop blocks in os_world_unregister while still registered
 * (it is then stopped like the others, or is waiting on the lock, which executes no guest code), and (b) the registry
 * cannot change while the world is stopped: the set stopped at stop is the set released at resume.
 *   "Stopped" is stated for registered threads. Before its registration and after its unregistration a thread executes
 *   only the backend's entry / exit code (no guest memory access, no game code).
 *
 * FAIRNESS. The world lock is granted in arrival order (world_lock below): a stopper repeating stop/resume cannot starve a
 * thread that is starting, ending or suspending a thread.
 *
 * DEADLOCK-FREEDOM. The stopper takes the world lock first and only then pauses anyone, so no stopped thread holds it
 * (the lock has one holder: the registry lock and the world lock are the same lock). The pause primitive needs no
 * cooperation from its target (a signal handler on POSIX, the kernel on Horizon), so a target that holds or waits for any
 * other platform lock is stopped all the same, and the stopper, which makes no platform call before resume, never waits
 * for a lock a stopped thread holds. A second stopper blocks on the world lock (it may be stopped meanwhile, which is
 * harmless) and stops the world after the first resume. The world lock is held between stop and resume only, and the
 * window contains no call into the game (obligation above), so it is never held across a game call; it is an os_lock so
 * that the host test backend counts it and would see a violation (tests/s4 nlk_*).
 *
 * COMBINATION WITH SuspendThread. The backend primitive is boolean. os_thread_suspend / os_thread_resume (the user
 * reason) also run under the world lock, so a user operation never interleaves with a stop; stop pauses exactly the
 * registered threads whose `user` flag is clear and resume releases exactly those. So paused = user || world holds
 * whenever a thread's state is observable: a thread suspended before a stop is not touched and stays suspended after
 * resume (ResumeThread during a stop cannot run: caller's obligation, and it would only block on the lock). */
#include "os_world.h"
#include "fnvl_host.h"

static os_lock_t g_world;
/* FIFO order of the world lock (a ticket gate in front of g_world). os_lock is a barging mutex: a stopper that runs
 * stop/resume back to back re-takes the lock in the gap between a resume and the next stop, and a thread blocked in
 * os_world_register / os_world_unregister / os_thread_suspend never wins it (observed: four starting threads blocked for
 * good against 400+ consecutive stops). Tickets are served in arrival order, so every waiter is served after at most the
 * holders that arrived before it: registration, unregistration and a second stop cannot be starved by a stopper. Only the
 * ticket holder takes g_world, so the lock itself is never contended. */
static volatile uint32_t g_tkt_next, g_tkt_serve;
static os_wslot *g_head;
static void world_lock(void)
{
    uint32_t my = __atomic_fetch_add(&g_tkt_next, 1, __ATOMIC_RELAXED);
    while (__atomic_load_n(&g_tkt_serve, __ATOMIC_ACQUIRE) != my)
        os_yield();
    os_lock(&g_world);
}
static void world_unlock(void)
{
    os_unlock(&g_world);
    __atomic_store_n(&g_tkt_serve, __atomic_load_n(&g_tkt_serve, __ATOMIC_RELAXED) + 1, __ATOMIC_RELEASE);
}
static __thread os_wslot *g_self;

void os_world_register(os_wslot *s, os_thread *t)
{
    world_lock();
    s->t = t;
    s->prev = NULL;
    s->next = g_head;
    if (g_head)
        g_head->prev = s;
    g_head = s;
    s->linked = 1;
    g_self = s;
    world_unlock();
}

void os_world_unregister(os_wslot *s)
{
    world_lock();
    if (s->linked) {
        if (s->prev)
            s->prev->next = s->next;
        else
            g_head = s->next;
        if (s->next)
            s->next->prev = s->prev;
        s->linked = 0;
    }
    s->dead = 1;
    if (g_self == s)
        g_self = NULL;
    world_unlock();
}

int os_thread_suspend(os_thread *t)
{
    if (!t)
        return -1;
    os_wslot *s = os_thread_wslot(t);
    int r = -1;
    world_lock();
    if (!s->user && !s->dead && os_raw_pause(t) == 0) {
        s->user = 1;
        r = 0;
    }
    world_unlock();
    return r;
}

int os_thread_resume(os_thread *t)
{
    if (!t)
        return -1;
    os_wslot *s = os_thread_wslot(t);
    int r = -1;
    world_lock();
    if (s->user) {
        s->user = 0;
        os_raw_resume(t);
        r = 0;
    }
    world_unlock();
    return r;
}

void fnvl_host_world_stop(void)
{
    world_lock();
    for (os_wslot *s = g_head; s; s = s->next) {
        if (s == g_self || s->user)
            continue;
        /* a registered thread is alive, started and not the caller: the primitive cannot refuse it */
        os_raw_pause(s->t);
    }
}

void fnvl_host_world_resume(void)
{
    for (os_wslot *s = g_head; s; s = s->next) {
        if (s == g_self || s->user)
            continue;
        os_raw_resume(s->t);
    }
    world_unlock();
}

/* the process's first thread: before the clock (FNVNX_INIT_CLOCK 101) and every game static */
__attribute__((constructor(102))) static void os_world_adopt_main(void)
{
    os_thread *t = os_raw_adopt_self();
    if (t)
        os_world_register(os_thread_wslot(t), t);
}
