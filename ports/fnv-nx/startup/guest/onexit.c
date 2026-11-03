/* onexit.c - the game's onexit table with MSVC 2008 CRT semantics (stream S10, docs/STARTUP.md
 * "Termination").
 *
 * crt0dat.c doexit(): under _lockexit (a recursive lock held across the handlers), walk the table from the
 * end, skip entries already run, clear the entry, call it, and if the table changed (a handler registered
 * more) continue from the new end. So every handler runs once, in reverse order of registration, and
 * handlers registered during exit run next. Then (not quick) the pre-terminators __xp, the terminators
 * __xt (CRT-internal: stdio flush; natively the libc exit that follows), and ExitProcess unless retcaller.
 *
 * One table holds every game registration so that relative order is kept between atexit/_onexit calls,
 * the ??__F registrations of the generated initializers and (after S2's rename) the __cxa_atexit
 * registrations GCC emits for function-local statics, as MSVC kept them in one table.
 */
#include "fnvnx_xc.h"

#include <stdlib.h>
#include <string.h>

typedef struct {
    union {
        fnvnx_pvfv f0;
        void (*f1)(void *);
    } fn;
    void *arg;
    uint8_t has_arg;
    uint8_t done;
} exit_entry;

#define STATIC_ENTRIES 64

static exit_entry g_static[STATIC_ENTRIES];
static exit_entry *g_tab = g_static;
static uint32_t g_count, g_cap = STATIC_ENTRIES, g_pending;
static int g_exit_done;   /* a full doexit has run: later registrations are kept but never run */

/* Recursive spin lock; the owner token is the address of a thread-local byte (unique per thread). */
static __thread char g_tok;
static void *volatile g_owner;
static uint32_t g_depth;
static void (*g_yield)(void);
static void (*g_exit_hook)(int) = exit;

static void lock(void) {
    void *me = &g_tok;
    if (__atomic_load_n(&g_owner, __ATOMIC_ACQUIRE) == me) {
        g_depth++;
        return;
    }
    for (;;) {
        void *expected = NULL;
        if (__atomic_compare_exchange_n(&g_owner, &expected, me, 0, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED)) break;
        if (g_yield) g_yield();
    }
    g_depth = 1;
}

static void unlock(void) {
    if (--g_depth == 0) __atomic_store_n(&g_owner, NULL, __ATOMIC_RELEASE);
}

static int push(exit_entry e) {
    int ok = 0;
    lock();
    if (g_count == g_cap) {
        uint32_t ncap = g_cap * 2;
        exit_entry *n;
        if (g_tab == g_static) {
            n = malloc(ncap * sizeof *n);
            if (n) memcpy(n, g_static, sizeof g_static);
        } else {
            n = realloc(g_tab, ncap * sizeof *n);
        }
        if (!n) goto out;
        g_tab = n;
        g_cap = ncap;
    }
    g_tab[g_count++] = e;
    g_pending++;
    ok = 1;
out:
    unlock();
    return ok;
}

fnvnx_pvfv fnvnx_xc_onexit(fnvnx_pvfv f) {
    exit_entry e;
    memset(&e, 0, sizeof e);
    e.fn.f0 = f;
    return (f && push(e)) ? f : NULL;
}

int fnvnx_xc_atexit(fnvnx_pvfv f) { return fnvnx_xc_onexit(f) ? 0 : -1; }

int fnvnx_xc_cxa_atexit(void (*f)(void *), void *arg, void *dso) {
    (void)dso; /* one module: every handler belongs to the game */
    exit_entry e;
    memset(&e, 0, sizeof e);
    if (!f) return -1;
    e.fn.f1 = f;
    e.arg = arg;
    e.has_arg = 1;
    return push(e) ? 0 : -1;
}

/* Highest index below *end whose entry has not run, or -1. */
static int32_t next_pending(uint32_t end) {
    while (end > 0) {
        end--;
        if (!g_tab[end].done) return (int32_t)end;
    }
    return -1;
}

void fnvnx_xc_doexit(int code, int quick, int retcaller) {
    lock();
    if (!g_exit_done && !quick) {
        for (;;) {
            int32_t i = next_pending(g_count); /* re-read: handlers may have registered more */
            if (i < 0) break;
            exit_entry e = g_tab[i];         /* the table may move while the handler runs */
            g_tab[i].done = 1;
            g_pending--;
            if (e.has_arg) e.fn.f1(e.arg);
            else e.fn.f0();
        }
        /* __xp_a..__xp_z / __xt_a..__xt_z hold CRT functions only (stdio flush, ...): the libc exit that
         * the exit hook calls does the native equivalent. */
    }
    if (retcaller) {
        unlock();
        return;
    }
    g_exit_done = 1;
    unlock();
    g_exit_hook(code);
    for (;;) {} /* the hook must not return */
}

void fnvnx_xc_exit(int code) { fnvnx_xc_doexit(code, 0, 0); }
void fnvnx_xc_cexit(void) { fnvnx_xc_doexit(0, 0, 1); }
void fnvnx_xc_c_exit(void) { fnvnx_xc_doexit(0, 1, 1); }

void fnvnx_xc_set_exit_hook(void (*hook)(int code)) { g_exit_hook = hook ? hook : exit; }
void fnvnx_xc_set_yield_hook(void (*yield)(void)) { g_yield = yield; }

uint32_t fnvnx_xc_onexit_count(void) { return g_count; }
uint32_t fnvnx_xc_onexit_pending(void) { return g_pending; }

void fnvnx_xc_onexit_reset(void) {
    /* Tests leave doexit through a longjmp-ing exit hook, possibly with the lock still held: drop it. */
    if (g_tab != g_static) free(g_tab);
    g_tab = g_static;
    g_cap = STATIC_ENTRIES;
    g_count = g_pending = 0;
    g_exit_done = 0;
    memset(g_static, 0, sizeof g_static);
    g_depth = 0;
    __atomic_store_n(&g_owner, NULL, __ATOMIC_RELEASE);
}
