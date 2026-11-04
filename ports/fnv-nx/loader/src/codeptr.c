/* Code-pointer discovery and rewriting (docs/DATAIMAGE.md "Finding code pointers").
 * loader/tools/scan_codeptrs.py implements the same algorithm; tests/s3 checks that both give identical lists. */
#include "internal.h"

/* ---- address map ---- */

int fnvnx_codemap_from_blob(fnvnx_codemap *map, const void *blob, uint32_t size) {
    const uint8_t *b = (const uint8_t *)blob;
    uint32_t n, i;
    map->e = NULL; map->n = 0;
    if (!blob || size < 24 || ((uintptr_t)blob & 3) || memcmp(b, FNVNX_ADDRMAP_MAGIC, 8)) return FNVNX_E_FORMAT;
    if (fnvnx_rd32(b + 8) != 1 || fnvnx_rd32(b + 16) != sizeof(fnvnx_codemap_entry)) return FNVNX_E_UNSUPPORTED;
    n = fnvnx_rd32(b + 12);
    if (n > (size - 24) / 12) return FNVNX_E_FORMAT;
    map->e = (const fnvnx_codemap_entry *)(const void *)(b + 24);
    for (i = 1; i < n; i++)
        if (map->e[i].orig <= map->e[i - 1].orig) { map->e = NULL; return FNVNX_E_FORMAT; }
    map->n = n;
    return FNVNX_OK;
}

const fnvnx_codemap_entry *fnvnx_codemap_find(const fnvnx_codemap *map, uint32_t orig) {
    uint32_t lo = 0, hi = map ? map->n : 0;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (map->e[mid].orig < orig) lo = mid + 1;
        else hi = mid;
    }
    return (map && lo < map->n && map->e[lo].orig == orig) ? &map->e[lo] : NULL;
}

int fnvnx_codemap_from_s2(fnvnx_codemap *map, const fnvnx_os *os, const fnvnx_s2_addrmap_entry *s2, uint32_t n,
                          const char *kinds, const fnvnx_codemap_entry *extra, uint32_t nextra) {
    fnvnx_codemap_entry *e;
    uint32_t i = 0, j = 0, k = 0;
    map->e = NULL; map->n = 0;
    if (!os || !os->alloc || (n && !s2) || (nextra && !extra) || (uint64_t)n + nextra > 0x0fffffffu) return FNVNX_E_ARG;
    e = (fnvnx_codemap_entry *)os->alloc(os->ctx, (n + nextra ? n + nextra : 1) * (uint32_t)sizeof *e);
    if (!e) return FNVNX_E_NOMEM;
    while (i < n || j < nextra) {
        if (i < n && (j >= nextra || s2[i].orig <= extra[j].orig)) {
            uint32_t info = FNVNX_AM_FUNC;
            if (kinds && kinds[i] == 'G') info |= FNVNX_AM_F_DELDTOR;
            else if (kinds && kinds[i] == 'V') info = FNVNX_AM_VTABLE;    /* image vftable -> native address point */
            else if (kinds && kinds[i] == 'E') info = FNVNX_AM_TEXT;      /* referenced .text, no native */
            if (j < nextra && extra[j].orig == s2[i].orig) j++;     /* S2 wins */
            e[k].orig = s2[i].orig;
            e[k].native = (uint32_t)(uintptr_t)s2[i].native;
            e[k].info = info;
            i++;
        } else {
            e[k] = extra[j++];
        }
        if (k && e[k].orig <= e[k - 1].orig) { os->free(os->ctx, e); return FNVNX_E_FORMAT; }   /* not sorted */
        k++;
    }
    map->e = e;
    map->n = k;
    return FNVNX_OK;
}

/* ---- scanner ---- */

typedef struct vtab { uint32_t start, end; uint8_t klass; } vtab;

/* Two-level lookup over records whose first u32 is a strictly increasing key (the code map, the vftable starts):
 * buckets are address pages of 1 << shift bytes from `lo`; first[b] is the index of the first record in bucket b or
 * later (first[nb]: the first record past the indexed pages, searched as one range). Exact-key answers are those
 * of a binary search over the whole array; without first[] (one record, unsorted keys, no memory) it is one.
 * (S15, J-0076: a 17-step search of the code map for every data word was most of the boot.) */
typedef struct keyidx {
    const uint8_t *base;
    uint32_t stride, n;
    uint32_t lo, shift, nb;
    uint32_t *first;
} keyidx;

