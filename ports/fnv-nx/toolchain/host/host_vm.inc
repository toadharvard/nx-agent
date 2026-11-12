/* host_vm.inc - VirtualAlloc's address space on the device (FNVNX_PLAT_VM_*, host.c platform_ext; the guest's
 * os_vm_* in platform/guest/os_guest.c, platform/win32/memory.cpp above it).
 *
 * Horizon has no demand paging: an application's memory is its heap, and the kernel can show heap pages at another
 * address. So a reservation is address space only (a libnx virtmem reservation in the stack region, or at the address
 * the game asks for: VirtualReservationSearch probes 16 MiB steps, the scrap heaps reserve 8 MiB each), and committing
 * pages maps a fresh, zeroed heap block there (one block per commit call, an "extent"). Decommitting a whole extent
 * unmaps it and gives the block back to the heap; decommitting part of one zeroes that part (the contents are gone, as
 * on Windows; the pages stay mapped and are READ-WRITE afterwards). Releasing a reservation unmaps everything in it.
 *
 * GUEST-OWNED PAGES AND THEIR PROTECTION (FNVNX_PLAT_VM_PROTECT). Mesosphere, Atmosphere 4e3f37038d9e82dde7054897213ab32b2ecf651f,
 * libraries/libmesosphere (hpp = include/mesosphere/kern_k_memory_block.hpp, source = source/kern_k_page_table_base.cpp):
 *  K1 svcSetMemoryPermission (svc/kern_svc_memory.cpp:35-50) accepts None, Read and ReadWrite (:24-33, anything else:
 *     0xD801 InvalidNewMemoryPermission, :43) and needs every block of the range in a state with FlagCanReprotect
 *     (source:1635). That flag (hpp:28) is in KMemoryState_FlagsData (hpp:49), hence in Normal (heap), CodeData and AliasCodeData
 *     (hpp:78, :77, :84), and NOT in Stack (hpp:90: FlagsMisc, hpp:64, the state svcMapMemory gives the target), nor SharedCode
 *     (hpp:101, the state of section views), nor the Code states (hpp:76, :83). None is expressible (ConvertToKMemoryPermission,
 *     hpp:182: perm None = kernel-read + NotMapped). The attribute must be None (source:1635, mask All): MapCodeMemory leaves
 *     the alias None (source:1060).
 *  K2 the kernel checks a range block by block against ONE first block: same state, same permission, same attribute for the
 *     whole range (CheckMemoryState with out_state, source:623-633). A VM_PROTECT range with several
 *     permissions therefore goes down as one svc per uniform run; the host walks svcQueryMemory runs.
 *  K3 svcSetProcessMemoryPermission (source:1659-1688) needs FlagCode (:1669): it works on AliasCode / Code only and turns them
 *     into AliasCodeData / CodeData on the first write permission (:1682-1685); it can NOT reprotect AliasCodeData again. So
 *     svcSetMemoryPermission (K1) is the one call that reprotects guest pages, svcSetProcessMemoryPermission only builds them.
 *  K4 svcUnmapProcessCodeMemory (source:1066-1160) checks the destination with CheckMemoryStateContiguous (:589-621) and
 *     perm_mask None (:1079): the alias may be in ANY permission (R, RW, none) when it is unmapped, and the heap
 *     source comes back as Normal, UserReadWrite (:1147-1152) with its contents. So unmap_fixed / decommit need no
 *     "restore read-write first".
 *  K5 the destination of svcMapProcessCodeMemory needs the alias-code region (source:1004: CanContain AliasCode): in a 32-bit
 *     space [small map start, large map end), a superset of the stack region that svcMapMemory needed (host_section.inc P3).
 * RULE. Every page the guest owns outside a window the host claimed at start is built as a code-memory alias in state
 * AliasCodeData: svcMapProcessCodeMemory(own process, dst, heap block) + svcSetProcessMemoryPermission(RW) (host_section.inc P1
 * proves the same pair for section backings). That is the construction of map_fixed and of every VM_COMMIT extent. The
 * windows S10 claims at start (the original image and the data window, startup/host) are mapped by svcMapMemory (state
 * Stack, K1: not reprotectable); the host converts them once at start, after the own-process handle exists
 * (host_win_adopt), to the same state, so the loader's sections (read-only image, no-access gaps) are reprotectable too.
 * VM_PROTECT then is svcSetMemoryPermission over the uniform runs. Pages of other states (the guest module, host memory,
 * section views, unmapped) are refused before any svc: ownership is the host's own table (extents, map_fixed ranges, windows),
 * because the kernel state alone cannot tell the guest module's data from guest-owned pages (both AliasCodeData).
 *
 * LIMITS. Every extent and every protection run is kernel memory blocks of the application's shared slab of 20,000
 * (host_section.inc); a protect splits at most two blocks off a run. map_fixed ranges: FNVNX_MF_MAX.
 *
 * Standalone (<switch.h>, malloc, logf_, g_own_process) so tests/s4/backends runs it under qemu against the kernel double kdouble.h. */
