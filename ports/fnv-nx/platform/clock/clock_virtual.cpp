/* clock_virtual.cpp - the virtual clocks of the verification mode (fnvnx_clock.h), definition VC1.
 *
 * Each thread has a counter t, nanoseconds. The main thread (and any thread nobody started through CreateThread) starts at 0;
 * a thread CreateThread starts begins at its creator's t at the moment of creation (clk_thread_start).
 *
 *   reads      return t in the API's units from the fixed epoch FILETIME 129383136000000000 (2011-01-01T00:00:00Z) and THEN
 *              advance t by the quantum q = 100 ns (one FILETIME / QueryPerformanceCounter unit): QPC (frequency 10 000 000),
 *              GetTickCount / timeGetTime (t / 10^6 ms), the wall clock (GetSystemTime*, GetLocalTime, _strdate/_strtime
 *              through them), the lifter's rdtsc (clk_read_ns).
 *   Sleep(ms)  t += ms * 10^6; Sleep(0) and a wait with timeout 0 that does not succeed: t += q.
 *   waits      a wait that times out: t += timeout. A wait that succeeds: t = max(t, the signalling thread's t at the signal):
 *              every waitable object carries the stamp of its last signal (platform/win32 w32_obj.clk_t).
 *   messages   a posted or sent message carries the poster's t; the receiver takes max(t, that) on retrieval, MSG.time = t / 10^6.
 *   zone       UTC, no daylight time.
 *   file times written by the platform (creation, last write of what it creates or writes) are the calling thread's t, no advance,
 *              and are read back by GetFileTime / FindFirstFile (an overlay table: the host file systems cannot set times).
 *   devices    the audio mixer's output pump and DirectShow's renderer pump run on virtual time: pump period n happens at
 *              start + n * period / rate, executed by whichever thread's t reaches it (every change of t is such a point) or
 *              by a thread that blocks on an event the device signals. What the device signals carries the period's instant.
 *
 * Nothing here reads a real clock. Real blocking (a wait waits for the real signal) is clock_rt.cpp's. The value of a wait's
 * outcome (signalled or timed out) between two threads that race is not defined by VC1; neither is the order in which two
 * threads that both became runnable run: see the report of the task that wrote this file. */
#include "../include/fnvnx_clock.h"
#include "../os/os_clock.h"

#include <stdlib.h>
#include <string.h>

static const uint64_t Q = 100;                                       /* ns */
static const int64_t EPOCH_FT = 129383136000000000ll;                /* 2011-01-01T00:00:00Z */
static const int64_t FT_UNIX_EPOCH = 116444736000000000ll;           /* 1970-01-01T00:00:00Z */

static __thread uint64_t t_now;     /* this thread's t */
static __thread uint64_t t_scope;   /* the instant a device period signals at, while it runs on this thread */
static __thread int t_scoped;
static __thread int t_in_sync;

/* ---- pumps */
struct clk_pump {
    clk_pump *next;
    clk_pump_fn render, after;
    void *ctx;
    uint32_t rate, period;
    uint64_t t0, n_done;
};
static os_lock_t g_dev_lock;       /* one device period at a time, in the order of their instants */
static clk_pump *g_pumps;

static uint64_t boundary(const clk_pump *k, uint64_t n)
{
    return k->t0 + ((uint64_t)n * k->period * 1000000000ull / k->rate) / Q * Q;
}

/* runs the earliest pending period whose instant is <= limit; 1 if one ran */
static int step_next(uint64_t limit)
{
    os_lock(&g_dev_lock);
    clk_pump *best = NULL;
    uint64_t bb = UINT64_MAX;
    for (clk_pump *k = g_pumps; k; k = k->next) {
        uint64_t b = boundary(k, k->n_done + 1);
        if (b <= limit && b < bb) {
            best = k;
            bb = b;
        }
    }
    if (!best) {
        os_unlock(&g_dev_lock);
        return 0;
    }
    best->n_done++;
    clk_pump_fn after = best->after;
    void *ctx = best->ctx;
    uint32_t period = best->period;
    t_scope = bb;
    t_scoped = 1;
    best->render(ctx, period);
    t_scoped = 0;
    os_unlock(&g_dev_lock);
    if (after)
        after(ctx, period);
    return 1;
}

static void device_sync(uint64_t t)
{
    if (!__atomic_load_n(&g_pumps, __ATOMIC_ACQUIRE) || t_in_sync)
        return;
    t_in_sync = 1;
    while (step_next(t)) {
    }
    t_in_sync = 0;
}

static inline void advance(uint64_t d)
{
    t_now += d;
    device_sync(t_now);
}

/* ---- events the device signals */
struct watch { const void *h; int refs; };
static os_lock_t g_watch_lock;
static watch *g_watch;
static size_t g_watch_n, g_watch_cap;

