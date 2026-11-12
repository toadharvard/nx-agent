/* test_u32_contract.cpp - user32 / gdi32 imports brought to their full contract (W21): the DefWindowProc rule table, update
 * regions and BeginPaint, keyboard focus, EnumDisplayDevicesA strings. Contract: MSDN; reference: Wine 9.0 win32u (defwnd.c,
 * sysparams.c) read as data. */
#include "s4_test.h"

#include <stdint.h>
#include <stdlib.h>
#include <string>
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

/* ---- group 7: CreateDIBSection over a section, GetDIBits */
extern "C" {
HBITMAP CreateDIBSection(HDC, const void *, UINT, void **, HANDLE, DWORD);
int GetDIBits(HDC, HBITMAP, UINT, UINT, void *, void *, UINT);
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
BOOL DeleteObject(HGDIOBJ);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
HANDLE CreateFileMappingA(HANDLE, void *, DWORD, DWORD, DWORD, LPCSTR);
LPVOID MapViewOfFile(HANDLE, DWORD, DWORD, DWORD, SIZE_T);
BOOL UnmapViewOfFile(LPCVOID);
BOOL CloseHandle(HANDLE);
}
namespace {
struct BI { uint8_t b[40 + 1024]; };
void w32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
uint32_t r32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
void mkbi(BI &x, int w, int h, int bpp, uint32_t comp = 0)
{
    memset(&x, 0, sizeof x);
    w32(x.b, 40); w32(x.b + 4, (uint32_t)w); w32(x.b + 8, (uint32_t)h); x.b[12] = 1; x.b[14] = (uint8_t)bpp; w32(x.b + 16, comp);
}
uint32_t quad(const BI &x, int i) { return r32(x.b + 40 + 4 * i) & 0xFFFFFF; }
}

TEST(u32c_dibsection_over_section)
{
    BI bi;
    mkbi(bi, 4, 4, 32);
    HANDLE sec = CreateFileMappingA((HANDLE)(intptr_t)-1, NULL, PAGE_READWRITE, 0, 0x20000, NULL);
    CHECK(sec != NULL);
    void *bits = NULL;
    HBITMAP bm = CreateDIBSection(NULL, &bi, 0, &bits, sec, 0x10008);       /* a section offset above the allocation granularity, dword aligned */
    CHECK(bm != NULL && bits != NULL);
    CHECK_EQ(((uintptr_t)bits) & 3, 0);
    if (bits) memset(bits, 0xAB, 64);
    void *view = MapViewOfFile(sec, 0x4 /* FILE_MAP_READ */, 0, 0x10000, 0x1000);
    CHECK(view != NULL);
    if (view) CHECK_EQ(((uint8_t *)view)[8], 0xAB);                         /* the bits live in the section */
    if (view && bits) ((uint8_t *)bits)[0] = 0x5A;
    if (view) CHECK_EQ(((uint8_t *)view)[8], 0x5A);
    CloseHandle(sec);                                                        /* the bitmap keeps the section referenced */
    if (bits) CHECK_EQ(((uint8_t *)bits)[0], 0x5A);
    HDC dc = CreateCompatibleDC(NULL);
    HGDIOBJ old = SelectObject(dc, bm);
    CHECK(old != NULL);
    SelectObject(dc, old); DeleteDC(dc);
    CHECK(DeleteObject(bm));
    if (view) UnmapViewOfFile(view);
    /* failures: offset not a multiple of 4, no room, a read-only section, a handle that is not a section */
    sec = CreateFileMappingA((HANDLE)(intptr_t)-1, NULL, PAGE_READWRITE, 0, 0x1000, NULL);
    SetLastError(0);
    bits = (void *)1;
    CHECK(CreateDIBSection(NULL, &bi, 0, &bits, sec, 2) == NULL); CHECK_EQ(GetLastError(), 87); CHECK(bits == NULL);
    mkbi(bi, 64, 64, 32);                                                    /* 16 KiB do not fit a 4 KiB section */
    CHECK(CreateDIBSection(NULL, &bi, 0, &bits, sec, 0) == NULL);
    mkbi(bi, 4, 4, 32);
    CHECK(CreateDIBSection(NULL, &bi, 0, &bits, sec, 0x1000) == NULL);       /* starts at the end */
    HBITMAP ok = CreateDIBSection(NULL, &bi, 0, &bits, sec, 0xFC0);          /* ends exactly at the end */
    CHECK(ok != NULL);
    if (ok) DeleteObject(ok);
    CHECK(CreateDIBSection(NULL, &bi, 0, &bits, (HANDLE)(uintptr_t)0x1234, 0) == NULL);
    CloseHandle(sec);
    HANDLE ro = CreateFileMappingA((HANDLE)(intptr_t)-1, NULL, PAGE_READONLY, 0, 0x1000, NULL);
    CHECK(CreateDIBSection(NULL, &bi, 0, &bits, ro, 0) == NULL);             /* read-only sections are not supported */
    CloseHandle(ro);
    HANDLE cow = CreateFileMappingA((HANDLE)(intptr_t)-1, NULL, PAGE_WRITECOPY, 0, 0x1000, NULL);
    HBITMAP cb = CreateDIBSection(NULL, &bi, 0, &bits, cow, 0);              /* PAGE_WRITECOPY is accepted */
    CHECK(cb != NULL);
    if (cb) DeleteObject(cb);
    CloseHandle(cow);
}

