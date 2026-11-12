/* window.cpp - user32: window classes, the window tree with geometry and state, a thread-agnostic message queue,
 * cursor/key state.
 *
 * Model: one desktop (GetSystemMetrics SM_CXSCREEN x SM_CYSCREEN) is the root of the window tree; every window is
 * a record (class, procedure, styles, text, extra bytes, window rectangle in screen coordinates, z-ordered children).
 * The client rectangle is derived from the styles by ncrule(), the one rule AdjustWindowRectEx also uses.
 * The queues, SendMessage/PostMessage/PeekMessage, input, hooks and the cursor are in msgqueue.cpp (shared: window_int.h);
 * the input stream (S7) injects key state through fnvnx_set_key_state, the renderer (S6) sets the display size.
 * Locking: g_ui_lock guards the tree, is never held across a window procedure call and is not recursive; the *_l
 * helpers expect it held. */
#include "window_int.h"
#include "fnvnx_boundary.h"

#include <stdio.h>
#include <stdlib.h>
#include <vector>
#include <string>

/* the DC side of painting (gdi32.cpp) and the text colours (gdifont.cpp) */
extern "C" {
HDC BeginPaint(HWND, void *);
BOOL EndPaint(HWND, const void *);
int gdi_dc_clipbox(void *hdc, RECT *out);
int gdi_fill_rect(void *hdc, const RECT *rc, void *hbr);
void gdi_dc_set_clip(void *hdc, const RECT *r, int n);
COLORREF gdi_syscolor(int index);
HBRUSH gdi_sys_brush(int index);
COLORREF SetTextColor(HDC, COLORREF);
COLORREF SetBkColor(HDC, COLORREF);
HDC GetDC(HWND);
int ReleaseDC(HWND, HDC);
int fnmq_get_message(MSG *m, HWND f, UINT lo, UINT hi);
void fnwin_erase_now(HWND h, bool children);
}

/* DefWindowProcA is the window procedure the platform owns. The game sees its export value (the import key in the
 * product), wherever a procedure is read back (GetWindowLong/GetClassLong), and a procedure it hands in that equals
 * that value is the platform's default again (stored as NULL): one rule for classes, windows and subclassing. */
extern "C" LRESULT DefWindowProcA(HWND h, UINT m, WPARAM w, LPARAM l);
static uintptr_t defproc_value(void)
{
    return (uintptr_t)fnvnx_bnd_export_value("user32.dll", "DefWindowProcA", FNVNX_BOUNDARY_BY_NAME, (const void *)DefWindowProcA);
}
static WNDPROC proc_in(uintptr_t v) { return v == defproc_value() ? NULL : (WNDPROC)v; }
static uintptr_t proc_out(WNDPROC p) { return p ? (uintptr_t)p : defproc_value(); }

#define S_CHILD 0x40000000u
#define S_POPUP 0x80000000u
#define S_VISIBLE 0x10000000u
#define S_MINIMIZE 0x20000000u
#define S_MAXIMIZE 0x01000000u
#define S_DISABLED 0x08000000u
#define S_CAPTION 0x00C00000u
#define S_BORDER 0x00800000u
#define S_DLGFRAME 0x00400000u
#define S_THICK 0x00040000u
#define X_DLGMODAL 0x1u
#define X_TOPMOST 0x8u
#define X_WINDOWEDGE 0x100u
#define X_CLIENTEDGE 0x200u
#define X_STATICEDGE 0x20000u
#define X_NOACTIVATE 0x08000000u
#define P_NOSIZE 0x1u
#define P_NOMOVE 0x2u
#define P_NOZORDER 0x4u
#define P_NOREDRAW 0x8u
#define P_NOACTIVATE 0x10u
#define P_FRAMECHANGED 0x20u
#define P_SHOWWINDOW 0x40u
#define P_HIDEWINDOW 0x80u
#define P_NOSENDCHANGING 0x400u
#define P_FORCE 0x80000000u    /* internal: report size and move as changed (creation, state changes) */
#define P_CREATE 0x40000000u   /* internal: WM_NCCALCSIZE was already sent */
#define M_CREATE 0x01
#define M_MOVE 0x03
#define M_SIZE 0x05
#define M_ACTIVATE 0x06
#define M_SETFOCUS 0x07
#define M_PAINT 0x0F
#define M_SHOWWINDOW 0x18
#define M_GETMINMAXINFO 0x24
#define M_WPCHANGING 0x46
#define M_WPCHANGED 0x47
#define M_STYLECHANGING 0x7C
#define M_STYLECHANGED 0x7D
#define M_NCCREATE 0x81
#define M_NCDESTROY 0x82
#define M_NCCALCSIZE 0x83
#define M_NCACTIVATE 0x86
#define FN_CW_USEDEFAULT ((int)0x80000000)
#define FN_MODULE ((HINSTANCE)(uintptr_t)0x00400000)   /* the exe: hInstance NULL means this */
/* structures with the pointer width of the build (the guest is ILP32) */
struct fnv_createstruct { LPVOID params; HINSTANCE inst; HMENU menu; HWND parent; int cy, cx, y, x; LONG style; LPCSTR name, cls; DWORD ex; };
struct fn_wpos { HWND hwnd, after; int x, y, cx, cy; UINT flags; };
struct fn_nccs { RECT rgrc[3]; fn_wpos *lppos; };
struct fn_mmi { POINT reserved, maxsize, maxpos, mintrack, maxtrack; };
struct fn_stylestruct { DWORD old, now; };

struct wclass {
    wclass *next;
    char *name;
    WNDPROC proc;
    ATOM atom;
    UINT style;
    int cbcls, cbwnd, nwin, sys, menu_own;
    LRESULT (*hproc)(HWND, UINT, WPARAM, LPARAM);   /* a window procedure of the platform itself (its own dialogs): a host function */
    HINSTANCE inst;
    HICON icon, iconsm;
    HCURSOR cursor;
    HBRUSH bg;
    uintptr_t menu;
    uint8_t *extra;
};
struct window {
    uint32_t magic;
    wclass *cls;
    WNDPROC proc;
    DWORD style, exstyle, tid;
    LONG userdata, id;
    HINSTANCE inst;
    window *lastpop;                                             /* top-level unowned window: its most recently active popup (NULL: itself) */
    window *parent, *owner, *first, *last, *next, *prev, *all;   /* first = top of the z-order, next = below */
    RECT wr, rr;                                                 /* window rectangle in screen coordinates; normal-state rectangle */
    int restore_max, paint, destroying, hasmenu;
    char *text;
    uint8_t *extra;
    LRESULT (*hproc)(HWND, UINT, WPARAM, LPARAM);                /* copied from the class; the game's subclassing replaces it */
    HICON icon_big, icon_small;                                  /* WM_SETICON (ICON_BIG, ICON_SMALL) */
    int ncactive, owned_hidden, nc_pending, erase;               /* WIN_NCACTIVATED, WIN_NEEDS_SHOW_OWNEDPOPUP, frame and background to paint */
    RECT *upd;                                                   /* the update region: disjoint rectangles, client coordinates */
    int nupd, cupd;
};
#define WND_MAGIC 0x574E4457u
static wclass *g_classes;
static unsigned g_next_atom = 0xC000;
static window g_desk, *g_all, *g_all_tail, *g_active, *g_capture, *g_focus, *g_focus_saved;   /* g_capture: the window that has the mouse (SetCapture) */
static os_lock_t g_ui_lock;
/* w became the active window: it is the last active popup of the unowned top-level window at the end of its owner chain */
static void note_active_l(window *w)
{
    window *r = w;
    while (r->owner && r->owner != &g_desk)
        r = r->owner;
    if (r != w && !(r->style & S_CHILD))
        r->lastpop = w;
}

static char *dup_str(const char *s)
{
    size_t n = s ? strlen(s) : 0;
    char *p = (char *)malloc(n + 1);
    if (p) { if (n) memcpy(p, s, n); p[n] = 0; }
    return p;
}
static bool hw_is(HWND h, long v) { return (uintptr_t)h == (uintptr_t)(intptr_t)v || (uintptr_t)h == (uintptr_t)(uint32_t)v; }
static HINSTANCE norm_inst(HINSTANCE i) { return i ? i : FN_MODULE; }
static bool cls_match(const wclass *c, LPCSTR n) { return (uintptr_t)n <= 0xFFFF ? c->atom == (ATOM)(uintptr_t)n : !w32_stricmp(c->name, n); }

/* "#n" with n in 1..0xBFFF names the integer atom n (Windows integer atoms); 0 if the string is not of that form */
static unsigned hash_atom(const char *n)
{
    if (!n || n[0] != '#' || n[1] < '1' || n[1] > '9')
        return 0;
    unsigned v = 0;
    for (const char *p = n + 1; *p; p++) {
        if (*p < '0' || *p > '9' || (v = v * 10 + (unsigned)(*p - '0')) > 0xBFFF)
            return 0;
    }
    return v;
}
static wclass *new_class_l(const char *name, WNDPROC proc, UINT style, int cbcls, int cbwnd, HINSTANCE inst, int sys)
{
    unsigned fixed = hash_atom(name);
    if (!fixed && g_next_atom > 0xFFFF)
        return NULL;
    wclass *c = (wclass *)calloc(1, sizeof *c);
    if (c) {
        c->name = dup_str(name);
        c->extra = (uint8_t *)calloc(1, (size_t)cbcls + 1);
    }
    if (!c || !c->name || !c->extra) {
        if (c) { free(c->name); free(c->extra); free(c); }
        return NULL;
    }
    c->proc = proc; c->style = style; c->cbcls = cbcls; c->cbwnd = cbwnd; c->inst = inst; c->sys = sys;
    c->atom = (ATOM)(fixed ? fixed : g_next_atom++);   /* string classes get 0xC000.. */
    c->next = g_classes;
    g_classes = c;
    return c;
}
/* a window class of the platform's own (a dialog it draws itself): global, its procedure a host function */
extern "C" ATOM fnwin_register_host_class(const char *name, LRESULT (*proc)(HWND, UINT, WPARAM, LPARAM), UINT style)
{
    os_lock(&g_ui_lock);
    wclass *c = new_class_l(name, NULL, style, 0, 0, NULL, 1);
    if (c) c->hproc = proc;
    ATOM a = c ? c->atom : 0;
    os_unlock(&g_ui_lock);
    return a;
}
/* the predefined classes (DefWindowProc-backed, global) and the desktop window, made on first use */
static void ui_init_l(void)
{
    if (g_desk.magic)
        return;
    /* The desktop's class is the only predefined class the platform has. BUTTON, STATIC, EDIT, COMBOBOX, LISTBOX, SCROLLBAR,
     * MDICLIENT and #32770 are NOT registered: their window procedures (the full message contract of each control) are not
     * implemented, and a class that only forwarded to DefWindowProc would be a window that looks like a control and is not
     * one. CreateWindowEx of such a class fails with ERROR_CANNOT_FIND_WND_CLASS (1407), the documented answer for a class
     * that is not registered. */
    wclass *dc = new_class_l("#32769", NULL, 0, 0, 0, NULL, 1);
    g_desk.cls = dc;
    g_desk.style = S_POPUP | S_VISIBLE;
    g_desk.wr.right = g_desk.rr.right = fnvnx_display_w();
    g_desk.wr.bottom = g_desk.rr.bottom = fnvnx_display_h();
    g_desk.magic = WND_MAGIC;
}
static window *wnd_l(HWND h)
{
    window *w = (window *)h;
    if (!w)
        return NULL;
    if (w == &g_desk)
        return w;
    for (window *p = g_all; p; p = p->all)
        if (p == w)
            return w;
    return NULL;
}
static window *wnd(HWND h)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    window *w = wnd_l(h);
    os_unlock(&g_ui_lock);
    return w;
}
static window *wnd_e(HWND h)   /* wnd() that sets 1400 */
{
    window *w = wnd(h);
    if (!w)
        SetLastError(1400);
    return w;
}

extern "C" HWND fnvnx_main_window(void)
{
    os_lock(&g_ui_lock);
    HWND h = (HWND)g_all;
    os_unlock(&g_ui_lock);
    return h;
}
extern "C" HWND fnvnx_desktop_window(void) { os_lock(&g_ui_lock); ui_init_l(); os_unlock(&g_ui_lock); return (HWND)&g_desk; }
extern "C" void fnwin_desktop_resize(void)   /* the display's size is display.cpp's; the desktop window follows it */
{
    os_lock(&g_ui_lock);
    g_desk.wr.right = g_desk.rr.right = fnvnx_display_w();
    g_desk.wr.bottom = g_desk.rr.bottom = fnvnx_display_h();
    os_unlock(&g_ui_lock);
}
/* ---- classes */
static wclass *find_class_l(LPCSTR n, HINSTANCE inst)   /* the application's class of that module first, then the global ones */
{
    for (int sys = 0; sys < 2; sys++)
        for (wclass *c = g_classes; c; c = c->next)
            if ((sys ? c->sys : (!c->sys && c->inst == inst)) && cls_match(c, n))
                return c;
    return NULL;
}
static void set_menu_l(wclass *c, uintptr_t m)
{
    if (c->menu_own)
        free((void *)c->menu);
    c->menu_own = m > 0xFFFF;
    c->menu = c->menu_own ? (uintptr_t)dup_str((const char *)m) : m;
}

extern "C" ATOM RegisterClassA(const WNDCLASSA *wc)
{
    if (!wc) { SetLastError(998); return 0; }
    LPCSTR n = wc->lpszClassName;
    char atomname[16];
    if (n && (uintptr_t)n <= 0xFFFF) {   /* an integer atom: the class is named "#n" */
        if ((uintptr_t)n > 0xBFFF) n = NULL;
        else { snprintf(atomname, sizeof atomname, "#%u", (unsigned)(uintptr_t)n); n = atomname; }
    }
    if (!n || !*n || strlen(n) > 255 || wc->cbClsExtra < 0 || wc->cbWndExtra < 0 ||
        wc->cbClsExtra > 0x7FFF || wc->cbWndExtra > 0x7FFF) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    os_lock(&g_ui_lock);
    ui_init_l();
    HINSTANCE inst = norm_inst(wc->hInstance);
    int err = 0;
    ATOM a = 0;
    for (wclass *c = g_classes; c; c = c->next)
        if (!c->sys && c->inst == inst && !w32_stricmp(c->name, n))
            err = 1410;
    wclass *c = err ? NULL : new_class_l(n, proc_in((uintptr_t)wc->lpfnWndProc), wc->style, wc->cbClsExtra, wc->cbWndExtra, inst, 0);
    if (c) {
        c->icon = wc->hIcon; c->cursor = wc->hCursor; c->bg = wc->hbrBackground;
        set_menu_l(c, (uintptr_t)wc->lpszMenuName);
        a = c->atom;
    } else if (!err)
        err = ERROR_NOT_ENOUGH_MEMORY;
    os_unlock(&g_ui_lock);
    if (err)
        SetLastError(err);
    return a;
}

extern "C" BOOL UnregisterClassA(LPCSTR n, HINSTANCE inst)
{
    if (!n) { SetLastError(998); return FALSE; }
    os_lock(&g_ui_lock);
    ui_init_l();
    int err = 1411;
    inst = norm_inst(inst);
    for (wclass **pp = &g_classes; *pp; pp = &(*pp)->next) {
        wclass *c = *pp;
        if (c->sys || c->inst != inst || !cls_match(c, n))
            continue;
        err = c->nwin ? 1412 : 0;
        if (!err) {
            *pp = c->next;
            if (c->menu_own) free((void *)c->menu);
            free(c->name); free(c->extra); free(c);
        }
        break;
    }
    os_unlock(&g_ui_lock);
    if (err)
        SetLastError(err);
    return !err;
}

/* GetClassLong / SetClassLong: non-negative indices address the class extra bytes, the negative ones the fields */
static DWORD class_long(HWND h, int idx, bool set, LONG v)
{
    window *w = wnd_e(h);
    if (!w)
        return 0;
    os_lock(&g_ui_lock);
    wclass *c = w->cls;
    uintptr_t old = 0;
    int err = 0;
    if (idx >= 0) {
        if (idx + 4 <= c->cbcls) {
            int32_t o, n = (int32_t)v;
            memcpy(&o, c->extra + idx, 4);
            old = (uintptr_t)(DWORD)o;
            if (set) memcpy(c->extra + idx, &n, 4);
        } else
            err = 1413;
    } else
        switch (idx) {
        case -8: old = c->menu; if (set) set_menu_l(c, (uintptr_t)v); break;                 /* GCL_MENUNAME */
        case -10: old = (uintptr_t)c->bg; if (set) c->bg = (HBRUSH)(uintptr_t)v; break;
        case -12: old = (uintptr_t)c->cursor; if (set) c->cursor = (HCURSOR)(uintptr_t)v; break;
        case -14: old = (uintptr_t)c->icon; if (set) c->icon = (HICON)(uintptr_t)v; break;
        case -16: old = (uintptr_t)c->inst; if (set) c->inst = (HINSTANCE)(uintptr_t)v; break;
        case -18: old = (uintptr_t)c->cbwnd; if (set) c->cbwnd = (int)v; break;              /* new windows only */
        case -20: old = (uintptr_t)c->cbcls; if (set) err = 1413; break;
        case -24:                                                                           /* GCL_WNDPROC: windows using the old one follow */
            old = proc_out(c->proc);
            if (set) {
                for (window *p = g_all; p; p = p->all)
                    if (p->cls == c && p->proc == c->proc) p->proc = proc_in((uintptr_t)(DWORD)v);
                c->proc = proc_in((uintptr_t)(DWORD)v);
            }
            break;
        case -26: old = c->style; if (set) c->style = (UINT)v; break;
        case -32: old = c->atom; if (set) err = 1413; break;
        case -34: old = (uintptr_t)c->iconsm; if (set) c->iconsm = (HICON)(uintptr_t)v; break;
        default: err = 1413;
        }
    os_unlock(&g_ui_lock);
    if (err) { SetLastError(err); return 0; }
    return (DWORD)old;
}
extern "C" DWORD GetClassLongA(HWND h, int idx) { return class_long(h, idx, false, 0); }
extern "C" DWORD SetClassLongA(HWND h, int idx, LONG v) { return class_long(h, idx, true, v); }

