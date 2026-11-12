/* test_msgqueue.cpp - per-thread message queues, input, hooks, cursor and lifecycle (platform/win32/msgqueue.cpp) against MSDN. */
#include "s4_test.h"

#include <stdint.h>
#include <thread>
#include <vector>

extern "C" {
void fnvnx_win_key(unsigned vk, unsigned scan, int down, unsigned flags);
void fnvnx_win_mouse(int x, int y, unsigned changed, unsigned state, int wheel);
void fnvnx_app_focus(int);
void fnvnx_app_suspend(void);
void fnvnx_app_resume(void);
void fnvnx_app_exit_request(void);
void fnvnx_app_display_change(int, int);
HCURSOR SetCursor(HCURSOR);
BOOL SetDoubleClickTime(UINT);
}
#define WM_SYSKEYDOWN 0x104
#define WM_SYSKEYUP 0x105
#define WM_LBUTTONDOWN 0x201
#define WM_LBUTTONUP 0x202
#define WM_LBUTTONDBLCLK 0x203
#define WM_RBUTTONDOWN 0x204
#define WM_MBUTTONDOWN 0x207
#define WM_MOUSEWHEEL 0x20A
#define MINT(n) ((LPCSTR)(uintptr_t)(n))
#define LP(x, y) ((LPARAM)(((unsigned)(x) & 0xFFFF) | ((unsigned)(y) << 16)))

struct L { HWND h; UINT m; WPARAM w; LPARAM l; DWORD tid; };
static L g_l[512];
static int g_n;
static LRESULT lproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    int i = __atomic_fetch_add(&g_n, 1, __ATOMIC_SEQ_CST);
    if (i < 512) g_l[i] = { h, m, w, l, GetCurrentThreadId() };
    if (m == 0x500) return (LRESULT)(w + l);
    return DefWindowProcA(h, m, w, l);
}
static bool seqs(std::vector<UINT> want, HWND h = NULL)
{
    std::vector<UINT> got;
    for (int i = 0; i < g_n && i < 512; i++) if (!h || g_l[i].h == h) got.push_back(g_l[i].m);
    if (got == want) return true;
    fprintf(stderr, "  sequence mismatch, got:");
    for (UINT m : got) fprintf(stderr, " %x", m);
    fprintf(stderr, "\n");
    return false;
}
static int has(HWND h, UINT m) { int n = 0; for (int i = 0; i < g_n && i < 512; i++) n += g_l[i].h == h && g_l[i].m == m; return n; }
static ATOM regc(const char *n, UINT style = 0, HBRUSH bg = NULL, HCURSOR cur = NULL)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = lproc; wc.lpszClassName = n; wc.style = style; wc.hbrBackground = bg; wc.hCursor = cur;
    return RegisterClassA(&wc);
}
static HWND mk(const char *cls, int x, int y, int w, int h, DWORD style = 0x90000000u)
{
    HWND r = CreateWindowExA(0, cls, "t", style, x, y, w, h, NULL, NULL, NULL, NULL);
    if (r) SetForegroundWindow(r);
    return r;
}
static bool get(MSG &m, HWND f = NULL, UINT lo = 0, UINT hi = 0, UINT fl = PM_REMOVE) { return PeekMessageA(&m, f, lo, hi, fl); }
static void pump(void)   /* everything retrievable, dispatched (WM_PAINT validates) */
{
    MSG m;
    while (get(m)) DispatchMessageA(&m);
}
static void release_keys(void)
{
    for (int vk = 1; vk < 255; vk++) { fnvnx_set_key_state(vk, 0); GetAsyncKeyState(vk); }
    if (GetKeyState(0x14) & 1) { fnvnx_set_key_state(0x14, 1); fnvnx_set_key_state(0x14, 0); }
    if (GetKeyState(0x90) & 1) { fnvnx_set_key_state(0x90, 1); fnvnx_set_key_state(0x90, 0); }
}
static void key(unsigned vk, int down, unsigned scan = 0, unsigned fl = 0) { fnvnx_win_key(vk, scan, down, fl); }

TEST(mq_priority_and_filters)
{
    regc("MqP");
    HWND a = mk("MqP", 100, 100, 300, 200), b = mk("MqP", 500, 100, 100, 100);
    HWND bb = a;
    pump();
    MSG m;
    SetWindowPos(a, NULL, 0, 0, 320, 210, 0x2 | 0x4 | 0x10);   /* sized: the update region is not empty */
    PostQuitMessage(5);
    key(0x41, 1, 0x1E);                                        /* input for the active window (b is the last one made) */
    HWND act = GetForegroundWindow();
    PostMessageA(a, 0x500, 1, 2);
    PostMessageA(NULL, 0x600, 3, 4);                           /* a thread message */
    CHECK(get(m, NULL, 0, 0, PM_NOREMOVE)); CHECK_EQ(m.message, 0x500);   /* posted first, in order */
    CHECK(get(m)); CHECK(m.hwnd == a); CHECK_EQ(m.wParam, 1); CHECK_EQ(m.lParam, 2);
    CHECK(get(m)); CHECK_EQ(m.message, 0x600); CHECK(m.hwnd == NULL);
    CHECK(get(m)); CHECK_EQ(m.message, WM_KEYDOWN); CHECK(m.hwnd == act);   /* then input */
    CHECK(get(m)); CHECK_EQ(m.message, WM_QUIT); CHECK_EQ(m.wParam, 5);      /* then quit: a flag, once */
    CHECK(get(m)); CHECK_EQ(m.message, 0x0F); CHECK(m.hwnd == a);           /* then paint */
    CHECK(get(m)); CHECK_EQ(m.message, 0x0F);                                /* not removed until validated */
    DispatchMessageA(&m);
    CHECK(!get(m));
    /* filters */
    PostMessageA(a, 0x501, 0, 0); PostMessageA(b, 0x502, 0, 0); PostMessageA(NULL, 0x503, 0, 0);
    SetForegroundWindow(a);
    key(0x42, 1, 0x30);
    CHECK(!get(m, (HWND)(intptr_t)-1, 0x501, 0x502));            /* thread messages only */
    CHECK(get(m, (HWND)(intptr_t)-1)); CHECK_EQ(m.message, 0x503);
    CHECK(get(m, b)); CHECK_EQ(m.message, 0x502);
    CHECK(!get(m, b));                                           /* not a's */
    CHECK(get(m, NULL, 0, 0, PM_REMOVE | (0x0407u << 16))); CHECK_EQ(m.message, WM_KEYDOWN);   /* PM_QS_INPUT */
    CHECK(!get(m, NULL, 0, 0, PM_REMOVE | (0x0407u << 16)));
    CHECK(get(m, NULL, 0, 0, PM_NOREMOVE | (0x0098u << 16))); CHECK_EQ(m.message, 0x501);   /* PM_QS_POSTMESSAGE */
    CHECK(!get(m, NULL, 0, 0, PM_REMOVE | (0x0020u << 16)));     /* PM_QS_PAINT: nothing painted */
    CHECK(get(m, a, 0x501, 0x501)); CHECK(!get(m));
    CHECK(!get(m, NULL, 9, 3)); CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(!get(m, (HWND)0x12344)); CHECK_EQ(GetLastError(), 1400);
    /* MSG.time and MSG.pt at posting */
    SetCursorPos(10, 20);
    PostMessageA(a, 0x504, 0, 0);
    CHECK(get(m)); CHECK_EQ(m.pt.x, 10); CHECK_EQ(m.pt.y, 20);
    CHECK(GetTickCount() - m.time < 200);
    DestroyWindow(a); DestroyWindow(b);
    (void)bb;
    pump();
}

