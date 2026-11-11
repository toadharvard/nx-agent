/* test_gpa_names.cpp - system functions the game and its DLLs obtain BY NAME with GetProcAddress: the whole contract
 * of each one (encptr.cpp, fls.cpp, user32 / gdi32 rows), error paths included. Cases gpa_*. */
#include "s4_test.h"
#include "host_boundary.h"

#ifndef ERROR_INVALID_PARAMETER
#define ERROR_INVALID_PARAMETER 87L
#endif
#ifndef ERROR_NOT_ENOUGH_MEMORY
#define ERROR_NOT_ENOUGH_MEMORY 8L
#endif

extern "C" {
void *fnvnx_win32_lookup_export(const char *dll, const char *name);
LPVOID EncodePointer(LPVOID);
LPVOID DecodePointer(LPVOID);
DWORD FlsAlloc(void (*)(void *));
BOOL FlsFree(DWORD);
LPVOID FlsGetValue(DWORD);
BOOL FlsSetValue(DWORD, LPVOID);
}

static void *export_of(const char *dll, const char *name) { return fnvnx_win32_lookup_export(dll, name); }

/* ---- group 1: EncodePointer / DecodePointer */
TEST(gpa_k32_exports_by_name)
{
    CHECK(export_of("kernel32", "EncodePointer") == (void *)&EncodePointer);
    CHECK(export_of("kernel32", "DecodePointer") == (void *)&DecodePointer);
    CHECK(export_of("kernel32", "FlsAlloc") == (void *)&FlsAlloc);
    CHECK(export_of("kernel32", "FlsFree") == (void *)&FlsFree);
    CHECK(export_of("kernel32", "FlsGetValue") == (void *)&FlsGetValue);
    CHECK(export_of("kernel32", "FlsSetValue") == (void *)&FlsSetValue);
    HMODULE k = GetModuleHandleA("kernel32.dll");
    CHECK(k != NULL);
    CHECK(GetProcAddress(k, "EncodePointer") == (FARPROC)(void *)&EncodePointer);
    CHECK(GetProcAddress(k, "FlsSetValue") == (FARPROC)(void *)&FlsSetValue);
}

static LPVOID g_enc_from_thread;
static DWORD enc_worker(LPVOID p)
{
    g_enc_from_thread = EncodePointer(p);
    return 0;
}

TEST(gpa_encode_pointer)
{
    uintptr_t samples[] = { 0, 1, 2, 3, 4, 0x10, 0x7fffffffu, 0x80000000u, 0xfffffffeu, 0xffffffffu, 0x00400000u,
                            (uintptr_t)&g_enc_from_thread, (uintptr_t)&samples, (uintptr_t)-1 };
    for (uintptr_t p : samples) {
        LPVOID e = EncodePointer((LPVOID)p);
        CHECK(DecodePointer(e) == (LPVOID)p);
        CHECK(EncodePointer((LPVOID)p) == e);   /* one cookie for the process */
    }
    /* a dense sweep of low values and a spread over the word: the round trip is exact and encode is injective */
    uintptr_t prev_e = 0;
    for (uintptr_t p = 0; p < 4096; p++) {
        LPVOID e = EncodePointer((LPVOID)p);
        CHECK(DecodePointer(e) == (LPVOID)p);
        if (p)
            CHECK((uintptr_t)e != prev_e);
        prev_e = (uintptr_t)e;
    }
    uint64_t x = 0x9E3779B97F4A7C15ull;
    for (int i = 0; i < 20000; i++) {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        uintptr_t p = (uintptr_t)x;
        CHECK(DecodePointer(EncodePointer((LPVOID)p)) == (LPVOID)p);
        CHECK(EncodePointer(DecodePointer((LPVOID)p)) == (LPVOID)p);
    }
    /* the cookie is never zero: most values do not encode to themselves */
    unsigned same = 0;
    for (uintptr_t p = 1; p < 257; p++)
        same += EncodePointer((LPVOID)p) == (LPVOID)p;
    CHECK(same < 8);
    /* a value encoded in another thread decodes here: the cookie is per process */
    HANDLE h = CreateThread(NULL, 0, enc_worker, (LPVOID)0x12345678, 0, NULL);
    CHECK(h != NULL);
    WaitForSingleObject(h, 5000);
    CloseHandle(h);
    CHECK(g_enc_from_thread == EncodePointer((LPVOID)0x12345678));
    CHECK(DecodePointer(g_enc_from_thread) == (LPVOID)0x12345678);
}

