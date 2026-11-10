/* test_window_tree.cpp - window objects, tree, geometry and state of platform/win32/window.cpp against MSDN. */
#include "s4_test.h"

#include <initializer_list>
#include <stdint.h>

extern "C" {
HWND GetTopWindow(HWND);
BOOL IsWindowVisible(HWND);
DWORD GetWindowThreadProcessId(HWND, LPDWORD);
BOOL UnregisterClassA(LPCSTR, HINSTANCE);
DWORD SetClassLongA(HWND, int, LONG);
DWORD GetClassLongA(HWND, int);
int GetWindowTextA(HWND, LPSTR, int);
int GetClassNameA(HWND, LPSTR, int);
HWND fnvnx_desktop_window(void);
void fnvnx_window_validate(HWND);
}

#define T_CHILD 0x40000000u
#define T_POPUP 0x80000000u
#define T_VISIBLE 0x10000000u
#define T_BORDER 0x00800000u
#define T_DLGFRAME 0x00400000u
#define T_CAPTION 0x00C00000u
#define T_THICK 0x00040000u
#define T_OVERLAPPEDWINDOW 0x00CF0000u
#define T_MINIMIZE 0x20000000u
#define T_MAXIMIZE 0x01000000u
#define T_TOP ((HWND)0)
#define T_BOTTOM ((HWND)(intptr_t)1)
#define T_TOPMOST ((HWND)(intptr_t)-1)
#define T_NOTOPMOST ((HWND)(intptr_t)-2)
#define T_BOGUS ((HWND)(uintptr_t)0x12344)
#define T_INSTANCE ((HINSTANCE)(uintptr_t)0x400000)

struct t_wpos { HWND hwnd, after; int x, y, cx, cy; UINT flags; };
struct t_log { HWND h; UINT m; WPARAM w; LPARAM l; };
static t_log g_lg[1024];
static int g_nlg;
static int g_veto_nccreate, g_veto_create, g_clamp_cx;

static LRESULT rec_proc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (g_nlg < 1024) g_lg[g_nlg++] = { h, m, w, l };
    if (m == 0x81 && g_veto_nccreate) return 0;
    if (m == 1 && g_veto_create) return (LRESULT)-1;
    if (m == 0x46 && g_clamp_cx) ((t_wpos *)(uintptr_t)l)->cx = g_clamp_cx;   /* WM_WINDOWPOSCHANGING may edit */
    return DefWindowProcA(h, m, w, l);
}
static void reset_log(void) { g_nlg = 0; }
/* the messages one window saw, as ids; h == NULL: every window */
static bool seq(HWND h, std::initializer_list<int> want)
{
    int k = 0;
    bool ok = true;
    for (int i = 0; i < g_nlg; i++)
        if (!h || g_lg[i].h == h) {
            if (k >= (int)want.size() || g_lg[i].m != (UINT)*(want.begin() + k)) ok = false;
            k++;
        }
    if (!ok || k != (int)want.size()) {
        fprintf(stderr, "  sequence mismatch, got:");
        for (int i = 0; i < g_nlg; i++) if (!h || g_lg[i].h == h) fprintf(stderr, " %x", g_lg[i].m);
        fprintf(stderr, "\n");
        return false;
    }
    return true;
}
static int log_pos(HWND h, UINT m) { for (int i = 0; i < g_nlg; i++) if (g_lg[i].h == h && g_lg[i].m == m) return i; return -1; }
static int log_count(HWND h, UINT m) { int n = 0; for (int i = 0; i < g_nlg; i++) if (g_lg[i].h == h && g_lg[i].m == m) n++; return n; }

static ATOM reg(const char *name, int clsx = 0, int wndx = 0, HINSTANCE inst = T_INSTANCE, UINT style = 0)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = rec_proc;
    wc.lpszClassName = name;
    wc.cbClsExtra = clsx;
    wc.cbWndExtra = wndx;
    wc.hInstance = inst;
    wc.style = style;
    return RegisterClassA(&wc);
}
static HWND mk(DWORD ex, const char *cls, DWORD st, int x, int y, int w, int h, HWND parent = NULL, HMENU menu = NULL)
{
    return CreateWindowExA(ex, cls, "t", st, x, y, w, h, parent, menu, T_INSTANCE, NULL);
}
static RECT wrect(HWND h) { RECT r = { -1, -1, -1, -1 }; GetWindowRect(h, &r); return r; }
static RECT crect(HWND h) { RECT r = { -1, -1, -1, -1 }; GetClientRect(h, &r); return r; }
#define CHECK_RECT(r, l_, t_, r_, b_) do { RECT q_ = (r); CHECK_EQ(q_.left, l_); CHECK_EQ(q_.top, t_); CHECK_EQ(q_.right, r_); CHECK_EQ(q_.bottom, b_); } while (0)

