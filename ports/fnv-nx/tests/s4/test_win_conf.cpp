/* test_win_conf.cpp - conformance of the time, window/message and input Win32 APIs with documented Windows behaviour. */
#include "s4_test.h"
#include "../../platform/win32/w32.h"

#include <stdint.h>

static uint64_t ft64(const FILETIME &f) { return ((uint64_t)(f.dwHighDateTime & 0xffffffffu) << 32) | (f.dwLowDateTime & 0xffffffffu); }
static FILETIME mkft(uint64_t v)
{
    FILETIME f;
    f.dwLowDateTime = (DWORD)(v & 0xffffffffu);
    f.dwHighDateTime = (DWORD)(v >> 32);
    return f;
}
static SYSTEMTIME mkst(int y, int mo, int d, int h = 0, int mi = 0, int s = 0, int ms = 0)
{
    SYSTEMTIME st;
    memset(&st, 0, sizeof st);
    st.wYear = (WORD)y; st.wMonth = (WORD)mo; st.wDay = (WORD)d;
    st.wHour = (WORD)h; st.wMinute = (WORD)mi; st.wSecond = (WORD)s; st.wMilliseconds = (WORD)ms;
    return st;
}

extern "C" int GetClassNameA(HWND, LPSTR, int);

/* ---- time */
TEST(wc_qpc_frequency_and_monotonic)
{
    LARGE_INTEGER f1, f2, prev, cur;
    CHECK(QueryPerformanceFrequency(&f1));
    CHECK(f1.QuadPart > 0);
    QueryPerformanceCounter(&prev);
    for (int i = 0; i < 100000; i++) {
        CHECK_EQ(QueryPerformanceCounter(&cur), TRUE);
        if (cur.QuadPart < prev.QuadPart) {
            CHECK(0);
            break;
        }
        prev = cur;
    }
    CHECK(QueryPerformanceFrequency(&f2));
    CHECK_EQ(f1.QuadPart, f2.QuadPart);   /* constant for the process lifetime */
    CHECK(!QueryPerformanceCounter(NULL));
    CHECK(!QueryPerformanceFrequency(NULL));
}

TEST(wc_clocks_agree_across_sleep)
{
    LARGE_INTEGER f, a, b;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&a);
    DWORD t0 = GetTickCount(), m0 = timeGetTime();
    Sleep(60);
    QueryPerformanceCounter(&b);
    DWORD t1 = GetTickCount(), m1 = timeGetTime();
    double qpc_ms = (double)(b.QuadPart - a.QuadPart) * 1000.0 / (double)f.QuadPart;
    CHECK(t1 - t0 >= 55);
    CHECK(m1 - m0 >= 55);
    CHECK(qpc_ms >= 55);
    CHECK(qpc_ms - (double)(t1 - t0) < 20 && (double)(t1 - t0) - qpc_ms < 20);
    CHECK(GetTickCount() <= 0xffffffffu && timeGetTime() <= 0xffffffffu);   /* a 32-bit value */
    CHECK_EQ(timeBeginPeriod(1), 0);
    CHECK_EQ(timeEndPeriod(1), 0);
}

TEST(wc_filetime_epoch_and_units)
{
    SYSTEMTIME st;
    FILETIME ft = mkft(0);   /* 1601-01-01 00:00:00, a Monday */
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wYear, 1601); CHECK_EQ(st.wMonth, 1); CHECK_EQ(st.wDay, 1);
    CHECK_EQ(st.wDayOfWeek, 1); CHECK_EQ(st.wHour, 0); CHECK_EQ(st.wMilliseconds, 0);
    ft = mkft(10000);        /* 100 ns units: 10000 of them are 1 ms */
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wSecond, 0); CHECK_EQ(st.wMilliseconds, 1);
    ft = mkft(10000000ull * 3661);   /* 1 h 1 min 1 s */
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wHour, 1); CHECK_EQ(st.wMinute, 1); CHECK_EQ(st.wSecond, 1);
    SYSTEMTIME unix0 = mkst(1970, 1, 1);
    CHECK(SystemTimeToFileTime(&unix0, &ft));
    CHECK(ft64(ft) == 116444736000000000ull);
    ft = mkft(0x8000000000000000ull);   /* beyond the range FileTimeToSystemTime accepts */
    CHECK(!FileTimeToSystemTime(&ft, &st));
    ft = mkft(0x7FFFFFFFFFFFFFFFull);
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wYear, 30828);
    SYSTEMTIME before = mkst(1600, 12, 31);
    CHECK(!SystemTimeToFileTime(&before, &ft));
}