/* ---- group 1: fiber local storage */
static const DWORD FLS_OUT = 0xFFFFFFFFu;
struct cb_rec { void *val; DWORD tid; };
static cb_rec g_cb[64];
static volatile int g_cb_n;
static void rec_cb(void *v)
{
    int n = __atomic_fetch_add(&g_cb_n, 1, __ATOMIC_SEQ_CST);
    if (n < 64) {
        g_cb[n].val = v;
        g_cb[n].tid = GetCurrentThreadId();
    }
}
static int cb_count(void) { return g_cb_n; }
static void cb_reset(void) { g_cb_n = 0; }

TEST(gpa_fls_single_thread)
{
    cb_reset();
    int inv0 = g_bnd_invokes;
    DWORD a = FlsAlloc(rec_cb);
    CHECK(a != FLS_OUT && a >= 1 && a < 128);
    DWORD b = FlsAlloc(NULL);
    CHECK(b != FLS_OUT && b >= 1 && b < 128 && b != a);
    SetLastError(0xdead);
    CHECK(FlsGetValue(a) == NULL);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);   /* a NULL value is not an error */
    CHECK(FlsSetValue(a, (LPVOID)0x1111));
    CHECK(FlsSetValue(b, (LPVOID)0x2222));
    SetLastError(0xdead);
    CHECK(FlsGetValue(a) == (LPVOID)0x1111);
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    CHECK(FlsGetValue(b) == (LPVOID)0x2222);
    CHECK(FlsSetValue(a, NULL));
    CHECK(FlsGetValue(a) == NULL);
    CHECK(FlsSetValue(a, (LPVOID)0x3333));
    /* bad indices: 0, out of range */
    DWORD bad[] = { 0, 128, 129, 1000, 0x7fffffffu, 0xFFFFFFFFu };
    for (DWORD i : bad) {
        SetLastError(0xdead);
        CHECK(FlsGetValue(i) == NULL);
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
        SetLastError(0xdead);
        CHECK(!FlsSetValue(i, (LPVOID)1));
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
        SetLastError(0xdead);
        CHECK(!FlsFree(i));
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    }
    DWORD unalloc = 1;
    while (unalloc == a || unalloc == b)
        unalloc++;
    SetLastError(0xdead);
    CHECK(FlsGetValue(unalloc) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0xdead);
    CHECK(!FlsSetValue(unalloc, (LPVOID)1));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0xdead);
    CHECK(!FlsFree(unalloc));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* a slot with a callback: FlsFree calls it with the value, once, on this thread; a slot without: no call */
    CHECK(FlsFree(b));
    CHECK_EQ(cb_count(), 0);
    CHECK(FlsFree(a));
    CHECK_EQ(cb_count(), 1);
    CHECK(g_cb[0].val == (LPVOID)0x3333);
    CHECK_EQ(g_cb[0].tid, GetCurrentThreadId());
    CHECK_EQ(g_bnd_invokes - inv0, 1);   /* the callback went through the boundary */
    /* freed: invalid again, and a second free fails */
    SetLastError(0xdead);
    CHECK(FlsGetValue(a) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0xdead);
    CHECK(!FlsFree(a));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* the lowest free slot is handed out again, with a NULL value */
    DWORD c = FlsAlloc(rec_cb);
    CHECK(c == (a < b ? a : b));
    CHECK(FlsGetValue(c) == NULL);
    CHECK(FlsFree(c));
    CHECK_EQ(cb_count(), 1);   /* a NULL value: no callback */
}

