#define _GNU_SOURCE
/* host_section_test.c - memory sections with shared views (toolchain/host/host_section.inc, with host_vm.inc) executed
 * under qemu-aarch64 against a MODEL OF THE KERNEL.
 *
 * THE DOUBLES MODEL MESOSPHERE AS PROVEN AT THE CITED LINES (Atmosphere 4e3f37038d9e82dde7054897213ab32b2ecf651f,
 * libraries/libmesosphere; host_section.inc's header has the proofs P1..P5). The model is exact where it matters for the
 * host's bookkeeping and nothing else: every kernel block is a page here with a state, and physical pages are real
 * shared memory (a memfd mapped at several addresses with MAP_SHARED), so two views of a section really see each other's
 * writes and an unmapped or not-mapped page really faults. Premises (state, check, source line):
 *   svcMapProcessCodeMemory     src: heap Normal, RW, not locked (kern_k_page_table_base.cpp:1010-1013); dst: Free and inside the
 *                               alias-code region (svc:151, :1014); src becomes Locked/not mapped, dst AliasCode, not user
 *                               accessible (:1034-1058)
 *   svcSetProcessMemoryPermission  needs FlagCode (AliasCode has it, AliasCodeData has not, :1665): AliasCode -> AliasCodeData RW (:1685)
 *   svcMapProcessMemory         a REAL handle (GetObjectWithoutPseudoHandle, svc:73); src every page FlagCanMapProcess =
 *                               AliasCode or AliasCodeData (hpp:83,84; Normal, SharedCode, Stack have not; :2528-2542); dst
 *                               inside the alias-code region else 0xDA01 (svc:82), every page Free else 0xD401 (:2447-2460);
 *                               dst becomes SharedCode RW (svc:98), source untouched; the page gets one more reference
 *   svcUnmapProcessMemory       dst every page SharedCode and the same physical page as src, src CanMapProcess (:4836-4850)
 *   svcUnmapProcessCodeMemory   src Locked Normal, dst CanCodeAlias, same pages (:1075-1100): heap again, dst Free. A reference
 *                               count > 1 on a page here means views still map it: counted in g_unmapcode_with_views, which
 *                               the host must keep at 0 (P5: the backing is released only after its last view)
 *   svcMapMemory/svcUnmapMemory the heap block moves to the stack region (host_vm.inc's extents); pages of the heap block
 *                               are inaccessible meanwhile
 * The model is NOT the kernel: whether the kernel really behaves so is the proof in host_section.inc, not this test. What
 * this test proves is that the host's bookkeeping, error mapping and its interplay with host_vm.inc are right GIVEN those premises. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>
#include <unistd.h>
#include <sys/mman.h>
#include <switch.h>
#include "fnvnx/hostapi.h"

#define PG 0x1000ull
#define ARENA (128ull << 20)
#define HEAP_PAGES 4096
enum { A_FREE, A_STACK, A_ALIAS_CODE, A_ALIAS_CODE_DATA, A_SHARED };
enum { H_FREE, H_NORMAL, H_LOCKED, H_MAPPED_OUT };

static uint8_t *g_arena, *g_heap;
static u64 g_alo;
static int g_memfd;
static uint8_t g_ast[ARENA / PG];      /* arena page state */
static uint32_t g_aphys[ARENA / PG];   /* heap page index backing an arena page */
static uint8_t g_hst[HEAP_PAGES];
static uint16_t g_hrefs[HEAP_PAGES];   /* arena pages mapping the heap page */
static int g_fail, g_live_resv, g_unmapcode_with_views, g_free_while_locked;
static u64 g_resv[4096][2];
static Result g_force_mapproc, g_force_setperm, g_force_unmapproc;   /* next call of that svc fails with this */

#define T(c) do { if (!(c)) { g_fail++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)
#define PGI(a) (((a) - g_alo) / PG)
#define IN_ARENA(a, n) ((a) >= g_alo && (a) + (n) <= g_alo + ARENA && (a) + (n) > (a))
#define R_KERNEL(d) MAKERESULT(Module_Kernel, d)
#define E_RANGE R_KERNEL(106)
#define E_REGION R_KERNEL(109)
#define E_HANDLE R_KERNEL(114)
#define OWN_HANDLE 0x1234u

