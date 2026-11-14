/* platstat.cpp - the platform lane's recorder (D25 #3, #4, #6; tools/perf/PLAT.md).
 *
 * Off until fnvnx_plat_start / fnvnx_plat_autostart (tools/perf/plat_autostart.c). While on, the stand-ins report:
 *   unimpl   a call to an entry whose function is missing (fnvnx_plat_note(FNVNX_NOTE_UNIMPL, ...): an unsupported TEB offset, a Wine 9.0 simplified body (fnvnx_wine_fixme), ...); an import with no export is the boot's own line
 *            ("fnvnx boot: import <dll>!<name>: no export") and its call a trap (tools/perf/plat.py reads both)
 *   open     a failed file open (CreateFileA, the CRT's fopen / _open): the path's class (bsa, esm, save, ini,
 *            other), the Win32 error, whether a case-insensitive scan of the parent directory finds the name
 *            ("ci yes": the path mapper missed a spelling; "ci no"; "ci nodir": the directory itself is missing),
 *            and a short hash of the Windows path (to tell entries apart; no path is printed)
 *   decode   an audio decode failure (Vorbis open, the MP3 stream of DirectShow)
 *   input    an input the mapping does not handle (a HID keyboard usage without a DirectInput code)
 * Each distinct (class, name, caller, detail) is counted. Lines, all "plat: ...", go to the sink (default
 * os_debug_print): the first of each distinct entry at once (the first 256), and at fnvnx_plat_flush (ExitProcess,
 * and every 30 s from the flusher):
 *   plat: <class> <name> caller 0x<pc>[ <detail>]
 *   plat: count <class> <name> caller 0x<pc> n <count>[ <detail>]
 *   plat: summary unimpl <distinct> <calls> open <d> <c> decode <d> <c> input <d> <c> dropped <n>
 * Names are function names of our own stand-ins (Class::method), callers addresses only. */
#include "w32.h"
#include <fnvnx/declared.h>
#include <fnvnx/reach.h>

#include <stdio.h>

volatile int fnvnx_plat_on;

#define NPLAT 2048
#define NPRINT 256
static const char *const cls_name[FNVNX_NOTE_NCLASS] = {"unimpl", "open", "decode", "input"};
struct pent { const char *name; unsigned caller; uint8_t cls, used; char detail[64]; uint64_t n; };
static struct pent g_e[NPLAT];
static unsigned g_ne, g_dropped, g_printed;
static uint64_t g_calls[FNVNX_NOTE_NCLASS], g_distinct[FNVNX_NOTE_NCLASS];
static os_lock_t g_plat;
static fnvnx_plat_sink g_sink;
static volatile int g_pending;
static os_thread *g_flusher;
static volatile int g_run;

static void emit(const char *l) { (g_sink ? g_sink : os_debug_print)(l); }

/* "virtual HRESULT Graph::EnumFilters(void**)" -> "Graph::EnumFilters" */
static void short_name(const char *pretty, char *out, size_t n)
{
    const char *paren = strchr(pretty, '(');
    const char *end = paren ? paren : pretty + strlen(pretty);
    const char *start = end;
    int depth = 0;
    while (start > pretty) {   /* back to the space before the qualified name (not inside a template argument) */
        char c = start[-1];
        if (c == '>') depth++;
        else if (c == '<') depth--;
        else if ((c == ' ' || c == '*' || c == '&') && depth == 0) break;
        start--;
    }
    size_t len = (size_t)(end - start);
    if (len >= n) len = n - 1;
    memcpy(out, start, len);
    out[len] = 0;
    if (!*out) snprintf(out, n, "?");
}

static uint32_t hstr(const char *s)
{
    uint32_t h = 2166136261u;
    for (; s && *s; s++) h = (h ^ (uint8_t)*s) * 16777619u;
    return h;
}

static void start_flusher(void);

/* The log line of a body that does what Wine 9.0's simplified implementation does (verify/VERDICT.md "Bodies that do what
 * Wine 9.0's simplified implementation does", verify/wine_fixme.tsv): one line at every call, as Wine's FIXME, whether the plat
 * lane is on or not; while it is on the call is also counted as an "unimpl" entry named wine-fixme with the id. */
extern "C" void fnvnx_wine_fixme(uint32_t id)
{
    char l[48];
    snprintf(l, sizeof l, "fixme: wine-9.0 id %u", (unsigned)id);
    emit(l);
    char d[24];
    snprintf(d, sizeof d, "id %u", (unsigned)id);
    fnvnx_plat_note(FNVNX_NOTE_UNIMPL, "wine-fixme", FNVNX_NOTE_RA, d);
}

