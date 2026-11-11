/* memtrack.cpp - the memory lane's recorder (D25 #8: allocation failures, memory over the Switch budget).
 * tools/perf/MEM.md.
 *
 * Off until fnvnx_mem_start / fnvnx_mem_autostart (tools/perf/mem_autostart.c). While on, the allocator stand-ins
 * report every allocation and free, by kind:
 *   heap     HeapAlloc / HeapReAlloc / HeapFree, LocalAlloc / LocalFree (memory.cpp), and, in a lane guest linked with
 *            tools/perf/mem_wrap.c, the C heap (malloc, calloc, realloc, free, memalign, aligned_alloc,
 *            posix_memalign) the game and the CRT stand-ins call directly;
 *   virtual  committed VirtualAlloc pages (memory.cpp: what os_vm_commit actually committed, released on decommit /
 *            release);
 *   gpu      D3D9 resources: textures, volume and cube textures, vertex and index buffers, render targets, depth
 *            stencils, offscreen surfaces, sized from their description (bytes per pixel by format, DXT blocks, the
 *            mip chain, cube faces, multisampling); freed when their Release returns 0. GetProcAddress hands out a
 *            Direct3DCreate9 that wraps the backend's (null device, Nine proxy, native renderer alike): the
 *            IDirect3D9's CreateDevice slot, the device's create slots and each resource's Release slot are
 *            replaced in per-class copies of the objects' vtables.
 * Recorded: live bytes per kind, the peak of their sum (and the split at that moment), failed allocations, and per
 * allocation site (kind, caller address): live, peak live, allocations, bytes, failures. A sampler thread prints the
 * live bytes every period. Lines, all "mem: ...", go to the sink (default os_debug_print):
 *   mem: t <ms> heap <b> virtual <b> gpu <b>
 *   mem: fail <kind> <bytes> caller 0x<pc> t <ms>
 *   mem: peak <b> heap <b> virtual <b> gpu <b> t <ms>
 *   mem: site <kind> 0x<pc> live <b> peak <b> allocs <n> bytes <b> fails <n>
 *   mem: summary heap <b> virtual <b> gpu <b> peak <b> fails <n> untracked <n> sites <n> period <ms>
 * fnvnx_mem_flush prints the last four (ExitProcess calls it; the sampler every FNVNX_MEM_DUMP_S seconds).
 * Callers are addresses only. Pointer map and site table are reserved from os_vm_* when recording starts (nothing in
 * .bss): an allocation the map cannot hold is counted (untracked) and its free is not seen. */
#include "w32.h"

#include <stdio.h>

volatile int w32_mem_on;

enum { K_HEAP = 0, K_VIRTUAL = 1, K_GPU = 2, NK = 3 };
static const char *const kind_name[NK] = {"heap", "virtual", "gpu"};

#define MAP_LOG2 20
#define NMAP (1u << MAP_LOG2)
#define NSITE 8192
#define NFAIL_PRINT 64
#ifndef FNVNX_MEM_DUMP_S
#define FNVNX_MEM_DUMP_S 30
#endif

struct ment { uintptr_t key; uint32_t size; uint16_t site; uint8_t kind, used; };
struct site { uint32_t caller; uint8_t kind, used; uint64_t live, peak, allocs, bytes, fails; };

static os_lock_t g_mem;
static struct ment *g_map;
static struct site *g_site;
static unsigned g_nsites;
static uint64_t g_live[NK], g_peak, g_peak_split[NK], g_peak_t, g_fails, g_untracked;
static uint64_t g_t0;
static unsigned g_period_ms = 1000;
static fnvnx_mem_sink g_sink;
static volatile int g_pending, g_ready;
static os_thread *g_sampler;
static volatile int g_run;

static void emit(const char *l) { (g_sink ? g_sink : os_debug_print)(l); }
static uint64_t ms_now(void) { return (clk_rt_ns() - g_t0) / 1000000ull; }

