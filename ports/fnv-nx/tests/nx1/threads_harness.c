/* NX1 check 4: toolchain/host/placement.h over every input the guest can pass, natively.
 * For each thread_create cpu argument (default -1, explicit cores 0..70, roles 0x100+r, junk), creation order and
 * Win32 priority, with and without the core 3 opt-in, and for each thread_set_cpu spec: the ideal core is inside
 * the mask, the mask inside the application cores (0-2; 0-3 with the opt-in), the priority inside the NPDM's
 * 0x1C..0x3B. Prints one line per class of violation and a summary; exit 1 on any. */
#include <stdint.h>
#include <stdio.h>

#include "placement.h"

static unsigned long bad_mask, bad_ideal, bad_prio, n;
static char first[3][160];

static void judge(int core3, int ideal, uint32_t mask, int prio, const char *what, unsigned long long arg) {
    uint32_t allowed = core3 ? FNVNX_APP_CORES_CORE3 : FNVNX_APP_CORES;
    n++;
    if (!mask || (mask & ~allowed)) {
        if (!bad_mask++) snprintf(first[0], sizeof first[0], "%s %#llx core3=%d -> mask %#x", what, arg, core3, mask);
    }
    if (ideal < 0 || ideal > 31 || !(mask >> ideal & 1) || !(allowed >> ideal & 1)) {
        if (!bad_ideal++) snprintf(first[1], sizeof first[1], "%s %#llx core3=%d -> ideal %d mask %#x", what, arg, core3,
                                   ideal, mask);
    }
    if (prio < 0x1C || prio > 0x3B) {
        if (!bad_prio++) snprintf(first[2], sizeof first[2], "%s %#llx -> priority %#x", what, arg, prio);
    }
}

int main(void) {
    uint64_t cpus[200];
    int nc = 0;
    cpus[nc++] = (uint64_t)-1;
    cpus[nc++] = (uint64_t)-2;
    for (uint64_t c = 0; c <= 70; c++) cpus[nc++] = c;
    for (uint64_t r = 0; r < 0x100; r += 7) cpus[nc++] = 0x100 + r;
    cpus[nc++] = 0x1FF;
    cpus[nc++] = 0x12345;
    cpus[nc++] = 0x8000000000000000ull;
    for (int core3 = 0; core3 <= 1; core3++)
        for (int i = 0; i < nc; i++)
            for (unsigned seq = 0; seq < 6; seq++)
                for (int64_t prio = -40; prio <= 40; prio += 5) {
                    int ideal = -1, hp = -1;
                    uint32_t mask = 0;
                    fnvnx_place_ex(cpus[i], seq, prio, core3, &ideal, &mask, &hp);
                    judge(core3, ideal, mask, hp, "thread_create cpu", (unsigned long long)cpus[i]);
                }
    /* thread_set_cpu: a single core (SetThreadAffinityMask with one bit / SetThreadIdealProcessor) or ideal+mask */
    for (uint64_t c = 0; c < 64; c++)
        for (int cur = 0; cur < 3; cur++) {
            int ideal;
            uint32_t mask;
            fnvnx_cpu_spec(c, cur, 0x6u | 1u << cur, &ideal, &mask);   /* a valid current placement */
            judge(0, ideal, mask, 0x2C, "thread_set_cpu core", (unsigned long long)c);
        }
    for (unsigned id = 0; id < 0x100; id += 3)
        for (uint32_t m = 0; m < 0x100; m += 5) {
            int ideal;
            uint32_t mask;
            fnvnx_cpu_spec(FNVNX_CPU_SPEC(id, m), 1, 0x6, &ideal, &mask);
            judge(0, ideal, mask, 0x2C, "thread_set_cpu spec", (unsigned long long)FNVNX_CPU_SPEC(id, m));
        }
    const char *names[3] = {"mask outside the application cores", "ideal core outside the mask/cores",
                            "priority outside 0x1C..0x3B"};
    unsigned long counts[3] = {bad_mask, bad_ideal, bad_prio};
    for (int k = 0; k < 3; k++)
        if (counts[k]) printf("FAIL placement.%d: %lu of %lu placements: %s, first: %s\n", k, counts[k], n, names[k], first[k]);
    printf("placement: %lu placements, %lu violations\n", n, bad_mask + bad_ideal + bad_prio);
    return bad_mask + bad_ideal + bad_prio ? 1 : 0;
}