static int watched(const void *h)
{
    int r = 0;
    os_lock(&g_watch_lock);
    for (size_t i = 0; i < g_watch_n && !r; i++)
        r = g_watch[i].h == h;
    os_unlock(&g_watch_lock);
    return r;
}

/* ---- file times overlay */
struct ftent { char *key; int64_t t[3]; };   /* creation, access, write */
static os_lock_t g_ft_lock;
static ftent *g_ft;
static size_t g_ft_n, g_ft_cap;

static char *fold(const char *p)
{
    size_t n = strlen(p);
    char *k = (char *)malloc(n + 1);
    if (!k)
        return NULL;
    for (size_t i = 0; i <= n; i++)
        k[i] = (char)(p[i] >= 'A' && p[i] <= 'Z' ? p[i] + 32 : p[i]);
    return k;
}

static ftent *ft_find(const char *key)   /* g_ft_lock held */
{
    for (size_t i = 0; i < g_ft_n; i++)
        if (!strcmp(g_ft[i].key, key))
            return &g_ft[i];
    return NULL;
}

static ftent *ft_add(char *key)   /* takes key; g_ft_lock held */
{
    if (g_ft_n == g_ft_cap) {
        size_t nc = g_ft_cap ? g_ft_cap * 2 : 64;
        ftent *n = (ftent *)realloc(g_ft, nc * sizeof *n);
        if (!n) {
            free(key);
            return NULL;
        }
        g_ft = n;
        g_ft_cap = nc;
    }
    ftent *e = &g_ft[g_ft_n++];
    e->key = key;
    memset(e->t, 0, sizeof e->t);
    return e;
}

static void ft_drop(const char *key)   /* g_ft_lock held */
{
    for (size_t i = 0; i < g_ft_n; i++)
        if (!strcmp(g_ft[i].key, key)) {
            free(g_ft[i].key);
            g_ft[i] = g_ft[--g_ft_n];
            return;
        }
}

