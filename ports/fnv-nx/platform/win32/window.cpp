/* window.cpp - user32/gdi32: one virtual window, a thread-agnostic message queue, cursor/key state.
 *
 * The engine creates its main window, pumps messages with PeekMessage/DispatchMessage and reads keys with
 * GetAsyncKeyState. Here a window is a record (class, wndproc, style, user data); SendMessage calls the window
 * procedure directly, PostMessage queues; the input stream (S7) injects messages and key state through
 * fnvnx_post_message / fnvnx_set_key_state, and the renderer (S6) sets the display size. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

#ifndef ERROR_CANNOT_FIND_WND_CLASS
#define ERROR_CANNOT_FIND_WND_CLASS 1407
#define ERROR_INVALID_WINDOW_HANDLE 1400
#define ERROR_CLASS_ALREADY_EXISTS 1410
#endif
#define FNV_WM_NCCREATE 0x0081
#define FNV_WM_NCDESTROY 0x0082
#define FNV_WM_CREATE 0x0001
#define FNV_WM_NCACTIVATE 0x0086
#define FNV_WM_SHOWWINDOW 0x0018
#define FNV_CW_USEDEFAULT ((int)0x80000000)
/* CREATESTRUCTA with the pointer width of the build (the guest is ILP32) */
struct fnv_createstruct {
    LPVOID params; HINSTANCE inst; HMENU menu; HWND parent;
    int cy, cx, y, x;
    LONG style;
    LPCSTR name, cls;
    DWORD ex;
};

struct wclass { char name[64]; WNDPROC proc; };
struct window {
    uint32_t magic;
    char cls[64], title[128];
    WNDPROC proc;
    DWORD style, exstyle;
    LONG userdata, id;
    int x, y, w, h;
    HINSTANCE inst;
    int visible, activated, destroying;
};
#define WND_MAGIC 0x574E4457u
static struct wclass g_classes[16];
static int g_nclasses;
static struct window *g_windows[16];
static int g_nwindows;
static int g_w = 1280, g_h = 720;
static os_lock_t g_ui_lock;

static struct window *wnd(HWND h)
{
    struct window *w = (struct window *)h;
    for (int i = 0; i < g_nwindows; i++)
        if (g_windows[i] == w)
            return w;
    return NULL;
}

extern "C" HWND fnvnx_main_window(void) { return g_nwindows ? (HWND)g_windows[0] : NULL; }
extern "C" void fnvnx_set_display_size(int w, int h) { g_w = w; g_h = h; }

extern "C" ATOM RegisterClassA(const WNDCLASSA *wc)
{
    if (!wc || !wc->lpszClassName || (uintptr_t)wc->lpszClassName <= 0xFFFF || g_nclasses == 16) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    os_lock(&g_ui_lock);
    for (int i = 0; i < g_nclasses; i++)
        if (!w32_stricmp(wc->lpszClassName, g_classes[i].name)) {
            os_unlock(&g_ui_lock);
            SetLastError(ERROR_CLASS_ALREADY_EXISTS);
            return 0;
        }
    snprintf(g_classes[g_nclasses].name, sizeof g_classes[0].name, "%s", wc->lpszClassName);
    g_classes[g_nclasses].proc = wc->lpfnWndProc;
    ATOM a = (ATOM)(0xC000 + g_nclasses++);
    os_unlock(&g_ui_lock);
    return a;
}

static void purge_queue(HWND h);

static bool system_class(LPCSTR cls)
{
    static const char *const names[] = { "BUTTON", "STATIC", "EDIT", "COMBOBOX", "LISTBOX", "SCROLLBAR", "#32770" };
    for (const char *n : names)
        if (!w32_stricmp(cls, n))
            return true;
    return false;
}

