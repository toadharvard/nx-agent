/* memory.cpp - the virtual-memory part: ONE page map of the process' 32-bit user address space
 * [0x00010000, 0x7FFEFFFF] (ENV_TRUTH P1: page 4 KiB, allocation granularity 64 KiB); VirtualAlloc, VirtualFree,
 * VirtualProtect, VirtualQuery, IsBad*Ptr (module.cpp calls w32_vm_probe), GlobalMemoryStatus(Ex) are functions of that
 * map, and keep the real OS protection (os_vm_*) in step with it. Then the heap functions over malloc, memory status
 * and system info.
 *
 * Every page of the range has: state (FREE / RESERVE / COMMIT), type (MEM_PRIVATE / MEM_MAPPED / MEM_IMAGE), the
 * allocation it belongs to (its base), the allocation protection and the current protection (with the PAGE_GUARD /
 * PAGE_NOCACHE / PAGE_WRITECOMBINE modifier bits as given). The real protection of a page is a function of its
 * current protection: none for a guard page (its first access faults; fnvnx_win32_vm_fault turns that into the
 * guard-page exception and clears the bit), none for reserved pages, else the access of the PAGE_* value.
 * Memory the platform or the loader got elsewhere is entered with fnvnx_win32_vm_note (flag F_FOREIGN: the OS
 * range is not ours to release); file views are entered by filemap.cpp through w32_vm_view_*. */
#include "w32.h"
#include "reach.h"

#include <malloc.h>
#include <stdlib.h>

#define GRAN 0x10000u
#define PAGE 0x1000u
#define UMIN 0x00010000u     /* lowest user address */
#define UEND 0x7FFF0000u     /* one past the highest user address (lpMaximumApplicationAddress + 1) */
#define NPAGES ((UEND - UMIN) / PAGE)
#define PPG (GRAN / PAGE)    /* pages per granule */

enum { ST_FREE = 0, ST_RESERVE = 1, ST_COMMIT = 2 };
enum { TY_PRIVATE = 0, TY_MAPPED = 1, TY_IMAGE = 2 };
/* F_ALIAS_PF / F_ALIAS_FILE: the page is a view of a host section object (filemap_backing.h FM_ALIAS_*), which the OS cannot re-protect */
enum { F_FOREIGN = 1, F_VIEW = 2, F_RESERVESEC = 4, F_RESET = 8, F_NEW = 16, F_WATCH = 32, F_ALIAS_PF = 64, F_ALIAS_FILE = 128 };
struct pg {
    uint32_t base;      /* allocation base address >> 12; 0: free */
    uint32_t caller;    /* the memory lane (memtrack.cpp): the call that reserved the allocation */
    uint16_t cur, aprot;
    uint8_t state, type, flags;
    uint8_t wdirty;     /* F_WATCH: written since the last reset (the page is writable at the OS level again) */
};
#define MEM_RA ((unsigned)(uintptr_t)__builtin_return_address(0))
enum { MEMK_HEAP = 0, MEMK_VIRTUAL = 1 };

static struct pg *g_pg;          /* NPAGES entries, zero = free; calloc'd lazily (untouched pages cost nothing) */
static size_t g_free_pages = NPAGES;
static os_lock_t g_vm_lock;

#define PROT_BASE_MASK 0xFFu
#define PROT_MOD_MASK (PAGE_GUARD | PAGE_NOCACHE | PAGE_WRITECOMBINE)

/* a PAGE_* value: exactly one base protection, at most one modifier, and no modifier on PAGE_NOACCESS */
static bool prot_valid(DWORD p)
{
    DWORD b = p & PROT_BASE_MASK, m = p & PROT_MOD_MASK;
    if (p & ~(PROT_BASE_MASK | PROT_MOD_MASK))
        return false;
    if (!b || (b & (b - 1)))
        return false;
    if (m & (m - 1))
        return false;
    return !(m && b == PAGE_NOACCESS);
}
static bool prot_write(DWORD p) { DWORD b = p & PROT_BASE_MASK; return b == PAGE_READWRITE || b == PAGE_WRITECOPY || b == PAGE_EXECUTE_READWRITE || b == 0x80; }
static bool prot_exec(DWORD p) { return (p & PROT_BASE_MASK) >= PAGE_EXECUTE; }
static bool prot_read(DWORD p) { return (p & PROT_BASE_MASK) != PAGE_NOACCESS; }
/* the real protection: a guard page is inaccessible until its first access is turned into the exception */
static int os_prot_of(DWORD p)
{
    if ((p & PAGE_GUARD) || !prot_read(p))
        return OS_VM_NONE;
    return OS_VM_READ | (prot_write(p) ? OS_VM_WRITE : 0) | (prot_exec(p) ? OS_VM_EXEC : 0);
}

/* the protection the OS gets for a page of the map: a write-watched page that is not yet written is read-only, so
 * that its first write faults into fnvnx_win32_vm_fault, which records it */
static int os_prot_pg(const struct pg &p)
{
    int o = os_prot_of(p.cur);
    return (p.flags & F_WATCH) && !p.wdirty ? o & ~OS_VM_WRITE : o;
}

static bool pg_init(void)   /* under the lock */
{
    if (!g_pg)
        g_pg = (struct pg *)calloc(NPAGES, sizeof(struct pg));
    return g_pg != NULL;
}
static inline size_t pidx(uintptr_t a) { return (size_t)((a - UMIN) / PAGE); }
static inline uintptr_t paddr(size_t i) { return (uintptr_t)i * PAGE + UMIN; }