#define FNVNX_VM_RESV 256
/* Each extent is one svcMapProcessCodeMemory (+ svcSetProcessMemoryPermission): in the worst case 4 kernel memory blocks (the
 * heap source splits in three, the alias is one). A 32BitNoReserved process cannot have its own system resource (ldr requires a 39-bit space
 * for system_resource_size), so blocks come from Mesosphere's shared application slab of 20,000
 * (ApplicationMemoryBlockSlabHeapSize). 3072 x 4 + ~4,000 for everything else stays under it. With 8192, heavy
 * VirtualAlloc use could exhaust the slab, and then every mapping in the process fails (NX1 check 7). */
#define FNVNX_VM_EXTENTS 3072
#define FNVNX_VM_ALIGN 0x10000ull   /* Windows' allocation granularity */
#define FNVNX_VM_NO_GO_LO 0x00400000ull   /* the original image and the data window (S10's fixed ranges) */
#define FNVNX_VM_NO_GO_HI 0x02100000ull

typedef struct { u64 addr, size; void *rv; } FnvnxVmResv;
typedef struct { u64 addr, size; void *src; } FnvnxVmExtent;
static FnvnxVmResv g_vm_resv[FNVNX_VM_RESV];
static FnvnxVmExtent g_vm_ext[FNVNX_VM_EXTENTS];
static int g_vm_next;
static Mutex g_vm_lock;   /* also guards g_mf and g_win below */

/* map_fixed ranges (host_v3.inc) and the windows the host claimed (host.c, host_win_adopt): the other guest-owned memory */
#define FNVNX_MF_MAX 512
#define FNVNX_WIN_MAX 4
typedef struct { u64 addr, size, src; } FnvnxMf;
typedef struct { u64 lo, hi; } FnvnxWin;
static FnvnxMf g_mf[FNVNX_MF_MAX];
static FnvnxWin g_win[FNVNX_WIN_MAX];

/* result values: hostapi.h (FNVNX_E_*, the numbers of the libnx and kernel results the host returns) */

/* A code-memory alias at dst of the heap block src (K5, host_section.inc P1): AliasCode, then AliasCodeData. If the
 * second step fails and the alias cannot be undone either, *src_lost is set: the block stays locked and must not be freed. */
static Result cm_map(u64 dst, u64 src, u64 size, int *src_lost) {
    *src_lost = 0;
    if (g_own_process == INVALID_HANDLE) return FNVNX_HE_NOTFOUND;
    Result rc = svcMapProcessCodeMemory(g_own_process, dst, src, size);
    if (R_FAILED(rc)) return rc;
    rc = svcSetProcessMemoryPermission(g_own_process, dst, size, Perm_Rw);
    if (R_FAILED(rc) && R_FAILED(svcUnmapProcessCodeMemory(g_own_process, dst, src, size))) *src_lost = 1;
    return rc;
}
/* undo of cm_map, whatever the permission of the alias (K4) */
static Result cm_unmap(u64 dst, u64 src, u64 size) {
    if (g_own_process == INVALID_HANDLE) return FNVNX_HE_NOTFOUND;
    return svcUnmapProcessCodeMemory(g_own_process, dst, src, size);
}

/* a page of [a, a+n) is a view of a memory section (host_section.inc): its kernel type is MemType_ProcessMem (svcMapProcessMemory,
 * SharedCode), and the host maps nothing else that way. Views are not VM-committed memory, so commit, decommit and
 * (through its decommit) release refuse a range holding one, with the kernel's InvalidCurrentMemory (0xD401). */
