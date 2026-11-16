/* waitgraph.cpp - the wait-graph watchdog for emulator and device runs (D25 #7: deadlocks, hangs).
 *
 * Recording: every blocking wait of the Win32 stand-ins writes the waiting thread's w32_wait_rec (w32.h): a critical
 * section's slow path (EnterCriticalSection when the section is held by another thread) and every wait of
 * WaitForSingleObject / WaitForMultipleObjects that does not return at once. A record carries the objects, bWaitAll,
 * the timeout, the start time and the caller's address. Off until fnvnx_waitgraph_start: one load of w32_wg_on.
 *
 * Scan (the scanner thread, or fnvnx_waitgraph_scan): a snapshot of every waiting thread under the registry lock and
 * w32_wait_lock (object records are written and cleared under w32_wait_lock, while the wait holds a reference on each
 * object, so the scan only reads live objects). Each wait gets its edges waiter -> owner:
 *   critical section -> OwningThread; mutex -> its owner; thread handle -> that thread; event, semaphore: no owner.
 * Deadlock: the greatest set D of waiting threads, each waiting in the same wait as at the previous scan, such that
 * an all-wait (one object, a section, or bWaitAll) has an object whose owner is in D, and an any-wait has only owned
 * alternatives, every owner in D. D holds the cycles and the threads stuck behind them; every cycle is reported once.
 * A wait with an owner-less object (an event nobody sets) cannot be proved dead this way: it shows up as a long wait.
 * Long wait: a wait older than the limit, reported once per wait (a finite timeout above the limit is reported too).
 *
 * Every line starts "wg: " and names threads by id, objects and callers by address only:
 *   wg: deadlock cycle 2 threads: tid 0x1004 cs 0x0123abcd pc 0x00401234 owner 0x1008 -> tid 0x1008 mutex ...
 *   wg: long-wait tid 0x1004 event 0x0123abcd owner - pc 0x00401234 waited 10012 ms timeout INFINITE
 * tools/threads/report.py turns a log into the report lane's table (the pc -> function start addresses).
 *
 * Autostart (the guest of a report lane, tools/threads/variant.sh): fnvnx_waitgraph_autostart(limit) from a
 * constructor turns recording on before the game runs; the scanner thread (1 s) starts at the first blocking wait and
 * calls fnvnx_threads_flush every FNVNX_THREADS_DUMP_S seconds. */
#include "w32.h"

#include <stdio.h>
#include <stdlib.h>

volatile int w32_wg_on;

#define WG_SLOTS 256
#define WG_SEEN 256
static os_lock_t g_reg;                       /* the thread table; taken before w32_wait_lock, never inside it */
static struct w32_thread *g_slot[WG_SLOTS];
static uint32_t g_prev_seq[WG_SLOTS];         /* the record's seq at the previous scan (stability) */
static uint32_t g_long_seq[WG_SLOTS];         /* the seq of the last wait reported as long, + 1 */
static uint32_t g_cycle_seen[WG_SEEN];        /* signatures of the cycles already reported */
static unsigned g_ncycle_seen;
static unsigned g_cycles, g_long, g_scans, g_threads;
static uint64_t g_limit_ns = 10000ull * 1000000ull;
static unsigned g_period_ms;
static fnvnx_waitgraph_sink g_sink;
static os_thread *g_scanner;
static volatile int g_run;

static void emit(const char *line) { (g_sink ? g_sink : os_debug_print)(line); }
static volatile int g_auto_pending;
static void start_scanner(void);

/* safe from a static constructor: no thread, no allocation, no lock */
extern "C" void fnvnx_waitgraph_autostart(unsigned limit_ms)
{
    g_limit_ns = (uint64_t)(limit_ms ? limit_ms : 10000) * 1000000ull;
    g_period_ms = 1000;
    g_auto_pending = 1;
    __atomic_store_n(&w32_wg_on, 1, __ATOMIC_SEQ_CST);
}
#ifndef FNVNX_THREADS_DUMP_S
#define FNVNX_THREADS_DUMP_S 10
#endif

static void reg(struct w32_thread *t)
{
    if (__builtin_expect(g_auto_pending, 0) && __atomic_exchange_n(&g_auto_pending, 0, __ATOMIC_SEQ_CST))
        start_scanner();   /* outside every lock: the first blocking wait of an autostarted guest */
    if (t->wg_slot)
        return;
    os_lock(&g_reg);
    for (int i = 0; i < WG_SLOTS; i++)
        if (!g_slot[i]) {
            g_slot[i] = t;
            g_prev_seq[i] = 0;
            g_long_seq[i] = 0;
            t->wg_slot = i + 1;
            g_threads++;
            break;
        }
    os_unlock(&g_reg);
}

