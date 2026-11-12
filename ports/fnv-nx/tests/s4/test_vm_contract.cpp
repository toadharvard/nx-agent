/* test_vm_contract.cpp - kernel32 virtual memory to contract (ENV_TRUTH P1, P2): one page map of the user address
 * space [0x10000, 0x7FFEFFFF]; VirtualAlloc / VirtualFree / VirtualProtect / VirtualQuery, IsBad*Ptr,
 * GlobalMemoryStatus(Ex) as functions of that map; file mappings. The real OS protection is checked by faulting. */
#include "s4_test.h"

#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "../../platform/os/os.h"

#define TOP 0x7FFF0000u
#define SYSERR GetLastError()

static sigjmp_buf g_jb;
static volatile int g_in_probe;
static void on_segv(int) { if (g_in_probe) siglongjmp(g_jb, 1); signal(SIGSEGV, SIG_DFL); }
/* does the access fault in the real OS? */
static bool faults(volatile unsigned char *p, bool write)
{
    struct sigaction sa, old;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_segv;
    sa.sa_flags = SA_NODEFER;
    sigaction(SIGSEGV, &sa, &old);
    bool f = false;
    g_in_probe = 1;
    if (sigsetjmp(g_jb, 1) == 0) {
        if (write) *p = 0x11; else (void)*p;
    } else {
        f = true;
    }
    g_in_probe = 0;
    sigaction(SIGSEGV, &old, NULL);
    return f;
}

static MEMORY_BASIC_INFORMATION Q(const void *p)
{
    MEMORY_BASIC_INFORMATION m;
    memset(&m, 0xEE, sizeof m);
    if (VirtualQuery(p, &m, sizeof m) != sizeof m) memset(&m, 0, sizeof m);
    return m;
}
static uintptr_t U(const void *p) { return (uintptr_t)p; }

