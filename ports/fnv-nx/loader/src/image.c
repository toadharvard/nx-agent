/* Mapping the data sections at their original addresses; base relocations. */
#include "internal.h"
#include <stdlib.h>

#define PAGE 0x1000u

static uint32_t page_up(uint32_t v) { return (v + PAGE - 1) & ~(PAGE - 1); }
static uint32_t vsize_eff(const fnvnx_section *s) { return s->vsize ? s->vsize : s->raw_size; }

int fnvnx_image_map(fnvnx_image *img, const fnvnx_pe *pe, const fnvnx_os *os, uint32_t load_base, uintptr_t bias,
                    uint32_t window_lo, uint32_t window_hi, unsigned flags) {
    unsigned i;
    if (!img || !pe || !os || !os->map_fixed || !os->read) return FNVNX_E_ARG;
    memset(img, 0, sizeof *img);
    img->pe = pe;
    img->bias = bias;
    img->load_base = load_base;
    img->delta = load_base - pe->image_base;
    if (load_base & 0xffff) return FNVNX_E_ARG;
    if ((uint64_t)load_base + pe->size_of_image > 0xffff0000ull) return FNVNX_E_RANGE;   /* keeps va + size from wrapping */

    if (flags & FNVNX_MAP_HEADERS) {
        fnvnx_mapping *m = &img->map[img->nmapped++];
        m->va = load_base;
        m->size = page_up(pe->size_of_headers);
        m->file_bytes = pe->size_of_headers;
        m->prot = FNVNX_PROT_R;
        m->sec = 0xffff;
    }
    for (i = 0; i < pe->nsections; i++) {
        const fnvnx_section *s = &pe->sec[i];
        fnvnx_mapping *m;
        int code = (s->characteristics & (FNVNX_SCN_CNT_CODE | FNVNX_SCN_MEM_EXECUTE)) != 0;
        int all = (flags & FNVNX_MAP_ALL) != 0;
        if (!all && (s->characteristics & FNVNX_SCN_DISCARDABLE)) continue;
        if (!all && code && !(flags & FNVNX_MAP_CODE)) continue;
        m = &img->map[img->nmapped++];
        m->va = load_base + s->rva;
        m->size = page_up(vsize_eff(s));
        m->file_bytes = s->raw_size < vsize_eff(s) ? s->raw_size : vsize_eff(s);
        if (all)   /* the section's own bits (Windows rule); host-executable never */
            m->prot = ((s->characteristics & FNVNX_SCN_MEM_READ) ? FNVNX_PROT_R : 0u) |
                      ((s->characteristics & FNVNX_SCN_MEM_WRITE) ? FNVNX_PROT_W : 0u);
        else
            m->prot = code ? FNVNX_PROT_R
                           : FNVNX_PROT_R | ((s->characteristics & FNVNX_SCN_MEM_WRITE) ? FNVNX_PROT_W : 0);
        m->sec = i;
        m->code = (unsigned)code;
    }
    for (i = 0; i < img->nmapped; i++) {
        const fnvnx_mapping *m = &img->map[i];
        if (!fnvnx_in(m->va, m->size, window_lo, window_hi)) {
            fnvnx_logf(os, "section at 0x%08x+0x%x outside window 0x%08x..0x%08x", m->va, m->size, window_lo,
                       window_hi);
            return FNVNX_E_RANGE;
        }
    }
    for (i = 0; i < img->nmapped; i++) {
        const fnvnx_mapping *m = &img->map[i];
        uint8_t *host = (uint8_t *)(m->va + bias);
        uint32_t off = m->sec == 0xffff ? 0 : pe->sec[m->sec].raw_off;
        if (os->map_fixed(os->ctx, (uintptr_t)m->va + bias, m->size, m->prot)) {
            fnvnx_logf(os, "map_fixed 0x%08x+0x%x failed", m->va, m->size);
            return FNVNX_E_MAP;
        }
        if (m->file_bytes && os->read(os->ctx, off, host, m->file_bytes)) return FNVNX_E_IO;
        memset(host + m->file_bytes, 0, m->size - m->file_bytes);   /* .bss and the page tail */
    }
    return FNVNX_OK;
}

static int find_mapping(const fnvnx_image *img, uint32_t va) {
    int lo = 0, hi = (int)img->nmapped - 1;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        const fnvnx_mapping *m = &img->map[mid];
        if (va < m->va) hi = mid - 1;
        else if (va - m->va >= m->size) lo = mid + 1;
        else return mid;
    }
    return -1;
}