TEST(wt_classes)
{
    ATOM a = reg("WtCls", 8, 12);
    CHECK(a >= 0xC000);
    CHECK_EQ(reg("WTCLS", 0, 0), 0);                      /* names are case-insensitive */
    CHECK_EQ(GetLastError(), 1410);
    ATOM b = reg("WtCls2");
    CHECK(b >= 0xC000 && b != a);
    CHECK(reg("WtCls", 0, 0, (HINSTANCE)(uintptr_t)0x500000) != 0);   /* another module: another class */
    WNDCLASSA bad;
    memset(&bad, 0, sizeof bad);
    CHECK_EQ(RegisterClassA(&bad), 0);                    /* no name, no procedure */
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(RegisterClassA(NULL), 0);
    CHECK_EQ(mk(0, "WtNoSuch", 0, 0, 0, 5, 5) != NULL, 0);
    CHECK_EQ(GetLastError(), 1407);

    HWND h = mk(0, "wtcls", T_POPUP, 0, 0, 50, 50);
    CHECK(h != NULL);
    CHECK_EQ(GetClassLongA(h, -32), a);             /* GCW_ATOM */
    CHECK_EQ(GetClassLongA(h, -18), 12);            /* GCL_CBWNDEXTRA */
    CHECK_EQ(GetClassLongA(h, -20), 8);             /* GCL_CBCLSEXTRA */
    CHECK_EQ(GetClassLongA(h, -24), (DWORD)(uintptr_t)rec_proc);
    CHECK_EQ(GetClassLongA(h, -16), (DWORD)(uintptr_t)T_INSTANCE);
    CHECK_EQ(GetClassLongA(h, 0), 0);               /* class extra bytes start zeroed */
    CHECK_EQ(GetClassLongA(h, 4), 0);
    SetLastError(0);
    CHECK_EQ(SetClassLongA(h, 4, 0x1234), 0);
    CHECK_EQ(GetClassLongA(h, 4), 0x1234);
    CHECK_EQ(SetClassLongA(h, 4, 7), 0x1234);             /* the previous value */
    CHECK_EQ(GetClassLongA(h, 8), 0);
    CHECK_EQ(GetLastError(), 1413);                       /* ERROR_INVALID_INDEX */
    CHECK_EQ(SetClassLongA(h, -26, 0x20), 0);             /* GCL_STYLE */
    CHECK_EQ(GetClassLongA(h, -26), 0x20);
    CHECK_EQ(SetClassLongA(h, -10, 5), 0);                /* GCL_HBRBACKGROUND */
    CHECK_EQ(GetClassLongA(h, -10), 5);
    CHECK_EQ(SetClassLongA(h, -12, 6), 0);                /* GCL_HCURSOR */
    CHECK_EQ(GetClassLongA(h, -12), 6);
    CHECK_EQ(SetClassLongA(h, -14, 9), 0);                /* GCL_HICON */
    CHECK_EQ(GetClassLongA(h, -14), 9);
    CHECK_EQ(SetClassLongA(h, -34, 3), 0);                /* GCL_HICONSM */
    CHECK_EQ(GetClassLongA(h, -34), 3);
    CHECK_EQ(GetClassLongA(h, -100), 0);
    CHECK_EQ(GetLastError(), 1413);
    CHECK_EQ(GetClassLongA(T_BOGUS, -32), 0);
    CHECK_EQ(GetLastError(), 1400);
    char buf[64];
    CHECK_EQ(GetClassNameA(h, buf, sizeof buf), 5);
    CHECK_STR(buf, "WtCls");
    CHECK_EQ(GetClassNameA(h, buf, 3), 2);                /* truncated, NUL terminated */
    CHECK_STR(buf, "Wt");

    CHECK(!UnregisterClassA("WtCls", T_INSTANCE));        /* a window exists */
    CHECK_EQ(GetLastError(), 1412);
    CHECK(DestroyWindow(h));
    CHECK(UnregisterClassA("WTCLS", T_INSTANCE));
    CHECK(!UnregisterClassA("WtCls", T_INSTANCE));
    CHECK_EQ(GetLastError(), 1411);
    CHECK(UnregisterClassA((LPCSTR)(uintptr_t)b, T_INSTANCE));   /* by atom */
    CHECK(UnregisterClassA("WtCls", (HINSTANCE)(uintptr_t)0x500000));
    CHECK(reg("WtCls2") != b);                            /* atoms are not reused */
    UnregisterClassA("WtCls2", T_INSTANCE);

    /* the predefined control classes */
    HWND bt = CreateWindowExA(0, "Button", "ok", T_POPUP, 0, 0, 40, 20, NULL, NULL, T_INSTANCE, NULL);
    CHECK(bt != NULL);
    CHECK_EQ(GetClassNameA(bt, buf, sizeof buf), 6);
    CHECK_STR(buf, "Button");
    CHECK(DestroyWindow(bt));
}