void w32_wg_forget(struct w32_thread *t)
{
    if (!t->wg_slot)
        return;
    os_lock(&g_reg);
    g_slot[t->wg_slot - 1] = NULL;
    t->wg_slot = 0;
    os_unlock(&g_reg);
}

static void rec_open(struct w32_thread *t) { __atomic_store_n(&t->wait.seq, t->wait.seq + 1, __ATOMIC_SEQ_CST); __atomic_thread_fence(__ATOMIC_SEQ_CST); }
static void rec_close(struct w32_thread *t) { __atomic_store_n(&t->wait.seq, t->wait.seq + 1, __ATOMIC_SEQ_CST); }

void w32_wg_cs_begin(struct w32_thread *t, LPCRITICAL_SECTION cs, uint32_t pc)
{
    reg(t);
    rec_open(t);
    t->wait.kind = W32_WG_CS;
    t->wait.pc = t->wg_caller ? t->wg_caller : pc;
    t->wait.ms = INFINITE;
    t->wait.t0 = clk_rt_ns();
    t->wait.all = 1;
    t->wait.n = 1;
    t->wait.obj[0] = cs;
    rec_close(t);
}

void w32_wg_obj_begin(struct w32_thread *t, struct w32_obj *const *objs, DWORD n, BOOL all, DWORD ms, uint32_t pc)
{
    /* the caller holds w32_wait_lock and called w32_wg_prepare before taking it */
    rec_open(t);
    t->wait.kind = W32_WG_OBJ;
    t->wait.pc = t->wg_caller ? t->wg_caller : pc;
    t->wait.ms = ms;
    t->wait.t0 = clk_rt_ns();
    t->wait.all = all || n == 1;
    t->wait.n = n > W32_WG_MAXOBJ ? W32_WG_MAXOBJ : (int)n;
    for (int i = 0; i < t->wait.n; i++)
        t->wait.obj[i] = objs[i];
    rec_close(t);
}

void w32_wg_end(struct w32_thread *t)
{
    if (!t->wait.kind)
        return;
    rec_open(t);
    t->wait.kind = 0;
    rec_close(t);
}

extern "C" void fnvnx_waitgraph_set_caller(unsigned pc) { w32_self()->wg_caller = pc; }

/* registration must not happen under w32_wait_lock (lock order g_reg -> w32_wait_lock) */
void w32_wg_prepare(struct w32_thread *t) { reg(t); }

/* ---- scan */
struct node {
    int slot;
    DWORD tid;
    struct w32_wait_rec r;
    DWORD owner[W32_WG_MAXOBJ];   /* 0: owner-less (event, semaphore, free) */
    int type[W32_WG_MAXOBJ];      /* 0: critical section; W32_* otherwise */
    int stable, in_d, mark;
};

static const char *type_name(int ty)
{
    switch (ty) {
    case 0: return "cs";
    case W32_EVENT: return "event";
    case W32_MUTEX: return "mutex";
    case W32_SEMAPHORE: return "semaphore";
    case W32_THREAD: return "thread";
    case W32_FILE: return "file";
    default: return "object";
    }
}

static int find_tid(struct node *v, int nv, DWORD tid)
{
    for (int i = 0; i < nv; i++)
        if (v[i].tid == tid)
            return i;
    return -1;
}

/* the first object whose owner is in D (for an any-wait every alternative's owner is in D) */
static int blocking_succ(struct node *v, int nv, int i, int *which)
{
    for (int k = 0; k < v[i].r.n; k++) {
        int j = v[i].owner[k] ? find_tid(v, nv, v[i].owner[k]) : -1;
        if (j >= 0 && v[j].in_d) {
            *which = k;
            return j;
        }
    }
    return -1;
}

static int still_dead(struct node *v, int nv, int i)
{
    int any_in = 0;
    for (int k = 0; k < v[i].r.n; k++) {
        int j = v[i].owner[k] && v[i].owner[k] != v[i].tid ? find_tid(v, nv, v[i].owner[k]) : -1;
        int in = j >= 0 && v[j].in_d;
        if (v[i].r.all) {
            if (in)
                return 1;
        } else {
            if (!in)
                return 0;       /* an alternative that can still become free */
            any_in = 1;
        }
    }
    return v[i].r.all ? 0 : any_in;
}