static int g_tp;
static void CALLBACK_(HWND, UINT, WPARAM w, DWORD) { g_tp = (int)w; }
TEST(mq_send_post_dispatch)
{
    regc("MqS");
    HWND a = mk("MqS", 0, 0, 50, 50), b = mk("MqS", 60, 0, 50, 50);
    pump();
    CHECK_EQ(SendMessageA(a, 0x500, 3, 4), 7);                   /* direct call, result returned */
    CHECK_EQ(SendMessageA((HWND)0x12344, 0x500, 3, 4), 0); CHECK_EQ(GetLastError(), 1400);
    CHECK_EQ(SendMessageA(NULL, 0x500, 3, 4), 0); CHECK_EQ(GetLastError(), 1400);
    g_n = 0;
    SendMessageA((HWND)0xFFFF, 0x505, 0, 0);                     /* HWND_BROADCAST */
    CHECK(seqs({ 0x505 }, a)); CHECK(seqs({ 0x505 }, b));
    CHECK(!PostMessageA((HWND)0x12344, 0x500, 0, 0)); CHECK_EQ(GetLastError(), 1400);
    CHECK(PostMessageA(NULL, 0x506, 0, 0));
    MSG m;
    CHECK(get(m)); CHECK(m.hwnd == NULL); CHECK_EQ(DispatchMessageA(&m), 0);   /* a thread message is not dispatched */
    g_n = 0;
    CHECK(PostMessageA((HWND)0xFFFF, 0x507, 0, 0));
    pump();
    CHECK(seqs({ 0x507 }, a)); CHECK(seqs({ 0x507 }, b));
    MSG t;
    memset(&t, 0, sizeof t);
    t.hwnd = a; t.message = 0x113; t.wParam = 9; t.lParam = (LPARAM)(uintptr_t)&CALLBACK_;
    g_n = 0;
    CHECK_EQ(DispatchMessageA(&t), 0); CHECK_EQ(g_tp, 9); CHECK(seqs({}));   /* WM_TIMER with a TIMERPROC */
    t.message = 0x500; t.lParam = 5; t.wParam = 1;
    CHECK_EQ(DispatchMessageA(&t), 6);
    DestroyWindow(a); DestroyWindow(b);
}

static HWND g_wa, g_wb;
static volatile int g_bready, g_bstop, g_bdone, g_got503;
static DWORD g_bproc_tid;
static LRESULT bproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (m == 0x500) { g_bproc_tid = GetCurrentThreadId(); return (LRESULT)(w * 10 + l); }
    if (m == 0x501) return SendMessageA(g_wa, 0x502, 0, 0) + 1;      /* mutual send while the sender waits */
    if (m == 0x503) { g_got503 = (int)w; return 0; }
    return lproc(h, m, w, l);
}
static LRESULT aproc(HWND h, UINT m, WPARAM w, LPARAM l) { return m == 0x502 ? 77 : lproc(h, m, w, l); }
static bool wait_for(volatile int &v, int want = 1)
{
    DWORD t0 = GetTickCount();
    while (v != want && GetTickCount() - t0 < 2000) { MSG m; while (get(m)) DispatchMessageA(&m); Sleep(1); }
    return v == want;
}
TEST(mq_cross_thread)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = bproc; wc.lpszClassName = "MqB";
    RegisterClassA(&wc);
    wc.lpfnWndProc = aproc; wc.lpszClassName = "MqA";
    RegisterClassA(&wc);
    g_wa = mk("MqA", 0, 0, 40, 40);
    g_bready = g_bstop = g_bdone = g_got503 = 0;
    test_thread th([] {
        g_wb = CreateWindowExA(0, "MqB", "b", 0x90000000u, 50, 0, 40, 40, NULL, NULL, NULL, NULL);
        g_bready = 1;
        DWORD t0 = GetTickCount();
        while (!g_bstop && GetTickCount() - t0 < 4000) { MSG m; while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE)) DispatchMessageA(&m); Sleep(1); }
        DestroyWindow(g_wb);   /* activates g_wa: a send to the main thread, which must keep pumping */
        g_bdone = 1;
    });
    CHECK(wait_for(g_bready));
    DWORD me = GetCurrentThreadId();
    CHECK_EQ(SendMessageA(g_wb, 0x500, 3, 4), 34);                   /* the receiver's thread ran the procedure, the sender got the reply */
    CHECK(g_bproc_tid != 0 && g_bproc_tid != me);
    CHECK_EQ(SendMessageA(g_wb, 0x501, 0, 0), 78);                   /* both threads send to each other: no deadlock */
    CHECK(PostMessageA(g_wb, 0x503, 42, 0));
    DWORD t0 = GetTickCount();
    while (!g_got503 && GetTickCount() - t0 < 2000) Sleep(1);
    CHECK_EQ(g_got503, 42);
    /* lifecycle from a third thread: the sends cross to both windows' threads */
    g_n = 0;
    volatile int done = 0;
    test_thread lc([&] { fnvnx_app_suspend(); done = 1; });
    CHECK(wait_for(done));
    lc.join();
    CHECK(seqs({ 0x218 }, g_wa));
    for (int i = 0; i < g_n; i++) if (g_l[i].h == g_wa) { CHECK_EQ(g_l[i].w, 4); CHECK_EQ(g_l[i].tid, me); }
    g_bstop = 1;
    CHECK(wait_for(g_bdone));
    th.join();
    DestroyWindow(g_wa);
}