static void start_sampler(void);
/* tables from os_vm_* (not malloc: the recorder must not recurse into a wrapped malloc) */
static int ensure_tables(void)
{
    if (__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE))
        return 1;
    os_lock(&g_mem);
    if (!g_ready) {
        size_t mb = (size_t)NMAP * sizeof(struct ment), sb = (size_t)NSITE * sizeof(struct site);
        void *m = os_vm_reserve(NULL, mb + sb);
        if (m && !os_vm_commit(m, mb + sb)) {
            memset(m, 0, mb + sb);
            g_map = (struct ment *)m;
            g_site = (struct site *)((char *)m + mb);
            __atomic_store_n(&g_ready, 1, __ATOMIC_RELEASE);
        }
    }
    os_unlock(&g_mem);
    return g_ready;
}

static inline uint32_t hptr(uintptr_t k) { return (uint32_t)(((uint64_t)k * 0x9E3779B97F4A7C15ull) >> (64 - MAP_LOG2)); }

/* caller holds g_mem */
static struct site *site_of(int kind, uint32_t caller)
{
    uint32_t h = ((caller * 2654435761u) ^ (uint32_t)kind) % NSITE;
    for (unsigned n = 0; n < NSITE; n++, h = (h + 1) % NSITE) {
        struct site *s = &g_site[h];
        if (s->used && s->caller == caller && s->kind == kind)
            return s;
        if (!s->used) {
            if (g_nsites >= NSITE * 7 / 8)
                break;
            s->used = 1;
            s->caller = caller;
            s->kind = (uint8_t)kind;
            g_nsites++;
            return s;
        }
    }
    return NULL;   /* the site table is full: counted in the totals only */
}

static void add_live(int kind, struct site *s, int64_t d)
{
    g_live[kind] += (uint64_t)d;
    if (s) {
        s->live += (uint64_t)d;
        if (d > 0 && s->live > s->peak)
            s->peak = s->live;
    }
    if (d > 0) {
        uint64_t tot = g_live[0] + g_live[1] + g_live[2];
        if (tot > g_peak) {
            g_peak = tot;
            for (int k = 0; k < NK; k++)
                g_peak_split[k] = g_live[k];
            g_peak_t = ms_now();
        }
    }
}

static void maybe_start(void)
{
    if (__builtin_expect(g_pending, 0) && __atomic_exchange_n(&g_pending, 0, __ATOMIC_ACQ_REL))
        start_sampler();   /* outside g_mem */
}

extern "C" void fnvnx_mem_note(int kind, const void *p, size_t n, unsigned caller)
{
    if (!w32_mem_on || !p || kind < 0 || kind >= NK)
        return;
    maybe_start();
    if (!ensure_tables())
        return;
    os_lock(&g_mem);
    struct site *s = site_of(kind, caller);
    if (s) {
        s->allocs++;
        s->bytes += n;
    }
    uintptr_t k = (uintptr_t)p;
    uint32_t h = hptr(k);
    unsigned probe = 0;
    for (; probe < 256; probe++, h = (h + 1) & (NMAP - 1))
        if (!g_map[h].used || g_map[h].key == k)
            break;
    if (probe == 256) {
        g_untracked++;
    } else {
        struct ment *e = &g_map[h];
        if (e->used) {   /* a pointer seen twice without a free: the old size leaves first */
            struct site *o = e->site ? &g_site[e->site - 1] : NULL;
            add_live(e->kind, o, -(int64_t)e->size);
        }
        e->key = k;
        e->size = (uint32_t)(n > 0xFFFFFFFFu ? 0xFFFFFFFFu : n);
        e->site = s ? (uint16_t)(s - g_site + 1) : 0;
        e->kind = (uint8_t)kind;
        e->used = 1;
    }
    add_live(kind, s, (int64_t)n);
    os_unlock(&g_mem);
}

