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
    unsigned caller;    /* the memory lane (memtrack.cpp): the VirtualAlloc call that reserved it */
};
#define MEM_RA ((unsigned)(uintptr_t)__builtin_return_address(0))
enum { MEMK_HEAP = 0, MEMK_VIRTUAL = 1 };

static size_t committed_pages(struct region *r, uintptr_t a, size_t n)
{
    size_t c = 0;
    for (size_t p = (a - r->base) / PAGE, last = (a + n - 1 - r->base) / PAGE; p <= last; p++)
        c += r->committed[p];
    return c;
}
static struct region g_regions[MAX_REGIONS];
static int g_nregions;
static os_lock_t g_vm_lock;

/* g_regions is sorted by base (regions never overlap): a binary search. The game commits its pools one page per
 * VirtualAlloc call (MemoryPool::CommitPage, ~340,000 calls to the main menu), so a linear scan of hundreds of
 * regions per call cost seconds of emulation (S5's profile: 358,223 calls, 26 s). */
static int region_index(uintptr_t a)   /* the first region whose base is above a */
{
    int lo = 0, hi = g_nregions;
    while (lo < hi) {
        int m = (lo + hi) / 2;
        if (g_regions[m].base <= a)
            lo = m + 1;
        else
            hi = m;
    }
    return lo;
}

