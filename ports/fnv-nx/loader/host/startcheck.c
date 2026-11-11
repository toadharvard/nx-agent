/* startcheck: tests of the start sequence (rules S1..S8) on the host, x86-64, sanitizers on.
 *
 *   startcheck synth.pe synth_start.h-matching-image [--exe FalloutNV.exe]
 *
 * The test provides the OS hooks over an mmap-reserved window with a bias (a page table records every mapping and
 * protection the loader asked for), a resolver, and its OWN fnvnx_lift_enter which records the calls. Expected
 * contents come from an independent reading of the file (headers, section table, import table) with the Windows
 * rule: file bytes then zeros. The real exe is private: only its path is passed; only aggregate numbers are printed. */
#define _DEFAULT_SOURCE
#include "fnvnx_start.h"
#include "fnvnx_loader.h"
#include "synth_start.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <fcntl.h>

static int n_ok, n_bad;
#define CHECK(c, ...) do { if (c) n_ok++; else { n_bad++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

#define WINDOW 0x20000000u
#define NPAGES (WINDOW / 4096u)
#define ALLOC_BASE 0x10000000u

/* ---- the platform ---- */
static uint8_t *g_file; static uint32_t g_file_len;
static uint8_t *g_res; static uintptr_t g_bias;
static struct { uint8_t mapped; uint8_t prot; } *g_pt;
static int g_saw_x, g_fail_alloc, g_alloc_calls, g_map_calls;
static uint32_t g_next_va;
static char g_log[64][160]; static unsigned g_nlog;
static fnvnx_proc *g_proc;

static int h_read(void *c, uint32_t off, void *dst, uint32_t size) {
    (void)c;
    if ((uint64_t)off + size > g_file_len) return -1;
    memcpy(dst, g_file + off, size);
    return 0;
}
static int to_prot(unsigned p) { return (p & FNVNX_PROT_R ? PROT_READ : 0) | (p & FNVNX_PROT_W ? PROT_WRITE : 0); }
static int h_map(void *c, uintptr_t addr, uint32_t size, unsigned prot) {
    uintptr_t lo = (uintptr_t)g_res;
    uint32_t i, p0;
    (void)c; g_map_calls++;
    if ((addr & 0xfff) || (size & 0xfff) || addr < lo || addr - lo + size > WINDOW) return -1;
    p0 = (uint32_t)((addr - lo) >> 12);
    for (i = 0; i < size >> 12; i++) if (g_pt[p0 + i].mapped) return -1;
    if (mmap((void *)addr, size, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS, -1, 0) == MAP_FAILED) return -1;
    if (prot & FNVNX_PROT_X) g_saw_x = 1;
    for (i = 0; i < size >> 12; i++) { g_pt[p0 + i].mapped = 1; g_pt[p0 + i].prot = (uint8_t)(FNVNX_PROT_R | FNVNX_PROT_W); }
    return 0;
}
static int h_prot(void *c, uintptr_t addr, uint32_t size, unsigned prot) {
    uint32_t i, p0 = (uint32_t)((addr - (uintptr_t)g_res) >> 12);
    (void)c;
    if (prot & FNVNX_PROT_X) g_saw_x = 1;
    for (i = 0; i < size >> 12; i++) if (!g_pt[p0 + i].mapped) return -1;
    if (mprotect((void *)addr, size, to_prot(prot))) return -1;
    for (i = 0; i < size >> 12; i++) g_pt[p0 + i].prot = (uint8_t)prot;
    return 0;
}
static void *h_alloc(void *c, uint32_t s) { (void)c; return malloc(s ? s : 1); }
static void h_free(void *c, void *p) { (void)c; free(p); }
static void h_log(void *c, const char *m) { (void)c; if (g_nlog < 64) snprintf(g_log[g_nlog++], sizeof g_log[0], "%s", m); }
/* freed ranges are handed out again (exact size match) so that create -> free -> create reuses addresses */
static struct { uint32_t va, size; } g_freed[8]; static unsigned g_nfreed, g_free_calls;
static uint32_t h_alloc_va(void *c, uint32_t size, unsigned prot) {
    uint32_t va = g_next_va;
    unsigned k;
    (void)prot; g_alloc_calls++;
    if (g_fail_alloc && g_alloc_calls >= g_fail_alloc) return 0;
    for (k = 0; k < g_nfreed; k++)
        if (g_freed[k].size == size) {
            va = g_freed[k].va; g_freed[k] = g_freed[--g_nfreed];
            return h_map(c, (uintptr_t)va + g_bias, size, FNVNX_PROT_R | FNVNX_PROT_W) ? 0 : va;
        }
    if (h_map(c, (uintptr_t)va + g_bias, size, FNVNX_PROT_R | FNVNX_PROT_W)) return 0;
    g_next_va += size + 0x1000;   /* a hole after every allocation */
    return va;
}
static int h_free_va(void *c, uint32_t va, uint32_t size) {
    uint32_t i, p0 = (uint32_t)((((uintptr_t)va + g_bias) - (uintptr_t)g_res) >> 12);
    (void)c; g_free_calls++;
    if ((va & 0xfff) || (size & 0xfff) || g_nfreed == 8) return -1;
    for (i = 0; i < size >> 12; i++) if (!g_pt[p0 + i].mapped) return -1;
    /* the contents are gone: a fresh anonymous mapping, no access until the next alloc_va */
    if (mmap((void *)((uintptr_t)va + g_bias), size, PROT_NONE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0) == MAP_FAILED) return -1;
    for (i = 0; i < size >> 12; i++) g_pt[p0 + i].mapped = 0;
    g_freed[g_nfreed].va = va; g_freed[g_nfreed++].size = size;
    return 0;
}
static fnvnx_os g_os = {NULL, h_read, h_map, h_prot, h_alloc, h_free, h_log, h_alloc_va, h_free_va};

