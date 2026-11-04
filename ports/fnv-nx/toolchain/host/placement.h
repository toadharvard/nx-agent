/* placement.h - where the host puts each thread (docs/PERF.md 6.3, J-0076). Plain C, no libnx: host_v2.inc uses it
 * for guest threads and host pthreads, tests/s5/test_placement.c checks it natively.
 *
 * Horizon: the application has cores 0-2 (core 3 is the system's); priorities run from 0x1C (most urgent) to 0x3B
 * (idle); the main thread runs at 0x2C (NPDM). A thread runs on its ideal core and may migrate inside its
 * affinity mask. Tune the table below: one row per role of toolchain/include/fnvnx/hostapi.h (FNVNX_ROLE_*). */
#ifndef FNVNX_PLACEMENT_H
#define FNVNX_PLACEMENT_H

#include <stdint.h>

#include "fnvnx/hostapi.h"

typedef struct FnvnxPlacement {
    const char *role;
    int ideal;          /* ideal core; -1: rotate over the mask's cores, one per new thread */
    uint32_t mask;      /* affinity mask over cores 0-2 */
    int prio;           /* Horizon priority; 0: the thread's Win32 priority mapped (fnvnx_win32_to_horizon_prio) */
} FnvnxPlacement;

static const FnvnxPlacement fnvnx_placement[FNVNX_ROLE_COUNT] = {
    [FNVNX_ROLE_DEFAULT]       = {"game",          -1, 0x6, 0},    /* cores 1-2, the scheduler balances */
    [FNVNX_ROLE_MAIN]          = {"main",           0, 0x1, 0},
    [FNVNX_ROLE_AI_LINEAR_1]   = {"ai-linear-1",    2, 0x6, 0},
    [FNVNX_ROLE_AI_LINEAR_2]   = {"ai-linear-2",    1, 0x6, 0},
    [FNVNX_ROLE_RENDER_WORKER] = {"render-worker",  1, 0x2, 0x2B}, /* one step above the game's normal 0x2C */
};

#define FNVNX_APP_CORES 0x7u

static inline int fnvnx_win32_to_horizon_prio(int64_t prio) {   /* Win32: higher = more urgent; 0 -> 0x2C */
    int64_t p = 0x2C - prio;
    return (int)(p < 0x1C ? 0x1C : p > 0x3B ? 0x3B : p);
}

/* thread_create's cpu argument (-1 default role, 0..63 a core, FNVNX_THREAD_ROLE(r)) and Win32 priority ->
 * ideal core, affinity mask, Horizon priority; seq numbers the threads created so far (rotation). Returns the
 * role used (-1 for an explicit core). */
static inline int fnvnx_place(uint64_t cpu, unsigned seq, int64_t win32_prio, int *ideal, uint32_t *mask, int *prio) {
    int role;
    if ((cpu & ~0xFFull) == 0x100 && (cpu & 0xFF) < FNVNX_ROLE_COUNT)
        role = (int)(cpu & 0xFF);
    else if ((int64_t)cpu < 0 || (cpu & ~0xFFull))
        role = FNVNX_ROLE_DEFAULT;
    else {
        *ideal = (int)cpu;
        *mask = 1u << (cpu & 31);
        *prio = fnvnx_win32_to_horizon_prio(win32_prio);
        return -1;
    }
    const FnvnxPlacement *p = &fnvnx_placement[role];
    *mask = p->mask & FNVNX_APP_CORES;
    if (p->ideal >= 0) {
        *ideal = p->ideal;
    } else {   /* the seq-th core of the mask */
        int n = 0, cores[32];
        for (int c = 0; c < 32; c++)
            if (*mask >> c & 1) cores[n++] = c;
        *ideal = n ? cores[seq % (unsigned)n] : 0;
    }
    *mask |= 1u << *ideal;
    *prio = p->prio ? p->prio : fnvnx_win32_to_horizon_prio(win32_prio);
    return role;
}

/* thread_set_cpu's spec -> ideal core and mask, given the thread's current ones (svcGetThreadCoreMask) */
static inline void fnvnx_cpu_spec(uint64_t spec, int cur_ideal, uint32_t cur_mask, int *ideal, uint32_t *mask) {
    if (!(spec >> 63)) {                       /* one core: SetThreadAffinityMask with one bit */
        *ideal = (int)(spec & 31);
        *mask = 1u << *ideal;
        return;
    }
    uint32_t m = (uint32_t)(spec >> 8) & FNVNX_APP_CORES;
    unsigned id = (unsigned)(spec & 0xFF);
    *mask = m ? m : cur_mask;
    if (id != FNVNX_CPU_KEEP && id < 32)
        *ideal = (int)id;
    else if (*mask >> cur_ideal & 1)
        *ideal = cur_ideal;
    else {                                     /* the old ideal core left the mask: its lowest core */
        int c = 0;
        while (c < 31 && !(*mask >> c & 1)) c++;
        *ideal = c;
    }
    *mask |= 1u << *ideal;
}

#endif /* FNVNX_PLACEMENT_H */