static int vm_has_view(u64 a, u64 n) {
    for (u64 p = a; p < a + n;) {
        MemoryInfo mi;
        u32 pi;
        if (R_FAILED(svcQueryMemory(&mi, &pi, p))) return 1;   /* cannot tell: do not touch */
        if ((mi.type & 0xFFu) == MemType_ProcessMem) return 1;
        u64 end = mi.addr + mi.size;
        if (end <= p) return 1;
        p = end;
    }
    return 0;
}

/* the address range is unmapped memory (svcQueryMemory) */
static int vm_unmapped(u64 a, u64 n) {
    for (u64 p = a; p < a + n;) {
        MemoryInfo mi;
        u32 pi;
        if (R_FAILED(svcQueryMemory(&mi, &pi, p)) || mi.type != MemType_Unmapped) return 0;
        u64 end = mi.addr + mi.size;
        if (end <= p) return 0;
        p = end;
    }
    return 1;
}

/* ---- guest-owned ranges (g_vm_lock held) */
static int own_find(u64 p, u64 *end) {   /* p lies in an extent, a map_fixed range or a window: its end */
    for (int i = 0; i < g_vm_next; i++)
        if (g_vm_ext[i].size && p >= g_vm_ext[i].addr && p < g_vm_ext[i].addr + g_vm_ext[i].size) { *end = g_vm_ext[i].addr + g_vm_ext[i].size; return 1; }
    for (int i = 0; i < FNVNX_MF_MAX; i++)
        if (g_mf[i].size && p >= g_mf[i].addr && p < g_mf[i].addr + g_mf[i].size) { *end = g_mf[i].addr + g_mf[i].size; return 1; }
    for (int i = 0; i < FNVNX_WIN_MAX; i++)
        if (g_win[i].hi && p >= g_win[i].lo && p < g_win[i].hi) { *end = g_win[i].hi; return 1; }
    return 0;
}
static int own_covers(u64 lo, u64 hi) {   /* every page of [lo, hi) is guest-owned */
    for (u64 p = lo; p < hi;) {
        u64 e;
        if (!own_find(p, &e)) return 0;
        p = e;
    }
    return 1;
}
static int foreign_overlaps(u64 lo, u64 hi) {   /* a map_fixed range or a window meets [lo, hi): not VM-committed memory */
    for (int i = 0; i < FNVNX_MF_MAX; i++)
        if (g_mf[i].size && g_mf[i].addr < hi && lo < g_mf[i].addr + g_mf[i].size) return 1;
    for (int i = 0; i < FNVNX_WIN_MAX; i++)
        if (g_win[i].hi && g_win[i].lo < hi && lo < g_win[i].hi) return 1;
    return 0;
}
static int in_window(u64 a, u64 n) {
    for (int i = 0; i < FNVNX_WIN_MAX; i++)
        if (g_win[i].hi && a >= g_win[i].lo && a + n <= g_win[i].hi) return 1;
    return 0;
}

/* set the permission of [lo, hi) page by page of the uniform kernel runs (K2); pages already so are skipped. On a kernel
 * failure the runs before it keep the new permission. */
static Result prot_apply(u64 lo, u64 hi, u32 perm) {
    for (u64 p = lo; p < hi;) {
        MemoryInfo mi;
        u32 pi;
        Result rc = svcQueryMemory(&mi, &pi, p);
        if (R_FAILED(rc)) return rc;
        u64 end = mi.addr + mi.size;
        if (end <= p) return FNVNX_HE_RANGE;
        if (end > hi) end = hi;
        if ((mi.perm & 7u) != perm) {
            rc = svcSetMemoryPermission((void *)(uintptr_t)p, end - p, perm);
            if (R_FAILED(rc)) return rc;
        }
        p = end;
    }
    return 0;
}

static FnvnxVmResv *vm_resv_of(u64 a, u64 n) {
    for (int i = 0; i < FNVNX_VM_RESV; i++) {
        FnvnxVmResv *r = &g_vm_resv[i];
        if (r->size && a >= r->addr && a + n <= r->addr + r->size && a + n >= a) return r;
    }
    return NULL;
}