extern "C" void fnvnx_waitgraph_scan(void)
{
    static struct node v[WG_SLOTS];
    int nv = 0;
    uint64_t now = clk_rt_ns();
    os_lock(&g_reg);
    os_lock(&w32_wait_lock);
    for (int s = 0; s < WG_SLOTS; s++) {
        struct w32_thread *t = g_slot[s];
        if (!t)
            continue;
        uint32_t a = __atomic_load_n(&t->wait.seq, __ATOMIC_SEQ_CST);
        struct w32_wait_rec r = t->wait;
        __atomic_thread_fence(__ATOMIC_SEQ_CST);
        uint32_t b = __atomic_load_n(&t->wait.seq, __ATOMIC_SEQ_CST);
        if (a != b || (a & 1) || !r.kind)
            continue;
        struct node *x = &v[nv++];
        memset(x, 0, sizeof *x);
        x->slot = s;
        x->tid = t->tid;
        x->r = r;
        x->r.seq = a;
        x->stable = g_prev_seq[s] == a;
        g_prev_seq[s] = a;
        for (int k = 0; k < r.n; k++) {
            if (r.kind == W32_WG_CS) {
                x->type[k] = 0;
                x->owner[k] = (DWORD)(uintptr_t)__atomic_load_n(&((LPCRITICAL_SECTION)r.obj[k])->OwningThread,
                                                                __ATOMIC_SEQ_CST);
            } else {
                struct w32_obj *o = (struct w32_obj *)r.obj[k];
                x->type[k] = o->type;
                x->owner[k] = o->type == W32_MUTEX ? w32_wg_mutex_owner_locked(o)
                              : o->type == W32_THREAD ? w32_thread_obj_tid(o) : 0;
                /* a finished thread or a free mutex has no edge: the wait is about to return */
                if (o->type == W32_THREAD && w32_thread_signaled(o))
                    x->owner[k] = 0;
            }
        }
    }
    os_unlock(&w32_wait_lock);
    os_unlock(&g_reg);

    char line[1024];
    /* long waits */
    for (int i = 0; i < nv; i++) {
        struct node *x = &v[i];
        uint64_t age = now > x->r.t0 ? now - x->r.t0 : 0;
        if (age < g_limit_ns || g_long_seq[x->slot] == x->r.seq + 1)
            continue;
        g_long_seq[x->slot] = x->r.seq + 1;
        g_long++;
        int o = snprintf(line, sizeof line, "wg: long-wait tid 0x%x", (unsigned)x->tid);
        for (int k = 0; k < x->r.n && o < (int)sizeof line - 80; k++) {
            o += snprintf(line + o, sizeof line - (size_t)o, " %s 0x%08x owner ", type_name(x->type[k]),
                          (unsigned)(uintptr_t)x->r.obj[k]);
            o += x->owner[k] ? snprintf(line + o, sizeof line - (size_t)o, "0x%x", (unsigned)x->owner[k])
                             : snprintf(line + o, sizeof line - (size_t)o, "-");
        }
        o += snprintf(line + o, sizeof line - (size_t)o, "%s pc 0x%08x waited %u ms timeout ",
                      x->r.n > 1 ? (x->r.all ? " (all)" : " (any)") : "", (unsigned)x->r.pc,
                      (unsigned)(age / 1000000ull));
        if (x->r.ms == INFINITE)
            snprintf(line + o, sizeof line - (size_t)o, "INFINITE");
        else
            snprintf(line + o, sizeof line - (size_t)o, "%u ms", (unsigned)x->r.ms);
        emit(line);
    }

    /* deadlocks: the greatest fixpoint D over the waits that did not change since the previous scan */
    for (int i = 0; i < nv; i++)
        v[i].in_d = v[i].stable;
    for (int changed = 1; changed;) {
        changed = 0;
        for (int i = 0; i < nv; i++)
            if (v[i].in_d && !still_dead(v, nv, i)) {
                v[i].in_d = 0;
                changed = 1;
            }
    }
    for (int i = 0; i < nv; i++)
        v[i].mark = 0;
    for (int i = 0; i < nv; i++) {
        if (!v[i].in_d || v[i].mark)
            continue;
        int path[WG_SLOTS], which[WG_SLOTS], np = 0, j = i, k = 0;
        while (j >= 0 && !v[j].mark) {
            v[j].mark = 1 + i;   /* this walk */
            int nx = blocking_succ(v, nv, j, &k);
            path[np] = j;
            which[np++] = k;
            j = nx;
        }
        if (j < 0 || v[j].mark != 1 + i)
            continue;            /* runs into a cycle another walk reported, or out of D */
        int start = 0;
        while (path[start] != j)
            start++;
        uint32_t sig = 2166136261u, lo = 0xffffffffu;
        for (int p = start; p < np; p++)
            lo = v[path[p]].tid < lo ? v[path[p]].tid : lo;
        for (int p = start; p < np; p++)   /* order-independent: sum of per-edge hashes */
            sig += (v[path[p]].tid * 2654435761u) ^ ((uint32_t)(uintptr_t)v[path[p]].r.obj[which[p]] * 40503u) ^ lo;
        int seen = 0;
        for (unsigned q = 0; q < g_ncycle_seen; q++)
            seen |= g_cycle_seen[q] == sig;
        if (seen)
            continue;
        if (g_ncycle_seen < WG_SEEN)
            g_cycle_seen[g_ncycle_seen++] = sig;
        g_cycles++;
        int o = snprintf(line, sizeof line, "wg: deadlock cycle %d threads:", np - start);
        for (int p = start; p < np && o < (int)sizeof line - 120; p++) {
            const struct node *x = &v[path[p]];
            int w = which[p];
            o += snprintf(line + o, sizeof line - (size_t)o, "%s tid 0x%x %s 0x%08x pc 0x%08x owner 0x%x",
                          p > start ? " ->" : "", (unsigned)x->tid, type_name(x->type[w]),
                          (unsigned)(uintptr_t)x->r.obj[w], (unsigned)x->r.pc, (unsigned)x->owner[w]);
        }
        emit(line);
    }
    g_scans++;
}