TEST(mq_key_messages)
{
    regc("MqK");
    HWND a = mk("MqK", 0, 0, 100, 100);
    pump(); release_keys();
    MSG m;
    key(0x41, 1, 0x1E);
    CHECK(get(m)); CHECK_EQ(m.message, WM_KEYDOWN); CHECK_EQ(m.wParam, 0x41); CHECK_EQ((unsigned)m.lParam, 0x001E0001u); CHECK(m.hwnd == a);
    key(0x41, 1, 0x1E);                                              /* auto-repeat: previous state */
    CHECK(get(m)); CHECK_EQ((unsigned)m.lParam, 0x401E0001u);
    key(0x41, 0, 0x1E);
    CHECK(get(m)); CHECK_EQ(m.message, WM_KEYUP); CHECK_EQ((unsigned)m.lParam, 0xC01E0001u);
    key('Q', 1, 0);                                                  /* the scan code of the US layout when none is given */
    CHECK(get(m)); CHECK_EQ((unsigned)m.lParam, 0x00100001u); key('Q', 0); get(m);
    key(0x27, 1, 0x4D, 1);                                           /* extended: bit 24 */
    CHECK(get(m)); CHECK_EQ((unsigned)m.lParam, 0x014D0001u); key(0x27, 0, 0x4D, 1); get(m);
    key(0x12, 1, 0x38);                                              /* Alt: WM_SYSKEYDOWN, context code */
    CHECK(get(m)); CHECK_EQ(m.message, WM_SYSKEYDOWN); CHECK_EQ(m.wParam, 0x12); CHECK_EQ((unsigned)m.lParam, 0x20380001u);
    key(0x41, 1, 0x1E);
    CHECK(get(m)); CHECK_EQ(m.message, WM_SYSKEYDOWN); CHECK_EQ((unsigned)m.lParam, 0x201E0001u);
    key(0x41, 0, 0x1E);
    CHECK(get(m)); CHECK_EQ(m.message, WM_SYSKEYUP);
    key(0x12, 0, 0x38);
    CHECK(get(m)); CHECK_EQ(m.message, WM_SYSKEYUP); CHECK_EQ((unsigned)m.lParam, 0xC0380001u);
    key(0x79, 1); CHECK(get(m)); CHECK_EQ(m.message, WM_SYSKEYDOWN); CHECK_EQ((unsigned)m.lParam, 0x00440001u);   /* F10 */
    key(0x79, 0); get(m);
    key(0x11, 1, 0x1D, 1);                                           /* right Ctrl */
    CHECK(get(m)); CHECK_EQ(m.wParam, 0x11); CHECK_EQ((unsigned)m.lParam, 0x011D0001u);
    CHECK(GetAsyncKeyState(0xA3) & 0x8000); CHECK(GetAsyncKeyState(0x11) & 0x8000); CHECK(!(GetAsyncKeyState(0xA2) & 0x8000));
    key(0xA3, 0, 0, 1); get(m);
    CHECK(!(GetAsyncKeyState(0x11) & 0x8000));
    CHECK_EQ(GetAsyncKeyState(256), 0); CHECK_EQ(GetAsyncKeyState(0x4141), 0);
    /* SendInput: scan-code and unicode events */
    uint8_t in[28];
    memset(in, 0, sizeof in);
    uint16_t sc = 0x1E; uint32_t fl = 8;                             /* KEYEVENTF_SCANCODE */
    memcpy(in + 6, &sc, 2); memcpy(in + 8, &fl, 4);
    uint32_t ty = 1; memcpy(in, &ty, 4);
    CHECK_EQ(SendInput(1, in, 28), 1);
    CHECK(get(m)); CHECK_EQ(m.message, WM_KEYDOWN); CHECK_EQ(m.wParam, 0x41);
    fl = 8 | 2; memcpy(in + 8, &fl, 4); SendInput(1, in, 28); get(m);
    uint16_t ch = 0x20AC; fl = 4; sc = ch; memcpy(in + 6, &sc, 2); memcpy(in + 8, &fl, 4);
    CHECK_EQ(SendInput(1, in, 28), 1);
    CHECK(get(m)); CHECK_EQ(m.message, WM_KEYDOWN); CHECK_EQ(m.wParam & 0xFFFF, 0xE7); CHECK_EQ(m.wParam >> 16, 0x20AC);
    CHECK(TranslateMessage(&m)); CHECK(get(m)); CHECK_EQ(m.message, WM_CHAR); CHECK_EQ(m.wParam, 0x80);   /* cp1252 */
    uint16_t vk = 0x41; memcpy(in + 4, &vk, 2); SendInput(1, in, 28);   /* VK with UNICODE: invalid */
    CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    uint8_t two[3][28]; memset(two, 0, sizeof two);
    uint32_t kb = 1, hw = 2; uint16_t k2 = 0x42;
    memcpy(two[0], &kb, 4); memcpy(two[0] + 4, &k2, 2); memcpy(two[1], &hw, 4); memcpy(two[2], &kb, 4); memcpy(two[2] + 4, &k2, 2);
    CHECK_EQ(SendInput(3, two, 28), 2);                              /* INPUT_HARDWARE is not inserted */
    CHECK_EQ(SendInput(1, two, 20), 0); CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    pump(); release_keys();
    DestroyWindow(a);
}

