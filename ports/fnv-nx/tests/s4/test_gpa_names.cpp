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

static DWORD fls_thread_body(LPVOID p)
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
/* the CHECKs of this procedure end it, not the process: the harness guard (s4_test.h), the case fails at its join */
static DWORD fls_thread(LPVOID p)
{
    return s4_thread_guard([&]() -> DWORD { return fls_thread_body(p); }, (DWORD)0xFA11);
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

/* ---- group 2: user32 GetLastActivePopup, GetProcessWindowStation, GetUserObjectInformationA */
extern "C" {
HWND GetLastActivePopup(HWND);
HANDLE GetProcessWindowStation(void);
BOOL GetUserObjectInformationA(HANDLE, int, void *, DWORD, LPDWORD);
HANDLE fnvnx_default_desktop(void);
}
#ifndef ERROR_INVALID_HANDLE
#define ERROR_INVALID_HANDLE 6L
#endif
#define ERROR_INSUFF 122L

static LRESULT gpa_proc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }
static HWND gpa_win(DWORD style, HWND parent)
{
    static bool reg;
    if (!reg) {
        WNDCLASSA wc;
        memset(&wc, 0, sizeof wc);
        wc.lpfnWndProc = gpa_proc;
        wc.lpszClassName = "GpaWin";
        RegisterClassA(&wc);
        reg = true;
    }
    return CreateWindowExA(0, "GpaWin", "g", style, 10, 10, 200, 150, parent, NULL, NULL, NULL);
}

TEST(gpa_user32_exports_by_name)
{
    CHECK(export_of("user32", "GetLastActivePopup") == (void *)&GetLastActivePopup);
    CHECK(export_of("user32", "GetProcessWindowStation") == (void *)&GetProcessWindowStation);
    CHECK(export_of("user32", "GetUserObjectInformationA") == (void *)&GetUserObjectInformationA);
    HMODULE u = LoadLibraryA("USER32.DLL");
    CHECK(GetProcAddress(u, "GetLastActivePopup") != NULL);
}

TEST(gpa_get_last_active_popup)
{
    const DWORD OVL = 0x00CF0000, POPUP = 0x80000000u, VIS = 0x10000000, CHILD = 0x40000000;
    SetLastError(0);
    CHECK(GetLastActivePopup(NULL) == NULL);
    CHECK_EQ(GetLastError(), 1400);
    CHECK(GetLastActivePopup((HWND)(uintptr_t)0x7ee4) == NULL);
    HWND o = gpa_win(OVL | VIS, NULL);
    CHECK(GetLastActivePopup(o) == o);                     /* no popup owned */
    HWND p1 = gpa_win(POPUP | VIS, o);
    HWND p2 = gpa_win(POPUP | VIS, o);
    HWND c = gpa_win(CHILD | VIS, o);
    SetForegroundWindow(p1);
    CHECK(GetActiveWindow() == p1);
    CHECK(GetLastActivePopup(o) == p1);
    SetForegroundWindow(p2);
    CHECK(GetLastActivePopup(o) == p2);
    SetForegroundWindow(o);
    CHECK(GetLastActivePopup(o) == p2);                    /* the popup remains the last active one while the owner is active */
    SetForegroundWindow(p1);
    CHECK(GetLastActivePopup(o) == p1);
    /* a popup, a child, an owned window: itself */
    CHECK(GetLastActivePopup(p1) == p1);
    CHECK(GetLastActivePopup(p2) == p2);
    CHECK(GetLastActivePopup(c) == c);
    HWND o2 = gpa_win(OVL | VIS, NULL);                    /* an unrelated top-level window has no popups */
    CHECK(GetLastActivePopup(o2) == o2);
    DestroyWindow(p1);                                     /* the popup goes: the owner is its own last active popup */
    CHECK(GetLastActivePopup(o) == o);
    DestroyWindow(p2);
    DestroyWindow(o2);
    DestroyWindow(o);
    CHECK(GetLastActivePopup(o) == NULL);
}