static void pg_set(size_t i, uintptr_t base, uint8_t state, uint8_t type, DWORD cur, DWORD aprot, uint8_t flags, uint32_t caller)
{
    if (g_pg[i].state == ST_FREE)
        g_free_pages--;
    g_pg[i].base = (uint32_t)(base / PAGE);
    g_pg[i].caller = caller;
    g_pg[i].cur = (uint16_t)cur;
    g_pg[i].aprot = (uint16_t)aprot;
    g_pg[i].state = state;
    g_pg[i].type = type;
    g_pg[i].flags = flags;
    g_pg[i].wdirty = 0;
}
static void pg_clear(size_t i)
{
    if (g_pg[i].state != ST_FREE)
        g_free_pages++;
    memset(&g_pg[i], 0, sizeof g_pg[i]);
}

/* bytes committed/uncommitted in [first, first + n) by caller, for the memory lane */
static void mem_account(size_t first, size_t n, bool committed, long long sign)
{
    if (!w32_mem_on)
        return;
    for (size_t i = first; i < first + n;) {
        if ((g_pg[i].state == ST_COMMIT) != committed) {
            i++;
            continue;
        }
        size_t j = i;
        while (j < first + n && (g_pg[j].state == ST_COMMIT) == committed && g_pg[j].caller == g_pg[i].caller)
            j++;
        fnvnx_mem_delta(MEMK_VIRTUAL, sign * (long long)((j - i) * PAGE), g_pg[i].caller);
        i = j;
    }
}

/* the extent (in pages) of the allocation page i belongs to, from its first page */
static size_t alloc_pages(size_t i)
{
    size_t j = i;
    while (j < NPAGES && g_pg[j].state != ST_FREE && g_pg[j].base == g_pg[i].base)
        j++;
    return j - i;
}

/* is every granule of [first, first + n pages) free? (a reservation owns its granules) */
static bool granules_free(size_t first, size_t n)
{
    size_t g0 = first & ~(size_t)(PPG - 1), g1 = (first + n + PPG - 1) & ~(size_t)(PPG - 1);
    if (g1 > NPAGES)
        return false;
    for (size_t i = g0; i < g1; i++)
        if (g_pg[i].state != ST_FREE)
            return false;
    return true;
}

/* first-fit / top-down search for n pages of free granules: the granule-aligned start page in [lo, hi) or NPAGES */
static size_t find_free(size_t n, bool top_down, size_t lo, size_t hi)
{
    size_t need = (n + PPG - 1) / PPG;
    if (!top_down) {
        size_t run = 0, start = 0;
        for (size_t g = lo / PPG; g < hi / PPG; g++) {
            bool f = true;
            for (size_t k = 0; k < PPG && f; k++)
                f = g_pg[g * PPG + k].state == ST_FREE;
            if (!f) {
                run = 0;
                continue;
            }
            if (!run)
                start = g;
            if (++run == need)
                return start * PPG;
        }
    } else {
        size_t run = 0;
        for (size_t g = hi / PPG; g-- > lo / PPG;) {
            bool f = true;
            for (size_t k = 0; k < PPG && f; k++)
                f = g_pg[g * PPG + k].state == ST_FREE;
            if (!f) {
                run = 0;
                continue;
            }
            if (++run == need)
                return g * PPG;
        }
    }
    return NPAGES;
}

typedef void *(*osmap_fn)(void *hint, size_t size, void *ctx);
static void *os_reserve_cb(void *hint, size_t size, void *) { return os_vm_reserve(hint, (size + GRAN - 1) & ~(size_t)(GRAN - 1)); }   /* the OS holds the whole granule */

/* places an allocation of n pages: at the granule of want (0: first fit or top down) with the OS range obtained by
 * osmap (hinted: exactly there). Returns the page index of the base or NPAGES with the last error set. */
static size_t place(uintptr_t want, size_t n, bool top_down, osmap_fn osmap, void *ctx)
{
    size_t span = (n + PPG - 1) / PPG * PPG;
    if (want) {
        size_t first = pidx(want & ~(uintptr_t)(GRAN - 1));
        if (!granules_free(first, n)) {
            SetLastError(ERROR_INVALID_ADDRESS);
            return NPAGES;
        }
        void *p = osmap((void *)paddr(first), n * PAGE, ctx);
        if (!p || p == W32_VIEW_FAIL) {
            if (!p)
                SetLastError(ERROR_INVALID_ADDRESS);
            return NPAGES;
        }
        return first;
    }
    size_t lo = 0, hi = NPAGES;
    for (;;) {
        size_t at = n <= NPAGES ? find_free(n, top_down, lo, hi) : NPAGES;
        if (at == NPAGES) {
            SetLastError(ERROR_NOT_ENOUGH_MEMORY);
            return NPAGES;
        }
        /* the OS may hold the address (the host's own mappings): the map is the rule, the next free fit is the answer */
        void *p = osmap((void *)paddr(at), n * PAGE, ctx);
        if (p == W32_VIEW_FAIL)   /* no range will do: the osmap has set the error */
            return NPAGES;
        if (p)
            return at;
        if (top_down)
            hi = at + span - PPG;
        else
            lo = at + PPG;
        if (lo >= hi)
            break;
    }
    SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return NPAGES;
}

