/* host_section.inc - memory sections with SEVERAL LIVE READ-WRITE VIEWS of the same pages (FNVNX_PLAT_SECTION_*, see
 * include/fnvnx/hostapi.h for the service table; host.c platform_ext). For the platform's CreateFileMapping over the paging
 * file + MapViewOfFile(FILE_MAP_WRITE): N views of arbitrary page-aligned sub-ranges of one backing, overlapping, each
 * unmapped on its own.
 *
 * RULE. Backing: heap pages turned into "code data" at a private address by the conversion fnvnx_reserve_remap_code
 * already performs (svcMapProcessCodeMemory, then svcSetProcessMemoryPermission(RW)). View: svcMapProcessMemory(dst,
 * own process, backing + offset, size). Unmap: svcUnmapProcessMemory with the same arguments.
 *
 * PROOFS from the Mesosphere sources, Atmosphere commit 4e3f37038d9e82dde7054897213ab32b2ecf651f, paths under
 * libraries/libmesosphere/ (source/ = kern_k_page_table_base.cpp unless a file is named; hpp = include/mesosphere/
 * kern_k_memory_block.hpp). The kernel model of these premises is the test double in tests/s4/backends/host_section_test.c.
 *
 * P1 the source range of svcMapProcessMemory needs FlagCanMapProcess. svc/kern_svc_process_memory.cpp:88-93 calls
 *    MakeAndOpenPageGroup(src, state_mask = state = FlagCanMapProcess, perm mask 0, attribute mask All = None);
 *    source/kern_k_page_table_base.cpp:2528-2542 adds FlagReferenceCounted to mask and state and checks every block of the range.
 *    hpp:43 FlagCanMapProcess; hpp:76 Code, :77 CodeData, :83 AliasCode and :84 AliasCodeData carry it (hpp:136,137 give the
 *    values 0x04DD7E08 and 0x0FFFBD09); FlagReferenceCounted is in FlagsData (hpp:49-55) and FlagsCode (hpp:57-62).
 *    Heap pages (hpp:78 Normal) do NOT carry it, and after svcMapProcessCodeMemory the heap SOURCE stays Normal, perm
 *    kernel-read/not-mapped, attribute Locked (:1002-1062, the Update calls at its end), so the heap is not a valid source.
 *    The valid source is the code alias: MapCodeMemory gives the destination AliasCode (:1062), and
 *    SetProcessMemoryPermission(RW) turns it into AliasCodeData (:1659-1690, case at :1685; it requires FlagCode, which
 *    AliasCode has, and attribute None, which MapCodeMemory set). AliasCodeData has FlagCanMapProcess: P1 holds for exactly the
 *    state our remap conversion produces, with attribute None. MapProcessMemory leaves the source blocks untouched (no
 *    Update on src_pt in svc/kern_svc_process_memory.cpp:88-98), so the backing stays a valid source for any number of views.
 * P2 the handle must be real. svc:73 and :115 resolve it with GetObjectWithoutPseudoHandle (include/mesosphere/
 *    kern_k_handle_table.hpp:112-124: table lookup only, the pseudo handle 0xFFFF8001 is NOT translated). host.c
 *    get_own_process_handle sends CUR_PROCESS_HANDLE as a copy handle over a session to a helper thread of the same process;
 *    source/kern_k_server_session.cpp:188 resolves each copy handle with GetObjectForIpc, which maps the current-process
 *    pseudo handle to the owner KProcess (hpp kern_k_handle_table.hpp:165-172), and the receiver gets a real entry in its
 *    own handle table. Same handle already serves svcMapProcessCodeMemory (host.c loader, which uses the same lookup, svc:145).
 * P3 the destination. svc:82 CanContain(dst, size, SharedCode) (:513-575): SharedCode region = alias-code region (:439-475;
 *    :468), minus the heap and alias regions (:556-563). In a 32-bit space that region is [MapSmall start, MapLarge end)
 *    (:193-196), and the STACK region is the small map itself (:197-198): every range virtmemFindStack gives, i.e. every
 *    host VM reservation (host_vm.inc), satisfies CanContain. The address is the caller's (svc takes dst_address as given,
 *    page aligned, svc:62). MapPageGroup (:2447-2460) additionally requires the whole range to be kernel-Free
 *    (CheckMemoryState(..., KMemoryState_Free, ...)): an UNCOMMITTED part of a host VM reservation is Free (a libnx
 *    reservation is user-side bookkeeping only, host_vm_unmapped uses the same fact), a committed extent (MappedMemory)
 *    and another view (SharedCode) are not: the kernel refuses with InvalidCurrentMemory (0xD401), nothing changes.
 *    So the rule "a fixed view address lies inside one VM reservation" is both sufficient for the kernel and what keeps
 *    other users of the address space (libnx virtmem) away from it; the backing's own address comes from
 *    virtmemFindCodeMemory (libnx's finder for svcMapProcessCodeMemory destinations: same alias-code region, AliasCode
 *    CanContain is the same function, svc:151); if it ever returned a range the kernel refuses, SECTION_CREATE returns the
 *    kernel's error, nothing is faked.
 * P4 SVC numbers (libvapours svc_definition_macro.hpp:131-136): 0x73 SetProcessMemoryPermission, 0x74 MapProcessMemory,
 *    0x75 UnmapProcessMemory, 0x77 MapProcessCodeMemory, 0x78 UnmapProcessCodeMemory. The syscall mask capability
 *    (source/kern_k_capabilities.cpp:106-117) is a bit mask per 24 ids; toolchain/npdm/fnvnx-app.json grants "all"
 *    (0x00..0xBF, npdmtool.py:92-95) and tests/nx1/check_svc.py lists 0x73-0x78 among the application SVCs. Needed beyond the
 *    loader's 0x73, 0x77: 0x74, 0x75, 0x78 (all within "all").
 * P5 lifetime. svcUnmapProcessMemory (svc:103-130, source :4836-4900) requires the SOURCE range to be mapped, CanMapProcess,
 *    attribute None, and the destination blocks to be SharedCode/RW; so a view can only be unmapped while the backing is
 *    still AliasCodeData: the backing must outlive its views (kernel-enforced, not a convention). The pages themselves are
 *    reference counted per page group: a map opens one reference (arch/arm64/kern_k_page_table.cpp:646, MapContiguous called
 *    by MapGroup :652), an unmap closes (:196, :259, :428), MakeAndOpenPageGroup's reference is closed by the svc (:95).
 *    Releasing the backing (svcUnmapProcessCodeMemory, :1075-1135: src Locked Normal, dst CanCodeAlias, attribute None)
 *    would not by itself free pages that views still reference, but it hands the heap pages back to the allocator (malloc),
 *    which would reuse them under live views. Rule: SECTION_FREE with live views DEFERS; the backing is released when the
 *    last view is unmapped (never earlier, never leaked while unviewed). A view is never created from a freed id.
 * VM_PROTECT: SharedCode (hpp:101) has neither FlagCanReprotect nor FlagCode; svcSetMemoryPermission requires
 *    FlagCanReprotect (:1625-1635) and svcSetProcessMemoryPermission FlagCode (:1665), so a view cannot be reprotected:
 *    the VM services refuse view pages (see below).
 * A view is always read-write: svc:98 passes KMemoryPermission_UserReadWrite as the fixed alias permission; there is no
 * parameter. So no read-only or copy-on-write view exists at this level (hostapi.h lists the Win32 cases that follow).
 *
 * VM services and views. A view page's kernel type is MemType_ProcessMem (0x0F, libnx svc.h) = SharedCode, and the host
 * creates no other such memory, so host_vm.inc decides from svcQueryMemory alone (no table shared between the two
 * files, no lock order): host_vm_commit and host_vm_decommit refuse a range that contains a ProcessMem page, host_vm_release
 * thereby refuses a reservation that holds a view (its decommit is refused first), all with 0xD401.
 *
 * LIMITS. 128 sections, 512 views. Kernel memory blocks come from the shared application slab of 20,000
 * (kern_kernel.hpp:63 ApplicationMemoryBlockSlabHeapSize, see host_vm.inc): a section uses at most 4 (the heap source
 * splits in three, the alias is one), a view at most 2 (the destination splits a free block in three, one of which is
 * the view): 128*4 + 512*2 = 1536, on top of host_vm.inc's 3072*4 = 12288 and about 4000 for the rest: 17.8k < 20k.
 * Address space: a section occupies its size at the backing address, besides its views. Ids run 1..0x7FFFFFFF, never reused.
 *
 * Standalone like host_vm.inc: needs <switch.h>, g_own_process (host.c), host_vm.inc included before this file. */
