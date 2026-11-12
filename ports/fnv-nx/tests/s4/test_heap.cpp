/* test_heap.cpp - the Win32 heap API over its documented domain: private heaps, fixed-size heaps, default flags,
 * HeapDestroy, zeroing, in-place realloc, exceptions, exact HeapSize, LocalFree, serialization. */
#include "s4_test.h"

#include <malloc.h>
#include <stdint.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef STATUS_NO_MEMORY
#define STATUS_NO_MEMORY 0xC0000017u
#endif
#ifndef HEAP_NO_SERIALIZE
#define HEAP_NO_SERIALIZE 0x00000001
#endif
#ifndef HEAP_GENERATE_EXCEPTIONS
#define HEAP_GENERATE_EXCEPTIONS 0x00000004
#endif
#ifndef HEAP_ZERO_MEMORY
#define HEAP_ZERO_MEMORY 0x00000008
#endif
#ifndef HEAP_REALLOC_IN_PLACE_ONLY
#define HEAP_REALLOC_IN_PLACE_ONLY 0x00000010
#endif

extern "C" BOOL HeapDestroy(HANDLE);

static bool all_bytes(const void *p, unsigned char v, size_t n)
{
    for (size_t i = 0; i < n; i++)
        if (((const unsigned char *)p)[i] != v) return false;
    return true;
}

TEST(heap_create_distinct)
{
    HANDLE a = HeapCreate(0, 0, 0), b = HeapCreate(0, 0, 0);
    CHECK(a != NULL && b != NULL);
    CHECK(a != b);
    CHECK(a != GetProcessHeap() && b != GetProcessHeap());
    CHECK(HeapDestroy(a));
    CHECK(HeapDestroy(b));
}

