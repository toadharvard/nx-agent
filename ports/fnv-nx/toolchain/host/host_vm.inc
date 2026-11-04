/* host_vm.inc - VirtualAlloc's address space on the device (FNVNX_PLAT_VM_*, host.c platform_ext; the guest's
 * os_vm_* in toolchain/guest/os_guest.c, platform/win32/memory.cpp above it).
 *
 * Horizon has no demand paging: an application's memory is its heap, and svcMapMemory can show heap pages at another
 * address of the stack region (MemType_MappedMemory). So a reservation is address space only (a libnx virtmem
 * reservation in the stack region, or at the address the game asks for: VirtualReservationSearch probes 16 MiB
 * steps, the scrap heaps reserve 8 MiB each), and committing pages maps a fresh, zeroed heap block there (one block
 * per commit call, an "extent"). Decommitting a whole extent unmaps it and gives the block back to the heap;
 * decommitting part of one zeroes that part (the contents are gone, as on Windows; the memory stays). Releasing a
 * reservation unmaps everything in it. Before, the guest took backed heap memory for every reservation up front and
 * could not honour a fixed address.
 *
 * Standalone (<switch.h>, malloc) so tests/s4/backends runs it under qemu with the svc and virtmem calls stubbed. */
#define FNVNX_VM_RESV 256
#define FNVNX_VM_EXTENTS 8192
#define FNVNX_VM_ALIGN 0x10000ull   /* Windows' allocation granularity */
#define FNVNX_VM_NO_GO_LO 0x00400000ull   /* the original image and the data window (S10's fixed ranges) */
#define FNVNX_VM_NO_GO_HI 0x02100000ull

typedef struct { u64 addr, size; void *rv; } FnvnxVmResv;
typedef struct { u64 addr, size; void *src; } FnvnxVmExtent;
static FnvnxVmResv g_vm_resv[FNVNX_VM_RESV];
static FnvnxVmExtent g_vm_ext[FNVNX_VM_EXTENTS];
static int g_vm_next;
static Mutex g_vm_lock;

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

static FnvnxVmResv *vm_resv_of(u64 a, u64 n) {
    for (int i = 0; i < FNVNX_VM_RESV; i++) {
        FnvnxVmResv *r = &g_vm_resv[i];
        if (r->size && a >= r->addr && a + n <= r->addr + r->size && a + n >= a) return r;
    }
    return NULL;
}

/* (hint or 0, size) -> the reservation's address, 0 if none */
static u64 host_vm_reserve(u64 hint, u64 size) {
    if (!size) return 0;
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
    u64 lo = a & ~0xFFFull, hi = (a + n + 0xFFF) & ~0xFFFull;
    mutexLock(&g_vm_lock);
    Result rc = 0;
    if (!n || !vm_resv_of(lo, hi - lo)) rc = MAKERESULT(Module_Libnx, LibnxError_BadInput);
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
                rc = MAKERESULT(Module_Libnx, LibnxError_OutOfMemory);
                break;
            }
            memset(src, 0, len);   /* unreachable from src once mapped */
            rc = svcMapMemory((void *)(uintptr_t)p, src, len);
            if (R_FAILED(rc)) {
                free(src);
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
    u64 lo = a & ~0xFFFull, hi = (a + n + 0xFFF) & ~0xFFFull;
    Result rc = 0;
    mutexLock(&g_vm_lock);
    for (int i = 0; i < g_vm_next; i++) {
        FnvnxVmExtent *e = &g_vm_ext[i];
        if (!e->size || e->addr >= hi || e->addr + e->size <= lo) continue;
        if (e->addr >= lo && e->addr + e->size <= hi) {
            Result r = svcUnmapMemory((void *)(uintptr_t)e->addr, e->src, e->size);
            if (R_SUCCEEDED(r)) {
                free(e->src);
                e->size = 0;
            } else if (!rc) {
                rc = r;
            }
        } else {
            u64 s = e->addr > lo ? e->addr : lo, t = e->addr + e->size < hi ? e->addr + e->size : hi;
            memset((void *)(uintptr_t)s, 0, t - s);
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
        return MAKERESULT(Module_Libnx, LibnxError_BadInput);
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
