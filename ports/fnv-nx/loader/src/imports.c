/* Import binding (IAT and delay-load IAT) and the loader-structure exclusions used by the code-pointer scanner. */
#include "internal.h"
#include <stdlib.h>

#define DELAY 0x10000u   /* flags passed in the hint argument of thunk_cb */
#define TERM  0x20000u   /* the zero entry that ends a thunk array */

static uint32_t dir_rva(const fnvnx_pe *pe, unsigned d) { return d < pe->ndirs ? pe->dir[d].rva : 0; }
static uint32_t dir_size(const fnvnx_pe *pe, unsigned d) { return d < pe->ndirs ? pe->dir[d].size : 0; }

/* Walks the import descriptors. cb gets (dll, name or NULL, ordinal, hint, iat slot VA, lookup entry VA,
 * hint/name VA or 0, its length); returns non-zero to stop with that error. */
typedef int (*thunk_cb)(void *ctx, const char *dll, const char *name, uint32_t ordinal, unsigned hint,
                        uint32_t slot, uint32_t lookup, uint32_t hn, uint32_t hnlen);
typedef int (*dll_cb)(void *ctx, uint32_t name_va, uint32_t name_len, int delay);

static int walk_imports_x(const fnvnx_image *img, thunk_cb tcb, dll_cb dcb, void *ctx, int with_delay) {
    const fnvnx_pe *pe = img->pe;
    uint32_t base = img->load_base, d, j;
    uint32_t rva = dir_rva(pe, FNVNX_DIR_IMPORT), size = dir_size(pe, FNVNX_DIR_IMPORT);
    int r;
    for (d = 0; size && d + 20 <= size; d += 20) {
        const uint8_t *desc = (const uint8_t *)fnvnx_image_ptr(img, base + rva + d, 20);
        uint32_t oft, name, ft, nlen, lookup;
        const char *dll;
        if (!desc) return FNVNX_E_UNSUPPORTED;
        oft = fnvnx_rd32(desc); name = fnvnx_rd32(desc + 12); ft = fnvnx_rd32(desc + 16);
        if (!oft && !name && !ft) break;
        dll = fnvnx_image_str(img, base + name, 256, &nlen);
        if (!dll || !ft) return FNVNX_E_FORMAT;
        if (dcb && (r = dcb(ctx, base + name, nlen, 0))) return r;
        lookup = oft ? oft : ft;
        for (j = 0;; j++) {
            uint32_t t, hn = 0, hnlen = 0, ord = FNVNX_BY_NAME;
            unsigned hint = 0;
            const char *fn = NULL;
            if (fnvnx_image_read32(img, base + lookup + 4 * j, &t)) return FNVNX_E_FORMAT;
            if (!fnvnx_image_ptr(img, base + ft + 4 * j, 4)) return FNVNX_E_FORMAT;
            if (!t) {   /* report the terminators as well so they are excluded too */
                if ((r = tcb(ctx, dll, NULL, 0, TERM, base + ft + 4 * j, base + lookup + 4 * j, 0, 0))) return r;
                break;
            }
            if (t & 0x80000000u) ord = t & 0xffffu;
            else {
                const uint8_t *h = (const uint8_t *)fnvnx_image_ptr(img, base + t, 2);
                uint32_t len;
                if (!h) return FNVNX_E_FORMAT;
                hint = fnvnx_rd16(h);
                fn = fnvnx_image_str(img, base + t + 2, 512, &len);
                if (!fn) return FNVNX_E_FORMAT;
                hn = base + t; hnlen = 2 + len + 1;
            }
            if ((r = tcb(ctx, dll, fn, ord, hint, base + ft + 4 * j, base + lookup + 4 * j, hn, hnlen))) return r;
        }
    }
    /* delay-load descriptors (ImgDelayDescr, 32 bytes) */
    rva = dir_rva(pe, FNVNX_DIR_DELAY_IMPORT); size = with_delay ? dir_size(pe, FNVNX_DIR_DELAY_IMPORT) : 0;
    for (d = 0; size && d + 32 <= size; d += 32) {
        const uint8_t *desc = (const uint8_t *)fnvnx_image_ptr(img, base + rva + d, 32);
        uint32_t attrs, name, iat, intt, rel, nlen;
        const char *dll;
        if (!desc) return FNVNX_E_UNSUPPORTED;
        attrs = fnvnx_rd32(desc); name = fnvnx_rd32(desc + 4); iat = fnvnx_rd32(desc + 12);
        intt = fnvnx_rd32(desc + 16);
        if (!name) break;
        rel = (attrs & 1) ? base : 0;           /* dlattrRva: fields are RVAs, otherwise VAs */
        dll = fnvnx_image_str(img, rel + name, 256, &nlen);
        if (!dll || !iat || !intt) return FNVNX_E_FORMAT;
        if (dcb && (r = dcb(ctx, rel + name, nlen, 1))) return r;
        for (j = 0;; j++) {
            uint32_t t, hn = 0, hnlen = 0, ord = FNVNX_BY_NAME;
            unsigned hint = 0;
            const char *fn = NULL;
            if (fnvnx_image_read32(img, rel + intt + 4 * j, &t)) return FNVNX_E_FORMAT;
            if (!fnvnx_image_ptr(img, rel + iat + 4 * j, 4)) return FNVNX_E_FORMAT;
            if (!t) {
                if ((r = tcb(ctx, dll, NULL, 0, TERM | DELAY, rel + iat + 4 * j, rel + intt + 4 * j, 0, 0))) return r;
                break;
            }
            if (t & 0x80000000u) ord = t & 0xffffu;
            else {
                const uint8_t *h = (const uint8_t *)fnvnx_image_ptr(img, rel + t, 2);
                uint32_t len;
                if (!h) return FNVNX_E_FORMAT;
                fn = fnvnx_image_str(img, rel + t + 2, 512, &len);
                if (!fn) return FNVNX_E_FORMAT;
                hn = rel + t; hnlen = 2 + len + 1;
            }
            if ((r = tcb(ctx, dll, fn, ord, DELAY | hint, rel + iat + 4 * j, rel + intt + 4 * j, hn, hnlen)))
                return r;
        }
    }
    return FNVNX_OK;
}