TEST(mq_translate_message)
{
    regc("MqT");
    HWND a = mk("MqT", 0, 0, 100, 100);
    pump(); release_keys();
    struct C { unsigned vk; unsigned st; int ch; };   /* st: 1 shift, 2 ctrl, 4 caps, 8 numlock */
    static const C tab[] = { { 'A', 0, 'a' }, { 'A', 1, 'A' }, { 'A', 4, 'A' }, { 'A', 5, 'a' }, { 'Z', 2, 26 }, { 'A', 2, 1 }, { 'M', 3, 13 },
        { '1', 0, '1' }, { '1', 1, '!' }, { '2', 1, '@' }, { '0', 1, ')' }, { '1', 4, '1' }, { 0xBA, 0, ';' }, { 0xBA, 1, ':' }, { 0xBD, 1, '_' },
        { 0xC0, 1, '~' }, { 0xDE, 1, '"' }, { 0xDB, 2, 27 }, { 13, 0, 13 }, { 8, 0, 8 }, { 9, 0, 9 }, { 0x1B, 0, 27 }, { 0x20, 0, 32 },
        { 0x65, 8, '5' }, { 0x65, 0, -1 }, { 0x6E, 8, '.' }, { 0x6B, 0, '+' }, { 0x27, 0, -1 }, { 0x70, 0, -1 }, { '5', 2, -1 } };
    for (const C &c : tab) {
        release_keys();
        if (c.st & 1) fnvnx_set_key_state(0x10, 1);
        if (c.st & 2) fnvnx_set_key_state(0x11, 1);
        if (c.st & 4) { fnvnx_set_key_state(0x14, 1); fnvnx_set_key_state(0x14, 0); }
        if (c.st & 8) { fnvnx_set_key_state(0x90, 1); fnvnx_set_key_state(0x90, 0); }
        MSG k, r;
        memset(&k, 0, sizeof k);
        k.hwnd = a; k.message = WM_KEYDOWN; k.wParam = c.vk; k.lParam = 0x00123451;
        CHECK(TranslateMessage(&k));
        if (c.ch < 0) CHECK(!get(r));
        else { CHECK(get(r)); CHECK_EQ(r.message, WM_CHAR); CHECK_EQ(r.wParam, c.ch); CHECK_EQ(r.lParam, 0x00123451); CHECK(r.hwnd == a); }
        if (s4_failures) fprintf(stderr, "  (vk %x state %x)\n", c.vk, c.st);
    }
    release_keys();
    MSG k, r;
    memset(&k, 0, sizeof k);
    k.hwnd = a; k.message = WM_SYSKEYDOWN; k.wParam = 'A';
    CHECK(TranslateMessage(&k)); CHECK(get(r)); CHECK_EQ(r.message, 0x106); CHECK_EQ(r.wParam, 'a');
    k.message = WM_KEYUP; CHECK(TranslateMessage(&k)); CHECK(!get(r));          /* TRUE for key messages, no character on release */
    k.message = WM_CHAR; CHECK(!TranslateMessage(&k));
    k.message = WM_KEYDOWN; k.wParam = 0xE7 | (0xE9u << 16); TranslateMessage(&k); CHECK(get(r)); CHECK_EQ(r.wParam, 0xE9);
    k.wParam = 0xE7 | (0x4E2Du << 16); TranslateMessage(&k); CHECK(get(r)); CHECK_EQ(r.wParam, '?');
    release_keys();
    DestroyWindow(a);
}

TEST(mq_mouse)
{
    regc("MqM"); regc("MqD", 8 /* CS_DBLCLKS */);
    HWND a = mk("MqM", 100, 100, 300, 200), d = mk("MqD", 500, 100, 100, 100);
    pump(); release_keys();
    MSG m;
    SetDoubleClickTime(500);
    fnvnx_win_mouse(150, 130, 0, 0, 0);
    CHECK(get(m)); CHECK_EQ(m.message, WM_MOUSEMOVE); CHECK_EQ(m.wParam, 0); CHECK_EQ(m.lParam, LP(50, 30)); CHECK(m.hwnd == a);
    POINT p; GetCursorPos(&p); CHECK_EQ(p.x, 150); CHECK_EQ(p.y, 130);
    fnvnx_win_mouse(150, 130, 1, 1, 0);
    CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONDOWN); CHECK_EQ(m.wParam, 1); CHECK_EQ(m.lParam, LP(50, 30));
    CHECK((unsigned short)GetAsyncKeyState(1) == 0x8001u);
    fnvnx_set_key_state(0x10, 1);
    fnvnx_win_mouse(150, 130, 1, 0, 0);
    CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONUP); CHECK_EQ(m.wParam, 4); CHECK(!(GetAsyncKeyState(1) & 0x8000));   /* MK_SHIFT only */
    fnvnx_set_key_state(0x10, 0);
    fnvnx_win_mouse(150, 130, 6, 6, 0);
    CHECK(get(m)); CHECK_EQ(m.message, WM_RBUTTONDOWN); CHECK_EQ(m.wParam, 2);
    CHECK(get(m)); CHECK_EQ(m.message, WM_MBUTTONDOWN); CHECK_EQ(m.wParam, 0x12);
    fnvnx_win_mouse(150, 130, 6, 0, 0); get(m); get(m);
    fnvnx_win_mouse(150, 130, 0, 0, 120);
    CHECK(get(m)); CHECK_EQ(m.message, WM_MOUSEWHEEL); CHECK_EQ(m.wParam, 120u << 16); CHECK_EQ(m.lParam, LP(150, 130));   /* screen coordinates, to the active window */
    fnvnx_win_mouse(-5, 99999, 0, 0, 0);
    GetCursorPos(&p); CHECK_EQ(p.x, 0); CHECK_EQ(p.y, GetSystemMetrics(1) - 1);   /* clamped to the desktop */
    CHECK(!get(m));                                                  /* over no window: nothing queued */
    /* double clicks: only CS_DBLCLKS classes, in time and in the rectangle */
    fnvnx_win_mouse(550, 150, 0, 0, 0); get(m);
    fnvnx_win_mouse(550, 150, 1, 1, 0); get(m); fnvnx_win_mouse(550, 150, 1, 0, 0); get(m); fnvnx_win_mouse(551, 150, 1, 1, 0);
    get(m); CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONDBLCLK); CHECK_EQ(m.wParam, 1);
    fnvnx_win_mouse(551, 150, 1, 0, 0); get(m);
    fnvnx_win_mouse(551, 150, 1, 1, 0); CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONDOWN);   /* the third is single */
    fnvnx_win_mouse(551, 150, 1, 0, 0); get(m);
    fnvnx_win_mouse(560, 150, 1, 1, 0); get(m); CHECK_EQ(m.message, 0x200); CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONDOWN);   /* outside the rectangle */
    fnvnx_win_mouse(560, 150, 1, 0, 0); get(m);
    SetDoubleClickTime(50);
    fnvnx_win_mouse(560, 150, 1, 1, 0); get(m); fnvnx_win_mouse(560, 150, 1, 0, 0); get(m); Sleep(120);
    fnvnx_win_mouse(560, 150, 1, 1, 0); CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONDOWN);       /* too late */
    fnvnx_win_mouse(560, 150, 1, 0, 0); get(m);
    SetDoubleClickTime(500);
    fnvnx_win_mouse(150, 130, 0, 0, 0); get(m); get(m);
    fnvnx_win_mouse(150, 130, 1, 1, 0); get(m); fnvnx_win_mouse(150, 130, 1, 0, 0); get(m);
    fnvnx_win_mouse(150, 130, 1, 1, 0); CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONDOWN);       /* a class without CS_DBLCLKS */
    fnvnx_win_mouse(150, 130, 1, 0, 0); get(m);
    /* swapped buttons: logical messages, physical GetAsyncKeyState */
    CHECK_EQ(SwapMouseButton(TRUE), FALSE);
    fnvnx_win_mouse(150, 130, 1, 1, 0);
    CHECK(get(m)); CHECK_EQ(m.message, WM_RBUTTONDOWN); CHECK_EQ(m.wParam, 2);
    CHECK(GetAsyncKeyState(1) & 0x8000); CHECK(!(GetAsyncKeyState(2) & 0x8000));
    fnvnx_win_mouse(150, 130, 1, 0, 0); get(m);
    CHECK_EQ(SwapMouseButton(FALSE), TRUE);
    /* SendInput: absolute and relative moves, buttons, wheel */
    uint8_t in[28];
    memset(in, 0, sizeof in);
    int32_t dx = 32768, dy = 32768; uint32_t fl = 0x8001, ty = 0;
    memcpy(in, &ty, 4); memcpy(in + 4, &dx, 4); memcpy(in + 8, &dy, 4); memcpy(in + 16, &fl, 4);
    CHECK_EQ(SendInput(1, in, 28), 1);
    GetCursorPos(&p); CHECK_EQ(p.x, GetSystemMetrics(0) / 2); CHECK_EQ(p.y, GetSystemMetrics(1) / 2);
    fnvnx_win_mouse(150, 130, 0, 0, 0); pump();
    dx = 5; dy = -3; fl = 1 | 2; memcpy(in + 4, &dx, 4); memcpy(in + 8, &dy, 4); memcpy(in + 16, &fl, 4);   /* relative move and left down */
    CHECK_EQ(SendInput(1, in, 28), 1);
    GetCursorPos(&p); CHECK_EQ(p.x, 155); CHECK_EQ(p.y, 127);
    CHECK(get(m)); CHECK_EQ(m.message, WM_MOUSEMOVE); CHECK(get(m)); CHECK_EQ(m.message, WM_LBUTTONDOWN);
    dx = dy = 0; fl = 4; memcpy(in + 4, &dx, 4); memcpy(in + 16, &fl, 4); SendInput(1, in, 28); get(m);
    int32_t wh = -120; fl = 0x800; memcpy(in + 12, &wh, 4); memcpy(in + 16, &fl, 4);
    CHECK_EQ(SendInput(1, in, 28), 1);
    CHECK(get(m)); CHECK_EQ(m.message, WM_MOUSEWHEEL); CHECK_EQ(m.wParam >> 16, 0xFF88u);
    pump(); release_keys();
    DestroyWindow(a); DestroyWindow(d);
}

