/* msgqueue.cpp - user32: per-thread message queues (sent, posted, input, quit, paint), SendMessage/PostMessage/
 * PeekMessage/DispatchMessage/TranslateMessage, the hardware input queue (fnvnx_win_key / fnvnx_win_mouse /
 * SendInput), hooks, the cursor, and the application lifecycle (platform/win32/LIFECYCLE.md).
 * One lock (g_mql) guards queues, hooks and input state; it is never held across a window procedure, a hook or a
 * call into the window tree. */
#include "window_int.h"

#include <stdint.h>
#include <stdlib.h>

#ifndef ERROR_RESOURCE_DATA_NOT_FOUND
#define ERROR_RESOURCE_DATA_NOT_FOUND 1812
#endif
namespace {
struct qnode { qnode *next; MSG m; uint64_t ck; };   /* ck: the poster's clock stamp (fnvnx_clock.h) */
struct snode { snode *next; HWND h; UINT m; WPARAM w; LPARAM l; LRESULT r; int done; uint64_t ck, rck; };   /* ck: the sender's stamp, rck: the receiver's at the reply */
struct mq {
    mq *next;
    DWORD tid;
    qnode *ph, *pt, *ih, *it;   /* posted, input */
    snode *sh, *st;             /* sent by other threads, awaiting a reply */
    unsigned np;
    int quit, quit_code, cursor_count;
    HCURSOR cursor;
};
struct hook { hook *next; int id; HOOKPROC proc; HINSTANCE mod; DWORD tid; int dead, busy; };
os_lock_t g_mql;
os_cond_t g_mqc;
mq *g_mqs;
hook *g_hooks;                  /* newest first */
volatile int g_nhooks;
int g_cx, g_cy;
unsigned g_btn;                 /* physical buttons down: bit 0 left, 1 right, 2 middle, 3 X1, 4 X2 */
unsigned g_dbl_ms = 500;
struct { int btn; HWND w; DWORD t; int x, y; } g_dbl;
int g_infocus = 1;
HWND g_lost;
int g_cursors[17], g_icons[8];
const unsigned QMAX = 10000;

bool m1(HWND h) { return (uintptr_t)h == (uintptr_t)-1 || (uintptr_t)h == 0xFFFFFFFFu; }
mq *mq_get(DWORD tid)   /* g_mql held */
{
    for (mq *q = g_mqs; q; q = q->next)
        if (q->tid == tid) return q;
    mq *q = (mq *)calloc(1, sizeof *q);
    if (q) { q->tid = tid; q->cursor = (HCURSOR)&g_cursors[0]; q->next = g_mqs; g_mqs = q; }
    return q;
}
void app(qnode *&h, qnode *&t, qnode *n) { n->next = NULL; if (t) t->next = n; else h = n; t = n; }
bool match(const MSG &m, HWND f, UINT lo, UINT hi)
{
    if (m1(f)) { if (m.hwnd) return false; }
    else if (f && m.hwnd != f) return false;
    return !(lo | hi) || (m.message >= lo && m.message <= hi);
}
bool take(qnode *&h, qnode *&t, HWND f, UINT lo, UINT hi, bool rm, MSG *out)   /* g_mql held */
{
    qnode *prev = NULL;
    for (qnode *n = h; n; prev = n, n = n->next)
        if (match(n->m, f, lo, hi)) {
            *out = n->m;
            out->time = clk_msg_retrieve(n->ck, n->m.time);
            if (rm) {
                (prev ? prev->next : h) = n->next;
                if (t == n) t = prev;
                free(n);
            }
            return true;
        }
    return false;
}
void stamp(MSG *m) { m->time = clk_peek_tick_ms(); m->pt.x = g_cx; m->pt.y = g_cy; }   /* g_mql held */

/* ---- hooks: the system calls the first live hook of a chain; it passes on with CallNextHookEx */
void free_hook(hook *k)   /* g_mql held */
{
    for (hook **p = &g_hooks; *p; p = &(*p)->next)
        if (*p == k) { *p = k->next; break; }
    g_nhooks--;
    free(k);
}
LRESULT call_from(int id, hook *after, int code, WPARAM w, LPARAM l, bool dbg)
{
    if (!g_nhooks)
        return 0;
    DWORD me = GetCurrentThreadId();
    hook *pick = NULL;
    bool seen = after == NULL;
    os_lock(&g_mql);
    for (int pass = 0; pass < 2 && !pick; pass++)   /* this thread's hooks, then the global ones */
        for (hook *k = g_hooks; k && !pick; k = k->next) {
            if (k->id != id || (pass == 0 ? k->tid != me : k->tid != 0)) continue;
            if (!seen) { seen = k == after; continue; }
            if (!k->dead) pick = k;
        }
    if (pick) pick->busy++;
    os_unlock(&g_mql);
    if (!pick)
        return 0;
    LRESULT r = 0;
    struct { uint32_t tid, inst; LPARAM l; WPARAM w; int code; } d = { (uint32_t)me, (uint32_t)(pick->tid ? pick->tid : me), l, w, code };
    if (!(dbg && id != 9 && call_from(9, NULL, 0, (WPARAM)id, (LPARAM)(uintptr_t)&d, false)))   /* WH_DEBUG sees every call first */
        r = pick->proc(code, w, l);
    os_lock(&g_mql);
    if (--pick->busy == 0 && pick->dead) free_hook(pick);
    os_unlock(&g_mql);
    return r;
}
LRESULT call_hook(int id, int code, WPARAM w, LPARAM l) { return call_from(id, NULL, code, w, l, true); }

/* ---- sending */
LRESULT deliver(HWND h, UINT m, WPARAM w, LPARAM l, int local)
{
    fnwin_info wi;
    if (!fnwin_info_get(h, &wi))
        return 0;
    struct { LPARAM l; WPARAM w; UINT m; HWND h; } cwp = { l, w, m, h };
    call_hook(4, 0, (WPARAM)local, (LPARAM)(uintptr_t)&cwp);   /* WH_CALLWNDPROC */
    LRESULT r = wi.hproc ? wi.hproc(h, m, w, l) : wi.proc ? wi.proc(h, m, w, l) : DefWindowProcA(h, m, w, l);
    struct { LRESULT r; LPARAM l; WPARAM w; UINT m; HWND h; } ret = { r, l, w, m, h };
    call_hook(12, 0, (WPARAM)local, (LPARAM)(uintptr_t)&ret);  /* WH_CALLWNDPROCRET */
    return r;
}
void service_sent(mq *me)   /* the receiver replies to what other threads sent */
{
    for (;;) {
        os_lock(&g_mql);
        snode *s = me->sh;
        if (s) { me->sh = s->next; if (!me->sh) me->st = NULL; }
        os_unlock(&g_mql);
        if (!s) return;
        clk_merge(s->ck);
        LRESULT r = deliver(s->h, s->m, s->w, s->l, 0);
        os_lock(&g_mql);
        s->r = r; s->rck = clk_stamp(); s->done = 1;
        os_cond_broadcast(&g_mqc);
        os_unlock(&g_mql);
    }
}
LRESULT send_cross(DWORD tid, HWND h, UINT m, WPARAM w, LPARAM l)
{
    snode s = { NULL, h, m, w, l, 0, 0, clk_msg_post(), 0 };
    os_lock(&g_mql);
    mq *t = mq_get(tid), *me = mq_get(GetCurrentThreadId());
    if (!t || !me) { os_unlock(&g_mql); SetLastError(ERROR_NOT_ENOUGH_MEMORY); return 0; }
    if (t->st) t->st->next = &s; else t->sh = &s;
    t->st = &s;
    os_cond_broadcast(&g_mqc);
    while (!s.done) {
        if (me->sh) { os_unlock(&g_mql); service_sent(me); os_lock(&g_mql); continue; }   /* no deadlock on mutual sends */
        if (clk_rt_cond_timedwait(&g_mqc, &g_mql, 100000000ull) == 1 && !s.done) {
            fnwin_info wi;
            os_unlock(&g_mql);
            bool alive = fnwin_info_get(h, &wi) != 0;
            os_lock(&g_mql);
            if (!alive) {   /* the window died unanswered: take the message back */
                snode *prev = NULL;
                for (snode *n = t->sh; n; prev = n, n = n->next)
                    if (n == &s) {
                        (prev ? prev->next : t->sh) = n->next;
                        if (t->st == n) t->st = prev;
                        s.done = 1;
                        break;
                    }
            }
        }
    }
    os_unlock(&g_mql);
    clk_merge(s.rck);   /* the reply takes the receiver's time */
    return s.r;
}
int post_to(DWORD tid, HWND h, UINT m, WPARAM w, LPARAM l)   /* 0 ok, else the error */
{
    qnode *n = (qnode *)calloc(1, sizeof *n);
    if (!n) return ERROR_NOT_ENOUGH_MEMORY;
    n->m.hwnd = h; n->m.message = m; n->m.wParam = w; n->m.lParam = l;
    os_lock(&g_mql);
    mq *t = mq_get(tid);
    int err = !t ? ERROR_NOT_ENOUGH_MEMORY : t->np >= QMAX ? 1816 /* ERROR_NOT_ENOUGH_QUOTA */ : 0;
    if (!err) { stamp(&n->m); n->ck = clk_msg_post(); app(t->ph, t->pt, n); t->np++; os_cond_broadcast(&g_mqc); }
    os_unlock(&g_mql);
    if (err) free(n);
    return err;
}
void to_input(HWND h, const MSG &m0)
{
    fnwin_info wi;
    if (!fnwin_info_get(h, &wi)) return;
    qnode *n = (qnode *)calloc(1, sizeof *n);
    if (!n) return;
    n->m = m0; n->m.hwnd = h;
    os_lock(&g_mql);
    mq *t = mq_get(wi.tid);
    n->m.time = clk_peek_tick_ms(); n->ck = clk_msg_post(); n->m.pt.x = g_cx; n->m.pt.y = g_cy;
    if (t) { app(t->ih, t->it, n); os_cond_broadcast(&g_mqc); }
    os_unlock(&g_mql);
    if (!t) free(n);
}
}   // namespace