/* commits pages [first, last] of a private allocation with protection prot; 0 or -1 (out of memory: this call's
 * commits are rolled back) */
static int commit_pages(size_t first, size_t last, DWORD prot)
{
    int want = os_prot_of(prot);
    bool fail = false;
    for (size_t p = first; p <= last && !fail;) {
        if (g_pg[p].state == ST_COMMIT) {
            p++;
            continue;
        }
        size_t q = p;
        while (q <= last && g_pg[q].state != ST_COMMIT)
            q++;
        if (os_vm_commit((void *)paddr(p), (q - p) * PAGE)) {
            fail = true;
            break;
        }
        mem_account(p, q - p, false, +1);
        for (size_t k = p; k < q; k++) {
            g_pg[k].state = ST_COMMIT;
            g_pg[k].cur = PAGE_READWRITE;   /* what os_vm_commit gives */
            g_pg[k].flags |= F_NEW;
        }
        p = q;
    }
    if (fail) {
        for (size_t p = first; p <= last; p++)
            if (g_pg[p].flags & F_NEW) {
                mem_account(p, 1, true, -1);
                os_vm_decommit((void *)paddr(p), PAGE);
                g_pg[p].state = ST_RESERVE;
                g_pg[p].cur = 0;
                g_pg[p].flags &= ~F_NEW;
            }
        return -1;
    }
    /* the protection: pages whose real protection differs from the wanted one. A new page is read-write at the OS
     * level whatever the map says; a watched page that is not written wants no write. */
    for (size_t p = first; p <= last; p++)
        g_pg[p].flags &= ~F_RESET;
    for (size_t p = first; p <= last;) {
        int want_p = (g_pg[p].flags & F_WATCH) && !g_pg[p].wdirty ? want & ~OS_VM_WRITE : want;
        int have = (g_pg[p].flags & F_NEW) ? OS_VM_READ | OS_VM_WRITE : os_prot_pg(g_pg[p]);
        if (have == want_p) {
            p++;
            continue;
        }
        size_t q = p + 1;
        while (q <= last) {
            int wq = (g_pg[q].flags & F_WATCH) && !g_pg[q].wdirty ? want & ~OS_VM_WRITE : want;
            int hq = (g_pg[q].flags & F_NEW) ? OS_VM_READ | OS_VM_WRITE : os_prot_pg(g_pg[q]);
            if (hq == wq || wq != want_p)
                break;
            q++;
        }
        os_vm_protect((void *)paddr(p), (q - p) * PAGE, want_p);
        p = q;
    }
    for (size_t p = first; p <= last; p++) {
        g_pg[p].cur = (uint16_t)prot;
        g_pg[p].flags &= ~F_NEW;
    }
    return 0;
}

static void release_alloc(size_t first)   /* a private allocation, all of it */
{
    size_t n = alloc_pages(first);
    mem_account(first, n, true, -1);
    os_vm_release((void *)paddr(first), (n + PPG - 1) / PPG * PPG * PAGE);
    for (size_t i = 0; i < n; i++)
        pg_clear(first + i);
}

#define VA_KNOWN (MEM_COMMIT | MEM_RESERVE | MEM_RESET | MEM_RESET_UNDO | MEM_TOP_DOWN | MEM_WRITE_WATCH | MEM_PHYSICAL | MEM_LARGE_PAGES)