/* ---- VirtualAlloc: flags, rounding, conflicts, exhaustion, contents */
TEST(vm_alloc_flags)
{
    SetLastError(0);
    CHECK(VirtualAlloc(NULL, 0, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(NULL, 4096, 0, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(NULL, 4096, MEM_RESERVE | 0x40, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(NULL, 4096, MEM_RESET | MEM_COMMIT, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(NULL, 4096, MEM_RESET_UNDO | MEM_RESET, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(NULL, 4096, MEM_WRITE_WATCH | MEM_COMMIT, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(NULL, 4096, MEM_PHYSICAL | MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_PRIVILEGE_NOT_HELD);
    CHECK(VirtualAlloc(NULL, 0x10000, MEM_LARGE_PAGES | MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE) == NULL && SYSERR == ERROR_PRIVILEGE_NOT_HELD);
    /* outside the user range */
    CHECK(VirtualAlloc((void *)(uintptr_t)0x1000, 4096, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc((void *)(uintptr_t)0x80000000u, 4096, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc((void *)(uintptr_t)(TOP - 0x1000), 0x2000, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    /* exhaustion: the user range [0x10000, 0x7FFEFFFF] is 0x7FFE0000 bytes, so a request for all of it is satisfiable only
     * on an empty map (it succeeds in a process where nothing is reserved yet, as when this case runs alone), and a real
     * process always has reservations (image, stacks). The case declares its own state: one granule held for the check. */
    void *hold = VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_READWRITE);
    CHECK(hold != NULL);
    CHECK(VirtualAlloc(NULL, 0x7FFE0000u, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    CHECK(VirtualAlloc(NULL, (SIZE_T)0xFFFFFFFFu, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    CHECK(VirtualFree(hold, 0, MEM_RELEASE));

    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x100000, MEM_RESERVE, PAGE_READWRITE);
    CHECK(r && (U(r) & 0xFFFF) == 0 && U(r) >= 0x10000 && U(r) + 0x100000 <= TOP);
    CHECK(VirtualFree(r, 0, MEM_RELEASE));
    /* reserve at an address: rounded down to 64 KiB */
    unsigned char *r2 = (unsigned char *)VirtualAlloc(r + 0x12345, 0x1000, MEM_RESERVE, PAGE_READWRITE);
    CHECK(r2 == r + 0x10000 + 0 || r2 == r);   /* rounded to the granule: r + 0x10000 */
    CHECK(r2 == r + 0x10000);
    /* conflicts */
    SetLastError(0);
    CHECK(VirtualAlloc(r2, 0x1000, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(VirtualAlloc(r2 + 0x2000, 0x1000, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_ADDRESS);   /* same granule */
    /* commit: page rounded, inside the reservation only */
    CHECK(VirtualAlloc(r2 + 0x300, 1, MEM_COMMIT, PAGE_READWRITE) == r2);
    CHECK(VirtualAlloc(r2, 0x5000, MEM_COMMIT, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_ADDRESS);   /* the reservation spans [r+0x12345, +0x1000) rounded to pages: 4 pages */
    CHECK(VirtualFree(r2, 0, MEM_RELEASE));
    /* commit at an address that is not reserved */
    CHECK(VirtualAlloc(r2, 0x1000, MEM_COMMIT, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_ADDRESS);

    /* zero fill, re-commit keeps contents, decommit drops them */
    unsigned char *c = (unsigned char *)VirtualAlloc(NULL, 0x4000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    CHECK(c != NULL);
    int nz = 0;
    for (int i = 0; i < 0x4000; i += 64) nz += c[i];
    CHECK_EQ(nz, 0);
    c[0x1010] = 0x77;
    CHECK(VirtualAlloc(c, 0x4000, MEM_COMMIT, PAGE_READWRITE) == c);
    CHECK_EQ(c[0x1010], 0x77);
    CHECK(VirtualFree(c + 0x1000, 0x1000, MEM_DECOMMIT));
    CHECK(VirtualAlloc(c + 0x1000, 0x1000, MEM_COMMIT, PAGE_READWRITE) == c + 0x1000);
    CHECK_EQ(c[0x1010], 0);
    /* MEM_RESET: committed pages only, alone; undo needs a reset */
    CHECK(VirtualAlloc(c + 0x1234, 0x100, MEM_RESET, PAGE_READWRITE) == c + 0x1000);
    CHECK(VirtualAlloc(c + 0x1000, 0x1000, MEM_RESET_UNDO, PAGE_READWRITE) == c + 0x1000);
    CHECK(VirtualAlloc(c + 0x1000, 0x1000, MEM_RESET_UNDO, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualFree(c + 0x2000, 0x1000, MEM_DECOMMIT));
    CHECK(VirtualAlloc(c + 0x2000, 0x1000, MEM_RESET, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(VirtualFree(c, 0, MEM_RELEASE));
    /* TOP_DOWN: above a plain bottom-up reservation */
    void *lo = VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_READWRITE);
    void *hi = VirtualAlloc(NULL, 0x10000, MEM_RESERVE | MEM_TOP_DOWN, PAGE_READWRITE);
    CHECK(lo && hi && U(hi) > U(lo) && U(hi) + 0x10000 <= TOP);
    VirtualFree(lo, 0, MEM_RELEASE);
    VirtualFree(hi, 0, MEM_RELEASE);
}

TEST(vm_alloc_protections)
{
    const DWORD base[8] = { PAGE_NOACCESS, PAGE_READONLY, PAGE_READWRITE, PAGE_WRITECOPY, PAGE_EXECUTE, PAGE_EXECUTE_READ,
                            PAGE_EXECUTE_READWRITE, 0x80 /* PAGE_EXECUTE_WRITECOPY */ };
    for (int i = 0; i < 8; i++) {
        void *p = VirtualAlloc(NULL, 0x1000, MEM_RESERVE | MEM_COMMIT, base[i]);
        CHECK(p != NULL);
        if (p) {
            MEMORY_BASIC_INFORMATION m = Q(p);
            CHECK_EQ(m.Protect, base[i]);
            CHECK_EQ(m.AllocationProtect, base[i]);
            VirtualFree(p, 0, MEM_RELEASE);
        }
    }
    const DWORD bad[] = { 0, 3, 5, 0x81, 0x100, 0x200, 0x400, 0x1000, 0x102 | 0x4, PAGE_GUARD | PAGE_NOACCESS,
                          PAGE_NOCACHE | PAGE_NOACCESS, PAGE_NOCACHE | PAGE_WRITECOMBINE | PAGE_READWRITE, PAGE_GUARD | PAGE_NOCACHE | PAGE_READWRITE, PAGE_GUARD | PAGE_WRITECOMBINE | PAGE_READWRITE,
                          PAGE_READWRITE | 0x800, PAGE_READWRITE | 0x10000 };
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        SetLastError(0);
        void *p = VirtualAlloc(NULL, 0x1000, MEM_RESERVE | MEM_COMMIT, bad[i]);
        CHECK(p == NULL && SYSERR == ERROR_INVALID_PARAMETER);
        if (p) VirtualFree(p, 0, MEM_RELEASE);
        SetLastError(0);
        p = VirtualAlloc(NULL, 0x1000, MEM_RESERVE, bad[i]);   /* the reserve protection is validated too */
        CHECK(p == NULL && SYSERR == ERROR_INVALID_PARAMETER);
        if (p) VirtualFree(p, 0, MEM_RELEASE);
    }
    const DWORD mods[] = { PAGE_READWRITE | PAGE_GUARD, PAGE_READONLY | PAGE_NOCACHE, PAGE_READWRITE | PAGE_WRITECOMBINE,
                           PAGE_EXECUTE_READ | PAGE_GUARD };
    for (unsigned i = 0; i < sizeof mods / sizeof mods[0]; i++) {
        void *p = VirtualAlloc(NULL, 0x1000, MEM_RESERVE | MEM_COMMIT, mods[i]);
        CHECK(p != NULL);
        if (p) {
            CHECK_EQ(Q(p).Protect, mods[i]);
            VirtualFree(p, 0, MEM_RELEASE);
        }
    }
    /* the commit protection is per page, the allocation protection stays the reserve's */
    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x4000, MEM_RESERVE, PAGE_READONLY);
    CHECK(r != NULL);
    CHECK(VirtualAlloc(r, 0x1000, MEM_COMMIT, PAGE_READWRITE) == r);
    CHECK(VirtualAlloc(r + 0x1000, 0x1000, MEM_COMMIT, PAGE_READONLY) == r + 0x1000);
    CHECK(VirtualAlloc(r + 0x2000, 0x1000, MEM_COMMIT, PAGE_EXECUTE_READ) == r + 0x2000);
    MEMORY_BASIC_INFORMATION m = Q(r);
    CHECK_EQ(m.Protect, PAGE_READWRITE);
    CHECK_EQ(m.AllocationProtect, PAGE_READONLY);
    CHECK_EQ(m.RegionSize, 0x1000);
    CHECK_EQ(Q(r + 0x1000).Protect, PAGE_READONLY);
    CHECK_EQ(Q(r + 0x2000).Protect, PAGE_EXECUTE_READ);
    CHECK(!faults(r, true));
    CHECK(faults(r + 0x1000, true));
    CHECK(!faults(r + 0x1000, false));
    CHECK(faults(r + 0x3000, false));   /* reserved, not committed */
    VirtualFree(r, 0, MEM_RELEASE);
}

/* ---- VirtualProtect */
TEST(vm_protect)
{
    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x4000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    CHECK(r != NULL);
    memset(r, 0x5A, 0x4000);
    DWORD old = 0xDEAD;
    CHECK(VirtualProtect(r + 0x1000, 0x1000, PAGE_READONLY, &old));
    CHECK_EQ(old, PAGE_READWRITE);
    CHECK_EQ(Q(r).Protect, PAGE_READWRITE);
    CHECK_EQ(Q(r).RegionSize, 0x1000);
    CHECK_EQ(Q(r + 0x1000).Protect, PAGE_READONLY);
    CHECK_EQ(Q(r + 0x1000).RegionSize, 0x1000);
    CHECK_EQ(Q(r + 0x2000).RegionSize, 0x2000);
    CHECK(faults(r + 0x1000, true));
    CHECK(!faults(r + 0x1000, false));
    CHECK_EQ(r[0x1000], 0x5A);   /* contents kept */
    CHECK(IsBadWritePtr(r + 0x1000, 1) && !IsBadReadPtr(r + 0x1000, 1));
    /* a range over several pages: the old protection is the first page's */
    CHECK(VirtualProtect(r + 0x1800, 0x1000, PAGE_NOACCESS, &old));   /* bytes 0x1800..0x27FF: pages 1 and 2 */
    CHECK_EQ(old, PAGE_READONLY);
    CHECK_EQ(Q(r + 0x1000).Protect, PAGE_NOACCESS);
    CHECK_EQ(Q(r + 0x1000).RegionSize, 0x2000);
    CHECK(faults(r + 0x2000, false));
    CHECK(VirtualProtect(r, 0x4000, PAGE_EXECUTE_READ, &old));
    CHECK_EQ(old, PAGE_READWRITE);
    CHECK_EQ(Q(r).RegionSize, 0x4000);
    CHECK(faults(r, true));
    CHECK(VirtualProtect(r, 0x4000, PAGE_READWRITE | PAGE_NOCACHE, &old));
    CHECK_EQ(old, PAGE_EXECUTE_READ);
    CHECK_EQ(Q(r).Protect, PAGE_READWRITE | PAGE_NOCACHE);
    CHECK(!faults(r, true));
    /* errors */
    SetLastError(0);
    CHECK(!VirtualProtect(r, 0, PAGE_READONLY, &old) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualProtect(r, 0x1000, 3, &old) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualProtect(r, 0x1000, PAGE_GUARD | PAGE_NOACCESS, &old) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualProtect(r, 0x1000, PAGE_READONLY, NULL) && SYSERR == ERROR_NOACCESS);
    CHECK(!VirtualProtect(r, 0x1000, PAGE_READONLY, (PDWORD)(uintptr_t)0x1000) && SYSERR == ERROR_NOACCESS);
    CHECK(!VirtualProtect(r + 0x3000, 0x2000, PAGE_READONLY, &old) && SYSERR == ERROR_INVALID_ADDRESS);   /* past the allocation */
    CHECK(VirtualFree(r + 0x3000, 0x1000, MEM_DECOMMIT));
    CHECK(!VirtualProtect(r + 0x2000, 0x2000, PAGE_READONLY, &old) && SYSERR == ERROR_INVALID_ADDRESS);   /* not committed */
    CHECK(!VirtualProtect((void *)(uintptr_t)0x2000, 0x1000, PAGE_READONLY, &old) && SYSERR == ERROR_INVALID_ADDRESS);
    /* across two allocations */
    unsigned char *a = (unsigned char *)VirtualAlloc(NULL, 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    unsigned char *b = (unsigned char *)VirtualAlloc(a + 0x10000, 0x10000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (b) {
        CHECK(!VirtualProtect(a + 0xF000, 0x2000, PAGE_READONLY, &old) && SYSERR == ERROR_INVALID_ADDRESS);
        CHECK_EQ(Q(a + 0xF000).Protect, PAGE_READWRITE);   /* nothing changed */
        VirtualFree(b, 0, MEM_RELEASE);
    }
    VirtualFree(a, 0, MEM_RELEASE);
    VirtualFree(r, 0, MEM_RELEASE);
}

/* ---- guard pages */
TEST(vm_guard)
{
    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x3000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    CHECK(r != NULL);
    r[0x1000] = 9;
    DWORD old;
    CHECK(VirtualProtect(r + 0x1000, 0x1000, PAGE_READWRITE | PAGE_GUARD, &old));
    CHECK_EQ(old, PAGE_READWRITE);
    CHECK_EQ(Q(r + 0x1000).Protect, PAGE_READWRITE | PAGE_GUARD);
    CHECK(faults(r + 0x1000, false));   /* the OS faults: the guard is armed */
    uint32_t code = 0;
    CHECK_EQ(fnvnx_win32_vm_fault(U(r + 0x1008), 0, &code), 1);
    CHECK_EQ(code, STATUS_GUARD_PAGE_VIOLATION);
    CHECK_EQ(Q(r + 0x1000).Protect, PAGE_READWRITE);   /* the bit is cleared, once */
    CHECK(!faults(r + 0x1000, true));
    CHECK_EQ(r[0x1000], 0x11);
    CHECK_EQ(fnvnx_win32_vm_fault(U(r + 0x1008), 0, &code), 2);   /* legal now: retry */
    /* access violations by the map: reserved, free, no access, read-only write, non-executable exec */
    CHECK(VirtualFree(r + 0x2000, 0x1000, MEM_DECOMMIT));
    CHECK_EQ(fnvnx_win32_vm_fault(U(r + 0x2000), 0, &code), 0);
    CHECK_EQ(code, STATUS_ACCESS_VIOLATION);
    CHECK(VirtualProtect(r, 0x1000, PAGE_READONLY, &old));
    CHECK_EQ(fnvnx_win32_vm_fault(U(r), 1, &code), 0);
    CHECK_EQ(code, STATUS_ACCESS_VIOLATION);
    CHECK_EQ(fnvnx_win32_vm_fault(U(r), 0, &code), 2);
    CHECK_EQ(fnvnx_win32_vm_fault(U(r), 8, &code), 0);
    CHECK(VirtualProtect(r, 0x1000, PAGE_EXECUTE_READ, &old));
    CHECK_EQ(fnvnx_win32_vm_fault(U(r), 8, &code), 2);
    CHECK_EQ(fnvnx_win32_vm_fault(U(r), 1, &code), 0);
    CHECK(VirtualProtect(r, 0x1000, PAGE_NOACCESS, &old));
    CHECK_EQ(fnvnx_win32_vm_fault(U(r), 0, &code), 0);
    CHECK_EQ(fnvnx_win32_vm_fault(0x1000, 0, &code), 0);
    CHECK_EQ(code, STATUS_ACCESS_VIOLATION);
    /* a guard bit given at commit time */
    unsigned char *g = (unsigned char *)VirtualAlloc(NULL, 0x1000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE | PAGE_GUARD);
    CHECK(g && Q(g).Protect == (PAGE_READWRITE | PAGE_GUARD));
    CHECK_EQ(fnvnx_win32_vm_fault(U(g), 1, &code), 1);
    CHECK(!faults(g, true));
    VirtualFree(g, 0, MEM_RELEASE);
    VirtualFree(r, 0, MEM_RELEASE);
}

/* ---- write watch: VirtualAlloc(MEM_WRITE_WATCH), GetWriteWatch, ResetWriteWatch. A watched page is read-only at the OS
 * level until its first write; the fault path records it (called here as the host fault handler would). */
TEST(vm_write_watch)
{
    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x5000, MEM_RESERVE | MEM_COMMIT | MEM_WRITE_WATCH, PAGE_READWRITE);
    CHECK(r != NULL);
    CHECK_EQ(Q(r).Protect, PAGE_READWRITE);                  /* the map still says read-write */
    PVOID addr[8];
    ULONG_PTR n = 8;
    ULONG gran = 0;
    CHECK_EQ(GetWriteWatch(0, r, 0x5000, addr, &n, &gran), 0u);
    CHECK(n == 0 && gran == 0x1000);                          /* nothing written yet */
    CHECK(faults(r + 0x1000, true));                          /* armed: the write faults in the OS */
    CHECK(!faults(r + 0x1000, false));
    uint32_t code = 0;
    CHECK_EQ(fnvnx_win32_vm_fault(U(r + 0x1008), 1, &code), 2);   /* recorded; the write is retried */
    CHECK(!faults(r + 0x1000, true));
    CHECK_EQ(fnvnx_win32_vm_fault(U(r + 0x3000), 1, &code), 2);
    CHECK_EQ(fnvnx_win32_vm_fault(U(r + 0x3000), 0, &code), 2);   /* a read is no write: the map permits it */
    r[0x3000] = 1;
    n = 8;
    CHECK_EQ(GetWriteWatch(0, r, 0x5000, addr, &n, &gran), 0u);
    CHECK(n == 2 && addr[0] == r + 0x1000 && addr[1] == r + 0x3000);
    n = 1;                                                    /* a short array: the first one, reset only that page */
    CHECK_EQ(GetWriteWatch(WRITE_WATCH_FLAG_RESET, r, 0x5000, addr, &n, &gran), 0u);
    CHECK(n == 1 && addr[0] == r + 0x1000);
    CHECK(faults(r + 0x1000, true));                          /* armed again */
    CHECK(!faults(r + 0x3000, true));                         /* still written */
    n = 8;
    CHECK_EQ(GetWriteWatch(0, r + 0x2000, 0x2000, addr, &n, &gran), 0u);   /* a sub-range */
    CHECK(n == 1 && addr[0] == r + 0x3000);
    CHECK_EQ(ResetWriteWatch(r, 0x5000), 0u);
    n = 8;
    CHECK_EQ(GetWriteWatch(0, r, 0x5000, addr, &n, &gran), 0u);
    CHECK_EQ(n, 0u);
    CHECK(faults(r + 0x3000, true));
    /* VirtualProtect keeps the tracking: read-only, then read-write again is still armed */
    DWORD old;
    CHECK(VirtualProtect(r, 0x1000, PAGE_READONLY, &old));
    CHECK(VirtualProtect(r, 0x1000, PAGE_READWRITE, &old));
    CHECK(faults(r, true));
    /* the contract's errors */
    unsigned char *plain = (unsigned char *)VirtualAlloc(NULL, 0x1000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    n = 8;
    CHECK_EQ(GetWriteWatch(0, plain, 0x1000, addr, &n, &gran), ~0u);   /* not a MEM_WRITE_WATCH region */
    CHECK_EQ(SYSERR, ERROR_INVALID_PARAMETER);
    CHECK_EQ(ResetWriteWatch(plain, 0x1000), ~0u);
    CHECK_EQ(GetWriteWatch(2, r, 0x1000, addr, &n, &gran), ~0u);       /* an unknown flag */
    CHECK_EQ(GetWriteWatch(0, r, 0, addr, &n, &gran), ~0u);            /* an empty range */
    CHECK_EQ(GetWriteWatch(0, r, 0x6000, addr, &n, &gran), ~0u);       /* beyond the allocation */
    CHECK_EQ(GetWriteWatch(0, r, 0x1000, NULL, &n, &gran), ~0u);
    CHECK(VirtualAlloc(NULL, 0x1000, MEM_COMMIT | MEM_WRITE_WATCH, PAGE_READWRITE) == NULL);   /* needs MEM_RESERVE */
    VirtualFree(plain, 0, MEM_RELEASE);
    VirtualFree(r, 0, MEM_RELEASE);
}

/* ---- VirtualFree rules */
TEST(vm_free)
{
    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_READWRITE);
    CHECK(r != NULL);
    SetLastError(0);
    CHECK(!VirtualFree(r, 0, 0) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualFree(r, 0, MEM_RELEASE | MEM_DECOMMIT) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualFree(r, 0x1000, MEM_RELEASE) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualFree(r + 0x1000, 0, MEM_RELEASE) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualFree(r, 0, MEM_RELEASE | 0x40) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(r, 0x4000, MEM_COMMIT, PAGE_READWRITE) == r);
    CHECK(!VirtualFree(r + 0x1000, 0, MEM_DECOMMIT) && SYSERR == ERROR_INVALID_PARAMETER);   /* size 0: the allocation base only */
    CHECK(!VirtualFree(r + 0xF000, 0x2000, MEM_DECOMMIT) && SYSERR == ERROR_INVALID_ADDRESS);   /* past the allocation */
    CHECK(VirtualFree(r + 0x1800, 0x1000, MEM_DECOMMIT));   /* pages 1 and 2 */
    CHECK_EQ(Q(r).RegionSize, 0x1000);
    CHECK_EQ(Q(r + 0x1000).State, MEM_RESERVE);
    CHECK_EQ(Q(r + 0x1000).RegionSize, 0x2000);
    CHECK_EQ(Q(r + 0x1000).Protect, 0);
    CHECK_EQ(Q(r + 0x3000).State, MEM_COMMIT);
    CHECK(VirtualFree(r + 0x1000, 0x1000, MEM_DECOMMIT));   /* already decommitted: fine */
    CHECK(VirtualFree(r, 0, MEM_DECOMMIT));   /* everything */
    CHECK_EQ(Q(r).State, MEM_RESERVE);
    CHECK_EQ(Q(r).RegionSize, 0x10000);
    CHECK(VirtualFree(r, 0, MEM_RELEASE));
    CHECK_EQ(Q(r).State, MEM_FREE);
    CHECK(!VirtualFree(r, 0, MEM_RELEASE) && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(!VirtualFree(r, 0, MEM_DECOMMIT) && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(!VirtualFree((void *)(uintptr_t)0x2000, 0, MEM_RELEASE) && SYSERR == ERROR_INVALID_ADDRESS);
}

/* ---- VirtualQuery */
TEST(vm_query)
{
    MEMORY_BASIC_INFORMATION m;
    SetLastError(0);
    CHECK_EQ(VirtualQuery((void *)(uintptr_t)TOP, &m, sizeof m), 0);
    CHECK_EQ(SYSERR, ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK_EQ(VirtualQuery((void *)(uintptr_t)0x80000000u, &m, sizeof m), 0);
    CHECK_EQ(SYSERR, ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK_EQ(VirtualQuery(NULL, &m, sizeof m), 0);
    CHECK_EQ(SYSERR, ERROR_INVALID_PARAMETER);
    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x30000, MEM_RESERVE, PAGE_READWRITE);
    SetLastError(0);
    CHECK_EQ(VirtualQuery(r, &m, sizeof m - 1), 0);
    CHECK_EQ(SYSERR, ERROR_BAD_LENGTH);
    CHECK_EQ(VirtualQuery(r, &m, 0), 0);
    /* a reserved run, then runs of equal attributes */
    m = Q(r + 0x123);
    CHECK(m.BaseAddress == r && m.AllocationBase == r && m.State == MEM_RESERVE && m.RegionSize == 0x30000);
    CHECK(m.Type == MEM_PRIVATE && m.AllocationProtect == PAGE_READWRITE && m.Protect == 0);
    CHECK(VirtualAlloc(r + 0x2000, 0x3000, MEM_COMMIT, PAGE_READWRITE) == r + 0x2000);
    CHECK(VirtualAlloc(r + 0x5000, 0x1000, MEM_COMMIT, PAGE_READONLY) == r + 0x5000);
    m = Q(r);
    CHECK(m.State == MEM_RESERVE && m.RegionSize == 0x2000);
    m = Q(r + 0x2fff);
    CHECK(m.BaseAddress == r + 0x2000 && m.State == MEM_COMMIT && m.RegionSize == 0x3000 && m.Protect == PAGE_READWRITE);
    m = Q(r + 0x5000);
    CHECK(m.State == MEM_COMMIT && m.RegionSize == 0x1000 && m.Protect == PAGE_READONLY && m.AllocationBase == r);
    m = Q(r + 0x6000);
    CHECK(m.State == MEM_RESERVE && m.RegionSize == 0x30000 - 0x6000);
    /* a query from the middle of a run starts at the page of the address */
    m = Q(r + 0x3800);
    CHECK(m.BaseAddress == r + 0x3000 && m.RegionSize == 0x2000);
    /* free regions: the true size up to the next allocation */
    CHECK(VirtualFree(r, 0, MEM_RELEASE));
    unsigned char *x = (unsigned char *)VirtualAlloc(NULL, 0x1000, MEM_RESERVE, PAGE_READWRITE);   /* 1 page of a 64 KiB granule */
    m = Q(x + 0x1000);
    CHECK(m.State == MEM_FREE && m.BaseAddress == x + 0x1000 && m.RegionSize >= 0xF000 && m.AllocationBase == NULL);
    CHECK(m.Protect == PAGE_NOACCESS && m.AllocationProtect == 0 && m.Type == 0);
    /* a free run: its end is the next non-free page, all of it free */
    unsigned char *p = x + 0x1000;
    size_t n = m.RegionSize;
    CHECK(U(p) + n <= TOP);
    if (U(p) + n < TOP) CHECK(Q(p + n).State != MEM_FREE);
    CHECK_EQ(Q(p + n - 0x1000).State, MEM_FREE);
    CHECK_EQ(Q(p + n - 0x1000).RegionSize, 0x1000);
    /* the granule tail cannot be reserved by another allocation */
    SetLastError(0);
    CHECK(VirtualAlloc(x + 0x1000, 0x1000, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(VirtualFree(x, 0, MEM_RELEASE));
    /* the whole free space below an explicit reservation */
    unsigned char *y = (unsigned char *)VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_READWRITE);
    m = Q(y);
    CHECK(m.State == MEM_RESERVE && m.RegionSize == 0x10000);
    VirtualFree(y, 0, MEM_RELEASE);
    /* the first free address below the application range */
    m = Q((void *)(uintptr_t)(TOP - 0x1000));
    CHECK(m.BaseAddress == (void *)(uintptr_t)(TOP - 0x1000) && m.State != 0);
}

/* ---- memory outside VirtualAlloc is in the map */
TEST(vm_notes)
{
    /* a range of the user space that the loader obtained from the OS itself */
    void *a = VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_READWRITE);
    CHECK(a != NULL);
    VirtualFree(a, 0, MEM_RELEASE);
    void *img = os_vm_reserve(a, 0x10000);
    CHECK(img == a);
    CHECK(img && os_vm_commit(img, 0x10000) == 0);
    unsigned char *b = (unsigned char *)img;
    fnvnx_win32_vm_note(U(b), 0x10000, MEM_IMAGE, PAGE_READONLY);
    fnvnx_win32_vm_note(U(b) + 0x1000, 0x2000, MEM_IMAGE, PAGE_EXECUTE_READ);
    fnvnx_win32_vm_note(U(b) + 0x3000, 0x1000, MEM_IMAGE, PAGE_WRITECOPY);
    MEMORY_BASIC_INFORMATION m = Q(b + 0x1800);
    CHECK(m.State == MEM_COMMIT && m.Type == MEM_IMAGE && m.AllocationBase == b && m.BaseAddress == b + 0x1000);
    CHECK(m.Protect == PAGE_EXECUTE_READ && m.RegionSize == 0x2000);
    CHECK_EQ(Q(b).RegionSize, 0x1000);
    CHECK(Q(b).Protect == PAGE_READONLY && Q(b).Type == MEM_IMAGE);
    CHECK(Q(b + 0x3000).Protect == PAGE_WRITECOPY && Q(b + 0x3000).AllocationBase == b);
    CHECK_EQ(Q(b + 0x4000).RegionSize, 0xC000);
    CHECK(!IsBadReadPtr(b + 0x1000, 0x2000) && IsBadWritePtr(b + 0x1000, 1) && !IsBadCodePtr((FARPROC)(b + 0x1000)));
    /* not the platform's to free or decommit */
    SetLastError(0);
    CHECK(!VirtualFree(b, 0, MEM_RELEASE) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(!VirtualFree(b, 0x1000, MEM_DECOMMIT) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(VirtualAlloc(b, 0x1000, MEM_RESERVE, PAGE_READWRITE) == NULL && SYSERR == ERROR_INVALID_ADDRESS);
    /* VirtualProtect works on it, and reaches the OS */
    DWORD old;
    CHECK(VirtualProtect(b + 0x4000, 0x1000, PAGE_READONLY, &old));
    CHECK_EQ(old, PAGE_READONLY);   /* the whole-image note set it */
    CHECK(VirtualProtect(b + 0x5000, 0x1000, PAGE_READONLY, &old));
    CHECK(faults(b + 0x5000, true));
    /* un-noting (a thread's stack ends, a module unloads) gives the pages back to the map */
    fnvnx_win32_vm_unnote(U(b), 0x10000);
    CHECK_EQ(Q(b).State, MEM_FREE);
    os_vm_release(b, 0x10000);
    /* private memory (heap-like) */
    void *c = VirtualAlloc(NULL, 0x4000, MEM_RESERVE, PAGE_READWRITE);
    VirtualFree(c, 0, MEM_RELEASE);
    void *h = os_vm_reserve(c, 0x10000);
    CHECK(h == c && os_vm_commit(h, 0x4000) == 0);
    fnvnx_win32_vm_note(U(h), 0x4000, MEM_PRIVATE, PAGE_READWRITE);
    m = Q(h);
    CHECK(m.State == MEM_COMMIT && m.Type == MEM_PRIVATE && m.Protect == PAGE_READWRITE && m.RegionSize == 0x4000 && m.AllocationBase == h);
    CHECK(!IsBadWritePtr(h, 0x4000));
    /* outside the 32-bit user space: not in the map */
    fnvnx_win32_vm_note(0x1000, 0x1000, MEM_PRIVATE, PAGE_READWRITE);
    fnvnx_win32_vm_unnote(U(h), 0x4000);
    os_vm_release(h, 0x10000);
}

/* ---- IsBad*Ptr */
TEST(vm_isbad)
{
    unsigned char *r = (unsigned char *)VirtualAlloc(NULL, 0x10000, MEM_RESERVE, PAGE_READWRITE);
    CHECK(VirtualAlloc(r, 0x2000, MEM_COMMIT, PAGE_READWRITE) == r);
    CHECK(VirtualAlloc(r + 0x2000, 0x1000, MEM_COMMIT, PAGE_READONLY) == r + 0x2000);
    CHECK(VirtualAlloc(r + 0x3000, 0x1000, MEM_COMMIT, PAGE_NOACCESS) == r + 0x3000);
    CHECK(VirtualAlloc(r + 0x4000, 0x1000, MEM_COMMIT, PAGE_EXECUTE) == r + 0x4000);
    CHECK(VirtualAlloc(r + 0x5000, 0x1000, MEM_COMMIT, PAGE_READWRITE | PAGE_GUARD) == r + 0x5000);
    CHECK(VirtualAlloc(r + 0x6000, 0x1000, MEM_COMMIT, PAGE_WRITECOPY) == r + 0x6000);
    CHECK(!IsBadReadPtr(NULL, 0) && !IsBadWritePtr(NULL, 0));
    CHECK(!IsBadReadPtr((void *)(uintptr_t)0xFFFFFFF0u, 0));
    CHECK(IsBadReadPtr(NULL, 1) && IsBadWritePtr(NULL, 1) && IsBadCodePtr(NULL));
    CHECK(!IsBadReadPtr(r, 0x2000) && !IsBadWritePtr(r, 0x2000));
    CHECK(IsBadReadPtr(r, 0x2001 + 0x1000 + 0x1000));   /* runs into the no-access page */
    CHECK(!IsBadReadPtr(r + 0x1FFF, 2));                /* over the page border into a readable page */
    CHECK(IsBadWritePtr(r + 0x1FFF, 2));                /* ... which is read-only */
    CHECK(!IsBadWritePtr(r + 0x1FFF, 1));
    CHECK(IsBadReadPtr(r + 0x3000, 1) && IsBadWritePtr(r + 0x3000, 1) && IsBadCodePtr((FARPROC)(r + 0x3000)));
    CHECK(!IsBadReadPtr(r + 0x4000, 1) && !IsBadCodePtr((FARPROC)(r + 0x4000)) && IsBadWritePtr(r + 0x4000, 1));
    CHECK(!IsBadWritePtr(r + 0x6000, 0x1000));          /* copy-on-write pages are writable */
    CHECK(IsBadReadPtr(r + 0x7000, 1));                 /* reserved */
    CHECK(IsBadReadPtr(r + 0x10000, 1));                /* free (or someone else's: not this allocation) */
    CHECK(IsBadReadPtr(r + 0x6FFF, 0x2));
    CHECK(IsBadReadPtr((void *)(uintptr_t)0x1000, 4));
    CHECK(IsBadReadPtr((void *)(uintptr_t)(TOP - 4), 8));
    CHECK(IsBadReadPtr((void *)(uintptr_t)0xFFFFFFFCu, 8));   /* wraps the 32-bit space */
    CHECK(IsBadReadPtr(r, (UINT_PTR)0xFFFFFFFFu));
    /* a guard page: TRUE once, the probe consumed the guard */
    CHECK(IsBadReadPtr(r + 0x5000, 1));
    CHECK_EQ(Q(r + 0x5000).Protect, PAGE_READWRITE);
    CHECK(!IsBadReadPtr(r + 0x5000, 1));
    CHECK(VirtualFree(r, 0, MEM_RELEASE));
    CHECK(IsBadReadPtr(r, 1));
}

/* ---- GlobalMemoryStatus(Ex) */
TEST(vm_memstatus)
{
    MEMORYSTATUSEX ms;
    memset(&ms, 0, sizeof ms);
    SetLastError(0);
    ms.dwLength = sizeof ms - 8;
    CHECK(!GlobalMemoryStatusEx(&ms) && SYSERR == ERROR_INVALID_PARAMETER);
    ms.dwLength = 0;
    CHECK(!GlobalMemoryStatusEx(&ms));
    ms.dwLength = sizeof ms;
    CHECK(GlobalMemoryStatusEx(&ms));
    CHECK_EQ(ms.ullTotalVirtual, 0x7FFE0000ull);
    CHECK(ms.ullAvailVirtual <= ms.ullTotalVirtual);
    MEMORYSTATUSEX m2;
    void *r = VirtualAlloc(NULL, 0x100000, MEM_RESERVE, PAGE_READWRITE);
    m2.dwLength = sizeof m2;
    CHECK(GlobalMemoryStatusEx(&m2));
    CHECK_EQ(ms.ullAvailVirtual - m2.ullAvailVirtual, 0x100000ull);
    void *c = VirtualAlloc(NULL, 0x1000, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    MEMORYSTATUSEX m3;
    m3.dwLength = sizeof m3;
    CHECK(GlobalMemoryStatusEx(&m3));
    CHECK_EQ(m2.ullAvailVirtual - m3.ullAvailVirtual, 0x1000ull);   /* free pages: the granule tail stays free */
    MEMORYSTATUS s;
    memset(&s, 0, sizeof s);
    GlobalMemoryStatus(&s);
    CHECK_EQ(s.dwLength, sizeof s);
    CHECK_EQ(s.dwTotalVirtual, 0x7FFE0000u);
    CHECK(s.dwAvailVirtual <= 0x7FFE0000u);
    VirtualFree(r, 0, MEM_RELEASE);
    VirtualFree(c, 0, MEM_RELEASE);
    CHECK(GlobalMemoryStatusEx(&m3));
    CHECK(m3.ullAvailVirtual >= ms.ullAvailVirtual - 0x1000);
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    CHECK_EQ(U(si.lpMaximumApplicationAddress) - U(si.lpMinimumApplicationAddress) + 1, ms.ullTotalVirtual);
}

/* ---- file mappings */
#define VD "Data\\vm\\"
static void vmsetup(void) { CreateDirectoryA("Data", NULL); CreateDirectoryA("Data\\vm", NULL); }
static void vmfile(const char *p, const char *data)
{
    char h[1024];
    if (!fnvnx_path_map(p, h, sizeof h)) unlink(h);
    s4_write_file(p, data);
}
static HANDLE vmopen(const char *p, DWORD acc)
{
    return CreateFileA(p, acc, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
}
static void rd(const char *p, char *buf, DWORD n)
{
    HANDLE h = vmopen(p, GENERIC_READ);
    DWORD got = 0;
    memset(buf, 0, n);
    ReadFile(h, buf, n, &got, NULL);
    CloseHandle(h);
}

TEST(vm_map_pagefile)
{
    SetLastError(0);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0, NULL) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, 3, 0, 0x1000, NULL) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_COMMIT | SEC_RESERVE, 0, 0x1000, NULL) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_IMAGE, 0, 0x1000, NULL) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_LARGE_PAGES, 0, 0x1000, NULL) == NULL && SYSERR == ERROR_PRIVILEGE_NOT_HELD);
    CHECK(CreateFileMappingA((HANDLE)(uintptr_t)0x1234, NULL, PAGE_READWRITE, 0, 0x1000, NULL) == NULL && SYSERR == ERROR_INVALID_HANDLE);
    HANDLE m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x30000, NULL);
    CHECK(m != NULL);
    unsigned char *v1 = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
    unsigned char *v2 = (unsigned char *)MapViewOfFile(m, FILE_MAP_ALL_ACCESS, 0, 0, 0x20000);
    unsigned char *v3 = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0x10000, 0);
    CHECK(v1 && v2 && v3);
    CHECK((U(v1) & 0xFFFF) == 0);
    MEMORY_BASIC_INFORMATION q = Q(v1);
    CHECK(q.Type == MEM_MAPPED && q.State == MEM_COMMIT && q.AllocationBase == v1 && q.RegionSize == 0x30000);
    CHECK(q.Protect == PAGE_READWRITE && q.AllocationProtect == PAGE_READWRITE);
    CHECK_EQ(Q(v2).RegionSize, 0x20000);
    CHECK(Q(v3).Protect == PAGE_READONLY && Q(v3).RegionSize == 0x20000);
    CHECK_EQ(v1[5], 0);
    v1[0x10010] = 0x42;
    CHECK_EQ(v2[0x10010], 0x42);   /* views of one mapping share pages, at once */
    CHECK_EQ(v3[0x10], 0x42);      /* v3 starts at offset 0x10000 */
    CHECK(faults(v3, true));
    CHECK(!faults(v1, true));
    /* the view outlives the handle */
    CHECK(CloseHandle(m));
    v2[0x20] = 7;
    CHECK_EQ(v1[0x20], 7);
    SetLastError(0);
    CHECK(!UnmapViewOfFile(v1 + 0x1000) && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(!UnmapViewOfFile(NULL) && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(!UnmapViewOfFile((void *)&m) && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK(UnmapViewOfFile(v1));
    CHECK_EQ(Q(v1).State, MEM_FREE);
    CHECK(faults(v1, false));
    CHECK(!UnmapViewOfFile(v1) && SYSERR == ERROR_INVALID_ADDRESS);
    CHECK_EQ(v2[0x20], 7);   /* the pages live while any view does */
    CHECK(UnmapViewOfFile(v2));
    CHECK(UnmapViewOfFile(v3));
    /* the view rules */
    m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READONLY, 0, 0x30000, NULL);
    CHECK(m != NULL);
    CHECK(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(MapViewOfFile(m, FILE_MAP_ALL_ACCESS, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(MapViewOfFile(m, FILE_MAP_COPY, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(MapViewOfFile(m, FILE_MAP_EXECUTE | FILE_MAP_READ, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(MapViewOfFile(m, 0x100, 0, 0, 0) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0x1000, 0) == NULL && SYSERR == ERROR_MAPPED_ALIGNMENT);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0xFFFF, 0) == NULL && SYSERR == ERROR_MAPPED_ALIGNMENT);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0x30000, 0) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 1, 0, 0) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0x30001) == NULL && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(MapViewOfFile((HANDLE)(uintptr_t)0x1234, FILE_MAP_READ, 0, 0, 0) == NULL && SYSERR == ERROR_INVALID_HANDLE);
    unsigned char *ro = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0x1800);
    CHECK(ro != NULL);
    CHECK_EQ(Q(ro).RegionSize, 0x2000);
    DWORD old;
    SetLastError(0);
    CHECK(!VirtualProtect(ro, 0x1000, PAGE_READWRITE, &old) && SYSERR == ERROR_INVALID_PARAMETER);   /* beyond the view's access */
    CHECK(VirtualProtect(ro, 0x1000, PAGE_NOACCESS, &old) && old == PAGE_READONLY);
    CHECK(!VirtualFree(ro, 0, MEM_RELEASE) && SYSERR == ERROR_INVALID_PARAMETER);   /* views end with UnmapViewOfFile */
    CHECK(!VirtualFree(ro, 0x1000, MEM_DECOMMIT) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(UnmapViewOfFile(ro));
    /* copy-on-write views of a section: private */
    HANDLE wc = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_WRITECOPY, 0, 0x10000, NULL);
    CHECK(wc != NULL);
    unsigned char *c1 = (unsigned char *)MapViewOfFile(wc, FILE_MAP_COPY, 0, 0, 0);
    unsigned char *c2 = (unsigned char *)MapViewOfFile(wc, FILE_MAP_WRITE, 0, 0, 0);
    CHECK(c1 && c2);
    CHECK_EQ(Q(c1).Protect, PAGE_WRITECOPY);
    CHECK_EQ(Q(c2).Protect, PAGE_WRITECOPY);
    c1[3] = 9;
    CHECK_EQ(c2[3], 0);
    c2[3] = 5;
    CHECK_EQ(c1[3], 9);
    UnmapViewOfFile(c1);
    UnmapViewOfFile(c2);
    CloseHandle(wc);
    CloseHandle(m);
}

TEST(vm_map_reserve_named)
{
    /* SEC_RESERVE: the view is reserved until committed with VirtualAlloc */
    HANDLE m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_RESERVE, 0, 0x20000, NULL);
    CHECK(m != NULL);
    unsigned char *v = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
    CHECK(v != NULL);
    CHECK_EQ(Q(v).State, MEM_RESERVE);
    CHECK(faults(v, false));
    CHECK(VirtualAlloc(v + 0x1000, 0x2000, MEM_COMMIT, PAGE_READWRITE) == v + 0x1000);
    CHECK_EQ(Q(v + 0x1000).State, MEM_COMMIT);
    CHECK_EQ(Q(v + 0x1000).RegionSize, 0x2000);
    CHECK_EQ(Q(v + 0x1000).Type, MEM_MAPPED);
    CHECK(!faults(v + 0x1000, true));
    SetLastError(0);
    CHECK(!VirtualFree(v + 0x1000, 0x1000, MEM_DECOMMIT) && SYSERR == ERROR_INVALID_PARAMETER);
    CHECK(UnmapViewOfFile(v));
    CloseHandle(m);
    /* a committed mapping's pages cannot be committed again with another type of access beyond the view's */
    m = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_COMMIT | SEC_NOCACHE, 0, 0x1000, NULL);
    CHECK(m != NULL);
    CloseHandle(m);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE | SEC_NOCACHE, 0, 0x1000, NULL) == NULL && GetLastError() == ERROR_INVALID_PARAMETER);

    /* named objects */
    SetLastError(0);
    HANDLE a = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x10000, "vmtest_named_a");
    CHECK(a != NULL && SYSERR == 0);
    SetLastError(0);
    HANDLE b = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x10000, "vmtest_named_a");
    CHECK(b != NULL && SYSERR == ERROR_ALREADY_EXISTS);
    SetLastError(0);
    HANDLE l = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x10000, "Local\\vmtest_named_a");
    CHECK(l != NULL && SYSERR == ERROR_ALREADY_EXISTS);
    SetLastError(0);
    HANDLE g = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x10000, "Global\\vmtest_named_a");
    CHECK(g != NULL && SYSERR == 0);
    unsigned char *va = (unsigned char *)MapViewOfFile(a, FILE_MAP_WRITE, 0, 0, 0);
    unsigned char *vb = (unsigned char *)MapViewOfFile(b, FILE_MAP_WRITE, 0, 0, 0);
    unsigned char *vg = (unsigned char *)MapViewOfFile(g, FILE_MAP_WRITE, 0, 0, 0);
    CHECK(va && vb && vg && va != vb);
    va[100] = 0x33;
    CHECK_EQ(vb[100], 0x33);
    CHECK_EQ(vg[100], 0);
    CHECK(UnmapViewOfFile(va));
    CHECK(UnmapViewOfFile(vb));
    CHECK(UnmapViewOfFile(vg));
    CHECK(CloseHandle(a) && CloseHandle(b) && CloseHandle(l) && CloseHandle(g));
    /* the name dies with the last handle: a new object */
    SetLastError(0);
    a = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x10000, "vmtest_named_a");
    CHECK(a != NULL && SYSERR == 0);
    va = (unsigned char *)MapViewOfFile(a, FILE_MAP_READ, 0, 0, 0);
    CHECK(va && va[100] == 0);
    UnmapViewOfFile(va);
    CloseHandle(a);
    SetLastError(0);
    CHECK(CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x1000, "vm\\bad") == NULL && SYSERR == ERROR_PATH_NOT_FOUND);
}

TEST(vm_map_file)
{
    vmsetup();
    vmfile(VD "a.bin", "0123456789abcdef");
    HANDLE f = vmopen(VD "a.bin", GENERIC_READ | GENERIC_WRITE);
    CHECK(f != INVALID_HANDLE_VALUE);
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL);   /* size 0: the file's */
    CHECK(m != NULL);
    CHECK(CloseHandle(f));   /* the mapping keeps the file */
    unsigned char *v = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
    CHECK(v != NULL);
    CHECK(v && !memcmp(v, "0123456789abcdef", 16));
    CHECK_EQ(Q(v).RegionSize, 0x1000);
    CHECK_EQ(v[16], 0);   /* the rest of the page is zero */
    v[0] = 'X';
    unsigned char *v2 = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
    CHECK(v2 && v2[0] == 'X');   /* at once, between views */
    char buf[32];
    CHECK(UnmapViewOfFile(v));
    rd(VD "a.bin", buf, 16);
    CHECK(!memcmp(buf, "X123456789abcdef", 16));   /* write-through at unmap */
    CHECK_EQ(v2[1], '1');
    /* the file did not grow */
    f = vmopen(VD "a.bin", GENERIC_READ);
    CHECK_EQ(GetFileSize(f, NULL), 16);
    CloseHandle(f);
    UnmapViewOfFile(v2);
    /* FlushViewOfFile writes through while the view lives */
    v = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
    v[1] = 'Y';
    CHECK(FlushViewOfFile(v, 0));
    rd(VD "a.bin", buf, 16);
    CHECK(!memcmp(buf, "XY23456789abcdef", 16));
    v[2] = 'Z';
    UnmapViewOfFile(v);
    rd(VD "a.bin", buf, 16);
    CHECK(!memcmp(buf, "XYZ3456789abcdef", 16));
    CloseHandle(m);
    /* copy-on-write: the file stays */
    f = vmopen(VD "a.bin", GENERIC_READ);
    m = CreateFileMappingA(f, NULL, PAGE_WRITECOPY, 0, 0, NULL);
    CHECK(m != NULL);
    v = (unsigned char *)MapViewOfFile(m, FILE_MAP_COPY, 0, 0, 0);
    CHECK(v != NULL && v[3] == '3');
    v[3] = '#';
    CHECK_EQ(Q(v).Protect, PAGE_WRITECOPY);
    UnmapViewOfFile(v);
    rd(VD "a.bin", buf, 16);
    CHECK(!memcmp(buf, "XYZ3456789abcdef", 16));
    CHECK(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0) != NULL);   /* a write view of a copy-on-write section is a copy view */
    CloseHandle(m);
    /* read-only mappings */
    m = CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0, NULL);
    CHECK(m != NULL);
    SetLastError(0);
    CHECK(MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CloseHandle(m);
    /* protection against the file handle's access */
    SetLastError(0);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL) == NULL && SYSERR == ERROR_ACCESS_DENIED);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READONLY, 0, 0x2000, NULL) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);   /* cannot extend */
    CHECK(CreateFileMappingA(f, NULL, PAGE_WRITECOPY, 0, 0x2000, NULL) == NULL && SYSERR == ERROR_NOT_ENOUGH_MEMORY);
    CloseHandle(f);
    /* a mapping larger than the file extends a writable file */
    f = vmopen(VD "a.bin", GENERIC_READ | GENERIC_WRITE);
    m = CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0x3000, NULL);
    CHECK(m != NULL);
    CHECK_EQ(GetFileSize(f, NULL), 0x3000);
    v = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0, 0);
    CHECK(v && !memcmp(v, "XYZ3456789abcdef", 16) && v[0x2FFF] == 0);
    CHECK_EQ(Q(v).RegionSize, 0x3000);
    v[0x2FFF] = 0x66;
    UnmapViewOfFile(v);
    unsigned char big[0x3000];
    DWORD got = 0;
    SetFilePointer(f, 0, NULL, FILE_BEGIN);
    CHECK(ReadFile(f, big, sizeof big, &got, NULL) && got == 0x3000 && big[0x2FFF] == 0x66);
    CloseHandle(m);
    CloseHandle(f);
    /* an empty file */
    vmfile(VD "empty.bin", "");
    f = vmopen(VD "empty.bin", GENERIC_READ | GENERIC_WRITE);
    SetLastError(0);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, NULL) == NULL && SYSERR == ERROR_FILE_INVALID);
    CloseHandle(f);
    /* a view at an offset of a larger file */
    HANDLE w = CreateFileA(VD "off.bin", GENERIC_READ | GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    m = CreateFileMappingA(w, NULL, PAGE_READWRITE, 0, 0x30000, NULL);
    CHECK(m != NULL);
    v = (unsigned char *)MapViewOfFile(m, FILE_MAP_WRITE, 0, 0x10000, 0x100);
    CHECK(v != NULL);
    CHECK_EQ(Q(v).RegionSize, 0x1000);
    memcpy(v, "OFFSET", 7);
    UnmapViewOfFile(v);
    CHECK(SetFilePointer(w, 0x10000, NULL, FILE_BEGIN) == 0x10000);
    CHECK(ReadFile(w, buf, 7, &got, NULL) && !memcmp(buf, "OFFSET", 7));
    CloseHandle(m);
    CloseHandle(w);
    /* a name of a file mapping */
    f = vmopen(VD "a.bin", GENERIC_READ | GENERIC_WRITE);
    HANDLE n1 = CreateFileMappingA(f, NULL, PAGE_READWRITE, 0, 0, "vmtest_named_file");
    SetLastError(0);
    HANDLE n2 = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 0x1000, "vmtest_named_file");
    CHECK(n1 && n2 && SYSERR == ERROR_ALREADY_EXISTS);
    unsigned char *p1 = (unsigned char *)MapViewOfFile(n1, FILE_MAP_READ, 0, 0, 0);
    unsigned char *p2 = (unsigned char *)MapViewOfFile(n2, FILE_MAP_READ, 0, 0, 0);
    CHECK(p1 && p2 && !memcmp(p2, "XYZ3456789abcdef", 16));   /* the existing object, the file's */
    UnmapViewOfFile(p1);
    UnmapViewOfFile(p2);
    CloseHandle(n1);
    CloseHandle(n2);
    CloseHandle(f);
}

/* a small PE image: headers, .text (R-X, raw 0x200 at 0x200), .data (RW, 0x2000 virtual, raw 0x200 at 0x400) */
static void put16(unsigned char *p, unsigned v) { p[0] = (unsigned char)v; p[1] = (unsigned char)(v >> 8); }
static void put32(unsigned char *p, unsigned v) { put16(p, v & 0xFFFF); put16(p + 2, v >> 16); }
static void build_pe(unsigned char *f /* 0x600 */)
{
    memset(f, 0, 0x600);
    f[0] = 'M'; f[1] = 'Z';
    put32(f + 0x3C, 0x80);
    unsigned char *nt = f + 0x80;
    memcpy(nt, "PE\0\0", 4);
    put16(nt + 4, 0x14C);       /* machine */
    put16(nt + 6, 2);           /* sections */
    put16(nt + 20, 0xE0);       /* optional header size */
    put16(nt + 22, 0x102);
    unsigned char *oh = nt + 24;
    put16(oh, 0x10B);
    put32(oh + 28, 0x400000);   /* image base */
    put32(oh + 32, 0x1000);     /* section alignment */
    put32(oh + 36, 0x200);      /* file alignment */
    put32(oh + 56, 0x4000);     /* size of image */
    put32(oh + 60, 0x200);      /* size of headers */
    unsigned char *s = nt + 24 + 0xE0;
    memcpy(s, ".text", 5);
    put32(s + 8, 0x200); put32(s + 12, 0x1000); put32(s + 16, 0x200); put32(s + 20, 0x200); put32(s + 36, 0x60000020);
    s += 40;
    memcpy(s, ".data", 5);
    put32(s + 8, 0x2000); put32(s + 12, 0x2000); put32(s + 16, 0x200); put32(s + 20, 0x400); put32(s + 36, 0xC0000040);
    memcpy(f + 0x200, "TEXTSEC", 7);
    memcpy(f + 0x400, "DATASEC", 7);
}

TEST(vm_map_image)
{
    vmsetup();
    unsigned char pe[0x600];
    build_pe(pe);
    char path[] = VD "img.dll";
    {
        char h[1024];
        if (!fnvnx_path_map(path, h, sizeof h)) unlink(h);
        HANDLE w = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
        DWORD n;
        WriteFile(w, pe, sizeof pe, &n, NULL);
        CloseHandle(w);
    }
    HANDLE f = vmopen(path, GENERIC_READ);
    HANDLE m = CreateFileMappingA(f, NULL, PAGE_READONLY | SEC_IMAGE, 0, 0, NULL);
    CHECK(m != NULL);
    unsigned char *v = (unsigned char *)MapViewOfFile(m, FILE_MAP_READ, 0, 0, 0);
    CHECK(v != NULL);
    if (v) {
        CHECK(v[0] == 'M' && v[1] == 'Z');
        CHECK(!memcmp(v + 0x1000, "TEXTSEC", 7));
        CHECK(!memcmp(v + 0x2000, "DATASEC", 7));
        CHECK(v[0x1100] == 0 && v[0x3FFF] == 0);   /* the part past the raw data is zero */
        MEMORY_BASIC_INFORMATION q = Q(v);
        CHECK(q.Type == MEM_IMAGE && q.AllocationBase == v && q.Protect == PAGE_READONLY && q.RegionSize == 0x1000);
        CHECK_EQ(q.AllocationProtect, 0x80);
        q = Q(v + 0x1000);
        CHECK(q.Type == MEM_IMAGE && q.Protect == PAGE_EXECUTE_READ && q.RegionSize == 0x1000 && q.AllocationBase == v);
        q = Q(v + 0x2000);
        CHECK(q.Protect == PAGE_WRITECOPY && q.RegionSize == 0x2000);
        CHECK(!faults(v + 0x2000, true));
        CHECK(faults(v + 0x1000, true));
        CHECK(faults(v, true));
        CHECK(MapViewOfFile(m, FILE_MAP_READ, 0, 0x10000, 0) == NULL && GetLastError() != 0);
        UnmapViewOfFile(v);
    }
    CloseHandle(m);
    CloseHandle(f);
    /* not an image */
    vmfile(VD "notpe.bin", "this is not a PE image at all");
    f = vmopen(VD "notpe.bin", GENERIC_READ);
    SetLastError(0);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READONLY | SEC_IMAGE, 0, 0, NULL) == NULL && SYSERR == ERROR_BAD_EXE_FORMAT);
    CloseHandle(f);
    /* a section whose raw data lies past the end of the file */
    unsigned char bad[0x600];
    memcpy(bad, pe, sizeof bad);
    put32(bad + 0x80 + 24 + 0xE0 + 40 + 16, 0x1000);
    {
        char h[1024];
        vmfile(VD "badpe.dll", "x");
        if (!fnvnx_path_map(VD "badpe.dll", h, sizeof h)) unlink(h);
        HANDLE w = CreateFileA(VD "badpe.dll", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
        DWORD n;
        WriteFile(w, bad, sizeof bad, &n, NULL);
        CloseHandle(w);
    }
    f = vmopen(VD "badpe.dll", GENERIC_READ);
    SetLastError(0);
    CHECK(CreateFileMappingA(f, NULL, PAGE_READONLY | SEC_IMAGE, 0, 0, NULL) == NULL && SYSERR == ERROR_BAD_EXE_FORMAT);
    CloseHandle(f);
}