extern "C" void fnvnx_threads_flush(void)
{
    if (w32_wg_on)
        fnvnx_waitgraph_summary();
}

static void scanner(void *arg)
{
    W32_UNUSED(arg);
    uint64_t next_dump = clk_rt_ns() + FNVNX_THREADS_DUMP_S * 1000000000ull;
    while (__atomic_load_n(&g_run, __ATOMIC_SEQ_CST)) {
        for (unsigned left = g_period_ms; left && __atomic_load_n(&g_run, __ATOMIC_SEQ_CST);) {
            unsigned step = left > 50 ? 50 : left;
            clk_rt_sleep_ns((uint64_t)step * 1000000ull);
            left -= step;
        }
        if (__atomic_load_n(&g_run, __ATOMIC_SEQ_CST))
            fnvnx_waitgraph_scan();
        if (g_sink == NULL && clk_rt_ns() >= next_dump) {   /* a lane's guest: periodic flush */
            fnvnx_threads_flush();
            next_dump = clk_rt_ns() + FNVNX_THREADS_DUMP_S * 1000000000ull;
        }
    }
}

static void start_scanner(void)
{
    if (g_scanner || !g_period_ms)
        return;
    g_run = 1;
    g_scanner = os_thread_create(scanner, NULL, 64 * 1024, OS_PRIO_NORMAL, -1);
    if (g_scanner && os_thread_start(g_scanner)) {
        os_thread_free(g_scanner);
        g_scanner = NULL;
    }
    if (!g_scanner)
        emit("wg: no scanner thread (call fnvnx_waitgraph_scan)");
}

extern "C" void fnvnx_waitgraph_start(unsigned limit_ms, unsigned period_ms, fnvnx_waitgraph_sink sink)
{
    g_sink = sink;
    g_limit_ns = (uint64_t)(limit_ms ? limit_ms : 10000) * 1000000ull;
    g_period_ms = period_ms;
    g_auto_pending = 0;
    __atomic_store_n(&w32_wg_on, 1, __ATOMIC_SEQ_CST);
    start_scanner();
}

extern "C" void fnvnx_waitgraph_stop(void)
{
    if (!g_scanner)
        return;
    __atomic_store_n(&g_run, 0, __ATOMIC_SEQ_CST);
    os_thread_join(g_scanner);
    os_thread_free(g_scanner);
    g_scanner = NULL;
}

extern "C" void fnvnx_waitgraph_counts(unsigned *cycles, unsigned *long_waits, unsigned *scans)
{
    if (cycles) *cycles = g_cycles;
    if (long_waits) *long_waits = g_long;
    if (scans) *scans = g_scans;
}

extern "C" void fnvnx_waitgraph_summary(void)
{
    char line[160];
    snprintf(line, sizeof line, "wg: summary cycles %u long-waits %u scans %u threads %u limit %u ms", g_cycles,
             g_long, g_scans, g_threads, (unsigned)(g_limit_ns / 1000000ull));
    emit(line);
}