/* ---- geometry: the non-client rule (AdjustWindowRectEx and the client rectangle are both this) */
struct nc_t { int l, t, r, b; };
static nc_t ncrule(DWORD st, DWORD ex, bool menu)
{
    int bx = 0, by = 0;
    if (st & S_THICK) { bx = GetSystemMetrics(32); by = GetSystemMetrics(33); }                 /* SM_CXSIZEFRAME */
    else if ((ex & X_DLGMODAL) || (st & S_DLGFRAME)) { bx = GetSystemMetrics(7); by = GetSystemMetrics(8); }   /* SM_CXFIXEDFRAME */
    else if (st & S_BORDER) { bx = GetSystemMetrics(5); by = GetSystemMetrics(6); }             /* SM_CXBORDER */
    int edges = ((ex & X_WINDOWEDGE) != 0) + ((ex & X_CLIENTEDGE) != 0);
    bx += edges * GetSystemMetrics(45); by += edges * GetSystemMetrics(46);                      /* SM_CXEDGE */
    if (ex & X_STATICEDGE) { bx += GetSystemMetrics(5); by += GetSystemMetrics(6); }
    nc_t n = { bx, by, bx, by };
    if ((st & S_CAPTION) == S_CAPTION) n.t += GetSystemMetrics(4);                               /* SM_CYCAPTION */
    if (menu && !(st & S_CHILD)) n.t += GetSystemMetrics(15);                                    /* SM_CYMENU */
    return n;
}
static RECT client_l(window *w)   /* screen coordinates */
{
    RECT c = w->wr;
    if (w == &g_desk)
        return c;
    if (w->style & S_MINIMIZE) {
        c.right = c.left; c.bottom = c.top;
        return c;
    }
    nc_t n = ncrule(w->style, w->exstyle, w->hasmenu);
    c.left += n.l; c.top += n.t; c.right -= n.r; c.bottom -= n.b;
    if (c.right < c.left) c.right = c.left;
    if (c.bottom < c.top) c.bottom = c.top;
    return c;
}
static void origin_l(window *p, long *x, long *y)   /* where the client coordinates of children start */
{
    RECT c = (!p || p == &g_desk) ? RECT{ 0, 0, 0, 0 } : client_l(p);
    *x = c.left; *y = c.top;
}
static RECT container_l(window *w) { return (!w->parent || w->parent == &g_desk) ? g_desk.wr : client_l(w->parent); }
static void shift_l(window *w, long dx, long dy)
{
    for (window *c = w->first; c; c = c->next) {
        c->wr.left += dx; c->wr.right += dx; c->wr.top += dy; c->wr.bottom += dy;
        c->rr.left += dx; c->rr.right += dx; c->rr.top += dy; c->rr.bottom += dy;
        shift_l(c, dx, dy);
    }
}
static void fill_mmi(window *w, fn_mmi *m)
{
    int mnx = GetSystemMetrics(34), mny = GetSystemMetrics(35), mxx = GetSystemMetrics(59), mxy = GetSystemMetrics(60);   /* SM_C*MINTRACK, SM_C*MAXTRACK (outside the lock: font metrics) */
    os_lock(&g_ui_lock);
    RECT c = container_l(w);
    long b = ncrule(w->style, w->exstyle, false).l;
    memset(m, 0, sizeof *m);
    m->maxpos.x = m->maxpos.y = -b;
    m->maxsize.x = c.right - c.left + 2 * b; m->maxsize.y = c.bottom - c.top + 2 * b;
    m->mintrack.x = mnx; m->mintrack.y = mny;
    m->maxtrack.x = mxx; m->maxtrack.y = mxy;
    os_unlock(&g_ui_lock);
}

/* ---- update regions (MSDN: InvalidateRect, ValidateRect, GetUpdateRect, BeginPaint, UpdateWindow; reference: win32u painting)
 * Each window holds an update region: disjoint rectangles in its client coordinates, an erase flag (the background is to
 * be erased by WM_ERASEBKGND at the next BeginPaint) and a frame flag (WM_NCPAINT is due). A window has `paint` set iff the
 * region or the frame flag is not empty: that is what makes the queue produce WM_PAINT for it. */
typedef std::vector<RECT> rlist;
static bool rc_empty(const RECT &r) { return r.right <= r.left || r.bottom <= r.top; }
static bool rc_isect(const RECT &a, const RECT &b, RECT *o)
{
    o->left = a.left > b.left ? a.left : b.left;
    o->top = a.top > b.top ? a.top : b.top;
    o->right = a.right < b.right ? a.right : b.right;
    o->bottom = a.bottom < b.bottom ? a.bottom : b.bottom;
    return !rc_empty(*o);
}
static void rl_sub(rlist &a, const RECT &s)   /* a = a minus s, still disjoint */
{
    rlist o;
    for (const RECT &e : a) {
        RECT x;
        if (!rc_isect(e, s, &x)) { o.push_back(e); continue; }
        if (e.top < x.top) o.push_back(RECT{ e.left, e.top, e.right, x.top });
        if (x.bottom < e.bottom) o.push_back(RECT{ e.left, x.bottom, e.right, e.bottom });
        if (e.left < x.left) o.push_back(RECT{ e.left, x.top, x.left, x.bottom });
        if (x.right < e.right) o.push_back(RECT{ x.right, x.top, e.right, x.bottom });
    }
    a.swap(o);
}
static void rl_add(rlist &a, const RECT &r)   /* a = a union r, still disjoint */
{
    rlist p;
    p.push_back(r);
    for (const RECT &e : a) rl_sub(p, e);
    a.insert(a.end(), p.begin(), p.end());
}
static RECT rl_bbox(const rlist &a)
{
    RECT u = { 0, 0, 0, 0 };
    for (size_t i = 0; i < a.size(); i++) {
        if (!i) { u = a[0]; continue; }
        if (a[i].left < u.left) u.left = a[i].left;
        if (a[i].top < u.top) u.top = a[i].top;
        if (a[i].right > u.right) u.right = a[i].right;
        if (a[i].bottom > u.bottom) u.bottom = a[i].bottom;
    }
    return u;
}
static rlist upd_load_l(window *w) { return rlist(w->upd, w->upd + w->nupd); }
static void upd_store_l(window *w, const rlist &a)
{
    free(w->upd);
    w->nupd = 0;
    w->upd = a.empty() ? NULL : (RECT *)malloc(a.size() * sizeof(RECT));
    if (w->upd) { memcpy(w->upd, a.data(), a.size() * sizeof(RECT)); w->nupd = (int)a.size(); }
    if (!w->nupd) w->erase = 0;   /* a window with nothing to paint has nothing to erase */
    w->paint = w->nupd > 0 || w->nc_pending;
}
static RECT client_box_l(window *w)   /* the client area in client coordinates */
{
    RECT c = client_l(w);
    return RECT{ 0, 0, c.right - c.left, c.bottom - c.top };
}
extern "C" BOOL fnwin_invalidate(HWND h, const RECT *rc, int erase)
{
    window *w = wnd_e(h);
    if (!w) return FALSE;
    os_lock(&g_ui_lock);
    RECT box = client_box_l(w), r = box, o;
    if (rc) r = *rc;
    rlist a = upd_load_l(w);
    if (rc_isect(r, box, &o)) {
        rl_add(a, o);
        if (erase) w->erase = 1;
    }
    int e = w->erase;
    upd_store_l(w, a);
    if (w->nupd) w->erase = e;
    os_unlock(&g_ui_lock);
    return TRUE;
}
extern "C" BOOL fnwin_validate(HWND h, const RECT *rc)
{
    window *w = wnd_e(h);
    if (!w) return FALSE;
    os_lock(&g_ui_lock);
    rlist a = upd_load_l(w);
    if (rc) rl_sub(a, *rc); else a.clear();
    int e = w->erase;
    upd_store_l(w, a);
    if (w->nupd) w->erase = e;
    os_unlock(&g_ui_lock);
    return TRUE;
}
extern "C" BOOL fnwin_update_rect(HWND h, RECT *bbox, int *erase)
{
    window *w = wnd_e(h);
    if (!w) return FALSE;
    os_lock(&g_ui_lock);
    rlist a = upd_load_l(w);
    RECT box = client_box_l(w), u = { 0, 0, 0, 0 }, o;
    rlist c;
    for (const RECT &e : a) if (rc_isect(e, box, &o)) c.push_back(o);
    if (!c.empty()) u = rl_bbox(c);
    if (bbox) *bbox = u;
    if (erase) *erase = !c.empty() && w->erase;
    os_unlock(&g_ui_lock);
    return !c.empty();
}
extern "C" void fnwin_invalidate_frame(HWND h)
{
    os_lock(&g_ui_lock);
    window *w = wnd_l(h);
    if (w && w != &g_desk) { w->nc_pending = 1; w->paint = 1; }
    os_unlock(&g_ui_lock);
}
extern "C" int fnwin_take_frame_paint(HWND h)
{
    os_lock(&g_ui_lock);
    window *w = wnd_l(h);
    int r = w ? w->nc_pending : 0;
    if (w) { w->nc_pending = 0; w->paint = w->nupd > 0; }
    os_unlock(&g_ui_lock);
    return r;
}
/* the visible children (WS_CLIPCHILDREN) and the siblings above (WS_CLIPSIBLINGS) are not part of what a window paints */
extern "C" RECT *fnwin_begin_paint(HWND h, int *n, RECT *bbox, int *erase)
{
    os_lock(&g_ui_lock);
    window *w = wnd_l(h);
    *n = 0; *erase = 0; *bbox = RECT{ 0, 0, 0, 0 };
    RECT *out = NULL;
    if (w) {
        RECT box = client_box_l(w), cl = client_l(w), o;
        rlist a = upd_load_l(w), c;
        for (const RECT &e : a) if (rc_isect(e, box, &o)) c.push_back(o);
        auto cut = [&](window *k) {
            if (!(k->style & S_VISIBLE) || (k->style & S_MINIMIZE)) return;
            RECT kr = { k->wr.left - cl.left, k->wr.top - cl.top, k->wr.right - cl.left, k->wr.bottom - cl.top };
            rl_sub(c, kr);
        };
        if (w->style & 0x02000000u) for (window *k = w->first; k; k = k->next) cut(k);            /* WS_CLIPCHILDREN */
        if ((w->style & 0x04000000u) && w->parent)                                                   /* WS_CLIPSIBLINGS: the windows above */
            for (window *k = w->prev; k; k = k->prev) {
                RECT pc = (w->parent == &g_desk) ? RECT{ 0, 0, 0, 0 } : client_l(w->parent);
                RECT kr = { k->wr.left - cl.left, k->wr.top - cl.top, k->wr.right - cl.left, k->wr.bottom - cl.top };
                (void)pc;
                if ((k->style & S_VISIBLE) && !(k->style & S_MINIMIZE)) rl_sub(c, kr);
            }
        *erase = w->erase && !c.empty();
        if (!c.empty()) {
            *bbox = rl_bbox(c);
            out = (RECT *)malloc(c.size() * sizeof(RECT));
            if (out) { memcpy(out, c.data(), c.size() * sizeof(RECT)); *n = (int)c.size(); }
        }
        w->erase = 0;
        upd_store_l(w, rlist());   /* BeginPaint validates the whole update region */
    }
    os_unlock(&g_ui_lock);
    return out;
}
extern "C" void fnvnx_window_validate(HWND h) { fnwin_validate(h, NULL); }   /* BeginPaint/ValidateRect: the update region becomes empty */

/* the window's area that a hide, destroy, move or shrink exposes goes into the update region of what lies beneath: the
 * windows below it in the z-order (and their descendants) and its parents. r: screen coordinates. */
static void expose_in_l(window *x, const RECT &r)
{
    RECT cl = client_l(x), o, box = { 0, 0, cl.right - cl.left, cl.bottom - cl.top };
    RECT rr = { r.left - cl.left, r.top - cl.top, r.right - cl.left, r.bottom - cl.top };
    if (x != &g_desk && (x->style & S_VISIBLE) && rc_isect(rr, box, &o)) {
        rlist a = upd_load_l(x);
        rl_add(a, o);
        upd_store_l(x, a);
        x->erase = 1;
    }
    for (window *k = x->first; k; k = k->next)
        expose_in_l(k, r);
}
static void expose_l(window *w, const RECT &r)
{
    for (window *p = w; p && p->parent; p = p->parent) {
        for (window *s = p->next; s; s = s->next)   /* lower siblings */
            if (s != &g_desk) expose_in_l(s, r);
        window *par = p->parent;
        if (par == &g_desk) break;
        RECT cl = client_l(par), o, box = { 0, 0, cl.right - cl.left, cl.bottom - cl.top };
        RECT rr = { r.left - cl.left, r.top - cl.top, r.right - cl.left, r.bottom - cl.top };
        if ((par->style & S_VISIBLE) && rc_isect(rr, box, &o)) {
            rlist a = upd_load_l(par);
            rl_add(a, o);
            upd_store_l(par, a);
            par->erase = 1;
        }
    }
}

/* ---- z-order */
static void unlink_l(window *w)
{
    window *p = w->parent;
    if (!p) return;
    if (w->prev) w->prev->next = w->next; else p->first = w->next;
    if (w->next) w->next->prev = w->prev; else p->last = w->prev;
    w->prev = w->next = NULL;
}
static void insert_after_l(window *p, window *w, window *a)   /* a == NULL: on top */
{
    w->parent = p; w->prev = a; w->next = a ? a->next : p->first;
    if (w->next) w->next->prev = w; else p->last = w;
    if (a) a->next = w; else p->first = w;
}
static window *last_topmost_l(window *p)
{
    window *a = NULL;
    if (p == &g_desk)
        for (window *c = p->first; c && (c->exstyle & X_TOPMOST); c = c->next) a = c;
    return a;
}
static bool after_ok_l(window *w, HWND after)
{
    if (hw_is(after, 0) || hw_is(after, 1) || hw_is(after, -1) || hw_is(after, -2))
        return true;
    window *s = wnd_l(after);
    return s && s->parent == w->parent;
}
static void zorder_l(window *w, HWND after)
{
    window *p = w->parent, *a = NULL;
    bool top = p == &g_desk;
    if (wnd_l(after) == w)
        return;
    unlink_l(w);
    if (hw_is(after, 0)) a = (top && !(w->exstyle & X_TOPMOST)) ? last_topmost_l(p) : NULL;       /* HWND_TOP */
    else if (hw_is(after, 1)) { a = p->last; w->exstyle &= ~X_TOPMOST; }                         /* HWND_BOTTOM */
    else if (hw_is(after, -1)) { if (top) w->exstyle |= X_TOPMOST; }                             /* HWND_TOPMOST */
    else if (hw_is(after, -2)) { w->exstyle &= ~X_TOPMOST; a = last_topmost_l(p); }              /* HWND_NOTOPMOST */
    else {
        a = wnd_l(after);
        if (top) w->exstyle = (w->exstyle & ~X_TOPMOST) | (a->exstyle & X_TOPMOST);
    }
    insert_after_l(p, w, a);
}

/* ---- what msgqueue.cpp needs from the tree (window_int.h) */
static bool vis_chain_l(window *w)
{
    for (window *p = w; p && p != &g_desk; p = p->parent)
        if (!(p->style & S_VISIBLE)) return false;
    return true;
}
extern "C" int fnwin_info_get(HWND h, fnwin_info *o)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    window *w = wnd_l(h);
    if (w) {
        o->style = w->style; o->exstyle = w->exstyle; o->cstyle = w->cls->style;
        o->tid = w == &g_desk ? GetCurrentThreadId() : w->tid;
        o->wr = w->wr; o->cr = client_l(w);
        o->parent = (w->parent && w->parent != &g_desk) ? (HWND)w->parent : NULL;
        o->paint = w->paint; o->proc = w->proc; o->hproc = w->hproc;
    }
    os_unlock(&g_ui_lock);
    return w != NULL;
}
static bool in_rect(const RECT &r, int x, int y) { return x >= r.left && x < r.right && y >= r.top && y < r.bottom; }
extern "C" HWND fnwin_from_point(int x, int y)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    window *p = &g_desk, *hit = NULL;
    for (;;) {   /* the topmost visible child whose window rectangle holds the point, level by level */
        window *n = NULL;
        for (window *c = p->first; c && !n; c = c->next)
            if ((c->style & S_VISIBLE) && !(c->style & S_MINIMIZE) && in_rect(c->wr, x, y)) n = c;
        if (!n) break;
        hit = p = n;
        if (!in_rect(client_l(n), x, y)) break;
    }
    os_unlock(&g_ui_lock);
    return (HWND)hit;
}
extern "C" HWND fnwin_next_paint(DWORD tid, HWND filter)
{
    os_lock(&g_ui_lock);
    window *r = NULL;
    for (window *w = g_all; w && !r; w = w->all)
        if (w->tid == tid && w->paint && (!filter || (HWND)w == filter) && vis_chain_l(w)) r = w;
    os_unlock(&g_ui_lock);
    return (HWND)r;
}
extern "C" HWND fnwin_active(void) { os_lock(&g_ui_lock); HWND h = (HWND)g_active; os_unlock(&g_ui_lock); return h; }
extern "C" void fnwin_set_active(HWND h)
{
    os_lock(&g_ui_lock);
    g_active = wnd_l(h) && h != (HWND)&g_desk ? (window *)h : NULL;
    if (g_active) note_active_l(g_active);
    os_unlock(&g_ui_lock);
}
extern "C" int fnwin_toplevels(HWND *out, int max)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    int n = 0;
    for (window *c = g_desk.first; c && n < max; c = c->next)
        out[n++] = (HWND)c;
    os_unlock(&g_ui_lock);
    return n;
}

/* ---- keyboard focus (MSDN SetFocus, GetFocus, WM_SETFOCUS, WM_KILLFOCUS): one window of the process holds it, or none */
static void activate(window *w);
extern "C" HWND fnwin_focus_get(void) { os_lock(&g_ui_lock); HWND h = (HWND)g_focus; os_unlock(&g_ui_lock); return h; }
extern "C" HWND fnwin_key_target(void)
{
    os_lock(&g_ui_lock);
    HWND h = (HWND)(g_focus ? g_focus : g_active);
    os_unlock(&g_ui_lock);
    return h;
}
/* the focus moves to n (NULL: none); the old holder is told first, and GetFocus is NULL while it is told */
static void focus_to(window *n)
{
    os_lock(&g_ui_lock);
    window *old = g_focus;
    if (old == n) { os_unlock(&g_ui_lock); return; }
    g_focus = NULL;
    os_unlock(&g_ui_lock);
    if (old) SendMessageA((HWND)old, 0x0008, (WPARAM)(uintptr_t)n, 0);   /* WM_KILLFOCUS */
    os_lock(&g_ui_lock);
    bool ok = n && wnd_l((HWND)n) && !n->destroying && !g_focus;
    if (ok) g_focus = n;
    os_unlock(&g_ui_lock);
    if (ok) SendMessageA((HWND)n, 0x0007, (WPARAM)(uintptr_t)old, 0);   /* WM_SETFOCUS */
}
extern "C" void fnwin_focus_suspend(void)   /* the application loses the focus: it is remembered for the way back */
{
    os_lock(&g_ui_lock);
    g_focus_saved = g_focus;
    os_unlock(&g_ui_lock);
    focus_to(NULL);
}
extern "C" void fnwin_focus_resume(HWND fallback)   /* the application has it back: the window that held it, else fallback */
{
    os_lock(&g_ui_lock);
    window *n = g_focus_saved && wnd_l((HWND)g_focus_saved) ? g_focus_saved : wnd_l(fallback);
    g_focus_saved = NULL;
    if (n == &g_desk) n = NULL;
    os_unlock(&g_ui_lock);
    if (n) focus_to(n);
}
extern "C" HWND SetFocus(HWND h)
{
    os_lock(&g_ui_lock);
    window *old = g_focus;
    os_unlock(&g_ui_lock);
    if (!h) { focus_to(NULL); return (HWND)old; }   /* keystrokes are ignored from here */
    window *w = wnd_e(h);
    if (!w) return NULL;
    os_lock(&g_ui_lock);
    bool ok = w != &g_desk, mine = w->tid == GetCurrentThreadId();
    window *top = w;
    for (window *p = w; ok && p && p != &g_desk; p = p->parent) {   /* shown, enabled and not minimized all the way up */
        top = p;
        if ((p->style & (S_MINIMIZE | S_DISABLED)) || !(p->style & S_VISIBLE) || p->destroying) ok = false;
        if (!(p->style & S_CHILD)) break;
    }
    os_unlock(&g_ui_lock);
    if (!ok) { if (w == &g_desk) SetLastError(ERROR_INVALID_PARAMETER); return NULL; }
    if (!mine) { SetLastError(5); return NULL; }   /* ERROR_ACCESS_DENIED: another thread's queue */
    os_lock(&g_ui_lock);
    bool isactive = g_active == top;
    os_unlock(&g_ui_lock);
    if (!isactive) activate(top);
    focus_to(w);
    os_lock(&g_ui_lock);
    bool got = g_focus == w;
    os_unlock(&g_ui_lock);
    return got ? (HWND)old : NULL;
}
extern "C" HWND GetFocus(void)
{
    os_lock(&g_ui_lock);
    HWND h = (g_focus && g_focus->tid == GetCurrentThreadId()) ? (HWND)g_focus : NULL;
    os_unlock(&g_ui_lock);
    return h;
}