/* The citation function of the declared-machine bodies (verify/VERDICT.md "Declared-machine bodies", verify/declared_machine.tsv): a body that
 * answers from the declared machine (no network, fixed time zone, registry values, no Galaxy) calls it with the id of its row. It writes nothing
 * and returns: the gate reads the call, the body's result is the row's value. */
extern "C" void fnvnx_declared_machine(uint32_t id)
{
    (void)id;
}

/* The one exit of the branches the reachability gate proves unreachable (contract/include/fnvnx/reach.h has the contract). Reached only
 * when a build invariant is violated: one log line through the same sink as the other lines, then the process ends through the
 * host's exit (os_exit_process: the hostapi `exit` slot on the console) with FNVNX_REACH_EXIT | fact_id. */
extern "C" void fnvnx_reach_violation(uint32_t fact_id)
{
    char l[48];
    snprintf(l, sizeof l, "reach violation fact %u", (unsigned)fact_id);
    emit(l);
    os_exit_process((int)(FNVNX_REACH_EXIT | fact_id));
}

/* The one assertion of an OWNER assumption (ASM-001..009, reach.h): the line carries the id and the value, the exit code the id and the low 16 bits. */
extern "C" void fnvnx_assume_violation(uint32_t asm_id, uint64_t value)
{
    char l[64];
    snprintf(l, sizeof l, "assume violation ASM-%03u value %llu", (unsigned)asm_id, (unsigned long long)value);
    emit(l);
    os_exit_process((int)(FNVNX_ASSUME_EXIT | (asm_id & 0xFFu) << 16 | (uint32_t)(value & 0xFFFFu)));
}

extern "C" void fnvnx_plat_note(int cls, const char *name, unsigned caller, const char *detail)
{
    if (!fnvnx_plat_on || cls < 0 || cls >= FNVNX_NOTE_NCLASS || !name)
        return;
    if (__builtin_expect(g_pending, 0) && __atomic_exchange_n(&g_pending, 0, __ATOMIC_ACQ_REL))
        start_flusher();
    char d[64];
    snprintf(d, sizeof d, "%s", detail ? detail : "");
    uint32_t h = ((hstr(name) ^ (caller * 2654435761u) ^ hstr(d)) * 31u + (uint32_t)cls) % NPLAT;
    int first = 0;
    struct pent copy;
    os_lock(&g_plat);
    g_calls[cls]++;
    struct pent *e = NULL;
    for (unsigned k = 0; k < NPLAT; k++, h = (h + 1) % NPLAT) {
        struct pent *x = &g_e[h];
        if (x->used && x->cls == cls && x->caller == caller && (x->name == name || !strcmp(x->name, name)) &&
            !strcmp(x->detail, d)) {
            e = x;
            break;
        }
        if (!x->used) {
            if (g_ne >= NPLAT * 7 / 8)
                break;
            x->used = 1;
            x->cls = (uint8_t)cls;
            x->name = name;
            x->caller = caller;
            memcpy(x->detail, d, sizeof d);
            g_ne++;
            g_distinct[cls]++;
            e = x;
            first = g_printed < NPRINT;
            if (first)
                g_printed++;
            break;
        }
    }
    if (e) {
        e->n++;
        copy = *e;
    } else {
        g_dropped++;
    }
    os_unlock(&g_plat);
    if (first) {
        char nm[96], l[256];
        short_name(copy.name, nm, sizeof nm);
        snprintf(l, sizeof l, "plat: %s %s caller 0x%08x%s%s", cls_name[cls], nm, copy.caller, *copy.detail ? " " : "",
                 copy.detail);
        emit(l);
    }
}

/* the open-failure detail: class of the Windows path, the error, the case-insensitive scan, the path hash */
static const char *path_class(const char *win)
{
    char low[W32_PATH_MAX];
    size_t n = 0;
    for (; win[n] && n + 1 < sizeof low; n++)
        low[n] = (char)(win[n] >= 'A' && win[n] <= 'Z' ? win[n] + 32 : (win[n] == '/' ? '\\' : win[n]));
    low[n] = 0;
    const char *dot = strrchr(low, '.');
    if (dot && !strcmp(dot, ".bsa")) return "bsa";
    if (dot && (!strcmp(dot, ".esm") || !strcmp(dot, ".esp"))) return "esm";
    if ((dot && (!strcmp(dot, ".fos") || !strcmp(dot, ".bak") || !strcmp(dot, ".nvse"))) || strstr(low, "\\saves\\"))
        return "save";
    if (dot && !strcmp(dot, ".ini")) return "ini";
    return "other";
}