TEST(gpa_fls_slot_limit)
{
    cb_reset();
    DWORD idx[130];
    int n = 0;
    for (;;) {
        SetLastError(0xdead);
        DWORD i = FlsAlloc(rec_cb);
        if (i == FLS_OUT) {
            CHECK_EQ(GetLastError(), ERROR_NOT_ENOUGH_MEMORY);
            break;
        }
        CHECK(i >= 1 && i < 128);
        for (int k = 0; k < n; k++)
            CHECK(idx[k] != i);
        idx[n++] = i;
        if (n >= 129)
            break;
    }
    CHECK_EQ(n, 127);   /* 128 slots, index 0 is never handed out */
    CHECK(FlsFree(idx[5]));   /* one freed: one allocation works again */
    DWORD again = FlsAlloc(NULL);
    CHECK_EQ(again, idx[5]);
    CHECK(FlsAlloc(NULL) == FLS_OUT);
    CHECK(FlsSetValue(idx[0], (LPVOID)7));
    CHECK(FlsFree(again));
    for (int k = 0; k < n; k++)
        if (k != 5)
            CHECK(FlsFree(idx[k]));
    CHECK_EQ(cb_count(), 1);
    CHECK(g_cb[0].val == (LPVOID)7);
}

/* ---- multi-thread */
static DWORD g_slot_a, g_slot_b;
static volatile int g_phase, g_ready;

static DWORD fls_thread(LPVOID p)
{
    uintptr_t n = (uintptr_t)p;
    SetLastError(0xdead);
    CHECK(FlsGetValue(g_slot_a) == NULL);       /* values are per thread: nothing of the other threads */
    CHECK_EQ(GetLastError(), ERROR_SUCCESS);
    CHECK(FlsSetValue(g_slot_a, (LPVOID)(0xA00 + n)));
    CHECK(FlsSetValue(g_slot_b, (LPVOID)(0xB00 + n)));
    CHECK(FlsGetValue(g_slot_a) == (LPVOID)(0xA00 + n));
    __atomic_add_fetch(&g_ready, 1, __ATOMIC_SEQ_CST);
    while (g_phase < 3)
        Sleep(1);
    if (n == 2)
        ExitThread(0);   /* the explicit exit runs the callbacks too */
    return 0;
}