TEST(wc_leap_years_and_weekdays)
{
    FILETIME ft, ft2;
    SYSTEMTIME st;
    SYSTEMTIME d = mkst(2000, 2, 29);
    CHECK(SystemTimeToFileTime(&d, &ft));          /* divisible by 400: leap */
    d = mkst(2000, 3, 1);
    CHECK(SystemTimeToFileTime(&d, &ft2));
    CHECK(ft64(ft2) - ft64(ft) == 86400ull * 10000000ull);
    d = mkst(1900, 2, 29); CHECK(!SystemTimeToFileTime(&d, &ft));   /* divisible by 100, not 400 */
    d = mkst(2100, 2, 29); CHECK(!SystemTimeToFileTime(&d, &ft));
    d = mkst(2012, 2, 29); CHECK(SystemTimeToFileTime(&d, &ft));
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wDayOfWeek, 3);                    /* Wednesday */
    d = mkst(2011, 2, 29); CHECK(!SystemTimeToFileTime(&d, &ft));
    d = mkst(2010, 4, 31); CHECK(!SystemTimeToFileTime(&d, &ft));
    d = mkst(2010, 12, 31); CHECK(SystemTimeToFileTime(&d, &ft));
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wDayOfWeek, 5);                    /* Friday */
    d = mkst(2000, 1, 1); CHECK(SystemTimeToFileTime(&d, &ft));
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wDayOfWeek, 6);                    /* Saturday */
    d = mkst(2024, 2, 29, 23, 59, 59, 999); CHECK(SystemTimeToFileTime(&d, &ft));
    CHECK(FileTimeToSystemTime(&ft, &st));
    CHECK_EQ(st.wDay, 29); CHECK_EQ(st.wMilliseconds, 999); CHECK_EQ(st.wDayOfWeek, 4);
    d = mkst(2010, 13, 1); CHECK(!SystemTimeToFileTime(&d, &ft));
    d = mkst(2010, 1, 1, 24); CHECK(!SystemTimeToFileTime(&d, &ft));
}