extern "C" void fnmq_purge(HWND h)
{
    os_lock(&g_mql);
    for (mq *q = g_mqs; q; q = q->next)
        for (int k = 0; k < 2; k++) {
            qnode *&hd = k ? q->ih : q->ph, *&tl = k ? q->it : q->pt, *prev = NULL;
            for (qnode *n = hd, *nx; n; n = nx) {
                nx = n->next;
                if (n->m.hwnd == h) { (prev ? prev->next : hd) = nx; if (tl == n) tl = prev; if (!k) q->np--; free(n); }
                else prev = n;
            }
        }
    os_unlock(&g_mql);
}
extern "C" LRESULT fnmq_hook(int id, int code, WPARAM w, LPARAM l) { return call_hook(id, code, w, l); }

extern "C" LRESULT SendMessageA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    fnwin_info wi;
    if ((uintptr_t)h == 0xFFFF) {   /* HWND_BROADCAST: every top-level window */
        HWND t[256];
        int n = fnwin_toplevels(t, 256);
        LRESULT r = 0;
        for (int i = 0; i < n; i++) r = SendMessageA(t[i], m, w, l);
        return r;
    }
    if (!h || !fnwin_info_get(h, &wi)) { SetLastError(1400); return 0; }
    return wi.tid == GetCurrentThreadId() ? deliver(h, m, w, l, 1) : send_cross(wi.tid, h, m, w, l);
}
extern "C" BOOL PostMessageA(HWND h, UINT m, WPARAM w, LPARAM l)
{
    HWND t[256];
    int n = 0, err = 0;
    fnwin_info wi;
    if ((uintptr_t)h == 0xFFFF) n = fnwin_toplevels(t, 256);
    else if (h && !fnwin_info_get(h, &wi)) { SetLastError(1400); return FALSE; }
    else if (h) t[n++] = h;
    if (!n && !h) err = post_to(GetCurrentThreadId(), NULL, m, w, l);   /* a thread message */
    for (int i = 0; i < n && !err; i++)
        if (fnwin_info_get(t[i], &wi)) err = post_to(wi.tid, t[i], m, w, l);
    if (err) SetLastError(err);
    return !err;
}
/* PostThreadMessage: a thread message (hwnd NULL) to the thread with id tid; 0 ok, else the error (waveOut CALLBACK_THREAD) */
extern "C" int fnvnx_post_thread_message(DWORD tid, UINT m, WPARAM w, LPARAM l) { return post_to(tid, NULL, m, w, l); }
extern "C" void fnvnx_post_message(HWND h, UINT m, WPARAM w, LPARAM l) { PostMessageA(h ? h : fnvnx_main_window(), m, w, l); }
extern "C" void PostQuitMessage(int code)
{
    os_lock(&g_mql);
    mq *q = mq_get(GetCurrentThreadId());
    if (q) { q->quit = 1; q->quit_code = code; }
    os_unlock(&g_mql);
}