static uint32_t kx_key(const keyidx *x, uint32_t i) {
    return *(const uint32_t *)(const void *)(x->base + (size_t)i * x->stride);
}

static void kx_build(keyidx *x, const fnvnx_os *os, const void *base, uint32_t stride, uint32_t n) {
    uint32_t i, b, last;
    x->base = (const uint8_t *)base; x->stride = stride; x->n = n;
    x->lo = x->shift = x->nb = 0; x->first = NULL;
    if (n < 2) return;
    for (i = 1; i < n; i++) if (kx_key(x, i) <= kx_key(x, i - 1)) return;      /* binary-search semantics only */
    last = kx_key(x, n - 1);
    for (x->shift = 8;; x->shift++) {                                           /* at most 64 Ki pages */
        x->lo = kx_key(x, 0) & ~((1u << x->shift) - 1u);
        if (((last - x->lo) >> x->shift) < 0x10000u) break;
    }
    x->nb = ((last - x->lo) >> x->shift) + 1;
    x->first = (uint32_t *)os->alloc(os->ctx, (x->nb + 1) * (uint32_t)sizeof(uint32_t));
    if (!x->first) { x->nb = 0; return; }
    for (i = 0, b = 0; b <= x->nb; b++) {
        while (i < n && ((kx_key(x, i) - x->lo) >> x->shift) < b) i++;
        x->first[b] = i;
    }
}

static int32_t kx_find(const keyidx *x, uint32_t k) {   /* index of the record with key k, or -1 */
    uint32_t lo = 0, hi = x->n;
    if (x->first) {
        uint32_t b;
        if (k < x->lo) return -1;
        b = (k - x->lo) >> x->shift;
        if (b < x->nb) { lo = x->first[b]; hi = x->first[b + 1]; }
        else lo = x->first[x->nb];
    }
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (kx_key(x, mid) < k) lo = mid + 1;
        else hi = mid;
    }
    return (lo < x->n && kx_key(x, lo) == k) ? (int32_t)lo : -1;
}

typedef struct scan {
    const fnvnx_image *img;
    const fnvnx_codemap *map;
    fnvnx_range *excl;
    uint32_t nexcl;
    int relmode;
    uint32_t text_lo, text_hi;     /* current VAs */
    vtab *vt;
    uint32_t nvt;
    uint32_t tls[64];
    uint32_t ntls;
    /* fast paths (same answers as the searches they replace) */
    keyidx cmx;                    /* the code map */
    keyidx vtx;                    /* vftable starts (only when every vftable is non-empty and sorted) */
    int vt_cursor;                 /* the vftables are sorted, non-empty and disjoint: a running cursor finds w's */
    int zero_not_text;             /* 0 is no .text address: a zero word is never a vftable slot */
    uint32_t *bitmem;              /* eligibility bitmaps, one per mapping (NULL: eligible_slow) */
    const uint32_t *bits[FNVNX_MAX_SECTIONS + 1];
    uint32_t nw[FNVNX_MAX_SECTIONS + 1];
} scan;

static int is_text(const scan *s, uint32_t v) { return v >= s->text_lo && v < s->text_hi; }

static int excluded(const scan *s, uint32_t w) {
    uint32_t lo = 0, hi = s->nexcl;
    while (lo < hi) {          /* first range with hi > w */
        uint32_t mid = lo + (hi - lo) / 2;
        if (s->excl[mid].hi <= w) lo = mid + 1;
        else hi = mid;
    }
    return lo < s->nexcl && s->excl[lo].lo < w + 4;
}

static int is_reloc(const scan *s, uint32_t w) {
    uint32_t lo = 0, hi = s->img->nrelocs;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (s->img->relocs[mid] < w) lo = mid + 1;
        else hi = mid;
    }
    return lo < s->img->nrelocs && s->img->relocs[lo] == w;
}

/* the data mapping that holds w, or NULL (headers never count) */
static const fnvnx_mapping *mapping_of(const scan *s, uint32_t w) {
    unsigned i;
    for (i = 0; i < s->img->nmapped; i++) {
        const fnvnx_mapping *m = &s->img->map[i];
        if (m->sec != 0xffff && w >= m->va && w - m->va < m->size) return m;
    }
    return NULL;
}

static int eligible_slow(const scan *s, const fnvnx_mapping *m, uint32_t w) {
    if (w < m->va || w - m->va > m->size - 4) return 0;
    if (excluded(s, w)) return 0;
    return !s->relmode || is_reloc(s, w);
}

