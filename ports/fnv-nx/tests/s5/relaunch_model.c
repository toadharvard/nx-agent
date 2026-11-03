/* tests/s5/relaunch_model.c - S10's placement model (tests/s10/test_reserve.c) applied to the real guest:
 * how often a launch has to relaunch for each guest placement S5 can choose.
 *
 *   cc -O2 -I startup/host tests/s5/relaunch_model.c startup/host/reserve.c -lm -o relaunch_model
 *   ./relaunch_model <guest bytes incl. bss> [host NSO bytes] [trials]
 */
#include <inttypes.h>
#define main s10_test_main
#include "../s10/test_reserve.c"
#undef main

int main(int argc, char **argv) {
    const uint64_t MiB = 1ull << 20;
    uint64_t guest = argc > 1 ? strtoull(argv[1], NULL, 0) : 40 * MiB;
    uint64_t nso = argc > 2 ? strtoull(argv[2], NULL, 0) : 2 * MiB;
    long trials = argc > 3 ? atol(argv[3]) : 200000;
    guest = (guest + 0xFFFFF) & ~0xFFFFFull;
    nso = (nso + 0x1FFFFF) & ~0x1FFFFFull;
    const FnvnxResvRange window = { FNVNX_RESV_WINDOW_LO, FNVNX_RESV_WINDOW_HI, FNVNX_RESV_REQUIRED, "window" };
    const FnvnxResvRange adj = { FNVNX_RESV_WINDOW_HI, FNVNX_RESV_WINDOW_HI + guest, FNVNX_RESV_REQUIRED, "guest@02100000" };
    const FnvnxResvRange far = { FNVNX_RESV_GUEST_BASE, FNVNX_RESV_GUEST_BASE + guest, FNVNX_RESV_REQUIRED, "guest@10000000" };
    struct { const char *name; FnvnxResvRange r[2]; uint32_t n; } cfg[] = {
        { "relocatable guest (data window required only)", { window }, 1 },
        { "fixed guest at 0x02100000, adjacent to the window", { window, adj }, 2 },
        { "fixed guest at 0x10000000", { window, far }, 2 },
    };
    printf("guest %" PRIu64 " MiB, host NSO %" PRIu64 " MiB, %ld launches each\n", guest / MiB, nso / MiB, trials);
    for (unsigned i = 0; i < sizeof cfg / sizeof cfg[0]; i++) {
        double an = 0, p = model(cfg[i].r, cfg[i].n, nso, MiB, trials, &an);
        printf("  %-52s P(relaunch) %5.1f %%  (first order %4.1f %%)  launches %.2f\n", cfg[i].name, 100 * p,
               100 * an, 1.0 / (1.0 - p));
    }
    return 0;
}