extern "C" void fnvnx_mem_forget(int kind, const void *p)
{
    if (!w32_mem_on || !p || !g_ready)
        return;
    os_lock(&g_mem);
    uintptr_t k = (uintptr_t)p;
    uint32_t h = hptr(k);
    for (unsigned probe = 0; probe < 256 && g_map[h].used; probe++, h = (h + 1) & (NMAP - 1)) {
        if (g_map[h].key != k || (kind >= 0 && g_map[h].kind != kind))
            continue;
        struct ment *e = &g_map[h];
        add_live(e->kind, e->site ? &g_site[e->site - 1] : NULL, -(int64_t)e->size);
        /* backward-shift deletion (linear probing, no tombstones) */
        uint32_t i = h;
        for (uint32_t j = (i + 1) & (NMAP - 1); g_map[j].used; j = (j + 1) & (NMAP - 1)) {
            uint32_t home = hptr(g_map[j].key);
            if (((j - home) & (NMAP - 1)) >= ((j - i) & (NMAP - 1))) {
                g_map[i] = g_map[j];
                i = j;
            }
        }
        g_map[i].used = 0;
        break;
    }
    os_unlock(&g_mem);
}

/* committed bytes without a pointer of their own (memory.cpp's VirtualAlloc commits, per region's site) */
extern "C" void fnvnx_mem_delta(int kind, long long d, unsigned caller)
{
    if (!w32_mem_on || !d || kind < 0 || kind >= NK)
        return;
    maybe_start();
    if (!ensure_tables())
        return;
    os_lock(&g_mem);
    struct site *s = site_of(kind, caller);
    if (s && d > 0) {
        s->allocs++;
        s->bytes += (uint64_t)d;
    }
    add_live(kind, s, d);
    os_unlock(&g_mem);
}

extern "C" void fnvnx_mem_fail(int kind, size_t n, unsigned caller)
{
    if (!w32_mem_on || kind < 0 || kind >= NK)
        return;
    if (!ensure_tables())
        return;
    os_lock(&g_mem);
    struct site *s = site_of(kind, caller);
    if (s)
        s->fails++;
    uint64_t nf = ++g_fails;
    os_unlock(&g_mem);
    if (nf <= NFAIL_PRINT) {
        char l[160];
        snprintf(l, sizeof l, "mem: fail %s %llu caller 0x%08x t %llu", kind_name[kind], (unsigned long long)n,
                 caller, (unsigned long long)ms_now());
        emit(l);
    }
}

/* the caller hand-off: a stand-in that allocates through malloc names its own caller for the wrapped malloc
 * (tools/perf/mem_wrap.c), and marks the call as recorded already ("inside") */
extern "C" int fnvnx_mem_enter(unsigned caller)
{
    if (!w32_mem_on)
        return 0;
    struct w32_thread *t = w32_self();
    t->mem_inside++;
    if (!t->mem_caller)
        t->mem_caller = caller;
    return 1;
}
extern "C" void fnvnx_mem_leave(int entered)
{
    if (!entered)
        return;
    struct w32_thread *t = w32_self();
    if (--t->mem_inside <= 0) {
        t->mem_inside = 0;
        t->mem_caller = 0;
    }
}
extern "C" int fnvnx_mem_inside(void) { return w32_mem_on && w32_self()->mem_inside > 0; }
extern "C" unsigned fnvnx_mem_caller(unsigned fallback)
{
    unsigned c = w32_mem_on ? w32_self()->mem_caller : 0;
    return c ? c : fallback;
}

/* ---- output */
static void line_t(void)
{
    char l[160];
    os_lock(&g_mem);
    uint64_t h = g_live[0], v = g_live[1], g = g_live[2];
    os_unlock(&g_mem);
    snprintf(l, sizeof l, "mem: t %llu heap %llu virtual %llu gpu %llu", (unsigned long long)ms_now(),
             (unsigned long long)h, (unsigned long long)v, (unsigned long long)g);
    emit(l);
}