static int walk_imports(const fnvnx_image *img, thunk_cb tcb, dll_cb dcb, void *ctx) {
    return walk_imports_x(img, tcb, dcb, ctx, 1);
}

/* ---- binding ---- */

typedef struct bind_ctx {
    fnvnx_image *img;
    fnvnx_import_resolver resolver;
    void *ctx;
    uint32_t unresolved_value;
    fnvnx_import_stats *st;
} bind_ctx;

static int bind_dll(void *c, uint32_t name_va, uint32_t len, int delay) {
    bind_ctx *b = (bind_ctx *)c;
    (void)name_va; (void)len;
    if (delay) b->st->delay_dlls++; else b->st->dlls++;
    return 0;
}

static int bind_thunk(void *c, const char *dll, const char *name, uint32_t ord, unsigned hint, uint32_t slot,
                      uint32_t lookup, uint32_t hn, uint32_t hnlen) {
    bind_ctx *b = (bind_ctx *)c;
    uint32_t v;
    (void)lookup; (void)hn; (void)hnlen;
    if (hint & TERM) return 0;
    v = b->resolver ? b->resolver(b->ctx, dll, name, ord, hint & 0xffffu) : 0;
    if (hint & DELAY) b->st->delay_slots++; else b->st->slots++;
    if (v) b->st->resolved++;
    else { b->st->unresolved++; v = b->unresolved_value; }
    fnvnx_wr32(fnvnx_image_ptr(b->img, slot, 4), v);
    return 0;
}

int fnvnx_imports_bind(fnvnx_image *img, fnvnx_import_resolver resolver, void *ctx, uint32_t unresolved_value,
                       fnvnx_import_stats *stats) {
    fnvnx_import_stats local;
    bind_ctx b;
    int r;
    if (!stats) stats = &local;
    memset(stats, 0, sizeof *stats);
    b.img = img; b.resolver = resolver; b.ctx = ctx; b.unresolved_value = unresolved_value; b.st = stats;
    r = walk_imports(img, bind_thunk, bind_dll, &b);
    return r;
}

/* ---- exclusions ---- */

typedef struct excl_ctx {
    fnvnx_range *r;
    uint32_t n, cap;
    int oom;
} excl_ctx;

static void add(excl_ctx *e, uint32_t lo, uint32_t size) {
    if (!size || e->oom) return;
    if (e->n == e->cap) { e->oom = 1; return; }
    e->r[e->n].lo = lo;
    e->r[e->n].hi = (uint64_t)lo + size > 0xffffffffull ? 0xffffffffu : lo + size;
    e->n++;
}

static int excl_dll(void *c, uint32_t name_va, uint32_t len, int delay) {
    (void)delay;
    add((excl_ctx *)c, name_va, len + 1);
    return 0;
}

static int excl_thunk(void *c, const char *dll, const char *name, uint32_t ord, unsigned hint, uint32_t slot,
                      uint32_t lookup, uint32_t hn, uint32_t hnlen) {
    excl_ctx *e = (excl_ctx *)c;
    (void)dll; (void)name; (void)ord; (void)hint;
    add(e, slot, 4);
    add(e, lookup, 4);
    if (hn) add(e, hn, hnlen);
    return 0;
}

static int cmp_range(const void *a, const void *b) {
    const fnvnx_range *x = (const fnvnx_range *)a, *y = (const fnvnx_range *)b;
    return x->lo < y->lo ? -1 : x->lo > y->lo;
}

static int count_thunk(void *c, const char *dll, const char *name, uint32_t ord, unsigned hint, uint32_t slot,
                       uint32_t lookup, uint32_t hn, uint32_t hnlen) {
    (void)dll; (void)name; (void)ord; (void)hint; (void)slot; (void)lookup; (void)hn; (void)hnlen;
    ++*(uint32_t *)c;
    return 0;
}
static int count_dll(void *c, uint32_t name_va, uint32_t len, int delay) {
    (void)name_va; (void)len; (void)delay;
    ++*(uint32_t *)c;
    return 0;
}