static void plat_up(const uint8_t *file, uint32_t len) {
    g_res = (uint8_t *)mmap(NULL, WINDOW, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (g_res == (uint8_t *)MAP_FAILED) { perror("mmap"); exit(2); }
    g_bias = (uintptr_t)g_res;
    g_pt = (__typeof__(g_pt))calloc(NPAGES, sizeof *g_pt);
    g_file = (uint8_t *)file; g_file_len = len;
    g_saw_x = 0; g_fail_alloc = 0; g_alloc_calls = 0; g_map_calls = 0; g_next_va = ALLOC_BASE; g_nlog = 0; g_nfreed = 0; g_free_calls = 0;
}
static void plat_down(void) { munmap(g_res, WINDOW); free(g_pt); g_pt = NULL; }
static int logged(const char *s) { unsigned i; for (i = 0; i < g_nlog; i++) if (strstr(g_log[i], s)) return 1; return 0; }
static unsigned mapped_pages_in(uint32_t lo, uint32_t hi) {
    unsigned n = 0; uint32_t p;
    for (p = lo >> 12; p < hi >> 12; p++) n += g_pt[p].mapped;
    return n;
}

/* ---- resolver: a pure function of the import; deny list for the failure test ---- */
static const char *g_deny[3]; static unsigned g_ndeny;
static uint32_t key_of(const char *dll, const char *name, uint32_t ord) {
    char b[200]; uint32_t h = 2166136261u; const char *s;
    snprintf(b, sizeof b, "%s!%s", dll, name ? name : "");
    if (!name) snprintf(b, sizeof b, "%s!#%u", dll, (unsigned)ord);
    for (s = b; *s; s++) h = (h ^ (uint8_t)*s) * 16777619u;
    return 0x00100000u + 16u * (h % 0xfff0u);
}
static uint32_t resolve(void *c, const char *dll, const char *name, uint32_t ord) {
    char b[200]; unsigned i;
    (void)c;
    if (name) snprintf(b, sizeof b, "%s!%s", dll, name); else snprintf(b, sizeof b, "%s!#%u", dll, (unsigned)ord);
    for (i = 0; i < g_ndeny; i++) if (!strcmp(b, g_deny[i])) return 0;
    return key_of(dll, name, ord);
}

/* ---- our lifter ---- */
typedef struct { uint32_t eip, esp, teb, w[4]; unsigned nlog; int in_stack; } call_rec;
static call_rec g_calls[8]; static unsigned g_ncalls;
uint32_t fnvnx_lift_enter(uint32_t eip, uint32_t esp, uint32_t teb) {
    call_rec *r = &g_calls[g_ncalls++ % 8];
    unsigned k;
    r->eip = eip; r->esp = esp; r->teb = teb; r->nlog = g_nlog;
    for (k = 0; k < 4; k++) memcpy(&r->w[k], g_res + esp + 4 * k, 4);
    r->in_stack = g_proc && esp >= g_proc->main.stack_limit && esp + 16 <= g_proc->main.stack_base;
    return eip == g_proc->entry_va ? 0xC0DE0001u : 0xdead0000u + g_ncalls;
}

/* ---- independent expectation ---- */
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint32_t rd16(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8; }
typedef struct { uint32_t rva, vsize, raw_off, raw_size, ch; char name[9]; } xsec;
typedef struct { uint8_t *img; uint8_t *prot; uint8_t *mapped_by_file; uint32_t base, size, nsec, soh, stack_res, entry, nslots; xsec s[16]; uint32_t imp_rva, tls_rva, tls_size, cb_off; } expect;

static uint32_t up(uint32_t v) { return (v + 0xfff) & ~0xfffu; }
static int build_expect(expect *e, const uint8_t *f, uint32_t len) {
    uint32_t lf = rd32(f + 0x3c), opt = lf + 24, shoff, i, k;
    memset(e, 0, sizeof *e);
    e->nsec = rd16(f + lf + 6);
    shoff = opt + rd16(f + lf + 20);
    e->base = rd32(f + opt + 28); e->size = up(rd32(f + opt + 56)); e->soh = rd32(f + opt + 60);
    e->stack_res = rd32(f + opt + 72); e->entry = rd32(f + opt + 16);
    e->imp_rva = rd32(f + opt + 96 + 8); e->tls_rva = rd32(f + opt + 96 + 72); e->tls_size = rd32(f + opt + 100 + 72);
    e->img = (uint8_t *)calloc(e->size, 1); e->prot = (uint8_t *)calloc(e->size >> 12, 1);
    memset(e->prot, 0, e->size >> 12);
    memcpy(e->img, f, e->soh);
    for (k = 0; k < (e->soh + 0xfff) >> 12; k++) e->prot[k] = FNVNX_PROT_R;
    for (i = 0; i < e->nsec; i++) {
        const uint8_t *h = f + shoff + 40 * i; xsec *s = &e->s[i];
        uint32_t n;
        memcpy(s->name, h, 8); s->name[8] = 0;
        s->vsize = rd32(h + 8); s->rva = rd32(h + 12); s->raw_size = rd32(h + 16); s->raw_off = rd32(h + 20); s->ch = rd32(h + 36);
        if (!s->vsize) s->vsize = s->raw_size;
        n = s->raw_size < s->vsize ? s->raw_size : s->vsize;
        if ((uint64_t)s->raw_off + n > len) return -1;
        memcpy(e->img + s->rva, f + s->raw_off, n);
        for (k = 0; k < up(s->vsize) >> 12; k++)
            e->prot[(s->rva >> 12) + k] = (uint8_t)((s->ch & 0x40000000u ? FNVNX_PROT_R : 0) | (s->ch & 0x80000000u ? FNVNX_PROT_W : 0));
    }
    return 0;
}
/* apply the import rule to the expected image; returns the number of slots */
static uint32_t expect_imports(expect *e) {
    uint32_t d, n = 0;
    for (d = 0; ; d += 20) {
        const uint8_t *ds = e->img + e->imp_rva + d;
        uint32_t oft = rd32(ds), name = rd32(ds + 12), ft = rd32(ds + 16), j;
        if (!oft && !name && !ft) break;
        for (j = 0;; j++) {
            uint32_t t = rd32(e->img + (oft ? oft : ft) + 4 * j), v;
            if (!t) break;
            v = (t & 0x80000000u) ? key_of((const char *)e->img + name, NULL, t & 0xffff)
                                  : key_of((const char *)e->img + name, (const char *)e->img + t + 2, 0);
            memcpy(e->img + ft + 4 * j, &v, 4);   /* little-endian host */
            n++;
        }
    }
    return n;
}

static int probe_read(const void *p) { int fd[2], r; char c; if (pipe(fd)) return -1; r = (int)write(fd[1], p, 1) == 1; if (r) (void)!read(fd[0], &c, 1); close(fd[0]); close(fd[1]); return r; }
static int probe_write(void *p) { static int z = -1; unsigned char save; int r; if (z < 0) z = open("/dev/zero", 0); if (probe_read(p)) memcpy(&save, p, 1); else save = 0; r = read(z, p, 1) == 1; if (r) memcpy(p, &save, 1); return r; }

static void check_no_exec(void) {
    FILE *f = fopen("/proc/self/maps", "r"); char line[512]; unsigned long lo, hi; char perm[8];
    int bad = 0;
    while (f && fgets(line, sizeof line, f))
        if (sscanf(line, "%lx-%lx %7s", &lo, &hi, perm) == 3 && hi > (unsigned long)g_res && lo < (unsigned long)g_res + WINDOW && perm[2] == 'x') bad = 1;
    if (f) fclose(f);
    CHECK(!bad && !g_saw_x, "no mapping of the guest window is host-executable");
}

static int main_run(const char *tag, const uint8_t *file, uint32_t len, const uint8_t sha[32], int synth) {
    fnvnx_proc *p = (fnvnx_proc *)calloc(1, sizeof *p);
    fnvnx_start_cfg cfg;
    expect e;
    uint32_t i, pg, nslots, ret, esp;
    unsigned nbad;
    int r;
    plat_up(file, len);
    g_proc = p; g_ndeny = 0; g_ncalls = 0;
    memset(&cfg, 0, sizeof cfg);
    cfg.file_size = len; memcpy(cfg.sha256, sha, 32); cfg.resolve = resolve; cfg.bias = g_bias;
    r = fnvnx_start_prepare(p, &g_os, &cfg);
    CHECK(r == 0, "%s: prepare returned %d", tag, r);
    if (r || build_expect(&e, file, len)) { n_bad++; goto out; }
    nslots = expect_imports(&e);
    CHECK(p->imports_bound == nslots, "%s: imports bound %u, independent count %u", tag, (unsigned)p->imports_bound, (unsigned)nslots);
    CHECK(g_nlog == 3 && !strcmp(g_log[0], "fnvnx-start: identity ok") && !strcmp(g_log[1], "fnvnx-start: image mapped") &&
          !strncmp(g_log[2], "fnvnx-start: imports bound ", 27) && (uint32_t)atoi(g_log[2] + 27) == nslots, "%s: log lines before enter (%u)", tag, g_nlog);
    /* TLS index word is 0 in the expectation */
    if (e.tls_size >= 24) { uint32_t idx = rd32(e.img + e.tls_rva + 8) - e.base; memset(e.img + idx, 0, 4); }
    /* every page: protection per S6 (hook bookkeeping + a real probe), every byte per the Windows rule */
    nbad = 0;
    for (pg = 0; pg < e.size >> 12; pg++) {
        uint32_t va = e.base + pg * 4096u;
        unsigned want = e.prot[pg]; int hostr = (want & (FNVNX_PROT_R | FNVNX_PROT_W)) != 0;
        if (!g_pt[va >> 12].mapped || g_pt[va >> 12].prot != want) { nbad++; continue; }
        if (probe_read(g_res + va) != hostr) { nbad++; continue; }
        if (hostr && memcmp(g_res + va, e.img + pg * 4096u, 4096)) nbad++;
        if (probe_write(g_res + va) != ((want & FNVNX_PROT_W) != 0)) nbad++;
    }
    CHECK(nbad == 0, "%s: %u of %u image pages differ in mapping, protection or content", tag, nbad, e.size >> 12);
    CHECK(mapped_pages_in(0, e.base) == 0 && mapped_pages_in(FNVNX_START_RESERVED_LO, FNVNX_START_RESERVED_HI) == 0, "%s: nothing below the image / in the reserved range", tag);
    /* section map */
    CHECK(p->nmap == e.nsec + 1 && p->map[0].sec == 0xffff && p->map[0].prot == FNVNX_PROT_R, "%s: section map head", tag);
    for (i = 0; i < e.nsec; i++) {
        const fnvnx_start_range *m = &p->map[i + 1];
        unsigned want = (e.s[i].ch & 0x40000000u ? 1u : 0) | (e.s[i].ch & 0x80000000u ? 2u : 0) | (e.s[i].ch & 0x20000000u ? 4u : 0);
        CHECK(m->va == e.base + e.s[i].rva && m->size == up(e.s[i].vsize) && m->prot == want && !strcmp(m->name, e.s[i].name), "%s: map entry %s", tag, e.s[i].name);
    }
    /* thread block */
    {
        const fnvnx_thread_block *t = &p->main;
        const uint8_t *teb = g_res + t->teb_va;
        uint32_t k, tls_va;
        int zero_rest = 1;
        CHECK(t->stack_base - t->stack_limit == up(e.stack_res), "%s: stack size 0x%x vs reserve 0x%x", tag, (unsigned)(t->stack_base - t->stack_limit), (unsigned)e.stack_res);
        CHECK(t->guard_va + 0x1000 == t->stack_limit && g_pt[t->guard_va >> 12].mapped && g_pt[t->guard_va >> 12].prot == 0 && probe_read(g_res + t->guard_va) == 0, "%s: guard page", tag);
        CHECK(probe_write(g_res + t->stack_limit) && probe_write(g_res + t->stack_base - 1), "%s: stack writable", tag);
        CHECK(rd32(teb) == 0xffffffffu && rd32(teb + 4) == t->stack_base && rd32(teb + 8) == t->stack_limit && rd32(teb + 0x18) == t->teb_va && rd32(teb + 0x2c) == t->tls_vec_va, "%s: TEB words", tag);
        for (k = 0; k < 4096; k++) if (!(k == 0 || k == 1 || k == 2 || k == 3 || (k >= 4 && k < 12) || (k >= 0x18 && k < 0x1c) || (k >= 0x2c && k < 0x30)) && teb[k]) zero_rest = 0;
        CHECK(zero_rest, "%s: rest of the TEB zero", tag);
        tls_va = rd32(g_res + t->tls_vec_va);
        CHECK(tls_va == t->tls_block_va, "%s: TLS vector slot 0", tag);
        if (e.tls_size >= 24) {
            uint32_t ts = rd32(e.img + e.tls_rva), te = rd32(e.img + e.tls_rva + 4), zf = rd32(e.img + e.tls_rva + 16), al = (rd32(e.img + e.tls_rva + 20) >> 20) & 15;
            uint32_t align = al ? 1u << (al - 1) : 1;
            CHECK(tls_va && tls_va % align == 0, "%s: TLS block aligned (%u)", tag, (unsigned)align);
            CHECK(!memcmp(g_res + tls_va, e.img + (ts - e.base), te - ts), "%s: TLS block = template", tag);
            for (k = 0; k < zf; k++) if (g_res[tls_va + (te - ts) + k]) zero_rest = 0;
            CHECK(zero_rest, "%s: TLS block zero fill", tag);
            CHECK(p->tls.template_size == te - ts && p->tls.zero_fill == zf, "%s: TLS layout", tag);
        } else CHECK(tls_va == 0, "%s: no TLS dir: slot 0 is 0", tag);
    }
    /* enter */
    ret = fnvnx_start_enter(p);
    esp = (p->main.stack_base - 16) & ~15u;
    CHECK(ret == 0xC0DE0001u && g_ncalls == p->ntls_cb + 1, "%s: enter returned 0x%x after %u calls", tag, (unsigned)ret, g_ncalls);
    for (i = 0; i < p->ntls_cb && i + 1 < g_ncalls; i++)
        CHECK(g_calls[i].eip == p->tls_cb[i] && g_calls[i].esp == esp && g_calls[i].teb == p->main.teb_va && g_calls[i].w[0] == FNVNX_START_SENTINEL &&
              g_calls[i].w[1] == e.base && g_calls[i].w[2] == 1 && g_calls[i].w[3] == 0 && g_calls[i].in_stack && g_calls[i].nlog == 3, "%s: callback %u call", tag, i);
    {
        call_rec *c = &g_calls[p->ntls_cb];
        CHECK(c->eip == e.base + e.entry && c->esp == esp && !(c->esp & 15) && c->teb == p->main.teb_va && c->w[0] == FNVNX_START_SENTINEL && c->w[1] == 0 && c->in_stack, "%s: entry call", tag);
        CHECK(c->nlog == 4 && g_nlog == 4, "%s: enter log line before the entry call", tag);
        { char want[64]; snprintf(want, sizeof want, "fnvnx-start: enter %08x", (unsigned)(e.base + e.entry)); CHECK(!strcmp(g_log[3], want), "%s: enter line '%s'", tag, g_log[3]); }
    }
    check_no_exec();
    if (synth) {
        unsigned k;
        CHECK(p->imports_bound == SS_NIMPORTS && p->ntls_cb == 2 && p->tls_cb[0] == SS_CB1 && p->tls_cb[1] == SS_CB2 && p->ngap == 2, "synthetic facts");
        for (k = 0; k < SS_NIMPORTS; k++) CHECK(rd32(g_res + ss_imports[k].slot) == key_of(ss_imports[k].dll, ss_imports[k].name, ss_imports[k].ordinal), "slot %u value", k);
        CHECK(rd32(g_res + ss_delay_slots[0]) == 0xd1d1d1d1u && rd32(g_res + ss_delay_slots[1]) == 0xd2d2d2d2u, "delay slots untouched");
        CHECK(rd32(g_res + SS_TLS_INDEX) == 0, "TLS index word 0");
        CHECK(mapped_pages_in(ALLOC_BASE, 0x20000000u) == 0x30 + 1 + 1 + 1, "pages allocated for the thread block");
        CHECK(p->main.stack_base - p->main.stack_limit == SS_STACK_RESERVE && p->pe.stack_commit == 0x2000, "stack reserve/commit");
    } else {
        printf("exe: sections mapped %u (+headers), SizeOfImage 0x%x, import slots bound %u, entry eip 0x%08x, stack 0x%x, TLS template %u bytes (+%u zero), tls callbacks %u, gap ranges %u\n",
               (unsigned)e.nsec, (unsigned)e.size, (unsigned)p->imports_bound, (unsigned)(e.base + e.entry), (unsigned)(p->main.stack_base - p->main.stack_limit),
               (unsigned)p->tls.template_size, (unsigned)p->tls.zero_fill, p->ntls_cb, p->ngap);
    }
    {   /* create -> free -> create: every range comes back, nothing stays mapped, the recycled addresses read as fresh */
        fnvnx_thread_block a, b, c2;
        uint32_t before = mapped_pages_in(ALLOC_BASE, 0x20000000u), k;
        r = fnvnx_thread_block_create(p, 0x8000, &a);
        CHECK(r == 0 && mapped_pages_in(ALLOC_BASE, 0x20000000u) > before, "%s: second thread block", tag);
        memset(g_res + a.stack_limit, 0xa5, 0x8000); memset(g_res + a.teb_va, 0xa5, 0x1000);
        k = mapped_pages_in(ALLOC_BASE, 0x20000000u) - before;
        CHECK(fnvnx_thread_block_free(p, &a) == 0 && mapped_pages_in(ALLOC_BASE, 0x20000000u) == before && g_free_calls == 3,
              "%s: block freed (%u pages, %u free calls)", tag, k, g_free_calls);
        r = fnvnx_thread_block_create(p, 0x8000, &b);
        CHECK(r == 0 && b.guard_va == a.guard_va && (b.teb_va == a.teb_va || b.teb_va == a.tls_vec_va) && (b.tls_vec_va == a.teb_va || b.tls_vec_va == a.tls_vec_va) && mapped_pages_in(ALLOC_BASE, 0x20000000u) == before + k,
              "%s: third thread block reuses the freed ranges", tag);
        for (k = 0; k < 0x8000; k += 4) if (rd32(g_res + b.stack_limit + k)) break;
        CHECK(k == 0x8000 && rd32(g_res + b.teb_va + 4) == b.stack_base && rd32(g_res + b.teb_va + 0x100) == 0 &&
              g_pt[b.guard_va >> 12].prot == 0 && probe_read(g_res + b.guard_va) == 0, "%s: recycled block is fresh (zero stack, TEB, guard)", tag);
        CHECK(fnvnx_thread_block_free(p, &b) == 0 && mapped_pages_in(ALLOC_BASE, 0x20000000u) == before, "%s: freed again", tag);
        g_fail_alloc = g_alloc_calls + 3;   /* the TLS area cannot be had: stack and TEB go back */
        CHECK(fnvnx_thread_block_create(p, 0x8000, &c2) != 0 && mapped_pages_in(ALLOC_BASE, 0x20000000u) == before &&
              c2.teb_va == 0 && c2.guard_va == 0, "%s: failed create leaves nothing allocated", tag);
        g_fail_alloc = 0;
    }
    free(e.img); free(e.prot);
out:
    plat_down();
    free(p);
    return r;
}

int main(int argc, char **argv) {
    FILE *f; uint8_t *file; uint32_t len;
    uint8_t sha[32];
    const char *exe = NULL;
    int i, r;
    if (argc < 2) { fprintf(stderr, "usage: startcheck synth.pe [--exe FalloutNV.exe]\n"); return 2; }
    for (i = 2; i + 1 < argc; i++) if (!strcmp(argv[i], "--exe")) exe = argv[i + 1];
    f = fopen(argv[1], "rb"); if (!f) { perror(argv[1]); return 2; }
    fseek(f, 0, SEEK_END); len = (uint32_t)ftell(f); fseek(f, 0, SEEK_SET);
    file = (uint8_t *)malloc(len); if (fread(file, 1, len, f) != len) return 2; fclose(f);
    CHECK(len == SS_FILE_SIZE, "generator file size");
    memcpy(sha, ss_sha256, 32);
    main_run("synth", file, len, sha, 1);

    {   /* failures */
        fnvnx_proc *p = (fnvnx_proc *)calloc(1, sizeof *p);
        fnvnx_start_cfg cfg;
        uint8_t bad[32];
        memcpy(bad, sha, 32); bad[7] ^= 1;
        memset(&cfg, 0, sizeof cfg);
        g_proc = p;
        /* wrong SHA-256: refused before anything is mapped */
        plat_up(file, len); cfg.file_size = len; memcpy(cfg.sha256, bad, 32); cfg.resolve = resolve; cfg.bias = g_bias; g_ndeny = 0;
        r = fnvnx_start_prepare(p, &g_os, &cfg);
        CHECK(r == FNVNX_START_E_IDENTITY && logged("identity mismatch") && !logged("identity ok") && g_map_calls == 0, "wrong sha: rc %d", r);
        plat_down();
        /* range in use: a page inside a section / inside a gap */
        for (i = 0; i < 2; i++) {
            plat_up(file, len); cfg.file_size = len; memcpy(cfg.sha256, sha, 32); cfg.resolve = resolve; cfg.bias = g_bias;
            CHECK(h_map(NULL, (uintptr_t)(i ? 0x00408000u : 0x00402000u) + g_bias, 0x1000, FNVNX_PROT_R) == 0, "pre-map");
            r = fnvnx_start_prepare(p, &g_os, &cfg);
            CHECK(r != 0 && logged("identity ok") && !logged("imports bound") && g_alloc_calls == 0, "range in use (%s): rc %d", i ? "gap" : "section", r);
            plat_down();
        }
        /* imports without provider: all listed, slots untouched, start refused */
        plat_up(file, len); cfg.file_size = len; cfg.bias = g_bias;
        g_deny[0] = "KERNEL32.dll!Sleep"; g_deny[1] = "USER32.dll!#17"; g_ndeny = 2;
        r = fnvnx_start_prepare(p, &g_os, &cfg);
        CHECK(r == FNVNX_START_E_IMPORTS && logged("KERNEL32.dll!Sleep") && logged("USER32.dll!#17") && !logged("imports bound") && g_alloc_calls == 0, "no provider: rc %d", r);
        CHECK(rd32(g_res + ss_imports[3].slot) != 0 && rd32(g_res + ss_imports[3].slot) < 0x00400000u, "unresolved slot left as in the file");
        CHECK(rd32(g_res + ss_imports[0].slot) == key_of("KERNEL32.dll", "GetTickCount", 0), "resolved slots are bound");
        g_ndeny = 0;
        plat_down();
        /* truncated file (its own hash is right): the headers do not fit */
        {
            uint8_t tsha[32]; fnvnx_sha256(file, 0x2000, tsha);
            plat_up(file, 0x2000); cfg.file_size = 0x2000; memcpy(cfg.sha256, tsha, 32); cfg.resolve = resolve; cfg.bias = g_bias;
            r = fnvnx_start_prepare(p, &g_os, &cfg);
            CHECK(r == FNVNX_E_FORMAT && g_map_calls == 0 && logged("identity ok"), "truncated file: rc %d", r);
            plat_down();
        }
        /* the platform cannot give guest memory */
        plat_up(file, len); cfg.file_size = len; memcpy(cfg.sha256, sha, 32); cfg.bias = g_bias; g_fail_alloc = 2;
        r = fnvnx_start_prepare(p, &g_os, &cfg);
        CHECK(r != 0 && logged("guest memory request"), "alloc_va failure: rc %d", r);
        plat_down();
        free(p);
    }

    if (exe) {
        char shapath[1024]; char hex[80]; uint8_t *ef; uint32_t elen; FILE *g;
        snprintf(shapath, sizeof shapath, "%s.sha256", exe);
        f = fopen(exe, "rb"); g = fopen(shapath, "r");
        if (!f || !g || !fgets(hex, sizeof hex, g)) { printf("exe or its .sha256 missing: skipped\n"); }
        else {
            fseek(f, 0, SEEK_END); elen = (uint32_t)ftell(f); fseek(f, 0, SEEK_SET);
            ef = (uint8_t *)malloc(elen); if (fread(ef, 1, elen, f) != elen) return 2;
            for (i = 0; i < 32; i++) { unsigned v; sscanf(hex + 2 * i, "%2x", &v); sha[i] = (uint8_t)v; }
            main_run("exe", ef, elen, sha, 0);
            free(ef);
        }
        if (f) fclose(f);
        if (g) fclose(g);
    }
    free(file);
    printf("startcheck: %d checks passed, %d failed\n", n_ok, n_bad);
    return n_bad ? 1 : 0;
}