TEST(wc_local_time_and_zones)
{
    SYSTEMTIME sys, loc;
    FILETIME sft, lft;
    GetSystemTimeAsFileTime(&sft);
    CHECK(FileTimeToLocalFileTime(&sft, &lft));
    GetLocalTime(&loc);
    GetSystemTime(&sys);
    TIME_ZONE_INFORMATION tz;
    CHECK(GetTimeZoneInformation(&tz) != 0xFFFFFFFFu);
    int64_t bias_s = (int64_t)tz.Bias * 60;
    CHECK_EQ((int64_t)(ft64(lft) - ft64(sft)), -bias_s * 10000000ll);
    SYSTEMTIME lst;
    FILETIME l2;
    CHECK(SystemTimeToFileTime(&loc, &l2));
    CHECK(SystemTimeToFileTime(&sys, &lft));
    int64_t d = ((int64_t)ft64(l2) - (int64_t)ft64(lft)) / 10000000 + bias_s;
    CHECK(d >= -2 && d <= 2);   /* the two reads are a moment apart */
    CHECK(SystemTimeToTzSpecificLocalTime(NULL, &sys, &lst));
    CHECK(SystemTimeToFileTime(&lst, &l2));
    d = ((int64_t)ft64(l2) - (int64_t)ft64(lft)) / 10000000 + bias_s;
    CHECK_EQ(d, 0);

    /* a fixed zone: US Eastern, daylight rules second Sunday of March / first Sunday of November at 02:00 */
    memset(&tz, 0, sizeof tz);
    tz.Bias = 300;
    tz.StandardDate = mkst(0, 11, 1, 2); tz.StandardDate.wDayOfWeek = 0;
    tz.DaylightDate = mkst(0, 3, 2, 2); tz.DaylightDate.wDayOfWeek = 0;
    tz.DaylightBias = -60;
    SYSTEMTIME u, o;
    u = mkst(2010, 1, 1, 0, 0, 0);
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wYear, 2009); CHECK_EQ(o.wMonth, 12); CHECK_EQ(o.wDay, 31); CHECK_EQ(o.wHour, 19);
    u = mkst(2010, 7, 1, 12, 0, 0);
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wHour, 8);                                   /* EDT */
    u = mkst(2010, 3, 14, 6, 59, 0);                        /* 01:59 EST, one minute before the change */
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wHour, 1); CHECK_EQ(o.wMinute, 59);
    u = mkst(2010, 3, 14, 7, 0, 0);                         /* 03:00 EDT */
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wHour, 3); CHECK_EQ(o.wMinute, 0);
    u = mkst(2010, 11, 7, 5, 59, 0);                        /* 01:59 EDT */
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wHour, 1); CHECK_EQ(o.wMinute, 59);
    u = mkst(2010, 11, 7, 6, 0, 0);                         /* 01:00 EST again */
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wHour, 1); CHECK_EQ(o.wMinute, 0);
    /* southern hemisphere: DST from October to April (Bias -600, AEST) */
    memset(&tz, 0, sizeof tz);
    tz.Bias = -600;
    tz.StandardDate = mkst(0, 4, 1, 3); tz.StandardDate.wDayOfWeek = 0;
    tz.DaylightDate = mkst(0, 10, 1, 2); tz.DaylightDate.wDayOfWeek = 0;
    tz.DaylightBias = -60;
    u = mkst(2010, 1, 15, 0, 0, 0);
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wHour, 11);                                  /* AEDT */
    u = mkst(2010, 7, 15, 0, 0, 0);
    CHECK(SystemTimeToTzSpecificLocalTime(&tz, &u, &o));
    CHECK_EQ(o.wHour, 10);
    SYSTEMTIME bad = mkst(2011, 2, 30);
    CHECK(!SystemTimeToTzSpecificLocalTime(NULL, &bad, &o));
    CHECK_EQ(GetTimeZoneInformation(NULL), 0xFFFFFFFFu);
}

TEST(wc_compare_filetime)
{
    FILETIME a = mkft(0x100000000ull), b = mkft(0xFFFFFFFFull), c = mkft(0x100000000ull);
    CHECK_EQ(CompareFileTime(&a, &b), 1);       /* the high dword decides first */
    CHECK_EQ(CompareFileTime(&b, &a), -1);
    CHECK_EQ(CompareFileTime(&a, &c), 0);
}

/* ---- windows and messages */
static int g_log[64], g_nlog;
static HWND g_log_hwnd[64];
static int g_create_result = 1, g_nccreate_result = 1;
static int g_in_create_valid, g_create_style;
static int g_destroy_valid;
static LRESULT wc_proc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (g_nlog < 64) { g_log_hwnd[g_nlog] = h; g_log[g_nlog++] = (int)m; }
    switch (m) {
    case 0x0081:   /* WM_NCCREATE */
        if (!g_nccreate_result) return 0;
        break;
    case 0x0001: { /* WM_CREATE */
        g_in_create_valid = IsWindow(h);
        struct cs { void *p; void *i; void *m; void *par; int cy, cx, y, x; LONG style; } *c = (struct cs *)(uintptr_t)l;
        g_create_style = c ? (int)c->style : -1;
        if (!g_create_result) return (LRESULT)-1;
        return 0;
    }
    case WM_DESTROY: case 0x0082:
        g_destroy_valid = IsWindow(h);
        break;
    case 0x0400: return (LRESULT)(w + 1);
    }
    return DefWindowProcA(h, m, w, l);
}
static bool logged(int m) { for (int i = 0; i < g_nlog; i++) if (g_log[i] == m) return true; return false; }
static int log_index(int m) { for (int i = 0; i < g_nlog; i++) if (g_log[i] == m) return i; return -1; }

static void drain(void)
{
    MSG m;
    while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) {}
}