/* GetMessage for the platform's own modal loops (MessageBox, SC_MOVE, caption buttons): waits for a message of the calling thread,
 * dispatching nothing; 0 when it is WM_QUIT (re-posted for the loop that is waiting for it) */
extern "C" int fnmq_get_message(MSG *m, HWND f, UINT lo, UINT hi)
{
    for (;;) {
        if (PeekMessageA(m, f, lo, hi, 1 /* PM_REMOVE */)) {
            if (m->message != WM_QUIT) return 1;
            PostQuitMessage((int)m->wParam);
            return 0;
        }
        os_lock(&g_mql);
        clk_rt_cond_timedwait(&g_mqc, &g_mql, 20000000ull);   /* 20 ms: a message that arrives meanwhile wakes it */
        os_unlock(&g_mql);
    }
}
extern "C" BOOL PeekMessageA(LPMSG msg, HWND f, UINT lo, UINT hi, UINT flags)
{
    fnwin_info wi;
    if (!msg) { SetLastError(998); return FALSE; }
    if (lo > hi) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    if (f && !m1(f) && !fnwin_info_get(f, &wi)) { SetLastError(1400); return FALSE; }
    unsigned qs = flags >> 16, cls = (qs & 0x4FF) ? qs : 0x4FF;   /* no PM_QS_* class: all of them */
    bool rm = (flags & 1) != 0, any = !(lo | hi);
    DWORD me = GetCurrentThreadId();
    os_lock(&g_mql);
    mq *q = mq_get(me);
    os_unlock(&g_mql);
    if (!q) { SetLastError(ERROR_NOT_ENOUGH_MEMORY); return FALSE; }
    if (cls & 0x40) service_sent(q);
    for (;;) {
        MSG m;
        bool got = false;
        os_lock(&g_mql);
        if (cls & 0x08) got = take(q->ph, q->pt, f, lo, hi, rm, &m) && (q->np -= rm, true);
        if (!got && (cls & 0x07)) got = take(q->ih, q->it, f, lo, hi, rm, &m);
        if (!got && (cls & 0x08) && q->quit && (!f || m1(f)) && (any || (lo <= WM_QUIT && WM_QUIT <= hi))) {
            memset(&m, 0, sizeof m);
            m.message = WM_QUIT; m.wParam = (WPARAM)q->quit_code;
            stamp(&m);
            if (rm) q->quit = 0;
            got = true;
        }
        os_unlock(&g_mql);
        if (!got && (cls & 0x20) && !m1(f) && (any || (lo <= 0x0F && 0x0F <= hi))) {   /* WM_PAINT: not removed until validated */
            HWND pw = fnwin_next_paint(me, f);
            if (pw) {
                memset(&m, 0, sizeof m);
                m.hwnd = pw; m.message = 0x0F;
                os_lock(&g_mql); stamp(&m); os_unlock(&g_mql);
                got = true;
            }
        }
        if (!got)
            return FALSE;
        int code = rm ? 0 : 3;   /* HC_ACTION, HC_NOREMOVE */
        call_hook(3, code, 0, (LPARAM)(uintptr_t)&m);   /* WH_GETMESSAGE may edit the message */
        bool key = m.message == 0x100 || m.message == 0x101 || m.message == 0x104 || m.message == 0x105;
        bool mouse = (m.message >= 0x200 && m.message <= 0x20E) || (m.message >= 0xA0 && m.message <= 0xA9);
        if (key && call_hook(2, code, m.wParam & 0xFFFF, m.lParam) && rm)
            continue;   /* WH_KEYBOARD: nonzero discards the message */
        if (mouse) {
            struct { int32_t x, y; HWND h; UINT hit; ULONG_PTR extra; } mh = { (int32_t)m.pt.x, (int32_t)m.pt.y, m.hwnd, 1, 0 };
            if (call_hook(7, code, m.message, (LPARAM)(uintptr_t)&mh) && rm)
                continue;
        }
        *msg = m;
        return TRUE;
    }
}

/* the characters of the US-English (en-US, code page 1252) layout; -1: the key makes none */
static int to_1252(unsigned u)
{
    static const unsigned short hi[32] = { 0x20AC, 0, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039,
        0x0152, 0, 0x017D, 0, 0, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153,
        0, 0x017E, 0x0178 };
    if (u < 0x80 || (u >= 0xA0 && u <= 0xFF)) return (int)u;
    for (int i = 0; i < 32; i++)
        if (hi[i] == u) return 0x80 + i;
    return '?';
}
/* the layout table: the character of a virtual key under a modifier state (pure); TranslateMessage reads the state from the
 * key state, platform/input/swkbd.cpp inverts it for every state it types */