TEST(gpa_fls_threads)
{
    cb_reset();
    g_phase = 0;
    g_ready = 0;
    g_slot_a = FlsAlloc(rec_cb);
    g_slot_b = FlsAlloc(rec_cb);
    CHECK(g_slot_a != FLS_OUT && g_slot_b != FLS_OUT && g_slot_a < g_slot_b);
    CHECK(FlsSetValue(g_slot_a, (LPVOID)0xA00));   /* the main thread first: it is first in the order */
    HANDLE h[3];
    DWORD tid[3];
    for (int i = 0; i < 3; i++)
        h[i] = CreateThread(NULL, 0, fls_thread, (LPVOID)(uintptr_t)(i + 1), 0, &tid[i]);
    while (g_ready < 3)
        Sleep(1);
    CHECK(FlsGetValue(g_slot_b) == NULL);          /* this thread never set slot b */
    CHECK(FlsGetValue(g_slot_a) == (LPVOID)0xA00);
    /* FlsFree(a): the callback for EVERY thread's non-NULL value, on THIS thread, then the values are gone */
    int inv0 = g_bnd_invokes;
    CHECK(FlsFree(g_slot_a));
    CHECK_EQ(cb_count(), 4);
    CHECK_EQ(g_bnd_invokes - inv0, 4);
    bool seen[4] = { false, false, false, false };
    for (int k = 0; k < 4 && k < cb_count(); k++) {
        CHECK_EQ(g_cb[k].tid, GetCurrentThreadId());
        uintptr_t v = (uintptr_t)g_cb[k].val;
        CHECK(v >= 0xA00 && v <= 0xA03);
        if (v >= 0xA00 && v <= 0xA03)
            seen[v - 0xA00] = true;
    }
    CHECK(seen[0] && seen[1] && seen[2] && seen[3]);
    CHECK_EQ((uintptr_t)g_cb[0].val, 0xA00);        /* registration order: this thread stored first */
    SetLastError(0xdead);
    CHECK(FlsGetValue(g_slot_a) == NULL);
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    /* thread exit: the callback runs on the exiting thread with its own non-NULL value, before the thread is over */
    cb_reset();
    g_phase = 3;
    for (int i = 0; i < 3; i++) {
        CHECK_EQ(WaitForSingleObject(h[i], 5000), WAIT_OBJECT_0);
        CloseHandle(h[i]);
    }
    CHECK_EQ(cb_count(), 3);
    for (int k = 0; k < 3 && k < cb_count(); k++) {
        int who = -1;
        for (int i = 0; i < 3; i++)
            if (g_cb[k].tid == tid[i])
                who = i;
        CHECK(who >= 0);
        if (who >= 0)
            CHECK((uintptr_t)g_cb[k].val == 0xB00 + (uintptr_t)(who + 1));
    }
    CHECK_EQ(g_bnd_lock_violations, 0);
    CHECK(FlsGetValue(g_slot_b) == NULL);
    CHECK(FlsFree(g_slot_b));
    CHECK_EQ(cb_count(), 3);
}

/* the callbacks at thread exit run in ascending index order; a thread that never stored has nothing to run */
static DWORD g_order_a, g_order_b, g_order_c;
static DWORD order_thread(LPVOID)
{
    FlsSetValue(g_order_c, (LPVOID)3);
    FlsSetValue(g_order_a, (LPVOID)1);
    FlsSetValue(g_order_b, (LPVOID)2);
    return 0;
}
static DWORD empty_thread(LPVOID) { return 0; }

TEST(gpa_fls_thread_exit_order)
{
    cb_reset();
    g_order_a = FlsAlloc(rec_cb);
    g_order_b = FlsAlloc(rec_cb);
    g_order_c = FlsAlloc(rec_cb);
    HANDLE h = CreateThread(NULL, 0, empty_thread, NULL, 0, NULL);
    WaitForSingleObject(h, 5000);
    CloseHandle(h);
    CHECK_EQ(cb_count(), 0);
    h = CreateThread(NULL, 0, order_thread, NULL, 0, NULL);
    WaitForSingleObject(h, 5000);
    CloseHandle(h);
    CHECK_EQ(cb_count(), 3);
    CHECK((uintptr_t)g_cb[0].val == 1 && (uintptr_t)g_cb[1].val == 2 && (uintptr_t)g_cb[2].val == 3);
    CHECK(FlsFree(g_order_a) && FlsFree(g_order_b) && FlsFree(g_order_c));
    CHECK_EQ(cb_count(), 3);   /* the ended thread's values are gone: nothing left to call */
    CHECK_EQ(g_bnd_lock_violations, 0);
}

/* a callback may itself use FLS (no platform lock is held across it) */
static DWORD g_reent_slot;
static void reent_cb(void *v)
{
    rec_cb(v);
    FlsSetValue(g_reent_slot, NULL);
    FlsGetValue(g_reent_slot);
    DWORD t = FlsAlloc(NULL);
    if (t != FLS_OUT)
        FlsFree(t);
}
TEST(gpa_fls_callback_reentrancy)
{
    cb_reset();
    g_reent_slot = FlsAlloc(reent_cb);
    CHECK(FlsSetValue(g_reent_slot, (LPVOID)5));
    CHECK(FlsFree(g_reent_slot));
    CHECK_EQ(cb_count(), 1);
    CHECK_EQ(g_bnd_lock_violations, 0);
}