extern "C" LPVOID VirtualAlloc(LPVOID addr, SIZE_T size, DWORD type, DWORD protect)
{
    type = W32_DW(type);
    protect = W32_DW(protect);
    uint64_t a = (uint64_t)(uintptr_t)addr, sz = (uint64_t)size;
    if (sizeof(void *) == 4)
        sz = (uint64_t)(SIZE_T)size;
    unsigned caller = MEM_RA;
    LPVOID res = NULL;
    DWORD err = ERROR_INVALID_PARAMETER;

    /* the flags, as documented */
    if (type & ~(DWORD)VA_KNOWN)
        goto bad;
    if (type & (MEM_RESET | MEM_RESET_UNDO)) {   /* each stands alone */
        if ((type & (MEM_RESET | MEM_RESET_UNDO)) == (MEM_RESET | MEM_RESET_UNDO) || (type & ~(DWORD)(MEM_RESET | MEM_RESET_UNDO)))
            goto bad;
        if (!sz)
            goto bad;
    } else {
        if (!(type & (MEM_COMMIT | MEM_RESERVE)) || !sz)
            goto bad;
        if ((type & MEM_PHYSICAL) && (!(type & MEM_RESERVE) || (type & MEM_COMMIT) || (type & ~(DWORD)(MEM_RESERVE | MEM_PHYSICAL | MEM_TOP_DOWN))
                                      || (protect & PROT_BASE_MASK) != PAGE_READWRITE))
            goto bad;
        if ((type & MEM_LARGE_PAGES) && (type & (MEM_COMMIT | MEM_RESERVE)) != (MEM_COMMIT | MEM_RESERVE))
            goto bad;
        if ((type & MEM_WRITE_WATCH) && !(type & MEM_RESERVE))
            goto bad;
        if (!prot_valid(protect))
            goto bad;
        if (type & (MEM_PHYSICAL | MEM_LARGE_PAGES)) {   /* AWE and large pages need SeLockMemoryPrivilege: this process has none */
            err = ERROR_PRIVILEGE_NOT_HELD;
            goto bad;
        }
    }
    os_lock(&g_vm_lock);
    if (!pg_init()) {
        err = ERROR_NOT_ENOUGH_MEMORY;
        os_unlock(&g_vm_lock);
        goto bad;
    }
    if (type & (MEM_RESET | MEM_RESET_UNDO)) {
        uint64_t pa = a & ~(uint64_t)(PAGE - 1), pe = (a + sz + PAGE - 1) & ~(uint64_t)(PAGE - 1);
        err = ERROR_INVALID_ADDRESS;
        if (pa < UMIN || pe > UEND || pe < pa)
            goto unlock_bad;
        size_t f = pidx((uintptr_t)pa), n = (size_t)((pe - pa) / PAGE);
        for (size_t i = f; i < f + n; i++)
            if (g_pg[i].state != ST_COMMIT || g_pg[i].base != g_pg[f].base)
                goto unlock_bad;
        if (type & MEM_RESET) {   /* the contents become undefined: they are kept */
            for (size_t i = f; i < f + n; i++)
                g_pg[i].flags |= F_RESET;
        } else {
            for (size_t i = f; i < f + n; i++)
                if (!(g_pg[i].flags & F_RESET)) {
                    err = ERROR_INVALID_PARAMETER;
                    goto unlock_bad;
                }
            for (size_t i = f; i < f + n; i++)
                g_pg[i].flags &= ~F_RESET;
        }
        res = (LPVOID)(uintptr_t)pa;
        goto unlock_ok;
    }
    if ((type & MEM_RESERVE) || !a) {   /* a new allocation (a commit without an address reserves too) */
        uint64_t base = a & ~(uint64_t)(GRAN - 1), end = a ? (a + sz + PAGE - 1) & ~(uint64_t)(PAGE - 1) : sz;
        size_t np;
        if (a) {
            if (a + sz < a || base < UMIN || end > UEND) {
                err = ERROR_INVALID_PARAMETER;
                goto unlock_bad;
            }
            np = (size_t)((end - base) / PAGE);
        } else {
            if (sz > (uint64_t)(UEND - UMIN)) {
                err = ERROR_NOT_ENOUGH_MEMORY;
                goto unlock_bad;
            }
            np = (size_t)((sz + PAGE - 1) / PAGE);
        }
        size_t at = place(a ? (uintptr_t)base : 0, np, (type & MEM_TOP_DOWN) != 0, os_reserve_cb, NULL);
        if (at == NPAGES) {
            err = GetLastError();
            goto unlock_bad;
        }
        uintptr_t ab = paddr(at);
        for (size_t i = 0; i < np; i++)
            pg_set(at + i, ab, ST_RESERVE, TY_PRIVATE, 0, protect, (type & MEM_WRITE_WATCH) ? F_WATCH : 0, caller);
        if (type & MEM_COMMIT) {
            size_t f = a ? pidx((uintptr_t)(a & ~(uint64_t)(PAGE - 1))) : at, l = at + np - 1;
            if (commit_pages(f, l, protect)) {
                release_alloc(at);
                err = ERROR_NOT_ENOUGH_MEMORY;
                goto unlock_bad;
            }
        }
        res = (LPVOID)ab;
    } else {   /* commit inside an existing reservation */
        uint64_t pa = a & ~(uint64_t)(PAGE - 1), pe = (a + sz + PAGE - 1) & ~(uint64_t)(PAGE - 1);
        err = ERROR_INVALID_ADDRESS;
        if (pa < UMIN || a + sz < a || pe > UEND)
            goto unlock_bad;
        size_t f = pidx((uintptr_t)pa), l = pidx((uintptr_t)pe) - 1;
        for (size_t i = f; i <= l; i++)
            if (g_pg[i].state == ST_FREE || g_pg[i].base != g_pg[f].base)
                goto unlock_bad;
        uint8_t fl = g_pg[f].flags;
        if (fl & F_FOREIGN)
            goto unlock_bad;
        if (fl & F_VIEW) {   /* only the reserved part of a SEC_RESERVE section can be committed, within the view's access */
            if (!(fl & F_RESERVESEC))
                goto unlock_bad;
            if ((prot_write(protect) && !prot_write(g_pg[f].aprot)) || (prot_exec(protect) && !prot_exec(g_pg[f].aprot))) {
                err = ERROR_INVALID_PARAMETER;
                goto unlock_bad;
            }
            for (size_t i = f; i <= l; i++) {
                g_pg[i].state = ST_COMMIT;
                g_pg[i].cur = (uint16_t)protect;
            }
            os_vm_protect((void *)paddr(f), (l - f + 1) * PAGE, os_prot_of(protect));
        } else if (commit_pages(f, l, protect)) {
            err = ERROR_NOT_ENOUGH_MEMORY;
            goto unlock_bad;
        }
        res = (LPVOID)(uintptr_t)pa;
    }
unlock_ok:
    os_unlock(&g_vm_lock);
    return res;
unlock_bad:
    os_unlock(&g_vm_lock);
bad:
    SetLastError(err);
    if (err == ERROR_NOT_ENOUGH_MEMORY && w32_mem_on)
        fnvnx_mem_fail(MEMK_VIRTUAL, (size_t)sz, caller);
    return NULL;
}

