/* test_u32_contract.cpp - user32 / gdi32 imports brought to their full contract (W21): the DefWindowProc rule table, update
 * regions and BeginPaint, keyboard focus, EnumDisplayDevicesA strings. Contract: MSDN; reference: Wine 9.0 win32u (defwnd.c,
 * sysparams.c) read as data. */
#include "s4_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <vector>

extern "C" {
BOOL fnwin_invalidate(HWND, const RECT *, int);
BOOL fnwin_validate(HWND, const RECT *);
BOOL fnwin_update_rect(HWND, RECT *, int *);
HDC BeginPaint(HWND, void *);
BOOL EndPaint(HWND, const void *);
COLORREF GetPixel(HDC, int, int);
HDC GetDC(HWND);
int ReleaseDC(HWND, HDC);
BOOL EnumDisplayDevicesA(LPCSTR, DWORD, void *, DWORD);
BOOL IsWindowVisible(HWND);
}

namespace {
struct Ev { HWND h; UINT m; WPARAM w; LPARAM l; };
std::vector<Ev> g_ev;
HDC g_erase_dc;
int g_erase_result = 1;
int g_paint_calls;
RECT g_paint_rc;
int g_paint_ferase;
bool g_paint_in_proc;   /* the paint handler runs BeginPaint/EndPaint itself */
LRESULT uproc(HWND h, UINT m, WPARAM w, LPARAM l)
{
    g_ev.push_back({ h, m, w, l });
    if (m == 0x14 && g_erase_result >= 0) { g_erase_dc = (HDC)(uintptr_t)w; if (g_erase_result == 2) return DefWindowProcA(h, m, w, l); return g_erase_result; }
    if (m == 0x0F && g_paint_in_proc) {
        alignas(8) unsigned char ps[128];
        g_paint_calls++;
        HDC dc = BeginPaint(h, ps);
        if (dc) {
            struct PS { HDC hdc; int ferase; RECT rc; } *p = (struct PS *)ps;
            g_paint_rc = p->rc; g_paint_ferase = p->ferase;
            EndPaint(h, ps);
        }
        return 0;
    }
    return DefWindowProcA(h, m, w, l);
}
int count(HWND h, UINT m) { int n = 0; for (const Ev &e : g_ev) n += e.h == h && e.m == m; return n; }
int at(HWND h, UINT m, int nth = 0) { int k = 0; for (size_t i = 0; i < g_ev.size(); i++) if (g_ev[i].h == h && g_ev[i].m == m && k++ == nth) return (int)i; return -1; }
ATOM reg(const char *n, UINT style = 0, HBRUSH bg = NULL)
{
    WNDCLASSA wc;
    memset(&wc, 0, sizeof wc);
    wc.lpfnWndProc = uproc; wc.lpszClassName = n; wc.style = style; wc.hbrBackground = bg;
    return RegisterClassA(&wc);
}
HWND mkw(const char *cls, DWORD style, int x, int y, int w, int h, HWND parent = NULL, DWORD ex = 0, const char *title = "u")
{
    return CreateWindowExA(ex, cls, title, style, x, y, w, h, parent, NULL, NULL, NULL);
}
void pump(void)
{
    MSG m;
    int guard = 0;
    while (PeekMessageA(&m, NULL, 0, 0, PM_REMOVE) && guard++ < 1000) { TranslateMessage(&m); DispatchMessageA(&m); }
}
const DWORD VIS = 0x10000000u, POPUP = 0x80000000u, CHILD = 0x40000000u, CAPTION = 0x00C00000u, SYSMENU = 0x00080000u,
            MINBOX = 0x00020000u, MAXBOX = 0x00010000u, THICK = 0x00040000u, OVL = 0x00CF0000u;
#define LPXY(x, y) ((LPARAM)(((unsigned)(x) & 0xFFFF) | ((unsigned)(y) << 16)))
bool rc_is(const RECT &r, long l, long t, long rr, long b) { return r.left == l && r.top == t && r.right == rr && r.bottom == b; }
}

TEST(u32c_defproc_text_and_creation)
{
    reg("U32Txt");
    HWND h = mkw("U32Txt", POPUP, 0, 0, 50, 50, NULL, 0, "caption");
    char buf[32];
    CHECK_EQ(DefWindowProcA(h, 0x000E, 0, 0), 7);                       /* WM_GETTEXTLENGTH: the creation text was stored by WM_NCCREATE */
    memset(buf, 'x', sizeof buf);
    CHECK_EQ(DefWindowProcA(h, 0x000D, sizeof buf, (LPARAM)(uintptr_t)buf), 7); CHECK_STR(buf, "caption");
    CHECK_EQ(DefWindowProcA(h, 0x000D, 4, (LPARAM)(uintptr_t)buf), 3); CHECK_STR(buf, "cap");      /* at most wParam - 1 characters */
    memset(buf, 'x', sizeof buf);
    CHECK_EQ(DefWindowProcA(h, 0x000D, 1, (LPARAM)(uintptr_t)buf), 0); CHECK_EQ(buf[0], 0);
    CHECK_EQ(DefWindowProcA(h, 0x000D, 0, (LPARAM)(uintptr_t)buf), 0);                              /* no room: nothing */
    CHECK_EQ(DefWindowProcA(h, 0x000D, 8, 0), 0);                                                   /* no buffer */
    CHECK_EQ(DefWindowProcA(h, 0x000C, 0, (LPARAM)(uintptr_t)"new text"), 1);
    CHECK_EQ(DefWindowProcA(h, 0x000E, 0, 0), 8);
    CHECK_EQ(DefWindowProcA(h, 0x000C, 0, 0), 1);                                                   /* NULL text: empty */
    CHECK_EQ(DefWindowProcA(h, 0x000E, 0, 0), 0);
    CHECK_EQ(DefWindowProcA(h, 0x0081, 0, 0), 0);                                                   /* WM_NCCREATE without a create structure: refused */
    DestroyWindow(h);
    CHECK_EQ(DefWindowProcA(h, 0x0010, 0, 0), 0);                                                   /* a window that is gone: 0 */
}