#define FNVNX_SEC_SECTIONS 128
#define FNVNX_SEC_VIEWS 512
#define FNVNX_SEC_PAGE 0xFFFull
#define FNVNX_SEC_KERNEL_ERR(desc) MAKERESULT(Module_Kernel, desc)

typedef struct { u64 base, size; void *src; VirtmemReservation *rv; u32 id, views; int live; } FnvnxSection;
typedef struct { u64 addr, size, off; int sec; VirtmemReservation *own; } FnvnxView;   /* sec: index + 1, 0 = unused; own: the
                                                                                          host's address range of an addr == 0 view */
static FnvnxSection g_sec[FNVNX_SEC_SECTIONS];
static FnvnxView g_sec_view[FNVNX_SEC_VIEWS];
static u32 g_sec_next_id = 1;
static Mutex g_sec_lock;

static u64 sec_err(u32 code) { return (u64)(-(s64)code); }

static FnvnxSection *sec_live(u64 id) {
    for (int i = 0; i < FNVNX_SEC_SECTIONS; i++)
        if (g_sec[i].id && g_sec[i].live && g_sec[i].id == id) return &g_sec[i];
    return NULL;
}

/* give the backing back (no views left): heap pages become heap again, the address range is free */
static Result sec_release(FnvnxSection *s) {
    Result rc = svcUnmapProcessCodeMemory(g_own_process, s->base, (u64)(uintptr_t)s->src, s->size);
    if (R_FAILED(rc)) return rc;   /* the premises of P1/P5 hold, so this is a kernel fault; nothing is hidden */
    virtmemLock();
    virtmemRemoveReservation(s->rv);
    virtmemUnlock();
    free(s->src);
    memset(s, 0, sizeof *s);
    return 0;
}