static std::vector<int> g_hl;
static HHOOK g_h1, g_h2;
static int g_ret1 = 42;
static LRESULT hp1(int c, WPARAM w, LPARAM l) { g_hl.push_back(1); return g_ret1; }
static LRESULT hp2(int c, WPARAM w, LPARAM l) { g_hl.push_back(2); return CallNextHookEx(g_h2, c, w, l); }
static LRESULT hp_self(int c, WPARAM w, LPARAM l)   /* unhooks itself, then still passes on */
{
    g_hl.push_back(3);
    CHECK(UnhookWindowsHookEx(g_h2));
    return CallNextHookEx(g_h2, c, w, l);
}
static struct { int code; WPARAM w; LPARAM l; } g_last;
static LRESULT hrec(int c, WPARAM w, LPARAM l) { g_last = { c, w, l }; g_hl.push_back(c + 10); return 0; }
static int g_swallow_vk;
static unsigned g_ll_vk, g_ll_scan, g_ll_flags;
static LRESULT ll_kbd(int c, WPARAM w, LPARAM l)
{
    const uint32_t *s = (const uint32_t *)(uintptr_t)l;
    g_ll_vk = s[0]; g_ll_scan = s[1]; g_ll_flags = s[2]; g_hl.push_back((int)w);
    return (int)s[0] == g_swallow_vk;
}
static LRESULT ll_mouse(int c, WPARAM w, LPARAM l)
{
    const int32_t *s = (const int32_t *)(uintptr_t)l;
    g_hl.push_back((int)w);
    return w == WM_MOUSEMOVE && s[0] == 300;
}
static LRESULT cwp(int c, WPARAM w, LPARAM l)
{
    struct S { LPARAM l; WPARAM w; UINT m; HWND h; } *s = (S *)(uintptr_t)l;
    g_hl.push_back((int)(c * 1000 + (int)w * 100 + (int)s->m % 100));
    return 0;
}
static LRESULT cwpr(int c, WPARAM w, LPARAM l)
{
    struct S { LRESULT r; LPARAM l; WPARAM w; UINT m; HWND h; } *s = (S *)(uintptr_t)l;
    g_hl.push_back((int)(c * 1000 + (int)w * 100 + (int)s->m % 100 + (s->r == 3 ? 1000 : 0)));   /* the result is passed too */
    return 0;
}
static LRESULT swallow_key(int c, WPARAM w, LPARAM l) { g_last = { c, w, l }; return w == 'X'; }
static LRESULT edit_msg(int c, WPARAM w, LPARAM l) { ((MSG *)(uintptr_t)l)->wParam = 99; return 0; }
static LRESULT cbt(int c, WPARAM w, LPARAM l) { g_last = { c, w, l }; return 1; }
static LRESULT dbg(int c, WPARAM w, LPARAM l) { g_hl.push_back(900 + (int)w); return 0; }