TEST(u32c_defproc_constant_answers)
{
    reg("U32Const");
    HWND h = mkw("U32Const", POPUP, 0, 0, 50, 50);
    CHECK_EQ(DefWindowProcA(h, 0x0011, 0, 0), 1);          /* WM_QUERYENDSESSION */
    CHECK_EQ(DefWindowProcA(h, 0x0013, 0, 0), 1);          /* WM_QUERYOPEN */
    CHECK_EQ(DefWindowProcA(h, 0x0055, 3, 0), 1);          /* WM_NOTIFYFORMAT: NFR_ANSI */
    CHECK_EQ(DefWindowProcA(h, 0x002E, 0, 0), -1);         /* WM_VKEYTOITEM */
    CHECK_EQ(DefWindowProcA(h, 0x002F, 0, 0), -1);         /* WM_CHARTOITEM */
    CHECK_EQ(DefWindowProcA(h, 0x022A, 0, 0), 0x454C4946); /* WM_DROPOBJECT: DRAG_FILE */
    CHECK_EQ(DefWindowProcA(h, 0x022B, 0, 0), 0);          /* WM_QUERYDROPOBJECT: no WS_EX_ACCEPTFILES */
    HWND f = mkw("U32Const", POPUP, 0, 0, 50, 50, NULL, 0x10);
    CHECK_EQ(DefWindowProcA(f, 0x022B, 0, 0), 1);
    CHECK_EQ(DefWindowProcA(h, 0x0035, 0, 0), 0);          /* WM_ISACTIVEICON */
    CHECK_EQ(DefWindowProcA(h, 0x0086, 1, 0), 1);          /* WM_NCACTIVATE */
    CHECK_EQ(DefWindowProcA(h, 0x0035, 0, 0), 1);
    CHECK_EQ(DefWindowProcA(h, 0x0086, 0, 0), 1);
    CHECK_EQ(DefWindowProcA(h, 0x0035, 0, 0), 0);
    CHECK_EQ(DefWindowProcA(h, 0x0050, 0, 0x04090409), 0);  /* WM_INPUTLANGCHANGEREQUEST: the one layout */
    CHECK_EQ(DefWindowProcA(h, 0x0083, 0, 0), 0);          /* WM_NCCALCSIZE without a rectangle */
    /* a message without a documented default action answers 0: WM_SETFONT / WM_GETFONT belong to controls, WM_ENDSESSION, user messages */
    for (UINT m : { 0x0030u, 0x0031u, 0x0016u, 0x0400u, 0x0401u, 0x8001u, 0xC123u, 0x0007u, 0x0008u, 0x0005u, 0x0003u, 0x0002u, 0x001Cu, 0x0112u + 0x1000 })
        CHECK_EQ(DefWindowProcA(h, m, 0, 0), 0);
    /* WM_IME_*: the environment has no IME, so there is no default IME window to pass them on to */
    for (UINT m : { 0x010Du, 0x010Eu, 0x010Fu, 0x0281u, 0x0282u }) CHECK_EQ(DefWindowProcA(h, m, 1, 2), 0);
    DestroyWindow(f); DestroyWindow(h);
}

TEST(u32c_defproc_icons)
{
    reg("U32Ico");
    HWND h = mkw("U32Ico", POPUP, 0, 0, 50, 50);
    HICON a = (HICON)(uintptr_t)0x1111, b = (HICON)(uintptr_t)0x2222;
    CHECK_EQ(DefWindowProcA(h, 0x007F, 0, 0), 0);                              /* WM_GETICON: none set */
    CHECK_EQ(DefWindowProcA(h, 0x0080, 0, (LPARAM)(uintptr_t)a), 0);           /* WM_SETICON ICON_SMALL returns the previous one */
    CHECK_EQ(DefWindowProcA(h, 0x0080, 1, (LPARAM)(uintptr_t)b), 0);           /* ICON_BIG */
    CHECK_EQ((uintptr_t)DefWindowProcA(h, 0x007F, 0, 0), 0x1111);
    CHECK_EQ((uintptr_t)DefWindowProcA(h, 0x007F, 1, 0), 0x2222);
    CHECK_EQ((uintptr_t)DefWindowProcA(h, 0x007F, 2, 0), 0x1111);              /* ICON_SMALL2 */
    CHECK_EQ(DefWindowProcA(h, 0x007F, 3, 0), 0);                              /* not an icon type */
    CHECK_EQ((uintptr_t)DefWindowProcA(h, 0x0080, 0, 0), 0x1111);
    CHECK_EQ(DefWindowProcA(h, 0x007F, 0, 0), 0);
    CHECK_EQ(DefWindowProcA(h, 0x0080, 7, (LPARAM)(uintptr_t)a), 0);           /* not an icon type: nothing changes */
    CHECK_EQ((uintptr_t)DefWindowProcA(h, 0x007F, 1, 0), 0x2222);
    DestroyWindow(h);
}