TEST(wt_rect_rule)
{
    reg("WtRect");
    static const DWORD styles[] = { 0, T_BORDER, T_DLGFRAME, T_CAPTION, T_THICK, T_THICK | T_CAPTION, T_OVERLAPPEDWINDOW,
                                    T_POPUP, T_POPUP | T_BORDER, T_POPUP | T_CAPTION, T_POPUP | T_THICK,
                                    T_POPUP | T_THICK | T_CAPTION | 0x80000 /* SYSMENU */, T_POPUP | T_DLGFRAME };
    static const DWORD exs[] = { 0, 1, 0x100, 0x200, 0x20000, 0x80, 0x40000, 0x300, 0x20001, 0x301 };
    int cases = 0;
    for (DWORD st : styles)
        for (DWORD ex : exs)
            for (int menu = 0; menu < 2; menu++) {
                HWND h = mk(ex, "WtRect", st, 10, 20, 300, 200, NULL, menu ? (HMENU)(uintptr_t)7 : NULL);
                CHECK(h != NULL);
                if (!h) continue;
                cases++;
                CHECK_RECT(wrect(h), 10, 20, 310, 220);
                RECT c = crect(h);
                CHECK_EQ(c.left, 0); CHECK_EQ(c.top, 0);
                RECT a = c;
                CHECK(AdjustWindowRectEx(&a, st, menu, ex));
                CHECK_EQ(a.right - a.left, 300);          /* the client rect grows back to the window rect */
                CHECK_EQ(a.bottom - a.top, 200);
                POINT p = { 0, 0 };
                CHECK(ClientToScreen(h, &p));
                CHECK_EQ(p.x, 10 - a.left);
                CHECK_EQ(p.y, 20 - a.top);
                CHECK(ScreenToClient(h, &p));
                CHECK_EQ(p.x, 0); CHECK_EQ(p.y, 0);
                if (ex == 0) {
                    RECT b = c;
                    CHECK(AdjustWindowRect(&b, st, menu));
                    CHECK_EQ(b.left, a.left); CHECK_EQ(b.bottom, a.bottom);
                }
                DestroyWindow(h);
            }
    CHECK_EQ(cases, 13 * 10 * 2);
    /* absolute values from the metrics: caption + frame + menu */
    RECT r = { 0, 0, 100, 100 };
    AdjustWindowRect(&r, T_OVERLAPPEDWINDOW, TRUE);
    int fx = GetSystemMetrics(32), fy = GetSystemMetrics(33), cap = GetSystemMetrics(4), mnu = GetSystemMetrics(15);
    CHECK_EQ(r.left, -fx); CHECK_EQ(r.right, 100 + fx);
    CHECK_EQ(r.top, -(fy + cap + mnu)); CHECK_EQ(r.bottom, 100 + fy);
    RECT n = { 5, 6, 105, 106 };
    AdjustWindowRect(&n, T_POPUP, FALSE);                 /* a popup has no frame */
    CHECK_RECT(n, 5, 6, 105, 106);
    CHECK(!AdjustWindowRect(NULL, 0, FALSE));
    CHECK_EQ(GetLastError(), 998);
    /* CW_USEDEFAULT: position 0,0, desktop size */
    HWND d = mk(0, "WtRect", 0, (int)0x80000000, (int)0x80000000, (int)0x80000000, (int)0x80000000);
    CHECK_RECT(wrect(d), 0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
    DestroyWindow(d);
    HWND big = mk(0, "WtRect", T_POPUP, -50, -60, 5000, 4000);   /* not clamped */
    CHECK_RECT(wrect(big), -50, -60, 4950, 3940);
    DestroyWindow(big);
}

TEST(wt_tree_and_geometry)
{
    reg("WtTree");
    HWND p = mk(0, "WtTree", T_POPUP | T_BORDER, 100, 50, 400, 300);
    CHECK(mk(0, "WtTree", T_CHILD, 0, 0, 10, 10, NULL) == NULL);   /* a child needs a parent */
    CHECK_EQ(GetLastError(), 1406);
    CHECK(mk(0, "WtTree", T_CHILD, 0, 0, 10, 10, T_BOGUS) == NULL);
    CHECK_EQ(GetLastError(), 1400);
    HWND c1 = mk(0, "WtTree", T_CHILD | T_VISIBLE, 10, 20, 100, 80, p);
    HWND c2 = mk(0x200, "WtTree", T_CHILD | T_VISIBLE, 30, 40, 100, 80, p);
    HWND g1 = mk(0, "WtTree", T_CHILD, 5, 5, 20, 20, c1);
    CHECK(c1 && c2 && g1);
    int bx = GetSystemMetrics(5);
    /* children are placed relative to the parent's client area (inside its border) */
    CHECK_RECT(wrect(c1), 100 + bx + 10, 50 + bx + 20, 100 + bx + 110, 50 + bx + 100);
    CHECK_RECT(wrect(g1), 100 + bx + 15, 50 + bx + 25, 100 + bx + 35, 50 + bx + 45);
    POINT pt = { 0, 0 };
    CHECK(ClientToScreen(c1, &pt));
    CHECK_EQ(pt.x, 100 + bx + 10); CHECK_EQ(pt.y, 50 + bx + 20);
    /* z-order: the newest child is on top */
    CHECK(GetWindow(p, 5) == c2);                          /* GW_CHILD */
    CHECK(GetTopWindow(p) == c2);
    CHECK(GetWindow(c2, 2) == c1);                         /* GW_HWNDNEXT */
    CHECK(GetWindow(c1, 3) == c2);                         /* GW_HWNDPREV */
    CHECK(GetWindow(c1, 0) == c2 && GetWindow(c2, 1) == c1);   /* FIRST, LAST */
    CHECK(GetWindow(c1, 2) == NULL && GetWindow(c2, 3) == NULL);
    CHECK(GetWindow(c1, 4) == NULL);                       /* GW_OWNER */
    CHECK(GetWindow(c1, 6) == c1);                         /* GW_ENABLEDPOPUP: no popup, the window itself */
    CHECK(GetWindow(c1, 7) == NULL);
    CHECK_EQ(GetLastError(), 1404);
    CHECK(GetWindow(T_BOGUS, 5) == NULL);
    CHECK_EQ(GetLastError(), 1400);
    CHECK(GetTopWindow(NULL) == p);
    CHECK(GetTopWindow(g1) == NULL);

    HWND own = mk(0, "WtTree", T_POPUP, 0, 0, 10, 10, p);   /* owned popup */
    CHECK(GetWindow(own, 4) == p);
    CHECK(GetWindow(p, 6) == own);                          /* its enabled popup */
    CHECK(GetTopWindow(NULL) == own);
    CHECK(GetWindow(own, 0) == own && GetWindow(own, 1) == p);

    /* EnumChildWindows: all descendants, depth first, in z-order */
    struct E { HWND seen[8]; int n; HWND kill; HWND stop; } e = { {}, 0, NULL, NULL };
    static E *pe;
    pe = &e;
    auto cb = [](HWND h, LPARAM) -> BOOL {
        pe->seen[pe->n++] = h;
        if (h == pe->kill) DestroyWindow(h);
        return h != pe->stop;
    };
    CHECK(EnumChildWindows(p, cb, 0));
    CHECK_EQ(e.n, 3);
    CHECK(e.seen[0] == c2 && e.seen[1] == c1 && e.seen[2] == g1);
    e.n = 0; e.stop = c1;
    CHECK(!EnumChildWindows(p, cb, 0));
    CHECK_EQ(e.n, 2);
    e.n = 0; e.stop = NULL; e.kill = c2;                    /* the callback destroys a window of the walk */
    CHECK(EnumChildWindows(p, cb, 0));
    CHECK(!IsWindow(c2));
    CHECK_EQ(e.n, 3);
    CHECK(!EnumChildWindows(T_BOGUS, cb, 0));
    CHECK_EQ(GetLastError(), 1400);
    c2 = NULL;

    /* FindWindowA: top-level windows by class (name or atom) and title */
    CHECK(FindWindowA("WTTREE", NULL) == own);
    CHECK(FindWindowA("WtTree", "t") == own);
    CHECK(FindWindowA(NULL, "nothing") == NULL);
    CHECK(FindWindowA("WtNoClass", NULL) == NULL);
    CHECK_EQ(GetLastError(), 1407);
    ATOM at = (ATOM)GetClassLongA(p, -32);
    CHECK(FindWindowA((LPCSTR)(uintptr_t)at, NULL) == own);
    CHECK(FindWindowA(NULL, NULL) == own);                 /* the top window */

    /* moving a parent moves its descendants */
    CHECK(SetWindowPos(p, 0, 300, 200, 0, 0, 0x1 | 0x4 | 0x10));
    CHECK_RECT(wrect(p), 300, 200, 700, 500);
    CHECK_RECT(wrect(g1), 300 + bx + 15, 200 + bx + 25, 300 + bx + 35, 200 + bx + 45);
    CHECK(DestroyWindow(own));
    CHECK(DestroyWindow(p));
    CHECK(!IsWindow(c1) && !IsWindow(g1));                 /* the children went with it */
    CHECK(GetTopWindow(NULL) == NULL);
}

TEST(wt_creation_and_destruction_sequences)
{
    reg("WtSeq");
    reset_log();
    HWND h = mk(0, "WtSeq", T_POPUP | T_VISIBLE, 10, 20, 200, 100);
    CHECK(h != NULL);
    CHECK(seq(h, { 0x24, 0x81, 0x83, 0x01, 0x18, 0x46, 0x1C, 0x86, 0x06, 0x07, 0x47, 0x05, 0x03 }));
    CHECK(GetActiveWindow() == h && GetForegroundWindow() == h);
    CHECK(IsWindowVisible(h));
    int i = log_pos(h, 5);
    CHECK_EQ(g_lg[i].w, 0);                                 /* SIZE_RESTORED */
    CHECK_EQ(g_lg[i].l, 200 | (100 << 16));
    CHECK_EQ(g_lg[log_pos(h, 3)].l, 10 | (20 << 16));

    reset_log();
    HWND hidden = mk(0, "WtSeq", T_POPUP, 0, 0, 20, 20);
    CHECK(seq(hidden, { 0x24, 0x81, 0x83, 0x01 }));         /* an invisible window shows nothing */
    CHECK(!IsWindowVisible(hidden));
    CHECK(GetActiveWindow() == h);

    HWND ch = mk(0, "WtSeq", T_CHILD | T_VISIBLE, 0, 0, 20, 20, h);
    reset_log();
    DestroyWindow(hidden);
    CHECK(seq(hidden, { 0x02, 0x82 }));
    reset_log();
    CHECK(DestroyWindow(h));                                /* the active window, with a child */
    /* deactivation, hide, WM_DESTROY, the children the same way, WM_NCDESTROY last */
    CHECK(seq(h, { 0x86, 0x06, 0x46, 0x18, 0x47, 0x02, 0x82 }));
    CHECK_EQ(g_lg[log_pos(h, 0x86)].w, 0);                  /* NCACTIVATE(FALSE) */
    CHECK_EQ(g_lg[log_pos(h, 0x06)].w & 0xFFFF, 0);         /* WA_INACTIVE */
    CHECK(log_pos(h, 0x02) < log_pos(ch, 0x02));
    CHECK(log_pos(ch, 0x02) < log_pos(ch, 0x82) && log_pos(ch, 0x82) < log_pos(h, 0x82));
    CHECK(!IsWindow(ch) && !IsWindow(h));
    CHECK(GetActiveWindow() == NULL);

    g_veto_nccreate = 1;
    reset_log();
    CHECK(mk(0, "WtSeq", T_POPUP, 0, 0, 5, 5) == NULL);
    g_veto_nccreate = 0;
    CHECK(log_count(NULL, 0x01) == 0);
    g_veto_create = 1;
    reset_log();
    CHECK(mk(0, "WtSeq", T_POPUP | T_VISIBLE, 0, 0, 5, 5) == NULL);
    g_veto_create = 0;
    CHECK(log_count(g_lg[0].h, 0x02) == 1 && log_count(g_lg[0].h, 0x82) == 1);
    CHECK(GetTopWindow(NULL) == NULL);                      /* nothing leaked */
    CHECK(UnregisterClassA("WtSeq", T_INSTANCE));           /* so no window counts against the class */
}

TEST(wt_setwindowpos_and_show)
{
    reg("WtPos");
    HWND h = mk(0, "WtPos", T_POPUP, 10, 10, 100, 100);
    reset_log();
    CHECK(SetWindowPos(h, 0, 200, 150, 300, 250, 0x4));
    CHECK_RECT(wrect(h), 200, 150, 500, 400);
    CHECK(seq(h, { 0x46, 0x83, 0x47, 0x05, 0x03 }));
    reset_log();
    CHECK(SetWindowPos(h, 0, 0, 0, 120, 130, 0x4 | 0x2));   /* NOMOVE */
    CHECK_RECT(wrect(h), 200, 150, 320, 280);
    CHECK(seq(h, { 0x46, 0x83, 0x47, 0x05 }));
    reset_log();
    CHECK(SetWindowPos(h, 0, 7, 8, 0, 0, 0x4 | 0x1));       /* NOSIZE */
    CHECK_RECT(wrect(h), 7, 8, 127, 138);
    CHECK(seq(h, { 0x46, 0x47, 0x03 }));
    reset_log();
    CHECK(SetWindowPos(h, 0, 9, 9, 9, 9, 0x4 | 0x2 | 0x1 | 0x400));   /* nothing to do, no WM_WINDOWPOSCHANGING */
    CHECK(seq(h, { 0x47 }));
    CHECK_RECT(wrect(h), 7, 8, 127, 138);
    reset_log();
    g_clamp_cx = 60;                                        /* the handler edits the WINDOWPOS */
    SetWindowPos(h, 0, 0, 0, 500, 40, 0x4 | 0x2);
    g_clamp_cx = 0;
    CHECK_RECT(wrect(h), 7, 8, 67, 48);
    SetWindowPos(h, 0, 0, 0, -5, -5, 0x4 | 0x2);            /* negative sizes are empty */
    CHECK_RECT(wrect(h), 7, 8, 7, 8);

    CHECK(!SetWindowPos(T_BOGUS, 0, 0, 0, 0, 0, 0));
    CHECK_EQ(GetLastError(), 1400);
    CHECK(!SetWindowPos(h, T_BOGUS, 0, 0, 0, 0, 3));
    CHECK_EQ(GetLastError(), 1400);

    /* SHOWWINDOW / HIDEWINDOW */
    reset_log();
    CHECK(SetWindowPos(h, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x40 | 0x10));
    CHECK(IsWindowVisible(h));
    CHECK(log_pos(h, 0x18) >= 0 && log_pos(h, 0x18) < log_pos(h, 0x46));
    CHECK(GetActiveWindow() == NULL);                       /* NOACTIVATE */
    CHECK(SetWindowPos(h, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x80));
    CHECK(!IsWindowVisible(h));
    CHECK_EQ((long long)(GetWindowLongA(h, -16) & T_VISIBLE), 0);

    /* z-order among top-level windows */
    HWND a = mk(0, "WtPos", T_POPUP, 0, 0, 10, 10), b = mk(0, "WtPos", T_POPUP, 0, 0, 10, 10), c = mk(0, "WtPos", T_POPUP, 0, 0, 10, 10);
    const UINT keep = 0x1 | 0x2 | 0x10;
    CHECK(GetTopWindow(NULL) == c && GetWindow(c, 2) == b && GetWindow(b, 2) == a && GetWindow(a, 2) == h);
    CHECK(SetWindowPos(a, T_TOP, 0, 0, 0, 0, keep));
    CHECK(GetTopWindow(NULL) == a && GetWindow(a, 2) == c);
    CHECK(SetWindowPos(a, T_BOTTOM, 0, 0, 0, 0, keep));
    CHECK(GetWindow(a, 2) == NULL && GetWindow(a, 3) == h);
    CHECK(SetWindowPos(c, b, 0, 0, 0, 0, keep));            /* below b */
    CHECK(GetTopWindow(NULL) == b && GetWindow(b, 2) == c);
    CHECK(SetWindowPos(a, T_TOPMOST, 0, 0, 0, 0, keep));
    CHECK(GetTopWindow(NULL) == a);
    CHECK((GetWindowLongA(a, -20) & 8) != 0);
    HWND d = mk(0, "WtPos", T_POPUP, 0, 0, 10, 10);         /* a new window stays below the topmost one */
    CHECK(GetTopWindow(NULL) == a && GetWindow(a, 2) == d);
    CHECK(SetWindowPos(d, T_TOP, 0, 0, 0, 0, keep));
    CHECK(GetTopWindow(NULL) == a && GetWindow(a, 2) == d);
    CHECK(SetWindowPos(a, T_NOTOPMOST, 0, 0, 0, 0, keep));
    CHECK(GetTopWindow(NULL) == a && (GetWindowLongA(a, -20) & 8) == 0);
    CHECK(SetWindowPos(a, T_TOP, 0, 0, 0, 0, 0x4 | keep));  /* NOZORDER: no change */
    CHECK(!SetWindowPos(a, (HWND)g_lg, 0, 0, 0, 0, keep));
    HWND kid = mk(0, "WtPos", T_CHILD, 0, 0, 5, 5, a);
    CHECK(!SetWindowPos(kid, b, 0, 0, 0, 0, keep));         /* b is not its sibling */
    CHECK_EQ(GetLastError(), 87);
    DestroyWindow(kid); DestroyWindow(a); DestroyWindow(b); DestroyWindow(c); DestroyWindow(d);

    /* ShowWindow states */
    HWND w = mk(0, "WtPos", T_OVERLAPPEDWINDOW, 50, 60, 400, 300);
    int W = GetSystemMetrics(SM_CXSCREEN), fx = GetSystemMetrics(32);
    CHECK_EQ(ShowWindow(w, 3), 0);                          /* SW_SHOWMAXIMIZED: was hidden */
    CHECK(IsWindowVisible(w) && !IsIconic(w));
    CHECK((GetWindowLongA(w, -16) & T_MAXIMIZE) != 0);
    CHECK_RECT(wrect(w), -fx, -GetSystemMetrics(33), W + fx, GetSystemMetrics(SM_CYSCREEN) + GetSystemMetrics(33));
    CHECK_EQ(crect(w).right, W);
    CHECK(GetActiveWindow() == w);
    reset_log();
    CHECK_EQ(ShowWindow(w, 6), 1);                          /* SW_MINIMIZE */
    CHECK(IsIconic(w) && IsWindowVisible(w));
    CHECK_EQ(wrect(w).left, -32000);
    CHECK_EQ(g_lg[log_pos(w, 5)].w, 1);                     /* SIZE_MINIMIZED */
    CHECK_EQ(crect(w).right, 0);
    CHECK(GetActiveWindow() == NULL);
    CHECK_EQ(ShowWindow(w, 9), 1);                          /* SW_RESTORE: back to maximized */
    CHECK(!IsIconic(w) && (GetWindowLongA(w, -16) & T_MAXIMIZE));
    CHECK_EQ(ShowWindow(w, 9), 1);                          /* now to normal */
    CHECK(!(GetWindowLongA(w, -16) & (T_MAXIMIZE | T_MINIMIZE)));
    CHECK_RECT(wrect(w), 50, 60, 450, 360);
    CHECK_EQ(ShowWindow(w, 7), 1);                          /* SW_SHOWMINNOACTIVE */
    CHECK(IsIconic(w));
    CHECK_EQ(ShowWindow(w, 1), 1);                          /* SW_SHOWNORMAL */
    CHECK_RECT(wrect(w), 50, 60, 450, 360);
    CHECK_EQ(ShowWindow(w, 2), 1);                          /* SW_SHOWMINIMIZED */
    CHECK(IsIconic(w));
    CHECK_EQ(ShowWindow(w, 11), 1);                         /* SW_FORCEMINIMIZE */
    CHECK_EQ(ShowWindow(w, 4), 1);                          /* SW_SHOWNOACTIVATE restores */
    CHECK(!IsIconic(w));
    CHECK_EQ(ShowWindow(w, 0), 1);
    CHECK(!IsWindowVisible(w));
    CHECK_EQ(ShowWindow(w, 8), 0);                          /* SW_SHOWNA */
    CHECK(IsWindowVisible(w));
    CHECK_EQ(ShowWindow(w, 10), 1);                         /* SW_SHOWDEFAULT */
    CHECK_EQ(ShowWindow(w, 12), 0);
    CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(ShowWindow(T_BOGUS, 5), 0);
    CHECK_EQ(GetLastError(), 1400);
    /* a child is visible only when its parent is */
    HWND k = mk(0, "WtPos", T_CHILD | T_VISIBLE, 0, 0, 10, 10, w);
    CHECK(IsWindowVisible(k));
    ShowWindow(w, 0);
    CHECK(!IsWindowVisible(k) && (GetWindowLongA(k, -16) & T_VISIBLE));
    CHECK(DestroyWindow(w));
    CHECK(DestroyWindow(h));
}

TEST(wt_longs_text_thread)
{
    reg("WtLong", 4, 8);
    HWND p = mk(0x2, "WtLong", T_POPUP, 0, 0, 50, 50);
    HWND c = mk(0, "WtLong", T_CHILD, 0, 0, 5, 5, p, (HMENU)(uintptr_t)77);
    CHECK_EQ(GetWindowLongA(c, -12), 77);                   /* GWL_ID */
    CHECK_EQ(SetWindowLongA(c, -12, 78), 77);
    CHECK_EQ(GetWindowLongA(c, -12), 78);
    CHECK_EQ(GetWindowLongA(p, -6), (LONG)(uintptr_t)T_INSTANCE);
    CHECK_EQ(GetWindowLongA(c, -8), (LONG)(uintptr_t)p);   /* GWL_HWNDPARENT of a child */
    CHECK_EQ(GetWindowLongA(p, -8), 0);
    CHECK_EQ(GetWindowLongA(p, -20), 2);
    CHECK_EQ((long long)(DWORD)GetWindowLongA(p, -16), (long long)T_POPUP);
    SetLastError(0);
    CHECK_EQ(GetWindowLongA(p, 0), 0);                      /* extra bytes: zeroed */
    CHECK_EQ(GetLastError(), 0);
    CHECK_EQ(SetWindowLongA(p, 4, 0x55AA), 0);
    CHECK_EQ(GetWindowLongA(p, 4), 0x55AA);
    CHECK_EQ(GetWindowLongA(p, 0), 0);
    CHECK_EQ(GetWindowLongA(p, 5), 0);                      /* 5..8 overruns the 8 bytes */
    CHECK_EQ(GetLastError(), 1413);
    CHECK_EQ(SetWindowLongA(p, 8, 1), 0);
    CHECK_EQ(GetLastError(), 1413);
    CHECK_EQ(GetWindowLongA(p, -3), 0);
    CHECK_EQ(GetLastError(), 1413);
    /* GWL_STYLE changes are what GetWindowLong sees */
    CHECK_EQ((long long)(DWORD)SetWindowLongA(p, -16, (LONG)(T_POPUP | T_BORDER)), (long long)T_POPUP);
    CHECK_EQ((long long)(DWORD)GetWindowLongA(p, -16), (long long)(T_POPUP | T_BORDER));
    CHECK_EQ(SetWindowLongA(p, -21, 1234), 0);
    CHECK_EQ(GetWindowLongA(p, -21), 1234);
    CHECK_EQ(GetWindowLongA(T_BOGUS, -21), 0);
    CHECK_EQ(GetLastError(), 1400);
    CHECK_EQ(SetWindowLongA(T_BOGUS, -21, 3), 0);
    CHECK_EQ(GetLastError(), 1400);
    /* SetParent through GWL_HWNDPARENT keeps the position inside the parent */
    HWND q = mk(0, "WtLong", T_POPUP, 300, 300, 60, 60);
    SetWindowLongA(c, -8, (LONG)(uintptr_t)q);
    CHECK(GetTopWindow(q) == c && GetTopWindow(p) == NULL);

    /* text of any length goes through WM_SETTEXT / WM_GETTEXT */
    char big[1000];
    memset(big, 'x', sizeof big);
    big[999] = 0;
    reset_log();
    CHECK(SetWindowTextA(p, big));
    CHECK(log_count(p, 0x0C) == 1);
    char out[1200];
    CHECK_EQ(GetWindowTextA(p, out, sizeof out), 999);
    CHECK_STR(out, big);
    CHECK(log_count(p, 0x0D) == 1);
    CHECK_EQ(GetWindowTextA(p, out, 10), 9);
    CHECK_EQ(out[9], 0);
    CHECK_EQ(GetWindowTextA(p, out, 0), 0);
    CHECK(SetWindowTextA(p, NULL));
    CHECK_EQ(GetWindowTextA(p, out, sizeof out), 0);
    CHECK_STR(out, "");
    CHECK_EQ(GetWindowTextA(T_BOGUS, out, sizeof out), 0);
    CHECK_EQ(GetLastError(), 1400);
    CHECK(!SetWindowTextA(T_BOGUS, "x"));
    CHECK_EQ(GetLastError(), 1400);

    DWORD pid = 0;
    CHECK_EQ(GetWindowThreadProcessId(p, &pid), GetCurrentThreadId());
    CHECK_EQ(pid, GetCurrentProcessId());
    CHECK_EQ(GetWindowThreadProcessId(p, NULL), GetCurrentThreadId());
    CHECK_EQ(GetWindowThreadProcessId(T_BOGUS, &pid), 0);
    CHECK_EQ(GetLastError(), 1400);
    DestroyWindow(q);
    DestroyWindow(p);
}

TEST(wt_invalid_handles)
{
    RECT r;
    POINT pt = { 1, 2 };
    char buf[8];
    HWND bad = T_BOGUS;
#define BAD(call, fail) do { SetLastError(0); CHECK_EQ((long long)(call), (long long)(fail)); CHECK_EQ(GetLastError(), 1400); } while (0)
    BAD(DestroyWindow(bad), 0);
    BAD(GetWindowRect(bad, &r), 0);
    BAD(GetClientRect(bad, &r), 0);
    BAD(ClientToScreen(bad, &pt), 0);
    BAD(ScreenToClient(bad, &pt), 0);
    BAD(SetWindowPos(bad, 0, 0, 0, 0, 0, 0), 0);
    BAD(ShowWindow(bad, 1), 0);
    BAD(GetWindowLongA(bad, -16), 0);
    BAD(SetWindowLongA(bad, -16, 0), 0);
    BAD(GetClassLongA(bad, -26), 0);
    BAD(GetClassNameA(bad, buf, 8), 0);
    BAD(GetWindowTextA(bad, buf, 8), 0);
    BAD(SetWindowTextA(bad, "x"), 0);
    BAD(GetWindow(bad, 0), 0);
    BAD(GetTopWindow(bad) != NULL, 0);
    BAD(EnumChildWindows(bad, [](HWND, LPARAM) -> BOOL { return TRUE; }, 0), 0);
    BAD(GetWindowThreadProcessId(bad, NULL), 0);
    BAD(UpdateWindow(bad), 0);
    BAD(SetForegroundWindow(bad), 0);
    CHECK(!IsWindowVisible(bad));
    CHECK(!IsIconic(bad));
    CHECK(!IsWindow(bad));
    BAD(DestroyWindow(NULL), 0);
    BAD(ShowWindow(NULL, 1), 0);
    BAD(GetWindowRect(NULL, &r), 0);
    CHECK(GetWindowRect(fnvnx_desktop_window(), &r));
    CHECK_RECT(r, 0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
    CHECK(!DestroyWindow(fnvnx_desktop_window()));
    CHECK_EQ(GetLastError(), 5);                            /* ERROR_ACCESS_DENIED */
    reg("WtBadPtr");
    HWND h = mk(0, "WtBadPtr", T_POPUP, 0, 0, 5, 5);
    CHECK(!GetWindowRect(h, NULL));
    CHECK_EQ(GetLastError(), 998);
    CHECK(!ClientToScreen(h, NULL));
    CHECK_EQ(GetLastError(), 998);
    DestroyWindow(h);
}

static int g_paints;
static LRESULT paint_proc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (m == 0x0F) g_paints++;
    return DefWindowProcA(h, m, w, l);
}

TEST(wt_update_window)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = paint_proc;
    wc.lpszClassName = "WtPaint";
    wc.hInstance = T_INSTANCE;
    RegisterClassA(&wc);
    HWND h = mk(0, "WtPaint", T_POPUP, 0, 0, 30, 30);
    g_paints = 0;
    CHECK(UpdateWindow(h));
    CHECK_EQ(g_paints, 0);                                  /* hidden: no update region painted */
    ShowWindow(h, 8);
    CHECK(UpdateWindow(h));
    CHECK_EQ(g_paints, 1);                                  /* a fresh visible window is wholly invalid */
    CHECK(UpdateWindow(h));
    CHECK_EQ(g_paints, 1);                                  /* DefWindowProc validated it */
    SetWindowPos(h, 0, 0, 0, 40, 40, 0x2 | 0x4 | 0x10);      /* a resize invalidates the client area */
    CHECK(UpdateWindow(h));
    CHECK_EQ(g_paints, 2);
    CHECK(UpdateWindow(h));
    CHECK_EQ(g_paints, 2);
    fnvnx_window_validate(h);
    DestroyWindow(h);
}