TEST(u32c_dibsection_core_header)
{
    uint8_t core[12 + 3 * 2];
    memset(core, 0, sizeof core);
    w32(core, 12); core[4] = 3; core[6] = 2; core[8] = 1; core[10] = 1;      /* a 3 x 2, 1-bit BITMAPCOREHEADER */
    core[12] = 0x10; core[13] = 0x20; core[14] = 0x30; core[15] = 0xA0; core[16] = 0xB0; core[17] = 0xC0;   /* RGBTRIPLE: blue, green, red */
    void *bits = NULL;
    HBITMAP bm = CreateDIBSection(NULL, core, 0, &bits, NULL, 0);
    CHECK(bm != NULL && bits != NULL);
    HDC dc = CreateCompatibleDC(NULL);
    BI out; mkbi(out, 0, 0, 0);
    CHECK_EQ(GetDIBits(dc, bm, 0, 0, NULL, out.b, 0), 2);                    /* described: the height */
    CHECK_EQ(r32(out.b + 4), 3u); CHECK_EQ(r32(out.b + 8), 2u); CHECK_EQ(out.b[14], 1);
    BI q; mkbi(q, 3, 2, 1);
    uint8_t px[16];
    CHECK_EQ(GetDIBits(dc, bm, 0, 2, px, q.b, 0), 2);
    CHECK_EQ(quad(q, 0), 0x302010u);                                         /* triple: B=0x10 G=0x20 R=0x30 */
    CHECK_EQ(quad(q, 1), 0xC0B0A0u);
    DeleteObject(bm); DeleteDC(dc);
    core[6] = 0;
    CHECK(CreateDIBSection(NULL, core, 0, &bits, NULL, 0) == NULL);          /* a zero height */
}

TEST(u32c_getdibits_default_tables_and_pal_colors)
{
    HDC dc = CreateCompatibleDC(NULL);
    BI src; mkbi(src, 2, 1, 32);
    void *bits;
    HBITMAP bm = CreateDIBSection(NULL, &src, 0, &bits, NULL, 0);
    ((uint32_t *)bits)[0] = 0xFF0000; ((uint32_t *)bits)[1] = 0x0000FF;
    uint8_t buf[64];
    BI t; mkbi(t, 2, 1, 8);                                                  /* 8 bits: the 256-colour default table */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, t.b, 0), 1);
    CHECK_EQ(quad(t, 0), 0x000000u); CHECK_EQ(quad(t, 1), 0x800000u); CHECK_EQ(quad(t, 7), 0xC0C0C0u); CHECK_EQ(quad(t, 8), 0xC0DCC0u);
    CHECK_EQ(quad(t, 9), 0xA6CAF0u);
    CHECK_EQ(quad(t, 10), 0x402000u);                                        /* lattice entry 10: red 0x40, green 0x20, blue 0 */
    CHECK_EQ(quad(t, 64), 0x000040u);                                        /* blue steps by 0x40 every 64 entries */
    CHECK_EQ(quad(t, 246), 0xFFFBF0u); CHECK_EQ(quad(t, 249), 0xFF0000u); CHECK_EQ(quad(t, 255), 0xFFFFFFu);
    CHECK_EQ(quad(t, 100), (uint32_t)((4 * 0x20) << 16 | (4 * 0x20) << 8 | 1 * 0x40));
    CHECK_EQ(buf[0], 249); CHECK_EQ(buf[1], 252);                            /* red, blue: exact entries */
    BI f; mkbi(f, 2, 1, 4);                                                  /* 4 bits: the 16 VGA colours in the reference's order */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, f.b, 0), 1);
    CHECK_EQ(quad(f, 7), 0x808080u); CHECK_EQ(quad(f, 8), 0xC0C0C0u); CHECK_EQ(quad(f, 9), 0xFF0000u); CHECK_EQ(quad(f, 12), 0x0000FFu);
    BI m; mkbi(m, 2, 1, 1);
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, m.b, 0), 1);
    CHECK_EQ(quad(m, 0), 0u); CHECK_EQ(quad(m, 1), 0xFFFFFFu);
    /* DIB_PAL_COLORS: the table is the indices, the pixels are matched against the DC's palette (the 20 system colours) */
    BI p; mkbi(p, 2, 1, 8);
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, p.b, 1), 1);
    bool ident = true;
    for (int i = 0; i < 256; i++) { uint16_t ix; memcpy(&ix, p.b + 40 + 2 * i, 2); ident = ident && ix == i; }
    CHECK(ident);
    CHECK_EQ(buf[0], 13); CHECK_EQ(buf[1], 16);                              /* 0xFF0000 and 0x0000FF are system entries 13 and 16 */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, p.b, 2), 0);                       /* DIB_PAL_INDICES is not for GetDIBits */
    DeleteObject(bm); DeleteDC(dc);
}