TEST(wc_window_lifecycle_messages)
{
    drain();
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = wc_proc;
    wc.lpszClassName = "WinConfClass";
    ATOM atom = RegisterClassA(&wc);
    CHECK(atom != 0);
    CHECK_EQ(RegisterClassA(&wc), 0);                 /* the same name twice */
    CHECK_EQ(GetLastError(), 1410);                   /* ERROR_CLASS_ALREADY_EXISTS */
    CHECK(CreateWindowExA(0, "NoSuchClassAnywhere", "x", 0, 0, 0, 10, 10, NULL, NULL, NULL, NULL) == NULL);
    CHECK_EQ(GetLastError(), 1407);                   /* ERROR_CANNOT_FIND_WND_CLASS */

    g_nlog = 0;
    g_in_create_valid = 0;
    HWND h = CreateWindowExA(0, "winconfclass", "Title", 0x80000000u /* WS_POPUP */, 0, 0, 640, 480, NULL, NULL, NULL, NULL);
    CHECK(h != NULL);
    CHECK(log_index(0x0081) >= 0 && log_index(0x0081) < log_index(0x0001));   /* NCCREATE before CREATE */
    CHECK(g_in_create_valid);                          /* the HWND is already usable in WM_CREATE */
    CHECK_EQ(g_create_style, (int)0x80000000u);
    CHECK(g_log_hwnd[0] == h);
    char buf[64];
    CHECK_EQ(GetClassNameA(h, buf, sizeof buf), 12);
    CHECK_STR(buf, "WinConfClass");
    CHECK(FindWindowA("WINCONFCLASS", NULL) == h);
    CHECK(FindWindowA(NULL, "Title") == h);
    CHECK(FindWindowA((LPCSTR)(uintptr_t)atom, NULL) == h);      /* MSDN: the class may be given as an atom */

    HWND h2 = CreateWindowExA(0, (LPCSTR)(uintptr_t)atom, "viaatom", 0, 0, 0, 1, 1, NULL, NULL, NULL, NULL);
    CHECK(h2 != NULL);
    CHECK_STR((GetClassNameA(h2, buf, sizeof buf), buf), "WinConfClass");
    CHECK(DestroyWindow(h2));

    /* ShowWindow: previous visibility; the first show activates */
    g_nlog = 0;
    CHECK_EQ(ShowWindow(h, 5 /* SW_SHOW */), 0);
    CHECK(logged(0x0018) && logged(WM_ACTIVATEAPP) && logged(WM_ACTIVATE) && logged(WM_SETFOCUS));
    CHECK(UpdateWindow(h));
    g_nlog = 0;
    CHECK_EQ(ShowWindow(h, 5), 1);
    CHECK_EQ(g_nlog, 0);                              /* nothing new */
    CHECK_EQ(ShowWindow(h, 0 /* SW_HIDE */), 1);
    CHECK_EQ(ShowWindow(h, 0), 0);
    CHECK(!ShowWindow(NULL, 5));

    CHECK_EQ(SendMessageA(h, 0x400, 41, 0), 42);
    CHECK_EQ(SendMessageA(NULL, 0x400, 41, 0), 0);

    /* destruction: WM_DESTROY, WM_NCDESTROY with the window still valid, queued messages dropped */
    PostMessageA(h, 0x401, 0, 0);
    g_nlog = 0;
    CHECK(DestroyWindow(h));
    CHECK(log_index(WM_DESTROY) >= 0 && log_index(WM_DESTROY) < log_index(0x0082));
    CHECK(g_destroy_valid);
    CHECK(!IsWindow(h));
    CHECK(!DestroyWindow(h));
    MSG m;
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    CHECK(!PostMessageA(h, 0x401, 0, 0));             /* gone: an invalid window handle */
    CHECK_EQ(GetLastError(), 1400);

    /* creation vetoed by WM_NCCREATE / WM_CREATE */
    g_nccreate_result = 0;
    CHECK(CreateWindowExA(0, "WinConfClass", "t", 0, 0, 0, 1, 1, NULL, NULL, NULL, NULL) == NULL);
    g_nccreate_result = 1;
    g_create_result = 0;
    CHECK(CreateWindowExA(0, "WinConfClass", "t", 0, 0, 0, 1, 1, NULL, NULL, NULL, NULL) == NULL);
    g_create_result = 1;
    CHECK(fnvnx_main_window() == NULL);               /* no window leaked */
}