/* ---- the heap (memalign / free of the .inc files go here) ---- */
static void *t_memalign(size_t al, size_t size)
{
    (void)al;
    size_t n = (size + PG - 1) / PG;
    for (size_t i = 0; i + n <= HEAP_PAGES; i++) {
        size_t k = 0;
        while (k < n && g_hst[i + k] == H_FREE) k++;
        if (k == n) { memset(&g_hst[i], H_NORMAL, n); return g_heap + i * PG; }
        i += k;
    }
    return NULL;
}
static void t_free(void *p)
{
    if (!p) return;
    size_t i = ((uint8_t *)p - g_heap) / PG;
    /* the extent length is not tracked: free the run of pages that are Normal (blocks are separated by free pages in the tests) */
    if (g_hst[i] != H_NORMAL) { g_free_while_locked++; return; }
    for (; i < HEAP_PAGES && g_hst[i] == H_NORMAL; i++) g_hst[i] = H_FREE;
}
#define memalign t_memalign
#define free t_free

/* ---- virtmem: free ranges of the arena, the lower half stands for the stack region, the upper for the rest of the alias-code region */
void mutexLock(Mutex *m) { (void)m; }
void mutexUnlock(Mutex *m) { (void)m; }
void virtmemLock(void) {}
void virtmemUnlock(void) {}
static int range_free(u64 a, u64 n)
{
    for (u64 p = a; p < a + n; p += PG) if (g_ast[PGI(p)] != A_FREE) return 0;
    for (int i = 0; i < 4096; i++) if (g_resv[i][1] && a < g_resv[i][0] + g_resv[i][1] && g_resv[i][0] < a + n) return 0;
    return 1;
}
static void *find(u64 lo, u64 hi, size_t size)
{
    size = (size + PG - 1) & ~(PG - 1);
    for (u64 c = lo; c + size <= hi; c += 0x10000) if (range_free(c, size)) return (void *)(uintptr_t)c;
    return NULL;
}
void *virtmemFindStack(size_t size, size_t guard) { (void)guard; return find(g_alo, g_alo + ARENA / 2, size); }
void *virtmemFindCodeMemory(size_t size, size_t guard) { (void)guard; return find(g_alo + ARENA / 2, g_alo + ARENA, size); }
VirtmemReservation *virtmemAddReservation(void *mem, size_t size)
{
    for (int i = 0; i < 4096; i++) if (!g_resv[i][1]) { g_resv[i][0] = (u64)(uintptr_t)mem; g_resv[i][1] = size; g_live_resv++; return (VirtmemReservation *)(uintptr_t)(i + 1); }
    return NULL;
}
void virtmemRemoveReservation(VirtmemReservation *rv) { g_resv[(uintptr_t)rv - 1][1] = 0; g_live_resv--; }