TEST(u32c_nc_hittest_frame)
{
    reg("U32Hit");
    HWND h = mkw("U32Hit", OVL | VIS, 100, 100, 400, 300);     /* caption, sysmenu, both boxes, sizing border */
    RECT wr, cr; POINT o = { 0, 0 };
    GetWindowRect(h, &wr); GetClientRect(h, &cr); ClientToScreen(h, &o);
    int fx = GetSystemMetrics(32), cap = GetSystemMetrics(4), bx = GetSystemMetrics(30);
    long capy = wr.top + fx + 2;                                       /* inside the caption */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.right - fx - 3, capy)), 20);                          /* HTCLOSE */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.right - fx - cap - 3, capy)), 9);                     /* HTMAXBUTTON */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.right - fx - cap - bx - 3, capy)), 8);                /* HTMINBUTTON */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.left + fx + 100, capy)), 2);                          /* HTCAPTION */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.left + fx + 2, capy)), 2);                            /* no icon: the sysmenu box is not there */
    DefWindowProcA(h, 0x0080, 0, 0x1234);                                                             /* an icon appears */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.left + fx + 2, capy)), 3);                            /* HTSYSMENU */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(o.x + 3, o.y + 3)), 1);                                  /* HTCLIENT */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.left + 1, wr.top + 150)), 10);                        /* HTLEFT */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.left, wr.top)), 13);                                  /* HTTOPLEFT */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.right - 1, wr.bottom - 1)), 17);                      /* HTBOTTOMRIGHT */
    CHECK_EQ(DefWindowProcA(h, 0x84, 0, LPXY(wr.right + 5, wr.top)), 0);                              /* HTNOWHERE */
    DestroyWindow(h);
    HWND t = mkw("U32Hit", CAPTION | SYSMENU | VIS, 100, 100, 400, 300, NULL, 0x80);   /* a tool window: close button only */
    GetWindowRect(t, &wr);
    int sc = GetSystemMetrics(4);
    CHECK_EQ(DefWindowProcA(t, 0x84, 0, LPXY(wr.right - GetSystemMetrics(7) - 3, wr.top + GetSystemMetrics(7) + 2)), 20);
    CHECK_EQ(DefWindowProcA(t, 0x84, 0, LPXY(wr.right - GetSystemMetrics(7) - sc - 3, wr.top + GetSystemMetrics(7) + 2)), 2);
    DestroyWindow(t);
}

TEST(u32c_syscommand_states)
{
    reg("U32Sys");
    HWND h = mkw("U32Sys", OVL | VIS, 10, 10, 400, 300);
    CHECK(!IsIconic(h));
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF020, 0), 0);               /* SC_MINIMIZE */
    CHECK(IsIconic(h));
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF120, 0), 0);               /* SC_RESTORE */
    CHECK(!IsIconic(h));
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF030, 0), 0);               /* SC_MAXIMIZE */
    CHECK((GetWindowLongA(h, -16) & 0x01000000) != 0);
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF120, 0), 0);
    CHECK((GetWindowLongA(h, -16) & 0x01000000) == 0);
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF130, 0), 1);               /* SC_TASKLIST: answered 1 */
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF140, 0), 1);               /* SC_SCREENSAVE */
    CHECK_EQ(SendMessageA(h, 0x0112, 0x0100, 0), 1);               /* an application command: handled by the client side */
    for (UINT sc : { 0xF040u, 0xF050u, 0xF110u, 0xF150u, 0xF070u, 0xF080u, 0xF090u, 0xF100u }) CHECK_EQ(SendMessageA(h, 0x0112, sc, 0), 0);
    CHECK(IsWindow(h));
    SetWindowLongA(h, -16, GetWindowLongA(h, -16) | 0x08000000);   /* WS_DISABLED: a disabled window ignores the commands */
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF020, 0), 0);
    CHECK(!IsIconic(h));
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF060, 0), 0);
    CHECK(IsWindow(h));
    SetWindowLongA(h, -16, GetWindowLongA(h, -16) & ~0x08000000);
    g_ev.clear();
    CHECK_EQ(SendMessageA(h, 0x0112, 0xF060, 0), 0);               /* SC_CLOSE: WM_CLOSE, whose default destroys */
    CHECK_EQ(count(h, 0x10), 1);
    CHECK(!IsWindow(h));
}

TEST(u32c_owned_popups_follow_owner)
{
    reg("U32Own");
    HWND o = mkw("U32Own", OVL | VIS, 10, 10, 400, 300);
    HWND p = mkw("U32Own", POPUP | VIS, 20, 20, 100, 100, o);       /* an owned pop-up */
    CHECK(IsWindowVisible(p));
    g_ev.clear();
    SendMessageA(o, 0x0112, 0xF020, 0);                             /* the owner is minimized: its pop-ups hide */
    CHECK(!IsWindowVisible(p));
    CHECK_EQ(count(p, 0x18), 2);                                    /* WM_SHOWWINDOW(FALSE, SW_PARENTCLOSING) arrives, then the ShowWindow(SW_HIDE) one */
    CHECK_EQ(g_ev[at(p, 0x18, 0)].w, 0); CHECK_EQ(g_ev[at(p, 0x18, 0)].l, 1);
    SendMessageA(o, 0x0112, 0xF120, 0);                             /* restored: they come back */
    CHECK(IsWindowVisible(p));
    DestroyWindow(p); DestroyWindow(o);
}

