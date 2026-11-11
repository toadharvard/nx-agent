/* host_vm_test.c - VirtualAlloc's device backend (toolchain/host/host_vm.inc) executed under qemu-aarch64 against the kernel double
 * kdouble.h: reservations take no memory, a fixed address is honoured or refused (taken, overlapping, the image window), commits map
 * one zeroed heap block per gap as a code alias (AliasCodeData, reprotectable) and are idempotent, decommits unmap whole extents (in
 * any protection) and zero partial ones, release unmaps everything and frees the address range; every range-taking service answers
 * a range that wraps. Test names (hostapi.h table): vm_reserve, vm_commit, vm_decommit, vm_release. */
#include "kdouble.h"
#include "fnvnx/hostapi.h"

static void logf_(const char *fmt, ...) { (void)fmt; }
static Handle g_own_process = OWN_HANDLE;
#include "../../../toolchain/host/host_vm.inc"
#undef free
#undef memalign
#define B(a) ((volatile uint8_t *)(uintptr_t)(a))

int main(void)
{
    kdouble_init();
    u64 lo = g_alo + (16ull << 20);   /* a 16 MiB step inside the arena (VirtualReservationSearch style); the lower half is the stack region */
    T(lo + (8ull << 20) < g_alo + ARENA / 2);

    /* vm_reserve: the probe the guest makes: a 0-byte reservation answers 0 */
    T(host_vm_reserve(0, 0) == 0);
    T(host_vm_reserve(0, 0x100000001ull) == 0 && host_vm_reserve(0, ~0ull) == 0);   /* above 4 GiB: 0 (no wrap to a tiny size) */
    u64 a = host_vm_reserve(0, 0x800000);   /* a scrap heap's 8 MiB */
    T(a != 0 && (a & 0xFFFF) == 0 && g_live_resv == 1 && heap_pages_in(H_LOCKED) == 0);
    /* fixed addresses: free -> exactly there; taken, overlapping or in the image window -> 0 */
    u64 f = host_vm_reserve(lo, 0x100000);
    T(f == lo);
    T(host_vm_reserve(lo + 0x80000, 0x10000) == 0);          /* inside the reservation above */
    u64 busy = lo + (8ull << 20);
    void *blk = t_memalign(PG, PG);
    T(svcMapMemory((void *)(uintptr_t)busy, blk, PG) == 0);   /* someone's mapping */
    T(host_vm_reserve(busy, 0x10000) == 0);
    T(host_vm_reserve(0x01000000, 0x10000) == 0);            /* the data window */
    T(host_vm_reserve(0x00300000, 0x200000) == 0);           /* overlapping the image range's start */
    T(host_vm_reserve(0xFFFFFFFF00000000ull, 0x10000) == 0);   /* hint wraps / above 4 GiB */
    T(host_vm_reserve(lo + 0x123456, 0x1000) == lo + 0x120000);   /* rounded down to 64 KiB */

    /* vm_commit: one zeroed block per gap, AliasCodeData, idempotent, only inside a reservation */
    T(host_vm_commit(a + 0x1000, 0x2000) == 0 && kstate(a + 0x1000) == A_ALIAS_CODE_DATA && kstate(a) == A_FREE);
    T(B(a + 0x1000)[0] == 0 && B(a + 0x2fff)[0] == 0);
    B(a + 0x1800)[0] = 7;
    T(host_vm_commit(a + 0x1000, 0x2000) == 0 && g_vm_next == 1);   /* already committed */
    T(B(a + 0x1800)[0] == 7);                                        /* and kept */
    T(host_vm_commit(a, 0x6000) == 0 && g_vm_next == 3);             /* the two gaps around it */
    T(host_vm_commit(a + 0x800000 - 0x1000, 0x2000) == FNVNX_HE_BADINPUT);   /* past the reservation */
    T(host_vm_commit(lo + (60ull << 20), 0x1000) == FNVNX_HE_BADINPUT);      /* not reserved */
    T(host_vm_commit(a, 0) == FNVNX_HE_BADINPUT);                              /* size 0 */
    T(host_vm_commit(~0ull - 0xFF, 0x1000) == FNVNX_HE_BADINPUT && host_vm_commit(~0ull - 0xFFF, 0xFFFF) == FNVNX_HE_BADINPUT);   /* wraps */
    T(host_vm_commit(a + 0x6000, 0xFFFFFFFFFFFFF000ull) == FNVNX_HE_BADINPUT);
    T(g_vm_next == 3);

    /* the protection of a committed page is kept by a second commit; a fresh page is read-write */
    T(host_vm_protect(a + 0x1000, 0x1000, FNVNX_VM_PROT_R) == 0 && kperm(a + 0x1000) == 1);
    T(host_vm_commit(a, 0x6000) == 0 && kperm(a + 0x1000) == 1 && kperm(a) == 3);
    T(host_vm_protect(a + 0x1000, 0x1000, 3) == 0);

    /* vm_decommit: part of an extent is zeroed, stays mapped and is read-write; a whole extent goes back, in any protection */
    B(a + 0x1800)[0] = 9;
    T(host_vm_protect(a + 0x1000, 0x1000, 0) == 0 && !CAN_READ(a + 0x1000));
    int locked = heap_pages_in(H_LOCKED);
    T(host_vm_decommit(a + 0x1000, 0x1000) == 0 && heap_pages_in(H_LOCKED) == locked);   /* half of the 0x1000..0x3000 extent */
    T(CAN_WRITE(a + 0x1000) && kperm(a + 0x1000) == 3 && B(a + 0x1800)[0] == 0);          /* no access before: read-write and zero now */
    T(host_vm_protect(a + 0x2000, 0x1000, 0) == 0);
    T(host_vm_decommit(a + 0x1000, 0x2000) == 0 && kstate(a + 0x1000) == A_FREE && heap_pages_in(H_LOCKED) == locked - 2);   /* the whole extent, one page no-access */
    T(host_vm_commit(a + 0x1000, 0x1000) == 0 && B(a + 0x1000)[0] == 0 && kperm(a + 0x1000) == 3);   /* recommit: a new block, fresh read-write */
    T(host_vm_decommit(a, 0) == 0);                                  /* size 0: nothing done */
    T(host_vm_decommit(a + 0x100, 0) == 0 && kstate(a) == A_ALIAS_CODE_DATA);   /* not the page the unaligned address is in */
    T(host_vm_decommit(~0ull - 0xFF, 0x1000) == FNVNX_HE_RANGE && host_vm_decommit(1, ~0ull) == FNVNX_HE_RANGE);   /* wraps */
    T(host_vm_decommit(a + 0x700000, 0x1000) == 0);                   /* committed nowhere: ignored */

    /* vm_release: not from the base -> BadInput; everything inside goes, the range can be reserved again */
    T(host_vm_release(a + 0x1000) == FNVNX_HE_BADINPUT && host_vm_release(0) == FNVNX_HE_BADINPUT && host_vm_release(~0ull) == FNVNX_HE_BADINPUT);
    T(host_vm_release(a) == 0 && g_live_resv == 2 && kstate(a) == A_FREE && kstate(a + 0x1000) == A_FREE);
    T(host_vm_release(a) == FNVNX_HE_BADINPUT);
    T(host_vm_commit(a, 0x1000) == FNVNX_HE_BADINPUT);
    T(host_vm_release(f) == 0);
    T(host_vm_reserve(lo, 0x100000) == lo);
    T(g_free_while_locked == 0 && g_unmapcode_with_views == 0);

    /* a kernel refusal reaches the caller and leaves nothing behind */
    u64 r2 = host_vm_reserve(0, 0x100000);
    g_force_mapcode = R_KERNEL(103);
    int l0 = heap_pages_in(H_NORMAL);
    T(host_vm_commit(r2, 0x2000) == R_KERNEL(103) && kstate(r2) == A_FREE && heap_pages_in(H_NORMAL) == l0 && !g_vm_ext[0].size);
    g_force_setperm = R_KERNEL(103);   /* the alias is built but cannot become data: it is undone, the block is given back */
    T(host_vm_commit(r2, 0x2000) == R_KERNEL(103) && kstate(r2) == A_FREE && heap_pages_in(H_LOCKED) == 0 && g_free_while_locked == 0);
    printf("host_vm: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
