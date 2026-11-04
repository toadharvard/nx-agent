/* test_placement.c - the host's thread placement table and decoders (toolchain/host/placement.h), natively.
 *
 *   cc -std=gnu11 -Wall -I toolchain/include tests/s5/test_placement.c -o test_placement && ./test_placement
 *   (tests/s5/test_tools.py builds and runs it)
 */
#include <stdio.h>

#include "../../toolchain/host/placement.h"

static int failures;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); failures++; } } while (0)

static void place(uint64_t cpu, unsigned seq, int64_t prio, int want_role, int want_ideal, uint32_t want_mask,
                  int want_prio) {
    int ideal = -9, hp = -9;
    uint32_t mask = 0;
    int role = fnvnx_place(cpu, seq, prio, &ideal, &mask, &hp);
    if (role != want_role || ideal != want_ideal || mask != want_mask || hp != want_prio) {
        printf("FAIL place(cpu %#llx, seq %u, prio %lld) = role %d core %d mask %#x prio %#x, want %d %d %#x %#x\n",
               (unsigned long long)cpu, seq, (long long)prio, role, ideal, mask, hp, want_role, want_ideal, want_mask,
               want_prio);
        failures++;
    }
}

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
    /* Win32 priorities clamp to Horizon's range */
    CHECK(fnvnx_win32_to_horizon_prio(15) == 0x1D && fnvnx_win32_to_horizon_prio(-15) == 0x3B);
    CHECK(fnvnx_win32_to_horizon_prio(100) == 0x1C);
    /* the table never puts a game thread on core 3 and keeps the main thread alone on core 0 */
    for (int r = 0; r < FNVNX_ROLE_COUNT; r++) {
        CHECK((fnvnx_placement[r].mask & ~FNVNX_APP_CORES) == 0);
        CHECK(r == FNVNX_ROLE_MAIN || !(fnvnx_placement[r].mask & 1));
    }

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