TEST(u32c_setredraw)
{
    reg("U32Redraw");
    HWND h = mkw("U32Redraw", POPUP | VIS, 0, 0, 40, 40);
    CHECK(IsWindowVisible(h));
    CHECK(fnwin_invalidate(h, NULL, 0));
    CHECK_EQ(SendMessageA(h, 0x000B, 0, 0), 0);                     /* WM_SETREDRAW(FALSE): validated, WS_VISIBLE off (the reference's way) */
    RECT u;
    CHECK(!fnwin_update_rect(h, &u, NULL));
    CHECK(!IsWindowVisible(h));
    CHECK_EQ(SendMessageA(h, 0x000B, 1, 0), 0);
    CHECK(IsWindowVisible(h));
    DestroyWindow(h);
}

TEST(u32c_update_region_rectangles)
{
    reg("U32Rgn");
    HWND h = mkw("U32Rgn", POPUP | VIS, 0, 0, 100, 80);
    pump();                                                         /* the first paint validates it */
    RECT u; int er = 0;
    CHECK(!fnwin_update_rect(h, &u, &er));
    RECT a = { 10, 10, 30, 30 }, b = { 20, 20, 50, 40 }, c = { -50, -50, 5, 5 }, d = { 90, 70, 200, 200 };
    CHECK(fnwin_invalidate(h, &a, 0));
    CHECK(fnwin_update_rect(h, &u, &er)); CHECK(rc_is(u, 10, 10, 30, 30)); CHECK_EQ(er, 0);
    CHECK(fnwin_invalidate(h, &b, 1));                                  /* an overlapping rectangle joins: the bounding box grows, erase marked */
    CHECK(fnwin_update_rect(h, &u, &er)); CHECK(rc_is(u, 10, 10, 50, 40)); CHECK_EQ(er, 1);
    CHECK(fnwin_invalidate(h, &c, 0)); CHECK(fnwin_invalidate(h, &d, 0));   /* clipped to the client area */
    CHECK(fnwin_update_rect(h, &u, &er)); CHECK(rc_is(u, 0, 0, 100, 80));
    RECT hole = { 0, 0, 100, 80 };
    CHECK(fnwin_validate(h, &hole));                                    /* everything validated: the erase mark goes too */
    CHECK(!fnwin_update_rect(h, &u, &er)); CHECK_EQ(er, 0);
    CHECK(fnwin_invalidate(h, &a, 1));
    RECT part = { 0, 0, 20, 100 };                                      /* validating part of it leaves the rest */
    CHECK(fnwin_validate(h, &part));
    CHECK(fnwin_update_rect(h, &u, &er)); CHECK(rc_is(u, 20, 10, 30, 30)); CHECK_EQ(er, 1);
    CHECK(fnwin_validate(h, NULL));
    CHECK(!fnwin_update_rect(h, &u, &er));
    RECT empty = { 5, 5, 5, 20 };
    CHECK(fnwin_invalidate(h, &empty, 0)); CHECK(!fnwin_update_rect(h, &u, &er));   /* an empty rectangle invalidates nothing */
    CHECK(!fnwin_invalidate(NULL, NULL, 0)); CHECK(!fnwin_validate((HWND)(uintptr_t)0x99, NULL));
    DestroyWindow(h);
}

TEST(u32c_beginpaint_endpaint)
{
    reg("U32Paint", 0, (HBRUSH)6 /* COLOR_WINDOW + 1 */);
    HWND h = mkw("U32Paint", POPUP | VIS, 0, 0, 100, 80);
    pump();
    g_erase_result = -1;                                                /* DefWindowProc erases with the class brush */
    RECT a = { 10, 10, 30, 30 };
    CHECK(fnwin_invalidate(h, &a, 1));
    g_ev.clear();
    alignas(8) unsigned char ps[128];
    struct PS { HDC hdc; int ferase; RECT rc; } *p = (struct PS *)ps;
    HDC dc = BeginPaint(h, ps);
    CHECK(dc != NULL);
    CHECK(rc_is(p->rc, 10, 10, 30, 30));                                /* rcPaint: the update region */
    CHECK_EQ(p->ferase, 0);                                             /* the default procedure erased */
    CHECK_EQ(count(h, 0x14), 1);                                        /* WM_ERASEBKGND was sent with the paint DC */
    RECT u;
    CHECK(!fnwin_update_rect(h, &u, NULL));                             /* BeginPaint validated */
    CHECK_EQ(GetPixel(dc, 15, 15), 0xFFFFFF);                           /* COLOR_WINDOW fills the region */
    CHECK_EQ((unsigned)GetPixel(dc, 40, 40) == 0xFFFFFF, 0);            /* outside the clip nothing was filled */
    CHECK(EndPaint(h, ps));
    /* a handler that does not erase: fErase tells the paint code to do it */
    g_erase_result = 0;
    CHECK(fnwin_invalidate(h, &a, 1));
    dc = BeginPaint(h, ps);
    CHECK_EQ(p->ferase, 1);
    CHECK(EndPaint(h, ps));
    /* no erase mark: no WM_ERASEBKGND */
    g_ev.clear();
    CHECK(fnwin_invalidate(h, &a, 0));
    dc = BeginPaint(h, ps);
    CHECK_EQ(count(h, 0x14), 0); CHECK_EQ(p->ferase, 0);
    CHECK(EndPaint(h, ps));
    /* WM_NCPAINT first when the frame is pending (a resize marks it) */
    g_erase_result = 1;
    SetWindowPos(h, NULL, 0, 0, 120, 90, 0x2 | 0x4 | 0x10);
    g_ev.clear();
    dc = BeginPaint(h, ps);
    CHECK_EQ(count(h, 0x85), 1);
    CHECK(at(h, 0x85) < at(h, 0x14));
    CHECK(EndPaint(h, ps));
    /* an empty update region: an empty rcPaint */
    dc = BeginPaint(h, ps);
    CHECK(rc_is(p->rc, 0, 0, 0, 0));
    CHECK(EndPaint(h, ps));
    CHECK(!EndPaint(h, ps));
    g_erase_result = 1;
    DestroyWindow(h);
}