extern "C" void fnvnx_mem_flush(void)
{
    if (!w32_mem_on || !g_ready)
        return;
    char l[200];
    line_t();
    os_lock(&g_mem);
    uint64_t pk = g_peak, ps[NK] = {g_peak_split[0], g_peak_split[1], g_peak_split[2]}, pt = g_peak_t;
    uint64_t live[NK] = {g_live[0], g_live[1], g_live[2]}, fails = g_fails, untr = g_untracked;
    unsigned nsites = g_nsites;
    os_unlock(&g_mem);
    snprintf(l, sizeof l, "mem: peak %llu heap %llu virtual %llu gpu %llu t %llu", (unsigned long long)pk,
             (unsigned long long)ps[0], (unsigned long long)ps[1], (unsigned long long)ps[2], (unsigned long long)pt);
    emit(l);
    /* the 64 sites with the highest peak, then every site with a failure */
    static int pick[NSITE];
    int np = 0;
    os_lock(&g_mem);
    for (int i = 0; i < NSITE; i++)
        if (g_site[i].used)
            pick[np++] = i;
    for (int a = 1; a < np; a++)   /* insertion sort by peak, descending (site counts are small) */
        for (int b = a; b > 0 && g_site[pick[b]].peak > g_site[pick[b - 1]].peak; b--) {
            int x = pick[b]; pick[b] = pick[b - 1]; pick[b - 1] = x;
        }
    struct site out[96];
    int no = 0;
    for (int i = 0; i < np && no < 96; i++)
        if (i < 64 || g_site[pick[i]].fails)
            out[no++] = g_site[pick[i]];
    os_unlock(&g_mem);
    for (int i = 0; i < no; i++) {
        snprintf(l, sizeof l, "mem: site %s 0x%08x live %llu peak %llu allocs %llu bytes %llu fails %llu",
                 kind_name[out[i].kind], out[i].caller, (unsigned long long)out[i].live,
                 (unsigned long long)out[i].peak, (unsigned long long)out[i].allocs,
                 (unsigned long long)out[i].bytes, (unsigned long long)out[i].fails);
        emit(l);
    }
    snprintf(l, sizeof l, "mem: summary heap %llu virtual %llu gpu %llu peak %llu fails %llu untracked %llu sites %u "
             "period %u", (unsigned long long)live[0], (unsigned long long)live[1], (unsigned long long)live[2],
             (unsigned long long)pk, (unsigned long long)fails, (unsigned long long)untr, nsites, g_period_ms);
    emit(l);
}

extern "C" void fnvnx_mem_counts(unsigned long long live[3], unsigned long long *peak, unsigned long long *fails)
{
    os_lock(&g_mem);
    for (int k = 0; k < NK; k++)
        if (live)
            live[k] = g_live[k];
    if (peak) *peak = g_peak;
    if (fails) *fails = g_fails;
    os_unlock(&g_mem);
}

static void sampler(void *arg)
{
    W32_UNUSED(arg);
    uint64_t next_dump = clk_rt_ns() + FNVNX_MEM_DUMP_S * 1000000000ull;
    while (__atomic_load_n(&g_run, __ATOMIC_ACQUIRE)) {
        for (unsigned left = g_period_ms; left && __atomic_load_n(&g_run, __ATOMIC_ACQUIRE);) {
            unsigned step = left > 50 ? 50 : left;
            clk_rt_sleep_ns((uint64_t)step * 1000000ull);
            left -= step;
        }
        if (!__atomic_load_n(&g_run, __ATOMIC_ACQUIRE))
            break;
        line_t();
        if (g_sink == NULL && clk_rt_ns() >= next_dump) {
            fnvnx_mem_flush();
            next_dump = clk_rt_ns() + FNVNX_MEM_DUMP_S * 1000000000ull;
        }
    }
}

static void start_sampler(void)
{
    if (g_sampler || !g_period_ms)
        return;
    g_run = 1;
    g_sampler = os_thread_create(sampler, NULL, 64 * 1024, OS_PRIO_NORMAL, -1);
    if (g_sampler && os_thread_start(g_sampler)) {
        os_thread_free(g_sampler);
        g_sampler = NULL;
    }
}