extern "C" int fnvnx_win_vk_char(unsigned vk, int sh, int ctl, int caps, int num);
static int vk_char(unsigned wp)
{
    unsigned vk = wp & 0xFFFF;
    if (vk == 0xE7) return to_1252((wp >> 16) & 0xFFFF);   /* VK_PACKET: the on-screen keyboard's character */
    return fnvnx_win_vk_char(vk, (GetKeyState(0x10) & 0x8000) != 0, (GetKeyState(0x11) & 0x8000) != 0, GetKeyState(0x14) & 1, GetKeyState(0x90) & 1);
}
extern "C" int fnvnx_win_vk_char(unsigned vk, int sh, int ctl, int caps, int num)
{
    if (vk >= 'A' && vk <= 'Z') return ctl ? (int)vk - 64 : (sh != caps) ? (int)vk : (int)vk + 32;
    if (vk >= '0' && vk <= '9') return ctl ? (vk == '6' && sh ? 0x1E : -1) : sh ? ")!@#$%^&*("[vk - '0'] : (int)vk;
    switch (vk) {
    case 8: return ctl ? 0x7F : 8;
    case 9: return ctl ? -1 : 9;
    case 13: return ctl ? 10 : 13;
    case 0x1B: return 27;
    case 0x20: return 32;
    }
    static const struct { uint8_t vk; char n, s; } oem[] = { { 0xBA, ';', ':' }, { 0xBB, '=', '+' }, { 0xBC, ',', '<' },
        { 0xBD, '-', '_' }, { 0xBE, '.', '>' }, { 0xBF, '/', '?' }, { 0xC0, '`', '~' }, { 0xDB, '[', '{' }, { 0xDC, '\\', '|' },
        { 0xDD, ']', '}' }, { 0xDE, '\'', '"' } };
    for (const auto &e : oem)
        if (e.vk == vk)
            return ctl ? (vk == 0xDB ? 27 : vk == 0xDC ? 28 : vk == 0xDD ? 29 : (vk == 0xBD && sh) ? 31 : -1) : sh ? e.s : e.n;
    if (vk >= 0x60 && vk <= 0x69) return (num && !sh && !ctl) ? (int)(vk - 0x60 + '0') : -1;
    if (vk == 0x6E) return (num && !sh && !ctl) ? '.' : -1;
    if (vk >= 0x6A && vk <= 0x6F && vk != 0x6C) return ctl ? -1 : "*+?-?/"[vk - 0x6A];
    return -1;
}
extern "C" BOOL TranslateMessage(const MSG *m)
{
    if (!m) { SetLastError(998); return FALSE; }
    UINT t = m->message;
    if (t != 0x100 && t != 0x101 && t != 0x104 && t != 0x105)
        return FALSE;
    int ch = (t == 0x100 || t == 0x104) ? vk_char(m->wParam) : -1;
    if (ch >= 0) {   /* the dead keys of a layout would give WM_DEADCHAR; en-US has none */
        fnwin_info wi;
        post_to(!m->hwnd || !fnwin_info_get(m->hwnd, &wi) ? GetCurrentThreadId() : wi.tid, m->hwnd, t == 0x100 ? 0x102 : 0x106, (WPARAM)ch, m->lParam);
    }
    return TRUE;
}
extern "C" LRESULT DispatchMessageA(const MSG *m)
{
    if (!m) { SetLastError(998); return 0; }
    if (m->message == 0x113 && m->lParam) {   /* WM_TIMER with a TIMERPROC */
        TIMERPROC tp((uintptr_t)m->lParam);   /* SetTimer's procedure: a game address, called through the boundary */
        tp(m->hwnd, 0x113, m->wParam, GetTickCount());
        return 0;
    }
    fnwin_info wi;
    if (!m->hwnd) return 0;
    if (!fnwin_info_get(m->hwnd, &wi) || wi.tid != GetCurrentThreadId()) { SetLastError(1400); return 0; }
    return wi.hproc ? wi.hproc(m->hwnd, m->message, m->wParam, m->lParam) : wi.proc ? wi.proc(m->hwnd, m->message, m->wParam, m->lParam) : DefWindowProcA(m->hwnd, m->message, m->wParam, m->lParam);
}

/* ---- hooks */
extern "C" HHOOK SetWindowsHookExA(int id, HOOKPROC f, HINSTANCE mod, DWORD tid)
{
    bool glob = id == 0 || id == 1 || id == 13 || id == 14;   /* journal and low-level hooks: global only */
    int err = (id < -1 || id > 14) ? 1426 : !f ? 1427 : (glob && tid) ? 1429 : (!tid && !glob && !mod) ? 1428 : 0;
    hook *k = err ? NULL : (hook *)calloc(1, sizeof *k);
    if (!err && !k) err = ERROR_NOT_ENOUGH_MEMORY;
    if (err) { SetLastError(err); return NULL; }
    k->id = id; k->proc = f; k->mod = mod; k->tid = tid;
    os_lock(&g_mql);
    k->next = g_hooks; g_hooks = k; g_nhooks++;
    os_unlock(&g_mql);
    return (HHOOK)k;
}
extern "C" BOOL UnhookWindowsHookEx(HHOOK h)
{
    bool ok = false;
    os_lock(&g_mql);
    for (hook *k = g_hooks; k && !ok; k = k->next)
        if ((HHOOK)k == h && !k->dead) {
            k->dead = 1;
            ok = true;
            if (!k->busy) free_hook(k);   /* inside its own procedure it goes when the procedure returns */
        }
    os_unlock(&g_mql);
    if (!ok) SetLastError(1404);
    return ok;
}
extern "C" LRESULT CallNextHookEx(HHOOK h, int code, WPARAM w, LPARAM l)
{
    hook *k = NULL;
    os_lock(&g_mql);
    for (hook *p = g_hooks; p && !k; p = p->next) if ((HHOOK)p == h) k = p;
    int id = k ? k->id : 0;
    os_unlock(&g_mql);
    if (!k) { SetLastError(1404); return 0; }
    return call_from(id, k, code, w, l, false);
}
static RECT clip_rect_locked(void);   /* ClipCursor, below */