TEST(u32c_getdibits_scan_lines_and_padding)
{
    HDC dc = CreateCompatibleDC(NULL);
    BI src; mkbi(src, 2, 4, 32);
    void *bits;
    HBITMAP bm = CreateDIBSection(NULL, &src, 0, &bits, NULL, 0);
    uint32_t *px = (uint32_t *)bits;                                         /* bottom-up: row 0 of memory is the bottom line */
    for (int line = 0; line < 4; line++) for (int x = 0; x < 2; x++) px[line * 2 + x] = 0x100 * (line + 1) + x;
    uint32_t out[16];
    BI q; mkbi(q, 2, 4, 32);
    memset(out, 0xEE, sizeof out);
    CHECK_EQ(GetDIBits(dc, bm, 1, 2, out, q.b, 0), 2);                       /* scan lines 1 and 2, counted from the bottom */
    CHECK_EQ(out[0], 0x200u); CHECK_EQ(out[2], 0x300u);
    CHECK_EQ(out[4], 0xEEEEEEEEu);                                           /* nothing past the block */
    mkbi(q, 2, -4, 32);                                                      /* top-down: the same block, upper line first */
    memset(out, 0xEE, sizeof out);
    CHECK_EQ(GetDIBits(dc, bm, 1, 2, out, q.b, 0), 2);
    CHECK_EQ(out[0], 0x300u); CHECK_EQ(out[2], 0x200u);
    mkbi(q, 2, 4, 32);
    CHECK_EQ(GetDIBits(dc, bm, 3, 9, out, q.b, 0), 1); CHECK_EQ(out[0], 0x400u);   /* clamped to the header's height */
    CHECK_EQ(GetDIBits(dc, bm, 4, 1, out, q.b, 0), 1);                       /* beyond the height: no bits, a description (reference: 1) */
    CHECK_EQ(GetDIBits(dc, bm, 0, 0, out, q.b, 0), 1);
    /* a header taller than the bitmap: the lines the bitmap does not have are zero and not counted */
    mkbi(q, 2, 6, 32);
    memset(out, 0xEE, sizeof out);
    CHECK_EQ(GetDIBits(dc, bm, 0, 6, out, q.b, 0), 4);
    CHECK_EQ(out[0], 0u); CHECK_EQ(out[3], 0u); CHECK_EQ(out[4], 0x100u); CHECK_EQ(out[10], 0x400u);   /* bottom-up: the missing lines lie below the bitmap, first in memory */
    /* a narrower header copies the columns that exist */
    mkbi(q, 1, 4, 32);
    memset(out, 0xEE, sizeof out);
    CHECK_EQ(GetDIBits(dc, bm, 0, 4, out, q.b, 0), 4);
    CHECK_EQ(out[0], 0x100u); CHECK_EQ(out[3], 0x400u);
    DeleteObject(bm); DeleteDC(dc);
}

