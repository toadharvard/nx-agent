/* host_prot_test.c - VM_PROTECT and map_fixed on guest-owned pages (toolchain/host/host_vm.inc: host_vm_protect,
 * h_map_fixed, host_win_adopt) executed under qemu-aarch64 against the kernel double kdouble.h, whose
 * svcSetMemoryPermission / svcUnmapProcessCodeMemory model the kernel as proven at host_vm.inc K1..K5 (None, R and RW on
 * AliasCodeData in uniform runs; Stack, SharedCode and the Code states refused; unmap in any permission). A page without permission
 * really faults here (probe). Test names (hostapi.h table): protect_args, protect_commit, protect_mapfixed, protect_window,
 * mapfixed_args, unmapfixed_args, protect_partial_failure, protect_not_owned. */
#include "kdouble.h"
#include "fnvnx/hostapi.h"

static void logf_(const char *fmt, ...) { (void)fmt; }
static Handle g_own_process = OWN_HANDLE;
#include "../../../toolchain/host/host_vm.inc"
#undef free
#undef memalign
#define B(a) ((volatile uint8_t *)(uintptr_t)(a))
#define R_ 1ull
#define W_ 2ull
#define RW_ 3ull

static int runs(u64 a, u64 n)   /* number of kernel runs (blocks of one state and permission) in [a, a+n) */
{
    int k = 0;
    for (u64 p = a; p < a + n;) { MemoryInfo mi; u32 pi; svcQueryMemory(&mi, &pi, p); p = mi.addr + mi.size; k++; }
    return k;
}