/* m is one of s->img->map[] */
static int eligible_in(const scan *s, const fnvnx_mapping *m, uint32_t w) {
    unsigned mi = (unsigned)(m - s->img->map);
    const uint32_t *b = mi <= FNVNX_MAX_SECTIONS ? s->bits[mi] : NULL;
    uint32_t k;
    if (!b || ((w - m->va) & 3u)) return eligible_slow(s, m, w);
    if (w < m->va) return 0;
    k = (w - m->va) >> 2;
    return k < s->nw[mi] && ((b[k >> 5] >> (k & 31u)) & 1u);
}

/* Per mapping, bit k = eligible_slow(va + 4k): in bounds, overlapping no exclusion range, a relocation in .reloc
 * mode. Exclusions come sorted and merged (fnvnx_collect_exclusions), so w is excluded exactly when some range has
 * lo < w + 4 and hi > w; checked, else (and for odd mappings, or without memory) eligible_slow answers. */
static void build_eligibility(scan *s, const fnvnx_os *os) {
    const fnvnx_image *img = s->img;
    uint32_t i, j, total = 0, off;
    for (j = 1; j < s->nexcl; j++)
        if (s->excl[j].lo < s->excl[j - 1].lo || s->excl[j].hi < s->excl[j - 1].hi) return;
    if (s->relmode)
        for (j = 1; j < img->nrelocs; j++) if (img->relocs[j] < img->relocs[j - 1]) return;
    for (i = 0; i < img->nmapped && i <= FNVNX_MAX_SECTIONS; i++) {
        const fnvnx_mapping *m = &img->map[i];
        if (m->sec == 0xffff || m->size < 4 || (uint64_t)m->va + m->size > 0xfffffff0ull) continue;
        total += ((m->size - 4) / 4 + 1 + 31) / 32 * (s->relmode ? 2u : 1u);
    }
    if (!total || !(s->bitmem = (uint32_t *)os->alloc(os->ctx, total * (uint32_t)sizeof(uint32_t)))) return;
    for (i = 0, off = 0; i < img->nmapped && i <= FNVNX_MAX_SECTIONS; i++) {
        const fnvnx_mapping *m = &img->map[i];
        uint32_t nw, ng, k, *b;
        if (m->sec == 0xffff || m->size < 4 || (uint64_t)m->va + m->size > 0xfffffff0ull) continue;
        nw = (m->size - 4) / 4 + 1;
        ng = (nw + 31) / 32;
        b = s->bitmem + off;
        off += ng * (s->relmode ? 2u : 1u);
        for (k = 0; k < ng; k++) b[k] = 0xffffffffu;
        if (nw & 31u) b[ng - 1] = (1u << (nw & 31u)) - 1u;
        for (j = 0; j < s->nexcl; j++) {
            int64_t d = (int64_t)s->excl[j].lo - m->va - 4, e = (int64_t)s->excl[j].hi - m->va;
            uint64_t k0 = d < 0 ? 0 : (uint64_t)(d / 4) + 1, k1 = e <= 0 ? 0 : ((uint64_t)e + 3) / 4;
            if (k1 > nw) k1 = nw;
            for (k = (uint32_t)k0; k < k1; k++) {
                if (!(k & 31u) && k + 32 <= k1) { b[k >> 5] = 0; k += 31; continue; }
                b[k >> 5] &= ~(1u << (k & 31u));
            }
        }
        if (s->relmode) {
            uint32_t *rb = b + ng;
            for (k = 0; k < ng; k++) rb[k] = 0;
            for (j = 0; j < img->nrelocs; j++) {
                uint32_t r = img->relocs[j];
                if (r >= m->va && !((r - m->va) & 3u) && (r - m->va) / 4 < nw)
                    rb[(r - m->va) / 128] |= 1u << (((r - m->va) / 4) & 31u);
            }
            for (k = 0; k < ng; k++) b[k] &= rb[k];
        }
        s->bits[i] = b;
        s->nw[i] = nw;
    }
}

static uint32_t word(const scan *s, uint32_t w) { return fnvnx_rd32((const void *)((uintptr_t)w + s->img->bias)); }

/* the 128 bytes at va (inside one mapping) are all zero */
static int zero128(const scan *s, uint32_t va) {
    const uint8_t *p = (const uint8_t *)((uintptr_t)va + s->img->bias);
    uint64_t x = 0, y;
    unsigned i;
    for (i = 0; i < 128; i += 8) { memcpy(&y, p + i, 8); x |= y; }
    return !x;
}