/* ---- keyboard and mouse input */
extern "C" unsigned fnvnx_win_vk_scan(unsigned vk, unsigned *ext)   /* the scan code (set 1) of a key of the US layout; 0 if none */
{
    *ext = 0;
    if (vk >= 'A' && vk <= 'Z') {   /* the three letter rows start at scan 0x10, 0x1E, 0x2C */
        static const char *const rows[3] = { "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM" };
        static const uint8_t base[3] = { 0x10, 0x1E, 0x2C };
        for (int r = 0; r < 3; r++)
            for (int i = 0; rows[r][i]; i++) if (rows[r][i] == (char)vk) return base[r] + i;
        return 0;
    }
    if (vk >= '1' && vk <= '9') return vk - '1' + 2;
    if (vk == '0') return 0x0B;
    if (vk >= 0x70 && vk <= 0x79) return vk - 0x70 + 0x3B;
    if (vk >= 0x60 && vk <= 0x69) { static const uint8_t n[] = { 0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49 }; return n[vk - 0x60]; }
    static const struct { uint8_t vk, sc, ex; } t[] = { { 0x1B, 1, 0 }, { 8, 0x0E, 0 }, { 9, 0x0F, 0 }, { 13, 0x1C, 0 }, { 0x20, 0x39, 0 },
        { 0xA0, 0x2A, 0 }, { 0xA1, 0x36, 0 }, { 0xA2, 0x1D, 0 }, { 0xA3, 0x1D, 1 }, { 0xA4, 0x38, 0 }, { 0xA5, 0x38, 1 }, { 0x14, 0x3A, 0 },
        { 0x7A, 0x57, 0 }, { 0x7B, 0x58, 0 }, { 0x90, 0x45, 1 }, { 0x91, 0x46, 0 }, { 0xBA, 0x27, 0 }, { 0xBB, 0x0D, 0 }, { 0xBC, 0x33, 0 },
        { 0xBD, 0x0C, 0 }, { 0xBE, 0x34, 0 }, { 0xBF, 0x35, 0 }, { 0xC0, 0x29, 0 }, { 0xDB, 0x1A, 0 }, { 0xDC, 0x2B, 0 }, { 0xDD, 0x1B, 0 },
        { 0xDE, 0x28, 0 }, { 0x26, 0x48, 1 }, { 0x25, 0x4B, 1 }, { 0x27, 0x4D, 1 }, { 0x28, 0x50, 1 }, { 0x24, 0x47, 1 }, { 0x23, 0x4F, 1 },
        { 0x21, 0x49, 1 }, { 0x22, 0x51, 1 }, { 0x2D, 0x52, 1 }, { 0x2E, 0x53, 1 }, { 0x6E, 0x53, 0 }, { 0x6B, 0x4E, 0 }, { 0x6D, 0x4A, 0 },
        { 0x6A, 0x37, 0 }, { 0x6F, 0x35, 1 } };
    for (const auto &e : t) if (e.vk == vk) { *ext = e.ex; return e.sc; }
    return 0;
}
static void journal(UINT msg, unsigned pl, unsigned ph, HWND h)
{
    struct { UINT m, l, h; uint32_t t; HWND w; } e = { msg, pl, ph, (uint32_t)GetTickCount(), h };
    call_hook(0, 0, 0, (LPARAM)(uintptr_t)&e);   /* WH_JOURNALRECORD */
}
/* key messages (WM_KEYDOWN/UP, WM_SYSKEYDOWN/UP) in the input queues that no PeekMessageA has removed yet */
extern "C" unsigned fnvnx_win_key_backlog(void)
{
    unsigned n = 0;
    os_lock(&g_mql);
    for (mq *q = g_mqs; q; q = q->next)
        for (qnode *e = q->ih; e; e = e->next)
            if (e->m.message >= 0x100 && e->m.message <= 0x105 && e->m.message != 0x102 && e->m.message != 0x103) n++;
    os_unlock(&g_mql);
    return n;
}
extern "C" void fnvnx_win_key(unsigned vk, unsigned scan, int down, unsigned flags)
{
    bool pack = vk == 0xE7, up = !down;
    unsigned ext = flags & 1, spec = vk;
    if (vk == 0 || vk > 254) return;
    if (vk == 0x10) spec = scan == 0x36 ? 0xA1 : 0xA0;
    else if (vk == 0x11) spec = ext ? 0xA3 : 0xA2;
    else if (vk == 0x12) spec = ext ? 0xA5 : 0xA4;
    unsigned gen = (spec >= 0xA0 && spec <= 0xA5) ? 0x10 + (spec - 0xA0) / 2 : spec, e2 = 0;
    if (!scan && !pack) scan = fnvnx_win_vk_scan(spec, &e2), ext |= e2;
    if (spec == 0xA3 || spec == 0xA5) ext = 1;
    bool prev = !pack && (GetKeyState((int)spec) & 0x8000), isalt = gen == 0x12;
    bool alt = isalt ? down : (GetKeyState(0x12) & 0x8000) != 0;
    UINT msg = ((alt || isalt || gen == 0x79) ? 0x104 : 0x100) + (up ? 1 : 0);
    struct { uint32_t vk, scan, flags, time; ULONG_PTR extra; } ll = { pack ? 0xE7 : spec, scan, (ext ? 1u : 0u) | ((flags & 0x10) ? 0x10u : 0u) | (alt ? 0x20u : 0u) | (up ? 0x80u : 0u), (uint32_t)GetTickCount(), 0 };
    if (call_hook(13, 0, msg, (LPARAM)(uintptr_t)&ll)) return;   /* WH_KEYBOARD_LL: nonzero swallows the event */
    if (!pack) fnvnx_set_key_state((int)spec, down);
    HWND tgt = fnwin_key_target();   /* the keyboard focus, else the active window */
    journal(msg, gen | (scan << 8), 1 | (ext << 15), tgt);
    if (!tgt) return;
    bool sys = msg >= 0x104;
    MSG m;
    memset(&m, 0, sizeof m);
    m.message = msg;
    m.wParam = pack ? (WPARAM)(0xE7 | (scan << 16)) : gen;
    m.lParam = (LPARAM)(1u | (pack ? 0 : (scan & 0xFF) << 16) | (ext << 24) | ((sys && alt) ? 1u << 29 : 0) | (((up || prev) ? 1u : 0) << 30) | ((up ? 1u : 0) << 31));
    to_input(tgt, m);
}
static void mouse_in(int x, int y, unsigned changed, unsigned state, int wheel, bool hwheel, unsigned inj)
{
    static const UINT dn[5] = { 0x201, 0x204, 0x207, 0x20B, 0x20B };
    int swap = GetSystemMetrics(23);
    os_lock(&g_mql);
    RECT clip = clip_rect_locked();   /* the cursor stays inside the ClipCursor rectangle */
    os_unlock(&g_mql);
    x = x < clip.left ? clip.left : x >= clip.right ? clip.right - 1 : x;
    y = y < clip.top ? clip.top : y >= clip.bottom ? clip.bottom - 1 : y;
    for (int ev = 0; ev < 7; ev++) {   /* 0: move, 1..5: buttons, 6: wheel */
        UINT msg;
        unsigned data = 0, bit = ev >= 1 && ev <= 5 ? 1u << (ev - 1) : 0;
        bool d = bit && (state & bit);
        if (ev == 0) { os_lock(&g_mql); bool same = x == g_cx && y == g_cy; os_unlock(&g_mql); if (same) continue; msg = 0x200; }
        else if (bit) { if (!(changed & bit)) continue; msg = dn[ev - 1] + (d ? 0 : 1); data = ev >= 4 ? (unsigned)(ev - 3) << 16 : 0; }
        else { if (!wheel) continue; msg = hwheel ? 0x20E : 0x20A; data = (uint32_t)(wheel & 0xFFFF) << 16; }
        int px = ev == 0 ? x : g_cx, py = ev == 0 ? y : g_cy;
        struct { int32_t x, y; uint32_t data, flags, time; ULONG_PTR extra; } ll = { px, py, data, inj & 1, (uint32_t)GetTickCount(), 0 };
        if (call_hook(14, 0, msg, (LPARAM)(uintptr_t)&ll)) continue;   /* WH_MOUSE_LL: nonzero swallows the event */
        os_lock(&g_mql);
        if (ev == 0) { g_cx = x; g_cy = y; }
        if (bit) { g_btn = d ? g_btn | bit : g_btn & ~bit; }
        unsigned ph = g_btn, cx = g_cx, cy = g_cy;
        os_unlock(&g_mql);
        if (bit) fnvnx_set_key_state(ev <= 2 ? ev : ev + 1, d);   /* VK_LBUTTON, RBUTTON, MBUTTON, XBUTTON1/2: the physical state */
        unsigned l = swap ? (ph & ~3u) | ((ph & 1) << 1) | ((ph >> 1) & 1) : ph;   /* logical buttons */
        WPARAM mk = ((l & 1) ? 1 : 0) | ((l & 2) ? 2 : 0) | ((l & 4) ? 0x10 : 0) | ((l & 8) ? 0x20 : 0) | ((l & 16) ? 0x40 : 0) |
                    ((GetKeyState(0x10) & 0x8000) ? 4 : 0) | ((GetKeyState(0x11) & 0x8000) ? 8 : 0);
        if (bit && ev <= 2 && swap) msg = dn[2 - ev] + (d ? 0 : 1);   /* the swapped left/right buttons */
        journal(msg, cx, cy, NULL);
        HWND cap = fnwin_capture();   /* the capturing window gets the mouse wherever it is */
        HWND tgt = cap ? cap : ev == 6 ? fnwin_active() : fnwin_from_point((int)cx, (int)cy);
        fnwin_info wi;
        if (!tgt || !fnwin_info_get(tgt, &wi)) continue;
        if (ev == 6) mk = (mk & 0xFFFF) | (WPARAM)data;
        else if (ev >= 4 && ev <= 5) mk |= data;
        if (bit && d) {   /* a second press of the same button, in time and place, in a CS_DBLCLKS class */
            DWORD now = GetTickCount();
            os_lock(&g_mql);
            int dx = (int)cx - g_dbl.x, dy = (int)cy - g_dbl.y;
            bool dbl = (wi.cstyle & 8) && g_dbl.w == tgt && g_dbl.btn == (int)msg && (now - g_dbl.t) <= g_dbl_ms &&
                       (dx < 0 ? -dx : dx) <= GetSystemMetrics(36) / 2 && (dy < 0 ? -dy : dy) <= GetSystemMetrics(37) / 2;
            g_dbl = dbl ? decltype(g_dbl){ 0, NULL, 0, 0, 0 } : decltype(g_dbl){ (int)msg, tgt, now, (int)cx, (int)cy };
            os_unlock(&g_mql);
            if (dbl) msg += 2;
        }
        MSG m;
        memset(&m, 0, sizeof m);
        m.message = msg; m.wParam = mk;
        long px2 = ev == 6 ? (long)cx : (long)cx - wi.cr.left, py2 = ev == 6 ? (long)cy : (long)cy - wi.cr.top;   /* the wheel: screen coordinates (MSDN) */
        m.lParam = (LPARAM)(((uint32_t)px2 & 0xFFFF) | ((uint32_t)py2 << 16));
        to_input(tgt, m);
    }
}
extern "C" void fnvnx_win_mouse(int x, int y, unsigned changed, unsigned state, int wheel) { mouse_in(x, y, changed, state, wheel, false, 0); }