TEST(mq_hooks)
{
    regc("MqH");
    HINSTANCE mod = (HINSTANCE)(uintptr_t)0x400000;
    CHECK(SetWindowsHookExA(15, hp1, mod, 0) == NULL); CHECK_EQ(GetLastError(), 1426);
    CHECK(SetWindowsHookExA(-2, hp1, mod, 0) == NULL); CHECK_EQ(GetLastError(), 1426);
    CHECK(SetWindowsHookExA(3, NULL, mod, 0) == NULL); CHECK_EQ(GetLastError(), 1427);
    CHECK(SetWindowsHookExA(13, hp1, NULL, GetCurrentThreadId()) == NULL); CHECK_EQ(GetLastError(), 1429);
    CHECK(SetWindowsHookExA(0, hp1, NULL, GetCurrentThreadId()) == NULL); CHECK_EQ(GetLastError(), 1429);
    CHECK(SetWindowsHookExA(2, hp1, NULL, 0) == NULL); CHECK_EQ(GetLastError(), 1428);   /* a global hook needs its module */
    CHECK(!UnhookWindowsHookEx((HHOOK)0x1234)); CHECK_EQ(GetLastError(), 1404);
    for (int id = -1; id <= 14; id++) {                                  /* every documented hook installs and goes */
        HHOOK h = SetWindowsHookExA(id, hp1, mod, (id == 0 || id == 1 || id == 13 || id == 14) ? 0 : GetCurrentThreadId());
        CHECK(h != NULL); CHECK(UnhookWindowsHookEx(h)); CHECK(!UnhookWindowsHookEx(h));
    }
    HWND a = mk("MqH", 0, 0, 100, 100);
    pump(); release_keys();
    MSG m;
    /* chain order: newest first, CallNextHookEx passes on, 0 at the end */
    g_h1 = SetWindowsHookExA(3, hp1, mod, GetCurrentThreadId());
    g_h2 = SetWindowsHookExA(3, hp2, mod, GetCurrentThreadId());
    g_hl.clear();
    PostMessageA(a, 0x500, 0, 0);
    CHECK(get(m)); CHECK((g_hl == std::vector<int>{ 2, 1 }));          /* WH_GETMESSAGE on retrieval */
    CHECK_EQ(CallNextHookEx(g_h2, 0, 0, 0), 42); CHECK_EQ(CallNextHookEx(g_h1, 0, 0, 0), 0);
    CHECK(UnhookWindowsHookEx(g_h1)); CHECK(UnhookWindowsHookEx(g_h2));
    /* a global hook after the thread's own */
    g_h1 = SetWindowsHookExA(3, hp1, mod, 0); g_h2 = SetWindowsHookExA(3, hp2, mod, GetCurrentThreadId());
    g_hl.clear(); PostMessageA(a, 0x500, 0, 0); get(m);
    CHECK((g_hl == std::vector<int>{ 2, 1 }));
    UnhookWindowsHookEx(g_h1); UnhookWindowsHookEx(g_h2);
    /* unhooking inside the procedure */
    g_h1 = SetWindowsHookExA(3, hp1, mod, GetCurrentThreadId()); g_h2 = SetWindowsHookExA(3, hp_self, mod, GetCurrentThreadId());
    g_hl.clear(); PostMessageA(a, 0x500, 0, 0); get(m);
    CHECK((g_hl == std::vector<int>{ 3, 1 }));
    g_hl.clear(); PostMessageA(a, 0x500, 0, 0); get(m);
    CHECK((g_hl == std::vector<int>{ 1 }));
    UnhookWindowsHookEx(g_h1);
    /* WH_GETMESSAGE edits the message; WM_KEYBOARD sees keys with HC_ACTION / HC_NOREMOVE; nonzero discards */
    g_h1 = SetWindowsHookExA(3, edit_msg, mod, GetCurrentThreadId());
    PostMessageA(a, 0x500, 1, 0); CHECK(get(m)); CHECK_EQ(m.wParam, 99);
    UnhookWindowsHookEx(g_h1);
    g_h1 = SetWindowsHookExA(2, swallow_key, mod, GetCurrentThreadId());
    key('Y', 1, 0x15);
    CHECK(get(m, NULL, 0, 0, PM_NOREMOVE)); CHECK_EQ(g_last.code, 3); CHECK_EQ(g_last.w, 'Y');
    CHECK(get(m)); CHECK_EQ(g_last.code, 0); CHECK_EQ((unsigned)g_last.l, 0x00150001u);
    key('X', 1, 0x2D);
    CHECK(!get(m));                                                      /* discarded */
    UnhookWindowsHookEx(g_h1);
    key('Y', 0, 0x15); key('X', 0, 0x2D); pump(); release_keys();
    g_h1 = SetWindowsHookExA(7, hrec, mod, GetCurrentThreadId());
    g_hl.clear(); fnvnx_win_mouse(10, 10, 0, 0, 0); CHECK(get(m)); CHECK_EQ(g_last.w, WM_MOUSEMOVE);
    const int32_t *mh = (const int32_t *)(uintptr_t)g_last.l; CHECK_EQ(mh[0], 10); CHECK_EQ(mh[1], 10);
    UnhookWindowsHookEx(g_h1);
    /* WH_KEYBOARD_LL / WH_MOUSE_LL: at the entry of the hardware queue; nonzero swallows */
    g_h1 = SetWindowsHookExA(13, ll_kbd, NULL, 0);
    g_swallow_vk = 'S'; g_hl.clear();
    key('S', 1, 0x1F);
    CHECK(!get(m)); CHECK(!(GetAsyncKeyState('S') & 0x8000));
    key('D', 1, 0x20);
    CHECK(get(m)); CHECK_EQ(m.wParam, 'D'); CHECK_EQ(g_ll_vk, 'D'); CHECK_EQ(g_ll_scan, 0x20); CHECK_EQ(g_ll_flags, 0);
    CHECK((g_hl == std::vector<int>{ WM_KEYDOWN, WM_KEYDOWN }));
    key('D', 0, 0x20); CHECK(get(m)); CHECK_EQ(g_ll_flags, 0x80u);
    uint8_t in[28]; memset(in, 0, sizeof in); uint32_t ty = 1, fl = 0; uint16_t vk = 'F'; memcpy(in, &ty, 4); memcpy(in + 4, &vk, 2); memcpy(in + 8, &fl, 4);
    SendInput(1, in, 28); CHECK(get(m)); CHECK_EQ(g_ll_flags, 0x10u);   /* LLKHF_INJECTED */
    key(0x12, 1, 0x38); get(m); key('G', 1, 0x22); get(m); CHECK_EQ(g_ll_flags, 0x20u);   /* LLKHF_ALTDOWN */
    UnhookWindowsHookEx(g_h1); pump(); release_keys();
    g_h1 = SetWindowsHookExA(14, ll_mouse, NULL, 0);
    g_hl.clear();
    fnvnx_win_mouse(300, 50, 0, 0, 0);
    POINT p; GetCursorPos(&p); CHECK(p.x != 300);                        /* the move was swallowed */
    fnvnx_win_mouse(20, 20, 1, 1, 0);
    CHECK((g_hl == std::vector<int>{ WM_MOUSEMOVE, WM_MOUSEMOVE, WM_LBUTTONDOWN }));
    fnvnx_win_mouse(20, 20, 1, 0, 0);
    UnhookWindowsHookEx(g_h1); pump(); release_keys();
    /* WH_CALLWNDPROC / RET around SendMessage; WH_DEBUG sees the call first */
    g_h1 = SetWindowsHookExA(4, cwp, mod, GetCurrentThreadId()); g_h2 = SetWindowsHookExA(12, cwpr, mod, GetCurrentThreadId());
    g_hl.clear();
    CHECK_EQ(SendMessageA(a, 0x500, 1, 2), 3);
    CHECK((g_hl == std::vector<int>{ 180, 1180 }));                  /* code 0 (HC_ACTION), w 1 (sent by this thread), message 0x500 */
    UnhookWindowsHookEx(g_h1); UnhookWindowsHookEx(g_h2);
    g_h1 = SetWindowsHookExA(9, dbg, mod, GetCurrentThreadId()); g_h2 = SetWindowsHookExA(4, cwp, mod, GetCurrentThreadId());
    g_hl.clear(); SendMessageA(a, 0x500, 1, 2);
    CHECK((g_hl == std::vector<int>{ 904, 180 }));
    UnhookWindowsHookEx(g_h1); UnhookWindowsHookEx(g_h2);
    /* WH_CBT HCBT_ACTIVATE: a nonzero result refuses the activation */
    HWND b = mk("MqH", 200, 0, 50, 50);
    SetForegroundWindow(a);
    g_h1 = SetWindowsHookExA(5, cbt, mod, GetCurrentThreadId());
    SetForegroundWindow(b);
    CHECK_EQ(g_last.code, 5); CHECK(g_last.w == (WPARAM)(uintptr_t)b);
    CHECK(GetForegroundWindow() == a);
    UnhookWindowsHookEx(g_h1);
    SetForegroundWindow(b); CHECK(GetForegroundWindow() == b);
    DestroyWindow(a); DestroyWindow(b);
    pump();
}