extern "C" {

const char *clk_name(void) { return "virtual"; }

uint64_t clk_read_ticks(void) { uint64_t v = t_now / Q; advance(Q); return v; }
uint64_t clk_tick_freq(void) { return 10000000ull; }
uint32_t clk_read_tick_ms(void) { uint32_t v = (uint32_t)(t_now / 1000000ull); advance(Q); return v; }
uint32_t clk_read_process_ms(void) { return clk_read_tick_ms(); }
uint64_t clk_read_ns(void) { uint64_t v = t_now; advance(Q); return v; }
int64_t clk_read_filetime(void) { int64_t v = EPOCH_FT + (int64_t)(t_now / Q); advance(Q); return v; }

uint32_t clk_peek_tick_ms(void) { return (uint32_t)(t_now / 1000000ull); }
uint64_t clk_peek_ns(void) { return t_now; }
int64_t clk_peek_filetime(void) { return EPOCH_FT + (int64_t)(t_now / Q); }

/* The zone is not a property of the clock: it is the host's (TZ_AT; the emulator's host answers UTC, the posix test backend the scripted
 * zone). One source for the real and the virtual clock, no literal here. */
int32_t clk_tz_offset_minutes(void) { return os_tz_offset_minutes(); }
int clk_tz_rule(clk_tzrule_t *r)
{
    os_tzrule_t o;
    int rc = os_tz_rule(&o);
    if (rc == 0)
        memcpy(r, &o, sizeof *r);
    return rc;
}

void clk_sleep_ns(uint64_t ns)
{
    advance(ns ? ns : Q);
    os_yield();   /* real scheduling is not part of VC1, but a polling loop must let the thread it waits for run */
}
void clk_wait_timeout(uint64_t ns) { advance(ns ? ns : Q); }
uint64_t clk_stamp(void) { return t_scoped ? t_scope : t_now; }
void clk_merge(uint64_t other)
{
    if (other > t_now) {
        t_now = other;
        device_sync(t_now);
    }
}
void clk_thread_start(uint64_t parent) { t_now = parent; }
uint64_t clk_msg_post(void) { return clk_stamp(); }
uint32_t clk_msg_retrieve(uint64_t stamp, uint32_t time)
{
    (void)time;
    clk_merge(stamp);
    return (uint32_t)(t_now / 1000000ull);
}

int clk_wait_idle(const void *const *handles, unsigned n, uint64_t timeout_ns)
{
    if (!__atomic_load_n(&g_pumps, __ATOMIC_ACQUIRE))
        return 0;
    int any = 0;
    for (unsigned i = 0; i < n && !any; i++)
        any = watched(handles[i]);
    if (!any)
        return 0;
    return step_next(timeout_ns == UINT64_MAX ? UINT64_MAX : t_now + timeout_ns);
}

void clk_device_watch(const void *handle, int on)
{
    os_lock(&g_watch_lock);
    size_t i = 0;
    while (i < g_watch_n && g_watch[i].h != handle)
        i++;
    if (on) {
        if (i < g_watch_n)
            g_watch[i].refs++;
        else {
            if (g_watch_n == g_watch_cap) {
                size_t nc = g_watch_cap ? g_watch_cap * 2 : 32;
                watch *w = (watch *)realloc(g_watch, nc * sizeof *w);
                if (!w) {
                    os_unlock(&g_watch_lock);
                    return;
                }
                g_watch = w;
                g_watch_cap = nc;
            }
            g_watch[g_watch_n++] = watch{ handle, 1 };
        }
    } else if (i < g_watch_n && --g_watch[i].refs == 0) {
        g_watch[i] = g_watch[--g_watch_n];
    }
    os_unlock(&g_watch_lock);
}

void *clk_pump_start(clk_pump_fn render, clk_pump_fn after, void *ctx, uint32_t rate, uint32_t period, size_t stack, int prio, int cpu)
{
    (void)stack; (void)prio; (void)cpu;   /* no thread */
    clk_pump *k = (clk_pump *)calloc(1, sizeof *k);
    if (!k)
        return NULL;
    k->render = render;
    k->after = after;
    k->ctx = ctx;
    k->rate = rate;
    k->period = period;
    k->t0 = t_now;
    os_lock(&g_dev_lock);
    clk_pump **pp = &g_pumps;
    while (*pp)
        pp = &(*pp)->next;
    __atomic_store_n(pp, k, __ATOMIC_RELEASE);
    os_unlock(&g_dev_lock);
    return k;
}

void clk_pump_stop(void *pump)
{
    clk_pump *k = (clk_pump *)pump;
    if (!k)
        return;
    os_lock(&g_dev_lock);   /* no period is running while the lock is held */
    clk_pump **pp = &g_pumps;
    while (*pp && *pp != k)
        pp = &(*pp)->next;
    if (*pp)
        __atomic_store_n(pp, k->next, __ATOMIC_RELEASE);
    os_unlock(&g_dev_lock);
    free(k);
}

void clk_file_stamp(const char *host, int what)
{
    int64_t now = EPOCH_FT + (int64_t)(t_now / Q);
    char *key = fold(host);
    if (!key)
        return;
    os_lock(&g_ft_lock);
    ftent *e = ft_find(key);
    if (e) {
        free(key);
        if (what & CLK_FS_CREATE)
            e->t[0] = e->t[1] = now;
        e->t[2] = now;
    } else {
        int64_t c = now, a = now;
        if (!(what & CLK_FS_CREATE)) {   /* a file the install brought: its creation and access times stay the host's */
            os_stat_t st;
            if (os_stat(host, &st) == 0) {
                c = st.ctime_us * 10 + FT_UNIX_EPOCH;
                a = st.atime_us * 10 + FT_UNIX_EPOCH;
            }
        }
        e = ft_add(key);
        if (e) {
            e->t[0] = c;
            e->t[1] = a;
            e->t[2] = now;
        }
    }
    os_unlock(&g_ft_lock);
}

void clk_file_moved(const char *from, const char *to)
{
    char *kf = fold(from), *kt = fold(to);
    if (kf && kt) {
        os_lock(&g_ft_lock);
        ftent *e = ft_find(kf);
        int64_t t[3] = { 0, 0, 0 };
        int have = e != NULL;
        if (e)
            memcpy(t, e->t, sizeof t);
        ft_drop(kf);
        ft_drop(kt);
        if (have) {
            char *k2 = fold(to);
            ftent *n = k2 ? ft_add(k2) : NULL;
            if (n)
                memcpy(n->t, t, sizeof t);
        }
        os_unlock(&g_ft_lock);
    }
    free(kf);
    free(kt);
}

void clk_reset(void)
{
    t_now = 0;
    t_scoped = 0;
    os_lock(&g_ft_lock);
    for (size_t i = 0; i < g_ft_n; i++)
        free(g_ft[i].key);
    g_ft_n = 0;
    os_unlock(&g_ft_lock);
}

void clk_file_removed(const char *host)
{
    char *k = fold(host);
    if (!k)
        return;
    os_lock(&g_ft_lock);
    ft_drop(k);
    os_unlock(&g_ft_lock);
    free(k);
}

int clk_file_times(const char *host, int64_t ft[3])
{
    char *k = fold(host);
    if (!k)
        return 0;
    os_lock(&g_ft_lock);
    ftent *e = ft_find(k);
    if (e)
        memcpy(ft, e->t, sizeof e->t);
    os_unlock(&g_ft_lock);
    free(k);
    return e != NULL;
}

}