/* (hint or 0, size) -> the reservation's address, 0 if none */
static u64 host_vm_reserve(u64 hint, u64 size) {
    if (!size || size > 0x100000000ull) return 0;   /* nothing of 0 or more than 4 GiB fits the guest's address space */
    size = (size + FNVNX_VM_ALIGN - 1) & ~(FNVNX_VM_ALIGN - 1);
    mutexLock(&g_vm_lock);
    FnvnxVmResv *slot = NULL;
    for (int i = 0; i < FNVNX_VM_RESV && !slot; i++)
        if (!g_vm_resv[i].size) slot = &g_vm_resv[i];
    u64 a = 0;
    if (slot) {
        virtmemLock();
        if (hint) {
            a = hint & ~(FNVNX_VM_ALIGN - 1);
            int bad = a + size < a || a + size > 0x100000000ull ||
                      (a < FNVNX_VM_NO_GO_HI && a + size > FNVNX_VM_NO_GO_LO) || !vm_unmapped(a, size);
            for (int i = 0; i < FNVNX_VM_RESV && !bad; i++) {
                FnvnxVmResv *r = &g_vm_resv[i];
                bad = r->size && a < r->addr + r->size && r->addr < a + size;
            }
            if (bad) a = 0;
        } else {
            a = (u64)(uintptr_t)virtmemFindStack(size, FNVNX_VM_ALIGN);
        }
        void *rv = a ? virtmemAddReservation((void *)(uintptr_t)a, size) : NULL;
        virtmemUnlock();
        if (rv) {
            slot->addr = a;
            slot->size = size;
            slot->rv = rv;
        } else {
            a = 0;
        }
    }
    mutexUnlock(&g_vm_lock);
    return a;
}

/* map zeroed heap memory under [a, a+n) wherever no extent is yet; 0 or an error */
static u64 host_vm_commit(u64 a, u64 n) {
    if (a + n < a || a + n + 0xFFF < a) return FNVNX_HE_BADINPUT;   /* wraps: inside no reservation */
    u64 lo = a & ~0xFFFull, hi = (a + n + 0xFFF) & ~0xFFFull;
    mutexLock(&g_vm_lock);
    Result rc = 0;
    if (!n || !vm_resv_of(lo, hi - lo)) rc = FNVNX_HE_BADINPUT;
    else if (vm_has_view(lo, hi - lo) || foreign_overlaps(lo, hi)) rc = FNVNX_HE_RANGE;
    for (u64 p = lo; !rc && p < hi;) {
        u64 next = hi;   /* the end of the gap that starts at p, or skip the extent that covers p */
        int covered = 0;
        for (int i = 0; i < g_vm_next; i++) {
            FnvnxVmExtent *e = &g_vm_ext[i];
            if (!e->size) continue;
            if (p >= e->addr && p < e->addr + e->size) { covered = 1; next = e->addr + e->size; break; }
            if (e->addr > p && e->addr < next) next = e->addr;
        }
        if (!covered) {
            u64 len = next - p;
            int slot = -1;
            for (int i = 0; i < g_vm_next && slot < 0; i++)
                if (!g_vm_ext[i].size) slot = i;
            if (slot < 0 && g_vm_next < FNVNX_VM_EXTENTS) slot = g_vm_next++;
            void *src = slot >= 0 ? memalign(0x1000, len) : NULL;
            if (!src) {
                rc = FNVNX_HE_NOMEM;
                break;
            }
            memset(src, 0, len);   /* unreachable from src once mapped */
            int lost;
            rc = cm_map(p, (u64)(uintptr_t)src, len, &lost);
            if (R_FAILED(rc)) {
                if (!lost) free(src);
                break;
            }
            g_vm_ext[slot].addr = p;
            g_vm_ext[slot].size = len;
            g_vm_ext[slot].src = src;
        }
        p = next;
    }
    mutexUnlock(&g_vm_lock);
    return rc;
}