static int printable_name(const char *p, uint32_t n) {
    uint32_t i;
    for (i = 0; i < n; i++) if ((unsigned char)p[i] < 0x21 || (unsigned char)p[i] > 0x7e) return 0;
    return 1;
}

/* MSVC x86 RTTI: CompleteObjectLocator {sig 0, offset, cdOffset, TypeDescriptor*, ClassHierarchyDescriptor*},
 * TypeDescriptor {type_info vftable*, spare, ".?AV...@@"}, CHD {sig 0, attributes, numBaseClasses, BCA*} */
static int valid_col(const scan *s, uint32_t c) {
    const fnvnx_image *img = s->img;
    const uint8_t *p = (const uint8_t *)fnvnx_image_ptr(img, c, 20), *q;
    const char *name;
    uint32_t td, chd, nb, len;
    if (!p || (c & 3)) return 0;
    if (fnvnx_rd32(p) != 0 || (fnvnx_rd32(p + 4) & 3) || fnvnx_rd32(p + 4) >= 0x100000u ||
        fnvnx_rd32(p + 8) >= 0x10000u)
        return 0;
    td = fnvnx_rd32(p + 12);
    chd = fnvnx_rd32(p + 16);
    q = (const uint8_t *)fnvnx_image_ptr(img, td, 12);
    if (!q || (td & 3) || !fnvnx_image_ptr(img, fnvnx_rd32(q), 4)) return 0;
    name = fnvnx_image_str(img, td + 8, 512, &len);
    if (!name || len < 4 || memcmp(name, ".?A", 3) || !printable_name(name, len)) return 0;
    q = (const uint8_t *)fnvnx_image_ptr(img, chd, 16);
    if (!q || (chd & 3) || fnvnx_rd32(q) != 0 || fnvnx_rd32(q + 4) >= 16) return 0;
    nb = fnvnx_rd32(q + 8);
    if (nb < 1 || nb > 4096 || !fnvnx_image_ptr(img, fnvnx_rd32(q + 12), 4 * nb)) return 0;
    return 1;
}

static uint32_t vt_lower(const scan *s, uint32_t w) {   /* index of the first vftable with end > w */
    uint32_t lo = 0, hi = s->nvt;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (s->vt[mid].end <= w) lo = mid + 1;
        else hi = mid;
    }
    return lo;
}

static int vt_find(const scan *s, uint32_t w) {   /* index of the vftable containing w, or -1 */
    uint32_t lo = vt_lower(s, w);
    return (lo < s->nvt && s->vt[lo].start <= w) ? (int)lo : -1;
}

static int vt_is_start(const scan *s, uint32_t v) {
    int i;
    if (s->vtx.n) return kx_find(&s->vtx, v) >= 0;
    i = vt_find(s, v);
    return i >= 0 && s->vt[i].start == v;
}

static int tls_index(const scan *s, uint32_t w) {
    uint32_t i;
    for (i = 0; i < s->ntls; i++) if (s->tls[i] == w) return (int)i;
    return -1;
}

static const fnvnx_codemap_entry *cm_find(const scan *s, uint32_t orig) {
    int32_t i;
    if (!s->cmx.n) return fnvnx_codemap_find(s->map, orig);
    i = kx_find(&s->cmx, orig);
    return i >= 0 ? &s->map->e[i] : NULL;
}

static const fnvnx_codemap_entry *known(const scan *s, uint32_t v) {
    const fnvnx_codemap_entry *e;
    if (!is_text(s, v)) return NULL;
    e = cm_find(s, v - s->img->delta);
    return (e && FNVNX_AM_KIND(e->info) != FNVNX_AM_MID) ? e : NULL;
}

/* a neighbour that is itself a plain known code pointer */
static int known_at(const scan *s, const fnvnx_mapping *m, uint32_t x) {
    if (!eligible_in(s, m, x) || vt_find(s, x) >= 0 || tls_index(s, x) >= 0) return 0;
    return known(s, word(s, x)) != NULL;
}

static int pr(int c) { return c >= 0x20 && c < 0x7f; }