/* ---- activation */
static void activate(window *w)   /* w is a top-level window */
{
    os_lock(&g_ui_lock);
    window *old = g_active;
    os_unlock(&g_ui_lock);
    if (old == w)
        return;
    if (fnmq_hook(5 /* WH_CBT */, 5 /* HCBT_ACTIVATE */, (WPARAM)(uintptr_t)w, 0))
        return;   /* the hook refuses the activation */
    os_lock(&g_ui_lock);
    g_active = w;
    note_active_l(w);
    os_unlock(&g_ui_lock);
    HWND hw = (HWND)w, ho = (HWND)old;
    if (old) {
        SendMessageA(ho, M_NCACTIVATE, 0, 0);
        SendMessageA(ho, M_ACTIVATE, 0, (LPARAM)(uintptr_t)hw);
    } else
        SendMessageA(hw, WM_ACTIVATEAPP, 1, 0);   /* the process was not active */
    os_lock(&g_ui_lock);
    DWORD st = w->style;
    os_unlock(&g_ui_lock);
    SendMessageA(hw, M_NCACTIVATE, 1, 0);
    SendMessageA(hw, M_ACTIVATE, 1 | ((st & S_MINIMIZE) ? 0x10000 : 0), (LPARAM)(uintptr_t)ho);
    os_lock(&g_ui_lock);   /* if the focus is held by another window it moves here */
    window *f = g_focus, *fo = f;
    while (f && f != &g_desk && f != w && (f->style & S_CHILD)) f = f->parent;
    bool mine = f == w;
    os_unlock(&g_ui_lock);
    if (fo && !mine && !(st & S_MINIMIZE)) focus_to(w);   /* the focus was elsewhere: it follows the activation (DefWindowProc sets it when there was none) */
}
static void deactivate(window *w)   /* w stops being able to be active: the next visible top-level window takes over */
{
    os_lock(&g_ui_lock);
    window *n = NULL;
    if (g_active == w)
        for (window *c = g_desk.first; c && !n; c = c->next)
            if (c != w && !(c->style & (S_CHILD | S_MINIMIZE | S_DISABLED)) && (c->style & S_VISIBLE) && !c->destroying)
                n = c;
    bool was = g_active == w;
    if (was && !n)
        g_active = NULL;
    os_unlock(&g_ui_lock);
    if (n)
        activate(n);
    else if (was) {
        SendMessageA((HWND)w, M_NCACTIVATE, 0, 0);
        SendMessageA((HWND)w, M_ACTIVATE, 0, 0);
        focus_to(NULL);
    }
}
static void notify_children(window *w, int show)   /* WM_SHOWWINDOW for the visible children of a window that was shown or hidden */
{
    os_lock(&g_ui_lock);
    int n = 0, k = 0;
    for (window *c = w->first; c; c = c->next) n += (c->style & S_VISIBLE) != 0;
    HWND *a = n ? (HWND *)malloc((size_t)n * sizeof *a) : NULL;
    if (a)
        for (window *c = w->first; c; c = c->next)
            if (c->style & S_VISIBLE) a[k++] = (HWND)c;
    os_unlock(&g_ui_lock);
    for (int i = 0; i < k; i++)
        if (wnd(a[i])) {
            SendMessageA(a[i], M_SHOWWINDOW, show, show ? 3 : 1);   /* SW_PARENTOPENING / SW_PARENTCLOSING */
            notify_children((window *)a[i], show);
        }
    free(a);
}

/* SetWindowPos for every caller: x, y are relative to the parent's client area (screen coordinates for a top-level
 * window); state -1 keeps WS_MINIMIZE/WS_MAXIMIZE, 0 normal, 1 minimized, 2 maximized */
static BOOL set_pos(window *w, HWND after, int x, int y, int cx, int cy, UINT flags, int state)
{
    HWND hw = (HWND)w;
    long ox, oy;
    os_lock(&g_ui_lock);
    origin_l(w->parent, &ox, &oy);
    if (flags & P_NOMOVE) { x = (int)(w->wr.left - ox); y = (int)(w->wr.top - oy); }
    if (flags & P_NOSIZE) { cx = (int)(w->wr.right - w->wr.left); cy = (int)(w->wr.bottom - w->wr.top); }
    bool wasvis = (w->style & S_VISIBLE) != 0;
    os_unlock(&g_ui_lock);
    if (cx < 0) cx = 0;
    if (cy < 0) cy = 0;
    fn_wpos wp = { hw, after, x, y, cx, cy, flags & 0xFFFFu };
    if ((flags & P_SHOWWINDOW) && !wasvis)
        SendMessageA(hw, M_SHOWWINDOW, 1, 0);
    if (!(flags & P_NOSENDCHANGING))
        SendMessageA(hw, M_WPCHANGING, 0, (LPARAM)(uintptr_t)&wp);   /* the handler may edit wp */
    os_lock(&g_ui_lock);
    if (!wnd_l(hw)) {
        os_unlock(&g_ui_lock);
        SetLastError(1400);
        return FALSE;
    }
    UINT fl = wp.flags;
    RECT old = w->wr, oc = client_l(w);
    DWORD ost = w->style;
    if (state >= 0) {
        if (state >= 1 && !(ost & (S_MINIMIZE | S_MAXIMIZE)))
            w->rr = old;
        w->style = (w->style & ~(S_MINIMIZE | S_MAXIMIZE)) | (state == 1 ? S_MINIMIZE : state == 2 ? S_MAXIMIZE : 0);
    }
    long nw = old.right - old.left, nh = old.bottom - old.top;
    if (!(fl & P_NOSIZE)) { nw = wp.cx < 0 ? 0 : wp.cx; nh = wp.cy < 0 ? 0 : wp.cy; }
    long nx = (fl & P_NOMOVE) ? old.left : wp.x + ox, ny = (fl & P_NOMOVE) ? old.top : wp.y + oy;
    w->wr.left = nx; w->wr.top = ny; w->wr.right = nx + nw; w->wr.bottom = ny + nh;
    if (!(w->style & (S_MINIMIZE | S_MAXIMIZE)))
        w->rr = w->wr;
    RECT nc = client_l(w);
    if (nc.left != oc.left || nc.top != oc.top)
        shift_l(w, nc.left - oc.left, nc.top - oc.top);
    bool statechg = ((ost ^ w->style) & (S_MINIMIZE | S_MAXIMIZE)) != 0;
    bool moved = statechg || old.left != nx || old.top != ny, sized = statechg || old.right - old.left != nw || old.bottom - old.top != nh;
    if (flags & P_FORCE) fl &= ~(P_NOMOVE | P_NOSIZE);
    else { if (!moved) fl |= P_NOMOVE; if (!sized) fl |= P_NOSIZE; }
    if (!(fl & P_NOZORDER)) {
        if (after_ok_l(w, wp.after)) zorder_l(w, wp.after);
        else fl |= P_NOZORDER;
    }
    if (fl & P_SHOWWINDOW) w->style |= S_VISIBLE;
    if (fl & P_HIDEWINDOW) w->style &= ~S_VISIBLE;
    bool nowvis = (w->style & S_VISIBLE) != 0;
    {   /* what the change leaves to be painted: a shown or re-framed window wholly; a resized one the part that is new, or
         * all of it when the class redraws on that direction (CS_HREDRAW, CS_VREDRAW); the frame whenever it changed */
        long ow = oc.right - oc.left, oh = oc.bottom - oc.top, cw = nc.right - nc.left, ch = nc.bottom - nc.top;
        UINT cst = w->cls->style;
        bool whole = (nowvis && !wasvis) || (fl & P_FRAMECHANGED);
        if (!whole && sized && !(fl & P_NOREDRAW)) whole = ((cst & 2) && cw != ow) || ((cst & 1) && ch != oh);
        rlist a = upd_load_l(w);
        if (whole) { if (cw > 0 && ch > 0) rl_add(a, RECT{ 0, 0, cw, ch }); }
        else if (sized && !(fl & P_NOREDRAW)) {
            if (cw > ow && ch > 0) rl_add(a, RECT{ ow, 0, cw, ch });
            if (ch > oh && cw > 0) rl_add(a, RECT{ 0, oh, cw, ch });
        }
        int e = w->erase || !a.empty();
        upd_store_l(w, a);
        w->erase = w->nupd ? e : 0;
        if ((nowvis && !wasvis) || sized || (fl & P_FRAMECHANGED)) { w->nc_pending = 1; w->paint = 1; }
        if (wasvis && !nowvis) expose_l(w, old);   /* what it covered is exposed: the windows beneath repaint it */
        else if (nowvis && (moved || sized)) {
            rlist gone;
            gone.push_back(old);
            rl_sub(gone, w->wr);
            for (const RECT &g : gone) expose_l(w, g);
        }
    }
    bool child = (w->style & S_CHILD) != 0, noact = (w->exstyle & X_NOACTIVATE) != 0;
    RECT nwr = w->wr;
    os_unlock(&g_ui_lock);
    wp.flags = fl;
    if (!(flags & P_CREATE) && (sized || (fl & P_FRAMECHANGED))) {
        fn_nccs p = { { nwr, old, oc }, &wp };
        SendMessageA(hw, M_NCCALCSIZE, 1, (LPARAM)(uintptr_t)&p);
    }
    if (nowvis != wasvis)
        notify_children(w, nowvis);
    if (wasvis && !nowvis) {
        SendMessageA(hw, M_SHOWWINDOW, 0, 0);
        deactivate(w);
    }
    if (nowvis && !child && !noact && !(fl & P_NOACTIVATE) && ((fl & P_SHOWWINDOW) || (!(fl & P_NOZORDER) && hw_is(wp.after, 0))))
        activate(w);
    SendMessageA(hw, M_WPCHANGED, 0, (LPARAM)(uintptr_t)&wp);
    return TRUE;
}

/* ---- creation and destruction */
static void destroy_rec(window *w, bool skip_destroy);
static BOOL show_impl(window *w, int cmd, bool keep_vis);

extern "C" HWND CreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR title, DWORD style, int x, int y, int w, int h,
                                HWND parent, HMENU menu, HINSTANCE inst, LPVOID param)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    wclass *c = cls ? find_class_l(cls, norm_inst(inst)) : NULL;
    window *par = parent ? wnd_l(parent) : NULL, *win = NULL;
    int err = !c ? 1407 : (parent && !par) ? 1400 : ((style & S_CHILD) && !par) ? 1406 : 0;
    if (!err && !(win = (window *)calloc(1, sizeof *win)))
        err = ERROR_NOT_ENOUGH_MEMORY;
    if (win) {
        win->text = dup_str(title);
        win->extra = (uint8_t *)calloc(1, (size_t)c->cbwnd + 1);
        if (!win->text || !win->extra) {
            free(win->text); free(win->extra); free(win);
            win = NULL;
            err = ERROR_NOT_ENOUGH_MEMORY;
        }
    }
    bool child = (style & S_CHILD) != 0;
    long ox = 0, oy = 0;
    if (win) {
        win->magic = WND_MAGIC;
        win->cls = c; win->proc = c->proc; win->hproc = c->hproc; win->inst = norm_inst(inst); win->tid = GetCurrentThreadId();
        win->style = style & ~(S_VISIBLE | S_MINIMIZE | S_MAXIMIZE);   /* applied by the show at the end of creation */
        win->exstyle = ex;
        win->id = (LONG)(uintptr_t)menu;
        win->hasmenu = !child && (menu || c->menu);
        bool ovl = !(style & (S_CHILD | S_POPUP));
        if (x == FN_CW_USEDEFAULT) x = 0;
        if (y == FN_CW_USEDEFAULT) y = 0;
        if (w == FN_CW_USEDEFAULT) w = ovl ? fnvnx_display_w() : 0;
        if (h == FN_CW_USEDEFAULT) h = ovl ? fnvnx_display_h() : 0;
        if (w < 0) w = 0;
        if (h < 0) h = 0;
        window *p = child ? par : &g_desk;
        if (child) origin_l(p, &ox, &oy);
        if (!child && par) {
            window *o = par;
            while ((o->style & S_CHILD) && o->parent && o->parent != &g_desk) o = o->parent;
            win->owner = o == &g_desk ? NULL : o;
        }
        win->wr.left = ox + x; win->wr.top = oy + y; win->wr.right = ox + x + w; win->wr.bottom = oy + y + h;
        win->rr = win->wr;
        insert_after_l(p, win, (!child && !(ex & X_TOPMOST)) ? last_topmost_l(p) : NULL);
        if (g_all_tail) g_all_tail->all = win; else g_all = win;
        g_all_tail = win;
        c->nwin++;
    }
    os_unlock(&g_ui_lock);
    if (!win) {
        SetLastError(err);
        return NULL;
    }
    HWND hw = (HWND)win;
    fnv_createstruct cs = { param, inst, menu, parent, h, w, y, x, (LONG)style, title, cls, ex };
    LRESULT ok = 0;
    if (!child) {
        fn_mmi mmi;
        fill_mmi(win, &mmi);
        SendMessageA(hw, M_GETMINMAXINFO, 0, (LPARAM)(uintptr_t)&mmi);
        if ((style & S_THICK) || !(style & S_POPUP)) {   /* an overlapped or sizing-border window is created within its track sizes */
            os_lock(&g_ui_lock);
            long cw = win->wr.right - win->wr.left, ch = win->wr.bottom - win->wr.top;
            if (cw > mmi.maxtrack.x) cw = mmi.maxtrack.x;
            if (ch > mmi.maxtrack.y) ch = mmi.maxtrack.y;
            if (cw < mmi.mintrack.x) cw = mmi.mintrack.x;
            if (ch < mmi.mintrack.y) ch = mmi.mintrack.y;
            win->wr.right = win->wr.left + cw; win->wr.bottom = win->wr.top + ch;
            win->rr = win->wr;
            os_unlock(&g_ui_lock);
        }
    }
    ok = SendMessageA(hw, M_NCCREATE, 0, (LPARAM)(uintptr_t)&cs);
    if (!ok) {   /* only WM_NCDESTROY follows a refused WM_NCCREATE */
        destroy_rec(win, true);
        return NULL;
    }
    fn_nccs ncp = { { win->wr, win->wr, win->wr }, NULL };
    SendMessageA(hw, M_NCCALCSIZE, 1, (LPARAM)(uintptr_t)&ncp);
    if ((int)SendMessageA(hw, M_CREATE, 0, (LPARAM)(uintptr_t)&cs) == -1) {
        destroy_rec(win, false);
        return NULL;
    }
    if (!wnd(hw)) {   /* destroyed by its own WM_CREATE */
        SetLastError(1400);
        return NULL;
    }
    if (style & (S_MINIMIZE | S_MAXIMIZE))   /* created in that state, as SW_SHOWMINIMIZED / SW_SHOWMAXIMIZED do it */
        show_impl(win, (style & S_MINIMIZE) ? 2 : 3, !(style & S_VISIBLE));
    else if (style & S_VISIBLE)
        set_pos(win, NULL, 0, 0, 0, 0, P_SHOWWINDOW | P_NOZORDER | P_NOMOVE | P_NOSIZE | P_FORCE | P_CREATE, -1);
    return hw;
}

/* destroys w: owned windows, deactivation, hide, WM_DESTROY, the children the same way, WM_NCDESTROY */
static void destroy_rec(window *w, bool skip_destroy)
{
    HWND hw = (HWND)w;
    os_lock(&g_ui_lock);
    bool first = !w->destroying;
    w->destroying = 1;
    bool top = !(w->style & S_CHILD);
    os_unlock(&g_ui_lock);
    if (!first)
        return;
    for (;;) {
        window *o = NULL;
        os_lock(&g_ui_lock);
        if (top)
            for (window *c = g_desk.first; c && !o; c = c->next)
                if (c->owner == w && !c->destroying) o = c;
        os_unlock(&g_ui_lock);
        if (!o) break;
        destroy_rec(o, false);
    }
    deactivate(w);
    os_lock(&g_ui_lock);
    bool vis = (w->style & S_VISIBLE) != 0;
    os_unlock(&g_ui_lock);
    if (vis)
        set_pos(w, NULL, 0, 0, 0, 0, P_HIDEWINDOW | P_NOZORDER | P_NOMOVE | P_NOSIZE | P_NOACTIVATE, -1);
    os_lock(&g_ui_lock);   /* the focus leaves the window (and the ones inside it) before it is destroyed */
    bool hasfocus = false;
    for (window *f = g_focus; f && f != &g_desk; f = f->parent)
        if (f == w) { hasfocus = true; break; }
    os_unlock(&g_ui_lock);
    if (hasfocus) focus_to(NULL);
    if (!skip_destroy)
        SendMessageA(hw, WM_DESTROY, 0, 0);
    for (;;) {   /* the children go the same way, inside the parent's WM_DESTROY .. WM_NCDESTROY */
        window *k = NULL;
        os_lock(&g_ui_lock);
        for (window *c = w->first; c && !k; c = c->next)
            if (!c->destroying) k = c;
        os_unlock(&g_ui_lock);
        if (!k) break;
        destroy_rec(k, false);
    }
    SendMessageA(hw, M_NCDESTROY, 0, 0);
    os_lock(&g_ui_lock);
    unlink_l(w);
    for (window **pp = &g_all; *pp; pp = &(*pp)->all)
        if (*pp == w) {
            *pp = w->all;
            break;
        }
    g_all_tail = NULL;
    for (window *p = g_all; p; p = p->all) g_all_tail = p;
    if (g_active == w) g_active = NULL;
    for (window *p = g_all; p; p = p->all) if (p->lastpop == w) p->lastpop = NULL;   /* it is its own last popup again */
    if (g_focus == w) g_focus = NULL;
    if (g_focus_saved == w) g_focus_saved = NULL;
    if (g_capture == w) g_capture = NULL;   /* a destroyed window loses the capture */
    w->cls->nwin--;
    os_unlock(&g_ui_lock);
    fnmq_purge(hw);
    w->magic = 0;
    free(w->text); free(w->extra); free(w->upd);
    free(w);
}

extern "C" BOOL DestroyWindow(HWND h)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    if (w == &g_desk || w->tid != GetCurrentThreadId()) {
        SetLastError(5);   /* ERROR_ACCESS_DENIED */
        return FALSE;
    }
    os_lock(&g_ui_lock);
    bool busy = w->destroying;
    os_unlock(&g_ui_lock);
    if (busy) {
        SetLastError(1400);
        return FALSE;
    }
    destroy_rec(w, false);
    return TRUE;
}

extern "C" BOOL IsWindow(HWND h) { return wnd(h) != NULL; }

extern "C" BOOL IsWindowVisible(HWND h)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    os_lock(&g_ui_lock);
    bool v = true;
    for (window *p = w; p && p != &g_desk; p = p->parent)
        v = v && (p->style & S_VISIBLE);
    os_unlock(&g_ui_lock);
    return v;
}
extern "C" BOOL IsIconic(HWND h)
{
    window *w = wnd_e(h);
    return w && (w->style & S_MINIMIZE) != 0;
}
extern "C" DWORD GetWindowThreadProcessId(HWND h, LPDWORD pid)
{
    window *w = wnd_e(h);
    if (!w)
        return 0;
    if (pid)
        *pid = GetCurrentProcessId();
    return w == &g_desk ? GetCurrentThreadId() : w->tid;
}

