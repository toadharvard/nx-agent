/* host_boundary.cpp - the host test process's implementation of shim/include/fnvnx_boundary.h.
 * "The game" is the native test code: an export's value is its native address, a game function is called natively, a
 * game thread runs natively on the OS thread the platform made, and there is no loader (images are registered by the
 * tests through fnvnx_win32_register_image). The calls are
 * counted (host_boundary.h) so the tests can see what the platform sent through the boundary. */
#include "fnvnx_boundary.h"
#include "host_boundary.h"
#include <dlfcn.h>
#include <execinfo.h>
#include <stdio.h>

#include <stdlib.h>
#include <string.h>
#include <string>
#include "fnvnx_win32_ext.h"

/* The declared fonts of the host tests (fnvnx_win32_ext.h): the reference environment's font set, the three directories named by
 * FNVNX_FONT_REF (wine-fonts/usr/share/wine/fonts, reg) and FNVNX_FONT_HOST. They are an input of every test binary, like the
 * registry hive: a run without them is not a run, so it stops here with the name of the missing input. */
extern "C" const struct fnvnx_font_dirs *fnvnx_declared_fonts(void)
{
    static std::string wine, reg, host;
    static fnvnx_font_dirs d;
    if (!d.wine) {
        const char *r = getenv("FNVNX_FONT_REF"), *h = getenv("FNVNX_FONT_HOST");
        if (!r || !h) {
            fprintf(stderr, "declared fonts missing: set FNVNX_FONT_REF and FNVNX_FONT_HOST (the reference font set)\n");
            abort();
        }
        wine = std::string(r) + "/wine-fonts/usr/share/wine/fonts"; reg = std::string(r) + "/reg"; host = h;
        d.wine = wine.c_str(); d.host = host.c_str(); d.reg = reg.c_str();
    }
    return &d;
}

uint32_t g_reach_last;
int g_reach_count;
extern "C" void fnvnx_reach_violation(uint32_t fact_id)
{
    g_reach_last = fact_id;
    g_reach_count++;
    throw ReachHit{fact_id};
}

extern "C" int g_bnd_lock_violations;
extern "C" const char *g_bnd_test;
void (*g_bnd_process_detach_hook)(void);
extern "C" unsigned fnvnx_bnd_locks_held(void);

/* ---- the platform locks each thread holds (os_posix.c reports every os_lock / os_trylock / os_unlock). The list keeps the
 * lock and the address its acquirer returns to, so a violation names the acquisition site. */
namespace {
struct Held { const void *lock, *site; };
const unsigned HELD_MAX = 256;
__thread Held t_held[HELD_MAX];
__thread unsigned t_nheld;
const int SITES_MAX = 32;
struct Site { const void *site; const void *callee; };
Site g_sites[SITES_MAX];
int g_nsites;
volatile int g_sites_lock;
}

extern "C" void fnvnx_lockcount_note(int delta, const void *lock, const void *site)
{
    if (delta > 0) {
        if (t_nheld < HELD_MAX)
            t_held[t_nheld] = Held{lock, site};
        t_nheld++;
        return;
    }
    unsigned n = t_nheld < HELD_MAX ? t_nheld : HELD_MAX;
    for (unsigned i = n; i-- > 0;)
        if (t_held[i].lock == lock) {
            for (unsigned j = i + 1; j < n; j++)
                t_held[j - 1] = t_held[j];
            t_nheld--;
            return;
        }
    /* a lock released by a thread other than the one that took it: not in this thread's list */
}

static void note_violation(fnvnx_gaddr callee)
{
    while (__atomic_exchange_n(&g_sites_lock, 1, __ATOMIC_ACQUIRE)) {}
    unsigned n = t_nheld < HELD_MAX ? t_nheld : HELD_MAX;
    for (unsigned i = 0; i < n; i++) {
        bool seen = false;
        for (int k = 0; k < g_nsites; k++)
            seen |= g_sites[k].site == t_held[i].site;
        if (!seen && g_nsites < SITES_MAX)
            g_sites[g_nsites++] = Site{t_held[i].site, (const void *)(uintptr_t)callee};
    }
    __atomic_store_n(&g_sites_lock, 0, __ATOMIC_RELEASE);
}

/* ---- the entries into the game seen so far, per call stack (development aid for the coverage table of the rule): with
 * FNVNX_BND_SITES=<file> set, the stacks (5 frames above the boundary function) and the test case that first drove them are
 * written there at exit; resolve with `addr2line -i -e <test binary>`. */