extern "C" BOOL VirtualFree(LPVOID addr, SIZE_T size, DWORD type)
{
    type = W32_DW(type);
    uint64_t a = (uint64_t)(uintptr_t)addr, sz = (uint64_t)size;
    DWORD err = ERROR_INVALID_PARAMETER;
    BOOL ok = FALSE;
    if ((type != MEM_RELEASE && type != MEM_DECOMMIT))
        goto bad;
    os_lock(&g_vm_lock);
    if (!pg_init()) {
        err = ERROR_NOT_ENOUGH_MEMORY;
        os_unlock(&g_vm_lock);
        goto bad;
    }
    {
        err = ERROR_INVALID_ADDRESS;
        if (a < UMIN || a >= UEND || g_pg[pidx((uintptr_t)a)].state == ST_FREE)
            goto unlock_bad;
        size_t i = pidx((uintptr_t)a);
        size_t first = pidx((uintptr_t)((uint64_t)g_pg[i].base * PAGE));   /* the allocation's first page */
        err = ERROR_INVALID_PARAMETER;
        if (g_pg[i].type != TY_PRIVATE || (g_pg[i].flags & (F_FOREIGN | F_VIEW)))
            goto unlock_bad;   /* views end with UnmapViewOfFile, noted memory is not ours */
        if (type == MEM_RELEASE) {
            if (sz || a != paddr(first))
                goto unlock_bad;
            release_alloc(first);
        } else {
            size_t n = alloc_pages(first), f, l;
            if (!sz) {   /* the whole allocation, named by its base */
                if (a != paddr(first))
                    goto unlock_bad;
                f = first;
                l = first + n - 1;
            } else {
                uint64_t pa = a & ~(uint64_t)(PAGE - 1), pe = (a + sz + PAGE - 1) & ~(uint64_t)(PAGE - 1);
                err = ERROR_INVALID_ADDRESS;
                if (a + sz < a || pe > paddr(first) + (uint64_t)n * PAGE)
                    goto unlock_bad;
                f = pidx((uintptr_t)pa);
                l = pidx((uintptr_t)pe) - 1;
            }
            mem_account(f, l - f + 1, true, -1);
            os_vm_decommit((void *)paddr(f), (l - f + 1) * PAGE);
            for (size_t k = f; k <= l; k++) {
                g_pg[k].state = ST_RESERVE;
                g_pg[k].cur = 0;
                g_pg[k].flags &= ~F_RESET;
                g_pg[k].wdirty = 0;
            }
        }
        ok = TRUE;
    }
unlock_bad:
    os_unlock(&g_vm_lock);
    if (ok)
        return TRUE;
bad:
    SetLastError(err);
    return FALSE;
}

/* the access a thread of the guest has to [a, a + n) by the map: 1 yes. access 0 read, 1 write, 8 code. A guard page
 * is no access (the first access is the exception): consume_guard clears the bit of the first one found, as
 * IsBad*Ptr's probe does. Under the lock. */
static int range_access(uint64_t a, uint64_t n, int access, bool consume_guard)
{
    if (a < UMIN || a >= UEND || n > UEND - a)
        return 0;
    for (size_t i = pidx((uintptr_t)a), e = pidx((uintptr_t)(a + n - 1)); i <= e; i++) {
        struct pg *p = &g_pg[i];
        if (p->state != ST_COMMIT)
            return 0;
        if (p->cur & PAGE_GUARD) {
            if (consume_guard) {
                p->cur &= ~PAGE_GUARD;
                os_vm_protect((void *)paddr(i), PAGE, os_prot_pg(*p));
            }
            return 0;
        }
        if (access == 1 ? !prot_write(p->cur) : access == 8 ? !prot_read(p->cur) && !prot_exec(p->cur) : !prot_read(p->cur))
            return 0;
    }
    return 1;
}

/* IsBadReadPtr / IsBadWritePtr / IsBadCodePtr (module.cpp). Memory of the LP64 host (addresses beyond 32 bits)
 * is not guest memory and has no entry in the map: the host build only ever passes such addresses for its own
 * buffers, which are valid. */
int w32_vm_probe(const void *p, size_t n, int access)
{
    uint64_t a = (uint64_t)(uintptr_t)p;
    if (!n)
        return 1;
    if (sizeof(void *) > 4 && a > 0xFFFFFFFFull)
        return 1;
    os_lock(&g_vm_lock);
    int ok = pg_init() && range_access(a, n, access, true);
    os_unlock(&g_vm_lock);
    return ok;
}

/* 0 or the error. A range with a page of a view that aliases a host section object (F_ALIAS_*) cannot be re-protected by
 * the OS: that request is the branch of the creation fact of its section (reach.h), taken after the lock is released
 * and only when the call is otherwise valid (every error of the contract above it keeps its answer). */
