/* window.cpp - user32/gdi32: one virtual window, a thread-agnostic message queue, cursor/key state.
 *
 * The engine creates its main window, pumps messages with PeekMessage/DispatchMessage and reads keys with
 * GetAsyncKeyState. Here a window is a record (class, wndproc, style, user data); SendMessage calls the window
 * procedure directly, PostMessage queues; the input stream (S7) injects messages and key state through
 * fnvnx_post_message / fnvnx_set_key_state, and the renderer (S6) sets the display size. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

struct wclass { char name[64]; WNDPROC proc; };
struct window {
    uint32_t magic;
    char cls[64], title[128];
    WNDPROC proc;
    DWORD style, exstyle;
    LONG userdata, id;
    HINSTANCE inst;
    int visible;
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
    if (!wc || !wc->lpszClassName || g_nclasses == 16) {
        SetLastError(ERROR_INVALID_PARAMETER);
        return 0;
    }
    os_lock(&g_ui_lock);
    snprintf(g_classes[g_nclasses].name, sizeof g_classes[0].name, "%s", wc->lpszClassName);
    g_classes[g_nclasses].proc = wc->lpfnWndProc;
    ATOM a = (ATOM)(0xC000 + g_nclasses++);
    os_unlock(&g_ui_lock);
    return a;
}

extern "C" HWND CreateWindowExA(DWORD ex, LPCSTR cls, LPCSTR title, DWORD style, int x, int y, int w, int h,
                                HWND parent, HMENU menu, HINSTANCE inst, LPVOID param)
{
    W32_UNUSED(x); W32_UNUSED(y); W32_UNUSED(w); W32_UNUSED(h);
    W32_UNUSED(parent); W32_UNUSED(menu); W32_UNUSED(param);
    os_lock(&g_ui_lock);
    WNDPROC proc = NULL;
    for (int i = 0; i < g_nclasses; i++)
        if ((uintptr_t)cls <= 0xFFFF ? (uintptr_t)cls == (uintptr_t)(0xC000 + i) : !w32_stricmp(cls, g_classes[i].name))
            proc = g_classes[i].proc;
    struct window *win = g_nwindows < 16 ? (struct window *)calloc(1, sizeof *win) : NULL;
    if (win) {
        win->magic = WND_MAGIC;
        snprintf(win->cls, sizeof win->cls, "%s", (uintptr_t)cls > 0xFFFF ? cls : "");
        snprintf(win->title, sizeof win->title, "%s", title ? title : "");
        win->proc = proc;
        win->style = style;
        win->exstyle = ex;
        win->inst = inst;
        g_windows[g_nwindows++] = win;
    }
    os_unlock(&g_ui_lock);
    if (!win) {
        SetLastError(ERROR_NOT_ENOUGH_MEMORY);
        return NULL;
    }
    return (HWND)win;
}

extern "C" BOOL DestroyWindow(HWND h)
{
    os_lock(&g_ui_lock);
    struct window *w = wnd(h);
    if (w)
        for (int i = 0; i < g_nwindows; i++)
            if (g_windows[i] == w) {
                memmove(g_windows + i, g_windows + i + 1, (size_t)(g_nwindows - i - 1) * sizeof g_windows[0]);
                g_nwindows--;
                break;
            }
    os_unlock(&g_ui_lock);
    if (!w)
        return FALSE;
    if (w->proc)
        w->proc(h, WM_DESTROY, 0, 0);
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
        if ((!cls || !w32_stricmp(cls, g_windows[i]->cls)) && (!title || !strcmp(title, g_windows[i]->title)))
            r = (HWND)g_windows[i];
    os_unlock(&g_ui_lock);
    return r;
}

extern "C" LRESULT DefWindowProcA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    W32_UNUSED(w); W32_UNUSED(l);
    if (m == WM_CLOSE)
        DestroyWindow(h);
    return 0;
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

extern "C" BOOL PostMessageA(HWND h, UINT m, WPARAM w, LPARAM l) { return post(h, m, w, l); }
extern "C" void fnvnx_post_message(HWND h, UINT m, WPARAM w, LPARAM l) { post(h ? h : fnvnx_main_window(), m, w, l); }
extern "C" void PostQuitMessage(int code) { post(NULL, WM_QUIT, (WPARAM)code, 0); }

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
    os_unlock(&g_ui_lock);
    return got;
}

extern "C" BOOL TranslateMessage(const MSG *m) { W32_UNUSED(m); return FALSE; }
extern "C" LRESULT DispatchMessageA(const MSG *m) { return m && m->hwnd ? SendMessageA(m->hwnd, m->message, m->wParam, m->lParam) : 0; }

extern "C" BOOL ShowWindow(HWND h, int cmd) { struct window *w = wnd(h); int was = w && w->visible; if (w) w->visible = cmd != 0; return was; }
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

extern "C" int GetSystemMetrics(int i)
{
    switch (i) {
    case SM_CXSCREEN: return g_w;
    case SM_CYSCREEN: return g_h;
    case 19: return 1;   /* SM_MOUSEPRESENT */
    case 43: return 3;   /* SM_CMOUSEBUTTONS */
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
extern "C" void fnvnx_set_key_state(int vk, int down) { if (vk >= 0 && vk < 256) g_keys[vk] = down ? 1 : 0; }
extern "C" short GetAsyncKeyState(int vk) { return vk >= 0 && vk < 256 && g_keys[vk] ? (short)0x8000 : 0; }
extern "C" short GetKeyState(int vk) { return vk >= 0 && vk < 256 && g_keys[vk] ? (short)0x8000 : 0; }
extern "C" UINT GetDoubleClickTime(void) { return 500; }
extern "C" BOOL SwapMouseButton(BOOL s) { W32_UNUSED(s); return FALSE; }
extern "C" UINT SendInput(UINT n, void *in, int sz) { W32_UNUSED(n); W32_UNUSED(in); W32_UNUSED(sz); return 0; }

