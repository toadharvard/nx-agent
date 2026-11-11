#define _GNU_SOURCE
/* host_section_test.c - memory sections with shared views (toolchain/host/host_section.inc, with host_vm.inc) executed
 * under qemu-aarch64 against the kernel double kdouble.h (a MODEL OF MESOSPHERE as proven at the cited lines; the proofs P1..P5
 * are in host_section.inc's header). The model is exact where it matters for the host's bookkeeping and nothing else; it is NOT
 * the kernel. What this test proves is that the host's bookkeeping, error mapping and its interplay with host_vm.inc are right
 * GIVEN those premises. */
#include "kdouble.h"
#include "fnvnx/hostapi.h"

static void logf_(const char *fmt, ...) { (void)fmt; }
static Handle g_own_process = OWN_HANDLE;
#include "../../../toolchain/host/host_vm.inc"
#include "../../../toolchain/host/host_section.inc"
#undef free
#undef memalign

#define B(a) ((volatile uint8_t *)(uintptr_t)(a))

int main(void)
{
    kdouble_init();

    /* P1 premises of the model itself: a plain heap block is no source, a pseudo handle is no handle, a view is no source */
    {
        void *blk = t_memalign(PG, 0x2000);
        T(svcMapProcessMemory((void *)(uintptr_t)(g_alo + 0x100000), OWN_HANDLE, (u64)(uintptr_t)blk, 0x2000) == E_RANGE);
        t_free(blk);
    }

    /* create: errors, then a section */
    T(host_section_create(0) == ERR(FNVNX_SEC_E_SIZE));
    T(host_section_create(0x1001) == ERR(FNVNX_SEC_E_SIZE));
    T(host_section_create(0x80001000ull) == ERR(FNVNX_SEC_E_SIZE));
    u64 id = host_section_create(0x10000);
    T(id == 1 && heap_pages_in(H_LOCKED) == 16 && g_live_resv == 1);
    T(g_sec[0].live && kstate(g_sec[0].base) == A_ALIAS_CODE_DATA);   /* P1: the converted state */

    /* views: host-chosen and fixed (inside a VM reservation), overlapping offsets 0x2000..0x4000 */
    u64 va = host_section_map(id, 0, 0, 0x4000);
    T(va != 0 && !(va >> 63) && !(va & 0xFFFF) && kstate(va) == A_SHARED);
    u64 resv = host_vm_reserve(0, 0x40000);
    u64 fx = resv + 0x10000;
    u64 vb = host_section_map(id, fx, 0x2000, 0x4000);
    T(vb == fx && kstate(fx) == A_SHARED && kstate(fx - 0x1000) == A_FREE);
    for (int i = 0; i < 0x4000; i++) T(B(va)[i] == 0 && B(vb)[i] == 0);   /* zeroed */
    B(va)[0x2000] = 0xA5; B(va)[0x3FFF] = 0x5A;
    T(B(vb)[0] == 0xA5 && B(vb)[0x1FFF] == 0x5A);                       /* A's offset 0x2000 is B's 0 */
    B(vb)[0x1000] = 0x77; B(vb)[0x3FFF] = 0x33;
    T(B(va)[0x3000] == 0x77 && B(vb)[0x3FFF] == 0x33 && B(va)[0x3FFF] == 0x5A);   /* both directions; B's tail is beyond A */

    /* a view of a sub-range starts at the right page; a view of everything */
    u64 vd = host_section_map(id, 0, 0, 0x10000);
    for (int p = 0; p < 16; p++) B(vd)[p * 0x1000 + 5] = (uint8_t)(0x10 + p);
    u64 vc = host_section_map(id, 0, 0x8000, 0x1000);
    T(vc && B(vc)[5] == 0x18 && B(vd)[0x2000] == 0xA5 && B(va)[0x2005] == 0x12);
    /* writes through a view that overlaps two others */
    B(vc)[6] = 0xEE;
    T(B(vd)[0x8006] == 0xEE);

    /* map errors */
    T(host_section_map(id, 0, 0, 0) == ERR(FNVNX_SEC_E_SIZE) && host_section_map(id, 0, 0, 0x1800) == ERR(FNVNX_SEC_E_SIZE));
    T(host_section_map(id, 0, 0x800, 0x1000) == ERR(FNVNX_SEC_E_ALIGN) && host_section_map(id, resv + 0x800, 0, 0x1000) == ERR(FNVNX_SEC_E_ALIGN));
    T(host_section_map(99, 0, 0, 0x1000) == ERR(FNVNX_SEC_E_ID) && host_section_map(0, 0, 0, 0x1000) == ERR(FNVNX_SEC_E_ID));
    T(host_section_map(id, 0, 0xF000, 0x2000) == ERR(FNVNX_SEC_E_RANGE) && host_section_map(id, 0, 0, 0x11000) == ERR(FNVNX_SEC_E_RANGE));
    T(host_section_map(id, 0, ~0xFFFull & ~0ull, 0x1000) == ERR(FNVNX_SEC_E_RANGE));   /* off + size wraps */
    T(host_section_map(id, g_alo + ARENA / 2 + 0x300000, 0, 0x1000) == ERR(FNVNX_SEC_E_REGION));   /* free, but not in a reservation */
    T(host_section_map(id, fx, 0, 0x1000) == ERR(FNVNX_SEC_E_RANGE));          /* over a view */
    T(host_section_map(id, fx + 0x3000, 0, 0x2000) == ERR(FNVNX_SEC_E_RANGE)); /* half over a view */
    T(host_section_map(id, resv + 0x40000 - 0x1000, 0, 0x2000) == ERR(FNVNX_SEC_E_REGION));   /* leaves the reservation */

    /* VM services and views */
    T(host_vm_commit(fx, 0x1000) == FNVNX_HE_RANGE && host_vm_commit(resv, 0x20000) == FNVNX_HE_RANGE);
    T(g_vm_next == 0 && kstate(resv) == A_FREE);                                 /* refused before anything was committed */
    T(host_vm_commit(resv, 0x8000) == 0 && kstate(resv) == A_ALIAS_CODE_DATA);              /* a range without a view commits */
    T(host_section_map(id, resv + 0x4000, 0, 0x1000) == ERR(FNVNX_SEC_E_RANGE)); /* over committed memory: kernel refuses */
    T(host_vm_decommit(fx, 0x1000) == FNVNX_HE_RANGE && host_vm_decommit(resv, 0x20000) == FNVNX_HE_RANGE);
    T(host_vm_decommit(resv, 0x8000) == 0);
    T(host_vm_release(resv) == FNVNX_HE_RANGE && host_vm_commit(resv, 0x1000) == 0);   /* reservation lives on */
    T(B(vb)[0x1000] == 0x77);

    /* unmap: exactly a view; the others and the data stay */
    T(host_section_unmap(va, 0x3000) == ERR(FNVNX_SEC_E_RANGE) && host_section_unmap(va + 0x1000, 0x1000) == ERR(FNVNX_SEC_E_RANGE));
    T(host_section_unmap(0x1234000, 0x1000) == ERR(FNVNX_SEC_E_RANGE));
    int resv_before = g_live_resv;
    T(host_section_unmap(va, 0x4000) == 0 && kstate(va) == A_FREE && g_live_resv == resv_before - 1);   /* host-chosen: its range is released */
    T(host_section_unmap(va, 0x4000) == ERR(FNVNX_SEC_E_RANGE));
    T(B(vb)[0] == 0xA5 && B(vb)[0x1000] == 0x77 && B(vd)[0x2000] == 0xA5 && B(vc)[5] == 0x18);
    B(vb)[2] = 0x99;
    T(B(vd)[0x2002] == 0x99);
    T(host_section_unmap(fx, 0x4000) == 0 && host_vm_release(resv) == 0);          /* now the reservation can go */

    /* free with live views is deferred: the id dies, the views and the pages live; the last unmap releases */
    T(host_section_free(id) == 0 && host_section_free(id) == ERR(FNVNX_SEC_E_ID));
    T(host_section_map(id, 0, 0, 0x1000) == ERR(FNVNX_SEC_E_ID));
    T(heap_pages_in(H_LOCKED) == 16 && B(vd)[0x2000] == 0xA5);
    T(host_section_unmap(vc, 0x1000) == 0 && heap_pages_in(H_LOCKED) == 16);
    T(host_section_unmap(vd, 0x10000) == 0 && heap_pages_in(H_LOCKED) == 0 && heap_pages_in(H_NORMAL) == 0 && g_live_resv == 0);
    T(g_unmapcode_with_views == 0 && g_free_while_locked == 0 && !g_sec[0].id);
    /* ids are not reused; a section without views is released by free */
    u64 id2 = host_section_create(0x2000);
    T(id2 == 2 && heap_pages_in(H_LOCKED) == 2);
    T(host_section_free(id2) == 0 && heap_pages_in(H_LOCKED) == 0 && g_live_resv == 0 && host_section_free(id2) == ERR(FNVNX_SEC_E_ID));

    /* kernel refusals reach the caller and leave nothing behind */
    u64 id3 = host_section_create(0x4000);
    g_force_mapproc = R_KERNEL(103);
    int rv = g_live_resv;
    T(host_section_map(id3, 0, 0, 0x1000) == ERR(0xCE01) && g_live_resv == rv && !g_sec_view[0].sec && g_sec[0].views == 0);
    u64 v3 = host_section_map(id3, 0, 0, 0x1000);
    T(v3 != 0 && g_sec[0].views == 1);
    g_force_unmapproc = R_KERNEL(106);
    T(host_section_unmap(v3, 0x1000) == ERR(FNVNX_SEC_E_RANGE) && g_sec[0].views == 1 && kstate(v3) == A_SHARED);   /* still a view */
    T(host_section_unmap(v3, 0x1000) == 0 && host_section_free(id3) == 0);
    g_force_setperm = R_KERNEL(103);
    T(host_section_create(0x4000) == ERR(0xCE01) && heap_pages_in(H_LOCKED) == 0 && heap_pages_in(H_NORMAL) == 0 && g_live_resv == 0);
    T(g_free_while_locked == 0 && g_unmapcode_with_views == 0);

    /* capacities: 128 sections, 512 views; the next one is refused and nothing is lost */
    u64 ids[FNVNX_SEC_SECTIONS + 1];
    int ok = 1;
    for (int i = 0; i < FNVNX_SEC_SECTIONS; i++) { ids[i] = host_section_create(0x1000); ok &= ids[i] > 0 && !(ids[i] >> 63); }
    T(ok && host_section_create(0x1000) == ERR(FNVNX_SEC_E_FULL));
    u64 vs[FNVNX_SEC_VIEWS];
    for (int i = 0; i < FNVNX_SEC_VIEWS; i++) { vs[i] = host_section_map(ids[0], 0, 0, 0x1000); ok &= vs[i] != 0 && !(vs[i] >> 63); }
    T(ok && host_section_map(ids[0], 0, 0, 0x1000) == ERR(FNVNX_SEC_E_FULL));
    B(vs[0])[7] = 42;
    T(B(vs[FNVNX_SEC_VIEWS - 1])[7] == 42);   /* 512 views of one page */
    for (int i = 0; i < FNVNX_SEC_SECTIONS; i++) host_section_free(ids[i]);   /* section 0: deferred */
    T(heap_pages_in(H_LOCKED) == 1);
    for (int i = 0; i < FNVNX_SEC_VIEWS; i++) ok &= host_section_unmap(vs[i], 0x1000) == 0;
    T(ok && heap_pages_in(H_LOCKED) == 0 && heap_pages_in(H_NORMAL) == 0 && g_live_resv == 0 && g_free_while_locked == 0 && g_unmapcode_with_views == 0);
    int busy = 0;
    for (int i = 0; i < FNVNX_SEC_SECTIONS; i++) busy += g_sec[i].id != 0;
    for (int i = 0; i < FNVNX_SEC_VIEWS; i++) busy += g_sec_view[i].sec != 0;
    T(busy == 0);
    printf("host_section: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