static DWORD protect_pages(uint64_t a, uint64_t sz, DWORD prot, DWORD *was)
{
    uint32_t reach = 0;
    uint64_t pa = a & ~(uint64_t)(PAGE - 1), pe = (a + sz + PAGE - 1) & ~(uint64_t)(PAGE - 1);
    DWORD err = 0;
    os_lock(&g_vm_lock);
    if (!pg_init() || pa < UMIN || a + sz < a || pe > UEND) {
        err = ERROR_INVALID_ADDRESS;
    } else {
        size_t f = pidx((uintptr_t)pa), l = pidx((uintptr_t)pe) - 1;
        for (size_t i = f; i <= l && !err; i++)   /* committed, and in one allocation */
            if (g_pg[i].state != ST_COMMIT || g_pg[i].base != g_pg[f].base)
                err = ERROR_INVALID_ADDRESS;
        if (!err && g_pg[f].type != TY_PRIVATE && ((prot_write(prot) && !prot_write(g_pg[f].aprot)) || (prot_exec(prot) && !prot_exec(g_pg[f].aprot))))
            err = ERROR_INVALID_PARAMETER;   /* beyond what the section / view grants */
        for (size_t i = f; i <= l && !err && !reach; i++)
            if (g_pg[i].flags & F_ALIAS_FILE)
                reach = REACH_KERNEL32_CreateFileMappingA_PROT;
            else if (g_pg[i].flags & F_ALIAS_PF)
                reach = REACH_KERNEL32_CreateFileMappingA_HFILE;
        if (!err && !reach && !(g_pg[f].flags & F_WATCH) && os_vm_protect((void *)paddr(f), (l - f + 1) * PAGE, os_prot_of(prot)))
            err = ERROR_INVALID_PARAMETER;
        if (!err && !reach) {
            *was = g_pg[f].cur;
            for (size_t i = f; i <= l; i++)
                g_pg[i].cur = (uint16_t)prot;
            if (g_pg[f].flags & F_WATCH)   /* each page by its own tracking state */
                for (size_t i = f; i <= l && !err; i++)
                    if (os_vm_protect((void *)paddr(i), PAGE, os_prot_pg(g_pg[i])))
                        err = ERROR_INVALID_PARAMETER;
        }
    }
    os_unlock(&g_vm_lock);
    if (reach)
        w32_reach(reach);
    return err;
}

extern "C" BOOL VirtualProtect(LPVOID addr, SIZE_T size, DWORD prot, PDWORD old)
{
    prot = W32_DW(prot);
    DWORD err = ERROR_INVALID_PARAMETER, was = 0;
    if (size && prot_valid(prot)) {
        if (!old || !w32_vm_probe(old, sizeof *old, 1))   /* before anything changes */
            err = ERROR_NOACCESS;
        else
            err = protect_pages((uint64_t)(uintptr_t)addr, (uint64_t)size, prot, &was);
    }
    if (err) {
        SetLastError(err);
        return FALSE;
    }
    *old = was;
    return TRUE;
}

extern "C" SIZE_T VirtualQuery(LPCVOID addr, PMEMORY_BASIC_INFORMATION mbi, SIZE_T len)
{
    uint64_t a = (uint64_t)(uintptr_t)addr;
    if (len < sizeof *mbi) {
        SetLastError(ERROR_BAD_LENGTH);
        return 0;
    }
    if (a < UMIN || a >= UEND) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    os_lock(&g_vm_lock);
    if (!pg_init()) {
        os_unlock(&g_vm_lock);
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return 0;
    }
    size_t p = pidx((uintptr_t)a), q = p + 1;
    struct pg c = g_pg[p];
    while (q < NPAGES) {
        const struct pg &d = g_pg[q];
        if (d.state != c.state || (c.state != ST_FREE && (d.base != c.base || d.type != c.type || d.aprot != c.aprot || d.cur != c.cur)))
            break;
        q++;
    }
    os_unlock(&g_vm_lock);
    memset(mbi, 0, sizeof *mbi);
    mbi->BaseAddress = (PVOID)(uintptr_t)paddr(p);
    mbi->RegionSize = (q - p) * PAGE;
    if (c.state == ST_FREE) {
        mbi->Protect = PAGE_NOACCESS;
    } else {
        mbi->AllocationBase = (PVOID)(uintptr_t)((uint64_t)c.base * PAGE);
        mbi->AllocationProtect = c.aprot;
        mbi->State = c.state == ST_COMMIT ? MEM_COMMIT : MEM_RESERVE;
        mbi->Protect = c.state == ST_COMMIT ? c.cur : 0;
        mbi->Type = c.type == TY_IMAGE ? MEM_IMAGE : c.type == TY_MAPPED ? MEM_MAPPED : MEM_PRIVATE;
        return sizeof *mbi;
    }
    mbi->State = MEM_FREE;
    return sizeof *mbi;
}

/* a memory fault at addr, by the map (the host fault handler of the lifter runtime asks) */
extern "C" int fnvnx_win32_vm_fault(uintptr_t addr, int access, uint32_t *code_out)
{
    uint32_t code = STATUS_ACCESS_VIOLATION;
    int r = 0;
    uint64_t a = (uint64_t)addr;
    os_lock(&g_vm_lock);
    if (pg_init() && a >= UMIN && a < UEND && g_pg[pidx((uintptr_t)a)].state == ST_COMMIT) {
        struct pg *p = &g_pg[pidx((uintptr_t)a)];
        if (p->cur & PAGE_GUARD) {
            p->cur &= ~PAGE_GUARD;   /* once: the access is retried after the handler */
            os_vm_protect((void *)(uintptr_t)(a & ~(uint64_t)(PAGE - 1)), PAGE, os_prot_pg(*p));
            code = STATUS_GUARD_PAGE_VIOLATION;
            r = 1;
        } else if (access == 1 && (p->flags & F_WATCH) && !p->wdirty && prot_write(p->cur)) {
            p->wdirty = 1;   /* the first write since the reset: record it, let the page be written, retry */
            os_vm_protect((void *)(uintptr_t)(a & ~(uint64_t)(PAGE - 1)), PAGE, os_prot_pg(*p));
            r = 2;
        } else if (access == 1 ? prot_write(p->cur) : access == 8 ? prot_exec(p->cur) : prot_read(p->cur)) {
            r = 2;   /* the map permits it: another thread cleared the guard first */
        }
    }
    os_unlock(&g_vm_lock);
    if (code_out)
        *code_out = code;
    return r;
}