/* hooks: accepted and never called (the game installs a keyboard hook to block the Windows key) */
static int g_hook_dummy;
extern "C" HHOOK SetWindowsHookExA(int id, HOOKPROC f, HINSTANCE m, DWORD t) { W32_UNUSED(id); W32_UNUSED(f); W32_UNUSED(m); W32_UNUSED(t); return (HHOOK)&g_hook_dummy; }
extern "C" BOOL UnhookWindowsHookEx(HHOOK h) { return h != NULL; }
extern "C" LRESULT CallNextHookEx(HHOOK h, int c, WPARAM w, LPARAM l) { W32_UNUSED(h); W32_UNUSED(c); W32_UNUSED(w); W32_UNUSED(l); return 0; }

/* resources and GDI: opaque non-null tokens */
static int g_res_dummy[4];
extern "C" HICON LoadIconA(HINSTANCE i, LPCSTR n) { W32_UNUSED(i); W32_UNUSED(n); return (HICON)&g_res_dummy[0]; }
extern "C" HCURSOR LoadCursorA(HINSTANCE i, LPCSTR n) { W32_UNUSED(i); W32_UNUSED(n); return (HCURSOR)&g_res_dummy[1]; }
extern "C" HGDIOBJ GetStockObject(int i) { W32_UNUSED(i); return (HGDIOBJ)&g_res_dummy[2]; }
extern "C" BOOL DeleteObject(HGDIOBJ o) { return o != NULL; }

/* ---- message boxes: logged; the answer is the default button */
static fnvnx_msgbox_hook g_msgbox_hook;
extern "C" void fnvnx_set_msgbox_hook(fnvnx_msgbox_hook h) { g_msgbox_hook = h; }

extern "C" int MessageBoxA(HWND h, LPCSTR text, LPCSTR caption, UINT type)
{
    W32_UNUSED(h);
    w32_log("MessageBox [%s]: %s", caption ? caption : "", text ? text : "");
    if (g_msgbox_hook)
        return g_msgbox_hook(text, caption, type);
    static const int buttons[6][3] = {
        { IDOK, IDOK, IDOK }, { IDOK, IDCANCEL, IDCANCEL }, { IDABORT, IDRETRY, IDIGNORE },
        { IDYES, IDNO, IDCANCEL }, { IDYES, IDNO, IDNO }, { IDRETRY, IDCANCEL, IDCANCEL },
    };
    unsigned kind = type & 0xF, def = (type >> 8) & 0xF;
    return buttons[kind < 6 ? kind : 0][def < 3 ? def : 0];
}