static u64 host_section_create(u64 size) {
    if (!size || (size & FNVNX_SEC_PAGE) || size > FNVNX_SEC_MAX_SIZE) return sec_err(FNVNX_SEC_E_SIZE);
    if (g_own_process == INVALID_HANDLE) return sec_err(MAKERESULT(Module_Libnx, LibnxError_NotFound));
    mutexLock(&g_sec_lock);
    u64 res;
    FnvnxSection *s = NULL;
    for (int i = 0; i < FNVNX_SEC_SECTIONS && !s; i++)
        if (!g_sec[i].id) s = &g_sec[i];
    if (!s) {
        res = sec_err(FNVNX_SEC_E_FULL);
    } else if (g_sec_next_id > 0x7FFFFFFFu) {
        res = sec_err(FNVNX_SEC_E_NOMEM);
    } else {
        void *src = memalign(0x1000, size);
        VirtmemReservation *rv = NULL;
        u64 dst = 0;
        if (src) {
            memset(src, 0, size);   /* heap memory is not zero in general; unreachable through src once mapped */
            virtmemLock();
            dst = (u64)(uintptr_t)virtmemFindCodeMemory(size, 0);
            rv = dst ? virtmemAddReservation((void *)(uintptr_t)dst, size) : NULL;
            virtmemUnlock();
        }
        if (!rv) {
            free(src);
            res = sec_err(FNVNX_SEC_E_NOMEM);
        } else {
            Result rc = svcMapProcessCodeMemory(g_own_process, dst, (u64)(uintptr_t)src, size);
            if (R_SUCCEEDED(rc)) {
                rc = svcSetProcessMemoryPermission(g_own_process, dst, size, Perm_Rw);   /* AliasCode -> AliasCodeData */
                if (R_FAILED(rc) && R_FAILED(svcUnmapProcessCodeMemory(g_own_process, dst, (u64)(uintptr_t)src, size)))
                    src = NULL;   /* the heap pages stay locked and mapped: they must not be freed either */
            }
            if (R_FAILED(rc)) {
                virtmemLock();
                if (src) virtmemRemoveReservation(rv);
                virtmemUnlock();
                free(src);
                res = sec_err(rc);
            } else {
                s->base = dst;
                s->size = size;
                s->src = src;
                s->rv = rv;
                s->views = 0;
                s->live = 1;
                s->id = g_sec_next_id++;
                res = s->id;
            }
        }
    }
    mutexUnlock(&g_sec_lock);
    return res;
}

