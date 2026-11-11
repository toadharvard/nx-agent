/* kdouble.h - the kernel double of tests/s4/backends: a MODEL OF MESOSPHERE (Atmosphere 4e3f37038d9e82dde7054897213ab32b2ecf651f,
 * libraries/libmesosphere) as proven at the cited lines, shared by host_section_test.c, host_vm_test.c, host_prot_test.c,
 * host_v3_test.c and host_total_test.c. Every kernel page is a page here with a STATE and a PERMISSION; physical pages are real
 * shared memory (a memfd mapped at several addresses with MAP_SHARED), so two views of a section see each other's writes and a page
 * without permission really faults (probe_read / probe_write catch the SIGSEGV). Premises (state, check, source line; "hpp" =
 * include/mesosphere/kern_k_memory_block.hpp, "src" = source/kern_k_page_table_base.cpp, "svc" = source/svc/):
 *   svcMapProcessCodeMemory     src: heap Normal, RW, not locked (src:1010-1013); dst: Free and inside the alias-code region (:1004);
 *                               src becomes Locked/not mapped, dst AliasCode, no user permission (:1034-1060)
 *   svcSetProcessMemoryPermission  needs FlagCode (src:1669): AliasCode -> AliasCodeData on a write permission (:1682-1685); an
 *                               AliasCodeData page is refused (K3)
 *   svcSetMemoryPermission      perm None / Read / ReadWrite only, else 0xD801 (svc/kern_svc_memory.cpp:24-33, :43); every page of the
 *                               range in ONE state with FlagCanReprotect (heap Normal, CodeData, AliasCodeData: hpp:49, :77, :78, :84)
 *                               and ONE permission (src:623-633, :1635), else 0xD401; Stack, SharedCode and the Code states are refused
 *   svcMapProcessMemory         a REAL handle (GetObjectWithoutPseudoHandle, svc/kern_svc_process_memory.cpp:73); src every page
 *                               FlagCanMapProcess = AliasCode or AliasCodeData (hpp:83,84; Normal, SharedCode, Stack have not;
 *                               src:2528-2542); dst inside the alias-code region else 0xDA01 (svc:82), every page Free else 0xD401
 *                               (:2447-2460); dst becomes SharedCode RW (svc:98), source untouched
 *   svcUnmapProcessMemory       dst every page SharedCode and the same physical page as src, src CanMapProcess (src:4836-4850)
 *   svcUnmapProcessCodeMemory   src Locked Normal, dst CanCodeAlias (AliasCode or AliasCodeData) in ANY permission (src:1066-1160, perm
 *                               mask None at :1079), same pages: heap again RW, dst Free. A reference count > 1 on a page means views
 *                               still map it: counted in g_unmapcode_with_views, which the host must keep at 0 (P5)
 *   svcMapMemory/svcUnmapMemory the heap block moves to the stack region (the S10 window claim; the host does not use them for its own
 *                               pages any more); target state Stack RW, FlagsMisc: NOT reprotectable (hpp:64, :90)
 * The model is NOT the kernel: whether the kernel really behaves so is the proof in host_vm.inc / host_section.inc, not this test. What the
 * tests prove is that the host's bookkeeping, error mapping and interplay are right GIVEN those premises. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <malloc.h>
#include <unistd.h>
#include <setjmp.h>
#include <signal.h>
#include <sys/mman.h>
#include <switch.h>

#define PG 0x1000ull
#define ARENA (128ull << 20)
#define HEAP_PAGES 4096
enum { A_FREE, A_STACK, A_ALIAS_CODE, A_ALIAS_CODE_DATA, A_SHARED };
enum { H_FREE, H_NORMAL, H_LOCKED, H_MAPPED_OUT };

static uint8_t *g_arena, *g_heap;
static u64 g_alo;
static int g_memfd;
static uint8_t g_ast[ARENA / PG];      /* arena page state */
static uint8_t g_aperm[ARENA / PG];    /* arena page user permission: 1 R, 2 W */
static uint32_t g_aphys[ARENA / PG];   /* heap page index backing an arena page */
static uint8_t g_hst[HEAP_PAGES];
static uint16_t g_hrefs[HEAP_PAGES];   /* arena pages mapping the heap page */
static int g_fail, g_live_resv, g_unmapcode_with_views, g_free_while_locked, g_setperm_calls;
static u64 g_resv[4096][2];
static Result g_force_mapproc, g_force_setperm, g_force_unmapproc, g_force_setmemperm, g_force_mapcode;   /* next call of that svc fails with this */