static const char *ci_scan(const char *host)
{
    if (!host || !*host) return "no";
    char dir[W32_PATH_MAX];
    snprintf(dir, sizeof dir, "%s", host);
    char *s = strrchr(dir, '/');
    const char *base = s ? s + 1 : dir;
    if (s) *s = 0;
    os_dir *dh = os_dir_open(s ? (s == dir ? "/" : dir) : ".");
    if (!dh) return "nodir";
    const char *hit = "no";
    for (const char *e; (e = os_dir_next(dh));)
        if (!w32_stricmp(e, base)) { hit = "yes"; break; }
    os_dir_close(dh);
    return hit;
}

extern "C" void fnvnx_plat_open_failed(const char *win, const char *host, unsigned err, unsigned caller,
                                       const char *api)
{
    if (!fnvnx_plat_on || !win)
        return;
    char d[64];
    snprintf(d, sizeof d, "%s err %u ci %s path %08x", path_class(win), err, ci_scan(host), hstr(win));
    fnvnx_plat_note(FNVNX_NOTE_OPEN, api, caller, d);
}

extern "C" void fnvnx_plat_flush(void)
{
    if (!fnvnx_plat_on)
        return;
    static struct pent snap[NPLAT];
    unsigned n = 0;
    uint64_t calls[FNVNX_NOTE_NCLASS], dist[FNVNX_NOTE_NCLASS];
    unsigned dropped;
    os_lock(&g_plat);
    for (unsigned i = 0; i < NPLAT; i++)
        if (g_e[i].used)
            snap[n++] = g_e[i];
    memcpy(calls, g_calls, sizeof calls);
    memcpy(dist, g_distinct, sizeof dist);
    dropped = g_dropped;
    os_unlock(&g_plat);
    char nm[96], l[256];
    for (unsigned i = 0; i < n; i++) {
        short_name(snap[i].name, nm, sizeof nm);
        snprintf(l, sizeof l, "plat: count %s %s caller 0x%08x n %llu%s%s", cls_name[snap[i].cls], nm, snap[i].caller,
                 (unsigned long long)snap[i].n, *snap[i].detail ? " " : "", snap[i].detail);
        emit(l);
    }
    int o = snprintf(l, sizeof l, "plat: summary");
    for (int c = 0; c < FNVNX_NOTE_NCLASS; c++)
        o += snprintf(l + o, sizeof l - (size_t)o, " %s %llu %llu", cls_name[c], (unsigned long long)dist[c],
                      (unsigned long long)calls[c]);
    snprintf(l + o, sizeof l - (size_t)o, " dropped %u", dropped);
    emit(l);
}

extern "C" void fnvnx_plat_counts(unsigned long long distinct[FNVNX_NOTE_NCLASS],
                                  unsigned long long calls[FNVNX_NOTE_NCLASS])
{
    os_lock(&g_plat);
    for (int c = 0; c < FNVNX_NOTE_NCLASS; c++) {
        if (distinct) distinct[c] = g_distinct[c];
        if (calls) calls[c] = g_calls[c];
    }
    os_unlock(&g_plat);
}

static void flusher(void *arg)
{
    W32_UNUSED(arg);
    while (__atomic_load_n(&g_run, __ATOMIC_ACQUIRE)) {
        for (unsigned left = 30000; left && __atomic_load_n(&g_run, __ATOMIC_ACQUIRE); left -= 50)
            clk_rt_sleep_ns(50000000ull);
        if (__atomic_load_n(&g_run, __ATOMIC_ACQUIRE))
            fnvnx_plat_flush();
    }
}

static void start_flusher(void)
{
    if (g_flusher || g_sink)
        return;
    g_run = 1;
    g_flusher = os_thread_create(flusher, NULL, 64 * 1024, OS_PRIO_NORMAL, -1);
    if (g_flusher && os_thread_start(g_flusher)) {
        os_thread_free(g_flusher);
        g_flusher = NULL;
    }
}

extern "C" void fnvnx_plat_autostart(void)   /* from a constructor: the flusher starts at the first note */
{
    g_pending = 1;
    __atomic_store_n(&fnvnx_plat_on, 1, __ATOMIC_RELEASE);
}

extern "C" void fnvnx_plat_start(fnvnx_plat_sink sink)
{
    g_sink = sink;
    g_pending = 0;
    __atomic_store_n(&fnvnx_plat_on, 1, __ATOMIC_RELEASE);
}

extern "C" void fnvnx_plat_stop(void)
{
    __atomic_store_n(&fnvnx_plat_on, 0, __ATOMIC_RELEASE);
    if (!g_flusher)
        return;
    __atomic_store_n(&g_run, 0, __ATOMIC_RELEASE);
    os_thread_join(g_flusher);
    os_thread_free(g_flusher);
    g_flusher = NULL;
}