TEST(gpa_window_station_and_user_object)
{
    HANDLE ws = GetProcessWindowStation();
    CHECK(ws != NULL && ws != INVALID_HANDLE_VALUE);
    CHECK(GetProcessWindowStation() == ws);               /* stable */
    HANDLE dk = fnvnx_default_desktop();
    CHECK(dk != NULL && dk != ws);
    DWORD need = 0;
    uint32_t fl[3] = { 9, 9, 9 };
    CHECK(GetUserObjectInformationA(ws, 1, fl, sizeof fl, &need));
    CHECK_EQ(need, 12);
    CHECK(fl[0] == 0 && fl[1] == 0 && fl[2] == 1);        /* WSF_VISIBLE */
    CHECK(GetUserObjectInformationA(dk, 1, fl, sizeof fl, NULL));
    CHECK(fl[2] == 0);
    char name[32];
    CHECK(GetUserObjectInformationA(ws, 2, name, sizeof name, &need));
    CHECK_STR(name, "WinSta0");
    CHECK_EQ(need, 8);
    CHECK(GetUserObjectInformationA(dk, 2, name, sizeof name, &need));
    CHECK_STR(name, "Default");
    CHECK(GetUserObjectInformationA(ws, 3, name, sizeof name, &need));
    CHECK_STR(name, "WindowStation");
    CHECK_EQ(need, 14);
    CHECK(GetUserObjectInformationA(dk, 3, name, sizeof name, &need));
    CHECK_STR(name, "Desktop");
    need = 77;
    CHECK(GetUserObjectInformationA(ws, 4, name, sizeof name, &need));   /* no SID */
    CHECK_EQ(need, 0);
    memset(name, 'x', sizeof name);
    need = 0;
    SetLastError(0);
    CHECK(!GetUserObjectInformationA(ws, 2, name, 7, &need));
    CHECK_EQ(GetLastError(), ERROR_INSUFF);
    CHECK_EQ(need, 8);
    CHECK(name[0] == 'x');
    need = 0;
    SetLastError(0);
    CHECK(!GetUserObjectInformationA(ws, 3, NULL, 0, &need));
    CHECK_EQ(GetLastError(), ERROR_INSUFF);
    CHECK_EQ(need, 14);
    SetLastError(0);
    CHECK(!GetUserObjectInformationA(ws, 1, fl, 11, &need));
    CHECK_EQ(GetLastError(), ERROR_INSUFF);
    CHECK_EQ(need, 12);
    SetLastError(0);
    CHECK(!GetUserObjectInformationA(ws, 1, NULL, 12, &need));
    CHECK_EQ(GetLastError(), 998);
    CHECK(GetUserObjectInformationA(ws, 2, name, 8, &need));   /* exactly enough */
    SetLastError(0);
    CHECK(!GetUserObjectInformationA((HANDLE)(uintptr_t)0x7ee4, 1, fl, sizeof fl, &need));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK(!GetUserObjectInformationA(NULL, 1, fl, sizeof fl, &need));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    HANDLE ev = CreateEventA(NULL, TRUE, FALSE, NULL);   /* a handle of another object type */
    SetLastError(0);
    CHECK(!GetUserObjectInformationA(ev, 1, fl, sizeof fl, &need));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    CloseHandle(ev);
    int badidx[] = { 0, 5, 6, 7, -1, 100 };
    for (int i : badidx) {
        SetLastError(0);
        CHECK(!GetUserObjectInformationA(ws, i, fl, sizeof fl, &need));
        CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    }
    CHECK(CloseHandle(ws));                               /* a closed handle is replaced by a fresh one */
    HANDLE ws2 = GetProcessWindowStation();
    CHECK(ws2 != NULL);
    CHECK(GetUserObjectInformationA(ws2, 2, name, sizeof name, &need));
    CHECK_STR(name, "WinSta0");
}

/* ---- group 3: gdi32 font queries by glyph index.
 * Reference: the Wine 9.0 dump /home/user/gd/fontref/fontdump.txt: its `glyph <font> U+XXXX metrics ... incx=N` lines are the
 * advance of a character; through the cmap (GetCharacterPlacementW's lpGlyphs) the same advance must come out by glyph index.
 * GetFontUnicodeRanges has no reference line: it is compared with FreeType's own cmap walk of the face file. */
#include <ft2build.h>
#include FT_FREETYPE_H
#include <stdlib.h>
#include <string>
#include <vector>