extern "C" void fnvnx_mem_autostart(unsigned period_ms)   /* from a constructor: no thread, no allocation */
{
    g_t0 = clk_rt_ns();
    g_period_ms = period_ms ? period_ms : 1000;
    g_pending = 1;
    __atomic_store_n(&w32_mem_on, 1, __ATOMIC_RELEASE);
}

extern "C" void fnvnx_mem_start(unsigned period_ms, fnvnx_mem_sink sink)
{
    g_sink = sink;
    if (!g_t0)
        g_t0 = clk_rt_ns();
    g_period_ms = period_ms;
    g_pending = 0;
    ensure_tables();
    __atomic_store_n(&w32_mem_on, 1, __ATOMIC_RELEASE);
    start_sampler();
}

extern "C" void fnvnx_mem_stop(void)   /* stops the sampler and the recording (the counts stay) */
{
    __atomic_store_n(&w32_mem_on, 0, __ATOMIC_RELEASE);
    if (!g_sampler)
        return;
    __atomic_store_n(&g_run, 0, __ATOMIC_RELEASE);
    os_thread_join(g_sampler);
    os_thread_free(g_sampler);
    g_sampler = NULL;
}

/* ---- D3D9 resources: by vtable slot (shim/include/d3d9.h's numbering; COM: no destructor slots) */
typedef long HR;
enum { SLOT_RELEASE = 2, SLOT_CREATEDEVICE = 16, SLOT_TEX = 23, SLOT_VOLTEX = 24, SLOT_CUBETEX = 25, SLOT_VB = 26,
       SLOT_IB = 27, SLOT_RT = 28, SLOT_DS = 29, SLOT_OFFSCREEN = 36, VT_COPY = 128 };

/* bits per pixel of a D3DFORMAT; 0: block-compressed (DXT: handled by the caller) or unknown -> 32 */
static unsigned fmt_bits(uint32_t f, int *dxt)
{
    *dxt = 0;
    switch (f) {
    case 0x31545844u: *dxt = 8; return 0;                 /* DXT1: 8 bytes per 4x4 block */
    case 0x32545844u: case 0x33545844u: case 0x34545844u: case 0x35545844u: case 0x31495441u: case 0x32495441u:
        *dxt = 16; return 0;                               /* DXT2-5, ATI1/ATI2 (BC4/BC5: ATI1 is 8) */
    case 20: return 24;
    case 21: case 22: case 31: case 32: case 33: case 34: case 35: case 62: case 63: case 64: case 67: case 71:
    case 75: case 77: case 79: case 82: case 83: case 84: case 112: case 114: case 102: return 32;
    case 23: case 24: case 25: case 26: case 30: case 51: case 60: case 61: case 70: case 73: case 80: case 81:
    case 111: case 101: return 16;
    case 27: case 28: case 41: case 50: case 52: case 85: return 8;
    case 29: case 40: return 16;
    case 36: case 110: case 113: case 115: return 64;
    case 116: return 128;
    case 0x5A544E49u: return 32;                           /* INTZ */
    case 0x4C4C554Eu: return 0;                            /* NULL render target: no memory */
    default: return 32;
    }
}

static uint64_t surface_bytes(uint32_t w, uint32_t h, uint32_t d, uint32_t f)
{
    int dxt;
    unsigned bits = fmt_bits(f, &dxt);
    if (f == 0x31495441u)
        dxt = 8;
    if (dxt)
        return (uint64_t)((w + 3) / 4) * ((h + 3) / 4) * d * (uint64_t)dxt;
    return (uint64_t)w * h * d * bits / 8;
}