#define T(c) do { if (!(c)) { g_fail++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)
#define PGI(a) (((a) - g_alo) / PG)
#define IN_ARENA(a, n) ((a) >= g_alo && (a) + (n) <= g_alo + ARENA && (a) + (n) > (a))
#define R_KERNEL(d) MAKERESULT(Module_Kernel, d)
#define E_RANGE R_KERNEL(106)
#define E_REGION R_KERNEL(109)
#define E_HANDLE R_KERNEL(114)
#define E_PERMISSION R_KERNEL(108)
#define OWN_HANDLE 0x1234u

/* ---- the heap (memalign / free of the .inc files go here) ---- */
__attribute__((unused)) static void *t_memalign(size_t al, size_t size)
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
__attribute__((unused)) static void t_free(void *p)
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
static int host_prot(int p) { return (p & 1 ? PROT_READ : 0) | (p & 2 ? PROT_WRITE : 0); }
static void amap(u64 a, u32 hp, int perm)   /* arena page a <- heap page hp, shared, user permission perm (1 R, 2 W) */
{
    void *r = mmap((void *)(uintptr_t)a, PG, host_prot(perm), MAP_SHARED | MAP_FIXED, g_memfd, (off_t)hp * PG);
    if (r == MAP_FAILED) { printf("FAIL mmap\n"); exit(2); }
    g_hrefs[hp]++;
    g_aperm[PGI(a)] = (uint8_t)perm;
}
static void aunmap(u64 a)
{
    g_hrefs[g_aphys[PGI(a)]]--;
    mmap((void *)(uintptr_t)a, PG, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    g_ast[PGI(a)] = A_FREE;
    g_aperm[PGI(a)] = 0;
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
    if (!IN_ARENA(addr, 1)) { mi->addr = addr & ~(PG - 1); mi->size = PG; mi->type = MemType_Heap; mi->perm = Perm_Rw; return 0; }
    static const u32 ty[] = { MemType_Unmapped, MemType_MappedMemory, MemType_ModuleCodeStatic, MemType_ModuleCodeMutable, MemType_ProcessMem };
    u64 i = PGI(addr), lo = i, hi = i;
    while (lo > 0 && g_ast[lo - 1] == g_ast[i] && g_aperm[lo - 1] == g_aperm[i]) lo--;
    while (hi + 1 < ARENA / PG && g_ast[hi + 1] == g_ast[i] && g_aperm[hi + 1] == g_aperm[i]) hi++;
    mi->addr = g_alo + lo * PG;
    mi->size = (hi - lo + 1) * PG;
    mi->type = ty[g_ast[i]];
    mi->perm = g_aperm[i];
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
        amap(d + k, hidx(s + k), 3);
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
    if (g_force_mapcode) { Result r = g_force_mapcode; g_force_mapcode = 0; return r; }
    if (!is_real(proc)) return E_HANDLE;
    if (!IN_ARENA(dst, size)) return E_REGION;
    if (!heap_range(src, size, H_NORMAL) || !arena_all(dst, size, A_FREE, A_FREE)) return E_RANGE;
    for (u64 k = 0; k < size; k += PG) {
        g_hst[hidx(src + k)] = H_LOCKED;
        mprotect((void *)(uintptr_t)(src + k), PG, PROT_NONE);
        g_aphys[PGI(dst + k)] = hidx(src + k);
        g_ast[PGI(dst + k)] = A_ALIAS_CODE;
        amap(dst + k, hidx(src + k), 0);   /* kernel read / not mapped: no user access */
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
        g_aperm[PGI(addr + k)] = 3;
        mprotect((void *)(uintptr_t)(addr + k), PG, PROT_READ | PROT_WRITE);
    }
    return 0;
}
Result svcSetMemoryPermission(void *addrp, u64 size, u32 perm)
{
    u64 addr = (u64)(uintptr_t)addrp;
    g_setperm_calls++;
    if (g_force_setmemperm) { Result r = g_force_setmemperm; g_force_setmemperm = 0; return r; }
    if (addr & (PG - 1)) return R_KERNEL(102);
    if ((size & (PG - 1)) || !size) return R_KERNEL(101);
    if (addr + size < addr) return E_RANGE;
    if (perm != Perm_None && perm != Perm_R && perm != Perm_Rw) return E_PERMISSION;   /* svc:24-33 */
    if (!IN_ARENA(addr, size)) return E_RANGE;
    int st = g_ast[PGI(addr)], pm = g_aperm[PGI(addr)];
    if (st != A_ALIAS_CODE_DATA) return E_RANGE;                                       /* FlagCanReprotect: AliasCodeData only here */
    for (u64 k = 0; k < size; k += PG)
        if (g_ast[PGI(addr + k)] != st || g_aperm[PGI(addr + k)] != pm) return E_RANGE;   /* one state, one permission (src:623-633) */
    for (u64 k = 0; k < size; k += PG) {
        g_aperm[PGI(addr + k)] = (uint8_t)perm;
        mprotect((void *)(uintptr_t)(addr + k), PG, host_prot((int)perm));
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
        amap(dst + k, g_aphys[PGI(src + k)], 3);                                /* alias permission fixed UserReadWrite */
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

/* ---- observation: can the CPU read / write this address right now (the page protection the model set with mprotect) */
static sigjmp_buf g_probe_jmp;
static void probe_segv(int sig) { (void)sig; siglongjmp(g_probe_jmp, 1); }
__attribute__((unused)) static int probe(u64 a, int write)
{
    struct sigaction sa = { .sa_handler = probe_segv }, old;
    sigaction(SIGSEGV, &sa, &old);
    int ok = 0;
    if (!sigsetjmp(g_probe_jmp, 1)) {
        volatile uint8_t *p = (volatile uint8_t *)(uintptr_t)a;
        if (write) { uint8_t v = *p; *p = v; } else (void)*p;
        ok = 1;
    }
    sigaction(SIGSEGV, &old, NULL);
    return ok;
}
#define CAN_READ(a) probe((a), 0)
#define CAN_WRITE(a) probe((a), 1)
__attribute__((unused)) static int kstate(u64 a) { return g_ast[PGI(a)]; }
__attribute__((unused)) static int kperm(u64 a) { return g_aperm[PGI(a)]; }
__attribute__((unused)) static int heap_pages_in(int st) { int n = 0; for (int i = 0; i < HEAP_PAGES; i++) n += g_hst[i] == st; return n; }
__attribute__((unused)) static u64 ERR(u32 c) { return (u64)(-(s64)c); }

/* arena + heap set up like every test needs it */
__attribute__((unused)) static void kdouble_init(void)
{
    setvbuf(stdout, NULL, _IONBF, 0);
    g_memfd = memfd_create("heap", 0);
    T(g_memfd >= 0 && ftruncate(g_memfd, HEAP_PAGES * PG) == 0);
    g_heap = mmap(NULL, HEAP_PAGES * PG, PROT_READ | PROT_WRITE, MAP_SHARED, g_memfd, 0);
    g_arena = mmap((void *)0x20000000, ARENA, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (g_arena == MAP_FAILED) g_arena = mmap((void *)0x60000000, ARENA, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    T(g_arena != MAP_FAILED && g_heap != MAP_FAILED);
    g_alo = (u64)(uintptr_t)g_arena;
}
