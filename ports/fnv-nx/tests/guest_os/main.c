/* guest_os: the platform's OS layer (os.h, os_clock.h) and the GPU entry, run as a guest on the L4 boot machine.
 * Links the real product roots (rt/guest, platform); defines no symbol of theirs (only main and test-local statics/globals g_*).
 * Result: exit code 0 = every check held, else the number of the first failed check; the words the harness compares with the
 * boot machine's own answer are in g_gpu_ret (read from the ELF symbol). */
#include <stdint.h>
#include "os.h"
#include "os_clock.h"
#include "fnvnx_render_hooks.h"

uint64_t g_gpu_ret[2];                     /* [0] = nxd3d_gpu_call(7, g_gpu_args, 5), [1] = nxd3d_gpu_call(0, NULL, 0) */
uint64_t g_gpu_args[5] = { 0x1122334455667788ull, 0, ~0ull, 42, 0xDEADBEEFull };
static os_lock_t g_lk = OS_LOCK_INIT;
static os_cond_t g_cv = OS_COND_INIT;
static volatile uint32_t g_count, g_ready;
#define N 1000u

static void worker(void *arg)
{
    for (uint32_t i = 0; i < N; i++) { os_lock(&g_lk); g_count += (uint32_t)(uintptr_t)arg; os_unlock(&g_lk); }
    os_lock(&g_lk); g_ready = 1; os_cond_signal(&g_cv); os_unlock(&g_lk);
}

int main(void)
{
    os_lock_t l = OS_LOCK_INIT;
    if (!os_trylock(&l)) return 1;                 /* unlocked -> acquired */
    if (os_trylock(&l)) return 2;                  /* held, not recursive */
    os_unlock(&l);
    if (!os_trylock(&l)) return 3;                 /* released -> acquirable again */
    os_unlock(&l);

    os_thread *t = os_thread_create(worker, (void *)1, 0x10000, OS_PRIO_NORMAL, -1);
    if (!t) return 4;
    if (os_thread_start(t) < 0) return 5;
    os_lock(&g_lk);
    while (!g_ready) os_cond_wait(&g_cv, &g_lk);   /* woken by the worker's signal; spurious wakeups re-check */
    os_unlock(&g_lk);
    if (os_thread_join(t) < 0) return 6;
    os_thread_free(t);
    if (g_count != N) return 7;                    /* N increments under the lock, none lost */

    uint64_t t0 = os_monotonic_ns();
    os_sleep_ns(2000000ull);
    uint64_t t1 = os_monotonic_ns();
    if (t1 < t0 || t1 - t0 < 2000000ull) return 8; /* the clock is monotonic, the sleep lasted at least what was asked */
    if (os_monotonic_ns() < t1) return 9;

    os_lock_t l2 = OS_LOCK_INIT; os_cond_t c2 = OS_COND_INIT;
    os_lock(&l2);
    t0 = os_monotonic_ns();
    if (os_cond_timedwait(&c2, &l2, 1000000ull) != 1) return 10;   /* nobody signals: timed out (1) */
    t1 = os_monotonic_ns();
    os_unlock(&l2);
    if (t1 - t0 < 1000000ull) return 11;

    g_gpu_ret[0] = nxd3d_gpu_call(7, g_gpu_args, 5);
    g_gpu_ret[1] = nxd3d_gpu_call(0, 0, 0);        /* nargs 0 with NULL is valid */
    return 0;
}
