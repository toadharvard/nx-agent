/* memory.cpp - VirtualAlloc/VirtualFree/VirtualQuery over os_vm_*, the heap functions over malloc, memory
 * status and system info.
 *
 * Regions follow Windows' granularity: reservations are 64 KiB aligned, commits are page (4 KiB) granular.
 * A table of reservations lets MEM_RELEASE with size 0 free the whole region and VirtualQuery answer. */
#include "w32.h"

#include <malloc.h>
#include <stdlib.h>

#define GRAN 0x10000u
#define PAGE 0x1000u
#define MAX_REGIONS 4096

struct region {
    uintptr_t base;
    size_t size;
    DWORD protect;
    uint8_t *committed; /* one byte per page */
};
static struct region g_regions[MAX_REGIONS];
static int g_nregions;
static os_lock_t g_vm_lock;

static struct region *region_of(uintptr_t a)
{
    for (int i = 0; i < g_nregions; i++)
        if (a >= g_regions[i].base && a < g_regions[i].base + g_regions[i].size)
            return &g_regions[i];
    return NULL;
}

static int commit_range(struct region *r, uintptr_t a, size_t n)
{
    size_t first = (a - r->base) / PAGE, last = (a + n - 1 - r->base) / PAGE;
    for (size_t p = first; p <= last;) {
        if (r->committed[p]) {
            p++;
            continue;
        }
        size_t q = p;
        while (q <= last && !r->committed[q])
            q++;
        if (os_vm_commit((void *)(r->base + p * PAGE), (q - p) * PAGE))
            return -1;
        memset(r->committed + p, 1, q - p);
        p = q;
    }
    return 0;
}

extern "C" LPVOID VirtualAlloc(LPVOID addr, SIZE_T size, DWORD type, DWORD protect)
{
    type = W32_DW(type);
    if (!size || !(type & (MEM_COMMIT | MEM_RESERVE))) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    os_lock(&g_vm_lock);
    LPVOID res = NULL;
    uintptr_t a = (uintptr_t)addr;
    struct region *r = a ? region_of(a) : NULL;
    if ((type & MEM_RESERVE) || !r) {
        if (!(type & MEM_RESERVE) && a) { /* commit outside any reservation */
            SetLastError(ERROR_INVALID_ADDRESS);
            goto out;
        }
        if (g_nregions == MAX_REGIONS) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            goto out;
        }
        uintptr_t base = a & ~(uintptr_t)(GRAN - 1);
        size_t sz = ((a + size - base) + PAGE - 1) & ~(size_t)(PAGE - 1);
        if (!a)
            sz = (size + PAGE - 1) & ~(size_t)(PAGE - 1);
        size_t rsz = (sz + GRAN - 1) & ~(size_t)(GRAN - 1);
        void *p = os_vm_reserve(a ? (void *)base : NULL, rsz);
        if (!p) {
            SetLastError(a ? ERROR_INVALID_ADDRESS : ERROR_NOT_ENOUGH_MEMORY);
            goto out;
        }
        r = &g_regions[g_nregions];
        r->base = (uintptr_t)p;
        r->size = rsz;
        r->protect = protect;
        r->committed = (uint8_t *)calloc(rsz / PAGE, 1);
        if (!r->committed) {
            os_vm_release(p, rsz);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            goto out;
        }
        g_nregions++;
        a = (uintptr_t)p + (a ? a - base : 0);
        if (type & MEM_COMMIT) {
            if (commit_range(r, a & ~(uintptr_t)(PAGE - 1), size + (a & (PAGE - 1)))) {
                SetLastError(ERROR_NOT_ENOUGH_MEMORY);
                goto out;
            }
        }
        res = (LPVOID)(type & MEM_COMMIT ? (a & ~(uintptr_t)(PAGE - 1)) : (uintptr_t)p);
    } else {
        uintptr_t pa = a & ~(uintptr_t)(PAGE - 1);
        size_t n = size + (a - pa);
        if (pa + n > r->base + r->size) {
            SetLastError(ERROR_INVALID_ADDRESS);
            goto out;
        }
        if (commit_range(r, pa, n)) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            goto out;
        }
        res = (LPVOID)pa;
    }