static struct region *region_of(uintptr_t a)
{
    int i = region_index(a) - 1;
    return i >= 0 && a < g_regions[i].base + g_regions[i].size ? &g_regions[i] : NULL;
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
        if (w32_mem_on)
            fnvnx_mem_delta(MEMK_VIRTUAL, (long long)((q - p) * PAGE), r->caller);
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
    unsigned caller = MEM_RA;
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
        uint8_t *pages = (uint8_t *)calloc(rsz / PAGE, 1);
        if (!pages) {
            os_vm_release(p, rsz);
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            goto out;
        }
        int at = region_index((uintptr_t)p);   /* keep g_regions sorted */
        memmove(&g_regions[at + 1], &g_regions[at], (size_t)(g_nregions - at) * sizeof g_regions[0]);
        r = &g_regions[at];
        r->base = (uintptr_t)p;
        r->size = rsz;
        r->protect = protect;
        r->caller = caller;
        r->committed = pages;
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
    if (!res && w32_mem_on && GetLastError() == ERROR_NOT_ENOUGH_MEMORY)
        fnvnx_mem_fail(MEMK_VIRTUAL, size, caller);
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
            if (w32_mem_on)
                fnvnx_mem_delta(MEMK_VIRTUAL, -(long long)(committed_pages(r, r->base, r->size) * PAGE), r->caller);
            os_vm_release((void *)r->base, r->size);
            free(r->committed);
            int at = (int)(r - g_regions);
            memmove(r, r + 1, (size_t)(g_nregions - at - 1) * sizeof g_regions[0]);
            g_nregions--;
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
            if (w32_mem_on)
                fnvnx_mem_delta(MEMK_VIRTUAL, -(long long)(committed_pages(r, pa, n) * PAGE), r->caller);
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

/* ---- heaps over the C heap (the engine replaces allocation with its own MemoryManager).
 * Every block carries a header {heap, requested size, capacity, list links}, 16-byte aligned, in front of the
 * payload. A heap is {flags, fixed-size limit, bytes in use, block list, lock}; blocks belong to the heap in their
 * header. Unless HEAP_NO_SERIALIZE is in effect (call flags | heap flags), every operation holds the heap's lock. */
#define PROCESS_HEAP ((HANDLE)(uintptr_t)0x00150000u)
#ifndef STATUS_NO_MEMORY
#define STATUS_NO_MEMORY 0xC0000017u
#endif
#ifndef HEAP_NO_SERIALIZE
#define HEAP_NO_SERIALIZE 0x00000001
#endif
#ifndef HEAP_GENERATE_EXCEPTIONS
#define HEAP_GENERATE_EXCEPTIONS 0x00000004
#endif
#define HEAP_MAX_BLOCK 0x7FFF8u   /* the largest single block of a fixed-size heap (MSDN) */
#define HEAP_PAGE 4096u

struct w32_heap;
struct alignas(16) heap_blk {
    w32_heap *heap;
    size_t size, cap;   /* requested size; usable bytes (size <= cap) */
    heap_blk *prev, *next;
};
struct w32_heap {
    DWORD flags;        /* HEAP_NO_SERIALIZE | HEAP_GENERATE_EXCEPTIONS from HeapCreate */
    size_t limit;       /* 0: growable; else total requested bytes allowed */
    size_t used;        /* total requested bytes of live blocks */
    heap_blk *blocks;
    os_lock_t lock;
    w32_heap *next;     /* registry of created heaps (HeapDestroy's handle check) */
};

static w32_heap g_proc_heap;
static w32_heap *g_heaps;   /* created heaps */
static os_lock_t g_heaps_lock;

static w32_heap *heap_of(HANDLE h) { return h == PROCESS_HEAP ? &g_proc_heap : (w32_heap *)h; }
static heap_blk *blk_of(const void *p) { return (heap_blk *)p - 1; }
static size_t cap_of(size_t n) { return n ? (n + 15) & ~(size_t)15 : 16; }

static void blk_link(w32_heap *h, heap_blk *b)
{
    b->heap = h;
    b->prev = NULL;
    b->next = h->blocks;
    if (h->blocks) h->blocks->prev = b;
    h->blocks = b;
}
static void blk_unlink(w32_heap *h, heap_blk *b)
{
    if (b->prev) b->prev->next = b->next; else h->blocks = b->next;
    if (b->next) b->next->prev = b->prev;
}
/* may a block of n bytes replace one of old bytes (0 for a new block) in h? */
static bool heap_fits(const w32_heap *h, size_t old, size_t n)
{
    if (!h->limit) return true;
    return n <= HEAP_MAX_BLOCK && n <= h->limit && h->used - old <= h->limit - n;
}
static void heap_enter(w32_heap *h, DWORD eff) { if (!(eff & HEAP_NO_SERIALIZE)) os_lock(&h->lock); }
static void heap_leave(w32_heap *h, DWORD eff) { if (!(eff & HEAP_NO_SERIALIZE)) os_unlock(&h->lock); }
static DWORD heap_eff(HANDLE hh, DWORD flags) { return W32_DW(flags) | heap_of(hh)->flags; }

/* an allocation failed: with HEAP_GENERATE_EXCEPTIONS the caller raises instead of returning NULL */
static void *heap_failed(DWORD eff)
{
    if (eff & HEAP_GENERATE_EXCEPTIONS)
        RaiseException(STATUS_NO_MEMORY, 0, 0, NULL);
    return NULL;
}

static void *heap_new(w32_heap *h, DWORD eff, size_t n)   /* under the heap's lock */
{
    if (!heap_fits(h, 0, n) || n > (size_t)-1 / 2) return NULL;
    size_t cap = cap_of(n);
    heap_blk *b = (heap_blk *)malloc(sizeof *b + cap);
    if (!b) return NULL;
    b->size = n;
    b->cap = cap;
    blk_link(h, b);
    h->used += n;
    void *p = b + 1;
    if (eff & HEAP_ZERO_MEMORY) memset(p, 0, cap);
    return p;
}

/* in place when the new size fits the block's capacity; the grown part is zeroed on request */
static bool heap_resize_inplace(w32_heap *h, heap_blk *b, DWORD eff, size_t n)
{
    if (n > b->cap || !heap_fits(h, b->size, n)) return false;
    if ((eff & HEAP_ZERO_MEMORY) && n > b->size) memset((char *)(b + 1) + b->size, 0, n - b->size);
    h->used = h->used - b->size + n;
    b->size = n;
    return true;
}

static void heap_free_blk(w32_heap *h, heap_blk *b)   /* under the heap's lock; after the memory lane forgot it */
{
    blk_unlink(h, b);
    h->used -= b->size;
    free(b);
}

extern "C" HANDLE GetProcessHeap(void) { return PROCESS_HEAP; }

extern "C" HANDLE HeapCreate(DWORD f, SIZE_T i, SIZE_T m)
{
    w32_heap *h = (w32_heap *)calloc(1, sizeof *h);
    if (!h) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    h->flags = W32_DW(f) & (HEAP_NO_SERIALIZE | HEAP_GENERATE_EXCEPTIONS);
    if (m) {   /* fixed size: rounded up to pages, and at least the initial size */
        size_t lim = m < i ? i : m;
        h->limit = (lim + HEAP_PAGE - 1) & ~(size_t)(HEAP_PAGE - 1);
        if (!h->limit) h->limit = lim;   /* wrapped around: the largest size */
    }
    os_lock(&g_heaps_lock);
    h->next = g_heaps;
    g_heaps = h;
    os_unlock(&g_heaps_lock);
    return (HANDLE)h;
}

extern "C" BOOL HeapDestroy(HANDLE hh)
{
    w32_heap *h = NULL;
    os_lock(&g_heaps_lock);   /* only a created, not yet destroyed heap is valid (never the process heap) */
    for (w32_heap **pp = &g_heaps; *pp; pp = &(*pp)->next)
        if ((HANDLE)*pp == hh) {
            h = *pp;
            *pp = h->next;
            break;
        }
    os_unlock(&g_heaps_lock);
    if (!h) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    int in = fnvnx_mem_enter(MEM_RA);
    os_lock(&h->lock);
    while (h->blocks) {
        heap_blk *b = h->blocks;
        if (in) fnvnx_mem_forget(MEMK_HEAP, b + 1);
        heap_free_blk(h, b);
    }
    os_unlock(&h->lock);
    free(h);
    fnvnx_mem_leave(in);
    return TRUE;
}

static void *heap_alloc(unsigned ra, HANDLE hh, DWORD flags, SIZE_T n)
{
    w32_heap *h = heap_of(hh);
    DWORD eff = heap_eff(hh, flags);
    int in = fnvnx_mem_enter(ra);   /* the memory lane: recorded here, by the game's call site */
    heap_enter(h, eff);
    void *p = heap_new(h, eff, n);
    heap_leave(h, eff);
    if (in) {
        if (p) fnvnx_mem_note(MEMK_HEAP, p, n, ra); else fnvnx_mem_fail(MEMK_HEAP, n, ra);
        fnvnx_mem_leave(in);
    }
    return p ? p : heap_failed(eff);
}

extern "C" LPVOID HeapAlloc(HANDLE h, DWORD flags, SIZE_T n) { return heap_alloc(MEM_RA, h, flags, n); }

extern "C" LPVOID HeapReAlloc(HANDLE hh, DWORD flags, LPVOID p, SIZE_T n)
{
    if (!p) {   /* unlike realloc, a NULL block is an error */
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    unsigned ra = MEM_RA;
    w32_heap *h = heap_of(hh);
    DWORD eff = heap_eff(hh, flags);
    heap_blk *b = blk_of(p);
    int in = fnvnx_mem_enter(ra);
    heap_enter(h, eff);
    size_t old = b->size;
    if (in)   /* before: once p is freed, another thread may get the address */
        fnvnx_mem_forget(MEMK_HEAP, p);
    void *q = NULL;
    if (heap_resize_inplace(h, b, eff, n)) {
        q = p;
    } else if (!(eff & HEAP_REALLOC_IN_PLACE_ONLY) && (q = heap_new(h, eff & ~(DWORD)HEAP_ZERO_MEMORY, n)) != NULL) {
        size_t keep = old < n ? old : n;
        memcpy(q, p, keep);
        if ((eff & HEAP_ZERO_MEMORY) && n > keep) memset((char *)q + keep, 0, n - keep);
        heap_free_blk(h, b);
    }
    heap_leave(h, eff);
    if (in) {
        if (q) fnvnx_mem_note(MEMK_HEAP, q, n, ra);
        else { fnvnx_mem_note(MEMK_HEAP, p, old, ra); fnvnx_mem_fail(MEMK_HEAP, n, ra); }   /* p stays valid */
        fnvnx_mem_leave(in);
    }
    return q ? q : heap_failed(eff);
}

static BOOL heap_free(unsigned ra, HANDLE hh, DWORD flags, LPVOID p)
{
    if (!p)
        return TRUE;
    w32_heap *h = heap_of(hh);
    DWORD eff = heap_eff(hh, flags);
    int in = fnvnx_mem_enter(ra);
    if (in)
        fnvnx_mem_forget(MEMK_HEAP, p);
    heap_enter(h, eff);
    heap_free_blk(h, blk_of(p));
    heap_leave(h, eff);
    fnvnx_mem_leave(in);
    return TRUE;
}

extern "C" BOOL HeapFree(HANDLE h, DWORD flags, LPVOID p) { return heap_free(MEM_RA, h, flags, p); }

extern "C" SIZE_T HeapSize(HANDLE hh, DWORD flags, LPCVOID p)
{
    if (!p) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return (SIZE_T)-1;
    }
    w32_heap *h = heap_of(hh);
    DWORD eff = heap_eff(hh, flags);
    heap_enter(h, eff);
    size_t n = blk_of(p)->size;
    heap_leave(h, eff);
    return n;
}

/* LMEM_FIXED blocks are blocks of the process heap */
extern "C" HANDLE LocalAlloc(UINT flags, SIZE_T n)
{
    return (HANDLE)heap_alloc(MEM_RA, PROCESS_HEAP, (flags & 0x40 /* LMEM_ZEROINIT */) ? HEAP_ZERO_MEMORY : 0, n);
}

extern "C" HANDLE LocalFree(HANDLE h)
{
    heap_free(MEM_RA, PROCESS_HEAP, 0, h);
    return NULL;
}

/* ---- status
 * The page-file fields are Windows' commit limit: RAM plus the page file (a system-managed one is about RAM's
 * size), so their free part is free RAM plus the free page file, far above free RAM. The heap's constructor
 * 0x00aa3880 shows a message box and exits when ullAvailPageFile < 512 MB (the only threshold the exe checks:
 * GlobalMemoryStatus at 0x00aa9879 only logs); with free RAM there the device would trip it as the process grows.
 * Here: total = 2 x RAM, free = free RAM + RAM. */
static uint64_t pagefile_total(uint64_t t) { return 2 * t; }
static uint64_t pagefile_avail(uint64_t t, uint64_t a) { return a + t; }

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
    ms->dwTotalPageFile = pagefile_total(t) > cap ? cap : (SIZE_T)pagefile_total(t);
    ms->dwAvailPageFile = pagefile_avail(t, a) > cap ? cap : (SIZE_T)pagefile_avail(t, a);
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
    ms->ullTotalPageFile = pagefile_total(t);
    ms->ullAvailPageFile = pagefile_avail(t, a);
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