TEST(u32c_getdibits_formats_and_masks)
{
    HDC dc = CreateCompatibleDC(NULL);
    BI src; mkbi(src, 1, 1, 24);
    void *bits;
    HBITMAP bm = CreateDIBSection(NULL, &src, 0, &bits, NULL, 0);
    ((uint8_t *)bits)[0] = 0xFF; ((uint8_t *)bits)[1] = 0x80; ((uint8_t *)bits)[2] = 0x00;   /* B G R */
    uint8_t buf[16];
    BI q; mkbi(q, 1, 1, 16, 3);                                              /* 16 bits with the caller's 5-6-5 masks */
    w32(q.b + 40, 0xF800); w32(q.b + 44, 0x07E0); w32(q.b + 48, 0x001F);
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 1);
    uint16_t v;
    memcpy(&v, buf, 2);
    CHECK_EQ(v, (uint16_t)(0x001F | (0x20 << 5)));                           /* blue 0xFF -> 31, green 0x80 -> 32 (6 bits), red 0 */
    mkbi(q, 1, 1, 16);                                                       /* BI_RGB 16 bits is 5-5-5 */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 1);
    memcpy(&v, buf, 2); CHECK_EQ(v, (uint16_t)(0x001F | (0x10 << 5)));
    mkbi(q, 1, 1, 24);                                                       /* the same depth: the bytes are copied */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 1);
    CHECK(buf[0] == 0xFF && buf[1] == 0x80 && buf[2] == 0x00);
    CHECK_EQ(r32(q.b + 32), 0u);                                             /* biClrUsed is returned 0 */
    CHECK_EQ(r32(q.b + 20), 4u);                                             /* biSizeImage: the padded size */
    /* invalid requests */
    mkbi(q, 1, 1, 24, 3);                                                    /* BI_BITFIELDS at 24 bits */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);
    mkbi(q, 1, 1, 16, 3);
    w32(q.b + 40, 0xFF00); w32(q.b + 44, 0x0FF0); w32(q.b + 48, 0);         /* overlapping masks */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);
    mkbi(q, 1, 1, 24, 4);                                                    /* BI_JPEG with a buffer */
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);
    mkbi(q, 0, 1, 24); CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);   /* zero width */
    mkbi(q, 1, 0, 24); CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);
    mkbi(q, 1, 1, 24); w32(q.b, 24); CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);   /* a header size that is none */
    mkbi(q, 1, 1, 24); q.b[12] = 0; CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);   /* no planes */
    /* RLE: described when no bits are asked for, never retrieved */
    mkbi(q, 1, 1, 8, 1);
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 0);
    CHECK(GetDIBits(dc, bm, 0, 0, NULL, q.b, 0) != 0);
    mkbi(q, 1, -1, 8, 1); CHECK_EQ(GetDIBits(dc, bm, 0, 0, NULL, q.b, 0), 0);               /* RLE is bottom-up only */
    mkbi(q, 1, 1, 4, 2); CHECK(GetDIBits(dc, bm, 0, 0, NULL, q.b, 0) != 0);
    mkbi(q, 1, 1, 8, 2); CHECK_EQ(GetDIBits(dc, bm, 0, 0, NULL, q.b, 0), 0);               /* BI_RLE4 is 4 bits */
    DeleteObject(bm); DeleteDC(dc);
}

TEST(u32c_getdibits_same_depth_keeps_the_table_and_masks)
{
    HDC dc = CreateCompatibleDC(NULL);
    BI src; mkbi(src, 2, 1, 8);
    w32(src.b + 40, 0x00112233); w32(src.b + 44, 0x00445566); w32(src.b + 48, 0x00778899);
    void *bits;
    HBITMAP bm = CreateDIBSection(NULL, &src, 0, &bits, NULL, 0);
    ((uint8_t *)bits)[0] = 1; ((uint8_t *)bits)[1] = 2;
    uint8_t buf[8];
    BI q; mkbi(q, 2, 1, 8);
    CHECK_EQ(GetDIBits(dc, bm, 0, 1, buf, q.b, 0), 1);                       /* same depth: the DIB's own table, the indices copied */
    CHECK_EQ(quad(q, 0), 0x112233u); CHECK_EQ(quad(q, 1), 0x445566u); CHECK_EQ(quad(q, 2), 0x778899u);
    CHECK(buf[0] == 1 && buf[1] == 2);
    BI d; mkbi(d, 1, 1, 32, 3); w32(d.b + 40, 0xFF0000); w32(d.b + 44, 0xFF00); w32(d.b + 48, 0xFF);
    void *b2;
    HBITMAP b32 = CreateDIBSection(NULL, &d, 0, &b2, NULL, 0);
    BI r; mkbi(r, 1, 1, 32);
    uint32_t px;
    CHECK_EQ(GetDIBits(dc, b32, 0, 1, &px, r.b, 0), 1);
    CHECK_EQ(r32(r.b + 40), 0xFF0000u); CHECK_EQ(r32(r.b + 44), 0xFF00u); CHECK_EQ(r32(r.b + 48), 0xFFu);   /* the masks come back */
    DeleteObject(bm); DeleteObject(b32); DeleteDC(dc);
}

