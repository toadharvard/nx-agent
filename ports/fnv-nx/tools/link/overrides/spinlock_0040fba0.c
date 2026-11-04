/* fnvnx_spin_unlock_0040fba0 - RecursiveSpinLock::Unlock (0x0040fba0) with a release store (docs/LINK.md
 * "Overrides", docs/PERF.md 6.2).
 *
 * The engine's recursive spin lock (32 bytes: owner thread id at +0, recursion count at +4; the global lock 0x011f11a0
 * of the AI linear task threads is one) is taken with InterlockedCompareExchange on the owner word (0x0040fbf0) and
 * released with a plain store of 0. On x86 that store is a release (TSO). On AArch64 it is not: the stores made
 * inside the critical section may become visible to the next owner on another core after it sees owner == 0. The
 * decomp's body (and our f-link's code for it: ldr/sub/str of the count, then `str wzr, [x19]`) has no barrier;
 * the empty call it makes first (0x0040fbe0, the PC build's stand-in for the 360's barrier) does nothing.
 *
 * Same layout and effect, plus release ordering (stlr): every store and load before the unlock is ordered before
 * the owner word reads 0. Lock and TryLock (0x0040fbf0, 0x0078d200) need no change: their CAS goes through
 * InterlockedCompareExchange, a full barrier in the platform layer. Copies of the release that units inlined
 * (`if (--x.count == 0) x.owner = 0;`) are not reached by this override: see PERF.md 6.2 for the list owner. */
#include <stdint.h>

struct fnvnx_recursive_spin_lock {
    volatile uint32_t owner;   /* +0: GetCurrentThreadId() of the owner, 0 when free */
    uint32_t count;            /* +4: recursion depth, only touched by the owner */
};

void fnvnx_spin_unlock_0040fba0(struct fnvnx_recursive_spin_lock *l)
{
    if (--l->count == 0)
        __atomic_store_n(&l->owner, 0, __ATOMIC_RELEASE);
}