int fnvnx_collect_exclusions(const fnvnx_image *img, const fnvnx_os *os, fnvnx_range **out, uint32_t *n) {
    const fnvnx_pe *pe = img->pe;
    uint32_t base = img->load_base, thunks = 0, i, k;
    excl_ctx e;
    int r;
    *out = NULL; *n = 0;
    r = walk_imports(img, count_thunk, count_dll, &thunks);
    if (r) thunks = 0;                /* malformed imports: only the directories are excluded */
    e.cap = 3 * thunks + 64;
    e.n = 0; e.oom = 0;
    e.r = (fnvnx_range *)os->alloc(os->ctx, e.cap * (uint32_t)sizeof(fnvnx_range));
    if (!e.r) return FNVNX_E_NOMEM;
    for (i = 0; i < pe->ndirs; i++)
        if (i != FNVNX_DIR_SECURITY) add(&e, base + pe->dir[i].rva, pe->dir[i].size);
    if (thunks) walk_imports(img, excl_thunk, excl_dll, &e);
    /* debug directory entries point at their raw data */
    for (k = 0; k + 28 <= dir_size(pe, FNVNX_DIR_DEBUG); k += 28) {
        const uint8_t *d = (const uint8_t *)fnvnx_image_ptr(img, base + dir_rva(pe, FNVNX_DIR_DEBUG) + k, 28);
        if (d && fnvnx_rd32(d + 20)) add(&e, base + fnvnx_rd32(d + 20), fnvnx_rd32(d + 16));
    }
    /* load config: the SafeSEH handler table (RVAs, which look like .text VAs) and a CFG function table */
    {
        uint32_t lsize = dir_size(pe, FNVNX_DIR_LOAD_CONFIG);
        const uint8_t *lc = lsize ? (const uint8_t *)fnvnx_image_ptr(img, base + dir_rva(pe, FNVNX_DIR_LOAD_CONFIG),
                                                                     lsize) : NULL;
        if (lc) {
            uint32_t declared = fnvnx_rd32(lc);   /* Size field */
            if (declared < lsize) lsize = declared;
            if (lsize >= 72 && fnvnx_rd32(lc + 68) < 0x10000000u) add(&e, fnvnx_rd32(lc + 64), 4 * fnvnx_rd32(lc + 68));
            if (lsize >= 92 && fnvnx_rd32(lc + 84) < 0x10000000u) {
                uint32_t stride = 4 + ((fnvnx_rd32(lc + 88) >> 28) & 0xfu);
                add(&e, fnvnx_rd32(lc + 80), stride * fnvnx_rd32(lc + 84));
            }
        }
    }
    if (e.oom) { os->free(os->ctx, e.r); return FNVNX_E_NOMEM; }
    qsort(e.r, e.n, sizeof *e.r, cmp_range);
    /* merge */
    for (i = 0, k = 0; i < e.n; i++) {
        if (k && e.r[i].lo <= e.r[k - 1].hi) { if (e.r[i].hi > e.r[k - 1].hi) e.r[k - 1].hi = e.r[i].hi; }
        else e.r[k++] = e.r[i];
    }
    *out = e.r; *n = k;
    return FNVNX_OK;
}

/* ---- strict binding (rule S4) ---- */

typedef struct strict_ctx {
    fnvnx_image *img;
    const fnvnx_os *os;
    fnvnx_import_resolve3 resolve;
    void *ctx;
    uint32_t bound, missing;
} strict_ctx;

static int strict_thunk(void *c, const char *dll, const char *name, uint32_t ord, unsigned hint, uint32_t slot,
                        uint32_t lookup, uint32_t hn, uint32_t hnlen) {
    strict_ctx *b = (strict_ctx *)c;
    uint32_t v;
    (void)lookup; (void)hn; (void)hnlen;
    if (hint & TERM) return 0;
    v = b->resolve(b->ctx, dll, name, name ? FNVNX_BY_NAME : ord);
    if (!v) {
        if (name) fnvnx_logf(b->os, "fnvnx-start: no provider for %s!%s", dll, name);
        else fnvnx_logf(b->os, "fnvnx-start: no provider for %s!#%u", dll, (unsigned)ord);
        b->missing++;
        return 0;
    }
    fnvnx_wr32(fnvnx_image_ptr(b->img, slot, 4), v);
    b->bound++;
    return 0;
}

int fnvnx_imports_bind_strict(fnvnx_image *img, const fnvnx_os *os, fnvnx_import_resolve3 resolve, void *ctx,
                              uint32_t *bound) {
    strict_ctx b;
    int r;
    if (!img || !resolve) return FNVNX_E_ARG;
    memset(&b, 0, sizeof b);
    b.img = img; b.os = os; b.resolve = resolve; b.ctx = ctx;
    r = walk_imports_x(img, strict_thunk, NULL, &b, 0);
    if (bound) *bound = b.bound;
    if (r) return r;
    return b.missing ? FNVNX_E_MISMATCH : FNVNX_OK;
}