TEST(u32c_wm_paint_generation)
{
    reg("U32Gen");
    HWND h = mkw("U32Gen", POPUP, 0, 0, 100, 80);
    g_erase_result = 1;
    pump();
    g_ev.clear();
    CHECK_EQ(count(h, 0x0F), 0);                                        /* hidden: nothing to paint */
    ShowWindow(h, 5);
    MSG m;
    PostMessageA(h, 0x0400, 0, 0);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE) && m.message == 0x0400);   /* posted messages come before WM_PAINT */
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE) && m.message == 0x0F);   /* WM_PAINT only when the queue is otherwise empty */
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE) && m.message == 0x0F);   /* peeking does not remove it: validation does */
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_REMOVE) && m.message == 0x0F);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE) && m.message == 0x0F);   /* retrieved but not validated: still there */
    DispatchMessageA(&m);                                                    /* DefWindowProc: BeginPaint + EndPaint validates */
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE));
    RECT r = { 1, 1, 5, 5 };
    fnwin_invalidate(h, &r, 0);
    CHECK(PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE) && m.message == 0x0F && m.hwnd == h);
    CHECK(UpdateWindow(h));
    CHECK(!PeekMessageA(&m, NULL, 0, 0, PM_NOREMOVE));
    /* the paint handler gets rcPaint = the invalidated part */
    g_paint_in_proc = true; g_paint_calls = 0;
    RECT r2 = { 7, 8, 19, 20 };
    fnwin_invalidate(h, &r2, 0);
    UpdateWindow(h);
    CHECK_EQ(g_paint_calls, 1); CHECK(rc_is(g_paint_rc, 7, 8, 19, 20));
    g_paint_in_proc = false;
    DestroyWindow(h);
}

TEST(u32c_resize_and_class_redraw_styles)
{
    reg("U32NoRedraw");
    reg("U32HRedraw", 0x2 /* CS_HREDRAW */);
    reg("U32VRedraw", 0x1 /* CS_VREDRAW */);
    g_erase_result = 1;
    RECT u; int er;
    HWND a = mkw("U32NoRedraw", POPUP | VIS, 0, 0, 50, 50), b = mkw("U32HRedraw", POPUP | VIS, 100, 0, 50, 50), c = mkw("U32VRedraw", POPUP | VIS, 200, 0, 50, 50);
    pump();
    SetWindowPos(a, NULL, 0, 0, 80, 70, 0x2 | 0x4 | 0x10);              /* no redraw styles: only the new strips */
    CHECK(fnwin_update_rect(a, &u, &er)); CHECK(rc_is(u, 0, 0, 80, 70)); CHECK_EQ(er, 1);
    pump();
    SetWindowPos(a, NULL, 0, 0, 90, 70, 0x2 | 0x4 | 0x10);              /* only the right strip is new */
    CHECK(fnwin_update_rect(a, &u, &er)); CHECK(rc_is(u, 80, 0, 90, 70));
    pump();
    SetWindowPos(b, NULL, 0, 0, 60, 50, 0x2 | 0x4 | 0x10);              /* CS_HREDRAW: a width change repaints it all */
    CHECK(fnwin_update_rect(b, &u, &er)); CHECK(rc_is(u, 0, 0, 60, 50));
    pump();
    SetWindowPos(b, NULL, 0, 0, 60, 60, 0x2 | 0x4 | 0x10);              /* a height change: only the new strip */
    CHECK(fnwin_update_rect(b, &u, &er)); CHECK(rc_is(u, 0, 50, 60, 60));
    pump();
    SetWindowPos(c, NULL, 0, 0, 40, 50, 0x2 | 0x4 | 0x10);              /* CS_VREDRAW, narrower: nothing is exposed */
    CHECK(!fnwin_update_rect(c, &u, &er));
    SetWindowPos(c, NULL, 0, 0, 40, 70, 0x2 | 0x4 | 0x10);              /* a height change repaints it all */
    CHECK(fnwin_update_rect(c, &u, &er)); CHECK(rc_is(u, 0, 0, 40, 70));
    pump();
    SetWindowPos(a, NULL, 0, 0, 90, 70, 0x2 | 0x4 | 0x10 | 0x8 /* SWP_NOREDRAW */ | 0x20);   /* FRAMECHANGED: all of it, whatever the flags */
    CHECK(fnwin_update_rect(a, &u, &er)); CHECK(rc_is(u, 0, 0, 90, 70));
    DestroyWindow(a); DestroyWindow(b); DestroyWindow(c);
}