TEST(mq_lifecycle)
{
    regc("MqL");
    HWND a = mk("MqL", 0, 0, 100, 100), b = mk("MqL", 200, 0, 50, 50);
    SetForegroundWindow(a);
    pump(); release_keys();
    DWORD me = GetCurrentThreadId();
    g_n = 0;
    fnvnx_app_focus(0);
    CHECK(seqs({ 0x86, 0x06, 0x1C, 0x08 }, a));
    CHECK(seqs({ 0x1C }, b));
    for (int i = 0; i < g_n; i++) {
        if (g_l[i].m == 0x86 || g_l[i].m == 0x06) CHECK_EQ(g_l[i].w, 0);
        if (g_l[i].m == 0x1C) { CHECK_EQ(g_l[i].w, 0); CHECK_EQ(g_l[i].l, me); }
    }
    CHECK(GetForegroundWindow() == NULL);
    key('A', 1, 0x1E); MSG m; CHECK(!get(m)); key('A', 0, 0x1E);          /* no active window: the keys go nowhere */
    g_n = 0; fnvnx_app_focus(0); CHECK_EQ(g_n, 0);                        /* already out of focus */
    fnvnx_app_suspend();
    CHECK(seqs({ 0x218 }, a)); CHECK(seqs({ 0x218 }, b)); CHECK_EQ(g_l[0].w, 4);
    g_n = 0;
    fnvnx_app_resume();
    CHECK(seqs({ 0x218, 0x218, 0x1C, 0x86, 0x06, 0x07 }, a));   /* gaining: WM_ACTIVATEAPP first; DefWindowProc takes the focus in WM_ACTIVATE */
    CHECK(GetForegroundWindow() == a);
    int k = 0;
    for (int i = 0; i < g_n; i++) if (g_l[i].h == a) { static const int w[] = { 0x12, 7, 1, 1, 1, 0 }; CHECK_EQ(g_l[i].w, w[k++]); }   /* the power events, then the focus return */
    g_n = 0; fnvnx_app_focus(1); CHECK_EQ(g_n, 0);
    g_n = 0;
    fnvnx_app_display_change(1920, 1080);
    CHECK(seqs({ 0x7E }, a)); CHECK(seqs({ 0x7E }, b));
    CHECK_EQ(g_l[0].w, 32); CHECK_EQ(g_l[0].l, LP(1920, 1080)); CHECK_EQ(GetSystemMetrics(0), 1920); CHECK_EQ(GetSystemMetrics(1), 1080);
    fnvnx_app_display_change(1280, 720);
    DestroyWindow(b);
    pump(); g_n = 0;
    fnvnx_app_exit_request();
    CHECK(seqs({}));                                                      /* posted, not sent */
    CHECK(get(m)); CHECK_EQ(m.message, WM_CLOSE); CHECK(m.hwnd == a);
    DispatchMessageA(&m);
    CHECK(!IsWindow(a));
    CHECK(has(a, 0x02) == 1 && has(a, 0x82) == 1);                         /* WM_DESTROY, WM_NCDESTROY */
}