static uint64_t chain_bytes(uint32_t w, uint32_t h, uint32_t d, uint32_t levels, uint32_t f)
{
    uint64_t sum = 0;
    for (uint32_t l = 0; (levels == 0 || l < levels) && l < 16; l++) {
        sum += surface_bytes(w ? w : 1, h ? h : 1, d ? d : 1, f);
        if (w <= 1 && h <= 1 && d <= 1)
            break;
        w = w > 1 ? w / 2 : 1;
        h = h > 1 ? h / 2 : 1;
        d = d > 1 ? d / 2 : 1;
    }
    return sum;
}

/* per-class vtable copies: original vtable -> copy with Release (and, for the factory / the device, the create
 * slots) replaced; the original slots are kept beside the copy */
struct vtc { void **orig; void **copy; };
static struct vtc g_vt[64];
static int g_nvt;
static os_lock_t g_vt_lock;

static void **vt_orig_of(void *obj)
{
    void **vt = *(void ***)obj;
    for (int i = 0; i < g_nvt; i++)
        if (g_vt[i].copy == vt)
            return g_vt[i].orig;
    return vt;
}

static void hook_obj(void *obj, int nslots, const int *slots, void *const *fns)
{
    void **vt = *(void ***)obj;
    void **copy = NULL;
    os_lock(&g_vt_lock);
    for (int i = 0; i < g_nvt && !copy; i++)
        if (g_vt[i].orig == vt || g_vt[i].copy == vt)
            copy = g_vt[i].copy;
    if (!copy && g_nvt < 64) {
        copy = (void **)os_vm_reserve(NULL, 0x10000);
        if (copy && os_vm_commit(copy, 0x10000))
            copy = NULL;
        if (copy) {
            memcpy(copy, vt, VT_COPY * sizeof(void *));   /* reads past a short vtable stay inside .rodata */
            for (int s = 0; s < nslots; s++)
                copy[slots[s]] = fns[s];
            g_vt[g_nvt].orig = vt;
            g_vt[g_nvt].copy = copy;
            g_nvt++;
        }
    }
    os_unlock(&g_vt_lock);
    if (copy)
        *(void ***)obj = copy;
}

typedef unsigned long (*release_fn)(void *);
static unsigned long res_release(void *self)
{
    release_fn f = (release_fn)vt_orig_of(self)[SLOT_RELEASE];
    unsigned long r = f(self);
    if (r == 0)
        fnvnx_mem_forget(K_GPU, self);
    return r;
}

static void track_res(void *res, uint64_t bytes, unsigned caller)
{
    if (!res)
        return;
    static const int slots[] = {SLOT_RELEASE};
    void *const fns[] = {(void *)res_release};
    hook_obj(res, 1, slots, fns);
    fnvnx_mem_note(K_GPU, res, (size_t)bytes, caller);
}

