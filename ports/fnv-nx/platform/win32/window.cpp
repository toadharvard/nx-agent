/* window.cpp - user32: window classes, the window tree with geometry and state, a thread-agnostic message queue,
 * cursor/key state.
 *
 * Model: one desktop (GetSystemMetrics SM_CXSCREEN x SM_CYSCREEN) is the root of the window tree; every window is
 * a record (class, procedure, styles, text, extra bytes, window rectangle in screen coordinates, z-ordered children).
 * The client rectangle is derived from the styles by ncrule(), the one rule AdjustWindowRectEx also uses.
 * SendMessage calls the window procedure directly, PostMessage queues; the input stream (S7) injects messages and
 * key state through fnvnx_post_message / fnvnx_set_key_state, and the renderer (S6) sets the display size.
 * Locking: g_ui_lock guards the tree, is never held across a window procedure call and is not recursive; the *_l
 * helpers expect it held. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

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
    window *parent, *owner, *first, *last, *next, *prev, *all;   /* first = top of the z-order, next = below */
    RECT wr, rr;                                                 /* window rectangle in screen coordinates; normal-state rectangle */
    int restore_max, paint, destroying, hasmenu;
    char *text;
    uint8_t *extra;
};
#define WND_MAGIC 0x574E4457u
static wclass *g_classes;
static unsigned g_next_atom = 0xC000;
static window g_desk, *g_all, *g_all_tail, *g_active;
static int g_w = 1280, g_h = 720;
static os_lock_t g_ui_lock;

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
/* the predefined classes (DefWindowProc-backed, global) and the desktop window, made on first use */
static void ui_init_l(void)
{
    if (g_desk.magic)
        return;
    static const struct { const char *name; int cbwnd; } sys[] = {
        { "Button", 0 }, { "Static", 0 }, { "Edit", 0 }, { "ComboBox", 0 }, { "ListBox", 0 }, { "ScrollBar", 0 },
        { "#32770", 30 /* DLGWINDOWEXTRA */ }, { "#32769", 0 } };
    wclass *dc = NULL;
    for (const auto &e : sys)
        dc = new_class_l(e.name, NULL, 0, 0, e.cbwnd, NULL, 1);   /* the last one is the desktop's */
    g_desk.cls = dc;
    g_desk.style = S_POPUP | S_VISIBLE;
    g_desk.wr.right = g_desk.rr.right = g_w;
    g_desk.wr.bottom = g_desk.rr.bottom = g_h;
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
extern "C" void fnvnx_set_display_size(int w, int h)
{
    os_lock(&g_ui_lock);
    g_w = w; g_h = h;
    g_desk.wr.right = g_desk.rr.right = w;
    g_desk.wr.bottom = g_desk.rr.bottom = h;
    os_unlock(&g_ui_lock);
}
extern "C" void fnvnx_window_validate(HWND h)   /* BeginPaint/ValidateRect: the update region becomes empty */
{
    os_lock(&g_ui_lock);
    window *w = wnd_l(h);
    if (w)
        w->paint = 0;
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
    wclass *c = err ? NULL : new_class_l(n, wc->lpfnWndProc, wc->style, wc->cbClsExtra, wc->cbWndExtra, inst, 0);
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
            old = (uintptr_t)c->proc;
            if (set) {
                for (window *p = g_all; p; p = p->all)
                    if (p->cls == c && p->proc == c->proc) p->proc = (WNDPROC)(uintptr_t)v;
                c->proc = (WNDPROC)(uintptr_t)v;
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
    os_lock(&g_ui_lock);
    RECT c = container_l(w);
    long b = ncrule(w->style, w->exstyle, false).l;
    memset(m, 0, sizeof *m);
    m->maxpos.x = m->maxpos.y = -b;
    m->maxsize.x = c.right - c.left + 2 * b; m->maxsize.y = c.bottom - c.top + 2 * b;
    m->mintrack.x = 112; m->mintrack.y = 27;
    m->maxtrack = m->maxsize;
    os_unlock(&g_ui_lock);
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

/* ---- activation */
extern "C" LRESULT SendMessageA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    window *win = wnd(h);
    if (!win)
        return 0;
    return win->proc ? win->proc(h, m, w, l) : DefWindowProcA(h, m, w, l);
}
static void activate(window *w)   /* w is a top-level window */
{
    os_lock(&g_ui_lock);
    window *old = g_active;
    g_active = w;
    os_unlock(&g_ui_lock);
    if (old == w)
        return;
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
    SendMessageA(hw, M_SETFOCUS, 0, (LPARAM)(uintptr_t)ho);
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
    if ((nowvis && !wasvis) || (sized && !(fl & P_NOREDRAW)) || (fl & P_FRAMECHANGED))
        w->paint = 1;
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
static void purge_queue(HWND h);
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
        win->cls = c; win->proc = c->proc; win->inst = norm_inst(inst); win->tid = GetCurrentThreadId();
        win->style = style & ~(S_VISIBLE | S_MINIMIZE | S_MAXIMIZE);   /* applied by the show at the end of creation */
        win->exstyle = ex;
        win->id = (LONG)(uintptr_t)menu;
        win->hasmenu = !child && (menu || c->menu);
        bool ovl = !(style & (S_CHILD | S_POPUP));
        if (x == FN_CW_USEDEFAULT) x = 0;
        if (y == FN_CW_USEDEFAULT) y = 0;
        if (w == FN_CW_USEDEFAULT) w = ovl ? g_w : 0;
        if (h == FN_CW_USEDEFAULT) h = ovl ? g_h : 0;
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
    w->cls->nwin--;
    os_unlock(&g_ui_lock);
    purge_queue(hw);
    w->magic = 0;
    free(w->text); free(w->extra);
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

extern "C" LRESULT DefWindowProcA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    window *win = wnd(h);
    switch (m) {
    case WM_CLOSE: DestroyWindow(h); return 0;
    case M_NCCREATE: return TRUE;      /* creation goes on */
    case 0x0014: return 1;             /* WM_ERASEBKGND: handled */
    case 0x0021: return 1;             /* WM_MOUSEACTIVATE: MA_ACTIVATE */
    case 0x0084: return 1;             /* WM_NCHITTEST: HTCLIENT */
    case M_NCACTIVATE: return TRUE;
    case M_PAINT: fnvnx_window_validate(h); return 0;   /* BeginPaint + EndPaint */
    case M_NCCALCSIZE: {               /* the client rectangle is the window rectangle less the non-client area */
        if (!win || !l) return 0;
        RECT *r = w ? &((fn_nccs *)(uintptr_t)l)->rgrc[0] : (RECT *)(uintptr_t)l;
        nc_t n = ncrule(win->style, win->exstyle, win->hasmenu);
        r->left += n.l; r->top += n.t; r->right -= n.r; r->bottom -= n.b;
        return 0;
    }
    case M_WPCHANGED: {                /* WM_SIZE and WM_MOVE follow a size or position change */
        fn_wpos *p = (fn_wpos *)(uintptr_t)l;
        if (!win || !p) return 0;
        os_lock(&g_ui_lock);
        RECT c = client_l(win);
        long ox, oy;
        origin_l(win->parent, &ox, &oy);
        DWORD st = win->style;
        os_unlock(&g_ui_lock);
        if (!(p->flags & P_NOSIZE))
            SendMessageA(h, M_SIZE, (st & S_MINIMIZE) ? 1 : (st & S_MAXIMIZE) ? 2 : 0,
                         (LPARAM)(((uint32_t)(c.right - c.left) & 0xFFFF) | ((uint32_t)(c.bottom - c.top) << 16)));
        if (!(p->flags & P_NOMOVE))
            SendMessageA(h, M_MOVE, 0, (LPARAM)(((uint32_t)(c.left - ox) & 0xFFFF) | ((uint32_t)(c.top - oy) << 16)));
        return 0;
    }
    case 0x000D: { /* WM_GETTEXT */
        if (!win || !l || !w) return 0;
        os_lock(&g_ui_lock);
        size_t n = strlen(win->text), k = n < (size_t)w - 1 ? n : (size_t)w - 1;
        memcpy((char *)(uintptr_t)l, win->text, k);
        ((char *)(uintptr_t)l)[k] = 0;
        os_unlock(&g_ui_lock);
        return (LRESULT)k;
    }
    case 0x000E: return win ? (LRESULT)strlen(win->text) : 0; /* WM_GETTEXTLENGTH */
    case 0x000C: { /* WM_SETTEXT */
        char *t = win ? dup_str(l ? (const char *)(uintptr_t)l : "") : NULL;
        if (!t) return FALSE;
        os_lock(&g_ui_lock);
        free(win->text);
        win->text = t;
        os_unlock(&g_ui_lock);
        return TRUE;
    }
    default: return 0;
    }
}

/* ---- message queue (one per process: the engine pumps on its main thread) */
#define QCAP 256
static MSG g_queue[QCAP];
static unsigned g_qhead, g_qtail;
static int g_quit_pending, g_quit_code;   /* PostQuitMessage is a flag, not a queued message */

static void purge_queue(HWND h)
{
    os_lock(&g_ui_lock);
    unsigned out = g_qhead;
    for (unsigned i = g_qhead; i != g_qtail; i++)
        if (g_queue[i % QCAP].hwnd != h)
            g_queue[out++ % QCAP] = g_queue[i % QCAP];
    g_qtail = out;
    os_unlock(&g_ui_lock);
}

static int post(HWND h, UINT m, WPARAM w, LPARAM l)
{
    os_lock(&g_ui_lock);
    int ok = g_qtail - g_qhead < QCAP;
    if (ok) {
        MSG *q = &g_queue[g_qtail++ % QCAP];
        memset(q, 0, sizeof *q);
        q->hwnd = h;
        q->message = m;
        q->wParam = w;
        q->lParam = l;
        q->time = GetTickCount();
    }
    os_unlock(&g_ui_lock);
    return ok;
}

extern "C" BOOL PostMessageA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    if (h && h != (HWND)(uintptr_t)0xFFFF && !wnd(h)) {   /* 0xFFFF is HWND_BROADCAST */
        SetLastError(1400);
        return FALSE;
    }
    return post(h, m, w, l);
}
extern "C" void fnvnx_post_message(HWND h, UINT m, WPARAM w, LPARAM l) { post(h ? h : fnvnx_main_window(), m, w, l); }
extern "C" void PostQuitMessage(int code)
{
    os_lock(&g_ui_lock);
    g_quit_pending = 1;
    g_quit_code = code;
    os_unlock(&g_ui_lock);
}

extern "C" BOOL PeekMessageA(LPMSG msg, HWND h, UINT lo, UINT hi, UINT remove)
{
    os_lock(&g_ui_lock);
    BOOL got = FALSE;
    for (unsigned i = g_qhead; i != g_qtail; i++) {
        MSG *q = &g_queue[i % QCAP];
        if (h && q->hwnd != h)
            continue;
        if ((lo || hi) && (q->message < lo || q->message > hi))
            continue;
        *msg = *q;
        if (remove & PM_REMOVE) {
            for (unsigned j = i; j != g_qhead; j--)
                g_queue[j % QCAP] = g_queue[(j - 1) % QCAP];
            g_qhead++;
        }
        got = TRUE;
        break;
    }
    /* WM_QUIT comes last, once the queue holds nothing else, and never for a window filter */
    if (!got && g_quit_pending && !h && (!(lo || hi) || (lo <= WM_QUIT && WM_QUIT <= hi))) {
        memset(msg, 0, sizeof *msg);
        msg->message = WM_QUIT;
        msg->wParam = (WPARAM)g_quit_code;
        msg->time = GetTickCount();
        if (remove & PM_REMOVE)
            g_quit_pending = 0;
        got = TRUE;
    }
    os_unlock(&g_ui_lock);
    return got;
}

extern "C" BOOL TranslateMessage(const MSG *m) { W32_UNUSED(m); return FALSE; }
extern "C" LRESULT DispatchMessageA(const MSG *m) { return m && m->hwnd ? SendMessageA(m->hwnd, m->message, m->wParam, m->lParam) : 0; }

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
extern "C" HWND SetFocus(HWND h) { W32_UNUSED(h); return fnvnx_main_window(); }
extern "C" HWND GetFocus(void) { return fnvnx_main_window(); }

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
        case GWL_WNDPROC: r = (LONG)(uintptr_t)w->proc; break;   /* only meaningful with 32-bit pointers (ILP32) */
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
            else { old = (LONG)(uintptr_t)w->proc; w->proc = (WNDPROC)(uintptr_t)v; }
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

extern "C" int GetSystemMetrics(int i)
{
    switch (i) {
    case SM_CXSCREEN: case 16 /* CXFULLSCREEN */: case 78 /* CXVIRTUALSCREEN */: return g_w;
    case SM_CYSCREEN: case 17 /* CYFULLSCREEN */: case 79 /* CYVIRTUALSCREEN */: return g_h;
    case 4: return 23;                    /* SM_CYCAPTION */
    case 5: case 6: return 1;             /* SM_CXBORDER, SM_CYBORDER */
    case 7: case 8: return 3;             /* SM_CXDLGFRAME, SM_CYDLGFRAME */
    case 11: case 12: return 32;          /* SM_CXICON, SM_CYICON */
    case 13: case 14: return 32;          /* SM_CXCURSOR, SM_CYCURSOR */
    case 15: return 20;                   /* SM_CYMENU */
    case 19: return 1;                    /* SM_MOUSEPRESENT */
    case 23: return g_swap_buttons;       /* SM_SWAPBUTTON */
    case 32: case 33: return 4;           /* SM_CXSIZEFRAME, SM_CYSIZEFRAME */
    case 36: case 37: return 4;           /* SM_CXDOUBLECLK, SM_CYDOUBLECLK */
    case 43: return 3;                    /* SM_CMOUSEBUTTONS */
    case 45: case 46: return 2;           /* SM_CXEDGE, SM_CYEDGE */
    case 49: case 50: return 16;          /* SM_CXSMICON, SM_CYSMICON */
    case 57: return 160;                  /* SM_CXMINIMIZED */
    case 58: return 28;                   /* SM_CYMINIMIZED */
    case 80: case 81: return 1;           /* SM_CMONITORS, SM_SAMEDISPLAYFORMAT */
    default: return 0;
    }
}

/* ---- cursor and keys */
static int g_cursor_count;
static LONG g_cx, g_cy;
static volatile uint8_t g_keys[256];

extern "C" int ShowCursor(BOOL show) { return show ? ++g_cursor_count : --g_cursor_count; }
extern "C" BOOL SetCursorPos(int x, int y) { g_cx = x; g_cy = y; return TRUE; }
extern "C" void fnvnx_set_cursor_pos(int x, int y) { g_cx = x; g_cy = y; }
extern "C" BOOL GetCursorPos(LPPOINT p) { if (!p) return FALSE; p->x = g_cx; p->y = g_cy; return TRUE; }
extern "C" BOOL ClipCursor(const RECT *r) { W32_UNUSED(r); return TRUE; }
extern "C" HWND SetCapture(HWND h) { W32_UNUSED(h); return NULL; }
extern "C" BOOL ReleaseCapture(void) { return TRUE; }
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
extern "C" UINT GetDoubleClickTime(void) { return 500; }
extern "C" BOOL SwapMouseButton(BOOL s) { BOOL old = g_swap_buttons != 0; g_swap_buttons = s != 0; return old; }

/* INPUT is 28 bytes in the 32-bit guest: type, then a 24-byte union (MOUSEINPUT / KEYBDINPUT) */
extern "C" UINT SendInput(UINT n, void *in, int sz)
{
    if (sz != 28 || (n && !in)) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    const uint8_t *p = (const uint8_t *)in;
    for (UINT i = 0; i < n; i++, p += 28) {
        uint32_t type, flags;
        uint16_t vk;
        memcpy(&type, p, 4);
        if (type != 1 /* INPUT_KEYBOARD */)
            continue;
        memcpy(&vk, p + 4, 2);
        memcpy(&flags, p + 12, 4);
        if (vk < 256)
            fnvnx_set_key_state(vk, !(flags & 2 /* KEYEVENTF_KEYUP */));
    }
    return n;
}

/* hooks: accepted and never called (the game installs a keyboard hook to block the Windows key); each gets its own
 * handle so that unhooking twice fails as on Windows */
static int g_hooks[8];
extern "C" HHOOK SetWindowsHookExA(int id, HOOKPROC f, HINSTANCE m, DWORD t)
{
    W32_UNUSED(id); W32_UNUSED(m); W32_UNUSED(t);
    if (!f) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return NULL;
    }
    os_lock(&g_ui_lock);
    HHOOK r = NULL;
    for (int i = 0; i < 8 && !r; i++)
        if (!g_hooks[i]) {
            g_hooks[i] = 1;
            r = (HHOOK)&g_hooks[i];
        }
    os_unlock(&g_ui_lock);
    if (!r)
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
    return r;
}
extern "C" BOOL UnhookWindowsHookEx(HHOOK h)
{
    os_lock(&g_ui_lock);
    BOOL ok = FALSE;
    for (int i = 0; i < 8; i++)
        if (h == (HHOOK)&g_hooks[i] && g_hooks[i]) {
            g_hooks[i] = 0;
            ok = TRUE;
        }
    os_unlock(&g_ui_lock);
    if (!ok)
        SetLastError(ERROR_INVALID_PARAMETER);
    return ok;
}
extern "C" LRESULT CallNextHookEx(HHOOK h, int c, WPARAM w, LPARAM l) { W32_UNUSED(h); W32_UNUSED(c); W32_UNUSED(w); W32_UNUSED(l); return 0; }