/* ---- the kernel ---- */
static void amap(u64 a, u32 hp, int prot)   /* arena page a <- heap page hp, shared */
{
    void *r = mmap((void *)(uintptr_t)a, PG, prot, MAP_SHARED | MAP_FIXED, g_memfd, (off_t)hp * PG);
    if (r == MAP_FAILED) { printf("FAIL mmap\n"); exit(2); }
    g_hrefs[hp]++;
}
static void aunmap(u64 a)
{
    g_hrefs[g_aphys[PGI(a)]]--;
    mmap((void *)(uintptr_t)a, PG, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    g_ast[PGI(a)] = A_FREE;
}
static int heap_range(u64 a, u64 n, int st)
{
    if (a < (u64)(uintptr_t)g_heap || a + n > (u64)(uintptr_t)g_heap + HEAP_PAGES * PG) return 0;
    for (u64 p = a; p < a + n; p += PG) if (g_hst[(p - (u64)(uintptr_t)g_heap) / PG] != st) return 0;
    return 1;
}
static int arena_all(u64 a, u64 n, int s1, int s2)
{
    for (u64 p = a; p < a + n; p += PG) if (g_ast[PGI(p)] != s1 && g_ast[PGI(p)] != s2) return 0;
    return 1;
}
static u32 hidx(u64 a) { return (u32)((a - (u64)(uintptr_t)g_heap) / PG); }

Result svcQueryMemory(MemoryInfo *mi, u32 *pi, u64 addr)
{
    memset(mi, 0, sizeof *mi);
    *pi = 0;
    if (!IN_ARENA(addr, 1)) { mi->addr = addr & ~(PG - 1); mi->size = PG; mi->type = MemType_Heap; return 0; }
    static const u32 ty[] = { MemType_Unmapped, MemType_MappedMemory, MemType_ModuleCodeStatic, MemType_ModuleCodeMutable, MemType_ProcessMem };
    u64 i = PGI(addr), lo = i, hi = i;
    while (lo > 0 && g_ast[lo - 1] == g_ast[i]) lo--;
    while (hi + 1 < ARENA / PG && g_ast[hi + 1] == g_ast[i]) hi++;
    mi->addr = g_alo + lo * PG;
    mi->size = (hi - lo + 1) * PG;
    mi->type = ty[g_ast[i]];
    return 0;
}
Result svcMapMemory(void *dst, void *src, u64 size)
{
    u64 d = (u64)(uintptr_t)dst, s = (u64)(uintptr_t)src;
    if (!IN_ARENA(d, size) || d >= g_alo + ARENA / 2 || !heap_range(s, size, H_NORMAL)) return E_RANGE;   /* stack region = lower half */
    if (!arena_all(d, size, A_FREE, A_FREE)) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) {
        g_hst[hidx(s + k)] = H_MAPPED_OUT;
        mprotect((void *)(uintptr_t)(s + k), PG, PROT_NONE);
        g_aphys[PGI(d + k)] = hidx(s + k);
        g_ast[PGI(d + k)] = A_STACK;
        amap(d + k, hidx(s + k), PROT_READ | PROT_WRITE);
    }
    return 0;
}
Result svcUnmapMemory(void *dst, void *src, u64 size)
{
    u64 d = (u64)(uintptr_t)dst, s = (u64)(uintptr_t)src;
    if (!IN_ARENA(d, size) || !heap_range(s, size, H_MAPPED_OUT) || !arena_all(d, size, A_STACK, A_STACK)) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) {
        if (g_aphys[PGI(d + k)] != hidx(s + k)) return E_RANGE;
        aunmap(d + k);
        g_hst[hidx(s + k)] = H_NORMAL;
        mprotect((void *)(uintptr_t)(s + k), PG, PROT_READ | PROT_WRITE);
    }
    return 0;
}
static int is_real(Handle h) { return h == OWN_HANDLE; }
Result svcMapProcessCodeMemory(Handle proc, u64 dst, u64 src, u64 size)
{
    if (!is_real(proc)) return E_HANDLE;
    if (!IN_ARENA(dst, size)) return E_REGION;
    if (!heap_range(src, size, H_NORMAL) || !arena_all(dst, size, A_FREE, A_FREE)) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) {
        g_hst[hidx(src + k)] = H_LOCKED;
        mprotect((void *)(uintptr_t)(src + k), PG, PROT_NONE);
        g_aphys[PGI(dst + k)] = hidx(src + k);
        g_ast[PGI(dst + k)] = A_ALIAS_CODE;
        amap(dst + k, hidx(src + k), PROT_NONE);   /* kernel read / not mapped: no user access */
    }
    return 0;
}
Result svcSetProcessMemoryPermission(Handle proc, u64 addr, u64 size, u32 perm)
{
    if (g_force_setperm) { Result r = g_force_setperm; g_force_setperm = 0; return r; }
    if (!is_real(proc) || perm != Perm_Rw) return E_HANDLE;
    if (!IN_ARENA(addr, size) || !arena_all(addr, size, A_ALIAS_CODE, A_ALIAS_CODE)) return E_RANGE;   /* FlagCode */
    for (u64 k = 0; k < size; k += PG) {
        g_ast[PGI(addr + k)] = A_ALIAS_CODE_DATA;
        mprotect((void *)(uintptr_t)(addr + k), PG, PROT_READ | PROT_WRITE);
    }
    return 0;
}
Result svcUnmapProcessCodeMemory(Handle proc, u64 dst, u64 src, u64 size)
{
    if (!is_real(proc)) return E_HANDLE;
    if (!IN_ARENA(dst, size) || !heap_range(src, size, H_LOCKED) || !arena_all(dst, size, A_ALIAS_CODE, A_ALIAS_CODE_DATA)) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) if (g_aphys[PGI(dst + k)] != hidx(src + k)) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) if (g_hrefs[hidx(src + k)] > 1) g_unmapcode_with_views++;
    for (u64 k = 0; k < size; k += PG) {
        aunmap(dst + k);
        g_hst[hidx(src + k)] = H_NORMAL;
        mprotect((void *)(uintptr_t)(src + k), PG, PROT_READ | PROT_WRITE);
    }
    return 0;
}
Result svcMapProcessMemory(void *dstp, Handle proc, u64 src, u64 size)
{
    u64 dst = (u64)(uintptr_t)dstp;
    if (g_force_mapproc) { Result r = g_force_mapproc; g_force_mapproc = 0; return r; }
    if (!is_real(proc)) return E_HANDLE;                                       /* GetObjectWithoutPseudoHandle */
    if (!IN_ARENA(src, size) || !arena_all(src, size, A_ALIAS_CODE, A_ALIAS_CODE_DATA)) return E_RANGE;   /* FlagCanMapProcess */
    if (!IN_ARENA(dst, size)) return E_REGION;                                  /* CanContain(SharedCode) */
    if (!arena_all(dst, size, A_FREE, A_FREE)) return E_RANGE;                  /* CheckMemoryState Free */
    for (u64 k = 0; k < size; k += PG) {
        g_aphys[PGI(dst + k)] = g_aphys[PGI(src + k)];
        g_ast[PGI(dst + k)] = A_SHARED;
        amap(dst + k, g_aphys[PGI(src + k)], PROT_READ | PROT_WRITE);          /* alias permission fixed UserReadWrite */
    }
    return 0;
}
Result svcUnmapProcessMemory(void *dstp, Handle proc, u64 src, u64 size)
{
    u64 dst = (u64)(uintptr_t)dstp;
    if (g_force_unmapproc) { Result r = g_force_unmapproc; g_force_unmapproc = 0; return r; }
    if (!is_real(proc)) return E_HANDLE;
    if (!IN_ARENA(dst, size) || !arena_all(dst, size, A_SHARED, A_SHARED)) return E_RANGE;
    if (!IN_ARENA(src, size) || !arena_all(src, size, A_ALIAS_CODE, A_ALIAS_CODE_DATA)) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) if (g_aphys[PGI(dst + k)] != g_aphys[PGI(src + k)]) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) aunmap(dst + k);
    return 0;
}