#define RA ((unsigned)(uintptr_t)__builtin_return_address(0))
#define ORIG(slot, T) ((T)vt_orig_of(dev)[slot])
static void fail_or_track(HR hr, void **pp, uint64_t bytes, unsigned caller)
{
    if (hr < 0)
        fnvnx_mem_fail(K_GPU, (size_t)bytes, caller);
    else if (pp)
        track_res(*pp, bytes, caller);
}
typedef HR (*tex_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
static HR d_tex(void *dev, uint32_t w, uint32_t h, uint32_t lv, uint32_t use, uint32_t f, uint32_t pool, void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_TEX, tex_fn)(dev, w, h, lv, use, f, pool, pp, sh);
    fail_or_track(hr, pp, chain_bytes(w, h, 1, lv, f), c);
    return hr;
}
typedef HR (*vol_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
static HR d_vol(void *dev, uint32_t w, uint32_t h, uint32_t d, uint32_t lv, uint32_t use, uint32_t f, uint32_t pool,
                void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_VOLTEX, vol_fn)(dev, w, h, d, lv, use, f, pool, pp, sh);
    fail_or_track(hr, pp, chain_bytes(w, h, d, lv, f), c);
    return hr;
}
typedef HR (*cube_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
static HR d_cube(void *dev, uint32_t e, uint32_t lv, uint32_t use, uint32_t f, uint32_t pool, void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_CUBETEX, cube_fn)(dev, e, lv, use, f, pool, pp, sh);
    fail_or_track(hr, pp, 6 * chain_bytes(e, e, 1, lv, f), c);
    return hr;
}
typedef HR (*buf_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
static HR d_vb(void *dev, uint32_t len, uint32_t use, uint32_t fvf, uint32_t pool, void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_VB, buf_fn)(dev, len, use, fvf, pool, pp, sh);
    fail_or_track(hr, pp, len, c);
    return hr;
}
static HR d_ib(void *dev, uint32_t len, uint32_t use, uint32_t f, uint32_t pool, void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_IB, buf_fn)(dev, len, use, f, pool, pp, sh);
    fail_or_track(hr, pp, len, c);
    return hr;
}
typedef HR (*rt_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, int, void **, void *);
static HR d_rt(void *dev, uint32_t w, uint32_t h, uint32_t f, uint32_t ms, uint32_t q, int lock, void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_RT, rt_fn)(dev, w, h, f, ms, q, lock, pp, sh);
    fail_or_track(hr, pp, surface_bytes(w, h, 1, f) * (ms > 1 ? ms : 1), c);
    return hr;
}
static HR d_ds(void *dev, uint32_t w, uint32_t h, uint32_t f, uint32_t ms, uint32_t q, int disc, void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_DS, rt_fn)(dev, w, h, f, ms, q, disc, pp, sh);
    fail_or_track(hr, pp, surface_bytes(w, h, 1, f) * (ms > 1 ? ms : 1), c);
    return hr;
}
typedef HR (*off_fn)(void *, uint32_t, uint32_t, uint32_t, uint32_t, void **, void *);
static HR d_off(void *dev, uint32_t w, uint32_t h, uint32_t f, uint32_t pool, void **pp, void *sh)
{
    unsigned c = RA;
    HR hr = ORIG(SLOT_OFFSCREEN, off_fn)(dev, w, h, f, pool, pp, sh);
    fail_or_track(hr, pp, surface_bytes(w, h, 1, f), c);
    return hr;
}
#undef ORIG

typedef HR (*cdev_fn)(void *, uint32_t, uint32_t, void *, uint32_t, void *, void **);
static HR f_create_device(void *d3d, uint32_t ad, uint32_t type, void *wnd, uint32_t fl, void *pp_, void **ppdev)
{
    HR hr = ((cdev_fn)vt_orig_of(d3d)[SLOT_CREATEDEVICE])(d3d, ad, type, wnd, fl, pp_, ppdev);
    if (hr >= 0 && ppdev && *ppdev) {
        static const int slots[] = {SLOT_TEX, SLOT_VOLTEX, SLOT_CUBETEX, SLOT_VB, SLOT_IB, SLOT_RT, SLOT_DS, SLOT_OFFSCREEN};
        void *const fns[] = {(void *)d_tex, (void *)d_vol, (void *)d_cube, (void *)d_vb, (void *)d_ib, (void *)d_rt,
                             (void *)d_ds, (void *)d_off};
        hook_obj(*ppdev, 8, slots, fns);
    }
    return hr;
}

typedef void *(*create9_fn)(unsigned);
static create9_fn g_create9;
static void *mem_direct3dcreate9(unsigned sdk)
{
    void *d3d = g_create9 ? g_create9(sdk) : NULL;
    if (d3d) {
        static const int slots[] = {SLOT_CREATEDEVICE};
        void *const fns[] = {(void *)f_create_device};
        hook_obj(d3d, 1, slots, fns);
    }
    return d3d;
}

/* module.cpp's GetProcAddress: the backend's Direct3DCreate9 behind the recorder's, while recording */
FARPROC w32_mem_hook_proc(const char *name, FARPROC f)
{
    if (!w32_mem_on || !f || strcmp(name, "Direct3DCreate9"))
        return f;
    g_create9 = (create9_fn)(void *)f;
    return (FARPROC)(void *)mem_direct3dcreate9;
}