extern "C" HWND CreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR title, DWORD style, int x, int y, int w, int h,
                                HWND parent, HMENU menu, HINSTANCE inst, LPVOID param)
{
    os_lock(&g_ui_lock);
    WNDPROC proc = NULL;
    const char *cname = NULL;
    for (int i = 0; i < g_nclasses; i++)
        if ((uintptr_t)cls <= 0xFFFF ? (uintptr_t)cls == (uintptr_t)(0xC000 + i) : !w32_stricmp(cls, g_classes[i].name)) {
            proc = g_classes[i].proc;
            cname = g_classes[i].name;
        }
    if (!cname && cls && (uintptr_t)cls > 0xFFFF && system_class(cls))
        cname = cls;   /* predefined control classes: no procedure here, DefWindowProc answers */
    struct window *win = NULL;
    if (cname && g_nwindows < 16)
        win = (struct window *)calloc(1, sizeof *win);
    if (win) {
        win->magic = WND_MAGIC;
        snprintf(win->cls, sizeof win->cls, "%s", cname);
        snprintf(win->title, sizeof win->title, "%s", title ? title : "");
        win->proc = proc;
        win->style = style;
        win->exstyle = ex;
        win->inst = inst;
        win->x = x == FNV_CW_USEDEFAULT ? 0 : x;
        win->y = y == FNV_CW_USEDEFAULT ? 0 : y;
        win->w = w == FNV_CW_USEDEFAULT ? 0 : w;
        win->h = h == FNV_CW_USEDEFAULT ? 0 : h;
        g_windows[g_nwindows++] = win;
    }
    os_unlock(&g_ui_lock);
    if (!win) {
        SetLastError(cname ? ERROR_NOT_ENOUGH_MEMORY : ERROR_CANNOT_FIND_WND_CLASS);
        return NULL;
    }
    /* as on Windows the procedure sees WM_NCCREATE (0 = fail) and WM_CREATE (-1 = fail) before the call returns */
    fnv_createstruct cs = { param, inst, menu, parent, h, w, y, x, (LONG)style, title, cls, ex };
    HWND hw = (HWND)win;
    LRESULT ok = SendMessageA(hw, FNV_WM_NCCREATE, 0, (LPARAM)(uintptr_t)&cs);
    if (ok)
        ok = (int)SendMessageA(hw, FNV_WM_CREATE, 0, (LPARAM)(uintptr_t)&cs) != -1;
    if (!ok) {
        DestroyWindow(hw);
        return NULL;
    }
    return hw;
}

extern "C" BOOL DestroyWindow(HWND h)
{
    os_lock(&g_ui_lock);
    struct window *w = wnd(h);
    bool first = w && !w->destroying;
    if (first)
        w->destroying = 1;
    os_unlock(&g_ui_lock);
    if (!first)
        return FALSE;
    /* the window stays valid while its procedure sees WM_DESTROY and WM_NCDESTROY */
    if (w->proc) {
        w->proc(h, WM_DESTROY, 0, 0);
        w->proc(h, FNV_WM_NCDESTROY, 0, 0);
    }
    os_lock(&g_ui_lock);
    for (int i = 0; i < g_nwindows; i++)
        if (g_windows[i] == w) {
            memmove(g_windows + i, g_windows + i + 1, (size_t)(g_nwindows - i - 1) * sizeof g_windows[0]);
            g_nwindows--;
            break;
        }
    os_unlock(&g_ui_lock);
    purge_queue(h);
    w->magic = 0;
    free(w);
    return TRUE;
}

extern "C" BOOL IsWindow(HWND h) { return wnd(h) != NULL; }

extern "C" HWND FindWindowA(LPCSTR cls, LPCSTR title)
{
    /* the game checks for a running instance before creating its window: only our own windows exist */
    os_lock(&g_ui_lock);
    HWND r = NULL;
    for (int i = 0; i < g_nwindows && !r; i++)
        if ((!cls || ((uintptr_t)cls > 0xFFFF && !w32_stricmp(cls, g_windows[i]->cls))) && (!title || !strcmp(title, g_windows[i]->title)))
            r = (HWND)g_windows[i];
    os_unlock(&g_ui_lock);
    return r;
}