static int string_tail(const scan *s, const fnvnx_mapping *m, uint32_t w) {
    const uint8_t *b = (const uint8_t *)((uintptr_t)w + s->img->bias);
    int prev = w > m->va ? b[-1] : -1, prev2 = w - m->va >= 2 ? b[-2] : -1;
    if (pr(b[0]) && pr(b[1]) && pr(b[2]) && b[3] == 0 && prev >= 0 && pr(prev)) return 1;
    if (pr(b[0]) && b[1] == 0 && pr(b[2]) && b[3] == 0 && prev == 0 && prev2 >= 0 && pr(prev2)) return 1;
    return 0;
}

/* collect vftable starts: COL-validated (one pass, the list grows by doubling) merged with hints */
static int find_vtables(scan *s, const fnvnx_os *os, const fnvnx_scan_opts *opts) {
    const fnvnx_image *img = s->img;
    uint32_t n = 0, ncap = 0, i, h, k;
    vtab *col = NULL;
    for (i = 0; i < img->nmapped; i++) {
        const fnvnx_mapping *m = &img->map[i];
        uint32_t w;
        if (m->sec == 0xffff) continue;
        uint64_t end = (uint64_t)m->va + m->size;
        for (w = m->va; (uint64_t)w + 8 <= end; w += 4) {
            uint32_t c;
            /* 32 words whose next word is 0 (not .text) at once: none of them starts a vftable */
            if (s->zero_not_text && !((w - m->va) & 127u) && (uint64_t)w + 132 <= end && zero128(s, w + 4)) {
                w += 124;
                continue;
            }
            /* cheapest test first: the slot after a COL word holds a .text address */
            if (!is_text(s, word(s, w + 4)) || !eligible_in(s, m, w) || !eligible_in(s, m, w + 4)) continue;
            c = word(s, w);
            if (!fnvnx_image_ptr(img, c, 20) || !valid_col(s, c)) continue;
            if (n == ncap) {
                uint32_t nc = ncap ? 2 * ncap : 256;
                vtab *g = nc > 0x10000000u / (uint32_t)sizeof(vtab) ? NULL :
                          (vtab *)os->alloc(os->ctx, nc * (uint32_t)sizeof(vtab));
                if (!g) { if (col) os->free(os->ctx, col); return FNVNX_E_NOMEM; }
                if (n) memcpy(g, col, n * sizeof(vtab));
                if (col) os->free(os->ctx, col);
                col = g; ncap = nc;
            }
            col[n].start = w + 4; col[n].klass = FNVNX_FX_VTABLE_COL;
            n++;
        }
    }
    if (!n && !(opts && opts->nhints)) return FNVNX_OK;
    /* merge in the hints that are not COL starts (both lists sorted) */
    s->vt = col;
    s->nvt = n;
    if (opts && opts->nhints) {
        vtab *all = (vtab *)os->alloc(os->ctx, (n + opts->nhints) * (uint32_t)sizeof(vtab));
        uint32_t a = 0, out = 0;
        if (!all) return FNVNX_E_NOMEM;
        for (h = 0; h <= opts->nhints; h++) {
            uint32_t v = 0;
            int have = h < opts->nhints;
            if (have) {
                const fnvnx_mapping *m;
                v = opts->vtable_hints[h] + img->delta;
                m = mapping_of(s, v);
                if (h && opts->vtable_hints[h] <= opts->vtable_hints[h - 1]) have = 0;   /* not sorted/unique */
                else if (!m || (v & 3) || !eligible_in(s, m, v) || !is_text(s, word(s, v))) have = 0;
            }
            while (a < n && (h == opts->nhints || col[a].start <= v)) {
                if (have && col[a].start == v) have = 0;
                all[out++] = col[a++];
            }
            if (have) { all[out].start = v; all[out].klass = FNVNX_FX_VTABLE_HINT; out++; }
        }
        if (col) os->free(os->ctx, col);
        s->vt = all;
        s->nvt = out;
    }
    /* extend every start until a non-code word, an ineligible word or the next start */
    for (k = 0; k < s->nvt; k++) {
        const fnvnx_mapping *m = mapping_of(s, s->vt[k].start);
        uint32_t w = s->vt[k].start, stop = k + 1 < s->nvt ? s->vt[k + 1].start : 0xffffffffu;
        while (w < stop && eligible_in(s, m, w) && is_text(s, word(s, w))) w += 4;
        s->vt[k].end = w;
    }
    return FNVNX_OK;
}

