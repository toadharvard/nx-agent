/* Import binding (rule S4): the regular import table only. */
#include "internal.h"

static uint32_t dir_rva(const fnvnx_pe *pe, unsigned d) { return d < pe->ndirs ? pe->dir[d].rva : 0; }
static uint32_t dir_size(const fnvnx_pe *pe, unsigned d) { return d < pe->ndirs ? pe->dir[d].size : 0; }

/* Walks the import descriptors; cb gets (dll, name or NULL, ordinal, iat slot VA) per import and returns non-zero to
 * stop with that error. */
typedef int (*thunk_cb)(void *ctx, const char *dll, const char *name, uint32_t ordinal, uint32_t slot);

static int walk_imports(const fnvnx_image *img, thunk_cb tcb, void *ctx) {
    const fnvnx_pe *pe = img->pe;
    uint32_t base = img->load_base, d, j;
    uint32_t rva = dir_rva(pe, FNVNX_DIR_IMPORT), size = dir_size(pe, FNVNX_DIR_IMPORT);
    int r;
    for (d = 0; size && d + 20 <= size; d += 20) {
        const uint8_t *desc = (const uint8_t *)fnvnx_image_ptr(img, base + rva + d, 20);
        uint32_t oft, name, ft, nlen, lookup;
        const char *dll;
        if (!desc) return FNVNX_E_RANGE;
        oft = fnvnx_rd32(desc); name = fnvnx_rd32(desc + 12); ft = fnvnx_rd32(desc + 16);
        if (!oft && !name && !ft) break;
        dll = fnvnx_image_str(img, base + name, 256, &nlen);
        if (!dll || !ft) return FNVNX_E_FORMAT;
        lookup = oft ? oft : ft;
        for (j = 0;; j++) {
            uint32_t t, ord = FNVNX_BY_NAME;
            const char *fn = NULL;
            if (fnvnx_image_read32(img, base + lookup + 4 * j, &t)) return FNVNX_E_FORMAT;
            if (!fnvnx_image_ptr(img, base + ft + 4 * j, 4)) return FNVNX_E_FORMAT;
            if (!t) break;
            if (t & 0x80000000u) ord = t & 0xffffu;
            else {
                uint32_t len;
                if (!fnvnx_image_ptr(img, base + t, 2)) return FNVNX_E_FORMAT;   /* the hint word */
                fn = fnvnx_image_str(img, base + t + 2, 512, &len);
                if (!fn) return FNVNX_E_FORMAT;
            }
            if ((r = tcb(ctx, dll, fn, ord, base + ft + 4 * j))) return r;
        }
    }
    return FNVNX_OK;
}

typedef struct strict_ctx {
    fnvnx_image *img;
    const fnvnx_os *os;
    fnvnx_import_resolve3 resolve;
    void *ctx;
    uint32_t bound, missing;
} strict_ctx;

static int strict_thunk(void *c, const char *dll, const char *name, uint32_t ord, uint32_t slot) {
    strict_ctx *b = (strict_ctx *)c;
    uint32_t v;
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
    if (!img || !resolve || !fnvnx_os_complete(os)) return FNVNX_E_ARG;
    memset(&b, 0, sizeof b);
    b.img = img; b.os = os; b.resolve = resolve; b.ctx = ctx;
    r = walk_imports(img, strict_thunk, &b);
    if (bound) *bound = b.bound;
    if (r) return r;
    return b.missing ? FNVNX_E_MISMATCH : FNVNX_OK;
}