extern "C" LRESULT DefWindowProcA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    struct window *win = wnd(h);
    switch (m) {
    case WM_CLOSE: DestroyWindow(h); return 0;
    case FNV_WM_NCCREATE: return TRUE;      /* creation goes on */
    case 0x0014: return 1;                  /* WM_ERASEBKGND: handled */
    case 0x0021: return 1;                  /* WM_MOUSEACTIVATE: MA_ACTIVATE */
    case 0x0084: return 1;                  /* WM_NCHITTEST: HTCLIENT */
    case FNV_WM_NCACTIVATE: return TRUE;
    case 0x000D: /* WM_GETTEXT */
        if (!win || !l || !w) return 0;
        snprintf((char *)(uintptr_t)l, (size_t)w, "%s", win->title);
        return (LRESULT)strlen((const char *)(uintptr_t)l);
    case 0x000E: return win ? (LRESULT)strlen(win->title) : 0; /* WM_GETTEXTLENGTH */
    case 0x000C: /* WM_SETTEXT */
        if (win) snprintf(win->title, sizeof win->title, "%s", l ? (const char *)(uintptr_t)l : "");
        return win != NULL;
    default: return 0;
    }
}

extern "C" LRESULT SendMessageA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    struct window *win = wnd(h);
    if (!win)
        return 0;
    return win->proc ? win->proc(h, m, w, l) : DefWindowProcA(h, m, w, l);
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
        SetLastError(ERROR_INVALID_WINDOW_HANDLE);
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
    struct window *w = wnd(h);
    if (!w)
        return FALSE;
    int was = w->visible;
    w->visible = cmd != 0;   /* SW_HIDE */
    if (cmd != 0 && !was)
        SendMessageA(h, FNV_WM_SHOWWINDOW, 1, 0);
    /* the first show of a window activates it (unless SW_SHOWNA / SW_SHOWNOACTIVATE): the app and the window
     * learn they are active and have the focus, which a game's pause logic may wait for */
    if (cmd != 0 && cmd != 4 && cmd != 8 && !w->activated) {
        w->activated = 1;
        SendMessageA(h, WM_ACTIVATEAPP, 1, 0);
        SendMessageA(h, FNV_WM_NCACTIVATE, 1, 0);
        SendMessageA(h, WM_ACTIVATE, 1 /* WA_ACTIVE */, 0);
        SendMessageA(h, WM_SETFOCUS, 0, 0);
    }
    return was;
}
extern "C" BOOL UpdateWindow(HWND h) { return wnd(h) != NULL; }
extern "C" BOOL SetForegroundWindow(HWND h) { return wnd(h) != NULL; }
extern "C" HWND GetForegroundWindow(void) { return fnvnx_main_window(); }
extern "C" HWND GetActiveWindow(void) { return fnvnx_main_window(); }
extern "C" HWND SetFocus(HWND h) { W32_UNUSED(h); return fnvnx_main_window(); }
extern "C" HWND GetFocus(void) { return fnvnx_main_window(); }
extern "C" HWND GetWindow(HWND h, UINT cmd) { W32_UNUSED(h); W32_UNUSED(cmd); return NULL; }
extern "C" BOOL IsIconic(HWND h) { W32_UNUSED(h); return FALSE; }
extern "C" BOOL SetWindowTextA(HWND h, LPCSTR t) { struct window *w = wnd(h); if (w) snprintf(w->title, sizeof w->title, "%s", t ? t : ""); return w != NULL; }
extern "C" BOOL EnumChildWindows(HWND h, WNDENUMPROC f, LPARAM l) { W32_UNUSED(h); W32_UNUSED(f); W32_UNUSED(l); return TRUE; }