/* ---- group 6: MessageBoxA, the platform's own modal dialog */
#include <atomic>
#include <chrono>
#include <thread>
extern "C" {
void fnvnx_win_key(unsigned vk, unsigned scan, int down, unsigned flags);
void fnvnx_win_mouse(int x, int y, unsigned changed, unsigned state, int wheel);
int MessageBoxA(HWND, LPCSTR, LPCSTR, UINT);
HWND FindWindowA(LPCSTR, LPCSTR);
int GetWindowTextA(HWND, LPSTR, int);
BOOL GetWindowRect(HWND, RECT *);
typedef int (*fnvnx_msgbox_hook_t)(const char *, const char *, UINT);
void fnvnx_set_msgbox_hook(fnvnx_msgbox_hook_t);
}
namespace {
struct Driver {
    std::atomic<HWND> dlg{ NULL };
    std::atomic<bool> seen{ false };
    char title[64] = {};
    RECT rc = {};
    bool active_when_seen = false, owner_disabled = false, topmost = false;
    HWND owner = NULL;
    unsigned color_at_5_5 = 0;
};
void sleep_ms(int ms) { std::this_thread::sleep_for(std::chrono::milliseconds(ms)); }
HWND wait_dialog(Driver &d)
{
    for (int i = 0; i < 400 && !d.seen; i++) {
        HWND h = FindWindowA("FnvnxMessageBox", NULL);
        if (h && IsWindowVisible(h) && GetActiveWindow() == h) { sleep_ms(60); d.dlg = h; d.seen = true; break; }
        sleep_ms(5);
    }
    if (d.dlg.load()) {
        GetWindowTextA(d.dlg, d.title, sizeof d.title);
        GetWindowRect(d.dlg, &d.rc);
        d.active_when_seen = GetActiveWindow() == d.dlg;
        d.topmost = (GetWindowLongA(d.dlg, -20) & 8) != 0;
        if (d.owner) d.owner_disabled = (GetWindowLongA(d.owner, -16) & 0x08000000) != 0;
        HDC dc = GetDC(d.dlg);
        d.color_at_5_5 = GetPixel(dc, 5, 5);
        ReleaseDC(d.dlg, dc);
    }
    return d.dlg;
}
void key(unsigned vk) { fnvnx_win_key(vk, 0, 1, 0); sleep_ms(15); fnvnx_win_key(vk, 0, 0, 0); sleep_ms(15); }
void click(int x, int y)
{
    fnvnx_win_mouse(x, y, 0, 0, 0); sleep_ms(10);
    fnvnx_win_mouse(x, y, 1, 1, 0); sleep_ms(10);
    fnvnx_win_mouse(x, y, 1, 0, 0); sleep_ms(10);
}
/* a box of `type` with keys sent by `act` once it is up; returns what MessageBoxA returned */
template <class F> int run_box(UINT type, const char *text, const char *cap, Driver &d, F act, HWND owner = NULL)
{
    d.owner = owner;
    test_thread t([&] { if (wait_dialog(d)) act(d); });
    int r = MessageBoxA(owner, text, cap, type);
    t.join();
    return r;
}
}