static Handle g_own_process = OWN_HANDLE;
#include "../../../toolchain/host/host_vm.inc"
#include "../../../toolchain/host/host_section.inc"
#undef free
#undef memalign

static u64 ERR(u32 c) { return (u64)(-(s64)c); }
#define B(a) ((volatile uint8_t *)(uintptr_t)(a))
static int kstate(u64 a) { return g_ast[PGI(a)]; }
static int heap_pages_in(int st) { int n = 0; for (int i = 0; i < HEAP_PAGES; i++) n += g_hst[i] == st; return n; }

int main(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    g_memfd = memfd_create("heap", 0);
    T(g_memfd >= 0 && ftruncate(g_memfd, HEAP_PAGES * PG) == 0);
    g_heap = mmap(NULL, HEAP_PAGES * PG, PROT_READ | PROT_WRITE, MAP_SHARED, g_memfd, 0);
    g_arena = mmap((void *)0x20000000, ARENA, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (g_arena == MAP_FAILED) g_arena = mmap((void *)0x60000000, ARENA, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    T(g_arena != MAP_FAILED && g_heap != MAP_FAILED);
    g_alo = (u64)(uintptr_t)g_arena;

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
    T(host_vm_commit(fx, 0x1000) == FNVNX_VM_E_VIEW && host_vm_commit(resv, 0x20000) == FNVNX_VM_E_VIEW);
    T(g_vm_next == 0 && kstate(resv) == A_FREE);                                 /* refused before anything was committed */
    T(host_vm_commit(resv, 0x8000) == 0 && kstate(resv) == A_STACK);              /* a range without a view commits */
    T(host_section_map(id, resv + 0x4000, 0, 0x1000) == ERR(FNVNX_SEC_E_RANGE)); /* over committed memory: kernel refuses */
    T(host_vm_decommit(fx, 0x1000) == FNVNX_VM_E_VIEW && host_vm_decommit(resv, 0x20000) == FNVNX_VM_E_VIEW);
    T(host_vm_decommit(resv, 0x8000) == 0);
    T(host_vm_release(resv) == FNVNX_VM_E_VIEW && host_vm_commit(resv, 0x1000) == 0);   /* reservation lives on */
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