/* The pages [pa, pe) as map indices, when they lie in one MEM_WRITE_WATCH allocation (under the lock). */
static bool watch_range(uint64_t a, uint64_t sz, size_t *f, size_t *l)
{
    uint64_t pa = a & ~(uint64_t)(PAGE - 1), pe = (a + sz + PAGE - 1) & ~(uint64_t)(PAGE - 1);
    if (!sz || a + sz < a || pa < UMIN || pe > UEND || !pg_init())
        return false;
    *f = pidx((uintptr_t)pa);
    *l = pidx((uintptr_t)pe) - 1;
    for (size_t i = *f; i <= *l; i++)
        if (g_pg[i].state == ST_FREE || g_pg[i].base != g_pg[*f].base || !(g_pg[i].flags & F_WATCH))
            return false;
    return true;
}

static void watch_rearm(size_t i)
{
    g_pg[i].wdirty = 0;
    if (g_pg[i].state == ST_COMMIT)
        os_vm_protect((void *)paddr(i), PAGE, os_prot_pg(g_pg[i]));
}

/* GetWriteWatch: the page addresses in [base, base + size) written since allocation or the last reset, in address order
 * (at most *count of them; *count = the number stored, *granularity = the page size). WRITE_WATCH_FLAG_RESET (1)
 * re-arms the pages it returned. 0, or ~0u with ERROR_INVALID_PARAMETER: another flag, no output, or a range that is not
 * inside one MEM_WRITE_WATCH allocation. */
extern "C" UINT GetWriteWatch(DWORD flags, PVOID base, SIZE_T size, PVOID *addresses, PULONG_PTR count, PULONG granularity)
{
    flags = W32_DW(flags);
    size_t f, l;
    if ((flags & ~1u) || !addresses || !count || !granularity) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return ~0u;
    }
    os_lock(&g_vm_lock);
    if (!watch_range((uint64_t)(uintptr_t)base, (uint64_t)size, &f, &l)) {
        os_unlock(&g_vm_lock);
        SetLastError(ERROR_INVALID_PARAMETER);
        return ~0u;
    }
    ULONG_PTR cap = *count, n = 0;
    for (size_t i = f; i <= l && n < cap; i++)
        if (g_pg[i].wdirty) {
            addresses[n++] = (PVOID)paddr(i);
            if (flags & 1)
                watch_rearm(i);
        }
    os_unlock(&g_vm_lock);
    *count = n;
    *granularity = PAGE;
    return 0;
}

/* ResetWriteWatch: every page of the range is clean and armed again. 0, or ~0u with ERROR_INVALID_PARAMETER. */
extern "C" UINT ResetWriteWatch(PVOID base, SIZE_T size)
{
    size_t f, l;
    os_lock(&g_vm_lock);
    if (!watch_range((uint64_t)(uintptr_t)base, (uint64_t)size, &f, &l)) {
        os_unlock(&g_vm_lock);
        SetLastError(ERROR_INVALID_PARAMETER);
        return ~0u;
    }
    for (size_t i = f; i <= l; i++)
        watch_rearm(i);
    os_unlock(&g_vm_lock);
    return 0;
}

/* memory that did not come from VirtualAlloc: the loader's image sections, heap regions and thread stacks */
extern "C" void fnvnx_win32_vm_note(uintptr_t base, size_t size, DWORD type, DWORD protect)
{
    uint64_t pa = (uint64_t)base & ~(uint64_t)(PAGE - 1), pe = ((uint64_t)base + size + PAGE - 1) & ~(uint64_t)(PAGE - 1);
    protect = W32_DW(protect);
    type = W32_DW(type);
    uint8_t ty = type == MEM_IMAGE ? TY_IMAGE : type == MEM_MAPPED ? TY_MAPPED : TY_PRIVATE;
    if (!size || pa < UMIN || pe > UEND || pe < pa || !prot_valid(protect))
        return;
    os_lock(&g_vm_lock);
    if (pg_init()) {
        DWORD ap = ty == TY_IMAGE ? 0x80 /* PAGE_EXECUTE_WRITECOPY */ : protect;
        for (size_t i = pidx((uintptr_t)pa), e = pidx((uintptr_t)pe); i < e; i++) {
            if (g_pg[i].state == ST_FREE) {
                size_t j = i;
                while (j < e && g_pg[j].state == ST_FREE)
                    pg_set(j++, paddr(i), ST_COMMIT, ty, protect, ap, F_FOREIGN, 0);
                i = j - 1;
            } else if (g_pg[i].type == ty) {   /* a page of an allocation: its protection */
                g_pg[i].cur = (uint16_t)protect;
            }
        }
    }
    os_unlock(&g_vm_lock);
}

