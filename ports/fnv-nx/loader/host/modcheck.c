/* modcheck: tests of the module loader (rules M2..M4) on the host, x86-64, sanitizers on.
 *
 *   modcheck DIR [--real-exe FalloutNV.exe --real-dir D --real-modules modules.c-derived-table ...]  (see main)
 *
 * DIR holds what synth/synth_modules.py wrote: a synthetic exe and DLLs (A imports B by name and ordinal, has TLS and must
 * be relocated; B has a forwarder to C and an entry point; C has no entry point; D and E are loaded on demand) and one
 * file per defect variant. The platform below provides the OS hooks over an mmap-reserved window with a page table, a
 * resolver for platform dlls, and its OWN fnvnx_lift_enter that records every guest call. Expectations are computed
 * independently of the loader: the Windows mapping rule applied to the file bytes, the relocations the generator
 * listed, the import values the generator derived from the export tables. */
#define _DEFAULT_SOURCE
#include "fnvnx_start.h"
#include "synth_mod.h"
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
#define EXE_BASE 0x400000u

/* ---- files ---- */
typedef struct { const char *name; uint8_t *buf; uint32_t len; } blob;
static blob g_blobs[32]; static unsigned g_nblobs;
static blob *g_f[1 + SM_NMOD];            /* os->read file selector -> blob (NULL: the file does not exist) */
static fnvnx_module_desc g_tab[SM_NMOD];
static uint32_t g_ntab;

static blob *load_blob(const char *dir, const char *name) {
    char path[1024]; FILE *f; blob *b = &g_blobs[g_nblobs++];
    snprintf(path, sizeof path, "%s/%s", dir, name);
    f = fopen(path, "rb"); if (!f) { perror(path); exit(2); }
    fseek(f, 0, SEEK_END); b->len = (uint32_t)ftell(f); fseek(f, 0, SEEK_SET);
    b->buf = (uint8_t *)malloc(b->len); if (fread(b->buf, 1, b->len, f) != b->len) exit(2);
    fclose(f); b->name = name;
    return b;
}
static blob *find_blob(const char *name) { unsigned i; for (i = 0; i < g_nblobs; i++) if (!strcmp(g_blobs[i].name, name)) return &g_blobs[i]; exit(2); }

/* ---- the platform ---- */
static uint8_t *g_res; static uintptr_t g_bias;
static struct { uint8_t mapped; uint8_t prot; } *g_pt;
static int g_saw_x, g_force_va_calls; static uint32_t g_force_va;
static uint32_t g_next_va;
static char g_log[96][200]; static unsigned g_nlog;
static fnvnx_proc *g_proc;