static u64 host_section_map(u64 id, u64 addr, u64 off, u64 size) {
    if (!size || (size & FNVNX_SEC_PAGE) || size > FNVNX_SEC_MAX_SIZE) return sec_err(FNVNX_SEC_E_SIZE);
    if ((addr | off) & FNVNX_SEC_PAGE) return sec_err(FNVNX_SEC_E_ALIGN);
    mutexLock(&g_sec_lock);
    u64 res;
    FnvnxSection *s = sec_live(id);
    FnvnxView *v = NULL;
    for (int i = 0; i < FNVNX_SEC_VIEWS && !v; i++)
        if (!g_sec_view[i].sec) v = &g_sec_view[i];
    if (!s) {
        res = sec_err(FNVNX_SEC_E_ID);
    } else if (off + size < off || off + size > s->size) {
        res = sec_err(FNVNX_SEC_E_RANGE);
    } else if (!v) {
        res = sec_err(FNVNX_SEC_E_FULL);
    } else {
        VirtmemReservation *own = NULL;
        u64 a = addr;
        if (!a) {   /* a range of the host's own in the stack region (P3), 64 KiB aligned: not a VM reservation, so the
                     * VM services do not know it and refuse it (not inside a reservation) */
            virtmemLock();
            a = (u64)(uintptr_t)virtmemFindStack(size, FNVNX_VM_ALIGN);
            own = a ? virtmemAddReservation((void *)(uintptr_t)a, size) : NULL;
            virtmemUnlock();
            if (!own) a = 0;
        }
        if (!a) {
            res = sec_err(FNVNX_SEC_E_NOMEM);
        } else {
            Result rc;
            if (own) {
                rc = svcMapProcessMemory((void *)(uintptr_t)a, g_own_process, s->base + off, size);
            } else {
                mutexLock(&g_vm_lock);   /* the caller's address: inside one VM reservation, which cannot be released meanwhile */
                rc = vm_resv_of(a, size) ? svcMapProcessMemory((void *)(uintptr_t)a, g_own_process, s->base + off, size)
                                         : FNVNX_SEC_KERNEL_ERR(109);   /* 0xDA01: not inside a VM reservation */
                mutexUnlock(&g_vm_lock);
            }
            if (R_FAILED(rc)) {
                if (own) {
                    virtmemLock();
                    virtmemRemoveReservation(own);
                    virtmemUnlock();
                }
                res = sec_err(rc);
            } else {
                v->addr = a;
                v->size = size;
                v->off = off;
                v->own = own;
                v->sec = (int)(s - g_sec) + 1;
                s->views++;
                res = a;
            }
        }
    }
    mutexUnlock(&g_sec_lock);
    return res;
}

static u64 host_section_unmap(u64 addr, u64 size) {
    mutexLock(&g_sec_lock);
    FnvnxView *v = NULL;
    for (int i = 0; i < FNVNX_SEC_VIEWS && !v; i++)
        if (g_sec_view[i].sec && g_sec_view[i].addr == addr && g_sec_view[i].size == size) v = &g_sec_view[i];
    u64 res = 0;
    if (!v) {
        res = sec_err(FNVNX_SEC_E_RANGE);
    } else {
        FnvnxSection *s = &g_sec[v->sec - 1];
        Result rc = svcUnmapProcessMemory((void *)(uintptr_t)addr, g_own_process, s->base + v->off, size);
        if (R_FAILED(rc)) {
            res = sec_err(rc);
        } else {
            VirtmemReservation *own = v->own;
            memset(v, 0, sizeof *v);
            s->views--;
            if (own) {
                virtmemLock();
                virtmemRemoveReservation(own);
                virtmemUnlock();
            }
            if (!s->live && !s->views) {
                Result r2 = sec_release(s);
                if (R_SUCCEEDED(rc)) rc = r2;
            }
            if (R_FAILED(rc)) res = sec_err(rc);
        }
    }
    mutexUnlock(&g_sec_lock);
    return res;
}

static u64 host_section_free(u64 id) {
    mutexLock(&g_sec_lock);
    FnvnxSection *s = sec_live(id);
    u64 res = 0;
    if (!s) {
        res = sec_err(FNVNX_SEC_E_ID);
    } else {
        s->live = 0;   /* the id is dead now; with views the pages stay until the last one is unmapped (P5) */
        if (!s->views) {
            Result rc = sec_release(s);
            if (R_FAILED(rc)) res = sec_err(rc);
        }
    }
    mutexUnlock(&g_sec_lock);
    return res;
}