/* INPUT is 28 bytes in the 32-bit guest: type, then MOUSEINPUT (dx dy data flags time extra) or KEYBDINPUT (vk scan flags time extra) */
extern "C" UINT SendInput(UINT n, void *in, int sz)
{
    if (sz != 28 || (n && !in)) { SetLastError(ERROR_INVALID_PARAMETER); return 0; }
    const uint8_t *p = (const uint8_t *)in;
    UINT done = 0;
    for (UINT i = 0; i < n; i++, p += 28) {
        uint32_t type;
        memcpy(&type, p, 4);
        if (type == 1) {   /* INPUT_KEYBOARD */
            uint16_t vk, sc;
            uint32_t fl;
            memcpy(&vk, p + 4, 2); memcpy(&sc, p + 6, 2); memcpy(&fl, p + 8, 4);
            unsigned ext = fl & 1;
            if (fl & 4) { if (vk) { SetLastError(ERROR_INVALID_PARAMETER); continue; } vk = 0xE7; }   /* KEYEVENTF_UNICODE */
            else if (fl & 8) {   /* KEYEVENTF_SCANCODE: the key of that scan code */
                unsigned v = 1, e;
                for (; v < 255; v++) if (fnvnx_win_vk_scan(v, &e) == sc && e == ext && v != 0x10 && v != 0x11 && v != 0x12) break;
                vk = (uint16_t)v;
            } else if (!vk || vk > 254) { SetLastError(ERROR_INVALID_PARAMETER); continue; }
            fnvnx_win_key(vk, (fl & 4) ? sc : (fl & 8) ? sc : 0, !(fl & 2), ext | 0x10);
            done++;
        } else if (type == 0) {   /* INPUT_MOUSE */
            int32_t dx, dy, data;
            uint32_t fl;
            memcpy(&dx, p + 4, 4); memcpy(&dy, p + 8, 4); memcpy(&data, p + 12, 4); memcpy(&fl, p + 16, 4);
            os_lock(&g_mql);
            int cx = g_cx, cy = g_cy;
            unsigned st = g_btn, ch = 0;
            os_unlock(&g_mql);
            int x = cx, y = cy;
            if (fl & 1) { if (fl & 0x8000) { x = (int)((int64_t)dx * GetSystemMetrics(0) / 65536); y = (int)((int64_t)dy * GetSystemMetrics(1) / 65536); } else { x += dx; y += dy; } }
            static const uint16_t dnf[5] = { 2, 8, 0x20, 0x80, 0x80 }, upf[5] = { 4, 0x10, 0x40, 0x100, 0x100 };
            for (int b = 0; b < 5; b++) {
                if (b >= 3 && data != b - 2) continue;   /* the X button named by mouseData */
                if (fl & dnf[b]) { ch |= 1u << b; st |= 1u << b; }
                else if (fl & upf[b]) { ch |= 1u << b; st &= ~(1u << b); }
            }
            int wheel = (fl & 0x1800) ? (int)data : 0;
            mouse_in(x, y, ch, st, wheel, (fl & 0x1000) != 0, 1);
            done++;
        }   /* INPUT_HARDWARE and unknown types: not inserted */
    }
    return done;
}