TEST(u32c_exposure_repaints_what_lay_beneath)
{
    reg("U32Under");
    g_erase_result = 1;
    HWND under = mkw("U32Under", POPUP | VIS, 0, 0, 200, 200);
    HWND over = mkw("U32Under", POPUP | VIS, 50, 50, 60, 60);
    pump();
    RECT u; int er;
    CHECK(!fnwin_update_rect(under, &u, &er));
    ShowWindow(over, 0);                                                /* hiding exposes the window beneath */
    CHECK(fnwin_update_rect(under, &u, &er)); CHECK(rc_is(u, 50, 50, 110, 110)); CHECK_EQ(er, 1);
    pump();
    ShowWindow(over, 5);
    pump();
    SetWindowPos(over, NULL, 150, 50, 0, 0, 0x1 | 0x4 | 0x10);          /* moving it uncovers part of the old place */
    CHECK(fnwin_update_rect(under, &u, &er)); CHECK(rc_is(u, 50, 50, 110, 110));
    pump();
    DestroyWindow(over);                                                /* destroying uncovers what it covered */
    CHECK(fnwin_update_rect(under, &u, &er)); CHECK(rc_is(u, 150, 50, 210 > 200 ? 200 : 210, 110));
    pump();
    HWND kid = mkw("U32Under", CHILD | VIS, 10, 10, 30, 30, under);
    pump();
    ShowWindow(kid, 0);                                                 /* a child's place goes to its parent */
    CHECK(fnwin_update_rect(under, &u, &er)); CHECK(rc_is(u, 10, 10, 40, 40));
    DestroyWindow(kid); DestroyWindow(under);
}

TEST(u32c_clip_children_and_siblings)
{
    reg("U32Clip");
    g_erase_result = 1;
    HWND p = mkw("U32Clip", POPUP | VIS | 0x02000000 /* WS_CLIPCHILDREN */, 0, 0, 100, 100);
    HWND k = mkw("U32Clip", CHILD | VIS, 0, 0, 100, 40, p);
    pump();
    fnwin_invalidate(p, NULL, 0);
    alignas(8) unsigned char ps[128];
    struct PS { HDC hdc; int ferase; RECT rc; } *q = (struct PS *)ps;
    BeginPaint(p, ps);
    CHECK(rc_is(q->rc, 0, 40, 100, 100));                               /* the child's area is not painted by the parent */
    EndPaint(p, ps);
    DestroyWindow(k); DestroyWindow(p);
}

TEST(u32c_focus_chain)
{
    reg("U32Focus");
    g_erase_result = 1;
    HWND a = mkw("U32Focus", OVL | VIS, 0, 0, 300, 200), b = mkw("U32Focus", OVL | VIS, 400, 0, 300, 200);
    pump();
    CHECK(GetActiveWindow() == b);                                       /* the newest shown window is active, and DefWindowProc gave it the focus */
    CHECK(GetFocus() == b);
    g_ev.clear();
    HWND prev = SetFocus(a);                                             /* SetFocus activates the window's top-level first */
    CHECK(prev == b);
    CHECK(GetActiveWindow() == a); CHECK(GetFocus() == a);
    CHECK_EQ(count(b, 0x08), 1); CHECK_EQ(count(a, 0x07), 1);            /* WM_KILLFOCUS to the old, WM_SETFOCUS to the new */
    CHECK(g_ev[at(b, 0x08)].w == (WPARAM)(uintptr_t)a);                  /* wParam: the window that receives the focus */
    CHECK(g_ev[at(a, 0x07)].w == (WPARAM)(uintptr_t)b);                  /* wParam: the window that lost it */
    CHECK(SetFocus(a) == a);                                             /* already there: no messages */
    g_ev.clear();
    CHECK(SetFocus(a) == a); CHECK_EQ(count(a, 0x07), 0);
    HWND kid = mkw("U32Focus", CHILD | VIS, 5, 5, 50, 50, a);
    CHECK(SetFocus(kid) == a); CHECK(GetFocus() == kid);
    HWND hid = mkw("U32Focus", CHILD, 5, 5, 50, 50, a);                  /* hidden: cannot take the focus */
    CHECK(SetFocus(hid) == NULL); CHECK(GetFocus() == kid);
    SetWindowLongA(kid, -16, GetWindowLongA(kid, -16) | 0x08000000);
    CHECK(SetFocus(kid) == NULL);                                        /* disabled */
    SetWindowLongA(kid, -16, GetWindowLongA(kid, -16) & ~0x08000000);
    SetLastError(0);
    CHECK(SetFocus((HWND)(uintptr_t)0x777) == NULL); CHECK_EQ(GetLastError(), 1400);
    g_ev.clear();
    CHECK(SetFocus(NULL) == kid);                                        /* keystrokes ignored from here */
    CHECK(GetFocus() == NULL); CHECK_EQ(count(kid, 0x08), 1);
    CHECK(g_ev[at(kid, 0x08)].w == 0);
    SetFocus(kid);
    g_ev.clear();
    DestroyWindow(kid);                                                  /* destroying the focus window: it is told it lost it first */
    CHECK(count(kid, 0x08) == 1 && at(kid, 0x08) < at(kid, 0x02));
    CHECK(GetFocus() == NULL);
    DestroyWindow(hid); DestroyWindow(b); DestroyWindow(a);
}