out:
    os_unlock(&g_vm_lock);
    return res;
}

extern "C" BOOL VirtualFree(LPVOID addr, SIZE_T size, DWORD type)
{
    type = W32_DW(type);
    os_lock(&g_vm_lock);
    BOOL ok = FALSE;
    uintptr_t a = (uintptr_t)addr;
    struct region *r = region_of(a);
    if (!r) {
        SetLastError(ERROR_INVALID_ADDRESS);
    } else if (type & MEM_RELEASE) {
        if (size || a != r->base) {
            SetLastError(ERROR_INVALID_PARAMETER);
        } else {
            os_vm_release((void *)r->base, r->size);
            free(r->committed);
            *r = g_regions[--g_nregions];
            ok = TRUE;
        }
    } else if (type & MEM_DECOMMIT) {
        uintptr_t pa = a & ~(uintptr_t)(PAGE - 1);
        size_t n = size ? ((a + size - pa) + PAGE - 1) & ~(size_t)(PAGE - 1) : r->base + r->size - pa;
        if (!size && a != r->base) {
            SetLastError(ERROR_INVALID_PARAMETER);
        } else if (pa + n > r->base + r->size) {
            SetLastError(ERROR_INVALID_PARAMETER);
        } else {
            os_vm_decommit((void *)pa, n);
            memset(r->committed + (pa - r->base) / PAGE, 0, n / PAGE);
            ok = TRUE;
        }
    } else {
        SetLastError(ERROR_INVALID_PARAMETER);
    }
    os_unlock(&g_vm_lock);
    return ok;
}

extern "C" SIZE_T VirtualQuery(LPCVOID addr, PMEMORY_BASIC_INFORMATION mbi, SIZE_T len)
{
    if (len < sizeof *mbi)
        return 0;
    uintptr_t a = (uintptr_t)addr & ~(uintptr_t)(PAGE - 1);
    os_lock(&g_vm_lock);
    struct region *r = region_of(a);
    memset(mbi, 0, sizeof *mbi);
    mbi->BaseAddress = (PVOID)a;
    if (!r) {
        mbi->State = MEM_FREE;
        mbi->Protect = PAGE_NOACCESS;
        mbi->RegionSize = PAGE;
    } else {
        size_t p = (a - r->base) / PAGE, q = p;
        uint8_t c = r->committed[p];
        while (q < r->size / PAGE && r->committed[q] == c)
            q++;
        mbi->AllocationBase = (PVOID)r->base;
        mbi->AllocationProtect = r->protect;
        mbi->RegionSize = (q - p) * PAGE;
        mbi->State = c ? MEM_COMMIT : MEM_RESERVE;
        mbi->Protect = c ? r->protect : 0;
        mbi->Type = MEM_PRIVATE;
    }
    os_unlock(&g_vm_lock);
    return sizeof *mbi;
}

/* ---- heaps: every heap is the C heap (the engine replaces allocation with its own MemoryManager) */
#define PROCESS_HEAP ((HANDLE)(uintptr_t)0x00150000u)

static size_t usable(void *p)
{
#if defined(__GLIBC__) || defined(__NEWLIB__) || defined(__SWITCH__)
    return malloc_usable_size(p);
#else
    (void)p;
    return 0;
#endif
}

extern "C" HANDLE GetProcessHeap(void) { return PROCESS_HEAP; }
extern "C" HANDLE HeapCreate(DWORD f, SIZE_T i, SIZE_T m) { W32_UNUSED(f); W32_UNUSED(i); W32_UNUSED(m); return PROCESS_HEAP; }
extern "C" BOOL HeapDestroy(HANDLE h) { W32_UNUSED(h); return TRUE; }

extern "C" LPVOID HeapAlloc(HANDLE h, DWORD flags, SIZE_T n)
{
    W32_UNUSED(h);
    void *p = (W32_DW(flags) & HEAP_ZERO_MEMORY) ? calloc(1, n ? n : 1) : malloc(n ? n : 1);
    if (!p)
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return p;
}