extern "C" void fnvnx_win32_vm_unnote(uintptr_t base, size_t size)
{
    uint64_t pa = (uint64_t)base & ~(uint64_t)(PAGE - 1), pe = ((uint64_t)base + size + PAGE - 1) & ~(uint64_t)(PAGE - 1);
    if (!size || pa < UMIN || pe > UEND || pe < pa)
        return;
    os_lock(&g_vm_lock);
    if (pg_init())
        for (size_t i = pidx((uintptr_t)pa), e = pidx((uintptr_t)pe); i < e; i++)
            if (g_pg[i].flags & F_FOREIGN)
                pg_clear(i);
    os_unlock(&g_vm_lock);
}

/* ---- the page map's services to file views (filemap.cpp) */
struct view_ctx { w32_view_osmap fn; void *ctx; };
static void *view_osmap(void *hint, size_t size, void *c)
{
    struct view_ctx *v = (struct view_ctx *)c;
    return v->fn(hint, size, v->ctx);
}

void *w32_vm_view_place(uintptr_t want, size_t size, w32_view_osmap osmap, void *ctx, DWORD alloc_prot, DWORD page_prot,
                        int committed, int image, int reserve_section)
{
    size_t np = (size + PAGE - 1) / PAGE;
    void *res = NULL;
    os_lock(&g_vm_lock);
    if (!pg_init()) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    } else if (want && (want < UMIN || want >= UEND || np > NPAGES)) {
        SetLastError(ERROR_INVALID_ADDRESS);
    } else if (!size || np > NPAGES) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    } else {
        struct view_ctx v = { osmap, ctx };
        size_t at = place(want, np, false, view_osmap, &v);
        if (at != NPAGES) {
            uint8_t fl = (uint8_t)(F_VIEW | (reserve_section ? F_RESERVESEC : 0));
            for (size_t i = 0; i < np; i++)
                pg_set(at + i, paddr(at), committed ? ST_COMMIT : ST_RESERVE, image ? TY_IMAGE : TY_MAPPED, committed ? page_prot : 0,
                       alloc_prot, fl, 0);
            res = (void *)paddr(at);
        }
    }
    os_unlock(&g_vm_lock);
    return res;
}

size_t w32_vm_view_remove(uintptr_t base)
{
    size_t n = 0;
    os_lock(&g_vm_lock);
    if (pg_init() && base >= UMIN && base < UEND && !(base & (PAGE - 1))) {
        size_t i = pidx(base);
        if (g_pg[i].state != ST_FREE && (g_pg[i].flags & F_VIEW) && paddr(i) == (uintptr_t)((uint64_t)g_pg[i].base * PAGE)) {
            n = alloc_pages(i);
            for (size_t k = 0; k < n; k++)
                pg_clear(i + k);
        }
    }
    os_unlock(&g_vm_lock);
    return n * PAGE;
}

void w32_vm_view_alias(uintptr_t base, size_t size, int file)
{
    os_lock(&g_vm_lock);
    if (pg_init() && base >= UMIN && base + size <= UEND)
        for (size_t i = pidx(base), e = pidx(base + size - 1); i <= e; i++)
            if (g_pg[i].flags & F_VIEW)
                g_pg[i].flags |= file ? F_ALIAS_FILE : F_ALIAS_PF;
    os_unlock(&g_vm_lock);
}

void w32_vm_view_prot(uintptr_t base, size_t size, DWORD prot)
{
    os_lock(&g_vm_lock);
    if (pg_init() && base >= UMIN && base + size <= UEND)
        for (size_t i = pidx(base), e = pidx(base + size - 1); i <= e; i++)
            if (g_pg[i].flags & F_VIEW && g_pg[i].state == ST_COMMIT)
                g_pg[i].cur = (uint16_t)prot;
    os_unlock(&g_vm_lock);
}

/* ---- heaps over the C heap (the engine replaces allocation with its own MemoryManager).
 * Every block carries a header {heap, requested size, capacity, list links}, 16-byte aligned, in front of the
 * payload. A heap is {flags, fixed-size limit, bytes in use, block list, lock}; blocks belong to the heap in their
 * header. Unless HEAP_NO_SERIALIZE is in effect (call flags | heap flags), every operation holds the heap's lock. */
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

/* virtual address space: the user range, and what of it is free in the page map */
static uint64_t virt_total(void) { return (uint64_t)UEND - UMIN; }
static uint64_t virt_avail(void)
{
    os_lock(&g_vm_lock);
    uint64_t n = pg_init() ? g_free_pages : NPAGES;
    os_unlock(&g_vm_lock);
    return n * PAGE;
}

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
    ms->dwTotalVirtual = (SIZE_T)virt_total();   /* both below 2 GiB: no clamping applies */
    ms->dwAvailVirtual = (SIZE_T)virt_avail();
}

extern "C" BOOL GlobalMemoryStatusEx(LPMEMORYSTATUSEX ms)
{
    if (!ms || W32_DW(ms->dwLength) != sizeof *ms) {
        SetLastError(ms ? ERROR_INVALID_PARAMETER : ERROR_NOACCESS);
        return FALSE;
    }
    uint64_t t, a;
    os_memory_info(&t, &a);
    ms->dwLength = sizeof *ms;
    ms->dwMemoryLoad = t ? (DWORD)(100 - a * 100 / t) : 0;
    ms->ullTotalPhys = t;
    ms->ullAvailPhys = a;
    ms->ullTotalPageFile = pagefile_total(t);
    ms->ullAvailPageFile = pagefile_avail(t, a);
    ms->ullTotalVirtual = virt_total();
    ms->ullAvailVirtual = virt_avail();
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