TEST(u32c_messagebox_returns_and_keys)
{
    fnvnx_set_msgbox_hook(NULL);
    Driver d;
    auto ret = [&](UINT type, std::vector<unsigned> keys, const char *cap = "Cap") {
        Driver dd;
        int r = run_box(type, "A text that is long enough to need no wrapping at all.", cap, dd, [&](Driver &) { for (unsigned k : keys) key(k); });
        return r;
    };
    /* Return presses the default button, per set and MB_DEFBUTTON */
    CHECK_EQ(ret(0 /* MB_OK */, { 0x0D }), 1);
    CHECK_EQ(ret(1 /* OKCANCEL */, { 0x0D }), 1);
    CHECK_EQ(ret(1 | 0x100, { 0x0D }), 2);
    CHECK_EQ(ret(2 /* ABORTRETRYIGNORE */, { 0x0D }), 3);
    CHECK_EQ(ret(2 | 0x100, { 0x0D }), 4);
    CHECK_EQ(ret(2 | 0x200, { 0x0D }), 5);
    CHECK_EQ(ret(3 /* YESNOCANCEL */, { 0x0D }), 6);
    CHECK_EQ(ret(3 | 0x200, { 0x0D }), 2);
    CHECK_EQ(ret(4 /* YESNO */, { 0x0D }), 6);
    CHECK_EQ(ret(4 | 0x100, { 0x0D }), 7);
    CHECK_EQ(ret(5 /* RETRYCANCEL */, { 0x0D }), 4);
    CHECK_EQ(ret(6 /* CANCELTRYCONTINUE */, { 0x0D }), 2);
    CHECK_EQ(ret(6 | 0x100, { 0x0D }), 10);
    CHECK_EQ(ret(6 | 0x200, { 0x0D }), 11);
    CHECK_EQ(ret(4 | 0x300, { 0x0D }), 6);                  /* a default button that does not exist: the first */
    /* Tab, Shift+Tab, arrows move the focus; Space presses */
    CHECK_EQ(ret(2, { 0x09, 0x0D }), 4);
    CHECK_EQ(ret(2, { 0x09, 0x09, 0x0D }), 5);
    CHECK_EQ(ret(2, { 0x09, 0x09, 0x09, 0x0D }), 3);        /* wraps around */
    CHECK_EQ(ret(2, { 0x27, 0x27, 0x20 }), 5);              /* right, right, space */
    CHECK_EQ(ret(2, { 0x25, 0x0D }), 5);                    /* left from the first wraps to the last */
    CHECK_EQ(ret(3, { 0x28, 0x0D }), 7);
    /* mnemonics */
    CHECK_EQ(ret(2, { 'I' }), 5);
    CHECK_EQ(ret(2, { 'R' }), 4);
    CHECK_EQ(ret(4, { 'N' }), 7);
    CHECK_EQ(ret(6, { 'T' }), 10);
    /* Escape: IDCANCEL if there is a Cancel button, IDOK if OK is the only one, else no effect */
    CHECK_EQ(ret(0, { 0x1B }), 1);
    CHECK_EQ(ret(1, { 0x1B }), 2);
    CHECK_EQ(ret(3, { 0x1B }), 2);
    CHECK_EQ(ret(5, { 0x1B }), 2);
    CHECK_EQ(ret(6, { 0x1B }), 2);
    CHECK_EQ(ret(4, { 0x1B, 0x1B, 0x0D }), 6);               /* YESNO: Escape does nothing, Return then answers */
    CHECK_EQ(ret(2, { 0x1B, 'R' }), 4);                      /* ABORTRETRYIGNORE: the same */
    /* the box as a window: the default title, the owner, topmost, painted */
    Driver e;
    int r = run_box(0, "text", NULL, e, [&](Driver &x) { key(0x0D); (void)x; });
    CHECK_EQ(r, 1);
    CHECK_STR(e.title, "Error");
    CHECK(e.active_when_seen);
    CHECK_EQ(e.color_at_5_5, 0x6A240Au);                     /* the title bar: COLOR_ACTIVECAPTION */
    CHECK(!IsWindow(e.dlg));                                 /* gone when the box returns */
    /* invalid requests fail with 0 */
    SetLastError(0);
    CHECK_EQ(MessageBoxA(NULL, "x", "y", 7), 0); CHECK_EQ(GetLastError(), 87);
    CHECK_EQ(MessageBoxA(NULL, "x", "y", 15), 0);
    SetLastError(0);
    CHECK_EQ(MessageBoxA((HWND)(uintptr_t)0x4444, "x", "y", 0), 0); CHECK_EQ(GetLastError(), 1400);
    CHECK_EQ(MessageBoxA((HWND)(uintptr_t)0x4444 /* an owner with MB_SERVICE_NOTIFICATION */, "x", "y", 0x00200000), 0);
}