extern "C" LPVOID HeapReAlloc(HANDLE h, DWORD flags, LPVOID p, SIZE_T n)
{
    W32_UNUSED(h);
    if (W32_DW(flags) & HEAP_REALLOC_IN_PLACE_ONLY)
        return usable(p) >= n ? p : NULL;
    size_t old = (W32_DW(flags) & HEAP_ZERO_MEMORY) ? usable(p) : 0;
    void *q = realloc(p, n ? n : 1);
    if (q && n > old && (W32_DW(flags) & HEAP_ZERO_MEMORY))
        memset((char *)q + old, 0, n - old);
    if (!q)
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return q;
}

extern "C" BOOL HeapFree(HANDLE h, DWORD flags, LPVOID p)
{
    W32_UNUSED(h);
    W32_UNUSED(flags);
    free(p);
    return TRUE;
}

extern "C" SIZE_T HeapSize(HANDLE h, DWORD flags, LPCVOID p)
{
    W32_UNUSED(h);
    W32_UNUSED(flags);
    return usable((void *)p);
}

extern "C" HANDLE LocalAlloc(UINT flags, SIZE_T n)
{
    return (HANDLE)((flags & 0x40 /* LMEM_ZEROINIT */) ? calloc(1, n ? n : 1) : malloc(n ? n : 1));
}

extern "C" HANDLE LocalFree(HANDLE h)
{
    free(h);
    return NULL;
}

/* ---- status */
extern "C" void GlobalMemoryStatus(LPMEMORYSTATUS ms)
{
    uint64_t t, a;
    os_memory_info(&t, &a);
    /* 32-bit SIZE_T fields saturate at 4 GiB like on a 32-bit Windows process */
    SIZE_T cap = (SIZE_T)(sizeof(SIZE_T) == 4 ? 0xFFFFFFFFu : ~(SIZE_T)0);
    ms->dwLength = sizeof *ms;
    ms->dwMemoryLoad = t ? (DWORD)(100 - a * 100 / t) : 0;
    ms->dwTotalPhys = t > cap ? cap : (SIZE_T)t;
    ms->dwAvailPhys = a > cap ? cap : (SIZE_T)a;
    ms->dwTotalPageFile = ms->dwTotalPhys;
    ms->dwAvailPageFile = ms->dwAvailPhys;
    ms->dwTotalVirtual = (SIZE_T)0x7FFE0000u;
    ms->dwAvailVirtual = ms->dwAvailPhys < ms->dwTotalVirtual ? ms->dwAvailPhys : ms->dwTotalVirtual;
}

extern "C" BOOL GlobalMemoryStatusEx(LPMEMORYSTATUSEX ms)
{
    uint64_t t, a;
    os_memory_info(&t, &a);
    ms->dwLength = sizeof *ms;
    ms->dwMemoryLoad = t ? (DWORD)(100 - a * 100 / t) : 0;
    ms->ullTotalPhys = t;
    ms->ullAvailPhys = a;
    ms->ullTotalPageFile = t;
    ms->ullAvailPageFile = a;
    ms->ullTotalVirtual = 0x7FFE0000ull;
    ms->ullAvailVirtual = a < 0x7FFE0000ull ? a : 0x7FFE0000ull;
    ms->ullAvailExtendedVirtual = 0;
    return TRUE;
}

extern "C" void GetSystemInfo(LPSYSTEM_INFO si)
{
    memset(si, 0, sizeof *si);
    si->wProcessorArchitecture = 0; /* PROCESSOR_ARCHITECTURE_INTEL: what the game was built for */
    si->dwPageSize = PAGE;
    si->lpMinimumApplicationAddress = (LPVOID)(uintptr_t)0x00010000u;
    si->lpMaximumApplicationAddress = (LPVOID)(uintptr_t)0x7FFEFFFFu;
    unsigned n = os_cpu_count();
    si->dwActiveProcessorMask = ((DWORD_PTR)1 << n) - 1;
    si->dwNumberOfProcessors = n;
    si->dwProcessorType = 586; /* PROCESSOR_INTEL_PENTIUM */
    si->dwAllocationGranularity = GRAN;
    si->wProcessorLevel = 6;
}