int main(void)
{
    kdouble_init();
    u64 MF = g_alo + (40ull << 20);

    /* ---- the model's own premises (K1): the pages svcMapMemory makes cannot be reprotected, an alias of heap can */
    {
        void *b = t_memalign(PG, 0x2000);
        T(svcMapMemory((void *)(uintptr_t)(g_alo + 0x300000), b, 0x2000) == 0 && kstate(g_alo + 0x300000) == A_STACK);
        T(svcSetMemoryPermission((void *)(uintptr_t)(g_alo + 0x300000), 0x1000, Perm_R) == E_RANGE);   /* Stack: no FlagCanReprotect */
        T(svcUnmapMemory((void *)(uintptr_t)(g_alo + 0x300000), b, 0x2000) == 0);
        t_free(b);
    }

    /* ---- mapfixed_args: the checks of h_map_fixed in their order */
    {
        void *b = t_memalign(PG, 0x4000);
        u64 s = (u64)(uintptr_t)b;
        T(h_map_fixed(MF, 0, s) == FNVNX_HE_BADINPUT);                          /* size 0 */
        T(h_map_fixed(MF, 0x4000, 0) == FNVNX_HE_BADINPUT);                     /* no source */
        T(h_map_fixed(MF + 0x800, 0x4000, s) == FNVNX_HE_BADINPUT && h_map_fixed(MF, 0x4800, s) == FNVNX_HE_BADINPUT);   /* unaligned */
        T(h_map_fixed(MF, 0x4000, s + 0x800) == FNVNX_HE_BADINPUT);
        T(h_map_fixed(~0ull - 0xFFF, 0x2000, s) == FNVNX_HE_BADINPUT);          /* wraps */
        T(h_map_fixed(MF, 0x4000, s) == 0 && kstate(MF) == A_ALIAS_CODE_DATA && kperm(MF) == 3 && heap_pages_in(H_LOCKED) == 4);
        T(g_hst[hidx(s)] == H_LOCKED && !probe(s, 0));                         /* the heap block is inaccessible meanwhile */
        T(h_map_fixed(MF, 0x1000, s) == FNVNX_HE_ALREADY && h_map_fixed(MF - 0x1000, 0x2000, s) == FNVNX_HE_ALREADY);   /* occupied, half occupied */
        void *c = t_memalign(PG, 0x2000);
        T(h_map_fixed(g_alo + ARENA + 0x10000, 0x2000, (u64)(uintptr_t)c) == FNVNX_HE_ALREADY);   /* outside the arena: svcQueryMemory says heap */
        T(h_map_fixed(MF + 0x10000, 0x2000, s) == E_RANGE);                    /* src is not ordinary heap (it is locked): the kernel's result */
        T(heap_pages_in(H_LOCKED) == 4 && kstate(MF + 0x10000) == A_FREE);
        t_free(c);   /* b stays locked: a map_fixed source is inaccessible for the rest of the process */
    }

    /* ---- protect_args: order of the checks: address, size, wrap, prot, ownership */
    T(host_vm_protect(0x1001, 0x1000, 3) == FNVNX_HE_ADDR && host_vm_protect(0x1001, 0, 9) == FNVNX_HE_ADDR);
    T(host_vm_protect(0x1000, 0, 3) == FNVNX_HE_SIZE && host_vm_protect(0x1000, 0x800, 9) == FNVNX_HE_SIZE);
    T(host_vm_protect(~0ull - 0xFFF, 0x2000, 3) == FNVNX_HE_RANGE && host_vm_protect(0x1000, ~0ull - 0xFFF, 9) == FNVNX_HE_RANGE);   /* wraps beats prot */
    T(host_vm_protect(MF, 0x1000, 4) == FNVNX_HE_PERM && host_vm_protect(MF, 0x1000, W_) == FNVNX_HE_PERM && host_vm_protect(MF, 0x1000, 8) == FNVNX_HE_PERM && host_vm_protect(MF, 0x1000, ~0ull) == FNVNX_HE_PERM);   /* X and others */
    T(host_vm_protect(MF + 0x300000, 0x1000, 3) == FNVNX_HE_RANGE);             /* valid arguments, not guest memory */

    /* ---- protect_commit: R, RW and none on committed pages; a range over several runs and extents */
    u64 resv = host_vm_reserve(0, 0x100000);
    T(resv && host_vm_commit(resv, 0x4000) == 0 && host_vm_commit(resv + 0x4000, 0x4000) == 0 && g_vm_next == 2);   /* two adjacent extents */
    T(CAN_WRITE(resv));
    T(host_vm_protect(resv, 0x1000, R_) == 0 && CAN_READ(resv) && !CAN_WRITE(resv) && kperm(resv) == 1);
    T(host_vm_protect(resv + 0x1000, 0x1000, 0) == 0 && !CAN_READ(resv + 0x1000) && !CAN_WRITE(resv + 0x1000));
    T(CAN_WRITE(resv + 0x2000) && runs(resv, 0x4000) == 3);                    /* R | none | RW(2 pages) */
    T(host_vm_protect(resv, 0x8000, R_) == 0 && runs(resv, 0x8000) == 1 && !CAN_WRITE(resv + 0x7000) && CAN_READ(resv + 0x5000));   /* over runs and the extent border */
    T(host_vm_protect(resv + 0x3000, 0x2000, 0) == 0 && !CAN_READ(resv + 0x4000) && CAN_READ(resv + 0x5000) && runs(resv, 0x8000) == 3);
    T(host_vm_protect(resv, 0x8000, RW_) == 0 && CAN_WRITE(resv) && CAN_WRITE(resv + 0x7000) && runs(resv, 0x8000) == 1);
    T(host_vm_protect(resv, 0x8000, RW_) == 0);                                  /* already so: nothing to do */
    T(host_vm_protect(resv + 0x1000, 0x1000, W_) == FNVNX_HE_PERM && kperm(resv + 0x1000) == 3);   /* write-only: the kernel has none (K1) */
    /* a stack guard below a thread stack: the lowest page no access, the rest read-write, as alloc_va's users need */
    T(host_vm_protect(resv, 0x1000, 0) == 0 && !CAN_READ(resv) && CAN_WRITE(resv + 0x1000));
    T(host_vm_decommit(resv, 0x4000) == 0 && kstate(resv) == A_FREE);        /* a no-access page is decommitted as it is */
    T(host_vm_protect(resv, 0x1000, RW_) == FNVNX_HE_RANGE);                    /* committed nowhere now */
    T(host_vm_protect(resv + 0x4000, 0x4000, RW_) == 0 && host_vm_protect(resv + 0x3000, 0x2000, RW_) == FNVNX_HE_RANGE);   /* the hole is not guest-owned: all or nothing */
    T(kperm(resv + 0x4000) == 3 && runs(resv + 0x4000, 0x4000) == 1);

    /* ---- protect_mapfixed: the loader's image: R sections, RW sections, no-access gap pages; unmap in any protection */
    {
        void *b = t_memalign(PG, 0x8000);
        int L0 = heap_pages_in(H_LOCKED);
        u64 s = (u64)(uintptr_t)b, a = MF + 0x20000;
        memset(b, 0x5A, 0x8000);
        T(h_map_fixed(a, 0x8000, s) == 0);
        T(B(a)[0] == 0x5A && B(a + 0x7FFF)[0] == 0x5A);                          /* the guest's contents are the mapped contents (it zeroed them) */
        T(host_vm_protect(a, 0x2000, R_) == 0 && host_vm_protect(a + 0x2000, 0x1000, 0) == 0 && host_vm_protect(a + 0x3000, 0x2000, RW_) == 0);
        T(host_vm_protect(a + 0x5000, 0x3000, R_) == 0);
        T(CAN_READ(a) && !CAN_WRITE(a) && !CAN_READ(a + 0x2000) && CAN_WRITE(a + 0x3000) && CAN_READ(a + 0x6000) && !CAN_WRITE(a + 0x6000));
        T(runs(a, 0x8000) == 4);
        T(host_vm_commit(a, 0x1000) == FNVNX_HE_BADINPUT);                          /* not inside a reservation */
        T(host_vm_decommit(a, 0x1000) == FNVNX_HE_RANGE);                           /* a map_fixed page is not VM-committed memory */
        T(heap_pages_in(H_LOCKED) == L0 + 8);                                       /* R, none and RW pages together: the source stays locked */
    }

    /* ---- protect_not_owned: other guest-looking memory is refused before any svc: heap, a section backing, a view, the hole */
    {
        T(host_vm_protect((u64)(uintptr_t)g_heap, 0x1000, RW_) == FNVNX_HE_RANGE);
        void *b = t_memalign(PG, 0x2000);
        u64 cb = g_alo + ARENA / 2 + 0x200000;   /* a section backing: AliasCodeData at a virtmem code address, owned by no extent */
        T(svcMapProcessCodeMemory(OWN_HANDLE, cb, (u64)(uintptr_t)b, 0x2000) == 0 && svcSetProcessMemoryPermission(OWN_HANDLE, cb, 0x2000, Perm_Rw) == 0);
        int calls = g_setperm_calls;
        T(host_vm_protect(cb, 0x1000, R_) == FNVNX_HE_RANGE && g_setperm_calls == calls && kperm(cb) == 3);
        u64 v = g_alo + 0x500000;   /* a view: SharedCode */
        T(svcMapProcessMemory((void *)(uintptr_t)v, OWN_HANDLE, cb, 0x1000) == 0 && host_vm_protect(v, 0x1000, R_) == FNVNX_HE_RANGE && kperm(v) == 3);
        T(svcSetMemoryPermission((void *)(uintptr_t)v, 0x1000, Perm_R) == E_RANGE);   /* and the kernel agrees: SharedCode has no CanReprotect */
        T(svcUnmapProcessMemory((void *)(uintptr_t)v, OWN_HANDLE, cb, 0x1000) == 0 && svcUnmapProcessCodeMemory(OWN_HANDLE, cb, (u64)(uintptr_t)b, 0x2000) == 0);
        t_free(b);
    }

    /* ---- protect_partial_failure: the kernel refuses a run: its result, the runs before it keep their new protection (hostapi.h) */
    {
        /* [R R | RW RW]: the first kernel call of the range fails -> its result, nothing changed; then the kernel refuses the SECOND run
         * (a one-shot forcer fires on the next call, so the first run is made by a call that succeeds first) */
        T(host_vm_protect(resv + 0x4000, 0x2000, R_) == 0 && host_vm_protect(resv + 0x6000, 0x2000, RW_) == 0 && runs(resv + 0x4000, 0x4000) == 2);
        g_force_setmemperm = R_KERNEL(103);
        T(host_vm_protect(resv + 0x4000, 0x4000, 0) == R_KERNEL(103) && kperm(resv + 0x4000) == 1 && kperm(resv + 0x6000) == 3);
        int n = g_setperm_calls;
        T(host_vm_protect(resv + 0x4000, 0x4000, 0) == 0 && g_setperm_calls == n + 2 && !CAN_READ(resv + 0x4000) && !CAN_READ(resv + 0x7000));   /* two runs, two calls */
        T(host_vm_protect(resv + 0x6000, 0x2000, RW_) == 0 && host_vm_protect(resv + 0x4000, 0x2000, RW_) == 0 && runs(resv + 0x4000, 0x4000) == 1);
    }

    /* ---- protect_window: S10's claimed windows (state Stack: the kernel refuses to reprotect them) are converted once by host_win_adopt */
    {
        u64 wlo = g_alo + (48ull << 20), whi = wlo + 0x40000;   /* the image range, as S10 maps it: svcMapMemory of a heap block */
        void *b = t_memalign(PG, 0x40000);
        T(svcMapMemory((void *)(uintptr_t)wlo, b, 0x40000) == 0);
        memset((void *)(uintptr_t)wlo, 0x33, 0x40000);
        T(host_vm_protect(wlo, 0x1000, R_) == FNVNX_HE_RANGE && h_map_fixed(wlo, 0x1000, 0) == FNVNX_HE_BADINPUT);   /* not adopted: not guest-owned */
        /* adopt: the claim is released (backing = the heap pages, RW again), then mapped as AliasCodeData */
        T(svcUnmapMemory((void *)(uintptr_t)wlo, b, 0x40000) == 0);
        T(host_win_adopt(wlo, whi, (u64)(uintptr_t)b) == 0 && kstate(wlo) == A_ALIAS_CODE_DATA && kstate(whi - PG) == A_ALIAS_CODE_DATA);
        T(B(wlo)[0x100] == 0x33);                                               /* contents kept across the conversion */
        T(host_vm_protect(wlo, 0x2000, R_) == 0 && host_vm_protect(wlo + 0x2000, 0x1000, 0) == 0 && host_vm_protect(wlo + 0x3000, 0x3D000, RW_) == 0);
        T(CAN_READ(wlo) && !CAN_WRITE(wlo) && !CAN_READ(wlo + 0x2000) && CAN_WRITE(wlo + 0x3000));
        T(host_vm_protect(wlo - 0x1000, 0x2000, RW_) == FNVNX_HE_RANGE && host_vm_protect(whi - 0x1000, 0x2000, RW_) == FNVNX_HE_RANGE);   /* leaves the window */
        T(host_vm_decommit(wlo, 0x1000) == FNVNX_HE_RANGE);                      /* not VM memory */
        /* map_fixed inside: read-write and zeroed whatever it was, src not looked at */
        B(wlo + 0x3000)[5] = 7;
        T(h_map_fixed(wlo, 0x4000, 0) == 0 && CAN_WRITE(wlo) && CAN_WRITE(wlo + 0x2000) && B(wlo + 0x3000)[5] == 0 && B(wlo)[0x100] == 0);
        T(h_map_fixed(wlo + 0x800, 0x1000, 0) == FNVNX_HE_BADINPUT && h_map_fixed(wlo, 0x80000, 0) == FNVNX_HE_BADINPUT);   /* unaligned; leaves the window: normal path needs a src */
        B(wlo + 0x1000)[0] = 9;
        T(host_vm_protect(wlo + 0x1000, 0x1000, 0) == 0 && h_map_fixed(wlo, 0x4000, 0x123000) == 0 && CAN_WRITE(wlo + 0x1000) && B(wlo + 0x1000)[0] == 0);
        T(kstate(wlo) == A_ALIAS_CODE_DATA && host_win_adopt(wlo + 0x100000, wlo + 0x110000, 0) != 0 );   /* a backing that is no heap: the kernel refuses */
    }

    /* ---- a map_fixed page inside a reservation: the VM services do not take it */
    {
        u64 r = host_vm_reserve(0, 0x20000);
        void *b = t_memalign(PG, 0x1000);
        T(h_map_fixed(r + 0x4000, 0x1000, (u64)(uintptr_t)b) == 0);
        T(host_vm_commit(r, 0x8000) == FNVNX_HE_RANGE && host_vm_commit(r, 0x4000) == 0);
        T(host_vm_decommit(r, 0x8000) == FNVNX_HE_RANGE && host_vm_release(r) == FNVNX_HE_RANGE);
    }
    /* ---- the map_fixed table: FNVNX_MF_MAX live ranges (three are taken by the blocks above: mapfixed_args, protect_mapfixed, the
     * reservation), then NoMem; a map_fixed range is never given back */
    {
        static u64 srcs[FNVNX_MF_MAX];
        enum { USED = 3, FREE = FNVNX_MF_MAX - USED };
        void *b = t_memalign(PG, 0x1000 * (FREE + 2));
        T(b != NULL);
        u64 s0 = (u64)(uintptr_t)b, base = g_alo + (80ull << 20);
        int ok = 1;
        for (int i = 0; i < FREE; i++) { srcs[i] = s0 + (u64)i * 0x1000; ok &= h_map_fixed(base + (u64)i * 0x2000, 0x1000, srcs[i]) == 0; }
        T(ok);
        T(h_map_fixed(base + 0x400000, 0x1000, s0 + (u64)FREE * 0x1000) == FNVNX_HE_NOMEM && kstate(base + 0x400000) == A_FREE);
        T(g_free_while_locked == 0 && g_unmapcode_with_views == 0);
    }
    T(g_unmapcode_with_views == 0);
    printf("host_prot: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