extern "C" {
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
HGDIOBJ GetStockObject(int);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
DWORD GetCharacterPlacementW(HDC, LPCWSTR, int, int, void *, DWORD);
DWORD GetGlyphOutlineW(HDC, UINT, UINT, void *, DWORD, void *, const void *);
BOOL GetCharABCWidthsI(HDC, UINT, UINT, unsigned short *, void *);
BOOL GetCharWidthI(HDC, UINT, UINT, unsigned short *, int *);
DWORD GetFontUnicodeRanges(HDC, void *);
}

namespace {
struct GcpW { uint32_t lStructSize; char *lpOutString; uint32_t *lpOrder; int *lpDx; int *lpCaretPos; char *lpClass; uint16_t *lpGlyphs; uint32_t nGlyphs; int nMaxFit; };
int stock_id(const std::string &n)
{
    static const struct { const char *n; int id; } t[] = { { "OEM_FIXED_FONT", 10 }, { "ANSI_FIXED_FONT", 11 }, { "ANSI_VAR_FONT", 12 },
        { "SYSTEM_FONT", 13 }, { "DEVICE_DEFAULT_FONT", 14 }, { "SYSTEM_FIXED_FONT", 16 }, { "DEFAULT_GUI_FONT", 17 } };
    for (auto &e : t) if (n == e.n) return e.id;
    return -1;
}
uint16_t glyph_of(HDC dc, unsigned ch)
{
    uint16_t g = 0xFFFF;
    GcpW gcp;
    memset(&gcp, 0, sizeof gcp);
    gcp.lStructSize = sizeof gcp; gcp.lpGlyphs = &g; gcp.nGlyphs = 1;
    WCHAR w = (WCHAR)ch;
    GetCharacterPlacementW(dc, &w, 1, 0, &gcp, 0x10 /* GCP_GLYPHSHAPE */);
    return g;
}
}

TEST(gpa_gdi32_exports_by_name)
{
    CHECK(export_of("gdi32", "GetCharWidthI") == (void *)&GetCharWidthI);
    CHECK(export_of("gdi32", "GetCharABCWidthsI") == (void *)&GetCharABCWidthsI);
    CHECK(export_of("gdi32", "GetFontUnicodeRanges") == (void *)&GetFontUnicodeRanges);
}

TEST(gpa_char_width_by_index_reference)
{
    FILE *f = fopen((std::string(getenv("FNVNX_FONT_REF") ? getenv("FNVNX_FONT_REF") : "") + "/fontdump.txt").c_str(), "r");
    REQUIRE(f != NULL);
    char *buf = (char *)malloc(1 << 20);
    int lines = 0, abc_ok = 0, abc_refused = 0;
    while (fgets(buf, 1 << 20, f)) {
        if (strncmp(buf, "glyph ", 6)) continue;
        char name[64], kind[16];
        unsigned ch = 0;
        if (sscanf(buf, "glyph %63s U+%x %15s", name, &ch, kind) != 3 || strcmp(kind, "metrics")) continue;
        const char *p = strstr(buf, " incx=");
        int incx = p ? atoi(p + 6) : -1, sid = stock_id(name);
        CHECK(sid >= 0 && incx >= 0);
        if (sid < 0 || incx < 0) continue;
        HDC dc = CreateCompatibleDC(NULL);
        HGDIOBJ old = SelectObject(dc, GetStockObject(sid));
        (void)old;
        uint16_t g = glyph_of(dc, ch);
        int w = -1;
        CHECK(GetCharWidthI(dc, 0, 1, &g, &w));
        /* the one source of an advance by glyph index: GetGlyphOutline(GGO_GLYPH_INDEX), cellinc.x */
        static const uint8_t ident[16] = { 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0 };
        uint8_t gm[20] = { 0 };
        CHECK(GetGlyphOutlineW(dc, g, 0x80 | 0, gm, 0, NULL, ident) != 0xFFFFFFFFu);
        int16_t cellx;
        memcpy(&cellx, gm + 16, 2);
        CHECK_EQ(w, cellx);
        const bool truetype = !strcmp(name, "OEM_FIXED_FONT");   /* a bitmap font's glyph index is its cmap charcode: the reference dump is by character */
        if (truetype) CHECK_EQ(w, incx);
        w = -1;
        CHECK(GetCharWidthI(dc, g, 1, NULL, &w));               /* NULL indices: consecutive from giFirst */
        CHECK_EQ(w, cellx);
        int three[3] = { -1, -1, -1 };
        uint16_t gs[3] = { g, g, g };
        CHECK(GetCharWidthI(dc, 0, 3, gs, three));
        CHECK(three[0] == cellx && three[1] == cellx && three[2] == cellx);
        int consecutive[2] = { -1, -1 };
        CHECK(GetCharWidthI(dc, g, 2, NULL, consecutive));
        CHECK_EQ(consecutive[0], cellx);
        int32_t abc[3] = { -99, -99, -99 };
        if (GetCharABCWidthsI(dc, 0, 1, &g, abc)) {
            abc_ok++;
            CHECK_EQ(abc[0] + abc[1] + abc[2], cellx);
            CHECK(abc[1] >= 1);
            int32_t abc2[6] = { 0 };
            CHECK(GetCharABCWidthsI(dc, g, 2, NULL, abc2));
            CHECK(abc2[0] == abc[0] && abc2[1] == abc[1] && abc2[2] == abc[2]);
        } else {
            abc_refused++;
            CHECK(strstr(name, "FIXED") == NULL || strcmp(name, "OEM_FIXED_FONT") != 0);   /* the TrueType stock font must answer */
        }
        DeleteDC(dc);
        lines++;
    }
    free(buf);
    fclose(f);
    CHECK_EQ(lines, 49);
    CHECK(abc_ok >= 7);          /* the TrueType stock font's lines */
    CHECK(abc_refused >= 1);     /* the bitmap ("System", "Courier") fonts: ABC is documented for TrueType only */
}