TEST(wt_integer_atoms_and_initial_state)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = rec_proc;
    wc.hInstance = T_INSTANCE;
    wc.lpszClassName = (LPCSTR)(uintptr_t)100;              /* integer atom 100: the class "#100" */
    CHECK_EQ(RegisterClassA(&wc), 100);
    wc.lpszClassName = "#100";
    CHECK_EQ(RegisterClassA(&wc), 0);                       /* the same class */
    CHECK_EQ(GetLastError(), 1410);
    wc.lpszClassName = (LPCSTR)(uintptr_t)0;
    CHECK_EQ(RegisterClassA(&wc), 0);
    CHECK_EQ(GetLastError(), 87);
    wc.lpszClassName = (LPCSTR)(uintptr_t)0xC001;           /* not an integer atom of this range */
    CHECK_EQ(RegisterClassA(&wc), 0);
    CHECK_EQ(GetLastError(), 87);
    wc.lpszClassName = "#200";
    CHECK_EQ(RegisterClassA(&wc), 200);                     /* "#n" registers the integer atom n */
    HWND a = CreateWindowExA(0, (LPCSTR)(uintptr_t)100, "a", T_POPUP, 0, 0, 5, 5, NULL, NULL, T_INSTANCE, NULL);
    HWND b = CreateWindowExA(0, "#100", "b", T_POPUP, 0, 0, 5, 5, NULL, NULL, T_INSTANCE, NULL);
    HWND c = CreateWindowExA(0, (LPCSTR)(uintptr_t)200, "c", T_POPUP, 0, 0, 5, 5, NULL, NULL, T_INSTANCE, NULL);
    CHECK(a && b && c);
    char buf[16];
    GetClassNameA(a, buf, sizeof buf);
    CHECK_STR(buf, "#100");
    CHECK_EQ(GetClassLongA(b, -32), 100);
    CHECK(FindWindowA("#100", "b") == b);
    CHECK(FindWindowA((LPCSTR)(uintptr_t)200, NULL) == c);
    CHECK(UnregisterClassA("#200", T_INSTANCE) == 0);       /* c still exists */
    DestroyWindow(a); DestroyWindow(b); DestroyWindow(c);
    CHECK(UnregisterClassA((LPCSTR)(uintptr_t)100, T_INSTANCE));
    CHECK(UnregisterClassA("#200", T_INSTANCE));
    CHECK(!UnregisterClassA("#100", T_INSTANCE));
    CHECK_EQ(GetLastError(), 1411);
    /* the predefined dialog class is the integer atom 0x8002 */
    HWND d = CreateWindowExA(0, "#32770", "d", T_POPUP, 0, 0, 5, 5, NULL, NULL, T_INSTANCE, NULL);
    CHECK(d != NULL);
    CHECK_EQ(GetClassLongA(d, -32), 0x8002);
    DestroyWindow(d);

    /* WS_MINIMIZE / WS_MAXIMIZE at creation: the state ShowWindow(SW_SHOWMINIMIZED / SW_SHOWMAXIMIZED) gives */
    reg("WtInit");
    HWND m = mk(0, "WtInit", T_POPUP | T_VISIBLE | T_MINIMIZE, 30, 40, 200, 100);
    CHECK(m != NULL && IsIconic(m) && IsWindowVisible(m));
    CHECK_EQ(wrect(m).left, -32000);
    CHECK_EQ(ShowWindow(m, 9), 1);                          /* restore: the rectangle given at creation */
    CHECK_RECT(wrect(m), 30, 40, 230, 140);
    CHECK(!IsIconic(m));
    DestroyWindow(m);
    HWND x = mk(0, "WtInit", T_POPUP | T_MAXIMIZE, 30, 40, 200, 100);   /* hidden, but maximized */
    CHECK(x != NULL && !IsWindowVisible(x));
    CHECK((GetWindowLongA(x, -16) & T_MAXIMIZE) != 0);
    CHECK_RECT(wrect(x), 0, 0, GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
    ShowWindow(x, 9);
    CHECK_RECT(wrect(x), 30, 40, 230, 140);
    DestroyWindow(x);
}