static int h_read(void *c, uint32_t file, uint32_t off, void *dst, uint32_t size) {
    (void)c;
    if (file > SM_NMOD || !g_f[file] || (uint64_t)off + size > g_f[file]->len) return -1;
    memcpy(dst, g_f[file]->buf + off, size);
    return 0;
}
static int to_prot(unsigned p) { return (p & FNVNX_PROT_R ? PROT_READ : 0) | (p & FNVNX_PROT_W ? PROT_WRITE : 0); }
static int map_pages(void *c, uintptr_t addr, uint32_t size, unsigned prot) {
    uintptr_t lo = (uintptr_t)g_res; uint32_t i, p0;
    (void)c;
    if ((addr & 0xfff) || (size & 0xfff) || addr < lo || addr - lo + size > WINDOW) return -1;
    p0 = (uint32_t)((addr - lo) >> 12);
    for (i = 0; i < size >> 12; i++) if (g_pt[p0 + i].mapped) return -1;
    if (mmap((void *)addr, size, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS, -1, 0) == MAP_FAILED) return -1;
    if (prot & FNVNX_PROT_X) g_saw_x = 1;
    for (i = 0; i < size >> 12; i++) { g_pt[p0 + i].mapped = 1; g_pt[p0 + i].prot = (uint8_t)(FNVNX_PROT_R | FNVNX_PROT_W); }
    return 0;
}
/* the map_fixed hook records every successful call; unmap_fixed accepts exactly one recorded (addr, size) pair and only once */
static struct { uintptr_t addr; uint32_t size; int live; } g_rec[512]; static unsigned g_nrec, g_unmaps, g_unmap_refused;
static int h_map(void *c, uintptr_t addr, uint32_t size, unsigned prot) {
    int r = map_pages(c, addr, size, prot);
    if (!r && g_nrec < 512) { g_rec[g_nrec].addr = addr; g_rec[g_nrec].size = size; g_rec[g_nrec++].live = 1; }
    return r;
}
static uintptr_t g_fail_unmap;   /* a range at this address cannot be given back (a failing platform) */
static int h_unmap(void *c, uintptr_t addr, uint32_t size) {
    unsigned k, hit = 512; uint32_t i, p0 = (uint32_t)((addr - (uintptr_t)g_res) >> 12);
    (void)c;
    if (g_fail_unmap && addr == g_fail_unmap) return -1;
    for (k = 0; k < g_nrec; k++) if (g_rec[k].live && g_rec[k].addr == addr && g_rec[k].size == size) hit = k;
    if (hit == 512) { g_unmap_refused++; return -1; }
    if (mmap((void *)addr, size, PROT_NONE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0) == MAP_FAILED) return -1;
    for (i = 0; i < size >> 12; i++) g_pt[p0 + i].mapped = 0;
    g_rec[hit].live = 0; g_unmaps++;
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
static void h_log(void *c, const char *m) { (void)c; if (g_nlog < 96) snprintf(g_log[g_nlog++], sizeof g_log[0], "%s", m); }
static int h_free_va(void *c, uint32_t va, uint32_t size) {
    uint32_t i, p0 = (uint32_t)((((uintptr_t)va + g_bias) - (uintptr_t)g_res) >> 12);
    (void)c;
    if ((va & 0xfff) || (size & 0xfff)) return -1;
    for (i = 0; i < size >> 12; i++) if (!g_pt[p0 + i].mapped) return -1;
    if (mmap((void *)((uintptr_t)va + g_bias), size, PROT_NONE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0) == MAP_FAILED) return -1;
    for (i = 0; i < size >> 12; i++) g_pt[p0 + i].mapped = 0;
    return 0;
}
/* g_force_va: the next allocations are placed at this address (a hostile or mistaken platform) */
static uint32_t h_alloc_va(void *c, uint32_t size, unsigned prot) {
    uint32_t va = g_next_va;
    (void)prot;
    if (g_force_va) { va = g_force_va; g_force_va_calls++; if (map_pages(c, (uintptr_t)va + g_bias, size, FNVNX_PROT_R | FNVNX_PROT_W)) return 0; return va; }
    if (map_pages(c, (uintptr_t)va + g_bias, size, FNVNX_PROT_R | FNVNX_PROT_W)) return 0;
    g_next_va += size + 0x1000;
    return va;
}
static unsigned g_nreg; static uint32_t g_regbase[32], g_regsize[32]; static char g_regname[32][64];
static void h_reg(void *c, const char *n, uint32_t b, uint32_t s) { (void)c; if (g_nreg < 32) { snprintf(g_regname[g_nreg], 64, "%s", n); g_regbase[g_nreg] = b; g_regsize[g_nreg] = s; } g_nreg++; }
static fnvnx_os g_os = {NULL, h_read, h_map, h_prot, h_alloc, h_free, h_log, h_alloc_va, h_free_va, h_reg, h_unmap};

static void plat_up(void) {
    g_res = (uint8_t *)mmap(NULL, WINDOW, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (g_res == (uint8_t *)MAP_FAILED) { perror("mmap"); exit(2); }
    g_bias = (uintptr_t)g_res;
    g_pt = (__typeof__(g_pt))calloc(NPAGES, sizeof *g_pt);
    g_saw_x = 0; g_force_va = 0; g_force_va_calls = 0; g_next_va = ALLOC_BASE; g_nlog = 0; g_nrec = g_unmaps = g_unmap_refused = 0;
}
static void plat_down(void) { munmap(g_res, WINDOW); free(g_pt); g_pt = NULL; }
static int logged(const char *s) { unsigned i; for (i = 0; i < g_nlog; i++) if (strstr(g_log[i], s)) return 1; return 0; }
static unsigned logged_n(const char *s) { unsigned i, n = 0; for (i = 0; i < g_nlog; i++) if (strstr(g_log[i], s)) n++; return n; }
static unsigned mapped_pages_in(uint32_t lo, uint32_t hi) { unsigned n = 0; uint32_t p; for (p = lo >> 12; p < hi >> 12; p++) n += g_pt[p].mapped; return n; }

/* ---- resolver: a pure function of the import, with the dll-name rule; a deny list for the failure tests ---- */
static const char *g_deny[4]; static unsigned g_ndeny;
static void norm(const char *in, char *out) {
    size_t n = 0; int dot = 0;
    for (; in[n]; n++) { char c = in[n]; if (c >= 'A' && c <= 'Z') c = (char)(c + 32); if (c == '.') dot = 1; out[n] = c; }
    if (!dot) { memcpy(out + n, ".dll", 4); n += 4; }
    out[n] = 0;
}
static uint32_t key_of(const char *dll, const char *name, uint32_t ord) {
    char d[200], b[300]; uint32_t h = 2166136261u; const char *s;
    norm(dll, d);
    if (name) snprintf(b, sizeof b, "%s!%s", d, name); else snprintf(b, sizeof b, "%s!#%u", d, (unsigned)ord);
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

/* ---- our lifter: records every call; a DllMain returns 1 unless its address is g_fail_eip ---- */
typedef struct { uint32_t eip, esp, teb, w[4]; unsigned nlog; } call_rec;
static call_rec g_calls[32]; static unsigned g_ncalls; static uint32_t g_fail_eip;
fnvnx_proc *fnvnx_start_proc(void) { return g_proc; }
uint32_t fnvnx_lift_enter(uint32_t eip, uint32_t esp, uint32_t teb) {
    call_rec *r = &g_calls[g_ncalls % 32]; unsigned k;
    r->eip = eip; r->esp = esp; r->teb = teb; r->nlog = g_nlog;
    for (k = 0; k < 4; k++) memcpy(&r->w[k], g_res + esp + 4 * k, 4);
    g_ncalls++;
    if (eip == g_proc->exe.entry_va) return 0xC0DE0001u;
    return eip == g_fail_eip ? 0u : 1u;
}

/* ---- independent expectation ---- */
static uint32_t rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static uint32_t rd16(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8; }
static void wr32(uint8_t *p, uint32_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24); }
static uint32_t up(uint32_t v) { return (v + 0xfff) & ~0xfffu; }
typedef struct { uint8_t *img, *prot; uint32_t base, size, nsec; struct { uint32_t rva, vsize, ch; } s[16]; } expect;

/* file bytes by the Windows rule, protections from the section bits, then the relocations, import slots and TLS index the
 * generator listed for image idx (0 = exe, k+1 = sm_table[k]) */
static void build_expect(expect *e, unsigned idx, const blob *b) {
    const uint8_t *f = b->buf; uint32_t lf = rd32(f + 0x3c), opt = lf + 24, shoff, i, k, soh;
    memset(e, 0, sizeof *e);
    e->nsec = rd16(f + lf + 6); shoff = opt + rd16(f + lf + 20);
    e->base = sm_images[idx].base; e->size = up(rd32(f + opt + 56)); soh = rd32(f + opt + 60);
    e->img = (uint8_t *)calloc(e->size, 1); e->prot = (uint8_t *)calloc(e->size >> 12, 1);
    memcpy(e->img, f, soh);
    for (k = 0; k < (soh + 0xfff) >> 12; k++) e->prot[k] = FNVNX_PROT_R;
    for (i = 0; i < e->nsec; i++) {
        const uint8_t *h = f + shoff + 40 * i; uint32_t vs = rd32(h + 8), rva = rd32(h + 12), rs = rd32(h + 16), ro = rd32(h + 20), ch = rd32(h + 36), n;
        if (!vs) vs = rs;
        n = rs < vs ? rs : vs;
        memcpy(e->img + rva, f + ro, n);
        e->s[i].rva = rva; e->s[i].vsize = vs; e->s[i].ch = ch;
        for (k = 0; k < up(vs) >> 12; k++) e->prot[(rva >> 12) + k] = (uint8_t)((ch & 0x40000000u ? FNVNX_PROT_R : 0) | (ch & 0x80000000u ? FNVNX_PROT_W : 0));
    }
    for (i = 0; i < SM_NRELOCS; i++)
        if (sm_relocs[i].img == idx) { uint8_t *w = e->img + (sm_relocs[i].va - e->base); wr32(w, rd32(w) + sm_relocs[i].delta); }
    for (i = 0; i < SM_NSLOTS; i++)
        if (sm_slots[i].img == idx)
            wr32(e->img + (sm_slots[i].slot - e->base), sm_slots[i].exported ? sm_slots[i].value : key_of(sm_slots[i].dll, sm_slots[i].name, sm_slots[i].ordinal));
    if (sm_images[idx].has_tls) wr32(e->img + (sm_images[idx].index_va - e->base), sm_images[idx].index);
}

static int probe_read(const void *p) { int fd[2], r; char c; if (pipe(fd)) return -1; r = (int)write(fd[1], p, 1) == 1; if (r) (void)!read(fd[0], &c, 1); close(fd[0]); close(fd[1]); return r; }
static int probe_write(void *p) { static int z = -1; unsigned char save; int r; if (z < 0) z = open("/dev/zero", 0); if (probe_read(p)) memcpy(&save, p, 1); else save = 0; r = read(z, p, 1) == 1; if (r) memcpy(p, &save, 1); return r; }

/* every page of image idx: mapping, protection (bookkeeping + a real probe), content */
static void check_image(const char *tag, unsigned idx, const blob *b) {
    expect e; uint32_t pg; unsigned nbad = 0;
    build_expect(&e, idx, b);
    for (pg = 0; pg < e.size >> 12; pg++) {
        uint32_t va = e.base + pg * 4096u; unsigned want = e.prot[pg]; int hostr = (want & (FNVNX_PROT_R | FNVNX_PROT_W)) != 0;
        if (!g_pt[va >> 12].mapped || g_pt[va >> 12].prot != want) { nbad++; continue; }
        if (probe_read(g_res + va) != hostr) { nbad++; continue; }
        if (hostr && memcmp(g_res + va, e.img + pg * 4096u, 4096)) nbad++;
        if (probe_write(g_res + va) != ((want & FNVNX_PROT_W) != 0)) nbad++;
    }
    CHECK(nbad == 0, "%s: image %u: %u of %u pages differ in mapping, protection or content", tag, idx, nbad, e.size >> 12);
    free(e.img); free(e.prot);
}

/* ---- scenarios ---- */
static const char *g_names[1 + SM_NMOD] = {"exe.pe", "c.dll", "b.dll", "a.dll", "d.dll", "e.dll", "f.dll"};
static void world(void) {   /* the unmodified world */
    unsigned i;
    for (i = 0; i < 1 + SM_NMOD; i++) g_f[i] = find_blob(g_names[i]);
    memcpy(g_tab, sm_table, sizeof g_tab);
    g_ntab = SM_NMOD; g_ndeny = 0; g_fail_eip = 0; g_ncalls = 0;
}
/* table row k is replaced by variant `file` (its hash and size; the base and size of image stay) */
static void variant(unsigned k, const char *file) {
    unsigned i;
    for (i = 0; i < SM_NVAR; i++)
        if (!strcmp(sm_variants[i].file, file)) { memcpy(g_tab[k].sha256, sm_variants[i].sha, 32); g_tab[k].file_size = sm_variants[i].size; g_f[k + 1] = find_blob(file); return; }
    exit(2);
}
static int start(fnvnx_proc *p, const char *exe_sha_from) {
    fnvnx_start_cfg cfg; (void)exe_sha_from;
    memset(&cfg, 0, sizeof cfg);
    cfg.file_size = g_f[0]->len; fnvnx_sha256(g_f[0]->buf, g_f[0]->len, cfg.sha256);
    cfg.resolve = resolve; cfg.bias = g_bias; cfg.modules = g_tab; cfg.nmodules = g_ntab; cfg.exe_name = "synth.exe";
    g_proc = p;
    return fnvnx_start_prepare(p, &g_os, &cfg);
}

typedef struct { unsigned who; const char *what; uint32_t reason, reserved; } want_call;
/* who: 1 = exe, 2 = c, 3 = b, 4 = a, 5 = d, 6 = e; what: "cb" or "main" */
static uint32_t base_of(unsigned who) { return who == 1 ? EXE_BASE : sm_table[who - 2].base; }
static uint32_t addr_of(const fnvnx_proc *p, unsigned who, const char *what) {
    const fnvnx_loaded *L = who == 1 ? &p->exe : &p->mod[who - 2];
    if (who > 1 && L->state == FNVNX_MOD_UNLOADED && !strcmp(what, "main")) return sm_images[who - 1].entry;   /* unloaded since: the generator's fact */
    return !strcmp(what, "cb") ? L->tls_cb[0] : L->entry_va;
}
static void check_calls(const char *tag, const fnvnx_proc *p, unsigned first, const want_call *w, unsigned n, const fnvnx_thread_block *b, uint32_t esp_now) {
    unsigned i; uint32_t esp = (esp_now - 16u) & ~15u;
    CHECK(g_ncalls == first + n, "%s: %u guest calls, want %u", tag, g_ncalls - first, n);
    for (i = 0; i < n && first + i < g_ncalls; i++) {
        const call_rec *c = &g_calls[(first + i) % 32];
        CHECK(c->eip == addr_of(p, w[i].who, w[i].what) && c->esp == esp && c->teb == b->teb_va && c->w[0] == FNVNX_START_SENTINEL &&
              c->w[1] == (w[i].who == 1 && !strcmp(w[i].what, "main") ? 0u : base_of(w[i].who)) && c->w[2] == w[i].reason && c->w[3] == w[i].reserved && esp >= b->stack_limit && esp + 16 <= b->stack_base,
              "%s: call %u: eip %08x esp %08x words %08x %08x %08x %08x (want %08x, who %u %s, reason %u, reserved %u)", tag, i, (unsigned)c->eip,
              (unsigned)c->esp, (unsigned)c->w[0], (unsigned)c->w[1], (unsigned)c->w[2], (unsigned)c->w[3], (unsigned)addr_of(p, w[i].who, w[i].what),
              w[i].who, w[i].what, (unsigned)w[i].reason, (unsigned)w[i].reserved);
    }
}

static void check_tls_vector(const char *tag, const fnvnx_proc *p, const fnvnx_thread_block *b) {
    uint32_t k; unsigned s;
    CHECK(p->tls_slots == 2 && rd32(g_res + b->teb_va + 0x2c) == b->tls_vec_va, "%s: %u TLS slots, TEB word", tag, (unsigned)p->tls_slots);
    for (s = 0; s < 2; s++) {
        uint32_t blk = rd32(g_res + b->tls_vec_va + 4 * s), ts = sm_images[s ? 3 : 0].tsize, zf = sm_images[s ? 3 : 0].zero, al = sm_images[s ? 3 : 0].align;
        const uint8_t *src = g_f[s ? 3 : 0]->buf; expect e; int ok = 1;
        build_expect(&e, s ? 3 : 0, g_f[s ? 3 : 0]);
        (void)src;
        CHECK(blk && blk % al == 0 && blk >= b->tls_vec_va + 8 && blk + ts + zf <= b->tls_vec_va + 0x1000, "%s: TLS slot %u block 0x%08x", tag, s, (unsigned)blk);
        if (blk) {
            ok = !memcmp(g_res + blk, e.img + (sm_images[s ? 3 : 0].tmpl - e.base), ts);
            for (k = 0; k < zf; k++) if (g_res[blk + ts + k]) ok = 0;
        }
        CHECK(ok, "%s: TLS block %u = template + zero fill", tag, s);
        free(e.img); free(e.prot);
    }
    CHECK(b->tls_block_va == rd32(g_res + b->tls_vec_va), "%s: tls_block_va is slot 0", tag);
}

static void good_world(void) {
    fnvnx_proc *p = (fnvnx_proc *)calloc(1, sizeof *p);
    fnvnx_thread_block t2; fnvnx_image_info inf; unsigned i, first;
    uint32_t base = 0, esp;
    int r;
    world(); plat_up();
    r = start(p, NULL);
    CHECK(r == 0, "prepare returned %d", r);
    if (r) { for (i = 0; i < g_nlog; i++) printf("  log: %s\n", g_log[i]); goto out; }
    /* mapping: every byte, protection, relocations, import slots, TLS index words */
    check_image("good", 0, g_f[0]); check_image("good", 1, g_f[1]); check_image("good", 2, g_f[2]); check_image("good", 3, g_f[3]);
    CHECK(mapped_pages_in(sm_table[3].base, sm_table[3].base + sm_table[3].size_of_image) == 0 && mapped_pages_in(sm_table[4].base, sm_table[4].base + sm_table[4].size_of_image) == 0,
          "on-demand modules are not mapped at start");
    CHECK(mapped_pages_in(0, EXE_BASE) == 0 && mapped_pages_in(FNVNX_START_RESERVED_LO, FNVNX_START_RESERVED_HI) == 0, "nothing below the exe / in the reserved range");
    {   /* every relocated word, counted per module, from the generator's list */
        unsigned cnt[1 + SM_NMOD] = {0}, k;
        for (k = 0; k < SM_NRELOCS; k++) cnt[sm_relocs[k].img - 1]++;
        CHECK(p->mod[0].relocs_applied == cnt[0] && p->mod[1].relocs_applied == cnt[1] && p->mod[2].relocs_applied == cnt[2] && cnt[0] == 0 && cnt[1] == 5 && cnt[2] == 10,
              "relocations applied %u %u %u, listed %u %u %u", (unsigned)p->mod[0].relocs_applied, (unsigned)p->mod[1].relocs_applied, (unsigned)p->mod[2].relocs_applied, cnt[0], cnt[1], cnt[2]);
    }
    CHECK(p->nmod == SM_NMOD && p->mod[0].state == FNVNX_MOD_MAPPED && p->mod[2].state == FNVNX_MOD_MAPPED && p->mod[3].state == FNVNX_MOD_UNLOADED, "module states");
    CHECK(p->exe.imports_bound == 7 && p->mod[0].imports_bound == 0 && p->mod[1].imports_bound == 3 && p->mod[2].imports_bound == 5, "imports bound %u %u %u %u",
          (unsigned)p->exe.imports_bound, (unsigned)p->mod[0].imports_bound, (unsigned)p->mod[1].imports_bound, (unsigned)p->mod[2].imports_bound);
    /* every slot value, read back from the mapped images (also compared page-wise above) */
    for (i = 0; i < SM_NSLOTS; i++) {
        uint32_t want = sm_slots[i].exported ? sm_slots[i].value : key_of(sm_slots[i].dll, sm_slots[i].name, sm_slots[i].ordinal);
        if (sm_slots[i].img <= 3) CHECK(rd32(g_res + sm_slots[i].slot) == want, "slot %08x %s!%s", (unsigned)sm_slots[i].slot, sm_slots[i].dll, sm_slots[i].name ? sm_slots[i].name : "#");
    }
    /* TLS: indices, vector, blocks */
    CHECK(p->exe.tls_index == 0 && p->mod[2].tls_index == 1 && p->mod[2].tls.present && !p->mod[0].tls.present && !p->mod[1].tls.present, "TLS indices");
    CHECK(rd32(g_res + sm_images[3].index_va) == 1 && rd32(g_res + sm_images[0].index_va) == 0, "TLS index words");
    check_tls_vector("main", p, &p->main);
    CHECK(logged("module c.dll mapped at 0x00520000, relocations 0") && logged("module a.dll mapped at 0x00500000") && logged("binding the imports of b.dll"), "mapping log lines");
    /* the image list before init */
    CHECK(fnvnx_images_count(p) == 1, "images before init: %u", (unsigned)fnvnx_images_count(p));
    /* M4: initialisation order and arguments */
    g_ncalls = 0;
    CHECK(fnvnx_start_init_modules(p) == 0, "init modules");
    {
        static const want_call w[] = {{3, "main", 1, 1}, {4, "cb", 1, 0}, {4, "main", 1, 1}};
        check_calls("init", p, 0, w, 3, &p->main, p->main.stack_base);
    }
    CHECK(p->ninit == 3 && p->init_order[0] == 0 && p->init_order[1] == 1 && p->init_order[2] == 2, "init order c, b, a");
    CHECK(logged("init b.dll") && logged("init a.dll") && !logged("init c.dll"), "init log lines");
    first = g_ncalls;
    CHECK(fnvnx_start_enter(p) == 0xC0DE0001u, "enter result");
    {
        static const want_call w[] = {{1, "cb", 1, 0}, {1, "main", 0, 0}};
        check_calls("enter", p, first, w, 2, &p->main, p->main.stack_base);
        CHECK(g_calls[(first + 1) % 32].w[1] == 0 && g_calls[(first + 1) % 32].w[2] == 0, "exe entry has no arguments");
    }
    /* the image list after init */
    CHECK(fnvnx_images_count(p) == 4, "images after init: %u", (unsigned)fnvnx_images_count(p));
    for (i = 0; i < 4; i++) {
        static const char *names[] = {"synth.exe", "c.dll", "b.dll", "a.dll"};
        CHECK(fnvnx_images_get(p, i, &inf) == 0 && !strcmp(inf.name, names[i]) && inf.base == sm_images[i].base + 0 * 0 - 0 + (i ? 0 : 0) &&
              inf.size_of_image == sm_images[i].soi && inf.nmap == (i >= 2 ? 5u : 4u) && inf.has_tls == sm_images[i].has_tls && inf.map[1].va == inf.base + 0x1000 &&
              inf.map[1].prot == (FNVNX_PROT_R | FNVNX_PROT_X), "image info %u (%s base %08x nmap %u)", i, inf.name, (unsigned)inf.base, inf.nmap);
    }
    CHECK(fnvnx_images_get(p, 4, &inf) != 0, "image index out of range");
    CHECK(g_nreg == 4, "register_image calls: %u", g_nreg);
    for (i = 0; i < 4; i++) {   /* every image registered exactly once with its name, base and page rounded size */
        unsigned j, hits = 0;
        CHECK(fnvnx_images_get(p, i, &inf) == 0, "images_get %u", i);
        for (j = 0; j < 4; j++) hits += !strcmp(g_regname[j], inf.name) && g_regbase[j] == inf.base && g_regsize[j] == inf.size_of_image;
        CHECK(hits == 1, "register_image of image %u: %u hits", i, hits);
    }
    /* thread attach / detach / process detach */
    CHECK(fnvnx_thread_block_create(p, 0x8000, &t2) == 0, "second thread block");
    check_tls_vector("second thread", p, &t2);
    first = g_ncalls;
    fnvnx_modules_thread_attach(&t2);
    {
        static const want_call w[] = {{3, "main", 2, 0}, {4, "cb", 2, 0}, {4, "main", 2, 0}, {1, "cb", 2, 0}};
        check_calls("thread attach", p, first, w, 4, &t2, t2.stack_base);
    }
    first = g_ncalls;
    fnvnx_modules_thread_detach(&t2);
    {
        static const want_call w[] = {{1, "cb", 3, 0}, {4, "cb", 3, 0}, {4, "main", 3, 0}, {3, "main", 3, 0}};
        check_calls("thread detach", p, first, w, 4, &t2, t2.stack_base);
    }
    first = g_ncalls;
    fnvnx_modules_process_detach(p, p->main.teb_va, p->main.stack_base, 0x77);
    {
        static const want_call w[] = {{1, "cb", 0, 0x77}, {4, "cb", 0, 0x77}, {4, "main", 0, 0x77}, {3, "main", 0, 0x77}};
        check_calls("process detach", p, first, w, 4, &p->main, p->main.stack_base);
    }
    /* DisableThreadLibraryCalls: module 4 drops out of thread attach/detach (its TLS callback too), not of process detach; the exe cannot be disabled */
    CHECK(fnvnx_modules_disable_thread_calls(base_of(4)) == 0 && fnvnx_modules_disable_thread_calls(base_of(1)) != 0 &&
          fnvnx_modules_disable_thread_calls(0x00010000u) != 0, "disable thread calls");
    first = g_ncalls;
    fnvnx_modules_thread_attach(&t2);
    {
        static const want_call w[] = {{3, "main", 2, 0}, {1, "cb", 2, 0}};
        check_calls("thread attach, 4 disabled", p, first, w, 2, &t2, t2.stack_base);
    }
    first = g_ncalls;
    fnvnx_modules_thread_detach(&t2);
    {
        static const want_call w[] = {{1, "cb", 3, 0}, {3, "main", 3, 0}};
        check_calls("thread detach, 4 disabled", p, first, w, 2, &t2, t2.stack_base);
    }
    first = g_ncalls;
    fnvnx_modules_process_detach(p, t2.teb_va, t2.stack_base, 0x78);
    {
        static const want_call w[] = {{1, "cb", 0, 0x78}, {4, "cb", 0, 0x78}, {4, "main", 0, 0x78}, {3, "main", 0, 0x78}};
        check_calls("process detach, 4 disabled", p, first, w, 4, &t2, t2.stack_base);
    }
    CHECK(fnvnx_thread_block_free(p, &t2) == 0, "free second block");

    /* on demand: an allocation the host puts inside the range of a not-yet-loaded module is refused */
    {
        fnvnx_thread_block bad; uint32_t va = sm_table[3].base + 0x1000;
        int before = g_force_va_calls;
        g_force_va = va;
        r = fnvnx_thread_block_create(p, 0x8000, &bad);
        CHECK(r != 0 && g_force_va_calls > before && logged("guest memory request") && bad.teb_va == 0, "alloc inside d.dll's range refused (rc %d)", r);
        g_force_va = 0;
        for (i = 0; i < 16; i++) if (g_pt[(va >> 12) + i].mapped) (void)h_free_va(NULL, va + 0x1000u * i, 0x1000);
        CHECK(fnvnx_address_reserved(p, sm_table[3].base, 1) && fnvnx_address_reserved(p, sm_table[4].base + sm_table[4].size_of_image - 1, 1) &&
              !fnvnx_address_reserved(p, sm_table[4].base + sm_table[4].size_of_image, 0x1000) && fnvnx_address_reserved(p, 0x000f0000u, 1) &&
              fnvnx_address_reserved(p, EXE_BASE, 1) && !fnvnx_address_reserved(p, 0x30000000u, 0x1000) && fnvnx_address_reserved(p, 0xfffff000u, 0x2000),
              "reserved set: unloaded rows, exe, keys, top of the address space");
    }
    /* LoadLibrary("D.DLL"): E first (no entry), then D with reserved = 0 */
    esp = p->main.stack_base - 0x400;
    first = g_ncalls;
    r = fnvnx_module_load(p, p->main.teb_va, esp, "D.DLL", &base);
    CHECK(r == 0 && base == sm_table[3].base, "load d.dll: rc %d base %08x", r, (unsigned)base);
    {
        static const want_call w[] = {{5, "main", 1, 0}};
        check_calls("load", p, first, w, 1, &p->main, esp);
    }
    {   /* d and e are mapped as expected, relocated, bound (d imports e.dll!efun and c.dll!cfun) */
        expect e; blob *bd = g_f[4];
        build_expect(&e, 4, bd);
        {   /* the last import slot of d.dll (image 4) */
            unsigned s, last = 0; for (s = 0; s < SM_NSLOTS; s++) if (sm_slots[s].img == 4) last = s;
            CHECK(!memcmp(g_res + e.base, e.img, 0x1000) && rd32(g_res + sm_slots[last].slot) == sm_slots[last].value, "d.dll header page and import value");
        }
        free(e.img); free(e.prot);
        check_image("load", 4, g_f[4]); check_image("load", 5, g_f[5]);
    }
    CHECK(p->ninit == 5 && p->init_order[3] == 4 && p->init_order[4] == 3 && p->mod[3].state == FNVNX_MOD_READY && p->mod[4].state == FNVNX_MOD_READY, "e.dll initialised before d.dll");
    first = g_ncalls;
    CHECK(fnvnx_module_load(p, p->main.teb_va, esp, "d.dll", &base) == 0 && base == sm_table[3].base && g_ncalls == first, "second load: same base, no new call");
    CHECK(fnvnx_module_load(p, p->main.teb_va, esp, "nosuch.dll", &base) == FNVNX_E_ARG && logged("not a module of the table"), "unknown module refused");
    CHECK(fnvnx_images_count(p) == 6, "images after load");
    CHECK(rd32(g_res + sm_images[0].index_va) == 0 && p->tls_slots == 2, "TLS slots unchanged by an on-demand load");
out:
    plat_down(); free(p);
}

/* every failure leaves the reason in the log and the right code; `what` names the case */
static void failing(void) {
    fnvnx_proc *p = (fnvnx_proc *)calloc(1, sizeof *p);
    unsigned first; int r; uint32_t base;
    /* a hash that is not the file's */
    world(); plat_up(); g_tab[0].sha256[3] ^= 1;
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_IDENTITY && logged("identity mismatch: module c.dll") && mapped_pages_in(sm_table[0].base, sm_table[0].base + 0x5000) == 0, "bad module hash: rc %d", r);
    plat_down();
    /* a module file that does not exist */
    world(); plat_up(); g_f[2] = NULL;
    r = start(p, NULL);
    CHECK(r == FNVNX_E_IO && logged("cannot read module b.dll") && !logged("imports bound"), "missing module file: rc %d", r);
    plat_down();
    /* a file longer than the table says */
    world(); plat_up(); variant(0, "c_long.dll"); memcpy(g_tab[0].sha256, sm_table[0].sha256, 32); g_tab[0].file_size = sm_table[0].file_size;
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_IDENTITY && logged("longer than the table says"), "file longer than its table entry: rc %d", r);
    plat_down();
    /* an unknown relocation type */
    world(); plat_up(); variant(2, "a_badreloc.dll");
    r = start(p, NULL);
    CHECK(r == FNVNX_E_UNSUPPORTED && logged("relocating a.dll"), "unknown relocation type: rc %d", r);
    plat_down();
    /* a module that needs relocating and has no relocation directory */
    world(); plat_up(); variant(2, "a_noreloc.dll"); g_tab[2].size_of_image = 0x5000;
    r = start(p, NULL);
    CHECK(r == FNVNX_E_UNSUPPORTED && logged("relocating a.dll"), "no relocation directory: rc %d", r);
    plat_down();
    /* overlapping bases (two modules; a module and the exe) */
    world(); plat_up(); g_tab[1].base = g_tab[2].base;
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_STATE && logged("module table entry"), "overlapping module bases: rc %d", r);
    plat_down();
    world(); plat_up(); g_tab[0].base = EXE_BASE;
    r = start(p, NULL);
    CHECK(r == FNVNX_E_RANGE && logged("not usable"), "module over the exe: rc %d", r);
    plat_down();
    world(); plat_up(); g_tab[3].base = FNVNX_START_RESERVED_LO & ~0xffffu;   /* an on-demand row in the reserved range */
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_STATE && logged("module table entry"), "module row in the key range: rc %d", r);
    plat_down();
    world(); plat_up(); g_tab[0].name = "C.DLL";
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_STATE, "table name not in normal form: rc %d", r);
    plat_down();
    /* an export that is not there, listing every slot without provider */
    world(); plat_up(); variant(1, "b_noexp.dll");
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_IMPORTS && logged_n("no provider for b.dll!bfun") == 1 && !logged("imports bound") && g_ncalls == 0, "missing export: rc %d (%u listed)", r, logged_n("no provider"));
    CHECK(rd32(g_res + sm_slots[7].slot) != 0, "slot left alone");
    plat_down();
    /* a forwarder to a missing name, and a forwarder cycle: no provider (the exe's and A's slot), no hang */
    world(); plat_up(); variant(1, "b_badfwd.dll");
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_IMPORTS && logged_n("no provider for b.dll!bfwd") == 2, "forwarder to a missing export: rc %d (%u)", r, logged_n("no provider for b.dll!bfwd"));
    plat_down();
    world(); plat_up(); variant(1, "b_cycle.dll");
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_IMPORTS && logged_n("no provider for b.dll!bfwd") == 2, "forwarder cycle: rc %d", r);
    plat_down();
    /* a platform slot without provider in the exe and in two modules: all are listed */
    world(); plat_up(); g_deny[0] = "KERNEL32.dll!Sleep"; g_ndeny = 1;
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_IMPORTS && logged_n("no provider for KERNEL32.dll!Sleep") == 3, "platform slots without provider: rc %d (%u)", r, logged_n("no provider for KERNEL32"));
    plat_down();
    /* DllMain returns FALSE: the sequence stops there, enter refuses */
    world(); plat_up();
    r = start(p, NULL);
    CHECK(r == 0, "prepare for the DllMain refusal: %d", r);
    if (!r) {
        g_fail_eip = p->mod[1].entry_va; g_ncalls = 0;
        CHECK(fnvnx_start_init_modules(p) == FNVNX_START_E_DLLINIT && g_ncalls == 1 && logged("DllMain of b.dll returned FALSE") && p->ninit == 1, "DllMain FALSE: calls %u", g_ncalls);
        CHECK(fnvnx_start_enter(p) == FNVNX_START_EXIT_DLLINIT && g_ncalls == 1, "enter after a refusal makes no call (calls %u)", g_ncalls);
    }
    plat_down();
    /* on demand: a module with a TLS directory is refused; DllMain FALSE fails the load, the dependency stays ready */
    world(); plat_up(); variant(3, "d_tls.dll");
    r = start(p, NULL);
    if (!r && !(r = fnvnx_start_init_modules(p))) {
        r = fnvnx_module_load(p, p->main.teb_va, p->main.stack_base - 0x400, "d.dll", &base);
        CHECK(r == FNVNX_E_UNSUPPORTED && logged("has a TLS directory") && p->mod[3].state == FNVNX_MOD_FAILED, "on-demand module with TLS: rc %d", r);
    } else CHECK(0, "prepare for the TLS case: %d", r);
    plat_down();
    world(); plat_up();
    r = start(p, NULL);
    if (!r && !(r = fnvnx_start_init_modules(p))) {
        g_fail_eip = sm_images[4].entry; first = g_ncalls;
        r = fnvnx_module_load(p, p->main.teb_va, p->main.stack_base - 0x400, "d.dll", &base);
        CHECK(r == FNVNX_START_E_DLLINIT && g_ncalls == first + 1 && p->mod[4].state == FNVNX_MOD_READY && p->mod[3].state == FNVNX_MOD_FAILED && p->ninit == 4, "on-demand DllMain FALSE: rc %d", r);
        first = g_ncalls;
        CHECK(fnvnx_module_load(p, p->main.teb_va, p->main.stack_base - 0x400, "d.dll", &base) == FNVNX_START_E_DLLINIT && g_ncalls == first, "failed module stays failed");
    } else CHECK(0, "prepare for the on-demand DllMain case: %d", r);
    plat_down();
    /* an on-demand load whose import has no provider is refused with the slot listed */
    world(); plat_up();
    r = start(p, NULL);
    if (!r && !(r = fnvnx_start_init_modules(p))) {
        g_deny[0] = "c.dll!cfun"; g_ndeny = 0;   /* c is a module: the deny list is for platform dlls only, so this must not matter */
        r = fnvnx_module_load(p, p->main.teb_va, p->main.stack_base - 0x400, "e.dll", &base);
        CHECK(r == 0 && base == sm_table[4].base, "load of e.dll alone: rc %d", r);
    }
    plat_down();
    /* more modules than the loader's table holds */
    world(); plat_up(); g_ntab = FNVNX_MAX_MODULES + 1;
    r = start(p, NULL);
    CHECK(r == FNVNX_START_E_STATE, "too many modules: rc %d", r);
    plat_down();
    free(p);
}


