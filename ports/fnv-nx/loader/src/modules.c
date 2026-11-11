/* The lifted PE modules of the exe's static import closure: rules M2 (map, relocate, import resolver), M3 (TLS), M4
 * (initialisation, thread and process notifications). See include/fnvnx_modules.h and include/fnvnx_start.h.
 *
 * Assumptions about the Windows loader (the rules below follow them):
 *  - a module is mapped as the exe is (headers and every section at base + rva, claimed to SizeOfImage) and relocated
 *    by adding (base - ImageBase) to every HIGHLOW word its base relocation blocks list (ABSOLUTE entries are padding);
 *    no other relocation type exists in an i386 image, so any other type refuses the start;
 *  - an import by name binds to the export of that name found by binary search of the export name table (which the PE
 *    format requires to be sorted by byte order: checked at load); an import by ordinal binds to function table entry
 *    (ordinal - OrdinalBase); a table entry that is 0 is no export; an entry inside the export directory is a
 *    forwarder string "DLL.Name" or "DLL.#N" (split at the first '.', the dll part has no extension), resolved by the
 *    same resolver; a chain longer than the number of modules revisits a module (a cycle) and has no provider;
 *  - DLL_PROCESS_ATTACH reaches TLS callbacks (hModule, reason, NULL) and then DllMain(hModule, reason, reserved)
 *    for each module in initialisation order (the table order); the loader's init routine result is a BOOLEAN, so
 *    only the low byte of eax is tested; thread and process notifications call the same two things per module, in
 *    the order the headers of fnvnx_start.h state. */
#include "start_internal.h"

#define NAME_BUF 261u
#define DLL_PROCESS_DETACH 0u
#define DLL_PROCESS_ATTACH 1u
#define DLL_THREAD_ATTACH  2u
#define DLL_THREAD_DETACH  3u
#define FILE_DLL           0x2000u

/* Windows module-name rule (contract/iat.py norm_dll): ASCII lowercase, ".dll" appended when there is no '.'. 0 when it
 * does not fit. */
static int norm_name(const char *in, char *out) {
    size_t n = 0;
    int dot = 0;
    for (; in[n]; n++) {
        char c = in[n];
        if (n + 5 > NAME_BUF - 1) return 0;
        if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        if (c == '.') dot = 1;
        out[n] = c;
    }
    if (!dot) { memcpy(out + n, ".dll", 4); n += 4; }
    out[n] = 0;
    return 1;
}

static int module_index(const fnvnx_proc *p, const char *dll) {
    char n[NAME_BUF];
    unsigned k;
    if (!p->nmod || !norm_name(dll, n)) return -1;
    for (k = 0; k < p->nmod; k++)
        if (p->mod[k].state != FNVNX_MOD_UNLOADED && !strcmp(n, p->mod[k].name)) return (int)k;
    return -1;
}

/* the table row of a module name (loaded or not), or -1 */
static int table_index(const fnvnx_proc *p, const char *dll) {
    char n[NAME_BUF];
    unsigned k;
    if (!norm_name(dll, n)) return -1;
    for (k = 0; k < p->nmod; k++)
        if (!strcmp(n, p->cfg.modules[k].name)) return (int)k;
    return -1;
}

/* ---- export directory ---- */

typedef struct exp_dir {
    int present;
    uint32_t rva, size, ordbase, nfunc, nname, aof, aon, aono;
} exp_dir;

/* an array of `count` elements of `elem` bytes at rva lies wholly inside one mapping */
static int arr_ok(const fnvnx_loaded *L, uint32_t rva, uint32_t count, uint32_t elem) {
    uint64_t bytes = (uint64_t)count * elem;
    return !count || (bytes <= 0xfffffffu && fnvnx_image_ptr(&L->img, L->img.load_base + rva, (uint32_t)bytes) != NULL);
}

static int exp_read(const fnvnx_loaded *L, exp_dir *e) {
    const fnvnx_pe *pe = &L->pe;
    const uint8_t *t;
    memset(e, 0, sizeof *e);
    if (pe->ndirs <= FNVNX_DIR_EXPORT || !pe->dir[FNVNX_DIR_EXPORT].size) return FNVNX_OK;
    e->rva = pe->dir[FNVNX_DIR_EXPORT].rva;
    e->size = pe->dir[FNVNX_DIR_EXPORT].size;
    if (e->size < 40) return FNVNX_E_FORMAT;
    t = (const uint8_t *)fnvnx_image_ptr(&L->img, L->img.load_base + e->rva, 40);
    if (!t) return FNVNX_E_RANGE;
    e->ordbase = fnvnx_rd32(t + 16);
    e->nfunc = fnvnx_rd32(t + 20);
    e->nname = fnvnx_rd32(t + 24);
    e->aof = fnvnx_rd32(t + 28);
    e->aon = fnvnx_rd32(t + 32);
    e->aono = fnvnx_rd32(t + 36);
    if (!arr_ok(L, e->aof, e->nfunc, 4) || !arr_ok(L, e->aon, e->nname, 4) || !arr_ok(L, e->aono, e->nname, 2))
        return FNVNX_E_RANGE;
    e->present = 1;
    return FNVNX_OK;
}

static const char *exp_name(const fnvnx_loaded *L, const exp_dir *e, uint32_t k) {
    return fnvnx_image_str(&L->img, L->img.load_base + fnvnx_rd32(fnvnx_image_ptr(&L->img, L->img.load_base + e->aon + 4u * k, 4)), 512, NULL);
}

/* Precondition of the binary search: every name is a string inside the image and the table is strictly ascending in
 * byte order (the PE format's rule). */