void *fnvnx_image_ptr(const fnvnx_image *img, uint32_t va, uint32_t size) {
    int i = find_mapping(img, va);
    if (i < 0 || !fnvnx_in(va, size, img->map[i].va, img->map[i].va + img->map[i].size)) return NULL;
    return (void *)((uintptr_t)va + img->bias);
}

int fnvnx_image_read32(const fnvnx_image *img, uint32_t va, uint32_t *out) {
    const void *p = fnvnx_image_ptr(img, va, 4);
    if (!p) return FNVNX_E_RANGE;
    *out = fnvnx_rd32(p);
    return FNVNX_OK;
}

const char *fnvnx_image_str(const fnvnx_image *img, uint32_t va, uint32_t max, uint32_t *len) {
    int i = find_mapping(img, va);
    const char *s;
    uint32_t n = 0, avail;
    if (i < 0) return NULL;
    s = (const char *)((uintptr_t)va + img->bias);
    avail = img->map[i].va + img->map[i].size - va;
    if (avail > max) avail = max;
    while (n < avail && s[n]) n++;
    if (n == avail) return NULL;
    if (len) *len = n;
    return s;
}

static int cmp_u32(const void *a, const void *b) {
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}

int fnvnx_image_load_relocs(fnvnx_image *img, const fnvnx_os *os) {
    const fnvnx_pe *pe = img->pe;
    uint32_t rva = pe->ndirs > FNVNX_DIR_BASERELOC ? pe->dir[FNVNX_DIR_BASERELOC].rva : 0;
    uint32_t size = pe->ndirs > FNVNX_DIR_BASERELOC ? pe->dir[FNVNX_DIR_BASERELOC].size : 0;
    uint8_t *buf;
    uint32_t pos, n = 0, pass;
    int r = FNVNX_OK;
    if (img->relocs) return FNVNX_OK;
    img->nrelocs = 0;
    if (!size) return FNVNX_OK;
    if (!os->alloc || !os->free) return FNVNX_E_ARG;
    buf = (uint8_t *)os->alloc(os->ctx, size);
    if (!buf) return FNVNX_E_NOMEM;
    r = fnvnx_pe_read_rva(pe, os, rva, buf, size);
    if (r) goto out;
    /* pass 0 counts, pass 1 stores */
    for (pass = 0; pass < 2; pass++) {
        n = 0;
        for (pos = 0; pos + 8 <= size;) {
            uint32_t page = fnvnx_rd32(buf + pos), bsize = fnvnx_rd32(buf + pos + 4), k;
            if (bsize < 8 || bsize > size - pos || (bsize & 1)) { r = FNVNX_E_FORMAT; goto out; }
            for (k = 8; k + 2 <= bsize; k += 2) {
                uint16_t e = fnvnx_rd16(buf + pos + k);
                uint32_t type = e >> 12, va = img->load_base + page + (e & 0xfffu);
                if (type == 0) continue;                       /* IMAGE_REL_BASED_ABSOLUTE: padding */
                if (type != 3) { r = FNVNX_E_UNSUPPORTED; goto out; }  /* only HIGHLOW in an x86 image */
                if (!fnvnx_image_ptr(img, va, 4) || img->map[find_mapping(img, va)].code)
                    continue;    /* .text (unmapped, or mapped read-only as data: FNVNX_MAP_CODE) and unmapped sections */
                if (pass) img->relocs[n] = va;
                n++;
            }
            pos += bsize;
        }
        if (!pass) {
            if (!n) goto out;
            img->relocs = (uint32_t *)os->alloc(os->ctx, n * 4);
            if (!img->relocs) { r = FNVNX_E_NOMEM; goto out; }
        }
    }
    qsort(img->relocs, n, 4, cmp_u32);
    img->nrelocs = n;
out:
    os->free(os->ctx, buf);
    if (r && img->relocs) { os->free(os->ctx, img->relocs); img->relocs = NULL; img->nrelocs = 0; }
    return r;
}

int fnvnx_image_relocate(fnvnx_image *img, const fnvnx_os *os, uint32_t *applied) {
    uint32_t i;
    int r;
    if (applied) *applied = 0;
    if (img->delta == 0 || img->relocated) return FNVNX_OK;
    if ((img->pe->characteristics & 0x0001) || img->pe->ndirs <= FNVNX_DIR_BASERELOC ||
        !img->pe->dir[FNVNX_DIR_BASERELOC].size)
        return FNVNX_E_UNSUPPORTED;                            /* relocations stripped */
    r = fnvnx_image_load_relocs(img, os);
    if (r) return r;
    for (i = 0; i < img->nrelocs; i++) {
        uint8_t *p = (uint8_t *)fnvnx_image_ptr(img, img->relocs[i], 4);
        fnvnx_wr32(p, fnvnx_rd32(p) + img->delta);
    }
    img->relocated = 1;
    if (applied) *applied = img->nrelocs;
    return FNVNX_OK;
}