/* ---- FreeLibrary: fnvnx_module_unload (rule at its declaration in include/fnvnx_start.h) ---- */
static fnvnx_proc *up_world(void) {
    fnvnx_proc *p = (fnvnx_proc *)calloc(1, sizeof *p);
    int r;
    world(); plat_up();
    r = start(p, NULL);
    if (!r) r = fnvnx_start_init_modules(p);
    CHECK(r == 0, "prepare + init for the unload tests: %d", r);
    return p;
}
static unsigned nranges(const fnvnx_proc *p, unsigned k) { return p->mod[k].img.nmapped + p->mod[k].ngap; }
static int gone(unsigned k) { return mapped_pages_in(sm_table[k].base, sm_table[k].base + sm_table[k].size_of_image) == 0; }
static int load(fnvnx_proc *p, const char *n) { uint32_t b; return fnvnx_module_load(p, p->main.teb_va, p->main.stack_base - 0x400, n, &b); }
static int unload(fnvnx_proc *p, uint32_t base) { return fnvnx_module_unload(p, p->main.teb_va, p->main.stack_base - 0x400, base); }
enum { C_ = 0, B_, A_, D_, E_, F_ };   /* table rows */

static void unload_tests(void) {
    fnvnx_proc *p = up_world();
    uint32_t esp = p->main.stack_base - 0x400, dpg = sm_table[D_].base + 0x4000;
    unsigned first, nr_d, nr_e, nr_f, k;
    int r;
    if (!p->modules_ready) { plat_down(); free(p); return; }
    /* refusals: static module (pinned), the exe, nothing loaded there, an address inside a module that is not its base, not loaded yet */
    first = g_ncalls;
    CHECK(unload(p, base_of(2)) == FNVNX_START_E_PINNED && unload(p, base_of(3)) == FNVNX_START_E_PINNED && unload(p, base_of(4)) == FNVNX_START_E_PINNED &&
          g_ncalls == first && p->ninit == 3 && p->mod[0].state == FNVNX_MOD_READY && g_unmaps == 0 && logged("is a static module"), "static modules are pinned");
    CHECK(unload(p, EXE_BASE) == FNVNX_E_ARG && unload(p, 0x00010000u) == FNVNX_E_ARG && unload(p, sm_table[D_].base) == FNVNX_E_ARG && unload(p, 0) == FNVNX_E_ARG &&
          fnvnx_module_unload(NULL, 0, 0, 0) == FNVNX_E_ARG && g_ncalls == first && g_unmaps == 0, "not a loaded module: exe, unmapped address, not loaded yet");
    /* load D (E comes as its dependency, held by nobody): E cannot be freed by the platform, D can */
    CHECK(load(p, "d.dll") == 0 && p->mod[D_].held && !p->mod[E_].held && p->mod[D_].dep_mask == (1u << E_) && p->mod[E_].dep_mask == 0, "load d.dll: held flags and import edges");
    CHECK(unload(p, sm_table[E_].base) == FNVNX_START_E_NOTHELD && unload(p, sm_table[D_].base + 0x1000) == FNVNX_E_ARG && p->mod[E_].state == FNVNX_MOD_READY, "dependency E is not held, an inner address is no module");
    nr_d = nranges(p, D_); nr_e = nranges(p, E_);
    g_res[dpg] = 0xEF; g_res[dpg + 1] = 0xBE; g_res[dpg + 2] = 0xAD; g_res[dpg + 3] = 0xDE;   /* stale data a fresh load must not see */
    first = g_ncalls;
    r = unload(p, sm_table[D_].base);
    CHECK(r == 0, "unload d.dll: %d", r);
    {
        static const want_call w[] = {{5, "main", 0, 0}};   /* DllMain(base, DLL_PROCESS_DETACH, 0); E has no entry point */
        check_calls("unload d", p, first, w, 1, &p->main, esp);
    }
    CHECK(gone(D_) && gone(E_) && p->mod[D_].state == FNVNX_MOD_UNLOADED && p->mod[E_].state == FNVNX_MOD_UNLOADED && p->ninit == 3 &&
          fnvnx_images_count(p) == 4 && g_unmaps == nr_d + nr_e && g_unmap_refused == 0, "d and e: pages released (%u ranges, %u unmaps), image list", nr_d + nr_e, g_unmaps);
    CHECK(!probe_read(g_res + sm_table[D_].base) && !probe_read(g_res + dpg) && !probe_read(g_res + sm_table[E_].base + 0x1000), "released pages are not accessible");
    CHECK(unload(p, sm_table[D_].base) == FNVNX_E_ARG, "second unload of the same base");
    CHECK(h_map(NULL, (uintptr_t)sm_table[D_].base + g_bias, sm_table[D_].size_of_image, 3) == 0 && h_unmap(NULL, (uintptr_t)sm_table[D_].base + g_bias, sm_table[D_].size_of_image) == 0, "the range is free again for map_fixed");
    /* reload: the file is mapped afresh, DllMain attaches again */
    first = g_ncalls;
    CHECK(load(p, "d.dll") == 0 && p->ninit == 5 && p->mod[D_].held, "reload d.dll");
    {
        static const want_call w[] = {{5, "main", 1, 0}};
        check_calls("reload d", p, first, w, 1, &p->main, esp);
    }
    check_image("reload", 4, g_f[4]); check_image("reload", 5, g_f[5]);   /* every byte as the file says: the stale word is gone */
    CHECK(g_res[dpg] == 0 && g_res[dpg + 3] == 0, "fresh data after reload");
    /* F imports D and E: init order E, D, F. D is released by the platform but F keeps it (and E); freeing F then detaches F, D in reverse init order and unloads E */
    first = g_ncalls;
    CHECK(load(p, "f.dll") == 0 && p->mod[F_].dep_mask == ((1u << D_) | (1u << E_)) && p->ninit == 6 && p->init_order[3] == E_ && p->init_order[4] == D_ && p->init_order[5] == F_, "load f.dll: init order e, d, f");
    {
        static const want_call w[] = {{7, "main", 1, 0}};
        check_calls("load f", p, first, w, 1, &p->main, esp);
    }
    first = g_ncalls;
    CHECK(unload(p, sm_table[D_].base) == FNVNX_START_UNLOAD_KEPT && g_ncalls == first && !p->mod[D_].held && p->mod[D_].state == FNVNX_MOD_READY && p->mod[E_].state == FNVNX_MOD_READY, "D released but imported by F: stays, no call");
    check_image("kept", 4, g_f[4]); check_image("kept", 5, g_f[5]); check_image("kept", 6, g_f[6]);
    g_unmaps = 0; nr_d = nranges(p, D_); nr_e = nranges(p, E_); nr_f = nranges(p, F_);
    r = unload(p, sm_table[F_].base);
    CHECK(r == 0, "unload f.dll: %d", r);
    {
        static const want_call w[] = {{7, "main", 0, 0}, {5, "main", 0, 0}};   /* F first, then D: reverse initialisation order */
        check_calls("unload f", p, first, w, 2, &p->main, esp);
    }
    CHECK(gone(D_) && gone(E_) && gone(F_) && p->ninit == 3 && g_unmaps == nr_d + nr_e + nr_f && fnvnx_images_count(p) == 4, "f, d and e are all gone (%u unmaps)", g_unmaps);
    /* E held explicitly first: D imports it. Freeing E keeps it; freeing D then takes both */
    CHECK(load(p, "e.dll") == 0 && load(p, "d.dll") == 0 && p->mod[E_].held && p->mod[D_].held, "load e then d");
    first = g_ncalls;
    CHECK(unload(p, sm_table[E_].base) == FNVNX_START_UNLOAD_KEPT && g_ncalls == first && !p->mod[E_].held && p->mod[E_].state == FNVNX_MOD_READY, "E released, still imported by D");
    r = unload(p, sm_table[D_].base);
    CHECK(r == 0 && g_ncalls == first + 1 && gone(D_) && gone(E_) && p->ninit == 3, "D freed: D detached, E (released earlier, no importer left) unloaded: rc %d", r);
    /* a dependency shared by two loaded modules stays while either is loaded */
    CHECK(load(p, "d.dll") == 0 && load(p, "f.dll") == 0, "load d, f");
    first = g_ncalls;
    r = unload(p, sm_table[F_].base);
    CHECK(r == 0 && g_ncalls == first + 1 && gone(F_) && !gone(D_) && !gone(E_) && p->mod[D_].state == FNVNX_MOD_READY && p->mod[E_].state == FNVNX_MOD_READY && p->ninit == 5,
          "F freed: D (held) and its dependency E stay: rc %d", r);
    check_image("shared", 4, g_f[4]); check_image("shared", 5, g_f[5]);
    CHECK(unload(p, sm_table[D_].base) == 0 && gone(D_) && gone(E_) && p->ninit == 3, "D freed: E goes with it");
    /* a whole chain from nothing: F alone loads D and E; one unload takes all three, detaches F then D */
    first = g_ncalls;
    CHECK(load(p, "f.dll") == 0 && g_ncalls == first + 2 && p->ninit == 6, "load f.dll alone: d then f attach");
    first = g_ncalls;
    r = unload(p, sm_table[F_].base);
    {
        static const want_call w[] = {{7, "main", 0, 0}, {5, "main", 0, 0}};
        check_calls("unload chain", p, first, w, 2, &p->main, esp);
    }
    CHECK(r == 0 && gone(D_) && gone(E_) && gone(F_) && p->ninit == 3 && g_unmap_refused == 0, "chain unloaded: rc %d", r);
    for (k = 0; k < 3; k++) CHECK(p->mod[k].state == FNVNX_MOD_READY, "static module %u untouched", k);
    check_image("static", 1, g_f[1]); check_image("static", 2, g_f[2]); check_image("static", 3, g_f[3]);
    /* the platform cannot give a range back: every range is tried, the module is left failed, the code says so */
    CHECK(load(p, "e.dll") == 0, "load e");
    g_unmaps = 0; nr_e = nranges(p, E_); g_fail_unmap = (uintptr_t)sm_table[E_].base + g_bias;
    r = unload(p, sm_table[E_].base);
    g_fail_unmap = 0;
    CHECK(r == FNVNX_E_MAP && g_unmaps == nr_e - 1 && p->mod[E_].state == FNVNX_MOD_FAILED && logged("could not all be released") && p->ninit == 3, "unmap_fixed failure: rc %d, %u of %u ranges", r, g_unmaps, nr_e);
    plat_down(); free(p);
}

int main(int argc, char **argv) {
    unsigned i;
    if (argc < 2) { fprintf(stderr, "usage: modcheck DIR\n"); return 2; }
    for (i = 0; i < 1 + SM_NMOD; i++) (void)load_blob(argv[1], g_names[i]);
    for (i = 0; i < SM_NVAR; i++) (void)load_blob(argv[1], sm_variants[i].file);
    good_world();
    failing();
    unload_tests();
    printf("modcheck: %d checks passed, %d failed\n", n_ok, n_bad);
    return n_bad ? 1 : 0;
}
