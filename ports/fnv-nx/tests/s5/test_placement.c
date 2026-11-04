/* test_placement.c - the host's thread placement table and decoders (toolchain/host/placement.h), natively.
 *
 *   cc -std=gnu11 -Wall -I toolchain/include tests/s5/test_placement.c -o test_placement && ./test_placement
 *   (tests/s5/test_tools.py builds and runs it)
 */
#include <stdio.h>

#include "../../toolchain/host/placement.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static void place3(int core3, uint64_t cpu, unsigned seq, int64_t prio, int want_role, int want_ideal,
                   uint32_t want_mask, int want_prio) {
    int ideal = -9, hp = -9;
    uint32_t mask = 0;
    int role = fnvnx_place_ex(cpu, seq, prio, core3, &ideal, &mask, &hp);
    if (role != want_role || ideal != want_ideal || mask != want_mask || hp != want_prio) {
        printf("FAIL place(cpu %#llx, seq %u, prio %lld) = role %d core %d mask %#x prio %#x, want %d %d %#x %#x\n",
               (unsigned long long)cpu, seq, (long long)prio, role, ideal, mask, hp, want_role, want_ideal, want_mask,
               want_prio);
        failures++;
    }
}
#define place(...) place3(0, __VA_ARGS__)

int main(void) {
    /* the policy of docs/PERF.md 6.3 */
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_MAIN), 0, 0, FNVNX_ROLE_MAIN, 0, 0x1, 0x2C);
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_RENDER_WORKER), 0, 0, FNVNX_ROLE_RENDER_WORKER, 1, 0x2, 0x2B);
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_AI_LINEAR_1), 5, 0, FNVNX_ROLE_AI_LINEAR_1, 2, 0x6, 0x2C);
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_AI_LINEAR_2), 6, -1, FNVNX_ROLE_AI_LINEAR_2, 1, 0x6, 0x2D);
    /* default role (cpu -1 or the explicit role): cores 1-2, the ideal core rotates; never core 0 */
    place((uint64_t)-1, 0, 0, FNVNX_ROLE_DEFAULT, 1, 0x6, 0x2C);
    place((uint64_t)-1, 1, 0, FNVNX_ROLE_DEFAULT, 2, 0x6, 0x2C);
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_DEFAULT), 2, 2, FNVNX_ROLE_DEFAULT, 1, 0x6, 0x2A);
    /* an explicit core: that core only */
    place(2, 0, 0, -1, 2, 0x4, 0x2C);
    /* an unknown role falls back to the default row */
    place(FNVNX_THREAD_ROLE(0x7F), 0, 0, FNVNX_ROLE_DEFAULT, 1, 0x6, 0x2C);
    /* the background roles: cores 1-2 like the default row */
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_IO), 0, 0, FNVNX_ROLE_IO, 1, 0x6, 0x2C);
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_AUDIO), 1, 2, FNVNX_ROLE_AUDIO, 2, 0x6, 0x2A);
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_TASKLET), 0, 0, FNVNX_ROLE_TASKLET, 1, 0x6, 0x2C);
    place(FNVNX_THREAD_ROLE(FNVNX_ROLE_LOADING), 0, 0, FNVNX_ROLE_LOADING, 1, 0x6, 0x2C);
    /* core 3 opted in: the background roles and the default row rotate over cores 1-3; main, the render worker,
     * the AI linear threads and the loading screen keep cores 0-2 */
    place3(1, (uint64_t)-1, 2, 0, FNVNX_ROLE_DEFAULT, 3, 0xE, 0x2C);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_IO), 0, 0, FNVNX_ROLE_IO, 1, 0xE, 0x2C);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_AUDIO), 2, 0, FNVNX_ROLE_AUDIO, 3, 0xE, 0x2C);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_TASKLET), 5, 0, FNVNX_ROLE_TASKLET, 3, 0xE, 0x2C);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_MAIN), 0, 0, FNVNX_ROLE_MAIN, 0, 0x1, 0x2C);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_RENDER_WORKER), 0, 0, FNVNX_ROLE_RENDER_WORKER, 1, 0x2, 0x2B);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_AI_LINEAR_1), 0, 0, FNVNX_ROLE_AI_LINEAR_1, 2, 0x6, 0x2C);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_AI_LINEAR_2), 0, 0, FNVNX_ROLE_AI_LINEAR_2, 1, 0x6, 0x2C);
    place3(1, FNVNX_THREAD_ROLE(FNVNX_ROLE_LOADING), 2, 0, FNVNX_ROLE_LOADING, 1, 0x6, 0x2C);
    /* Win32 priorities clamp to Horizon's range */
    CHECK(fnvnx_win32_to_horizon_prio(15) == 0x1D && fnvnx_win32_to_horizon_prio(-15) == 0x3B);
    CHECK(fnvnx_win32_to_horizon_prio(100) == 0x1C);
    /* without the opt-in no game thread is on core 3; the main thread stays alone on core 0 either way; core 3 only
     * for the background roles */
    for (int r = 0; r < FNVNX_ROLE_COUNT; r++) {
        CHECK((fnvnx_placement[r].mask & ~FNVNX_APP_CORES) == 0);
        CHECK(r == FNVNX_ROLE_MAIN || !(fnvnx_placement[r].mask & 1));
        CHECK(!(fnvnx_placement[r].mask3 & 1) && (fnvnx_placement[r].mask3 & ~FNVNX_APP_CORES_CORE3) == 0);
        int bg = r == FNVNX_ROLE_DEFAULT || r == FNVNX_ROLE_IO || r == FNVNX_ROLE_AUDIO || r == FNVNX_ROLE_TASKLET;
        CHECK(bg ? (fnvnx_placement[r].mask3 & 0x8) != 0 : fnvnx_placement[r].mask3 == 0);
    }
    CHECK(FNVNX_CORE3_DEFAULT == 0);

    /* thread_set_cpu specs */
    int ideal;
    uint32_t mask;
    fnvnx_cpu_spec(1, 2, 0x6, &ideal, &mask);                                  /* one core */
    CHECK(ideal == 1 && mask == 0x2);
    fnvnx_cpu_spec(FNVNX_CPU_SPEC(FNVNX_CPU_KEEP, 0x5), 2, 0x6, &ideal, &mask);  /* mask 0b101: core 2 stays ideal */
    CHECK(ideal == 2 && mask == 0x5);
    fnvnx_cpu_spec(FNVNX_CPU_SPEC(FNVNX_CPU_KEEP, 0x3), 2, 0x6, &ideal, &mask);  /* ideal left the mask: lowest core */
    CHECK(ideal == 0 && mask == 0x3);
    fnvnx_cpu_spec(FNVNX_CPU_SPEC(0, 0), 1, 0x6, &ideal, &mask);                 /* ideal 0, mask kept: 0 joins */
    CHECK(ideal == 0 && mask == 0x7);
    fnvnx_cpu_spec(FNVNX_CPU_SPEC(2, 0x8), 1, 0x6, &ideal, &mask);               /* core 3 is not ours */
    CHECK(ideal == 2 && mask == 0x6);

    printf("%s: %d failures\n", failures ? "FAIL" : "PASS", failures);
    return failures != 0;
}