static LRESULT plain_proc(HWND h, UINT m, WPARAM w, LPARAM l) { return DefWindowProcA(h, m, w, l); }

TEST(wc_message_queue_semantics)
{
    drain();
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = plain_proc;
    wc.lpszClassName = "WinConfQueue";
    CHECK(RegisterClassA(&wc) != 0);
    HWND a = CreateWindowExA(0, "WinConfQueue", "a", 0, 0, 0, 1, 1, NULL, NULL, NULL, NULL);
    HWND b = CreateWindowExA(0, "WinConfQueue", "b", 0, 0, 0, 1, 1, NULL, NULL, NULL, NULL);
    CHECK(a && b && a != b);
    MSG m;
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));
    CHECK(!PeekMessageA(&m, a, 0, 0, PM_NOREMOVE));
    PostMessageA(a, 0x500, 1, 10);
    PostMessageA(b, 0x501, 2, 20);
    PostMessageA(a, 0x502, 3, 30);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE));   /* leaves it queued */
    CHECK_EQ(m.message, 0x500);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE));
    CHECK_EQ(m.message, 0x500);
    CHECK_EQ(m.wParam, 1); CHECK_EQ(m.lParam, 10); CHECK(m.hwnd == a);
    CHECK(PeekMessageA(&m, b, 0, 0, PM_REMOVE));         /* the window filter skips a's messages */
    CHECK_EQ(m.message, 0x501); CHECK(m.hwnd == b);
    CHECK(!PeekMessageA(&m, b, 0, 0, PM_REMOVE));
    CHECK(PeekMessageA(&m, a, 0x502, 0x502, PM_REMOVE));   /* the range filter */
    CHECK_EQ(m.message, 0x502);
    CHECK(PeekMessageA(&m, a, 0, 0, PM_REMOVE));
    CHECK_EQ(m.message, 0x500);
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));      /* FALSE when empty */

    /* PostQuitMessage: WM_QUIT with the exit code, after everything else, once, never for a window filter */
    PostMessageA(a, 0x503, 0, 0);
    PostQuitMessage(7);
    PostMessageA(a, 0x504, 0, 0);
    CHECK(!PeekMessageA(&m, a, WM_QUIT, WM_QUIT, PM_REMOVE));
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)); CHECK_EQ(m.message, 0x503);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)); CHECK_EQ(m.message, 0x504);
    CHECK(!PeekMessageA(&m, a, 0, 0, PM_REMOVE));
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE));
    CHECK_EQ(m.message, WM_QUIT); CHECK_EQ(m.wParam, 7); CHECK(m.hwnd == NULL);
    CHECK(PeekMessageA(&m, NULL, WM_QUIT, WM_QUIT, PM_REMOVE));
    CHECK_EQ(m.message, WM_QUIT);
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_REMOVE));      /* delivered once */

    /* DispatchMessage calls the class procedure, returns its result; a thread message does nothing */
    MSG d;
    memset(&d, 0, sizeof d);
    d.hwnd = a; d.message = WM_CLOSE;
    CHECK_EQ(DispatchMessageA(&d), 0);
    CHECK(!IsWindow(a));                                  /* DefWindowProc(WM_CLOSE) destroyed it */
    d.hwnd = NULL; d.message = 0x600;
    CHECK_EQ(DispatchMessageA(&d), 0);
    CHECK(!TranslateMessage(&d));                         /* not a key message */
    CHECK(DestroyWindow(b));
}