extern "C" LONG GetWindowLongA(HWND h, int idx)
{
    struct window *w = wnd(h);
    if (!w)
        return 0;
    switch (idx) {
    case GWL_WNDPROC: return (LONG)(uintptr_t)w->proc; /* only meaningful with 32-bit pointers (ILP32) */
    case GWL_STYLE: return (LONG)w->style;
    case GWL_EXSTYLE: return (LONG)w->exstyle;
    case GWL_USERDATA: return w->userdata;
    case GWL_ID: return w->id;
    case GWL_HINSTANCE: return (LONG)(uintptr_t)w->inst;
    default: return 0;
    }
}

extern "C" LONG SetWindowLongA(HWND h, int idx, LONG v)
{
    struct window *w = wnd(h);
    if (!w)
        return 0;
    LONG old = GetWindowLongA(h, idx);
    switch (idx) {
    case GWL_WNDPROC: w->proc = (WNDPROC)(uintptr_t)v; break;
    case GWL_STYLE: w->style = (DWORD)v; break;
    case GWL_EXSTYLE: w->exstyle = (DWORD)v; break;
    case GWL_USERDATA: w->userdata = v; break;
    case GWL_ID: w->id = v; break;
    default: break;
    }
    return old;
}

extern "C" BOOL AdjustWindowRect(LPRECT r, DWORD s, BOOL m) { W32_UNUSED(r); W32_UNUSED(s); W32_UNUSED(m); return TRUE; }
extern "C" BOOL AdjustWindowRectEx(LPRECT r, DWORD s, BOOL m, DWORD e) { W32_UNUSED(r); W32_UNUSED(s); W32_UNUSED(m); W32_UNUSED(e); return TRUE; }

extern "C" BOOL GetClientRect(HWND h, LPRECT r)
{
    if (!wnd(h) || !r)
        return FALSE;
    r->left = r->top = 0;
    r->right = g_w;
    r->bottom = g_h;
    return TRUE;
}

extern "C" BOOL GetWindowRect(HWND h, LPRECT r) { return GetClientRect(h, r); }
extern "C" BOOL SetWindowPos(HWND h, HWND a, int x, int y, int cx, int cy, UINT f) { W32_UNUSED(a); W32_UNUSED(x); W32_UNUSED(y); W32_UNUSED(cx); W32_UNUSED(cy); W32_UNUSED(f); return wnd(h) != NULL; }
extern "C" BOOL ClientToScreen(HWND h, LPPOINT p) { W32_UNUSED(h); W32_UNUSED(p); return TRUE; }
extern "C" BOOL ScreenToClient(HWND h, LPPOINT p) { W32_UNUSED(h); W32_UNUSED(p); return TRUE; }

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
    case 49: case 50: return 16;          /* SM_CXSMICON, SM_CYSMICON */
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

/* resources and GDI: opaque non-null tokens */
static int g_res_dummy[4];
extern "C" HICON LoadIconA(HINSTANCE i, LPCSTR n) { W32_UNUSED(i); W32_UNUSED(n); return (HICON)&g_res_dummy[0]; }
extern "C" HCURSOR LoadCursorA(HINSTANCE i, LPCSTR n) { W32_UNUSED(i); W32_UNUSED(n); return (HCURSOR)&g_res_dummy[1]; }
extern "C" HGDIOBJ GetStockObject(int i) { W32_UNUSED(i); return (HGDIOBJ)&g_res_dummy[2]; }
extern "C" BOOL DeleteObject(HGDIOBJ o) { return o != NULL; }

/* IAT slots named in round 5 of S5 (imports.tsv): window text/class, the display device list, common controls */
extern "C" int GetWindowTextA(HWND h, LPSTR out, int n)
{
    struct window *w = wnd(h);
    if (!out || n <= 0) return 0;
    snprintf(out, (size_t)n, "%s", w ? w->title : "");
    return (int)strlen(out);
}
extern "C" int GetClassNameA(HWND h, LPSTR out, int n)
{
    struct window *w = wnd(h);
    if (!out || n <= 0 || !w) return 0;
    snprintf(out, (size_t)n, "%s", w->cls);
    return (int)strlen(out);
}
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