/* ---- tree queries */
extern "C" HWND GetWindow(HWND h, UINT cmd)
{
    window *w = wnd_e(h);
    if (!w)
        return NULL;
    os_lock(&g_ui_lock);
    window *r = NULL;
    int err = 0;
    switch (cmd) {
    case 0: r = w->parent ? w->parent->first : w; break;                                         /* GW_HWNDFIRST */
    case 1: r = w->parent ? w->parent->last : w; break;
    case 2: r = w->next; break;
    case 3: r = w->prev; break;
    case 4: r = (w->style & S_CHILD) ? NULL : w->owner; break;                                   /* GW_OWNER */
    case 5: r = w->first; break;                                                                 /* GW_CHILD */
    case 6:                                                                                      /* GW_ENABLEDPOPUP */
        r = w;
        for (window *c = g_desk.first; c; c = c->next)
            if (c->owner == w && (c->style & S_POPUP) && !(c->style & S_DISABLED)) { r = c; break; }
        break;
    default: err = 1404;
    }
    os_unlock(&g_ui_lock);
    if (err)
        SetLastError(err);
    return (HWND)r;
}
extern "C" HWND GetTopWindow(HWND h)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    window *w = h ? wnd_l(h) : &g_desk;
    HWND r = w ? (HWND)w->first : NULL;
    os_unlock(&g_ui_lock);
    if (!w)
        SetLastError(1400);
    return r;
}
static void collect_l(window *w, HWND *out, int *n)
{
    for (window *c = w->first; c; c = c->next) {
        if (out) out[*n] = (HWND)c;
        (*n)++;
        collect_l(c, out, n);
    }
}
extern "C" BOOL EnumChildWindows(HWND h, WNDENUMPROC f, LPARAM l)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    window *w = h ? wnd_l(h) : &g_desk;
    int n = 0;
    HWND *a = NULL;
    if (w) {
        if (h) {
            collect_l(w, NULL, &n);
            a = n ? (HWND *)malloc((size_t)n * sizeof *a) : NULL;
            n = 0;
            if (a) collect_l(w, a, &n);
        } else {   /* NULL parent: EnumWindows, the top-level windows */
            for (window *c = w->first; c; c = c->next) n++;
            a = n ? (HWND *)malloc((size_t)n * sizeof *a) : NULL;
            n = 0;
            for (window *c = w->first; c && a; c = c->next) a[n++] = (HWND)c;
        }
    }
    os_unlock(&g_ui_lock);
    if (!w || !f) {
        SetLastError(w ? ERROR_INVALID_PARAMETER : 1400);
        return FALSE;
    }
    BOOL r = TRUE;
    for (int i = 0; i < n && r; i++)
        if (wnd(a[i]))   /* a window the callback destroyed is skipped */
            r = f(a[i], l);
    free(a);
    return r;
}
extern "C" HWND FindWindowA(LPCSTR cls, LPCSTR title)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    bool known = !cls;
    for (wclass *c = g_classes; c && !known; c = c->next)
        known = cls_match(c, cls);
    HWND r = NULL;
    for (window *w = g_desk.first; w && known && !r; w = w->next)
        if (!(w->style & S_CHILD) && (!cls || cls_match(w->cls, cls)) && (!title || !w32_stricmp(w->text, title)))
            r = (HWND)w;
    os_unlock(&g_ui_lock);
    if (!known)
        SetLastError(1407);
    return r;
}

/* ---- DefWindowProc: ONE table, message -> rule. Source of each rule: MSDN's default action for the message and the
 * reference's default_window_proc (win32u defwnd.c, "ref"). A message that has no documented default action is not in the
 * table and answers 0: that is the contract's own answer (MSDN: "if an application processes this message, it should
 * return zero"; WM_SETFONT/WM_GETFONT are handled by controls, never by DefWindowProc), the one explicit default of
 * def_dispatch(). Environment: no IME (ENV: SM_IMMENABLED 0), no menu or scroll-bar subsystem (the window's frame has no
 * surface of its own: the platform presents client surfaces), one keyboard layout (ENV I4). */
enum { HT_NOWHERE = 0, HT_CLIENT = 1, HT_CAPTION = 2, HT_SYSMENU = 3, HT_MENU = 5, HT_HSCROLL = 6, HT_VSCROLL = 7, HT_MINBUTTON = 8,
       HT_MAXBUTTON = 9, HT_LEFT = 10, HT_RIGHT = 11, HT_TOP = 12, HT_TOPLEFT = 13, HT_TOPRIGHT = 14, HT_BOTTOM = 15,
       HT_BOTTOMLEFT = 16, HT_BOTTOMRIGHT = 17, HT_BORDER = 18, HT_SIZE = 4, HT_CLOSE = 20 };
enum { SC_SIZE_ = 0xF000, SC_MOVE_ = 0xF010, SC_MINIMIZE_ = 0xF020, SC_MAXIMIZE_ = 0xF030, SC_NEXTWINDOW_ = 0xF040,
       SC_PREVWINDOW_ = 0xF050, SC_CLOSE_ = 0xF060, SC_VSCROLL_ = 0xF070, SC_HSCROLL_ = 0xF080, SC_MOUSEMENU_ = 0xF090,
       SC_KEYMENU_ = 0xF100, SC_ARRANGE_ = 0xF110, SC_RESTORE_ = 0xF120, SC_TASKLIST_ = 0xF130, SC_SCREENSAVE_ = 0xF140,
       SC_HOTKEY_ = 0xF150 };
#define S_MINIMIZEBOX 0x00020000u
#define S_MAXIMIZEBOX 0x00010000u
#define S_SYSMENU 0x00080000u
#define S_HSCROLL 0x00100000u
#define S_VSCROLL 0x00200000u
#define X_TOOLWINDOW 0x80u
#define X_ACCEPTFILES 0x10u
#define X_LAYERED 0x80000u
#define C_PARENTDC 0x80u
#define C_NOCLOSE 0x200u

static bool g_menu_sys_key, g_f10_key;   /* the Alt / F10 press-and-release state of the thread's keyboard (ref menu_sys_key, f10_key) */
static bool win_has_icon_l(window *w) { return w->icon_small || w->icon_big || w->cls->icon || w->cls->iconsm; }
static window *parent_l(window *w)   /* ref get_parent: the parent of a child, the owner of a popup */
{
    if (w->style & S_CHILD) return w->parent == &g_desk ? NULL : w->parent;
    return w->owner;
}
static LRESULT send_parent(window *w, UINT m, WPARAM wp, LPARAM lp)
{
    os_lock(&g_ui_lock);
    window *p = parent_l(w);
    os_unlock(&g_ui_lock);
    return p ? SendMessageA((HWND)p, m, wp, lp) : 0;
}
static HWND root_of(window *w)   /* GA_ROOT: the top-level ancestor through parents (not owners) */
{
    os_lock(&g_ui_lock);
    while (w->parent && w->parent != &g_desk && (w->style & S_CHILD)) w = w->parent;
    os_unlock(&g_ui_lock);
    return (HWND)w;
}
static bool is_iconic_w(window *w) { os_lock(&g_ui_lock); bool r = (w->style & S_MINIMIZE) != 0; os_unlock(&g_ui_lock); return r; }

/* WM_NCHITTEST (ref handle_nc_hit_test): the frame decides, from the outside in: sizing border, caption with its buttons,
 * menu bar, scroll bars */
static bool in_rect(const RECT &r, int x, int y);
static int hit_test(window *w, int x, int y)
{
    os_lock(&g_ui_lock);
    RECT r = w->wr, c = client_l(w);
    DWORD st = w->style, ex = w->exstyle;
    bool menu = w->hasmenu != 0, icon = win_has_icon_l(w);
    os_unlock(&g_ui_lock);
    if (!in_rect(r, x, y)) return HT_NOWHERE;
    if (in_rect(c, x, y) || w == &g_desk) return HT_CLIENT;
    auto infl = [&](int dx, int dy) { r.left += dx; r.right -= dx; r.top += dy; r.bottom -= dy; };
    if (st & S_THICK) {
        int fx = GetSystemMetrics(32), fy = GetSystemMetrics(33), sx = GetSystemMetrics(30), sy = GetSystemMetrics(31);
        infl(fx, fy);
        if (!in_rect(r, x, y)) {
            if (y < r.top) return x < r.left + sx ? HT_TOPLEFT : x >= r.right - sx ? HT_TOPRIGHT : HT_TOP;
            if (y >= r.bottom) return x < r.left + sx ? HT_BOTTOMLEFT : x >= r.right - sx ? HT_BOTTOMRIGHT : HT_BOTTOM;
            if (x < r.left) return y < r.top + sy ? HT_TOPLEFT : y >= r.bottom - sy ? HT_BOTTOMLEFT : HT_LEFT;
            if (x >= r.right) return y < r.top + sy ? HT_TOPRIGHT : y >= r.bottom - sy ? HT_BOTTOMRIGHT : HT_RIGHT;
        }
    } else {
        if ((ex & X_DLGMODAL) || (st & S_DLGFRAME)) infl(GetSystemMetrics(7), GetSystemMetrics(8));
        else if (st & S_BORDER) infl(GetSystemMetrics(5), GetSystemMetrics(6));
        if (!in_rect(r, x, y)) return HT_BORDER;
    }
    if ((st & S_CAPTION) == S_CAPTION) {
        r.top += ((ex & X_TOOLWINDOW) ? GetSystemMetrics(51) : GetSystemMetrics(4)) - 1;
        if (!in_rect(r, x, y)) {
            bool minmax = (st & S_SYSMENU) && (st & (S_MINIMIZEBOX | S_MAXIMIZEBOX));
            bool rtl = (ex & 0x400000u) != 0;
            int cap = GetSystemMetrics(4), bx = GetSystemMetrics(30);
            long &inner = rtl ? r.right : r.left;   /* the system-menu side */
            long &outer = rtl ? r.left : r.right;   /* the buttons' side */
            auto beyond = [&](int d) { return rtl ? (outer += d, (long)x < outer) : (outer -= d, (long)x > outer); };
            if ((st & S_SYSMENU) && !(ex & X_TOOLWINDOW) && icon) {
                inner += rtl ? -(cap - 1) : cap - 1;
                if (rtl ? (long)x > inner : (long)x < inner) return HT_SYSMENU;
            }
            if ((st & S_SYSMENU) && beyond(cap)) return HT_CLOSE;
            if (minmax && !(ex & X_TOOLWINDOW)) {
                if (beyond(bx)) return HT_MAXBUTTON;
                if (beyond(bx)) return HT_MINBUTTON;
            }
            return HT_CAPTION;
        }
    }
    if (menu && y < c.top && x >= c.left && x < c.right) return HT_MENU;
    RECT sc = c;
    bool left = (ex & 0x4000u) != 0;   /* WS_EX_LEFTSCROLLBAR */
    if (st & S_VSCROLL) {
        if (left) sc.left -= GetSystemMetrics(2); else sc.right += GetSystemMetrics(2);
        if (in_rect(sc, x, y)) return HT_VSCROLL;
    }
    if (st & S_HSCROLL) {
        sc.bottom += GetSystemMetrics(3);
        if (in_rect(sc, x, y)) {
            if ((st & S_VSCROLL) && (left ? x <= sc.left + GetSystemMetrics(2) : x >= sc.right - GetSystemMetrics(2))) return HT_SIZE;
            return HT_HSCROLL;
        }
    }
    return HT_NOWHERE;   /* a frame the window drew itself */
}

/* ---- shared pieces of the rules */
static void set_text_l(window *w, const char *t)
{
    char *n = dup_str(t ? t : "");
    if (!n) return;
    os_lock(&g_ui_lock);
    free(w->text);
    w->text = n;
    os_unlock(&g_ui_lock);
}
static void set_style_bits(window *w, DWORD set, DWORD clr)
{
    os_lock(&g_ui_lock);
    w->style = (w->style | set) & ~clr;
    os_unlock(&g_ui_lock);
}
/* WM_SHOWWINDOW to the owned pop-ups of a window that is minimized, restored or maximized (ref show_owned_popups) */
static void show_owned_popups(window *w, bool show)
{
    HWND list[64];
    int n = 0;
    os_lock(&g_ui_lock);
    for (window *c = g_all; c && n < 64; c = c->all)
        if (c->owner == w && !c->destroying && (show ? c->owned_hidden : (c->style & S_VISIBLE) != 0)) list[n++] = (HWND)c;
    os_unlock(&g_ui_lock);
    for (int i = 0; i < n; i++) SendMessageA(list[i], M_SHOWWINDOW, show, show ? 3 /* SW_PARENTOPENING */ : 1 /* SW_PARENTCLOSING */);
}
/* the modal loop of SC_MOVE / SC_SIZE (MSDN WM_SYSCOMMAND, WM_ENTERSIZEMOVE, WM_MOVING, WM_SIZING): the window follows the
 * pointer (touch or mouse) or the arrow keys until the button is released or Enter is pressed; Escape puts it back */
extern "C" int fnmq_get_message(MSG *m, HWND f, UINT lo, UINT hi);
static RECT moved_rect(int op, int dir, RECT o, long dx, long dy)
{
    if (op == SC_MOVE_) { o.left += dx; o.right += dx; o.top += dy; o.bottom += dy; return o; }
    /* WMSZ_LEFT 1, RIGHT 2, TOP 3, TOPLEFT 4, TOPRIGHT 5, BOTTOM 6, BOTTOMLEFT 7, BOTTOMRIGHT 8 */
    if (dir == 1 || dir == 4 || dir == 7) o.left += dx;
    if (dir == 2 || dir == 5 || dir == 8) o.right += dx;
    if (dir == 3 || dir == 4 || dir == 5) o.top += dy;
    if (dir == 6 || dir == 7 || dir == 8) o.bottom += dy;
    return o;
}
static void sys_size_move(window *w, WPARAM cmd, LPARAM lp)
{
    HWND h = (HWND)w;
    int op = (int)(cmd & 0xFFF0), dir = (int)(cmd & 0xF);
    os_lock(&g_ui_lock);
    DWORD st = w->style;
    RECT orig = w->wr;
    long ox, oy;
    origin_l(w->parent, &ox, &oy);
    os_unlock(&g_ui_lock);
    if ((st & (S_MAXIMIZE | S_MINIMIZE)) || (op == SC_SIZE_ && !(st & S_THICK))) return;   /* only a normal window moves, only a sizing border sizes */
    POINT start = { (long)(short)(lp & 0xFFFF), (long)(short)((lp >> 16) & 0xFFFF) };
    if (!lp) GetCursorPos(&start);
    SendMessageA(h, 0x0231, 0, 0);   /* WM_ENTERSIZEMOVE */
    SetCapture(h);
    bool done = false, cancel = false;
    long pdx = 0, pdy = 0, kx = 0, ky = 0;
    while (!done) {
        MSG m;
        if (!fnmq_get_message(&m, NULL, 0, 0)) break;
        bool upd = false;
        if (m.message == 0x0200) {   /* WM_MOUSEMOVE */
            POINT p = { (long)(short)(m.lParam & 0xFFFF), (long)(short)((m.lParam >> 16) & 0xFFFF) };
            ClientToScreen(m.hwnd, &p);
            pdx = p.x - start.x; pdy = p.y - start.y;
            upd = true;
        } else if (m.message == 0x0202 || m.message == 0x0205 || m.message == 0x0208) {   /* a button is released */
            done = true;
        } else if (m.message == 0x0100) {   /* WM_KEYDOWN */
            long sx = m.wParam == 0x25 ? -8 : m.wParam == 0x27 ? 8 : 0, sy = m.wParam == 0x26 ? -8 : m.wParam == 0x28 ? 8 : 0;
            if (m.wParam == 0x0D) done = true;
            else if (m.wParam == 0x1B) done = cancel = true;
            else if (sx || sy) {
                if (op == SC_SIZE_ && dir == 0) { dir = sx < 0 ? 1 : sx > 0 ? 2 : sy < 0 ? 3 : 6; }   /* the first arrow chooses the edge */
                else { kx += sx; ky += sy; upd = true; }
            }
        } else {
            TranslateMessage(&m);
            DispatchMessageA(&m);
        }
        if (!upd) continue;
        RECT n = moved_rect(op, dir, orig, pdx + kx, pdy + ky);
        SendMessageA(h, op == SC_MOVE_ ? 0x0216 : 0x0214, op == SC_MOVE_ ? 0 : (WPARAM)dir, (LPARAM)(uintptr_t)&n);   /* WM_MOVING / WM_SIZING: the window may edit it */
        SetWindowPos(h, NULL, (int)(n.left - ox), (int)(n.top - oy), (int)(n.right - n.left), (int)(n.bottom - n.top), 0x4 | 0x10);
    }
    ReleaseCapture();
    if (cancel)
        SetWindowPos(h, NULL, (int)(orig.left - ox), (int)(orig.top - oy), (int)(orig.right - orig.left), (int)(orig.bottom - orig.top), 0x4 | 0x10);
    SendMessageA(h, 0x0232, 0, 0);   /* WM_EXITSIZEMOVE */
}
/* the caption button that was pressed acts when the pointer is released on it (ref track_min_max_box, track_close_button) */
static void track_caption_button(window *w, int ht, LPARAM lp)
{
    HWND h = (HWND)w;
    SetCapture(h);
    int at = ht;
    MSG m;
    while (fnmq_get_message(&m, NULL, 0x200, 0x20E)) {
        POINT p = { (long)(short)(m.lParam & 0xFFFF), (long)(short)((m.lParam >> 16) & 0xFFFF) };
        ClientToScreen(m.hwnd, &p);
        if (m.message == 0x0200) at = hit_test(w, (int)p.x, (int)p.y);
        else if (m.message == 0x0202) { at = hit_test(w, (int)p.x, (int)p.y); break; }
    }
    ReleaseCapture();
    if (at != ht) return;
    os_lock(&g_ui_lock);
    bool max = (w->style & S_MAXIMIZE) != 0;
    os_unlock(&g_ui_lock);
    UINT sc = ht == HT_MINBUTTON ? (is_iconic_w(w) ? SC_RESTORE_ : SC_MINIMIZE_) : ht == HT_MAXBUTTON ? (max ? SC_RESTORE_ : SC_MAXIMIZE_) : SC_CLOSE_;
    SendMessageA(h, 0x0112, sc, lp);
}

/* WM_SYSCOMMAND (MSDN, ref handle_sys_command) */
static LRESULT def_syscommand(window *w, HWND h, WPARAM wp, LPARAM lp)
{
    os_lock(&g_ui_lock);
    bool disabled = (w->style & S_DISABLED) != 0;
    os_unlock(&g_ui_lock);
    if (disabled) return 0;
    if (fnmq_hook(5 /* WH_CBT */, 8 /* HCBT_SYSCOMMAND */, wp, lp)) return 0;
    switch (wp & 0xFFF0) {
    case SC_SIZE_: case SC_MOVE_: sys_size_move(w, wp, lp); break;
    case SC_MINIMIZE_: show_owned_popups(w, false); ShowWindow(h, 6 /* SW_MINIMIZE */); break;
    case SC_MAXIMIZE_: if (is_iconic_w(w)) show_owned_popups(w, true); ShowWindow(h, 3 /* SW_MAXIMIZE */); break;
    case SC_CLOSE_: return SendMessageA(h, WM_CLOSE, 0, 0);
    case SC_RESTORE_: if (is_iconic_w(w)) show_owned_popups(w, true); ShowWindow(h, 9 /* SW_RESTORE */); break;
    case SC_VSCROLL_: case SC_HSCROLL_: case SC_MOUSEMENU_: case SC_KEYMENU_:
        break;   /* tracked by the menu and scroll-bar subsystems; the platform has none, so a window has no menu or scroll bar to track */
    case SC_TASKLIST_: case SC_SCREENSAVE_: return 1;   /* ref: the shell's, answered 1 */
    case SC_HOTKEY_: case SC_ARRANGE_: case SC_NEXTWINDOW_: case SC_PREVWINDOW_: break;   /* ref: a one-window session has nothing to switch */
    default: return 1;   /* a command of the application's own: "handle on client side" */
    }
    return 0;
}

static void validate_tree(HWND h)
{
    HWND kids[256];
    int n = 0;
    os_lock(&g_ui_lock);
    window *w = wnd_l(h);
    if (w) for (window *c = w->first; c && n < 256; c = c->next) kids[n++] = (HWND)c;
    os_unlock(&g_ui_lock);
    fnwin_validate(h, NULL);
    for (int i = 0; i < n; i++) validate_tree(kids[i]);
}
static void foreach_child(window *w, void (*fn)(HWND, UINT, WPARAM, LPARAM), UINT m, WPARAM wp, LPARAM lp)
{
    HWND kids[256];
    int n = 0;
    os_lock(&g_ui_lock);
    for (window *c = w->first; c && n < 256; c = c->next) kids[n++] = (HWND)c;
    os_unlock(&g_ui_lock);
    for (int i = 0; i < n; i++) fn(kids[i], m, wp, lp);
}
static void send_to(HWND h, UINT m, WPARAM wp, LPARAM lp) { SendMessageA(h, m, wp, lp); }