TEST(u32c_syskeys_and_menu_keys)
{
    reg("U32Keys");
    g_erase_result = 1;
    HWND h = mkw("U32Keys", OVL | VIS, 0, 0, 300, 200);
    pump();
    g_ev.clear();
    /* Alt+F4 posts SC_CLOSE to the top-level window */
    SendMessageA(h, 0x104, 0x73, 1u << 29);
    pump();
    CHECK_EQ(count(h, 0x112), 1); CHECK_EQ(g_ev[at(h, 0x112)].w, 0xF060);
    CHECK(!IsWindow(h));
    h = mkw("U32Keys", OVL | VIS, 0, 0, 300, 200);
    pump();
    g_ev.clear();
    /* F10 pressed and released, Alt pressed and released: SC_KEYMENU */
    SendMessageA(h, 0x100, 0x79, 0); SendMessageA(h, 0x101, 0x79, 0);
    CHECK_EQ(count(h, 0x112), 1); CHECK_EQ(g_ev[at(h, 0x112)].w, 0xF100);
    g_ev.clear();
    SendMessageA(h, 0x104, 0x12, 1u << 29); SendMessageA(h, 0x105, 0x12, 0);
    CHECK_EQ(count(h, 0x112), 1); CHECK_EQ(g_ev[at(h, 0x112)].w, 0xF100);
    g_ev.clear();
    SendMessageA(h, 0x104, 0x12, 1u << 29); SendMessageA(h, 0x104, 0x41, 1u << 29); SendMessageA(h, 0x105, 0x12, 0);   /* Alt+A: not a bare Alt */
    CHECK_EQ(count(h, 0x112), 0);
    g_ev.clear();
    SendMessageA(h, 0x106, 'x', 1u << 29);                               /* WM_SYSCHAR with Alt: SC_KEYMENU with the character */
    CHECK_EQ(count(h, 0x112), 1); CHECK_EQ(g_ev[at(h, 0x112)].l, 'x');
    g_ev.clear();
    SendMessageA(h, 0x106, '\t', 1u << 29); SendMessageA(h, 0x106, 0x1B, 1u << 29);   /* Alt+Tab, Alt+Esc: nothing */
    CHECK_EQ(count(h, 0x112), 0);
    SendMessageA(h, 0x0112, 0xF020, 0);                                  /* iconic: Alt+Enter posts SC_RESTORE */
    g_ev.clear();
    SendMessageA(h, 0x106, '\r', 0);
    pump();
    CHECK_EQ(count(h, 0x112), 1); CHECK(!IsIconic(h));
    /* the other mouse and key defaults */
    g_ev.clear();
    SendMessageA(h, 0x20C, (2 << 16), 0);                                /* WM_XBUTTONUP with XBUTTON2: WM_APPCOMMAND */
    CHECK_EQ(count(h, 0x319), 1); CHECK_EQ(g_ev[at(h, 0x319)].l, (LPARAM)0x80020000u);
    g_ev.clear();
    SendMessageA(h, 0x205, 0, LPXY(5, 6));                               /* WM_RBUTTONUP: WM_CONTEXTMENU at the screen position */
    CHECK_EQ(count(h, 0x7B), 1);
    g_ev.clear();
    SendMessageA(h, 0x4D, 0, 0);                                         /* WM_KEYF1: WM_HELP */
    CHECK_EQ(count(h, 0x53), 1);
    DestroyWindow(h);
}

TEST(u32c_child_defaults_go_to_parent)
{
    reg("U32Par");
    g_erase_result = 1;
    HWND p = mkw("U32Par", POPUP | VIS, 0, 0, 100, 100);
    HWND c = mkw("U32Par", CHILD | VIS, 0, 0, 50, 50, p);
    pump();
    g_ev.clear();
    SendMessageA(c, 0x20A, 0, 0);          /* WM_MOUSEWHEEL */
    SendMessageA(c, 0x20E, 0, 0);          /* WM_MOUSEHWHEEL */
    SendMessageA(c, 0x7B, 0, 0);           /* WM_CONTEXTMENU */
    SendMessageA(c, 0x53, 0, 0);           /* WM_HELP */
    SendMessageA(c, 0x319, 0, 0);          /* WM_APPCOMMAND */
    SendMessageA(c, 0x21, 0, 0);           /* WM_MOUSEACTIVATE */
    for (UINT m : { 0x20Au, 0x20Eu, 0x7Bu, 0x53u, 0x319u, 0x21u }) CHECK_EQ(count(p, m), 1);
    CHECK_EQ(SendMessageA(c, 0x21, 0, LPXY(1, 0x201)), 1);               /* MA_ACTIVATE */
    CHECK_EQ(DefWindowProcA(p, 0x21, 0, LPXY(2, 0x201)), 3);             /* a caption click: MA_NOACTIVATE */
    g_ev.clear();
    SendMessageA(p, 0x20A, 0, 0);                                         /* a top-level window does not pass the wheel on */
    CHECK_EQ(count(p, 0x20A), 1);
    DestroyWindow(c); DestroyWindow(p);
}

TEST(u32c_print_and_syncpaint)
{
    reg("U32Print", 0, (HBRUSH)6);
    g_erase_result = 2;                                                  /* the procedure passes WM_ERASEBKGND to DefWindowProc */
    HWND h = mkw("U32Print", POPUP | VIS, 0, 0, 40, 40);
    pump();
    g_ev.clear();
    DefWindowProcA(h, 0x317, (WPARAM)(uintptr_t)GetDC(h), 8 | 4);        /* PRF_ERASEBKGND | PRF_CLIENT */
    CHECK_EQ(count(h, 0x14), 1); CHECK_EQ(count(h, 0x318), 1);
    g_ev.clear();
    DefWindowProcA(h, 0x317, 0, 4);
    CHECK_EQ(count(h, 0x14), 0); CHECK_EQ(count(h, 0x318), 1);
    ShowWindow(h, 0);
    g_ev.clear();
    DefWindowProcA(h, 0x317, 0, 1 | 4);                                   /* PRF_CHECKVISIBLE and hidden: nothing */
    CHECK_EQ(count(h, 0x318), 0);
    ShowWindow(h, 5);
    pump();
    RECT r = { 0, 0, 10, 10 };
    fnwin_invalidate(h, &r, 1);
    g_ev.clear();
    DefWindowProcA(h, 0x88, 0, 0);                                        /* WM_SYNCPAINT: the erase is done now */
    CHECK_EQ(count(h, 0x14), 1);
    g_erase_result = 1;
    DestroyWindow(h);
}

