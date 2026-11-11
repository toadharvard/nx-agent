/* window_int.h - what window.cpp (the window tree) and msgqueue.cpp (queues, input, hooks, cursor) share. */
#ifndef FNVNX_WINDOW_INT_H
#define FNVNX_WINDOW_INT_H
#include "w32.h"

extern "C" {
/* a snapshot of one window record (screen coordinates); 0 if h is not a window of this process */
struct fnwin_info {
    unsigned style, exstyle, cstyle;   /* window styles, extended styles, class style */
    DWORD tid;                         /* the creating thread (the desktop: the caller) */
    RECT wr, cr;                       /* window and client rectangle */
    HWND parent;                       /* NULL for a top-level window */
    int paint;                         /* the update region is not empty */
    WNDPROC proc;                      /* NULL: DefWindowProcA */
};
int fnwin_info_get(HWND h, fnwin_info *o);
HWND fnwin_from_point(int x, int y);                 /* the deepest visible window at a screen point, else NULL */
HWND fnwin_next_paint(DWORD tid, HWND filter);       /* a visible window of the thread with a non-empty update region */
HWND fnwin_active(void);
void fnwin_set_active(HWND h);                       /* no notification: the lifecycle code sends its own */
int fnwin_toplevels(HWND *out, int max);             /* every top-level window, z-order; returns the count */
void fnmq_purge(HWND h);                             /* a window is gone: its posted and input messages go with it */
LRESULT fnmq_hook(int id, int code, WPARAM w, LPARAM l);   /* first hook of the chain (0 if none); callable by window.cpp */
HCURSOR fnmq_set_cursor(HCURSOR c);
HCURSOR fnmq_arrow_cursor(void);
/* input and lifecycle entries (msgqueue.cpp); fnvnx_input_* belong to DirectInput (platform/input), these feed user32 */
void fnvnx_win_key(unsigned vk, unsigned scan, int down, unsigned flags);   /* flags: 1 extended key, 0x10 injected; vk 0xE7: scan is a UTF-16 unit */
void fnvnx_win_mouse(int x, int y, unsigned buttons_changed_mask, unsigned buttons_state, int wheel);   /* bits: L R M X1 X2 */
void fnvnx_app_focus(int in_focus);
void fnvnx_app_suspend(void);
void fnvnx_app_resume(void);
void fnvnx_app_exit_request(void);
void fnvnx_app_display_change(int w, int h);
BOOL SetDoubleClickTime(UINT ms);
HCURSOR SetCursor(HCURSOR c);
}
#endif