/* extents inside [a, a+n) go back to the heap; parts of extents are zeroed */
static u64 host_vm_decommit(u64 a, u64 n) {
    if (!n) return 0;
    if (a + n < a || a + n + 0xFFF < a) return FNVNX_HE_RANGE;   /* wraps: not guest memory */
    u64 lo = a & ~0xFFFull, hi = (a + n + 0xFFF) & ~0xFFFull;
    Result rc = 0;
    mutexLock(&g_vm_lock);
    if (vm_has_view(lo, hi - lo) || foreign_overlaps(lo, hi)) {
        mutexUnlock(&g_vm_lock);
        return FNVNX_HE_RANGE;
    }
    for (int i = 0; i < g_vm_next; i++) {
        FnvnxVmExtent *e = &g_vm_ext[i];
        if (!e->size || e->addr >= hi || e->addr + e->size <= lo) continue;
        if (e->addr >= lo && e->addr + e->size <= hi) {
            Result r = cm_unmap(e->addr, (u64)(uintptr_t)e->src, e->size);   /* any protection (K4) */
            if (R_SUCCEEDED(r)) {
                free(e->src);
                e->size = 0;
            } else if (!rc) {
                rc = r;
            }
        } else {
            u64 s = e->addr > lo ? e->addr : lo, t = e->addr + e->size < hi ? e->addr + e->size : hi;
            Result r = prot_apply(s, t, Perm_Rw);   /* the part that stays mapped is zeroed, and read-write */
            if (R_SUCCEEDED(r)) memset((void *)(uintptr_t)s, 0, t - s);
            else if (!rc) rc = r;
        }
    }
    while (g_vm_next && !g_vm_ext[g_vm_next - 1].size) g_vm_next--;
    mutexUnlock(&g_vm_lock);
    return rc;
}

static u64 host_vm_release(u64 a) {
    mutexLock(&g_vm_lock);
    FnvnxVmResv *r = vm_resv_of(a, 1);
    if (!r || r->addr != a) {
        mutexUnlock(&g_vm_lock);
        return FNVNX_HE_BADINPUT;
    }
    u64 size = r->size;
    void *rv = r->rv;
    mutexUnlock(&g_vm_lock);
    Result rc = host_vm_decommit(a, size);
    if (R_FAILED(rc)) return rc;
    mutexLock(&g_vm_lock);
    virtmemLock();
    virtmemRemoveReservation((VirtmemReservation *)rv);
    virtmemUnlock();
    r->size = 0;
    mutexUnlock(&g_vm_lock);
    return 0;
}

/* FNVNX_PLAT_VM_PROTECT (address, size, prot) -> 0 or a result; the checks in the order of svcSetMemoryPermission
 * (svc/kern_svc_memory.cpp:36-43), then the host's: every page guest-owned. The kernel is asked once per uniform run (K2). */
static u64 host_vm_protect(u64 addr, u64 size, u64 prot) {
    if (addr & 0xFFF) return FNVNX_HE_ADDR;
    if (!size || (size & 0xFFF)) return FNVNX_HE_SIZE;
    if (addr + size < addr) return FNVNX_HE_RANGE;
    if (prot > 3 || prot == 2) return FNVNX_HE_PERM;   /* R (1), RW (3) and none (0): the kernel has no write-only permission (K1) */
    mutexLock(&g_vm_lock);
    Result rc = own_covers(addr, addr + size) ? prot_apply(addr, addr + size, (u32)prot) : FNVNX_HE_RANGE;   /* Perm_R = 1, Perm_W = 2 */
    mutexUnlock(&g_vm_lock);
    return rc;
}

/* A claimed window (S10: the original image and the data window) is mapped by svcMapMemory (state Stack: not reprotectable, K1).
 * The host converts it once, from main after the own-process handle exists: the caller released the claim (backing = the heap
 * pages, RW again), the same pages become the range's AliasCodeData. Single-threaded at that point. 0 or a kernel result. */
static Result host_win_adopt(u64 lo, u64 hi, u64 backing) {
    int slot = -1, lost;
    for (int i = 0; i < FNVNX_WIN_MAX && slot < 0; i++)
        if (!g_win[i].hi) slot = i;
    if (slot < 0) return FNVNX_HE_NOMEM;
    Result rc = cm_map(lo, backing, hi - lo, &lost);
    if (R_SUCCEEDED(rc)) { g_win[slot].lo = lo; g_win[slot].hi = hi; }
    return rc;
}

/* map_fixed inside an adopted window: the range becomes read-write and zero (nothing to map) */
static Result host_win_zero(u64 addr, u64 size) {
    Result rc = prot_apply(addr, addr + size, Perm_Rw);
    if (R_SUCCEEDED(rc)) memset((void *)(uintptr_t)addr, 0, size);
    return rc;
}