TEST(wc_window_longs_and_metrics)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = plain_proc;
    wc.lpszClassName = "WinConfLong";
    CHECK(RegisterClassA(&wc) != 0);
    HWND h = CreateWindowExA(0x8 /* topmost */, "WinConfLong", "l", 0x80000000u, 0, 0, 100, 50, NULL, NULL, NULL, NULL);
    CHECK(h != NULL);
    CHECK_EQ(SetWindowLongA(h, GWL_USERDATA, 11), 0);       /* returns the previous value */
    CHECK_EQ(SetWindowLongA(h, GWL_USERDATA, 22), 11);
    CHECK_EQ(GetWindowLongA(h, GWL_USERDATA), 22);
    CHECK_EQ(GetWindowLongA(h, GWL_EXSTYLE), 8);
    CHECK_EQ((unsigned)GetWindowLongA(h, GWL_STYLE), 0x80000000u);
    if (sizeof(void *) == 4) {                              /* subclassing needs 32-bit pointers */
        LONG old = SetWindowLongA(h, GWL_WNDPROC, (LONG)(uintptr_t)&wc_proc);
        CHECK(old == (LONG)(uintptr_t)&plain_proc);
        CHECK_EQ(SendMessageA(h, 0x400, 1, 0), 2);
    }
    CHECK_EQ(GetWindowLongA(NULL, GWL_USERDATA), 0);
    fnvnx_set_display_size(1920, 1080);
    RECT r;
    CHECK(GetClientRect(h, &r));
    CHECK_EQ(r.left, 0); CHECK_EQ(r.top, 0); CHECK_EQ(r.right, 100); CHECK_EQ(r.bottom, 50);   /* the window's own size, not the display's */
    CHECK(GetWindowRect(h, &r));
    CHECK_EQ(r.right - r.left, 100);
    CHECK(!GetClientRect(NULL, &r));
    CHECK_EQ(GetSystemMetrics(SM_CXSCREEN), 1920);
    CHECK_EQ(GetSystemMetrics(SM_CYSCREEN), 1080);
    CHECK_EQ(GetSystemMetrics(16), 1920);                   /* SM_CXFULLSCREEN */
    CHECK_EQ(GetSystemMetrics(80), 1);                      /* SM_CMONITORS */
    CHECK_EQ(GetSystemMetrics(43), 3);
    r.left = 10; r.top = 20; r.right = 110; r.bottom = 220;
    CHECK(AdjustWindowRect(&r, 0x80000000u, FALSE));        /* a popup has no frame */
    CHECK_EQ(r.right - r.left, 100); CHECK_EQ(r.bottom - r.top, 200);
    CHECK(GetActiveWindow() == NULL);                       /* a window that was never shown is not active */
    ShowWindow(h, 5);
    CHECK(GetActiveWindow() == h && GetForegroundWindow() == h);
    CHECK(SetForegroundWindow(h));
    CHECK(DestroyWindow(h));
    CHECK(GetActiveWindow() == NULL && GetForegroundWindow() == NULL);
    fnvnx_set_display_size(1280, 720);
}

TEST(wc_cursor_hooks_and_dialogs)
{
    int a = ShowCursor(FALSE);
    CHECK_EQ(ShowCursor(FALSE), a - 1);
    CHECK_EQ(ShowCursor(TRUE), a);                         /* the display counter, new value returned */
    CHECK_EQ(ShowCursor(TRUE), a + 1);
    CHECK_EQ(ShowCursor(FALSE), a);
    POINT p;
    CHECK(SetCursorPos(30, 40));
    CHECK(GetCursorPos(&p));
    CHECK_EQ(p.x, 30); CHECK_EQ(p.y, 40);

    CHECK(SetWindowsHookExA(13, NULL, NULL, 0) == NULL);   /* no procedure */
    HOOKPROC proc = (HOOKPROC)(void *)&GetTickCount;
    HHOOK h1 = SetWindowsHookExA(13, proc, NULL, 0), h2 = SetWindowsHookExA(13, proc, NULL, 0);
    CHECK(h1 && h2 && h1 != h2);
    CHECK_EQ(CallNextHookEx(h1, 0, 0, 0), 0);
    CHECK(UnhookWindowsHookEx(h1));
    CHECK(!UnhookWindowsHookEx(h1));                       /* already gone */
    CHECK(UnhookWindowsHookEx(h2));
    CHECK(!UnhookWindowsHookEx(NULL));

    CHECK_EQ(MessageBoxA(NULL, "t", "c", MB_YESNO), IDYES);
    CHECK_EQ(MessageBoxA(NULL, "t", "c", 3 /* YESNOCANCEL */ | 0x200 /* DEFBUTTON3 */), IDCANCEL);
    CHECK_EQ(MessageBoxA(NULL, "t", "c", 2 /* ABORTRETRYIGNORE */ | 0x100), IDRETRY);
    CHECK_EQ(MessageBoxA(NULL, "t", "c", 6 /* CANCELTRYCONTINUE */ | 0x200), 11);
}