static void find_tls(scan *s) {
    const fnvnx_image *img = s->img;
    const fnvnx_pe *pe = img->pe;
    const uint8_t *t;
    uint32_t cb, i;
    s->ntls = 0;
    if (pe->ndirs <= FNVNX_DIR_TLS || pe->dir[FNVNX_DIR_TLS].size < 24) return;
    t = (const uint8_t *)fnvnx_image_ptr(img, img->load_base + pe->dir[FNVNX_DIR_TLS].rva, 24);
    if (!t || !(cb = fnvnx_rd32(t + 12))) return;
    for (i = 0; i < 64; i++) {
        uint32_t v;
        if (!mapping_of(s, cb + 4 * i) || fnvnx_image_read32(img, cb + 4 * i, &v) || !v) break;
        s->tls[s->ntls++] = cb + 4 * i;
    }
}

typedef struct sink { fnvnx_fixup *out; uint32_t cap, n; fnvnx_scan_stats *stats; } sink;

static void emit(sink *k, const fnvnx_fixup *f) {
    k->stats->by_class[f->klass]++;
    if (k->n < k->cap && k->out) k->out[k->n] = *f;
    k->n++;
}

/* one eligible word w holding v; vi = the vftable containing w or -1 */
static void classify(const scan *s, const fnvnx_mapping *m, uint32_t w, uint32_t v, int vi, sink *k) {
    const fnvnx_image *img = s->img;
    fnvnx_scan_stats *stats = k->stats;
    const fnvnx_codemap_entry *e;
    fnvnx_fixup f;
    uint32_t o;
    int ti;
    if (!is_text(s, v)) {
        if (vi < 0 && v && vt_is_start(s, v)) stats->vptr_refs++;
        e = vi < 0 ? cm_find(s, v - img->delta) : NULL;
        if (!e || FNVNX_AM_KIND(e->info) != FNVNX_AM_VTABLE) return;
        f.loc = w - img->delta; f.target = v - img->delta; f.aux = 0;
        f.klass = FNVNX_FX_VPTR; f.conf = FNVNX_CONF_MED;
        emit(k, &f);
        return;
    }
    stats->textptrs++;
    o = v - img->delta;
    f.loc = w - img->delta; f.target = o; f.aux = 0;
    if (vi >= 0) {
        f.klass = s->vt[vi].klass; f.conf = FNVNX_CONF_HIGH;
        f.aux = (uint16_t)((w - s->vt[vi].start) / 4);
    } else if ((ti = tls_index(s, w)) >= 0) {
        f.klass = FNVNX_FX_TLS_CALLBACK; f.conf = FNVNX_CONF_HIGH; f.aux = (uint16_t)ti;
    } else {
        e = cm_find(s, o);
        if (!e) { f.klass = FNVNX_FX_UNKNOWN; f.conf = FNVNX_CONF_NONE; }
        else if (FNVNX_AM_KIND(e->info) == FNVNX_AM_MID) { f.klass = FNVNX_FX_MID; f.conf = FNVNX_CONF_NONE; }
        else if (s->relmode) { f.klass = FNVNX_FX_RELOC; f.conf = FNVNX_CONF_HIGH; }
        else if (known_at(s, m, w - 4) || known_at(s, m, w + 4)) {
            uint32_t b = 0;
            while (b < 0xffff && known_at(s, m, w - 4 * (b + 1))) b++;
            f.klass = FNVNX_FX_RUN; f.conf = FNVNX_CONF_MED; f.aux = (uint16_t)b;
        } else if (string_tail(s, m, w)) { f.klass = FNVNX_FX_REJ_STRING; f.conf = FNVNX_CONF_NONE; }
        else if (!(o & 0xfffu)) { f.klass = FNVNX_FX_REJ_ROUND; f.conf = FNVNX_CONF_NONE; }
        else { f.klass = FNVNX_FX_ISOLATED; f.conf = FNVNX_CONF_LOW; }
    }
    emit(k, &f);
}

/* the vftables are sorted by start, non-empty and end at or before the next start, so their ends never decrease
 * and a running cursor gives vt_find's answer for increasing w */
static int vt_sorted(const scan *s) {
    uint32_t k;
    for (k = 0; k < s->nvt; k++)
        if (s->vt[k].end <= s->vt[k].start || (k + 1 < s->nvt && s->vt[k].end > s->vt[k + 1].start)) return 0;
    return 1;
}