static int exp_check(const fnvnx_proc *p, const fnvnx_loaded *L) {
    exp_dir e;
    uint32_t k;
    int r = exp_read(L, &e);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: %s: export directory: %s", L->name, fnvnx_strerror(r)); return r; }
    for (k = 0; k < e.nname; k++) {
        const char *a = exp_name(L, &e, k);
        if (!a || (k && strcmp(exp_name(L, &e, k - 1), a) >= 0)) {
            fnvnx_logf(p->os, "fnvnx-start: %s: export name table is not a sorted list of strings (entry %u)", L->name, (unsigned)k);
            return FNVNX_E_FORMAT;
        }
    }
    return FNVNX_OK;
}

typedef struct exp_hit {
    uint32_t va;
    const char *fwd;   /* forwarder string, or NULL */
} exp_hit;

/* name != NULL: by name, else by ordinal. 1 = found. */
static int exp_find(const fnvnx_loaded *L, const char *name, uint32_t ordinal, exp_hit *h) {
    exp_dir e;
    uint32_t idx, f, lo = 0, hi;
    if (exp_read(L, &e) || !e.present) return 0;
    if (name) {
        hi = e.nname;
        for (;;) {
            uint32_t mid;
            const char *s;
            int c;
            if (lo >= hi) return 0;
            mid = lo + (hi - lo) / 2;
            if (!(s = exp_name(L, &e, mid))) return 0;
            c = strcmp(name, s);
            if (!c) {
                const uint8_t *o = (const uint8_t *)fnvnx_image_ptr(&L->img, L->img.load_base + e.aono + 2u * mid, 2);
                if (!o) return 0;
                idx = fnvnx_rd16(o);
                break;
            }
            if (c < 0) hi = mid; else lo = mid + 1;
        }
    } else {
        if (ordinal < e.ordbase) return 0;
        idx = ordinal - e.ordbase;
    }
    if (idx >= e.nfunc) return 0;
    f = fnvnx_rd32(fnvnx_image_ptr(&L->img, L->img.load_base + e.aof + 4u * idx, 4));
    if (!f) return 0;
    h->fwd = NULL;
    if (f >= e.rva && f - e.rva < e.size) {
        h->fwd = fnvnx_image_str(&L->img, L->img.load_base + f, e.rva + e.size - f, NULL);
        return h->fwd != NULL;
    }
    if (!fnvnx_image_ptr(&L->img, L->img.load_base + f, 1)) return 0;
    h->va = L->img.load_base + f;
    return 1;
}

/* ---- the one resolver ---- */

static uint32_t resolve_hops(fnvnx_proc *p, const char *dll, const char *name, uint32_t ordinal, unsigned hops) {
    char d[NAME_BUF];
    exp_hit h;
    const char *dot, *rest;
    size_t n;
    uint32_t ord = 0;
    int k = module_index(p, dll);
    if (k < 0) return p->cfg.resolve(p->cfg.resolve_ctx, dll, name, ordinal);
    if (!exp_find(&p->mod[k], name, ordinal, &h)) return 0;
    if (!h.fwd) return h.va;
    if (hops >= p->nmod) return 0;                    /* longer than the number of modules: a cycle */
    dot = strchr(h.fwd, '.');
    if (!dot || dot == h.fwd || !dot[1] || (size_t)(dot - h.fwd) > NAME_BUF - 6) return 0;
    memcpy(d, h.fwd, (size_t)(dot - h.fwd));
    d[dot - h.fwd] = 0;
    if (!norm_name(d, d)) return 0;                   /* in place: the result is never shorter than the input */
    rest = dot + 1;
    if (rest[0] != '#') return resolve_hops(p, d, rest, FNVNX_BY_NAME, hops + 1);
    for (n = 1; rest[n]; n++) {
        if (rest[n] < '0' || rest[n] > '9' || ord > 0xffffu) return 0;
        ord = ord * 10u + (uint32_t)(rest[n] - '0');
    }
    return n > 1 && ord >= 1 && ord <= 0xffffu ? resolve_hops(p, d, NULL, ord, hops + 1) : 0;
}

uint32_t fnvnx_modules_resolve(void *ctx, const char *dll, const char *name, uint32_t ordinal) {
    return resolve_hops((fnvnx_proc *)ctx, dll, name, ordinal, 0);
}

/* ---- M2: map ---- */

/* The table itself (every row, static or on demand): usable entries, names unique, ranges inside the address space,
 * outside the reserved range [LO, HI), the exe image and each other. */
static int check_table(const fnvnx_proc *p) {
    unsigned k, j;
    for (k = 0; k < p->nmod; k++) {
        const fnvnx_module_desc *d = &p->cfg.modules[k];
        if (!d->name || !d->name[0] || !d->file_size || !d->size_of_image || (d->base & 0xffffu) || (d->size_of_image & (FNVNX_PAGE - 1u)) ||
            !fnvnx_in(d->base, d->size_of_image, 0, 0xffff0000u) ||
            (d->base < FNVNX_START_RESERVED_HI && (uint64_t)d->base + d->size_of_image > FNVNX_START_RESERVED_LO) ||
            fnvnx_images_overlap(p, d->base, d->size_of_image, -1) == 0 ? 0 : 0) {
            fnvnx_logf(p->os, "fnvnx-start: module table entry %u is not usable (name, size, 64 KiB base or range)", k);
            return FNVNX_START_E_STATE;
        }
        if (table_index(p, d->name) != (int)k) {
            fnvnx_logf(p->os, "fnvnx-start: module table names %s twice (or not in the normal form)", d->name);
            return FNVNX_START_E_STATE;
        }
    }
    return FNVNX_OK;
}