/* ---- input */
TEST(wc_key_state)
{
    const int VK_F9 = 0x78;
    CHECK_EQ(GetAsyncKeyState(VK_F9), 0);
    fnvnx_set_key_state(VK_F9, 1);
    CHECK_EQ((unsigned short)GetAsyncKeyState(VK_F9), 0x8001u);   /* down, and pressed since the last call */
    CHECK_EQ((unsigned short)GetAsyncKeyState(VK_F9), 0x8000u);   /* still down, no new press */
    CHECK_EQ((unsigned short)GetKeyState(VK_F9), 0x8000u);
    fnvnx_set_key_state(VK_F9, 0);
    CHECK_EQ(GetAsyncKeyState(VK_F9), 0);
    fnvnx_set_key_state(VK_F9, 1);
    fnvnx_set_key_state(VK_F9, 0);
    CHECK_EQ((unsigned short)GetAsyncKeyState(VK_F9), 0x0001u);   /* a tap between two calls is still reported */
    CHECK_EQ(GetAsyncKeyState(VK_F9), 0);
    CHECK_EQ(GetAsyncKeyState(-1), 0);
    CHECK_EQ(GetAsyncKeyState(256), 0);

    fnvnx_set_key_state(0xA1, 1);                          /* right shift is also Shift */
    CHECK(GetAsyncKeyState(0x10) & 0x8000);
    CHECK(GetKeyState(0x10) & 0x8000);
    fnvnx_set_key_state(0xA0, 1);
    fnvnx_set_key_state(0xA1, 0);
    CHECK(GetAsyncKeyState(0x10) & 0x8000);                // left shift keeps it down
    fnvnx_set_key_state(0xA0, 0);
    CHECK(!(GetAsyncKeyState(0x10) & 0x8000));
    GetAsyncKeyState(0xA0); GetAsyncKeyState(0xA1); GetAsyncKeyState(0x10);

    CHECK_EQ(GetKeyState(0x14) & 1, 0);                    // CapsLock toggles on each press
    fnvnx_set_key_state(0x14, 1); fnvnx_set_key_state(0x14, 0);
    CHECK_EQ(GetKeyState(0x14) & 1, 1);
    fnvnx_set_key_state(0x14, 1); fnvnx_set_key_state(0x14, 0);
    CHECK_EQ(GetKeyState(0x14) & 1, 0);
    GetAsyncKeyState(0x14);

    CHECK_EQ(GetDoubleClickTime(), 500);
    CHECK_EQ(SwapMouseButton(TRUE), FALSE);                // returns the previous setting
    CHECK_EQ(GetSystemMetrics(23), 1);
    CHECK_EQ(SwapMouseButton(FALSE), TRUE);
    CHECK_EQ(SwapMouseButton(FALSE), FALSE);
}

TEST(wc_send_input)
{
    uint8_t in[2][28];
    memset(in, 0, sizeof in);
    uint32_t type = 1;                                     // INPUT_KEYBOARD
    uint16_t vk = 0x7A;                                    // F11
    memcpy(in[0], &type, 4); memcpy(in[0] + 4, &vk, 2);
    memcpy(in[1], &type, 4); memcpy(in[1] + 4, &vk, 2);
    uint32_t up = 2;
    memcpy(in[1] + 12, &up, 4);                            // KEYEVENTF_KEYUP
    CHECK_EQ(SendInput(1, in[0], 28), 1);
    CHECK((unsigned short)GetAsyncKeyState(0x7A) == 0x8001u);
    CHECK_EQ(SendInput(1, in[1], 28), 1);
    CHECK_EQ(GetAsyncKeyState(0x7A), 0);
    uint32_t mouse = 0;
    uint8_t mi[28];
    memset(mi, 0, sizeof mi);
    memcpy(mi, &mouse, 4);
    CHECK_EQ(SendInput(1, mi, 28), 1);                     // accepted, nothing to inject
    CHECK_EQ(SendInput(1, in[0], 12), 0);                  // wrong cbSize
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK_EQ(SendInput(1, NULL, 28), 0);
    fnvnx_set_key_state(0x7A, 0);
    GetAsyncKeyState(0x7A);
}