TEST(heap_destroy_invalid)
{
    SetLastError(0);
    CHECK(!HeapDestroy(GetProcessHeap()));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK(!HeapDestroy(NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    HANDLE a = HeapCreate(0, 0, 0);
    CHECK(HeapDestroy(a));
    SetLastError(0);
    CHECK(!HeapDestroy(a));   /* already destroyed */
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CHECK(GetProcessHeap() != NULL);
    void *p = HeapAlloc(GetProcessHeap(), 0, 10);
    CHECK(p != NULL);
    CHECK(HeapFree(GetProcessHeap(), 0, p));
}

TEST(heap_destroy_frees_all_and_isolates)
{
    HANDLE b = HeapCreate(0, 0, 0);
    void *keep[8];
    for (int i = 0; i < 8; i++) {
        keep[i] = HeapAlloc(b, 0, 100 + i);
        memset(keep[i], 0x40 + i, 100 + i);
    }
    /* exact accounting: the test binary runs with the allocator's thread cache off (GLIBC_TUNABLES in the Makefile),
       so a freed chunk is never counted as in use; `before` is taken before the heap object itself exists */
    size_t before = mallinfo2().uordblks;
    HANDLE a = HeapCreate(0, 0, 0);
    void *blk[1000];
    for (int i = 0; i < 1000; i++) blk[i] = HeapAlloc(a, 0, 2000 + i);   /* above the tcache sizes: mallinfo counts cached chunks as in use */
    CHECK(mallinfo2().uordblks > before);
    HeapFree(a, 0, blk[3]);   /* one already freed, the rest live at destroy */
    CHECK(HeapDestroy(a));
    CHECK_EQ(mallinfo2().uordblks, before);   /* every block and the heap object are gone */
    for (int i = 0; i < 8; i++) {
        CHECK(all_bytes(keep[i], 0x40 + i, 100 + i));
        CHECK_EQ(HeapSize(b, 0, keep[i]), 100 + i);
    }
    void *n = HeapAlloc(b, 0, 5);
    CHECK(n != NULL);
    CHECK(HeapDestroy(b));
}

TEST(heap_fixed_size)
{
    HANDLE h = HeapCreate(0, 0, 0x10000);
    CHECK(h != NULL);
    void *a = HeapAlloc(h, 0, 0x8000), *b = HeapAlloc(h, 0, 0x8000);
    CHECK(a != NULL && b != NULL);
    CHECK(HeapAlloc(h, 0, 0x100) == NULL);   /* the heap is full */
    HeapFree(h, 0, b);
    void *c = HeapAlloc(h, 0, 0x4000);
    CHECK(c != NULL);
    CHECK(HeapReAlloc(h, 0, c, 0x9000) == NULL);   /* growth beyond the total fails, c stays valid */
    CHECK_EQ(HeapSize(h, 0, c), 0x4000);
    CHECK(HeapDestroy(h));
    /* a single block above 0x7FFF8 fails in a fixed heap even if the total would fit */
    h = HeapCreate(0, 0, 0x400000);
    CHECK(HeapAlloc(h, 0, 0x7FFF9) == NULL);
    void *d = HeapAlloc(h, 0, 0x7FFF8);
    CHECK(d != NULL);
    CHECK(HeapReAlloc(h, 0, d, 0x7FFF9) == NULL);
    CHECK_EQ(HeapSize(h, 0, d), 0x7FFF8);
    CHECK(HeapDestroy(h));
    /* a growable heap has no such limit */
    h = HeapCreate(0, 0, 0);
    void *e = HeapAlloc(h, 0, 0x800000);
    CHECK(e != NULL);
    CHECK(HeapDestroy(h));
}

TEST(heap_zero_and_realloc)
{
    HANDLE h = HeapCreate(0, 0, 0);
    unsigned char *p = (unsigned char *)HeapAlloc(h, HEAP_ZERO_MEMORY, 100);
    CHECK(p && all_bytes(p, 0, 100));
    memset(p, 0xAA, 100);
    p = (unsigned char *)HeapReAlloc(h, HEAP_ZERO_MEMORY, p, 5000);
    CHECK(p != NULL);
    CHECK(all_bytes(p, 0xAA, 100));
    CHECK(all_bytes(p + 100, 0, 4900));   /* the grown part is zero */
    memset(p, 0xBB, 5000);
    p = (unsigned char *)HeapReAlloc(h, HEAP_ZERO_MEMORY, p, 50);   /* shrink, then grow again: zero again */
    CHECK(p && all_bytes(p, 0xBB, 50));
    p = (unsigned char *)HeapReAlloc(h, HEAP_ZERO_MEMORY, p, 300);
    CHECK(p && all_bytes(p, 0xBB, 50) && all_bytes(p + 50, 0, 250));
    CHECK_EQ(HeapSize(h, 0, p), 300);
    /* without the flag the content up to the smaller size is kept */
    p = (unsigned char *)HeapReAlloc(h, 0, p, 20000);
    CHECK(p && all_bytes(p, 0xBB, 50));
    CHECK(HeapDestroy(h));
}

TEST(heap_realloc_in_place)
{
    HANDLE h = HeapCreate(0, 0, 0);
    unsigned char *p = (unsigned char *)HeapAlloc(h, 0, 100);
    memset(p, 0x77, 100);
    void *q = HeapReAlloc(h, HEAP_REALLOC_IN_PLACE_ONLY, p, 40);   /* shrinking is always in place */
    CHECK(q == p);
    CHECK_EQ(HeapSize(h, 0, p), 40);
    q = HeapReAlloc(h, HEAP_REALLOC_IN_PLACE_ONLY, p, 100);   /* back up within the block */
    CHECK(q == p || q == NULL);
    if (q) CHECK_EQ(HeapSize(h, 0, p), 100);
    void *big = HeapReAlloc(h, HEAP_REALLOC_IN_PLACE_ONLY, p, 1 << 24);   /* cannot be done in place */
    CHECK(big == NULL);
    CHECK(all_bytes(p, 0x77, 40));   /* the original block stays valid */
    CHECK(HeapSize(h, 0, p) != (SIZE_T)-1);
    CHECK(HeapFree(h, 0, p));
    CHECK(HeapDestroy(h));
}

TEST(heap_size_exact)
{
    HANDLE h = HeapCreate(0, 0, 0);
    static const size_t sz[] = {0, 1, 7, 8, 9, 15, 16, 17, 100, 4095, 70000};
    for (size_t s : sz) {
        void *p = HeapAlloc(h, 0, s);
        CHECK(p != NULL);
        CHECK_EQ(HeapSize(h, 0, p), s);
        CHECK(((uintptr_t)p & 7) == 0);
        HeapFree(h, 0, p);
    }
    HANDLE ph = GetProcessHeap();
    void *p = HeapAlloc(ph, 0, 33);
    CHECK_EQ(HeapSize(ph, 0, p), 33);
    p = HeapReAlloc(ph, 0, p, 1000);
    CHECK_EQ(HeapSize(ph, 0, p), 1000);
    HeapFree(ph, 0, p);
    CHECK(HeapDestroy(h));
}

TEST(heap_zero_size_unique)
{
    HANDLE h = HeapCreate(0, 0, 0);
    void *a = HeapAlloc(h, 0, 0), *b = HeapAlloc(h, 0, 0);
    CHECK(a != NULL && b != NULL && a != b);
    CHECK(HeapFree(h, 0, a));
    CHECK(HeapFree(h, 0, b));
    CHECK(HeapFree(h, 0, NULL));
    CHECK(HeapFree(GetProcessHeap(), 0, NULL));
    CHECK(HeapDestroy(h));
}

TEST(heap_default_flags)
{
    HANDLE h = HeapCreate(HEAP_NO_SERIALIZE, 0, 0);
    CHECK(h != NULL);
    void *p = HeapAlloc(h, 0, 64);
    CHECK(p != NULL);
    CHECK(HeapFree(h, 0, p));
    CHECK(HeapDestroy(h));
    /* HEAP_ZERO_MEMORY is not a heap option, but the default HEAP_GENERATE_EXCEPTIONS is: checked in the fork test */
}

/* HEAP_GENERATE_EXCEPTIONS: a failure raises STATUS_NO_MEMORY. This platform has no SEH, so RaiseException ends
 * the process; the child must die, while the same failure without the flag returns NULL in the child. */
static int run_child(HANDLE (*mk)(void), DWORD flags, int op)
{
    fflush(NULL);
    pid_t pid = fork();
    if (pid == 0) {
        if (!freopen("/dev/null", "w", stderr)) _exit(5);
        HANDLE h = mk();
        void *r;
        if (op == 0) {
            r = HeapAlloc(h, flags, 0x100000);
        } else {
            void *p = HeapAlloc(h, 0, 16);
            r = HeapReAlloc(h, flags, p, 0x100000);
        }
        _exit(r == NULL ? 3 : 4);   /* returned: NULL (3) or a block (4) */
    }
    int st = 0;
    waitpid(pid, &st, 0);
    return WIFEXITED(st) ? WEXITSTATUS(st) : 100;   /* 100: died by a signal (os_fatal aborts) or exit(1) paths */
}
static HANDLE mk_fixed(void) { return HeapCreate(0, 0, 0x10000); }
static HANDLE mk_fixed_exc(void) { return HeapCreate(HEAP_GENERATE_EXCEPTIONS, 0, 0x10000); }

TEST(heap_generate_exceptions)
{
    CHECK_EQ(run_child(mk_fixed, 0, 0), 3);   /* plain failure: NULL */
    CHECK_EQ(run_child(mk_fixed, 0, 1), 3);
    CHECK(run_child(mk_fixed, HEAP_GENERATE_EXCEPTIONS, 0) != 3 && run_child(mk_fixed, HEAP_GENERATE_EXCEPTIONS, 0) != 4);
    CHECK(run_child(mk_fixed, HEAP_GENERATE_EXCEPTIONS, 1) != 3 && run_child(mk_fixed, HEAP_GENERATE_EXCEPTIONS, 1) != 4);
    /* the heap's default flag is OR-ed into every call */
    CHECK(run_child(mk_fixed_exc, 0, 0) != 3 && run_child(mk_fixed_exc, 0, 0) != 4);
    CHECK(run_child(mk_fixed_exc, 0, 1) != 3 && run_child(mk_fixed_exc, 0, 1) != 4);
    /* success never raises */
    HANDLE h = HeapCreate(HEAP_GENERATE_EXCEPTIONS, 0, 0);
    void *p = HeapAlloc(h, 0, 100);
    CHECK(p != NULL);
    CHECK(HeapDestroy(h));
}

TEST(heap_local_free)
{
    CHECK(LocalFree(NULL) == NULL);
    HANDLE a = LocalAlloc(0, 40), z = LocalAlloc(0x40, 40);
    CHECK(a != NULL && z != NULL);
    CHECK(all_bytes(z, 0, 40));
    CHECK(LocalFree(a) == NULL);
    CHECK(LocalFree(z) == NULL);
    /* a block of the process heap can be freed by LocalFree, and a LocalAlloc block by HeapFree */
    void *p = HeapAlloc(GetProcessHeap(), 0, 24);
    CHECK(LocalFree((HANDLE)p) == NULL);
    HANDLE l = LocalAlloc(0, 24);
    CHECK_EQ(HeapSize(GetProcessHeap(), 0, l), 24);
    CHECK(HeapFree(GetProcessHeap(), 0, l));
}

/* serialization: two threads hammer one heap (and a second pair a NO_SERIALIZE heap each); a broken block list
 * or a torn size shows up as lost content, a wrong HeapSize or a crash */
static HANDLE g_h;
static volatile LONG g_bad;
static DWORD hammer(LPVOID arg)
{
    unsigned char tag = (unsigned char)(uintptr_t)arg;
    void *live[64] = {};
    unsigned x = tag * 2654435761u + 1;
    for (int i = 0; i < 60000; i++) {
        x = x * 1664525u + 1013904223u;
        int k = (x >> 8) & 63;
        size_t n = 1 + ((x >> 16) & 255);
        if (live[k]) {
            size_t s = HeapSize(g_h, 0, live[k]);
            if (s < 1 || s > 256 || !all_bytes(live[k], tag, s)) InterlockedIncrement(&g_bad);
            if (x & 1) {
                void *q = HeapReAlloc(g_h, 0, live[k], n);
                if (!q) InterlockedIncrement(&g_bad); else {
                    if (!all_bytes(q, tag, s < n ? s : n)) InterlockedIncrement(&g_bad);
                    live[k] = q;
                    memset(q, tag, n);
                }
            } else {
                HeapFree(g_h, 0, live[k]);
                live[k] = NULL;
            }
        } else {
            live[k] = HeapAlloc(g_h, 0, n);
            if (!live[k]) { InterlockedIncrement(&g_bad); continue; }
            memset(live[k], tag, n);
        }
    }
    for (void *p : live)
        if (p) HeapFree(g_h, 0, p);
    return 0;
}

TEST(heap_two_threads)
{
    g_h = HeapCreate(0, 0, 0);
    g_bad = 0;
    HANDLE t[2];
    t[0] = test_CreateThread(NULL, 0, hammer, (LPVOID)(uintptr_t)0x11, 0, NULL);
    t[1] = test_CreateThread(NULL, 0, hammer, (LPVOID)(uintptr_t)0x22, 0, NULL);
    WaitForSingleObject(t[0], INFINITE);
    WaitForSingleObject(t[1], INFINITE);
    CHECK_EQ(g_bad, 0);
    size_t before = mallinfo2().uordblks;
    CHECK(HeapDestroy(g_h));
    CHECK(mallinfo2().uordblks <= before);
    /* the process heap too */
    g_h = GetProcessHeap();
    t[0] = test_CreateThread(NULL, 0, hammer, (LPVOID)(uintptr_t)0x33, 0, NULL);
    t[1] = test_CreateThread(NULL, 0, hammer, (LPVOID)(uintptr_t)0x44, 0, NULL);
    WaitForSingleObject(t[0], INFINITE);
    WaitForSingleObject(t[1], INFINITE);
    CHECK_EQ(g_bad, 0);
}