namespace {
struct Trace { const char *kind, *test; void *fr[6]; int n; };
const int TRACES_MAX = 8192;
Trace g_traces[TRACES_MAX];
int g_ntraces;
volatile int g_trace_lock;
const char *g_trace_path = getenv("FNVNX_BND_SITES");

void trace_dump()
{
    FILE *f = fopen(g_trace_path, "w");
    if (!f)
        return;
    Dl_info di = {};
    dladdr((void *)&trace_dump, &di);
    fprintf(f, "BASE %p\n", di.dli_fbase);   /* the load address of the test binary (a PIE): addr2line takes addr - BASE */
    for (int i = 0; i < g_ntraces; i++) {
        fprintf(f, "%s %s", g_traces[i].kind, g_traces[i].test ? g_traces[i].test : "-");
        for (int k = 0; k < g_traces[i].n; k++)
            fprintf(f, " %p", g_traces[i].fr[k]);
        fprintf(f, "\n");
    }
    fclose(f);
}

__attribute__((noinline)) void trace_entry(const char *kind)
{
    if (!g_trace_path)
        return;
    void *fr[8];
    int n = backtrace(fr, 8);
    Trace t = {kind, g_bnd_test, {}, 0};
    for (int k = 2; k < n && t.n < 6; k++)   /* skip trace_entry and the boundary function itself */
        t.fr[t.n++] = fr[k];
    while (__atomic_exchange_n(&g_trace_lock, 1, __ATOMIC_ACQUIRE)) {}
    bool seen = false;
    for (int i = 0; i < g_ntraces && !seen; i++)
        seen = g_traces[i].n == t.n && !memcmp(g_traces[i].fr, t.fr, sizeof(void *) * (size_t)t.n);
    if (!seen && g_ntraces < TRACES_MAX) {
        if (!g_ntraces)
            atexit(trace_dump);
        g_traces[g_ntraces++] = t;
    }
    __atomic_store_n(&g_trace_lock, 0, __ATOMIC_RELEASE);
}

/* every entry into the game: the platform may hold no lock, and the entry is recorded for the coverage table */
void enter_game(const char *kind, fnvnx_gaddr callee)
{
    if (fnvnx_bnd_locks_held()) {
        __atomic_fetch_add(&g_bnd_lock_violations, 1, __ATOMIC_RELAXED);
        note_violation(callee);
    }
    trace_entry(kind);
}
}