int fnvnx_scan_codeptrs(const fnvnx_image *img, const fnvnx_os *os, const fnvnx_codemap *map,
                        const fnvnx_scan_opts *opts, fnvnx_fixup *out, uint32_t cap, uint32_t *count,
                        fnvnx_scan_stats *stats) {
    scan s;
    fnvnx_scan_stats local;
    sink sk;
    uint32_t i, k;
    int r, zero_quiet, fast = !(opts && (opts->flags & FNVNX_SCAN_NO_INDEX));
    if (!img || !os || !os->alloc || !os->free || !count) return FNVNX_E_ARG;
    if (!stats) stats = &local;
    memset(stats, 0, sizeof *stats);
    memset(&s, 0, sizeof s);
    s.img = img;
    s.map = map;
    s.relmode = img->nrelocs && !(opts && (opts->flags & FNVNX_SCAN_NO_RELOCS));
    s.text_lo = img->load_base + img->pe->text_lo_rva;
    s.text_hi = img->load_base + img->pe->text_hi_rva;
    r = fnvnx_collect_exclusions(img, os, &s.excl, &s.nexcl);
    if (r) return r;
    if (fast && map && map->n) kx_build(&s.cmx, os, map->e, (uint32_t)sizeof *map->e, map->n);
    if (fast) build_eligibility(&s, os);
    s.zero_not_text = fast && !is_text(&s, 0);
    r = find_vtables(&s, os, opts);
    if (r) goto done;
    find_tls(&s);
    for (k = 0; k < s.nvt; k++) {
        if (s.vt[k].klass == FNVNX_FX_VTABLE_COL) stats->vtables_col++;
        else stats->vtables_hint++;
        stats->vtable_slots += (s.vt[k].end - s.vt[k].start) / 4;
    }
    s.vt_cursor = fast && vt_sorted(&s);
    if (s.vt_cursor && s.nvt) kx_build(&s.vtx, os, s.vt, (uint32_t)sizeof *s.vt, s.nvt);
    {   /* a zero word yields nothing unless 0 is a .text address or (0 - delta) an image vftable in the map */
        const fnvnx_codemap_entry *z = cm_find(&s, 0u - img->delta);
        zero_quiet = !is_text(&s, 0) && !(z && FNVNX_AM_KIND(z->info) == FNVNX_AM_VTABLE);
    }
    sk.out = out; sk.cap = cap; sk.n = 0; sk.stats = stats;
    for (i = 0; i < img->nmapped && i <= FNVNX_MAX_SECTIONS; i++) {      /* map[] holds MAX_SECTIONS + 1 */
        const fnvnx_mapping *m = &img->map[i];
        const uint32_t *b = s.bits[i];
        uint32_t w;
        if (m->sec == 0xffff) continue;
        if (b && s.vt_cursor) {   /* eligible words in increasing order, 32 at a time */
            uint32_t ng = (s.nw[i] + 31) / 32, gi, vc = s.nvt ? vt_lower(&s, m->va) : 0, words = 0;
            for (gi = 0; gi < ng; gi++) {
                uint32_t g = b[gi];
                words += (uint32_t)__builtin_popcount(g);
                if (g && zero_quiet && 32 * (gi + 1) <= s.nw[i] && zero128(&s, m->va + 128 * gi)) continue;
                while (g) {
                    uint32_t v;
                    int vi;
                    w = m->va + 128 * gi + 4 * (uint32_t)__builtin_ctz(g);
                    g &= g - 1;
                    v = word(&s, w);
                    if (!v && zero_quiet) continue;
                    while (vc < s.nvt && s.vt[vc].end <= w) vc++;
                    vi = vc < s.nvt && s.vt[vc].start <= w ? (int)vc : -1;
                    classify(&s, m, w, v, vi, &sk);
                }
            }
            stats->words += words;
            continue;
        }
        for (w = m->va; (uint64_t)w + 4 <= (uint64_t)m->va + m->size; w += 4) {
            if (!eligible_in(&s, m, w)) continue;
            stats->words++;
            classify(&s, m, w, word(&s, w), vt_find(&s, w), &sk);
        }
    }
    *count = sk.n;
    r = sk.n > cap && out ? FNVNX_E_NOMEM : FNVNX_OK;
done:
    if (s.vt) os->free(os->ctx, s.vt);
    if (s.bitmem) os->free(os->ctx, s.bitmem);
    if (s.cmx.first) os->free(os->ctx, s.cmx.first);
    if (s.vtx.first) os->free(os->ctx, s.vtx.first);
    os->free(os->ctx, s.excl);
    return r;
}

/* ---- applying ---- */