TEST(u32c_messagebox_mouse_help_and_modality)
{
    fnvnx_set_msgbox_hook(NULL);
    reg("U32MbOwner");
    HWND owner = mkw("U32MbOwner", POPUP | VIS, 0, 0, 300, 200);
    pump();
    /* the pointer: down and up on the button; the box is centred, the buttons sit at the bottom */
    Driver d;
    int r = run_box(4 /* YESNO */, "Question?", "Q", d, [&](Driver &x) {
        int cx = (int)((x.rc.left + x.rc.right) / 2), by = (int)(x.rc.bottom - 24);
        click(cx + 40, by);                                  /* the second button: No */
    }, owner);
    CHECK_EQ(r, 7);
    CHECK(d.owner_disabled);                                 /* MB_APPLMODAL: the owner is disabled while the box is up */
    CHECK((GetWindowLongA(owner, -16) & 0x08000000) == 0);   /* and enabled again after */
    CHECK(GetActiveWindow() == owner);                        /* and active again */
    /* the owner is not told about keys while the box is up; a click elsewhere does nothing */
    Driver c;
    r = run_box(1 /* OKCANCEL */, "x", "c", c, [&](Driver &x) {
        click(5, 5);                                         /* a click outside the buttons: nothing */
        click(1, 1);
        sleep_ms(30);
        key(0x0D);
        (void)x;
    });
    CHECK_EQ(r, 1);
    /* a press that is released elsewhere is not a press */
    Driver p;
    r = run_box(4, "x", "p", p, [&](Driver &x) {
        int cx = (int)((x.rc.left + x.rc.right) / 2), by = (int)(x.rc.bottom - 24);
        fnvnx_win_mouse(cx - 40, by, 0, 0, 0); sleep_ms(10);
        fnvnx_win_mouse(cx - 40, by, 1, 1, 0); sleep_ms(10);   /* down on Yes */
        fnvnx_win_mouse(cx + 40, by, 0, 1, 0); sleep_ms(10);   /* dragged to No */
        fnvnx_win_mouse(cx + 40, by, 1, 0, 0); sleep_ms(10);   /* released there: neither is pressed */
        key(0x0D);                                             /* the focus is on Yes (pressed it) */
    });
    CHECK_EQ(r, 6);
    /* MB_HELP: F1 and the Help button send WM_HELP to the owner and the box goes on */
    g_ev.clear();
    Driver h;
    r = run_box(0 | 0x4000 /* MB_HELP */, "help me", "h", h, [&](Driver &) { key(0x70); key(0x70); key(0x0D); }, owner);
    CHECK_EQ(r, 1);
    CHECK_EQ(count(owner, 0x53), 2);
    /* MB_TOPMOST and MB_SYSTEMMODAL are topmost; MB_TASKMODAL without an owner disables the thread's windows */
    Driver t;
    r = run_box(0 | 0x40000, "x", "t", t, [&](Driver &) { key(0x0D); });
    CHECK(t.topmost);
    Driver s;
    r = run_box(0 | 0x1000, "x", "s", s, [&](Driver &) { key(0x0D); });
    CHECK(s.topmost);
    Driver n;
    r = run_box(0, "x", "n", n, [&](Driver &) { key(0x0D); });
    CHECK(!n.topmost);
    Driver tm;
    HWND probe = owner;
    bool disabled_during = false;
    r = run_box(0 | 0x2000, "x", "tm", tm, [&](Driver &) { disabled_during = (GetWindowLongA(probe, -16) & 0x08000000) != 0; key(0x0D); });
    CHECK(disabled_during);
    CHECK((GetWindowLongA(owner, -16) & 0x08000000) == 0);
    DestroyWindow(owner);
}

TEST(u32c_messagebox_hook_answers)
{
    static int calls;
    calls = 0;
    fnvnx_set_msgbox_hook([](const char *t, const char *c, UINT ty) -> int { calls++; (void)t; (void)c; return 100 + (int)(ty & 0xF); });
    CHECK_EQ(MessageBoxA(NULL, "a", "b", 4), 104);
    CHECK_EQ(MessageBoxA(NULL, "a", "b", 0), 100);
    CHECK_EQ(calls, 2);
    fnvnx_set_msgbox_hook(NULL);
}