/* ---- cursor */
extern "C" HANDLE fnvnx_load_icon_resource(HINSTANCE, LPCSTR, int is_cursor);   /* resource.cpp */
static HCURSOR sys_handle(int *tab, int count, HINSTANCE i, LPCSTR n, const uint16_t *ids, int is_cursor)
{
    uintptr_t id = (uintptr_t)n;
    if (i) return (HCURSOR)fnvnx_load_icon_resource(i, n, is_cursor);   /* a module's resource: the PE resource directory */
    if (!i && id <= 0xFFFF)
        for (int k = 0; k < count; k++)
            if (ids[k] == id) return (HCURSOR)&tab[k];
    SetLastError(1814 /* ERROR_RESOURCE_NAME_NOT_FOUND */);
    return NULL;
}
extern "C" HCURSOR LoadCursorA(HINSTANCE i, LPCSTR n)
{
    static const uint16_t ids[17] = { 32512, 32513, 32514, 32515, 32516, 32640, 32641, 32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650, 32651, 32652 };
    return sys_handle(g_cursors, 17, i, n, ids, 1);
}
extern "C" HICON LoadIconA(HINSTANCE i, LPCSTR n)
{
    static const uint16_t ids[8] = { 32512, 32513, 32514, 32515, 32516, 32517, 32518, 32519 };
    return (HICON)sys_handle(g_icons, 8, i, n, ids, 0);
}
extern "C" HCURSOR fnmq_arrow_cursor(void) { return (HCURSOR)&g_cursors[0]; }
extern "C" HCURSOR fnmq_set_cursor(HCURSOR c)
{
    os_lock(&g_mql);
    mq *q = mq_get(GetCurrentThreadId());
    HCURSOR old = q ? q->cursor : NULL;
    if (q) q->cursor = c;
    os_unlock(&g_mql);
    return old;
}
extern "C" HCURSOR SetCursor(HCURSOR c) { return fnmq_set_cursor(c); }
extern "C" int ShowCursor(BOOL show)
{
    os_lock(&g_mql);
    mq *q = mq_get(GetCurrentThreadId());
    int r = q ? (show ? ++q->cursor_count : --q->cursor_count) : 0;
    os_unlock(&g_mql);
    return r;
}
/* the cursor clip rectangle (ClipCursor): the whole desktop while none is set; right and bottom are exclusive */
static RECT g_clip;
static bool g_clip_set;
static RECT clip_rect_locked(void)
{
    if (g_clip_set)
        return g_clip;
    RECT r = { 0, 0, GetSystemMetrics(0), GetSystemMetrics(1) };
    return r;
}
static void cursor_clamp_locked(int x, int y)
{
    RECT c = clip_rect_locked();
    g_cx = x < c.left ? c.left : x >= c.right ? c.right - 1 : x;
    g_cy = y < c.top ? c.top : y >= c.bottom ? c.bottom - 1 : y;
}
extern "C" void fnvnx_set_cursor_pos(int x, int y)
{
    os_lock(&g_mql);
    cursor_clamp_locked(x, y);
    os_unlock(&g_mql);
}
/* ClipCursor: confines the cursor to the rectangle (clipped to the desktop), NULL lifts it; the cursor moves into the
 * rectangle at once. A rectangle with no area, or outside the desktop, is ERROR_INVALID_PARAMETER. */