int fnvnx_image_finalize(fnvnx_image *img, const fnvnx_os *os) {
    unsigned i;
    if (!os->protect) return FNVNX_OK;
    for (i = 0; i < img->nmapped; i++)
        if (os->protect(os->ctx, (uintptr_t)img->map[i].va + img->bias, img->map[i].size, img->map[i].prot))
            return FNVNX_E_MAP;
    return FNVNX_OK;
}

void fnvnx_image_release(fnvnx_image *img, const fnvnx_os *os) {
    if (img->relocs && os->free) os->free(os->ctx, img->relocs);
    img->relocs = NULL;
    img->nrelocs = 0;
}

int fnvnx_image_check_bytes(const fnvnx_image *img, const fnvnx_bytes_probe *p, uint32_t n, unsigned percent,
                            uint32_t *matched) {
    uint32_t i, ok = 0;
    for (i = 0; i < n; i++) {
        const void *q = fnvnx_image_ptr(img, p[i].va + img->delta, p[i].len);
        ok += q && p[i].len && !memcmp(q, p[i].bytes, p[i].len);
    }
    if (matched) *matched = ok;
    return (n && (uint64_t)ok * 100 >= (uint64_t)n * percent) ? FNVNX_OK : FNVNX_E_PROBES;
}

int fnvnx_image_vtable_slots(const fnvnx_image *img, uint32_t vftable, uint32_t *out, uint32_t cap, uint32_t *n) {
    uint32_t lo = img->load_base + img->pe->text_lo_rva, hi = img->load_base + img->pe->text_hi_rva, v, k;
    *n = 0;
    for (k = 0; k < cap; k++) {
        if (fnvnx_image_read32(img, vftable + img->delta + 4 * k, &v) || v < lo || v >= hi) break;
        out[k] = v - img->delta;
    }
    *n = k;
    return k ? FNVNX_OK : FNVNX_E_RANGE;
}

int fnvnx_image_tls_index_reset(const fnvnx_image *img, uint32_t *va, uint32_t *old) {
    const fnvnx_pe *pe = img->pe;
    const uint8_t *t;
    uint8_t *w;
    uint32_t ia;
    if (va) *va = 0;
    if (old) *old = 0;
    if (pe->ndirs <= FNVNX_DIR_TLS || pe->dir[FNVNX_DIR_TLS].size < 24) return FNVNX_OK;
    t = (const uint8_t *)fnvnx_image_ptr(img, img->load_base + pe->dir[FNVNX_DIR_TLS].rva, 24);
    if (!t) return FNVNX_E_RANGE;
    ia = fnvnx_rd32(t + 8);   /* AddressOfIndex (a VA at the preferred base; relocated with the image) */
    if (!ia) return FNVNX_OK;
    w = (uint8_t *)fnvnx_image_ptr(img, ia, 4);
    if (!w && img->delta) {   /* not yet relocated: the preferred-base VA */
        ia += img->delta;
        w = (uint8_t *)fnvnx_image_ptr(img, ia, 4);
    }
    if (!w) return FNVNX_E_RANGE;
    if (va) *va = ia;
    if (old) *old = fnvnx_rd32(w);
    w[0] = w[1] = w[2] = w[3] = 0;
    return FNVNX_OK;
}

int fnvnx_image_tls_callbacks(const fnvnx_image *img, uint32_t *out, uint32_t cap, uint32_t *n) {
    const fnvnx_pe *pe = img->pe;
    const uint8_t *t;
    uint32_t cb, k;
    *n = 0;
    if (pe->ndirs <= FNVNX_DIR_TLS || pe->dir[FNVNX_DIR_TLS].size < 24) return FNVNX_OK;
    t = (const uint8_t *)fnvnx_image_ptr(img, img->load_base + pe->dir[FNVNX_DIR_TLS].rva, 24);
    if (!t) return FNVNX_E_RANGE;
    cb = fnvnx_rd32(t + 12);   /* AddressOfCallBacks (a VA at the preferred base) */
    if (!cb) return FNVNX_OK;
    if (!fnvnx_image_ptr(img, cb, 4) && img->delta) cb += img->delta;
    for (k = 0; k < 4096; k++) {
        const uint8_t *w = (const uint8_t *)fnvnx_image_ptr(img, cb + 4 * k, 4);
        if (!w) return FNVNX_E_RANGE;
        if (!fnvnx_rd32(w)) return FNVNX_OK;
        if (*n < cap && out) out[*n] = fnvnx_rd32(w);
        (*n)++;
    }
    return FNVNX_E_FORMAT;   /* no terminator */
}