/* resources: opaque non-null tokens (GDI objects live in gdi32.cpp) */
static int g_res_dummy[4];
extern "C" HICON LoadIconA(HINSTANCE i, LPCSTR n) { W32_UNUSED(i); W32_UNUSED(n); return (HICON)&g_res_dummy[0]; }
extern "C" HCURSOR LoadCursorA(HINSTANCE i, LPCSTR n) { W32_UNUSED(i); W32_UNUSED(n); return (HCURSOR)&g_res_dummy[1]; }

/* IAT slots named in round 5 of S5 (imports.tsv): the display device list, common controls */
/* DISPLAY_DEVICEA: one primary display attached to the desktop */
struct fnvnx_display_device { DWORD cb; char name[32], string[128]; DWORD flags; char id[128], key[128]; };
extern "C" BOOL EnumDisplayDevicesA(LPCSTR dev, DWORD i, void *out, DWORD flags)
{
    struct fnvnx_display_device *d = (struct fnvnx_display_device *)out;
    W32_UNUSED(flags);
    if (dev || i != 0 || !d || d->cb < sizeof *d) return FALSE;
    memset((char *)d + sizeof d->cb, 0, sizeof *d - sizeof d->cb);
    snprintf(d->name, sizeof d->name, "\\\\.\\DISPLAY1");
    snprintf(d->string, sizeof d->string, "fnv-nx display");
    d->flags = 0x1 /* ATTACHED_TO_DESKTOP */ | 0x4 /* PRIMARY_DEVICE */;
    return TRUE;
}
extern "C" BOOL InitCommonControlsEx(const void *icc) { return icc != NULL; }
extern "C" void *ImageList_LoadImageA(HINSTANCE i, LPCSTR bmp, int cx, int grow, DWORD mask, UINT type, UINT f)
{
    W32_UNUSED(i); W32_UNUSED(bmp); W32_UNUSED(cx); W32_UNUSED(grow); W32_UNUSED(mask); W32_UNUSED(type); W32_UNUSED(f);
    return NULL;   /* no resources: the caller's image list stays empty */
}
extern "C" BOOL ImageList_Destroy(void *l) { return l != NULL; }