extern "C" BOOL ClipCursor(const RECT *r)
{
    RECT d = { 0, 0, GetSystemMetrics(0), GetSystemMetrics(1) };
    RECT c = d;
    if (r) {
        c.left = r->left > d.left ? r->left : d.left;
        c.top = r->top > d.top ? r->top : d.top;
        c.right = r->right < d.right ? r->right : d.right;
        c.bottom = r->bottom < d.bottom ? r->bottom : d.bottom;
        if (c.right <= c.left || c.bottom <= c.top) {
            SetLastError(ERROR_INVALID_PARAMETER);
            return FALSE;
        }
    }
    os_lock(&g_mql);
    g_clip_set = r != NULL;
    g_clip = c;
    cursor_clamp_locked(g_cx, g_cy);
    os_unlock(&g_mql);
    return TRUE;
}
extern "C" BOOL GetClipCursor(RECT *r)
{
    if (!r) { SetLastError(998); return FALSE; }
    os_lock(&g_mql);
    *r = clip_rect_locked();
    os_unlock(&g_mql);
    return TRUE;
}
extern "C" BOOL SetCursorPos(int x, int y) { fnvnx_set_cursor_pos(x, y); return TRUE; }
extern "C" BOOL GetCursorPos(LPPOINT p)
{
    if (!p) { SetLastError(998); return FALSE; }
    os_lock(&g_mql);
    p->x = g_cx; p->y = g_cy;
    os_unlock(&g_mql);
    return TRUE;
}
extern "C" UINT GetDoubleClickTime(void) { return g_dbl_ms; }
extern "C" BOOL SetDoubleClickTime(UINT ms) { g_dbl_ms = ms == 0 ? 500 : ms > 5000 ? 5000 : ms; return TRUE; }

/* ---- lifecycle: Horizon applet events as the messages Windows sends for the same user action */
static void to_all(UINT m, WPARAM w, LPARAM l)
{
    HWND t[256];
    int n = fnwin_toplevels(t, 256);
    for (int i = 0; i < n; i++) SendMessageA(t[i], m, w, l);
}
extern "C" void fnvnx_app_focus(int in)
{
    in = in != 0;
    os_lock(&g_mql);
    int same = g_infocus == in;
    g_infocus = in;
    HWND tgt = g_lost;
    os_unlock(&g_mql);
    if (same) return;   /* already in that state: Windows sends nothing */
    fnwin_info wi;
    if (!in) {
        tgt = fnwin_active();
        if (!tgt) tgt = fnvnx_main_window();
        os_lock(&g_mql); g_lost = tgt; os_unlock(&g_mql);
    } else if (!(tgt && fnwin_info_get(tgt, &wi)))
        tgt = fnvnx_main_window();
    if (!tgt || !fnwin_info_get(tgt, &wi)) return;
    fnwin_set_active(in ? tgt : NULL);
    auto activate_app = [&] {                                 /* WM_ACTIVATEAPP: the thread id of the receiver */
        HWND t[256];
        int n = fnwin_toplevels(t, 256);
        for (int i = 0; i < n; i++) {
            fnwin_info ti;
            if (fnwin_info_get(t[i], &ti)) SendMessageA(t[i], 0x1C, in, (LPARAM)ti.tid);
        }
    };
    if (in) activate_app();                                   /* gaining: the application first, then the window */
    SendMessageA(tgt, 0x86, in, 0);                          /* WM_NCACTIVATE */
    SendMessageA(tgt, 0x06, in ? 1 : 0, 0);                  /* WM_ACTIVATE: WA_ACTIVE / WA_INACTIVE (DefWindowProc takes the focus) */
    if (!in) activate_app();                                  /* losing: the window first, then the application */
    if (in) fnwin_focus_resume(tgt); else fnwin_focus_suspend();   /* WM_SETFOCUS / WM_KILLFOCUS: the window that had the focus */
}
extern "C" void fnvnx_app_suspend(void) { to_all(0x218, 4 /* PBT_APMSUSPEND */, 0); }
extern "C" void fnvnx_app_resume(void)
{
    to_all(0x218, 0x12 /* PBT_APMRESUMEAUTOMATIC */, 0);
    to_all(0x218, 7 /* PBT_APMRESUMESUSPEND */, 0);
    fnvnx_app_focus(1);
}
extern "C" void fnvnx_app_exit_request(void)
{
    HWND h = fnvnx_main_window();
    if (h) PostMessageA(h, WM_CLOSE, 0, 0);
}
extern "C" void fnvnx_app_display_change(int w, int h)
{
    fnvnx_set_display_size(w, h);
    to_all(0x7E, 32, (LPARAM)(((uint32_t)w & 0xFFFF) | ((uint32_t)h << 16)));   /* WM_DISPLAYCHANGE */
}
