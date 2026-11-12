/* test_host_hooks.cpp - the lifter runtime's two host hooks on the real product unit platform/boundary/host_hooks.cpp
 * (fnvl_host_tls_slot, fnvl_host_ticks_ns; their contract is the header comment of that file and shim/include/fnvl_host.h).
 * Nothing is replaced: the unit and the clock (platform/clock) are the product files linked into this binary.
 * Rule R-D of platform/manifest/DOUBLES.md: the unit has its own test until the lifter runtime is part of a test binary. */
#include "s4_test.h"

#include <pthread.h>
#include <stdint.h>

#include "fnvl_host.h"
#include "fnvnx_clock.h"

namespace {
struct Probe {
    struct cpu **first, **again;
    struct cpu *initial;
    struct cpu *after_write;
};

void *probe_thread(void *a)
{
    Probe *p = (Probe *)a;
    p->first = fnvl_host_tls_slot();
    p->initial = *p->first;                       /* zero in every new thread, whatever another thread stored */
    *p->first = (struct cpu *)(uintptr_t)0x2000;
    p->again = fnvl_host_tls_slot();
    p->after_write = *p->again;
    return NULL;
}
}   // namespace

TEST(host_hooks_tls_slot_per_thread)
{
    struct cpu **mine = fnvl_host_tls_slot();
    CHECK(mine != NULL);
    CHECK(fnvl_host_tls_slot() == mine);          /* the same slot for every call of a thread */
    *mine = (struct cpu *)(uintptr_t)0x1000;
    CHECK(*fnvl_host_tls_slot() == (struct cpu *)(uintptr_t)0x1000);

    Probe p = {};
    pthread_t t;
    CHECK_EQ(test_pthread_create(&t, NULL, probe_thread, &p), 0);
    CHECK_EQ(pthread_join(t, NULL), 0);
    CHECK(p.first != NULL && p.first != mine);   /* another thread, another slot */
    CHECK(p.initial == NULL);                    /* zero although this thread had stored 0x1000 */
    CHECK(p.again == p.first);
    CHECK(p.after_write == (struct cpu *)(uintptr_t)0x2000);
    CHECK(*mine == (struct cpu *)(uintptr_t)0x1000);   /* the other thread's store did not reach this slot */

    Probe q = {};
    CHECK_EQ(test_pthread_create(&t, NULL, probe_thread, &q), 0);
    CHECK_EQ(pthread_join(t, NULL), 0);
    CHECK(q.initial == NULL);                    /* a second thread starts at zero too: no value carries over */
    *mine = NULL;
}

TEST(host_hooks_ticks_are_the_clock)
{
    /* one clock: the hook reads the counter clk_read_ns reads (the clock's reads never go backwards, and in the virtual
     * clock every read advances it), so a read of the hook lies between two reads of the clock and vice versa */
    uint64_t a = clk_read_ns();
    uint64_t t = fnvl_host_ticks_ns();
    uint64_t b = clk_read_ns();
    CHECK(a <= t && t <= b);
    uint64_t t2 = fnvl_host_ticks_ns();
    CHECK(t <= t2);
}