int fnvnx_fixups_apply(fnvnx_image *img, const fnvnx_codemap *map, const fnvnx_fixup *fx, uint32_t n,
                       unsigned min_conf, const fnvnx_apply_hooks *hooks, fnvnx_apply_stats *stats) {
    fnvnx_apply_stats local;
    uint32_t i;
    if (!stats) stats = &local;
    memset(stats, 0, sizeof *stats);
    for (i = 0; i < n; i++) {
        const fnvnx_fixup *f = &fx[i];
        const fnvnx_codemap_entry *e;
        uint8_t *p;
        uint32_t v = 0;
        if (f->klass >= 0x80 || f->conf < min_conf) { stats->skipped++; continue; }
        p = (uint8_t *)fnvnx_image_ptr(img, f->loc + img->delta, 4);
        if (!p || fnvnx_rd32(p) != f->target + img->delta) { stats->mismatched++; continue; }
        e = fnvnx_codemap_find(map, f->target);
        if (e && e->native && (e->info & FNVNX_AM_F_DELDTOR) && hooks && hooks->deldtor &&
            (v = hooks->deldtor(hooks->ctx, f->target, e->native)) != 0)
            stats->adapted++;
        else if (e && e->native && !(e->info & FNVNX_AM_F_DELDTOR)) { v = e->native; stats->applied++; }
        else if (hooks && hooks->trap && (v = hooks->trap(hooks->ctx, f->target)) != 0) stats->trapped++;
        else { stats->unresolved++; continue; }
        fnvnx_wr32(p, v);
    }
    return stats->mismatched ? FNVNX_E_MISMATCH : FNVNX_OK;
}

/* ---- list files ---- */

uint32_t fnvnx_fixups_serialize(const fnvnx_pe *pe, const fnvnx_fixup *fx, uint32_t n, void *buf, uint32_t cap) {
    uint8_t *b = (uint8_t *)buf;
    uint32_t need = FNVNX_FIXUPS_HEADER + 12 * n, i;
    if (!b || cap < need) return need;
    memcpy(b, FNVNX_FIXUPS_MAGIC, 8);
    fnvnx_wr32(b + 8, 1); fnvnx_wr32(b + 12, n); fnvnx_wr32(b + 16, pe->timestamp);
    fnvnx_wr32(b + 20, pe->size_of_image); fnvnx_wr32(b + 24, 12); fnvnx_wr32(b + 28, 0);
    for (i = 0; i < n; i++) {
        uint8_t *e = b + FNVNX_FIXUPS_HEADER + 12 * i;
        fnvnx_wr32(e, fx[i].loc); fnvnx_wr32(e + 4, fx[i].target);
        e[8] = fx[i].klass; e[9] = fx[i].conf; e[10] = (uint8_t)fx[i].aux; e[11] = (uint8_t)(fx[i].aux >> 8);
    }
    return need;
}

int fnvnx_fixups_parse(const fnvnx_pe *pe, const void *blob, uint32_t size, fnvnx_fixup *out, uint32_t cap,
                       uint32_t *count) {
    const uint8_t *b = (const uint8_t *)blob;
    uint32_t n, i;
    *count = 0;
    if (size < FNVNX_FIXUPS_HEADER || memcmp(b, FNVNX_FIXUPS_MAGIC, 8)) return FNVNX_E_FORMAT;
    if (fnvnx_rd32(b + 8) != 1 || fnvnx_rd32(b + 24) != 12) return FNVNX_E_UNSUPPORTED;
    if (fnvnx_rd32(b + 16) != pe->timestamp || fnvnx_rd32(b + 20) != pe->size_of_image) return FNVNX_E_MISMATCH;
    n = fnvnx_rd32(b + 12);
    if (n > (size - FNVNX_FIXUPS_HEADER) / 12) return FNVNX_E_FORMAT;
    if (n > cap) { *count = n; return FNVNX_E_NOMEM; }
    for (i = 0; i < n; i++) {
        const uint8_t *e = b + FNVNX_FIXUPS_HEADER + 12 * i;
        out[i].loc = fnvnx_rd32(e); out[i].target = fnvnx_rd32(e + 4);
        out[i].klass = e[8]; out[i].conf = e[9]; out[i].aux = (uint16_t)(e[10] | (e[11] << 8));
        if (i && out[i].loc <= out[i - 1].loc) return FNVNX_E_FORMAT;
    }
    *count = n;
    return FNVNX_OK;
}