/* WM_ERASEBKGND / WM_ICONERASEBKGND: the class brush fills the DC's clip box (the client area for CS_PARENTDC) */
static LRESULT def_erase(window *w, HWND h, WPARAM wp, LPARAM)
{
    os_lock(&g_ui_lock);
    HBRUSH bg = w->cls->bg;
    bool pdc = (w->cls->style & C_PARENTDC) != 0;
    os_unlock(&g_ui_lock);
    if (!bg) return 0;
    RECT r;
    if (pdc) GetClientRect(h, &r);
    else if (!gdi_dc_clipbox((void *)(uintptr_t)wp, &r)) return 1;   /* no usable DC: nothing to fill (ref: still 1) */
    gdi_fill_rect((void *)(uintptr_t)wp, &r, bg);
    return 1;
}
/* WM_CTLCOLOR*: the colours of the reference's system palette for the control, and the brush that goes with them */
static LRESULT def_ctlcolor(HDC dc, unsigned type /* 0 msgbox 1 edit 2 listbox 3 button 4 dialog 5 scrollbar 6 static */)
{
    if (type == 5) {
        SetTextColor(dc, gdi_syscolor(15));   /* COLOR_3DFACE */
        SetBkColor(dc, gdi_syscolor(20));     /* COLOR_3DHILIGHT */
        return (LRESULT)(uintptr_t)gdi_sys_brush(0);   /* COLOR_SCROLLBAR (the reference dithers it with a pattern brush; the platform has none) */
    }
    SetTextColor(dc, gdi_syscolor(8));   /* COLOR_WINDOWTEXT */
    if (type == 1 || type == 2) { SetBkColor(dc, gdi_syscolor(5)); return (LRESULT)(uintptr_t)gdi_sys_brush(5); }   /* COLOR_WINDOW */
    SetBkColor(dc, gdi_syscolor(15));
    return (LRESULT)(uintptr_t)gdi_sys_brush(15);                                                                  /* COLOR_3DFACE */
}
static LRESULT def_paint(window *, HWND h, WPARAM, LPARAM)
{
    alignas(8) uint8_t ps[128];   /* a PAINTSTRUCT of the build */
    if (BeginPaint(h, ps)) EndPaint(h, ps);
    return 0;
}
static LRESULT def_setcursor(window *w, HWND h, WPARAM wp, LPARAM lp)
{
    os_lock(&g_ui_lock);
    bool child = (w->style & S_CHILD) != 0;
    window *par = (child && w->parent != &g_desk) ? w->parent : NULL;
    HCURSOR cls = w->cls->cursor;
    os_unlock(&g_ui_lock);
    unsigned ht = (unsigned)(lp & 0xFFFF);
    if (par && (ht < HT_LEFT || ht > HT_BOTTOMRIGHT) && SendMessageA((HWND)par, 0x20, wp, lp)) return TRUE;   /* the parent first, except on the sizing border */
    HCURSOR c = NULL;
    switch (ht) {
    case HT_CLIENT: c = cls; break;
    case HT_LEFT: case HT_RIGHT: c = LoadCursorA(NULL, (LPCSTR)32644); break;               /* IDC_SIZEWE */
    case HT_TOP: case HT_BOTTOM: c = LoadCursorA(NULL, (LPCSTR)32645); break;               /* IDC_SIZENS */
    case HT_TOPLEFT: case HT_BOTTOMRIGHT: c = LoadCursorA(NULL, (LPCSTR)32642); break;      /* IDC_SIZENWSE */
    case HT_TOPRIGHT: case HT_BOTTOMLEFT: c = LoadCursorA(NULL, (LPCSTR)32643); break;      /* IDC_SIZENESW */
    default: c = fnmq_arrow_cursor(); break;                                                 /* IDC_ARROW */
    }
    (void)h;
    if (c) fnmq_set_cursor(c);
    return c != NULL;
}
struct fn_helpinfo { UINT cb; int ctx, ctl; HANDLE item; ULONG_PTR ctxid; POINT pos; };
#define LPOINT(x, y) ((LPARAM)(((uint32_t)(x) & 0xFFFF) | ((uint32_t)(y) << 16)))

/* RDW_ERASENOW: a pending background erase is done now, to the window's DC clipped to its update region */
extern "C" void gdi_dc_set_clip(void *hd, const RECT *r, int n);
extern "C" void fnwin_erase_now(HWND h, bool children)
{
    window *w = wnd(h);
    if (!w) return;
    os_lock(&g_ui_lock);
    RECT box = client_box_l(w), o;
    rlist a = upd_load_l(w), c;
    for (const RECT &e : a) if (rc_isect(e, box, &o)) c.push_back(o);
    bool pend = w->erase && !c.empty();
    if (pend) w->erase = 0;
    os_unlock(&g_ui_lock);
    if (pend) {
        HDC dc = GetDC(h);
        if (dc) {
            gdi_dc_set_clip(dc, c.data(), (int)c.size());
            SendMessageA(h, 0x0014, (WPARAM)(uintptr_t)dc, 0);   /* WM_ERASEBKGND */
            ReleaseDC(h, dc);
        }
    }
    if (children) {
        HWND kids[256];
        int n = 0;
        os_lock(&g_ui_lock);
        for (window *k = w->first; k && n < 256; k = k->next) kids[n++] = (HWND)k;
        os_unlock(&g_ui_lock);
        for (int i = 0; i < n; i++) fnwin_erase_now(kids[i], true);
    }
}

static LRESULT def_dblclick(window *w, HWND h, WPARAM hit, LPARAM lp)   /* ref handle_nc_button_dbl_click */
{
    if (is_iconic_w(w)) { SendMessageA(h, 0x0112, SC_RESTORE_, lp); return 0; }
    os_lock(&g_ui_lock);
    DWORD st = w->style;
    bool noclose = (w->cls->style & C_NOCLOSE) != 0;
    os_unlock(&g_ui_lock);
    switch ((int)hit) {
    case HT_CAPTION:
        if (st & S_MAXIMIZEBOX) SendMessageA(h, 0x0112, (st & S_MAXIMIZE) ? SC_RESTORE_ : SC_MAXIMIZE_, lp);
        break;
    case HT_SYSMENU:   /* the system menu's Close item is there for a WS_SYSMENU window and enabled unless the class is CS_NOCLOSE */
        if ((st & S_SYSMENU) && !noclose) SendMessageA(h, 0x0112, SC_CLOSE_, lp);
        break;
    case HT_HSCROLL: SendMessageA(h, 0x0112, SC_HSCROLL_ + HT_HSCROLL, lp); break;
    case HT_VSCROLL: SendMessageA(h, 0x0112, SC_VSCROLL_ + HT_VSCROLL, lp); break;
    default: break;
    }
    return 0;
}
static LRESULT def_nclbuttondown(window *w, HWND h, WPARAM wp, LPARAM lp)   /* ref handle_nc_lbutton_down */
{
    os_lock(&g_ui_lock);
    DWORD st = w->style;
    os_unlock(&g_ui_lock);
    switch ((int)wp) {
    case HT_CAPTION: {
        HWND top = root_of(w);
        BOOL ok = SetForegroundWindow(top);
        if (ok || GetActiveWindow() == top) SendMessageA(h, 0x0112, SC_MOVE_ + HT_CAPTION, lp);
        break; }
    case HT_SYSMENU: if (st & S_SYSMENU) SendMessageA(h, 0x0112, SC_MOUSEMENU_ + HT_SYSMENU, lp); break;
    case HT_MENU: SendMessageA(h, 0x0112, SC_MOUSEMENU_, lp); break;
    case HT_HSCROLL: SendMessageA(h, 0x0112, SC_HSCROLL_ + HT_HSCROLL, lp); break;
    case HT_VSCROLL: SendMessageA(h, 0x0112, SC_VSCROLL_ + HT_VSCROLL, lp); break;
    case HT_MINBUTTON: case HT_MAXBUTTON: case HT_CLOSE: track_caption_button(w, (int)wp, lp); break;
    case HT_LEFT: case HT_RIGHT: case HT_TOP: case HT_TOPLEFT: case HT_TOPRIGHT: case HT_BOTTOM: case HT_BOTTOMLEFT: case HT_BOTTOMRIGHT:
        SendMessageA(h, 0x0112, SC_SIZE_ + wp - (HT_LEFT - 1), lp);   /* SC_SIZE + WMSZ_* */
        break;
    default: break;   /* HTBORDER, and the rest: nothing */
    }
    return 0;
}
static LRESULT def_ncrbuttondown(window *w, HWND h, WPARAM wp, LPARAM)   /* ref handle_nc_rbutton_down */
{
    if (wp != HT_CAPTION && wp != HT_SYSMENU) return 0;
    SetCapture(h);
    MSG m;
    int ht = (int)wp;
    memset(&m, 0, sizeof m);
    while (fnmq_get_message(&m, NULL, 0x200, 0x20E))
        if (m.message == 0x0205) { ht = hit_test(w, (int)m.pt.x, (int)m.pt.y); break; }
    ReleaseCapture();
    if (ht == HT_CAPTION || ht == HT_SYSMENU) SendMessageA(h, 0x007B, (WPARAM)(uintptr_t)h, LPOINT(m.pt.x, m.pt.y));
    return 0;
}
static LRESULT def_sysmenu_key(window *w, HWND h, WPARAM wp, LPARAM lp)   /* ref WM_SYSKEYDOWN */
{
    bool menu_key = wp == 0x12 || wp == 0xA4 || wp == 0xA5;   /* VK_MENU, VK_LMENU, VK_RMENU */
    if ((lp >> 29) & 1) {   /* KEYDATA_ALT */
        g_menu_sys_key = menu_key && !g_menu_sys_key;
        g_f10_key = false;
        if (wp == 0x73) {   /* VK_F4: close the top-level window */
            HWND top = root_of(w);
            window *t = wnd(top);
            if (t && !(t->cls->style & C_NOCLOSE)) PostMessageA(top, 0x0112, SC_CLOSE_, 0);
        }
    } else if (wp == 0x79) {   /* VK_F10 */
        if (GetKeyState(0x10) & 0x8000) SendMessageA(h, 0x007B, (WPARAM)(uintptr_t)h, (LPARAM)-1);
        g_f10_key = true;
    } else if (wp == 0x1B && (GetKeyState(0x10) & 0x8000))
        SendMessageA(h, 0x0112, SC_KEYMENU_, ' ');
    return 0;
}
static LRESULT def_keyup(window *w, HWND, WPARAM wp, LPARAM)   /* ref WM_KEYUP, WM_SYSKEYUP: Alt or F10 pressed and released */
{
    if (((wp == 0x12 || wp == 0xA4 || wp == 0xA5) && g_menu_sys_key) || (wp == 0x79 && g_f10_key))
        SendMessageA(root_of(w), 0x0112, SC_KEYMENU_, 0);
    g_menu_sys_key = g_f10_key = false;
    return 0;
}
static LRESULT def_syschar(window *w, HWND h, WPARAM wp, LPARAM lp)   /* ref WM_SYSCHAR */
{
    g_menu_sys_key = false;
    if (wp == '\r' && is_iconic_w(w)) { PostMessageA(h, 0x0112, SC_RESTORE_, 0); return 0; }
    if (((lp >> 29) & 1) && wp) {
        if (wp == '\t' || wp == 0x1B) return 0;
        if (wp == ' ' && (w->style & S_CHILD)) send_parent(w, 0x0106, wp, lp);
        else SendMessageA(h, 0x0112, SC_KEYMENU_, wp);
    }
    return 0;   /* a Ctrl-Esc or an unhandled character would beep: the platform has no system sounds */
}
static LRESULT def_rbuttonup(window *w, HWND h, WPARAM, LPARAM lp)
{
    POINT p = { (long)(short)(lp & 0xFFFF), (long)(short)((lp >> 16) & 0xFFFF) };
    ClientToScreen(h, &p);
    SendMessageA(h, 0x007B, (WPARAM)(uintptr_t)h, LPOINT(p.x, p.y));   /* WM_CONTEXTMENU */
    (void)w;
    return 0;
}
static LRESULT def_xbuttonup(window *, HWND h, WPARAM wp, LPARAM)
{
    unsigned which = (unsigned)((wp >> 16) & 0xFFFF);
    if (which == 1 || which == 2)   /* XBUTTON1, XBUTTON2: WM_APPCOMMAND, FAPPCOMMAND_MOUSE */
        SendMessageA(h, 0x0319, (WPARAM)(uintptr_t)h, LPOINT(wp & 0xFFFF, 0x8000 | which));
    return 0;
}
static LRESULT def_mousewheel(window *w, HWND h, WPARAM wp, LPARAM lp)
{
    if (!(w->style & S_CHILD)) return 0;
    os_lock(&g_ui_lock);
    window *p = parent_l(w);
    os_unlock(&g_ui_lock);
    (void)h;
    return p ? SendMessageA((HWND)p, 0x020A, wp, lp) : 0;
}
static LRESULT def_mousehwheel(window *w, HWND, WPARAM wp, LPARAM lp) { return (w->style & S_CHILD) ? send_parent(w, 0x020E, wp, lp) : 0; }
static LRESULT def_print(window *w, HWND h, WPARAM wp, LPARAM lp)   /* ref WM_PRINT: PRF_ERASEBKGND then PRF_CLIENT */
{
    if ((lp & 1 /* PRF_CHECKVISIBLE */) && !IsWindowVisible(h)) return 0;
    if (lp & 8 /* PRF_ERASEBKGND */) SendMessageA(h, 0x0014, wp, 0);
    if (lp & 4 /* PRF_CLIENT */) SendMessageA(h, 0x0318, wp, lp);
    (void)w;
    return 0;
}
static LRESULT def_appcommand(window *w, HWND, WPARAM wp, LPARAM lp)
{
    os_lock(&g_ui_lock);
    window *p = parent_l(w);
    os_unlock(&g_ui_lock);
    if (!p) fnmq_hook(10 /* WH_SHELL */, 12 /* HSHELL_APPCOMMAND */, wp, lp);
    else SendMessageA((HWND)p, 0x0319, wp, lp);
    return 0;
}
static LRESULT def_none(window *, HWND, WPARAM, LPARAM) { return 0; }
static LRESULT def_buttondown_reset(window *, HWND, WPARAM, LPARAM) { g_f10_key = g_menu_sys_key = false; return 0; }
static LRESULT def_keydown(window *, HWND, WPARAM wp, LPARAM) { if (wp == 0x79) g_f10_key = true; return 0; }
static LRESULT def_lbuttondbl(window *w, HWND h, WPARAM, LPARAM lp) { return def_dblclick(w, h, HT_CLIENT, lp); }
static LRESULT def_activate(window *w, HWND h, WPARAM wp, LPARAM) { if ((wp & 0xFFFF) != 0 && !is_iconic_w(w)) SetFocus(h); return 0; }
static LRESULT def_setredraw(window *w, HWND h, WPARAM wp, LPARAM)
{
    if (wp) set_style_bits(w, S_VISIBLE, 0);
    else { validate_tree(h); set_style_bits(w, 0, S_VISIBLE); }
    return 0;
}
static LRESULT def_settext(window *w, HWND, WPARAM, LPARAM lp) { set_text_l(w, lp ? (const char *)(uintptr_t)lp : ""); return TRUE; }
static LRESULT def_gettext(window *w, HWND, WPARAM wp, LPARAM lp)
{
    if (!lp || !wp) return 0;
    os_lock(&g_ui_lock);
    size_t n = strlen(w->text), k = n < (size_t)wp - 1 ? n : (size_t)wp - 1;
    memcpy((char *)(uintptr_t)lp, w->text, k);
    ((char *)(uintptr_t)lp)[k] = 0;
    os_unlock(&g_ui_lock);
    return (LRESULT)k;
}
static LRESULT def_gettextlen(window *w, HWND, WPARAM, LPARAM) { os_lock(&g_ui_lock); size_t n = strlen(w->text); os_unlock(&g_ui_lock); return (LRESULT)n; }
static LRESULT def_close(window *, HWND h, WPARAM, LPARAM) { DestroyWindow(h); return 0; }
static LRESULT def_one(window *, HWND, WPARAM, LPARAM) { return 1; }
static LRESULT def_minus1(window *, HWND, WPARAM, LPARAM) { return (LRESULT)-1; }
static LRESULT def_showwindow(window *w, HWND h, WPARAM wp, LPARAM lp)   /* ref WM_SHOWWINDOW: the owner's pop-ups follow its minimize and restore */
{
    if (!lp) return 0;   /* sent from ShowWindow */
    os_lock(&g_ui_lock);
    bool vis = (w->style & S_VISIBLE) != 0, owned = w->owner != NULL, act = false;
    if (!((vis && wp) || (!vis && !wp)) && owned) {
        if (wp) { if (w->owned_hidden) { w->owned_hidden = 0; act = true; } }
        else { w->owned_hidden = 1; act = true; }
    }
    os_unlock(&g_ui_lock);
    if (act) ShowWindow(h, wp ? 4 /* SW_SHOWNOACTIVATE */ : 0);
    return 0;
}
static LRESULT def_cancelmode(window *, HWND h, WPARAM, LPARAM) { g_menu_sys_key = false; if (GetCapture() == h) ReleaseCapture(); return 0; }
static LRESULT def_mouseactivate(window *w, HWND, WPARAM wp, LPARAM lp)
{
    if (w->style & S_CHILD) { LRESULT r = send_parent(w, 0x21, wp, lp); if (r) return r; }
    return (((lp >> 16) & 0xFFFF) == 0x0201 && (lp & 0xFFFF) == HT_CAPTION) ? 3 /* MA_NOACTIVATE */ : 1 /* MA_ACTIVATE */;
}
static LRESULT def_isactiveicon(window *w, HWND, WPARAM, LPARAM) { os_lock(&g_ui_lock); int a = w->ncactive; os_unlock(&g_ui_lock); return a != 0; }
static LRESULT def_querydragicon(window *w, HWND, WPARAM, LPARAM)
{
    if (w->cls->icon) return (LRESULT)(uintptr_t)w->cls->icon;
    for (unsigned id = 1; id < 64; id++) { HICON i = LoadIconA(w->inst, (LPCSTR)(uintptr_t)id); if (i) return (LRESULT)(uintptr_t)i; }
    return (LRESULT)(uintptr_t)LoadIconA(NULL, (LPCSTR)32512);   /* IDI_APPLICATION */
}
static LRESULT def_wpchanging(window *w, HWND h, WPARAM, LPARAM lp)   /* ref: an overlapped or sizing-border window stays within its track sizes */
{
    fn_wpos *p = (fn_wpos *)(uintptr_t)lp;
    if (!p || (p->flags & P_NOSIZE)) return 0;
    os_lock(&g_ui_lock);
    DWORD st = w->style;
    os_unlock(&g_ui_lock);
    if (!(st & S_THICK) && (st & (S_POPUP | S_CHILD))) return 0;
    fn_mmi mi;
    fill_mmi(w, &mi);
    SendMessageA(h, M_GETMINMAXINFO, 0, (LPARAM)(uintptr_t)&mi);
    if (p->cx > mi.maxtrack.x) p->cx = (int)mi.maxtrack.x;
    if (p->cy > mi.maxtrack.y) p->cy = (int)mi.maxtrack.y;
    if (!(st & S_MINIMIZE)) {
        if (p->cx < mi.mintrack.x) p->cx = (int)mi.mintrack.x;
        if (p->cy < mi.mintrack.y) p->cy = (int)mi.mintrack.y;
    }
    return 0;
}
static LRESULT def_wpchanged(window *w, HWND h, WPARAM, LPARAM lp)   /* ref: WM_SIZE and WM_MOVE follow a size or position change */
{
    fn_wpos *p = (fn_wpos *)(uintptr_t)lp;
    if (!p) return 0;
    os_lock(&g_ui_lock);
    RECT c = client_l(w);
    long ox, oy;
    origin_l(w->parent, &ox, &oy);
    DWORD st = w->style;
    os_unlock(&g_ui_lock);
    if (!(p->flags & P_NOSIZE)) SendMessageA(h, M_SIZE, (st & S_MINIMIZE) ? 1 : (st & S_MAXIMIZE) ? 2 : 0, LPOINT(c.right - c.left, c.bottom - c.top));
    if (!(p->flags & P_NOMOVE)) SendMessageA(h, M_MOVE, 0, LPOINT(c.left - ox, c.top - oy));
    return 0;
}
static LRESULT def_keyf1(window *w, HWND h, WPARAM, LPARAM)   /* ref WM_KEYF1: WM_HELP for the window */
{
    fn_helpinfo hi;
    memset(&hi, 0, sizeof hi);
    hi.cb = sizeof hi; hi.ctx = 1 /* HELPINFO_WINDOW */; hi.ctl = (int)w->id; hi.item = (HANDLE)h;
    GetCursorPos(&hi.pos);
    SendMessageA(h, 0x0053, 0, (LPARAM)(uintptr_t)&hi);
    return 0;
}
static LRESULT def_inputlang(window *w, HWND, WPARAM wp, LPARAM lp) { foreach_child(w, send_to, 0x51, wp, lp); return 0; }
static LRESULT def_help(window *w, HWND, WPARAM wp, LPARAM lp) { return send_parent(w, 0x53, wp, lp); }
static LRESULT def_contextmenu(window *w, HWND, WPARAM wp, LPARAM lp)   /* a child passes it on; a caption would pop up the system menu */
{
    if (w->style & S_CHILD) send_parent(w, 0x7B, wp, lp);
    return 0;
}
static LRESULT def_stylechanged(window *w, HWND h, WPARAM wp, LPARAM lp)
{
    fn_stylestruct *s = (fn_stylestruct *)(uintptr_t)lp;
    if ((int)wp == -16 && s && (w->exstyle & X_LAYERED) && ((s->old ^ s->now) & (S_CAPTION | S_THICK | S_VSCROLL | S_HSCROLL)))
        SetWindowPos(h, NULL, 0, 0, 0, 0, P_FRAMECHANGED | P_NOACTIVATE | P_NOZORDER | P_NOSIZE | P_NOMOVE);
    return 0;
}
static LRESULT def_geticon(window *w, HWND, WPARAM wp, LPARAM)
{
    os_lock(&g_ui_lock);
    HICON r = (wp == 0 || wp == 2) ? w->icon_small : wp == 1 ? w->icon_big : NULL;
    os_unlock(&g_ui_lock);
    return (LRESULT)(uintptr_t)r;
}
static LRESULT def_seticon(window *w, HWND, WPARAM wp, LPARAM lp)
{
    os_lock(&g_ui_lock);
    HICON old = NULL;
    if (wp == 0) { old = w->icon_small; w->icon_small = (HICON)(uintptr_t)lp; }
    else if (wp == 1) { old = w->icon_big; w->icon_big = (HICON)(uintptr_t)lp; }
    os_unlock(&g_ui_lock);
    return (LRESULT)(uintptr_t)old;
}
static LRESULT def_nccreate(window *w, HWND, WPARAM, LPARAM lp)
{
    fnv_createstruct *cs = (fnv_createstruct *)(uintptr_t)lp;
    if (!cs) return 0;
    set_text_l(w, cs->name);
    return 1;
}
static LRESULT def_ncdestroy(window *w, HWND, WPARAM, LPARAM) { set_text_l(w, ""); return 0; }
static LRESULT def_nccalcsize(window *w, HWND, WPARAM wp, LPARAM lp)
{
    if (!lp) return 0;
    RECT *r = wp ? &((fn_nccs *)(uintptr_t)lp)->rgrc[0] : (RECT *)(uintptr_t)lp;
    os_lock(&g_ui_lock);
    DWORD st = w->style, ex = w->exstyle;
    bool menu = w->hasmenu != 0;
    os_unlock(&g_ui_lock);
    if (st & S_MINIMIZE) return 0;
    nc_t n = ncrule(st, ex, menu);
    r->left += n.l; r->top += n.t; r->right -= n.r; r->bottom -= n.b;
    if (r->bottom < r->top) r->bottom = r->top;
    if (r->right < r->left) r->right = r->left;
    return 0;
}
static LRESULT def_nchittest(window *w, HWND, WPARAM, LPARAM lp) { return hit_test(w, (int)(short)(lp & 0xFFFF), (int)(short)((lp >> 16) & 0xFFFF)); }
static LRESULT def_ncpaint(window *, HWND h, WPARAM, LPARAM) { fnwin_take_frame_paint(h); return 0; }
static LRESULT def_ncactivate(window *w, HWND, WPARAM wp, LPARAM) { os_lock(&g_ui_lock); w->ncactive = wp != 0; os_unlock(&g_ui_lock); return TRUE; }
static LRESULT def_syncpaint(window *, HWND h, WPARAM, LPARAM) { fnwin_erase_now(h, true); return 0; }
static LRESULT def_ctl(unsigned type, WPARAM wp) { return def_ctlcolor((HDC)(uintptr_t)wp, type); }
static LRESULT def_ctl0(window *, HWND, WPARAM wp, LPARAM) { return def_ctl(0, wp); }
static LRESULT def_ctl1(window *, HWND, WPARAM wp, LPARAM) { return def_ctl(1, wp); }
static LRESULT def_ctl2(window *, HWND, WPARAM wp, LPARAM) { return def_ctl(2, wp); }
static LRESULT def_ctl3(window *, HWND, WPARAM wp, LPARAM) { return def_ctl(3, wp); }
static LRESULT def_ctl4(window *, HWND, WPARAM wp, LPARAM) { return def_ctl(4, wp); }
static LRESULT def_ctl5(window *, HWND, WPARAM wp, LPARAM) { return def_ctl(5, wp); }
static LRESULT def_ctl6(window *, HWND, WPARAM wp, LPARAM) { return def_ctl(6, wp); }
static LRESULT def_ctlold(window *, HWND, WPARAM wp, LPARAM lp) { return def_ctl((unsigned)((lp >> 16) & 0xFFFF), wp); }
static LRESULT def_dropobject(window *, HWND, WPARAM, LPARAM) { return 0x454C4946; }   /* DRAG_FILE */
static LRESULT def_querydrop(window *w, HWND, WPARAM, LPARAM) { return (w->exstyle & X_ACCEPTFILES) != 0; }
static LRESULT def_erase_msg(window *w, HWND h, WPARAM wp, LPARAM lp) { return def_erase(w, h, wp, lp); }
static LRESULT def_syscmd_msg(window *w, HWND h, WPARAM wp, LPARAM lp) { return def_syscommand(w, h, wp, lp); }
static LRESULT def_setcursor_msg(window *w, HWND h, WPARAM wp, LPARAM lp) { return def_setcursor(w, h, wp, lp); }
static LRESULT def_paint_msg(window *w, HWND h, WPARAM wp, LPARAM lp) { return def_paint(w, h, wp, lp); }