TEST(mq_defwindowproc)
{
    HCURSOR cur = LoadCursorA(NULL, MINT(32513));
    regc("MqN"); regc("MqBrush", 0, (HBRUSH)6, cur);
    HWND a = mk("MqN", 100, 100, 400, 300, 0x90CF0000u), br = mk("MqBrush", 600, 0, 50, 50);
    pump();
    CHECK_EQ(DefWindowProcA(a, 0x14, 0, 0), 0);                           /* WM_ERASEBKGND: no class brush */
    CHECK_EQ(DefWindowProcA(br, 0x14, 0, 0), 1);
    CHECK_EQ(DefWindowProcA(a, 0x218, 4, 0), 1);
    RECT wr, cr; POINT o = { 0, 0 };
    GetWindowRect(a, &wr); GetClientRect(a, &cr); ClientToScreen(a, &o);
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(o.x + 5, o.y + 5)), 1);       /* HTCLIENT */
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.left - 5, wr.top)), 0);    /* HTNOWHERE */
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.left + 100, wr.top + 10)), 2);   /* HTCAPTION */
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.left + 1, wr.top + 150)), 10);   /* HTLEFT */
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.right - 1, wr.top + 150)), 11);
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.left + 100, wr.top)), 12);
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.left + 100, wr.bottom - 1)), 15);
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.left, wr.top)), 13);
    CHECK_EQ(DefWindowProcA(a, 0x84, 0, LP(wr.right - 1, wr.bottom - 1)), 17);
    HWND bd = mk("MqN", 0, 400, 100, 100, 0x90800000u);                   /* WS_BORDER: not sizable */
    GetWindowRect(bd, &wr);
    CHECK_EQ(DefWindowProcA(bd, 0x84, 0, LP(wr.left, wr.top + 50)), 18);
    DestroyWindow(bd);
    /* WM_SETCURSOR: the class cursor in the client area, the arrow elsewhere */
    SetCursor(NULL);
    CHECK_EQ(DefWindowProcA(br, 0x20, (WPARAM)(uintptr_t)br, LP(1, 0x200)), 1);
    CHECK(SetCursor(NULL) == cur);
    CHECK_EQ(DefWindowProcA(br, 0x20, (WPARAM)(uintptr_t)br, LP(2, 0x200)), 1);
    CHECK(SetCursor(NULL) == LoadCursorA(NULL, MINT(32512)));
    /* WM_SYSKEYDOWN F4 with Alt, WM_SYSCOMMAND SC_CLOSE, WM_CLOSE */
    g_n = 0;
    SendMessageA(a, WM_SYSKEYDOWN, 0x73, 1u << 29);
    pump();   /* DefWindowProc posts WM_SYSCOMMAND(SC_CLOSE) to the top-level window (ref) */
    CHECK(has(a, WM_SYSKEYDOWN) == 1 && has(a, 0x112) == 1 && has(a, WM_CLOSE) == 1 && has(a, 0x82) == 1); CHECK(!IsWindow(a));
    g_n = 0;
    SendMessageA(br, WM_SYSKEYDOWN, 0x73, 0);
    CHECK(seqs({ WM_SYSKEYDOWN }, br)); CHECK(IsWindow(br));
    CHECK_EQ(SendMessageA(br, 0x112, 0xF060, 0), 0); CHECK(!IsWindow(br));
    CHECK_EQ(DefWindowProcA(NULL, 0x4321, 1, 2), 0);
}

TEST(mq_cursor_and_resources)
{
    test_thread t([] {
        CHECK_EQ(ShowCursor(TRUE), 1); CHECK_EQ(ShowCursor(FALSE), 0); CHECK_EQ(ShowCursor(FALSE), -1);   /* the counter of this thread */
        HCURSOR arrow = LoadCursorA(NULL, MINT(32512)), ibeam = LoadCursorA(NULL, MINT(32513));
        CHECK(SetCursor(ibeam) == arrow); CHECK(SetCursor(NULL) == ibeam);
    });
    t.join();
    static const int cids[] = { 32512, 32513, 32514, 32515, 32516, 32640, 32641, 32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650, 32651 };
    static const int iids[] = { 32512, 32513, 32514, 32515, 32516, 32517, 32518 };
    std::vector<void *> seen;
    for (int id : cids) { void *h = LoadCursorA(NULL, MINT(id)); CHECK(h != NULL); CHECK(h == LoadCursorA(NULL, MINT(id))); for (void *s : seen) CHECK(s != h); seen.push_back(h); }
    for (int id : iids) { void *h = LoadIconA(NULL, MINT(id)); CHECK(h != NULL); CHECK(h == LoadIconA(NULL, MINT(id))); for (void *s : seen) CHECK(s != h); seen.push_back(h); }
    CHECK(LoadCursorA(NULL, MINT(5)) == NULL); CHECK_EQ(GetLastError(), 1814);
    CHECK(LoadCursorA((HINSTANCE)(uintptr_t)0x400000, "cursor") == NULL); CHECK_EQ(GetLastError(), 1812);
    CHECK(!GetCursorPos(NULL)); CHECK_EQ(GetLastError(), 998);
    CHECK(SetCursorPos(100000, -5)); POINT p; GetCursorPos(&p); CHECK_EQ(p.x, GetSystemMetrics(0) - 1); CHECK_EQ(p.y, 0);
    /* ClipCursor: the cursor stays in the rectangle (right/bottom exclusive), moves into it at once, NULL lifts it */
    RECT cr = { 100, 100, 200, 150 }, got;
    CHECK(ClipCursor(&cr)); CHECK(GetClipCursor(&got)); CHECK(got.left == 100 && got.top == 100 && got.right == 200 && got.bottom == 150);
    GetCursorPos(&p); CHECK(p.x >= 100 && p.x < 200 && p.y >= 100 && p.y < 150);
    SetCursorPos(0, 1000); GetCursorPos(&p); CHECK_EQ(p.x, 100); CHECK_EQ(p.y, 149);
    RECT empty = { 10, 10, 10, 20 };
    CHECK(!ClipCursor(&empty)); CHECK_EQ(GetLastError(), ERROR_INVALID_PARAMETER);
    CHECK(GetClipCursor(&got)); CHECK_EQ(got.left, 100);                       /* the failed call changed nothing */
    CHECK(ClipCursor(NULL)); CHECK(GetClipCursor(&got)); CHECK(got.left == 0 && got.top == 0 && got.right == GetSystemMetrics(0));
    SetCursorPos(5, 5); GetCursorPos(&p); CHECK_EQ(p.x, 5);
    CHECK(!GetClipCursor(NULL)); CHECK_EQ(GetLastError(), 998);
    CHECK_EQ(GetDoubleClickTime(), 500);
    CHECK(SetDoubleClickTime(1000)); CHECK_EQ(GetDoubleClickTime(), 1000);
    CHECK(SetDoubleClickTime(0)); CHECK_EQ(GetDoubleClickTime(), 500);
    CHECK(SetDoubleClickTime(9999)); CHECK_EQ(GetDoubleClickTime(), 5000);
    SetDoubleClickTime(500);
    CHECK(fnvnx_win32_lookup_export("user32", "SetCursor") == (void *)&SetCursor);
}