/* ---- message boxes: logged; the answer is the default button */
static fnvnx_msgbox_hook g_msgbox_hook;
extern "C" void fnvnx_set_msgbox_hook(fnvnx_msgbox_hook h) { g_msgbox_hook = h; }

extern "C" int MessageBoxA(HWND h, LPCSTR text, LPCSTR caption, UINT type)
{
    W32_UNUSED(h);
    w32_log("MessageBox [%s]: %s", caption ? caption : "", text ? text : "");
    if (g_msgbox_hook)
        return g_msgbox_hook(text, caption, type);
    static const int buttons[7][3] = {
        { IDOK, IDOK, IDOK }, { IDOK, IDCANCEL, IDCANCEL }, { IDABORT, IDRETRY, IDIGNORE },
        { IDYES, IDNO, IDCANCEL }, { IDYES, IDNO, IDNO }, { IDRETRY, IDCANCEL, IDCANCEL }, { 2 /* IDCANCEL */, 10 /* IDTRYAGAIN */, 11 /* IDCONTINUE */ },
    };
    unsigned kind = type & 0xF, def = (type >> 8) & 0xF;
    /* MB_OK / MB_OKCANCEL: the player sees it (the device's error applet, os_show_message) and the answer is OK;
     * questions (retry, yes/no) take their default button without a dialog, as before */
    if (kind <= 1)
        os_show_message(caption ? caption : "", text ? text : "");
    return buttons[kind < 7 ? kind : 0][def < 3 ? def : 0];
}