TEST(gpa_char_width_by_index_errors)
{
    HDC dc = CreateCompatibleDC(NULL);
    SelectObject(dc, GetStockObject(10));   /* OEM_FIXED_FONT: Liberation Mono */
    int w[4] = { 5, 5, 5, 5 };
    int32_t abc[12];
    uint16_t g = 36;
    SetLastError(0);
    CHECK(!GetCharWidthI(dc, 0, 1, &g, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    SetLastError(0);
    CHECK(!GetCharABCWidthsI(dc, 0, 1, &g, NULL));
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(GetCharWidthI(dc, 0, 0, NULL, w));            /* nothing asked */
    CHECK(w[0] == 5);
    CHECK(GetCharABCWidthsI(dc, 0, 0, NULL, abc));
    /* an index the face does not have: zero widths, success */
    uint16_t beyond = 0xFFFF;
    CHECK(GetCharWidthI(dc, 0, 1, &beyond, w));
    CHECK_EQ(w[0], 0);
    CHECK(GetCharABCWidthsI(dc, 0xFFFF, 1, NULL, abc));
    CHECK(abc[0] == 0 && abc[1] == 0 && abc[2] == 0);
    /* invalid DCs */
    SetLastError(0);
    CHECK(!GetCharWidthI(NULL, 0, 1, &g, w));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    SetLastError(0);
    CHECK(!GetCharABCWidthsI((HDC)(uintptr_t)0x7ee4, 0, 1, &g, abc));
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
    /* a bitmap font: no ABC widths, plain widths work */
    SelectObject(dc, GetStockObject(13));               /* SYSTEM_FONT */
    CHECK(!GetCharABCWidthsI(dc, 0, 1, &g, abc));
    CHECK(GetCharWidthI(dc, 0, 1, &g, w));
    DeleteDC(dc);
}

TEST(gpa_font_unicode_ranges)
{
    HDC dc = CreateCompatibleDC(NULL);
    SelectObject(dc, GetStockObject(10));               /* OEM_FIXED_FONT: Liberation Mono */
    DWORD size = GetFontUnicodeRanges(dc, NULL);
    CHECK(size > 16 && (size - 16) % 4 == 0);
    std::vector<uint8_t> buf(size + 16, 0xEE);
    CHECK_EQ(GetFontUnicodeRanges(dc, buf.data()), size);
    uint32_t hdr[4];
    memcpy(hdr, buf.data(), 16);
    CHECK_EQ(hdr[0], size);
    CHECK_EQ(hdr[1], 0);                                 /* flAccel */
    CHECK_EQ(hdr[3], (size - 16) / 4);
    CHECK(buf[size] == 0xEE);                            /* nothing past the size */
    /* the same walk with FreeType on the face file */
    std::string path = std::string(getenv("FNVNX_FONT_HOST") ? getenv("FNVNX_FONT_HOST") : "") + "/truetype/liberation/LiberationMono-Regular.ttf";
    FT_Library lib;
    REQUIRE(FT_Init_FreeType(&lib) == 0);
    FT_Face face = NULL;
    if (FT_New_Face(lib, path.c_str(), 0, &face)) { FT_Done_FreeType(lib); REQUIRE(!"FT_New_Face"); }
    if (FT_Select_Charmap(face, FT_ENCODING_UNICODE)) { FT_Done_Face(face); FT_Done_FreeType(lib); REQUIRE(!"FT_Select_Charmap"); }
    std::vector<std::pair<unsigned, unsigned>> want;
    unsigned total = 0;
    FT_UInt gi = 0;
    FT_ULong c = FT_Get_First_Char(face, &gi), prev = 0;
    while (gi && c <= 0xFFFF) {
        if (want.empty() || c != prev + 1) want.push_back({ (unsigned)c, 1 }); else want.back().second++;
        total++;
        prev = c;
        c = FT_Get_Next_Char(face, c, &gi);
    }
    FT_Done_Face(face);
    FT_Done_FreeType(lib);
    CHECK_EQ(hdr[2], total);
    CHECK_EQ(hdr[3], want.size());
    REQUIRE(want.size() > 10);
    if (hdr[3] == want.size()) {
        uint32_t sum = 0;
        for (size_t i = 0; i < want.size(); i++) {
            uint16_t lo, n;
            memcpy(&lo, &buf[16 + 4 * i], 2);
            memcpy(&n, &buf[18 + 4 * i], 2);
            CHECK(lo == want[i].first && n == want[i].second);
            sum += n;
            if (i) CHECK(want[i].first > want[i - 1].first + want[i - 1].second);   /* ascending, runs are maximal */
        }
        CHECK_EQ(sum, hdr[2]);
    }
    CHECK(want[0].first == 0x20 || want[0].first < 0x21);   /* the basic Latin run starts at the front */
    /* a bitmap font has no Unicode cmap: the empty set */
    SelectObject(dc, GetStockObject(13));
    DWORD s2 = GetFontUnicodeRanges(dc, NULL);
    REQUIRE(s2 >= 16);
    std::vector<uint8_t> small(s2, 0xEE);                /* the buffer is the size the NULL query returned (the call has no count) */
    CHECK_EQ(GetFontUnicodeRanges(dc, small.data()), s2);
    uint32_t h2[4];
    memcpy(h2, small.data(), 16);
    CHECK(h2[0] == s2 && h2[1] == 0 && h2[3] == (s2 - 16) / 4 && h2[2] <= 0xFFFF);
    DeleteDC(dc);
    SetLastError(0);
    CHECK_EQ(GetFontUnicodeRanges(NULL, NULL), 0);
    CHECK_EQ(GetLastError(), ERROR_INVALID_HANDLE);
}

/* ---- group 4: gdi32 D3DKMTOpenAdapterFromHdc / D3DKMTEscape (natural-layout D3DKMT structures) */
extern "C" {
int D3DKMTOpenAdapterFromHdc(void *);
int D3DKMTEscape(const void *);
HDC GetDC(HWND);
int ReleaseDC(HWND, HDC);
HDC CreateDCA(LPCSTR, LPCSTR, LPCSTR, const void *);
}
namespace {
struct KmtOpen { HDC hDc; uint32_t hAdapter; uint32_t luid_low; int32_t luid_high; uint32_t vidpn; };
struct KmtEsc { uint32_t hAdapter, hDevice, Type, Flags; void *data; uint32_t size, hContext; };
const int ST_OK = 0, ST_INVALID = (int)0xC000000D, ST_NO_MEMORY = (int)0xC0000017;
}

TEST(gpa_d3dkmt)
{
    CHECK(export_of("gdi32", "D3DKMTOpenAdapterFromHdc") == (void *)&D3DKMTOpenAdapterFromHdc);
    CHECK(export_of("gdi32", "D3DKMTEscape") == (void *)&D3DKMTEscape);
    CHECK(export_of("gdi32", "D3DKMTCloseAdapter") == NULL);   /* only the names the game's DLLs take */
    /* open: every kind of display DC works, a memory DC and garbage do not */
    HDC scr = GetDC(NULL);
    KmtOpen o = { scr, 0xdead, 0xdead, 0xdead, 0xdead };
    CHECK_EQ(D3DKMTOpenAdapterFromHdc(&o), ST_OK);
    CHECK(o.hAdapter != 0 && o.hAdapter != 0xdead);
    CHECK_EQ(o.vidpn, 0);
    uint32_t h1 = o.hAdapter, low = o.luid_low;
    int32_t high = o.luid_high;
    HDC disp = CreateDCA("DISPLAY", NULL, NULL, NULL);
    KmtOpen o2 = { disp, 0, 0, 0, 7 };
    CHECK_EQ(D3DKMTOpenAdapterFromHdc(&o2), ST_OK);
    CHECK(o2.hAdapter == h1 && o2.luid_low == low && o2.luid_high == high && o2.vidpn == 0);   /* one adapter */
    DeleteDC(disp);
    ReleaseDC(NULL, scr);
    HDC mem = CreateCompatibleDC(NULL);
    KmtOpen bad = { mem, 0x55, 0x55, 0x55, 0x55 };
    CHECK_EQ(D3DKMTOpenAdapterFromHdc(&bad), ST_INVALID);
    CHECK(bad.hAdapter == 0x55);                                  /* untouched on failure */
    DeleteDC(mem);
    bad.hDc = NULL;
    CHECK_EQ(D3DKMTOpenAdapterFromHdc(&bad), ST_INVALID);
    bad.hDc = (HDC)(uintptr_t)0x7ee4;
    CHECK_EQ(D3DKMTOpenAdapterFromHdc(&bad), ST_INVALID);
    CHECK_EQ(D3DKMTOpenAdapterFromHdc(NULL), ST_INVALID);
    /* the handle is a real handle of the table: it is not an event, and a closed one is replaced */
    HANDLE ev = CreateEventA(NULL, TRUE, FALSE, NULL);
    KmtEsc e = { (uint32_t)(uintptr_t)ev, 0, 0, 0, NULL, 0, 0 };
    CHECK_EQ(D3DKMTEscape(&e), ST_INVALID);
    CloseHandle(ev);
    /* escape: NULL, bad handles, 0 */
    CHECK_EQ(D3DKMTEscape(NULL), ST_INVALID);
    e.hAdapter = 0;
    CHECK_EQ(D3DKMTEscape(&e), ST_INVALID);
    e.hAdapter = 0x7ee4;
    CHECK_EQ(D3DKMTEscape(&e), ST_INVALID);
    /* a valid adapter and every escape type: the display driver has no escape entry (Wine 9.0 parity: STATUS_NO_MEMORY) */
    char priv[16] = { 0 };
    for (uint32_t type = 0; type < 6; type++) {
        KmtEsc ok = { h1, 0, type, 0, priv, sizeof priv, 0 };
        CHECK_EQ(D3DKMTEscape(&ok), ST_NO_MEMORY);
    }
    KmtEsc none = { h1, 0, 0, 0, NULL, 0, 0 };
    CHECK_EQ(D3DKMTEscape(&none), ST_NO_MEMORY);
    SetLastError(1234);
    CHECK_EQ(D3DKMTEscape(&none), ST_NO_MEMORY);
    CHECK_EQ(GetLastError(), 1234);                               /* NTSTATUS API: GetLastError untouched */
    CHECK(CloseHandle((HANDLE)(uintptr_t)h1));
    CHECK_EQ(D3DKMTEscape(&none), ST_INVALID);                    /* closed handle */
    KmtOpen again = { GetDC(NULL), 0, 0, 0, 0 };
    CHECK_EQ(D3DKMTOpenAdapterFromHdc(&again), ST_OK);
    CHECK(again.hAdapter != 0);
    KmtEsc fresh = { again.hAdapter, 0, 0, 0, NULL, 0, 0 };
    CHECK_EQ(D3DKMTEscape(&fresh), ST_NO_MEMORY);
}