TEST(u32c_erase_background_brushes)
{
    reg("U32Bkg", 0, (HBRUSH)(uintptr_t)(5 + 1));                          /* COLOR_WINDOW */
    reg("U32Bk0");
    HWND h = mkw("U32Bkg", POPUP | VIS, 0, 0, 40, 40), n = mkw("U32Bk0", POPUP | VIS, 100, 0, 40, 40);
    g_erase_result = -1;
    pump();
    HDC dc = GetDC(h), dn = GetDC(n);
    CHECK_EQ(DefWindowProcA(h, 0x14, (WPARAM)(uintptr_t)dc, 0), 1);
    CHECK_EQ(GetPixel(dc, 3, 3), 0xFFFFFF);
    CHECK_EQ(DefWindowProcA(n, 0x14, (WPARAM)(uintptr_t)dn, 0), 0);        /* no class brush: not erased */
    CHECK_EQ(DefWindowProcA(h, 0x27, (WPARAM)(uintptr_t)dc, 0), 1);        /* WM_ICONERASEBKGND the same */
    ReleaseDC(h, dc); ReleaseDC(n, dn);
    g_erase_result = 1;
    DestroyWindow(h); DestroyWindow(n);
}

TEST(u32c_ctlcolor_defaults)
{
    reg("U32Ctl");
    HWND h = mkw("U32Ctl", POPUP | VIS, 0, 0, 40, 40);
    HDC dc = GetDC(h);
    LRESULT b1 = DefWindowProcA(h, 0x133, (WPARAM)(uintptr_t)dc, 0), b2 = DefWindowProcA(h, 0x138, (WPARAM)(uintptr_t)dc, 0);
    CHECK(b1 != 0 && b2 != 0 && b1 != b2);                                 /* COLOR_WINDOW brush for an edit, COLOR_3DFACE for static text */
    CHECK_EQ(DefWindowProcA(h, 0x133, (WPARAM)(uintptr_t)dc, 0), b1);      /* the system brushes are shared */
    CHECK_EQ(DefWindowProcA(h, 0x134, (WPARAM)(uintptr_t)dc, 0), b1);
    CHECK_EQ(DefWindowProcA(h, 0x135, (WPARAM)(uintptr_t)dc, 0), b2);
    CHECK_EQ(DefWindowProcA(h, 0x19, (WPARAM)(uintptr_t)dc, 3 << 16), b2); /* the 16-bit form carries the type in the high word */
    ReleaseDC(h, dc);
    DestroyWindow(h);
}

TEST(u32c_wpchanging_track_sizes)
{
    reg("U32Trk");
    HWND h = mkw("U32Trk", OVL | VIS, 0, 0, 20, 20);               /* an overlapped window is kept above the minimum track size */
    RECT wr;
    GetWindowRect(h, &wr);
    CHECK_EQ(wr.right - wr.left, GetSystemMetrics(34));                   /* SM_CXMINTRACK */
    CHECK_EQ(wr.bottom - wr.top, GetSystemMetrics(35));
    SetWindowPos(h, NULL, 0, 0, 20000, 20000, 0x2 | 0x4 | 0x10);         /* and below the maximum */
    GetWindowRect(h, &wr);
    CHECK_EQ(wr.right - wr.left, GetSystemMetrics(59)); CHECK_EQ(wr.bottom - wr.top, GetSystemMetrics(60));
    DestroyWindow(h);
    HWND p = mkw("U32Trk", POPUP | VIS, 0, 0, 20, 20);                    /* a pop-up without a sizing border is not limited */
    GetWindowRect(p, &wr);
    CHECK_EQ(wr.right - wr.left, 20);
    DestroyWindow(p);
}

TEST(u32c_enum_display_devices_reference_strings)
{
    struct DD { uint32_t cb; char name[32], str[128]; uint32_t flags; char id[128], key[128]; } d;
    memset(&d, 0, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA(NULL, 0, &d, 0));
    CHECK_EQ(d.flags, 0x1 | 0x4 | 0x10);                    /* ATTACHED_TO_DESKTOP | PRIMARY_DEVICE | VGA_COMPATIBLE */
    CHECK(!strncmp(d.id, "PCI\\VEN_", 8) && strstr(d.id, "&DEV_") && strstr(d.id, "&SUBSYS_") && strstr(d.id, "&REV_"));
    memset(&d, 0, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA("\\\\.\\DISPLAY1", 0, &d, 0));
    CHECK_STR(d.str, "Generic Non-PnP Monitor");
    CHECK_STR(d.id, "MONITOR\\Default_Monitor\\{4d36e96e-e325-11ce-bfc1-08002be10318}\\0000");
    CHECK(strstr(d.key, "\\Registry\\Machine\\System\\CurrentControlSet\\Control\\Class\\{4d36e96e-e325-11ce-bfc1-08002be10318}\\0000"));
    memset(&d, 0, sizeof d); d.cb = sizeof d;
    CHECK(EnumDisplayDevicesA("\\\\.\\DISPLAY1", 0, &d, 1));
    CHECK_STR(d.id, "\\\\?\\DISPLAY#Default_Monitor#0000&0000#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}");
}