/* ---- THE table: message -> rule. Sorted by message number; src: where the rule comes from. */
struct defrule { UINT msg; LRESULT (*fn)(window *, HWND, WPARAM, LPARAM); const char *src; };
static const defrule k_defrules[] = {
    { 0x0006, def_activate, "ref WM_ACTIVATE: an activated, not minimized window takes the keyboard focus" },
    { 0x000B, def_setredraw, "MSDN WM_SETREDRAW; ref: WS_VISIBLE follows, the region is validated when redraw is off" },
    { 0x000C, def_settext, "MSDN WM_SETTEXT: stored, TRUE" },
    { 0x000D, def_gettext, "MSDN WM_GETTEXT: at most wParam - 1 characters and the terminator, the count copied" },
    { 0x000E, def_gettextlen, "MSDN WM_GETTEXTLENGTH" },
    { 0x000F, def_paint_msg, "MSDN WM_PAINT: BeginPaint + EndPaint validates" },
    { 0x0010, def_close, "MSDN WM_CLOSE: DestroyWindow" },
    { 0x0011, def_one, "MSDN WM_QUERYENDSESSION: the session may end" },
    { 0x0013, def_one, "MSDN WM_QUERYOPEN: the icon may open" },
    { 0x0014, def_erase_msg, "MSDN WM_ERASEBKGND: the class background brush" },
    { 0x0018, def_showwindow, "ref WM_SHOWWINDOW: owned pop-ups follow their owner" },
    { 0x0019, def_ctlold, "MSDN WM_CTLCOLOR (16-bit form: the control type in the high word of lParam)" },
    { 0x001F, def_cancelmode, "MSDN WM_CANCELMODE: menu mode ends, the capture is released" },
    { 0x0020, def_setcursor_msg, "MSDN WM_SETCURSOR: parent first, the class cursor in the client area, a sizing cursor on the border" },
    { 0x0021, def_mouseactivate, "ref WM_MOUSEACTIVATE: parent first; MA_ACTIVATE, MA_NOACTIVATE for a caption click" },
    { 0x0026, def_paint_msg, "MSDN WM_PAINTICON" },
    { 0x0027, def_erase_msg, "MSDN WM_ICONERASEBKGND" },
    { 0x002E, def_minus1, "MSDN WM_VKEYTOITEM: -1, the list box does its default" },
    { 0x002F, def_minus1, "MSDN WM_CHARTOITEM: -1, the list box does its default" },
    { 0x0035, def_isactiveicon, "ref WM_ISACTIVEICON: the frame is drawn active" },
    { 0x0037, def_querydragicon, "ref WM_QUERYDRAGICON: class icon, else the application's first icon, else IDI_APPLICATION" },
    { 0x0046, def_wpchanging, "ref WM_WINDOWPOSCHANGING: track sizes" },
    { 0x0047, def_wpchanged, "ref WM_WINDOWPOSCHANGED: WM_SIZE and WM_MOVE" },
    { 0x004D, def_keyf1, "ref WM_KEYF1: WM_HELP for the window" },
    { 0x0050, def_none, "MSDN WM_INPUTLANGCHANGEREQUEST: ActivateKeyboardLayout; the environment has the one layout (ENV I4), already active" },
    { 0x0051, def_inputlang, "ref WM_INPUTLANGCHANGE: passed on to the children" },
    { 0x0053, def_help, "ref WM_HELP: the parent" },
    { 0x0055, def_one, "ref WM_NOTIFYFORMAT: NFR_ANSI (an ANSI window)" },
    { 0x007B, def_contextmenu, "ref WM_CONTEXTMENU: a child passes it on; the system menu has no menu subsystem to pop up" },
    { 0x007D, def_stylechanged, "ref WM_STYLECHANGED: a layered window's frame change is re-framed" },
    { 0x007F, def_geticon, "ref WM_GETICON" },
    { 0x0080, def_seticon, "ref WM_SETICON" },
    { 0x0081, def_nccreate, "ref WM_NCCREATE: the creation text is stored; creation goes on" },
    { 0x0082, def_ncdestroy, "ref WM_NCDESTROY: the window text goes" },
    { 0x0083, def_nccalcsize, "ref WM_NCCALCSIZE: the client rectangle less the non-client area" },
    { 0x0084, def_nchittest, "MSDN WM_NCHITTEST" },
    { 0x0085, def_ncpaint, "MSDN WM_NCPAINT: the frame has no surface of its own in the platform (client surfaces are presented): nothing is pending after it" },
    { 0x0086, def_ncactivate, "ref WM_NCACTIVATE: the frame takes the active state; TRUE" },
    { 0x0088, def_syncpaint, "ref WM_SYNCPAINT: RDW_ERASENOW | RDW_ERASE | RDW_ALLCHILDREN" },
    { 0x00A0, def_none, "ref WM_NCMOUSEMOVE: only scroll-bar hot tracking (no scroll bars in the platform)" },
    { 0x00A1, def_nclbuttondown, "ref WM_NCLBUTTONDOWN" },
    { 0x00A3, [](window *w, HWND h, WPARAM wp, LPARAM lp) -> LRESULT { return def_dblclick(w, h, wp, lp); }, "ref WM_NCLBUTTONDBLCLK" },
    { 0x00A4, def_ncrbuttondown, "ref WM_NCRBUTTONDOWN" },
    { 0x00A5, def_none, "ref WM_NCRBUTTONUP: nothing" },
    { 0x00AC, def_xbuttonup, "ref WM_NCXBUTTONUP: WM_APPCOMMAND" },
    { 0x0100, def_keydown, "ref WM_KEYDOWN: F10 pressed" },
    { 0x0101, def_keyup, "ref WM_KEYUP: Alt or F10 released" },
    { 0x0104, def_sysmenu_key, "ref WM_SYSKEYDOWN: Alt+F4 closes, F10 and Alt arm the menu key" },
    { 0x0105, def_keyup, "ref WM_SYSKEYUP" },
    { 0x0106, def_syschar, "ref WM_SYSCHAR: the Alt+character menu key" },
    { 0x010D, def_none, "ref WM_IME_STARTCOMPOSITION: to the default IME window; the environment has no IME (SM_IMMENABLED 0)" },
    { 0x010E, def_none, "ref WM_IME_ENDCOMPOSITION: as above" },
    { 0x010F, def_none, "ref WM_IME_COMPOSITION: as above" },
    { 0x0112, def_syscmd_msg, "MSDN WM_SYSCOMMAND" },
    { 0x0132, def_ctl0, "MSDN WM_CTLCOLORMSGBOX" },
    { 0x0133, def_ctl1, "MSDN WM_CTLCOLOREDIT" },
    { 0x0134, def_ctl2, "MSDN WM_CTLCOLORLISTBOX" },
    { 0x0135, def_ctl3, "MSDN WM_CTLCOLORBTN" },
    { 0x0136, def_ctl4, "MSDN WM_CTLCOLORDLG" },
    { 0x0137, def_ctl5, "MSDN WM_CTLCOLORSCROLLBAR" },
    { 0x0138, def_ctl6, "MSDN WM_CTLCOLORSTATIC" },
    { 0x0201, def_buttondown_reset, "ref WM_LBUTTONDOWN" },
    { 0x0203, def_lbuttondbl, "ref WM_LBUTTONDBLCLK: the client double click" },
    { 0x0204, def_buttondown_reset, "ref WM_RBUTTONDOWN" },
    { 0x0205, def_rbuttonup, "ref WM_RBUTTONUP: WM_CONTEXTMENU" },
    { 0x0207, def_buttondown_reset, "ref WM_MBUTTONDOWN" },
    { 0x020A, def_mousewheel, "ref WM_MOUSEWHEEL: a child passes it on" },
    { 0x020C, def_xbuttonup, "ref WM_XBUTTONUP: WM_APPCOMMAND" },
    { 0x020E, def_mousehwheel, "ref WM_MOUSEHWHEEL: a child passes it on" },
    { 0x0218, def_one, "MSDN WM_POWERBROADCAST: a query is granted" },
    { 0x022A, def_dropobject, "ref WM_DROPOBJECT: DRAG_FILE" },
    { 0x022B, def_querydrop, "ref WM_QUERYDROPOBJECT: whether the window accepts files" },
    { 0x0281, def_none, "ref WM_IME_SETCONTEXT: to the default IME window; the environment has no IME" },
    { 0x0282, def_none, "ref WM_IME_NOTIFY: as above" },
    { 0x02A2, def_none, "ref WM_NCMOUSELEAVE: only scroll-bar hot tracking" },
    { 0x0313, def_none, "ref WM_POPUPSYSTEMMENU: the system menu (no menu subsystem)" },
    { 0x0317, def_print, "ref WM_PRINT: PRF_ERASEBKGND, PRF_CLIENT" },
    { 0x0319, def_appcommand, "ref WM_APPCOMMAND: the parent, else the shell hook" },
};
/* the dispatch: a message with a row runs it; every other message has no documented default action and answers 0 */
static LRESULT def_dispatch(window *w, HWND h, UINT m, WPARAM wp, LPARAM lp)
{
    for (const defrule &r : k_defrules)
        if (r.msg == m) return r.fn(w, h, wp, lp);
    return 0;
}
extern "C" LRESULT DefWindowProcA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    window *win = wnd(h);
    return win ? def_dispatch(win, h, m, w, l) : 0;
}

extern "C" BOOL ShowWindow(HWND h, int cmd)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    if (cmd < 0 || cmd > 11) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return FALSE;
    }
    return show_impl(w, cmd, false);
}
/* keep_vis: change the state only, leaving the window's visibility as it is (creation of a hidden window) */
static BOOL show_impl(window *w, int cmd, bool keep_vis)
{
    HWND h = (HWND)w;
    if (w == &g_desk)
        return TRUE;
    /* target state (0 normal, 1 minimized, 2 maximized), visibility and activation of each command */
    os_lock(&g_ui_lock);
    bool was = (w->style & S_VISIBLE) != 0, restore_max = w->restore_max != 0;
    int cur = (w->style & S_MINIMIZE) ? 1 : (w->style & S_MAXIMIZE) ? 2 : 0, target = cur, vis = 1, act = 1;
    os_unlock(&g_ui_lock);
    switch (cmd) {
    case 0: vis = act = 0; break;                              /* SW_HIDE */
    case 1: case 10: target = 0; break;                        /* SW_SHOWNORMAL, SW_SHOWDEFAULT */
    case 2: target = 1; break;                                 /* SW_SHOWMINIMIZED */
    case 3: target = 2; break;                                 /* SW_SHOWMAXIMIZED */
    case 4: target = 0; act = 0; break;                        /* SW_SHOWNOACTIVATE */
    case 5: break;                                             /* SW_SHOW */
    case 6: case 11: target = 1; act = 0; break;               /* SW_MINIMIZE, SW_FORCEMINIMIZE */
    case 7: target = 1; act = 0; break;                        /* SW_SHOWMINNOACTIVE */
    case 8: act = 0; break;                                    /* SW_SHOWNA */
    case 9: target = (cur == 1 && restore_max) ? 2 : 0; break; /* SW_RESTORE */
    }
    if (keep_vis) { vis = was; act = 0; }
    bool statechg = target != cur;
    if (statechg || vis != was) {
        RECT r;
        if (target == cur) { os_lock(&g_ui_lock); r = w->wr; os_unlock(&g_ui_lock); }
        else if (target == 2) {
            fn_mmi mi;
            fill_mmi(w, &mi);
            SendMessageA(h, M_GETMINMAXINFO, 0, (LPARAM)(uintptr_t)&mi);
            os_lock(&g_ui_lock);
            RECT c = container_l(w);
            os_unlock(&g_ui_lock);
            r.left = c.left + mi.maxpos.x; r.top = c.top + mi.maxpos.y;
            r.right = r.left + mi.maxsize.x; r.bottom = r.top + mi.maxsize.y;
        } else {
            os_lock(&g_ui_lock);
            if (target == 0) r = w->rr;
            else {
                RECT c = container_l(w);
                long mx = w->parent == &g_desk ? -32000 : c.left, my = w->parent == &g_desk ? -32000 : c.bottom - GetSystemMetrics(58);
                r.left = mx; r.top = my; r.right = mx + GetSystemMetrics(57); r.bottom = my + GetSystemMetrics(58);
            }
            os_unlock(&g_ui_lock);
        }
        os_lock(&g_ui_lock);
        w->restore_max = target == 1 ? (cur == 2 ? 1 : cur == 1 ? w->restore_max : 0) : 0;
        long ox, oy;
        origin_l(w->parent, &ox, &oy);
        os_unlock(&g_ui_lock);
        UINT f = P_NOZORDER | (vis ? P_SHOWWINDOW : P_HIDEWINDOW) | (act ? 0 : P_NOACTIVATE) | (statechg ? P_FORCE : (P_NOMOVE | P_NOSIZE));
        if (!set_pos(w, NULL, (int)(r.left - ox), (int)(r.top - oy), (int)(r.right - r.left), (int)(r.bottom - r.top), f, statechg ? target : -1))
            return was;
        if (target == 1 && (cmd == 6 || cmd == 11))
            deactivate(w);   /* the next top-level window in z-order becomes active */
    } else if (act && vis && !(w->style & S_CHILD))
        activate(w);
    return was;
}