/* ---- map_fixed / unmap_fixed (FnvnxHostApi slot map_fixed, FNVNX_PLAT_UNMAP_FIXED): needs logf_ */
/* map_fixed (addr, size, src) -> 0 or a result (hostapi.h): [addr, addr+size) becomes read-write zero-or-src memory at exactly addr.
 * Checks in this order: size 0, wrap or addr/size not page aligned -> BadInput; inside an adopted window (host_win_adopt, S10's
 * claim of the original image and the data window): read-write and zeroed, src not looked at; src 0 or not aligned -> BadInput;
 * the ranges table full -> OutOfMemory; the range not unmapped (svcQueryMemory) -> AlreadyInitialized; then the kernel
 * (cm_map: AliasCodeData, reprotectable by VM_PROTECT, host_vm.inc K1-K5): its result unchanged, e.g. 0xDA01 when addr is outside
 * the alias-code region, 0xD401 when src is not ordinary heap. The heap block src becomes inaccessible until unmap_fixed. */
static u64 h_map_fixed(u64 addr, u64 size, u64 src) {
    if (!size || addr + size < addr || ((addr | size) & 0xFFF)) return FNVNX_HE_BADINPUT;
    mutexLock(&g_vm_lock);
    Result rc;
    if (in_window(addr, size)) {
        rc = host_win_zero(addr, size);
    } else if (!src || (src & 0xFFF)) {
        rc = FNVNX_HE_BADINPUT;
    } else {
        FnvnxMf *slot = NULL;
        for (int i = 0; i < FNVNX_MF_MAX && !slot; i++)
            if (!g_mf[i].size) slot = &g_mf[i];
        MemoryInfo mi;
        u32 pi;
        if (!slot) {
            rc = FNVNX_HE_NOMEM;
        } else if (R_FAILED(svcQueryMemory(&mi, &pi, addr)) || mi.type != MemType_Unmapped || mi.addr + mi.size < addr + size) {
            rc = FNVNX_HE_ALREADY;   /* occupied (random ASLR placement) */
        } else {
            int lost;
            logf_("fnvnx host: map_fixed %#010lx..%#010lx from %#010lx\n", addr, addr + size, src);
            rc = cm_map(addr, src, size, &lost);
            if (R_SUCCEEDED(rc)) { slot->addr = addr; slot->size = size; slot->src = src; }
        }
    }
    mutexUnlock(&g_vm_lock);
    return rc;
}

/* FNVNX_PLAT_UNMAP_FIXED (addr, size, src): the undo of h_map_fixed, see hostapi.h. (addr, size, src) must be exactly the
 * arguments of one live map_fixed (the host keeps the table: g_mf). The pages may have any protection (K4). Checks: addr or src
 * not page aligned -> 0xCC01; size 0 or not a page multiple -> 0xCA01; wrap -> 0xD401; inside an adopted window: read-write and
 * zeroed again, src not looked at; no live map_fixed with exactly these arguments -> 0xD401; else the kernel's result. */
static u64 h_unmap_fixed(u64 addr, u64 size, u64 src) {
    if ((addr | src) & 0xFFF) return FNVNX_HE_ADDR;
    if (!size || (size & 0xFFF)) return FNVNX_HE_SIZE;
    if (addr + size < addr) return FNVNX_HE_RANGE;
    mutexLock(&g_vm_lock);
    Result rc;
    if (in_window(addr, size)) {
        rc = host_win_zero(addr, size);
    } else {
        FnvnxMf *m = NULL;
        for (int i = 0; i < FNVNX_MF_MAX && !m; i++)
            if (g_mf[i].size && g_mf[i].addr == addr && g_mf[i].size == size && g_mf[i].src == src) m = &g_mf[i];
        if (!m) {
            rc = FNVNX_HE_RANGE;
        } else {
            logf_("fnvnx host: unmap_fixed %#010lx..%#010lx to %#010lx\n", addr, addr + size, src);
            rc = cm_unmap(addr, src, size);
            if (R_SUCCEEDED(rc)) memset(m, 0, sizeof *m);
        }
    }
    mutexUnlock(&g_vm_lock);
    return rc;
}