extern "C" {
const char *g_bnd_test;
unsigned fnvnx_bnd_locks_held(void) { return t_nheld; }

static void print_site(const void *site, const char *what, const void *callee)
{
    Dl_info di = {};
    dladdr(site, &di);
    printf("  lock taken at %p (%s+0x%lx, module offset 0x%lx)%s%p\n", site, di.dli_sname ? di.dli_sname : "?",
           di.dli_saddr ? (unsigned long)((const char *)site - (const char *)di.dli_saddr) : 0ul,
           (unsigned long)((const char *)site - (const char *)di.dli_fbase), what, callee);
}

void fnvnx_bnd_print_lock_violations(void)
{
    for (int k = 0; k < g_nsites; k++)
        print_site(g_sites[k].site, ", guest callee ", g_sites[k].callee);
}

struct fnvnx_bnd_mark fnvnx_bnd_violation_mark(void)
{
    return fnvnx_bnd_mark{__atomic_load_n(&g_bnd_lock_violations, __ATOMIC_RELAXED), g_nsites};
}

void fnvnx_bnd_violation_forget(struct fnvnx_bnd_mark m)
{
    __atomic_store_n(&g_bnd_lock_violations, m.violations, __ATOMIC_RELAXED);
    g_nsites = m.sites;
}

void fnvnx_bnd_print_held(void)
{
    for (unsigned i = 0; i < t_nheld && i < HELD_MAX; i++)
        print_site(t_held[i].site, ", still held ", 0);
}

int g_bnd_lock_violations;   /* guest calls made while the thread held a platform lock (see host_boundary.h) */
int g_bnd_exports, g_bnd_invokes, g_bnd_threads_created, g_bnd_threads_run, g_bnd_threads_freed,
    g_bnd_threads_freed_unrun, g_bnd_process_detaches;
uint32_t (*g_bnd_loader)(const char *name, uint32_t *base);
int (*g_bnd_invoke_hook)(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind,
                         struct fnvnx_bnd_ret *out);

/* the product's import-key table has rows the platform tables do not (bridge `sym` rows of other owners): the hook stands
 * for such a row, asked when the platform has no body */
fnvnx_gaddr (*g_bnd_key_hook)(const char *dll, const char *name, uint32_t ordinal);
fnvnx_gaddr fnvnx_bnd_export_value(const char *dll, const char *name, uint32_t ord, const void *native)
{
    fnvnx_gaddr v = (fnvnx_gaddr)native;
    if (!v && g_bnd_key_hook)
        v = g_bnd_key_hook(dll, name, ord);
    if (v)
        __atomic_fetch_add(&g_bnd_exports, 1, __ATOMIC_RELAXED);
    return v;
}

typedef uintptr_t W;
void fnvnx_bnd_guest_invoke(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind,
                            struct fnvnx_bnd_ret *out)
{
    __atomic_fetch_add(&g_bnd_invokes, 1, __ATOMIC_RELAXED);
    enter_game("invoke", addr);
    if (g_bnd_invoke_hook && g_bnd_invoke_hook(addr, ecx, nargs, args, kind, out))
        return;
    W a[16] = {0};
    for (uint32_t i = 0; i < nargs && i < 16; i++)
        a[i] = args[i];
    /* SysV: the caller pops its arguments, so a callee that declares fewer than the 16 passed ones is called correctly */
#define ARGS a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], a[11], a[12], a[13], a[14], a[15]
    struct fnvnx_bnd_ret r = {0, 0, 0.0};
    if (kind == FNVNX_BND_RET_ST0) {
        r.st0 = ((double (*)(W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W))addr)(ARGS);
    } else {
        uint64_t v = ((uint64_t(*)(W, W, W, W, W, W, W, W, W, W, W, W, W, W, W, W))addr)(ARGS);
        /* a pointer / LONG_PTR result is pointer-wide here; edx:eax of an int64 result are its two halves */
        r.eax = kind == FNVNX_BND_RET_I64 ? (uint32_t)v : (fnvnx_gword)v;
        r.edx = kind == FNVNX_BND_RET_I64 ? (uint32_t)(v >> 32) : 0;
    }
#undef ARGS
    if (out)
        *out = r;
}

struct fnvnx_bnd_thread {
    fnvnx_gaddr start;
};

fnvnx_bnd_thread *fnvnx_bnd_thread_create(fnvnx_gaddr start, size_t)
{
    fnvnx_bnd_thread *t = (fnvnx_bnd_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->start = start;
    __atomic_fetch_add(&g_bnd_threads_created, 1, __ATOMIC_RELAXED);
    return t;
}

uint32_t fnvnx_bnd_thread_run(fnvnx_bnd_thread *t, fnvnx_gaddr param)
{
    enter_game("thread_run", t->start);
    __atomic_fetch_add(&g_bnd_threads_run, 1, __ATOMIC_RELAXED);
    /* the parameter is a native pointer here, wider than a stack word: the start routine is called directly */
    return (uint32_t)((unsigned long (*)(void *))t->start)((void *)param);
}

void fnvnx_bnd_thread_release(fnvnx_bnd_thread *t, int notify)
{
    __atomic_fetch_add(&g_bnd_threads_freed, 1, __ATOMIC_RELAXED);
    if (!notify)
        __atomic_fetch_add(&g_bnd_threads_freed_unrun, 1, __ATOMIC_RELAXED);
    else   /* the modules hear of the thread detach: a call into the game */
        enter_game("thread_release", t->start);
    free(t);
}

uint32_t fnvnx_bnd_module_load(const char *name, uint32_t *base)
{
    enter_game("module_load", 0);   /* the loader runs the modules' DllMain */
    if (g_bnd_loader)
        return g_bnd_loader(name, base);
    return 126;   /* ERROR_MOD_NOT_FOUND: the host process has no translated modules */
}

/* the host implementation records the flag per module base (what the loader keeps in its module row) */
uint32_t g_bnd_disabled[16];
int g_bnd_disabled_n;
uint32_t fnvnx_bnd_disable_thread_calls(uint32_t base)
{
    for (int i = 0; i < g_bnd_disabled_n; i++)
        if (g_bnd_disabled[i] == base)
            return 0;
    if (g_bnd_disabled_n < 16)
        g_bnd_disabled[g_bnd_disabled_n++] = base;
    return 0;
}

/* FreeLibrary of a translated module: the host implementation records the calls; a test sets the answer */
uint32_t g_bnd_unload_base[16];
int g_bnd_unload_n;
int g_bnd_unload_outcome = FNVNX_BND_PINNED;     /* the outcome the next calls report (default: a static module, never unloaded) */
uint32_t g_bnd_unload_error;                     /* nonzero: the Win32 error the next calls report */
uint32_t fnvnx_bnd_module_unload(uint32_t base, int *outcome)
{
    enter_game("module_unload", 0);   /* FreeLibrary runs DllMain(DETACH) of the module */
    if (g_bnd_unload_n < 16)
        g_bnd_unload_base[g_bnd_unload_n] = base;
    g_bnd_unload_n++;
    *outcome = g_bnd_unload_outcome;
    return g_bnd_unload_error;
}

void fnvnx_bnd_process_detach(void)
{
    enter_game("process_detach", 0);   /* DllMain(DLL_PROCESS_DETACH) of the modules */
    __atomic_fetch_add(&g_bnd_process_detaches, 1, __ATOMIC_RELAXED);
    if (g_bnd_process_detach_hook)
        g_bnd_process_detach_hook();
}
}