extern "C" BOOL UpdateWindow(HWND h)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    os_lock(&g_ui_lock);
    bool vis = true;
    for (window *p = w; p && p != &g_desk; p = p->parent)
        vis = vis && (p->style & S_VISIBLE);
    bool need = vis && w->paint;
    os_unlock(&g_ui_lock);
    if (need)
        SendMessageA(h, M_PAINT, 0, 0);   /* DefWindowProc validates; a procedure that does not leaves the region */
    return TRUE;
}
extern "C" BOOL SetForegroundWindow(HWND h)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    os_lock(&g_ui_lock);
    while (w->parent && w->parent != &g_desk && (w->style & S_CHILD)) w = w->parent;
    bool ok = w != &g_desk && (w->style & S_VISIBLE) && !(w->style & (S_CHILD | S_DISABLED));
    os_unlock(&g_ui_lock);
    if (!ok) {
        SetLastError(5);   /* a hidden window cannot be brought to the foreground */
        return FALSE;
    }
    activate(w);
    return TRUE;
}
extern "C" HWND GetForegroundWindow(void) { os_lock(&g_ui_lock); HWND h = (HWND)g_active; os_unlock(&g_ui_lock); return h; }
extern "C" HWND GetActiveWindow(void) { return GetForegroundWindow(); }
/* MSDN GetLastActivePopup: for an unowned top-level window the owned popup that was most recently active (activation is
 * recorded in note_active_l); hwnd itself when it was the last active, owns no popup, is a child or is owned. A window
 * that is not one: NULL with ERROR_INVALID_WINDOW_HANDLE. */
extern "C" HWND GetLastActivePopup(HWND h)
{
    window *w = wnd_e(h);
    if (!w)
        return NULL;
    os_lock(&g_ui_lock);
    window *r = (w->style & S_CHILD) || w->owner ? w : (w->lastpop ? w->lastpop : w);
    os_unlock(&g_ui_lock);
    return (HWND)r;
}

extern "C" BOOL SetWindowTextA(HWND h, LPCSTR t)
{
    if (!wnd_e(h))
        return FALSE;
    return SendMessageA(h, 0x000C, 0, (LPARAM)(uintptr_t)(t ? t : "")) != 0;   /* WM_SETTEXT */
}
extern "C" int GetWindowTextA(HWND h, LPSTR out, int n)
{
    if (!wnd_e(h))
        return 0;
    if (!out || n <= 0)
        return 0;
    out[0] = 0;
    SetLastError(0);
    return (int)SendMessageA(h, 0x000D, (WPARAM)n, (LPARAM)(uintptr_t)out);   /* WM_GETTEXT: the procedure answers */
}
extern "C" int GetClassNameA(HWND h, LPSTR out, int n)
{
    window *w = wnd_e(h);
    if (!w)
        return 0;
    if (!out || n <= 0) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    size_t len = strlen(w->cls->name), k = len < (size_t)n - 1 ? len : (size_t)n - 1;
    memcpy(out, w->cls->name, k);
    out[k] = 0;
    return (int)k;
}

/* SetParent through GWL_HWNDPARENT: a child keeps its place inside the new parent's client area; a top-level window
 * gets a new owner. Returns the previous parent/owner. */
static HWND reparent(window *w, HWND hnp)
{
    os_lock(&g_ui_lock);
    window *np = hnp ? wnd_l(hnp) : (w->style & S_CHILD) ? &g_desk : NULL;
    int err = (hnp && !np) ? 1400 : 0;
    for (window *p = np; p && !err; p = p->parent)
        if (p == w) err = ERROR_INVALID_PARAMETER;   /* a window cannot become its own descendant's child */
    HWND old = NULL;
    if (!err && (w->style & S_CHILD)) {
        old = (HWND)w->parent;
        long ox, oy, nx, ny;
        origin_l(w->parent, &ox, &oy);
        RECT before = w->wr, oc = client_l(w);
        unlink_l(w);
        insert_after_l(np, w, NULL);
        origin_l(np, &nx, &ny);
        long dx = nx - ox, dy = ny - oy;
        w->wr.left += dx; w->wr.right += dx; w->wr.top += dy; w->wr.bottom += dy;
        w->rr.left += dx; w->rr.right += dx; w->rr.top += dy; w->rr.bottom += dy;
        RECT nc = client_l(w);
        shift_l(w, nc.left - oc.left, nc.top - oc.top);
        (void)before;
    } else if (!err) {
        old = (HWND)w->owner;
        while (np && (np->style & S_CHILD) && np->parent != &g_desk) np = np->parent;
        w->owner = np == &g_desk ? NULL : np;
    }
    os_unlock(&g_ui_lock);
    if (err)
        SetLastError(err);
    return err ? NULL : old;
}

extern "C" LONG GetWindowLongA(HWND h, int idx)
{
    window *w = wnd_e(h);
    if (!w)
        return 0;
    os_lock(&g_ui_lock);
    LONG r = 0;
    int err = 0;
    if (idx >= 0) {
        if (idx + 4 <= w->cls->cbwnd) { int32_t v; memcpy(&v, w->extra + idx, 4); r = v; }
        else err = 1413;
    } else
        switch (idx) {
        case GWL_WNDPROC: r = (LONG)proc_out(w->proc); break;   /* only meaningful with 32-bit pointers (ILP32) */
        case GWL_HINSTANCE: r = (LONG)(uintptr_t)w->inst; break;
        case -8: r = (LONG)(uintptr_t)((w->style & S_CHILD) ? w->parent : w->owner); break;   /* GWL_HWNDPARENT */
        case GWL_ID: r = w->id; break;
        case GWL_STYLE: r = (LONG)w->style; break;
        case GWL_EXSTYLE: r = (LONG)w->exstyle; break;
        case GWL_USERDATA: r = w->userdata; break;
        default: err = 1413;
        }
    os_unlock(&g_ui_lock);
    if (err)
        SetLastError(err);
    return r;
}

extern "C" LONG SetWindowLongA(HWND h, int idx, LONG v)
{
    window *w = wnd_e(h);
    if (!w)
        return 0;
    if (idx == -8)
        return (LONG)(uintptr_t)reparent(w, (HWND)(uintptr_t)v);
    if (idx == GWL_STYLE || idx == GWL_EXSTYLE) {   /* WM_STYLECHANGING may edit the new value, WM_STYLECHANGED reports it */
        os_lock(&g_ui_lock);
        fn_stylestruct ss = { idx == GWL_STYLE ? w->style : w->exstyle, (DWORD)v };
        os_unlock(&g_ui_lock);
        SendMessageA(h, M_STYLECHANGING, (WPARAM)idx, (LPARAM)(uintptr_t)&ss);
        os_lock(&g_ui_lock);
        DWORD &slot = idx == GWL_STYLE ? w->style : w->exstyle;
        DWORD old = slot;
        slot = ss.now;
        ss.old = old;
        os_unlock(&g_ui_lock);
        SendMessageA(h, M_STYLECHANGED, (WPARAM)idx, (LPARAM)(uintptr_t)&ss);
        return (LONG)old;
    }
    os_lock(&g_ui_lock);
    LONG old = 0;
    int err = 0;
    if (idx >= 0) {
        if (idx + 4 <= w->cls->cbwnd) {
            int32_t o, n = (int32_t)v;
            memcpy(&o, w->extra + idx, 4);
            memcpy(w->extra + idx, &n, 4);
            old = o;
        } else
            err = 1413;
    } else
        switch (idx) {
        case GWL_WNDPROC:
            if (w->tid != GetCurrentThreadId()) err = 5;   /* the procedure of another thread's window */
            else { old = (LONG)proc_out(w->proc); w->proc = proc_in((uintptr_t)(DWORD)v); w->hproc = NULL; }
            break;
        case GWL_HINSTANCE: old = (LONG)(uintptr_t)w->inst; w->inst = (HINSTANCE)(uintptr_t)v; break;
        case GWL_ID: old = w->id; w->id = v; break;
        case GWL_USERDATA: old = w->userdata; w->userdata = v; break;
        default: err = 1413;
        }
    os_unlock(&g_ui_lock);
    if (err)
        SetLastError(err);
    return old;
}

extern "C" BOOL AdjustWindowRectEx(LPRECT r, DWORD s, BOOL m, DWORD e)
{
    if (!r) { SetLastError(998); return FALSE; }
    nc_t n = ncrule(s, e, m != 0);
    r->left -= n.l; r->top -= n.t; r->right += n.r; r->bottom += n.b;
    return TRUE;
}
extern "C" BOOL AdjustWindowRect(LPRECT r, DWORD s, BOOL m) { return AdjustWindowRectEx(r, s, m, 0); }

extern "C" BOOL GetClientRect(HWND h, LPRECT r)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    if (!r) { SetLastError(998); return FALSE; }
    os_lock(&g_ui_lock);
    RECT c = client_l(w);
    os_unlock(&g_ui_lock);
    r->left = r->top = 0;
    r->right = c.right - c.left;
    r->bottom = c.bottom - c.top;
    return TRUE;
}
extern "C" BOOL GetWindowRect(HWND h, LPRECT r)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    if (!r) { SetLastError(998); return FALSE; }
    os_lock(&g_ui_lock);
    *r = w->wr;
    os_unlock(&g_ui_lock);
    return TRUE;
}
extern "C" BOOL SetWindowPos(HWND h, HWND a, int x, int y, int cx, int cy, UINT f)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    f &= 0x3FFFu;
    os_lock(&g_ui_lock);
    int err = w == &g_desk ? 5 : ((f & P_SHOWWINDOW) && (f & P_HIDEWINDOW)) ? ERROR_INVALID_PARAMETER : 0;
    if (!err && !(f & P_NOZORDER) && !after_ok_l(w, a))
        err = wnd_l(a) ? ERROR_INVALID_PARAMETER : 1400;   /* a window that is not a sibling / not a window */
    os_unlock(&g_ui_lock);
    if (err) {
        SetLastError(err);
        return FALSE;
    }
    return set_pos(w, a, x, y, cx, cy, f, -1);
}
static BOOL map_point(HWND h, LPPOINT p, int sign)
{
    window *w = wnd_e(h);
    if (!w)
        return FALSE;
    if (!p) { SetLastError(998); return FALSE; }
    os_lock(&g_ui_lock);
    RECT c = client_l(w);
    os_unlock(&g_ui_lock);
    p->x += sign * c.left;
    p->y += sign * c.top;
    return TRUE;
}
extern "C" BOOL ClientToScreen(HWND h, LPPOINT p) { return map_point(h, p, 1); }
extern "C" BOOL ScreenToClient(HWND h, LPPOINT p) { return map_point(h, p, -1); }

static int g_swap_buttons;

extern "C" int fnvnx_swap_buttons(void) { return g_swap_buttons; }   /* SM_SWAPBUTTON (display.cpp) */

/* ---- cursor and keys */
static volatile uint8_t g_keys[256];

/* mouse capture: the window that gets every mouse message wherever the cursor is, until released. The previous holder
 * is told with WM_CAPTURECHANGED (lParam: the window that took it, NULL on release). */
#define W32_WM_CAPTURECHANGED 0x215
extern "C" HWND fnwin_capture(void)
{
    os_lock(&g_ui_lock);
    HWND h = (HWND)g_capture;
    os_unlock(&g_ui_lock);
    return h;
}
static HWND capture_swap(window *w)
{
    os_lock(&g_ui_lock);
    ui_init_l();
    window *old = g_capture;
    g_capture = w;
    os_unlock(&g_ui_lock);
    if (old && old != w)
        SendMessageA((HWND)old, W32_WM_CAPTURECHANGED, 0, (LPARAM)(uintptr_t)w);
    return (HWND)old;
}
extern "C" HWND SetCapture(HWND h)
{
    window *w = wnd_e(h);
    if (!w || w == &g_desk)
        return NULL;
    return capture_swap(w);
}
extern "C" BOOL ReleaseCapture(void)
{
    capture_swap(NULL);
    return TRUE;
}
extern "C" HWND GetCapture(void) { return fnwin_capture(); }
/* g_pressed: went down since GetAsyncKeyState last read it (its low bit); g_toggle: GetKeyState's toggle bit */
static volatile uint8_t g_pressed[256], g_toggle[256];
static void set_key(int vk, int down)
{
    if (down && !g_keys[vk]) {
        g_pressed[vk] = 1;
        if (vk == 0x14 || vk == 0x90 || vk == 0x91)   /* CapsLock, NumLock, ScrollLock */
            g_toggle[vk] ^= 1;
    }
    g_keys[vk] = down ? 1 : 0;
}
extern "C" void fnvnx_set_key_state(int vk, int down)
{
    if (vk < 0 || vk >= 256)
        return;
    set_key(vk, down);
    /* the generic Shift/Control/Alt keys follow their left and right halves, and the other way round */
    static const uint8_t pair[3][3] = { { 0x10, 0xA0, 0xA1 }, { 0x11, 0xA2, 0xA3 }, { 0x12, 0xA4, 0xA5 } };
    for (const uint8_t *p : pair) {
        if (vk == p[0] && !down) { set_key(p[1], 0); set_key(p[2], 0); }
        else if (vk == p[1] || vk == p[2]) set_key(p[0], g_keys[p[1]] | g_keys[p[2]]);
    }
}
extern "C" short GetAsyncKeyState(int vk)
{
    if (vk < 0 || vk >= 256)
        return 0;
    short r = g_keys[vk] ? (short)0x8000 : 0;
    if (g_pressed[vk]) {
        g_pressed[vk] = 0;
        r |= 1;
    }
    return r;
}
extern "C" short GetKeyState(int vk)
{
    if (vk < 0 || vk >= 256)
        return 0;
    return (short)((g_keys[vk] ? 0x8000 : 0) | g_toggle[vk]);
}
extern "C" BOOL SwapMouseButton(BOOL s) { BOOL old = g_swap_buttons != 0; g_swap_buttons = s != 0; return old; }

/* GetSystemMetrics, EnumDisplayDevicesA, ChangeDisplaySettingsA: platform/win32/display.cpp */
/* InitCommonControlsEx, ImageList_*: platform/win32/resource.cpp */

/* ---- MessageBoxA (ENV D3: the text is shown modally by the platform's own dialog over the frame; the pressed button is
 * returned). The dialog is a popup window of the platform's own class (FnvnxMessageBox), owned by hWnd, painted with the
 * GDI text functions into its own surface (the path every window uses: fnvnx_window_surface), with its own message loop on
 * the calling thread. Buttons: every MB_* set, MB_DEFBUTTON1-4, MB_HELP; Tab/Shift+Tab/arrows move the focus (the
 * controller maps to these keys, ENV I1), Return and Space press the focused button, a letter its mnemonic, Escape (and
 * the close command) is IDCANCEL if there is a Cancel button, IDOK if there is only OK, else ignored; the pointer (touch is
 * the mouse, ENV I5) presses a button by down and up on it. MB_APPLMODAL disables the owner, MB_TASKMODAL all the thread's
 * top-level windows (without an owner), MB_SYSTEMMODAL/MB_TOPMOST make it topmost, MB_SETFOREGROUND brings it to the
 * foreground, MB_RIGHT / MB_RTLREADING right-align the text (RTL also mirrors the icon and the buttons). The test hook
 * answers instead of the dialog. */
static fnvnx_msgbox_hook g_msgbox_hook;
extern "C" void fnvnx_set_msgbox_hook(fnvnx_msgbox_hook h) { g_msgbox_hook = h; }

extern "C" {
BOOL ExtTextOutA(HDC, int, int, UINT, const RECT *, LPCSTR, UINT, const INT *);
BOOL GetTextExtentPoint32A(HDC, LPCSTR, int, SIZE *);
BOOL GetTextMetricsA(HDC, void *);
int SetBkMode(HDC, int);
HGDIOBJ GetStockObject(int);
HGDIOBJ SelectObject(HDC, HGDIOBJ);
HDC CreateCompatibleDC(HDC);
BOOL DeleteDC(HDC);
HFONT CreateFontIndirectA(const void *);
BOOL DeleteObject(HGDIOBJ);
int gdi_dc_poly(void *hdc, const int32_t *xy, int n, uint32_t colorref);
}

