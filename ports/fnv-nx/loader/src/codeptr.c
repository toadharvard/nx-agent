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

static int eligible_in(const scan *s, const fnvnx_mapping *m, uint32_t w) {
    if (w < m->va || w - m->va > m->size - 4) return 0;
    if (excluded(s, w)) return 0;
    return !s->relmode || is_reloc(s, w);
}

static uint32_t word(const scan *s, uint32_t w) { return fnvnx_rd32((const void *)((uintptr_t)w + s->img->bias)); }

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

static int vt_find(const scan *s, uint32_t w) {   /* index of the vftable containing w, or -1 */
    uint32_t lo = 0, hi = s->nvt;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (s->vt[mid].end <= w) lo = mid + 1;
        else hi = mid;
    }
    return (lo < s->nvt && s->vt[lo].start <= w) ? (int)lo : -1;
}

static int vt_is_start(const scan *s, uint32_t v) {
    int i = vt_find(s, v);
    return i >= 0 && s->vt[i].start == v;
}

static int tls_index(const scan *s, uint32_t w) {
    uint32_t i;
    for (i = 0; i < s->ntls; i++) if (s->tls[i] == w) return (int)i;
    return -1;
}

static const fnvnx_codemap_entry *known(const scan *s, uint32_t v) {
    const fnvnx_codemap_entry *e;
    if (!is_text(s, v)) return NULL;
    e = fnvnx_codemap_find(s->map, v - s->img->delta);
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

/* collect vftable starts: COL-validated (pass 0 counts, pass 1 stores) merged with hints */
static int find_vtables(scan *s, const fnvnx_os *os, const fnvnx_scan_opts *opts) {
    const fnvnx_image *img = s->img;
    uint32_t pass, n = 0, i, h, k, total;
    vtab *col = NULL;
    for (pass = 0; pass < 2; pass++) {
        n = 0;
        for (i = 0; i < img->nmapped; i++) {
            const fnvnx_mapping *m = &img->map[i];
            uint32_t w;
            if (m->sec == 0xffff) continue;
            for (w = m->va; (uint64_t)w + 8 <= (uint64_t)m->va + m->size; w += 4) {
                uint32_t c = word(s, w);
                if (!eligible_in(s, m, w) || !eligible_in(s, m, w + 4) || !is_text(s, word(s, w + 4))) continue;
                if (!fnvnx_image_ptr(img, c, 20) || !valid_col(s, c)) continue;
                if (pass) { col[n].start = w + 4; col[n].klass = FNVNX_FX_VTABLE_COL; }
                n++;
            }
        }
        if (!pass) {
            total = n + (opts ? opts->nhints : 0);
            if (!total) return FNVNX_OK;
            col = (vtab *)os->alloc(os->ctx, total * (uint32_t)sizeof(vtab));
            if (!col) return FNVNX_E_NOMEM;
        }
    }
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
        os->free(os->ctx, col);
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

int fnvnx_scan_codeptrs(const fnvnx_image *img, const fnvnx_os *os, const fnvnx_codemap *map,
                        const fnvnx_scan_opts *opts, fnvnx_fixup *out, uint32_t cap, uint32_t *count,
                        fnvnx_scan_stats *stats) {
    scan s;
    fnvnx_scan_stats local;
    uint32_t n = 0, i, k;
    int r;
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
    r = find_vtables(&s, os, opts);
    if (r) goto done;
    find_tls(&s);
    for (k = 0; k < s.nvt; k++) {
        if (s.vt[k].klass == FNVNX_FX_VTABLE_COL) stats->vtables_col++;
        else stats->vtables_hint++;
        stats->vtable_slots += (s.vt[k].end - s.vt[k].start) / 4;
    }

    for (i = 0; i < img->nmapped; i++) {
        const fnvnx_mapping *m = &img->map[i];
        uint32_t w;
        if (m->sec == 0xffff) continue;
        for (w = m->va; (uint64_t)w + 4 <= (uint64_t)m->va + m->size; w += 4) {
            uint32_t v, o;
            int vi, ti;
            fnvnx_fixup f;
            const fnvnx_codemap_entry *e;
            if (!eligible_in(&s, m, w)) continue;
            stats->words++;
            v = word(&s, w);
            vi = vt_find(&s, w);
            if (!is_text(&s, v)) {
                if (vi < 0 && v && vt_is_start(&s, v)) stats->vptr_refs++;
                e = vi < 0 ? fnvnx_codemap_find(map, v - img->delta) : NULL;
                if (!e || FNVNX_AM_KIND(e->info) != FNVNX_AM_VTABLE) continue;
                f.loc = w - img->delta; f.target = v - img->delta; f.aux = 0;
                f.klass = FNVNX_FX_VPTR; f.conf = FNVNX_CONF_MED;
                stats->by_class[f.klass]++;
                if (n < cap && out) out[n] = f;
                n++;
                continue;
            }
            stats->textptrs++;
            o = v - img->delta;
            f.loc = w - img->delta; f.target = o; f.aux = 0;
            if (vi >= 0) {
                f.klass = s.vt[vi].klass; f.conf = FNVNX_CONF_HIGH;
                f.aux = (uint16_t)((w - s.vt[vi].start) / 4);
            } else if ((ti = tls_index(&s, w)) >= 0) {
                f.klass = FNVNX_FX_TLS_CALLBACK; f.conf = FNVNX_CONF_HIGH; f.aux = (uint16_t)ti;
            } else {
                e = fnvnx_codemap_find(map, o);
                if (!e) { f.klass = FNVNX_FX_UNKNOWN; f.conf = FNVNX_CONF_NONE; }
                else if (FNVNX_AM_KIND(e->info) == FNVNX_AM_MID) { f.klass = FNVNX_FX_MID; f.conf = FNVNX_CONF_NONE; }
                else if (s.relmode) { f.klass = FNVNX_FX_RELOC; f.conf = FNVNX_CONF_HIGH; }
                else if (known_at(&s, m, w - 4) || known_at(&s, m, w + 4)) {
                    uint32_t b = 0;
                    while (b < 0xffff && known_at(&s, m, w - 4 * (b + 1))) b++;
                    f.klass = FNVNX_FX_RUN; f.conf = FNVNX_CONF_MED; f.aux = (uint16_t)b;
                } else if (string_tail(&s, m, w)) { f.klass = FNVNX_FX_REJ_STRING; f.conf = FNVNX_CONF_NONE; }
                else if (!(o & 0xfffu)) { f.klass = FNVNX_FX_REJ_ROUND; f.conf = FNVNX_CONF_NONE; }
                else { f.klass = FNVNX_FX_ISOLATED; f.conf = FNVNX_CONF_LOW; }
            }
            stats->by_class[f.klass]++;
            if (n < cap && out) out[n] = f;
            n++;
        }
    }
    *count = n;
    r = n > cap && out ? FNVNX_E_NOMEM : FNVNX_OK;
done:
    if (s.vt) os->free(os->ctx, s.vt);
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