namespace {
enum { MB_ID_OK = 1, MB_ID_CANCEL = 2, MB_ID_ABORT = 3, MB_ID_RETRY = 4, MB_ID_IGNORE = 5, MB_ID_YES = 6, MB_ID_NO = 7, MB_ID_HELP = 9,
       MB_ID_TRYAGAIN = 10, MB_ID_CONTINUE = 11 };
struct mbbtn { int id; const char *label; char mnem; RECT r; };
struct mbox {
    HWND dlg, owner;
    UINT type;
    std::vector<std::string> lines;
    std::string caption;
    int nb, focus, pressed, result;
    bool done, rtl, right, has_cancel, ok_only;
    mbbtn b[4];
    int icon, margin, title_h, line_h, w, h, text_x, text_y, text_w, icon_x, icon_y, body_h;
};
mbox *g_mbs[16];
os_lock_t g_mb_lock;
mbox *mb_of(HWND h)
{
    os_lock(&g_mb_lock);
    mbox *r = NULL;
    for (mbox *m : g_mbs) if (m && m->dlg == h) r = m;
    os_unlock(&g_mb_lock);
    return r;
}
bool mb_add(mbox *m)
{
    os_lock(&g_mb_lock);
    bool ok = false;
    for (mbox *&s : g_mbs) if (!s) { s = m; ok = true; break; }
    os_unlock(&g_mb_lock);
    return ok;
}
void mb_remove(mbox *m)
{
    os_lock(&g_mb_lock);
    for (mbox *&s : g_mbs) if (s == m) s = NULL;
    os_unlock(&g_mb_lock);
}
inline void fillr(HDC dc, long l, long t, long r, long b, int sysidx)
{
    RECT rc = { l, t, r, b };
    gdi_fill_rect((void *)dc, &rc, (void *)(uintptr_t)(sysidx + 1));
}
void frame3d(HDC dc, const RECT &r, bool sunk)   /* the classic two-line edge */
{
    int hi = 20, lo = 21, in_hi = 22, in_lo = 16;   /* COLOR_BTNHIGHLIGHT, 3DDKSHADOW, 3DLIGHT, BTNSHADOW */
    if (sunk) { int t = hi; hi = lo; lo = t; t = in_hi; in_hi = in_lo; in_lo = t; }
    fillr(dc, r.left, r.top, r.right, r.top + 1, hi); fillr(dc, r.left, r.top, r.left + 1, r.bottom, hi);
    fillr(dc, r.left, r.bottom - 1, r.right, r.bottom, lo); fillr(dc, r.right - 1, r.top, r.right, r.bottom, lo);
    fillr(dc, r.left + 1, r.top + 1, r.right - 1, r.top + 2, in_hi); fillr(dc, r.left + 1, r.top + 1, r.left + 2, r.bottom - 1, in_hi);
    fillr(dc, r.left + 1, r.bottom - 2, r.right - 1, r.bottom - 1, in_lo); fillr(dc, r.right - 2, r.top + 1, r.right - 1, r.bottom - 1, in_lo);
}
void text_at(HDC dc, int x, int y, const std::string &s, int color)
{
    SetTextColor(dc, gdi_syscolor(color));
    SetBkMode(dc, 1 /* TRANSPARENT */);
    if (!s.empty()) ExtTextOutA(dc, x, y, 0, NULL, s.c_str(), (UINT)s.size(), NULL);
}
int text_w(HDC dc, const std::string &s)
{
    SIZE sz = { 0, 0 };
    if (!s.empty() && !GetTextExtentPoint32A(dc, s.c_str(), (int)s.size(), &sz)) return 0;
    return (int)sz.cx;
}
/* the one rule that breaks a text into lines of at most `maxw` pixels: at newlines, at the last blank that fits, else
 * inside a word */
void wrap_text(HDC dc, const char *t, int maxw, std::vector<std::string> &out)
{
    std::string cur, word;
    auto flush = [&] { out.push_back(cur); cur.clear(); };
    auto add_word = [&] {
        if (word.empty()) return;
        std::string trial = cur.empty() ? word : cur + " " + word;
        if (text_w(dc, trial) <= maxw) cur = trial;
        else if (cur.empty()) {   /* a word wider than the line: broken inside */
            std::string piece;
            for (char c : word) {
                if (!piece.empty() && text_w(dc, piece + c) > maxw) { out.push_back(piece); piece.clear(); }
                piece += c;
            }
            cur = piece;
        } else { flush(); cur = word; }
        word.clear();
    };
    bool any = false;
    for (const char *p = t; *p; p++) {
        if (*p == '\r') continue;
        if (*p == '\n') { add_word(); flush(); any = true; }
        else if (*p == ' ' || *p == '\t') add_word();
        else word += *p;
    }
    add_word();
    if (!cur.empty() || !any) flush();
}
/* the four message-box icons, 32 x 32: a disc (a triangle for the exclamation mark) with the sign in white */
void mb_icon(HDC dc, int kind, int x, int y)
{
    if (!kind) return;
    uint32_t col = kind == 0x10 ? 0x2020D0 : kind == 0x30 ? 0x20C8F0 : 0xC07020;   /* COLORREF 0x00BBGGRR */
    int32_t poly[2 * 16];
    int n = 0;
    if (kind == 0x30) { poly[0] = x + 16; poly[1] = y + 1; poly[2] = x + 31; poly[3] = y + 30; poly[4] = x + 1; poly[5] = y + 30; n = 3; }
    else {
        static const int8_t ring[16][2] = { { 16, 0 }, { 22, 2 }, { 27, 5 }, { 30, 10 }, { 31, 16 }, { 30, 22 }, { 27, 27 }, { 22, 30 },
                                             { 16, 31 }, { 10, 30 }, { 5, 27 }, { 2, 22 }, { 1, 16 }, { 2, 10 }, { 5, 5 }, { 10, 2 } };
        for (; n < 16; n++) { poly[2 * n] = x + ring[n][0]; poly[2 * n + 1] = y + ring[n][1]; }
    }
    gdi_dc_poly((void *)dc, poly, n, col);
    uint8_t lf[60];   /* LOGFONTA: 22 px bold */
    memset(lf, 0, sizeof lf);
    int32_t hgt = -22, wgt = 700;
    memcpy(lf, &hgt, 4); memcpy(lf + 16, &wgt, 4); lf[23] = 1 /* DEFAULT_CHARSET */; memcpy(lf + 28, "Arial", 5);
    HFONT f = CreateFontIndirectA(lf);
    HGDIOBJ old = f ? SelectObject(dc, f) : NULL;
    const char g[2] = { kind == 0x10 ? 'X' : kind == 0x20 ? '?' : kind == 0x30 ? '!' : 'i', 0 };
    int gw = text_w(dc, g);
    SetTextColor(dc, 0xFFFFFF);
    SetBkMode(dc, 1);
    ExtTextOutA(dc, x + 16 - gw / 2, y + (kind == 0x30 ? 8 : 4), 0, NULL, g, 1, NULL);
    if (old) SelectObject(dc, old);
    if (f) DeleteObject(f);
}
/* where each button goes: laid out in a row at the bottom, mirrored for right-to-left reading */
void mb_buttons_rects(mbox *m, int bw, int bh, int y)
{
    int gap = 6, total = m->nb * bw + (m->nb - 1) * gap, x = (m->w - total) / 2;
    for (int i = 0; i < m->nb; i++) {
        int k = m->rtl ? m->nb - 1 - i : i;
        m->b[k].r = RECT{ x, y, x + bw, y + bh };
        x += bw + gap;
    }
}
void mb_draw(mbox *m, HDC dc)
{
    HGDIOBJ old = SelectObject(dc, GetStockObject(17 /* DEFAULT_GUI_FONT */));
    fillr(dc, 0, 0, m->w, m->h, 15);                                           /* COLOR_BTNFACE */
    fillr(dc, 0, m->title_h, m->w, m->title_h + m->body_h, 5);               /* the text area: COLOR_WINDOW */
    fillr(dc, 0, 0, m->w, m->title_h, 2);                                      /* the title bar: COLOR_ACTIVECAPTION */
    text_at(dc, 8, (m->title_h - m->line_h) / 2, m->caption, 9);               /* COLOR_CAPTIONTEXT */
    mb_icon(dc, m->icon, m->icon_x, m->icon_y);
    for (size_t i = 0; i < m->lines.size(); i++) {
        int tw = text_w(dc, m->lines[i]);
        int x = m->right ? m->text_x + m->text_w - tw : m->text_x;
        text_at(dc, x, m->text_y + (int)i * m->line_h, m->lines[i], 8);       /* COLOR_WINDOWTEXT */
    }
    for (int i = 0; i < m->nb; i++) {
        RECT r = m->b[i].r;
        bool down = m->pressed == i;
        bool foc = m->focus == i;
        fillr(dc, r.left, r.top, r.right, r.bottom, 15);
        frame3d(dc, r, down);
        if (foc) {   /* the default / focused button has a black outline and a focus rectangle */
            fillr(dc, r.left - 1, r.top - 1, r.right + 1, r.top, 8); fillr(dc, r.left - 1, r.bottom, r.right + 1, r.bottom + 1, 8);
            fillr(dc, r.left - 1, r.top, r.left, r.bottom, 8); fillr(dc, r.right, r.top, r.right + 1, r.bottom, 8);
            fillr(dc, r.left + 4, r.top + 4, r.right - 4, r.top + 5, 8); fillr(dc, r.left + 4, r.bottom - 5, r.right - 4, r.bottom - 4, 8);
            fillr(dc, r.left + 4, r.top + 4, r.left + 5, r.bottom - 4, 8); fillr(dc, r.right - 5, r.top + 4, r.right - 4, r.bottom - 4, 8);
        }
        std::string lab = m->b[i].label;
        int tw = text_w(dc, lab);
        text_at(dc, (int)(r.left + (r.right - r.left - tw) / 2) + (down ? 1 : 0), (int)(r.top + (r.bottom - r.top - m->line_h) / 2) + (down ? 1 : 0), lab, 18 /* COLOR_BTNTEXT */);
    }
    fillr(dc, 0, 0, m->w, 1, 21); fillr(dc, 0, m->h - 1, m->w, m->h, 21); fillr(dc, 0, 0, 1, m->h, 21); fillr(dc, m->w - 1, 0, m->w, m->h, 21);
    if (old) SelectObject(dc, old);
}
int mb_hit(mbox *m, int x, int y)
{
    for (int i = 0; i < m->nb; i++)
        if (x >= m->b[i].r.left && x < m->b[i].r.right && y >= m->b[i].r.top && y < m->b[i].r.bottom) return i;
    return -1;
}
void mb_redraw(mbox *m)
{
    fnwin_invalidate(m->dlg, NULL, 0);
}
/* a button is pressed: the box ends with its id, except Help, which sends WM_HELP to the owner and goes on */
void mb_press(mbox *m, int i)
{
    if (i < 0 || i >= m->nb) return;
    if (m->b[i].id == MB_ID_HELP) {
        struct { UINT cb; int ctx, ctl; HANDLE item; ULONG_PTR ctxid; POINT pos; } hi;
        memset(&hi, 0, sizeof hi);
        hi.cb = sizeof hi; hi.ctx = 1; hi.item = (HANDLE)m->dlg;
        GetCursorPos(&hi.pos);
        HWND tgt = m->owner ? m->owner : m->dlg;
        SendMessageA(tgt, 0x0053, 0, (LPARAM)(uintptr_t)&hi);   /* WM_HELP */
        return;
    }
    m->result = m->b[i].id;
    m->done = true;
}
/* Escape and the close command: IDCANCEL when there is a Cancel button, IDOK when OK is the only one, else nothing */
bool mb_escape(mbox *m)
{
    if (m->has_cancel) m->result = MB_ID_CANCEL;
    else if (m->ok_only) m->result = MB_ID_OK;
    else return false;
    m->done = true;
    return true;
}
LRESULT mb_proc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    mbox *m = mb_of(h);
    if (!m) return DefWindowProcA(h, msg, wp, lp);
    switch (msg) {
    case 0x0014: return 1;   /* WM_ERASEBKGND: the paint covers everything */
    case 0x000F: {           /* WM_PAINT */
        alignas(8) uint8_t ps[128];
        HDC dc = BeginPaint(h, ps);
        if (dc) { mb_draw(m, dc); EndPaint(h, ps); }
        return 0; }
    case 0x0010:             /* WM_CLOSE */
        mb_escape(m);
        return 0;
    case 0x0100: {           /* WM_KEYDOWN */
        bool shift = (GetKeyState(0x10) & 0x8000) != 0;
        int n = m->nb;
        switch (wp) {
        case 0x1B: mb_escape(m); break;                                                     /* VK_ESCAPE */
        case 0x0D: case 0x20: mb_press(m, m->focus); break;                                 /* VK_RETURN, VK_SPACE */
        case 0x09: m->focus = (m->focus + (shift ? n - 1 : 1)) % n; mb_redraw(m); break;   /* VK_TAB */
        case 0x25: case 0x26: m->focus = (m->focus + n - 1) % n; mb_redraw(m); break;       /* VK_LEFT, VK_UP */
        case 0x27: case 0x28: m->focus = (m->focus + 1) % n; mb_redraw(m); break;           /* VK_RIGHT, VK_DOWN */
        case 0x70: for (int i = 0; i < n; i++) if (m->b[i].id == MB_ID_HELP) mb_press(m, i); break;   /* VK_F1 */
        default: break;
        }
        return 0; }
    case 0x0102: {           /* WM_CHAR: a mnemonic */
        char c = (char)wp;
        if (c >= 'a' && c <= 'z') c = (char)(c - 32);
        for (int i = 0; i < m->nb; i++) if (m->b[i].mnem && m->b[i].mnem == c) { m->focus = i; mb_press(m, i); break; }
        return 0; }
    case 0x0201: {           /* WM_LBUTTONDOWN */
        int i = mb_hit(m, (int)(short)(lp & 0xFFFF), (int)(short)((lp >> 16) & 0xFFFF));
        if (i >= 0) { m->pressed = i; m->focus = i; SetCapture(h); mb_redraw(m); }
        return 0; }
    case 0x0202: {           /* WM_LBUTTONUP: pressed on the button it was released on */
        int i = mb_hit(m, (int)(short)(lp & 0xFFFF), (int)(short)((lp >> 16) & 0xFFFF));
        int was = m->pressed;
        m->pressed = -1;
        if (was >= 0) { ReleaseCapture(); mb_redraw(m); if (i == was) mb_press(m, i); }
        return 0; }
    case 0x0200: {           /* WM_MOUSEMOVE: the pressed button shows pressed only while the pointer is on it */
        if (m->pressed >= 0) mb_redraw(m);
        return 0; }
    default: break;
    }
    return DefWindowProcA(h, msg, wp, lp);
}
bool g_mb_class;
} // namespace

extern "C" int MessageBoxA(HWND owner, LPCSTR text, LPCSTR caption, UINT type)
{
    type = W32_DW(type);
    W32Log().s("MessageBox [").s(caption ? caption : "").s("]: ").s(text ? text : "").emit();
    if (g_msgbox_hook)
        return g_msgbox_hook(text, caption, type);
    unsigned kind = type & 0xF, defb = (type >> 8) & 0xF;
    if (kind > 6 || ((type & 0x00200000u) && owner)) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }   /* MB_SERVICE_NOTIFICATION has no owner */
    window *ow = NULL;
    if (owner) {
        ow = wnd(owner);
        if (!ow) { SetLastError(1400); return 0; }
        owner = root_of(ow);   /* the owner of a pop-up is a top-level window */
        if (owner == (HWND)&g_desk) owner = NULL;
    }
    static const struct { int id; const char *label; char mnem; } B[] = {
        { MB_ID_OK, "OK", 0 }, { MB_ID_CANCEL, "Cancel", 0 }, { MB_ID_ABORT, "Abort", 'A' }, { MB_ID_RETRY, "Retry", 'R' },
        { MB_ID_IGNORE, "Ignore", 'I' }, { MB_ID_YES, "Yes", 'Y' }, { MB_ID_NO, "No", 'N' }, { MB_ID_TRYAGAIN, "Try Again", 'T' },
        { MB_ID_CONTINUE, "Continue", 'C' }, { MB_ID_HELP, "Help", 0 } };
    mbox m;
    m.dlg = NULL; m.owner = owner; m.type = type; m.nb = 0; m.focus = 0; m.pressed = -1; m.result = 0; m.done = false;
    m.rtl = (type & 0x00100000u) != 0; m.right = m.rtl || (type & 0x00080000u) != 0;
    m.icon = (int)(type & 0xF0u) >= 0x10 && (type & 0xF0u) <= 0x40 ? (int)(type & 0xF0u) : 0;
    m.caption = caption ? caption : "Error";   /* MSDN: the default title */
    /* the buttons of each set, in order: index into B */
    static const int SEQ[7][3] = { { 0, -1, -1 }, { 0, 1, -1 }, { 2, 3, 4 }, { 5, 6, 1 }, { 5, 6, -1 }, { 3, 1, -1 }, { 1, 7, 8 } };
    for (int k = 0; k < 3 && SEQ[kind][k] >= 0; k++) {
        const auto &s = B[SEQ[kind][k]];
        m.b[m.nb++] = mbbtn{ s.id, s.label, s.mnem, RECT{ 0, 0, 0, 0 } };
    }
    if (type & 0x4000u) m.b[m.nb++] = mbbtn{ MB_ID_HELP, "Help", 0, RECT{ 0, 0, 0, 0 } };   /* MB_HELP */
    m.has_cancel = false;
    for (int i = 0; i < m.nb; i++) if (m.b[i].id == MB_ID_CANCEL) m.has_cancel = true;
    m.ok_only = kind == 0;
    m.focus = (int)defb < m.nb ? (int)defb : 0;
    /* layout, from the font the dialog is painted with */
    HDC mdc = CreateCompatibleDC(NULL);
    HGDIOBJ oldf = mdc ? SelectObject(mdc, GetStockObject(17)) : NULL;
    uint8_t tm[60];
    memset(tm, 0, sizeof tm);
    int fh = 16;
    if (mdc && GetTextMetricsA(mdc, tm)) { int32_t h0, el; memcpy(&h0, tm, 4); memcpy(&el, tm + 16, 4); fh = h0 + el; }
    m.line_h = fh + 1;
    m.margin = 12;
    m.title_h = fh + 8;
    int dispw = fnvnx_display_w(), disph = fnvnx_display_h();
    int iconw = m.icon ? 32 + 12 : 0;
    int maxtext = dispw / 2 - 2 * m.margin - iconw;
    if (maxtext < 120) maxtext = 120;
    if (mdc) wrap_text(mdc, text ? text : "", maxtext, m.lines); else m.lines.push_back(text ? text : "");
    int tw = 0;
    for (const std::string &l : m.lines) { int w = mdc ? text_w(mdc, l) : 0; if (w > tw) tw = w; }
    int bw = 75, bh = 23;
    for (int i = 0; i < m.nb; i++) { int w = (mdc ? text_w(mdc, m.b[i].label) : 0) + 24; if (w > bw) bw = w; }
    int area_h = (int)m.lines.size() * m.line_h;
    if (m.icon && area_h < 32) area_h = 32;
    int row_w = m.nb * bw + (m.nb - 1) * 6;
    m.w = m.margin * 2 + iconw + tw;
    if (m.w < row_w + 2 * m.margin) m.w = row_w + 2 * m.margin;
    if (m.w < 160) m.w = 160;
    int body = m.margin + area_h + m.margin;
    m.body_h = body;
    m.h = m.title_h + body + m.margin + bh + m.margin;
    if (m.w > dispw) m.w = dispw;
    if (m.h > disph) m.h = disph;
    m.text_w = m.w - 2 * m.margin - iconw;
    m.text_x = m.margin + (m.rtl ? 0 : iconw);
    m.text_y = m.title_h + m.margin + (area_h - (int)m.lines.size() * m.line_h) / 2;
    m.icon_x = m.rtl ? m.w - m.margin - 32 : m.margin;
    m.icon_y = m.title_h + m.margin + (area_h - 32) / 2;
    mb_buttons_rects(&m, bw, bh, m.title_h + body + m.margin);
    if (oldf) SelectObject(mdc, oldf);
    if (mdc) DeleteDC(mdc);
    /* the class, once */
    if (!g_mb_class) {
        if (!fnwin_register_host_class("FnvnxMessageBox", mb_proc, 0)) return 0;
        g_mb_class = true;
    }
    /* what the box makes unavailable to input: the owner (MB_APPLMODAL), or without an owner all of the thread's windows
     * (MB_TASKMODAL) */
    HWND dis[64];
    int ndis = 0;
    if (owner) dis[ndis++] = owner;
    else if (type & 0x2000u) {
        HWND all[64];
        int n = fnwin_toplevels(all, 64);
        DWORD me = GetCurrentThreadId();
        for (int i = 0; i < n && ndis < 64; i++) { fnwin_info wi; if (fnwin_info_get(all[i], &wi) && wi.tid == me) dis[ndis++] = all[i]; }
    }
    int ndone = 0;
    for (int i = 0; i < ndis; i++) {
        window *w = wnd(dis[i]);
        if (!w) continue;
        os_lock(&g_ui_lock);
        bool was = (w->style & S_DISABLED) != 0;
        os_unlock(&g_ui_lock);
        if (was) { dis[i] = NULL; continue; }   /* it was disabled already: not the box's to re-enable */
        set_style_bits(w, S_DISABLED, 0);
        SendMessageA(dis[i], 0x000A, 0, 0);     /* WM_ENABLE(FALSE) */
        ndone++;
    }
    DWORD ex = 0x1 /* WS_EX_DLGMODALFRAME */ | ((type & 0x40000u) || (type & 0x3000u) == 0x1000u ? X_TOPMOST : 0) | (m.right ? 0x1000u : 0) | (m.rtl ? 0x2000u : 0);
    int px = (dispw - m.w) / 2, py = (disph - m.h) / 2;
    HWND dlg = CreateWindowExA(ex, "FnvnxMessageBox", m.caption.c_str(), S_POPUP, px, py, m.w, m.h, owner, NULL, NULL, NULL);
    if (dlg) {
        m.dlg = dlg;
        if (mb_add(&m)) {
            ShowWindow(dlg, 5);                                   /* SW_SHOW: activates it, the focus follows */
            if (type & 0x10000u) SetForegroundWindow(dlg);        /* MB_SETFOREGROUND */
            SetFocus(dlg);
            mb_redraw(&m);
            for (;;) {
                MSG msg;
                if (m.done) break;
                if (!fnmq_get_message(&msg, NULL, 0, 0)) { if (!mb_escape(&m)) { m.result = m.b[m.focus].id; m.done = true; } break; }
                bool input = (msg.message >= 0x100 && msg.message <= 0x109) || (msg.message >= 0x200 && msg.message <= 0x20E);
                if (input && msg.hwnd != dlg) continue;           /* the windows beneath get no input while the box is up */
                TranslateMessage(&msg);
                DispatchMessageA(&msg);
            }
            mb_remove(&m);
        }
    } else
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    /* give the windows back before the box goes: the activation then falls to the owner */
    for (int i = 0; i < ndis; i++) {
        if (!dis[i]) continue;
        window *w = wnd(dis[i]);
        if (!w) continue;
        set_style_bits(w, 0, S_DISABLED);
        SendMessageA(dis[i], 0x000A, 1, 0);   /* WM_ENABLE(TRUE) */
    }
    (void)ndone;
    if (dlg) DestroyWindow(dlg);
    if (owner && wnd(owner)) SetForegroundWindow(owner);
    return dlg ? m.result : 0;
}
