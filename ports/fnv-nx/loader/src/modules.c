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

/* The table itself (every row, static or on demand): usable entries, names unique and in normal form, ranges inside the
 * address space, outside the reserved range [LO, HI), the exe image and each other. */
static int check_table(const fnvnx_proc *p) {
    unsigned k;
    for (k = 0; k < p->nmod; k++) {
        const fnvnx_module_desc *d = &p->cfg.modules[k];
        if (!d->name || !d->name[0] || !d->file_size || !d->size_of_image || (d->base & 0xffffu) || (d->size_of_image & (FNVNX_PAGE - 1u)) ||
            !fnvnx_in(d->base, d->size_of_image, 0, 0xffff0000u) ||
            (d->base < FNVNX_START_RESERVED_HI && (uint64_t)d->base + d->size_of_image > FNVNX_START_RESERVED_LO) ||
            fnvnx_images_overlap(p, d->base, d->size_of_image, (int)k)) {
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

/* Read, hash-check, parse, map at the static base, relocate and check the exports of table row k. */
static int load_one(fnvnx_proc *p, unsigned k) {
    const fnvnx_module_desc *d = &p->cfg.modules[k];
    fnvnx_loaded *L = &p->mod[k];
    uint8_t sha[32], extra;
    uint8_t *buf;
    int r;
    L->name = d->name;
    L->file = FNVNX_FILE_MODULE(k);
    if (!(buf = (uint8_t *)p->os->alloc(p->os->ctx, d->file_size))) return FNVNX_E_NOMEM;
    r = p->os->read(p->os->ctx, L->file, 0, buf, d->file_size) ? FNVNX_E_IO : FNVNX_OK;
    if (r) fnvnx_logf(p->os, "fnvnx-start: cannot read module %s", L->name);
    else fnvnx_sha256(buf, d->file_size, sha);
    p->os->free(p->os->ctx, buf);
    if (r) return r;
    if (memcmp(sha, d->sha256, 32)) {
        fnvnx_logf(p->os, "fnvnx-start: identity mismatch: module %s is not the file the table was built from", L->name);
        return FNVNX_START_E_IDENTITY;
    }
    if (!p->os->read(p->os->ctx, L->file, d->file_size, &extra, 1)) {   /* the file must end where the table says */
        fnvnx_logf(p->os, "fnvnx-start: identity mismatch: module %s is longer than the table says", L->name);
        return FNVNX_START_E_IDENTITY;
    }
    if ((r = fnvnx_pe_open(&L->pe, p->os, L->file, d->file_size))) {
        fnvnx_logf(p->os, "fnvnx-start: PE headers of %s: %s", L->name, fnvnx_strerror(r));
        return r;
    }
    if (!(L->pe.characteristics & FILE_DLL) || L->pe.size_of_image != d->size_of_image) {
        fnvnx_logf(p->os, "fnvnx-start: %s is not a DLL image of the size the table says", L->name);
        return FNVNX_E_UNSUPPORTED;
    }
    L->img.pe = &L->pe;
    if ((r = fnvnx_loaded_map(p, L, d->base, (int)k))) return r;
    L->state = FNVNX_MOD_MAPPED;
    r = fnvnx_image_relocate(&L->img, p->os, &L->relocs_applied);
    fnvnx_image_release(&L->img, p->os);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: relocating %s from 0x%08x to 0x%08x: %s", L->name, (unsigned)L->pe.image_base, (unsigned)d->base, fnvnx_strerror(r)); return r; }
    if ((r = exp_check(p, L))) return r;
    fnvnx_logf(p->os, "fnvnx-start: module %s mapped at 0x%08x, relocations %u", L->name, (unsigned)d->base, (unsigned)L->relocs_applied);
    return FNVNX_OK;
}

/* the static modules only: the on-demand ones wait for fnvnx_module_load */
int fnvnx_modules_map(fnvnx_proc *p) {
    unsigned k;
    int r;
    if ((r = check_table(p))) return r;
    for (k = 0; k < p->nmod; k++)
        if (p->cfg.modules[k].is_static && (r = load_one(p, k))) return r;
    return FNVNX_OK;
}

/* ---- M3, M4 setup ---- */

int fnvnx_modules_prepare_tls(fnvnx_proc *p) {
    unsigned k;
    uint32_t next = 1u;
    int r;
    for (k = 0; k < p->nmod; k++) {
        if (p->mod[k].state == FNVNX_MOD_UNLOADED) continue;
        if ((r = fnvnx_loaded_tls(p, &p->mod[k], next))) return r;
        if (p->mod[k].tls.present) next++;
    }
    p->tls_slots = next;
    return FNVNX_OK;
}

static int finish_one(fnvnx_proc *p, fnvnx_loaded *L) {
    int r;
    if ((r = fnvnx_loaded_entry(p, L, 0))) return r;
    if ((r = fnvnx_loaded_protect(p, L))) fnvnx_logf(p->os, "fnvnx-start: protecting %s failed", L->name);
    return r;
}

int fnvnx_modules_finish(fnvnx_proc *p) {
    unsigned k;
    int r;
    for (k = 0; k < p->nmod; k++)
        if (p->mod[k].state != FNVNX_MOD_UNLOADED && (r = finish_one(p, &p->mod[k]))) return r;
    return FNVNX_OK;
}

/* ---- M4: initialisation and notifications ---- */

typedef struct gthread { uint32_t teb, esp; } gthread;   /* the guest thread a call runs on: its TEB and current esp */

/* TLS callbacks of L with (base, reason, cb_reserved); then, when `entry` and L has an entry point, DllMain(base, reason,
 * reserved). Returns DllMain's eax, or 1 when there was none. */
static uint32_t notify(const fnvnx_proc *p, gthread t, const fnvnx_loaded *L, uint32_t reason, uint32_t cb_reserved,
                       int entry, uint32_t reserved) {
    uint32_t k;
    for (k = 0; k < L->ntls_cb; k++)
        (void)fnvnx_call_guest_at(p, t.teb, t.esp, L->tls_cb[k], L->img.load_base, reason, cb_reserved);
    return entry && L->entry_va ? fnvnx_call_guest_at(p, t.teb, t.esp, L->entry_va, L->img.load_base, reason, reserved) : 1u;
}

/* DLL_PROCESS_ATTACH of module k; FALSE (low byte) = refusal; on success k joins the initialisation order */
static int attach_process(fnvnx_proc *p, gthread t, unsigned k, uint32_t reserved) {
    fnvnx_loaded *L = &p->mod[k];
    if (L->ntls_cb || L->entry_va) fnvnx_logf(p->os, "fnvnx-start: init %s", L->name);
    if (!(notify(p, t, L, DLL_PROCESS_ATTACH, 0u, 1, reserved) & 0xffu)) {
        fnvnx_logf(p->os, "fnvnx-start: DllMain of %s returned FALSE (STATUS_DLL_INIT_FAILED)", L->name);
        L->state = FNVNX_MOD_FAILED;
        return FNVNX_START_E_DLLINIT;
    }
    L->state = FNVNX_MOD_READY;
    p->init_order[p->ninit++] = (uint16_t)k;
    return FNVNX_OK;
}

int fnvnx_start_init_modules(fnvnx_proc *p) {
    unsigned k;
    gthread t;
    int r;
    if (!p || !p->exe.nmap) return FNVNX_E_ARG;
    t.teb = p->main.teb_va;
    t.esp = p->main.stack_base;
    for (k = 0; k < p->nmod && !p->modules_ready; k++) {
        if (p->mod[k].state == FNVNX_MOD_FAILED) return FNVNX_START_E_DLLINIT;   /* a refused DllMain stays refused */
        if (p->mod[k].state == FNVNX_MOD_MAPPED && (r = attach_process(p, t, k, 1u))) return r;
    }
    p->modules_ready = 1;
    return FNVNX_OK;
}

static void attach_order(const fnvnx_proc *p, gthread t, uint32_t reason) {
    unsigned k;
    for (k = 0; k < p->ninit; k++) (void)notify(p, t, &p->mod[p->init_order[k]], reason, 0u, 1, 0u);
    (void)notify(p, t, &p->exe, reason, 0u, 0, 0u);
}

static void detach_order(const fnvnx_proc *p, gthread t, uint32_t reason, uint32_t reserved) {
    unsigned k;
    (void)notify(p, t, &p->exe, reason, reserved, 0, 0u);
    for (k = p->ninit; k--;) (void)notify(p, t, &p->mod[p->init_order[k]], reason, reserved, 1, reserved);
}

void fnvnx_modules_thread_attach(const fnvnx_thread_block *b) {
    gthread t;
    t.teb = b->teb_va; t.esp = b->stack_base;
    attach_order(fnvnx_start_proc(), t, DLL_THREAD_ATTACH);
}
void fnvnx_modules_thread_detach(const fnvnx_thread_block *b) {
    gthread t;
    t.teb = b->teb_va; t.esp = b->stack_base;
    detach_order(fnvnx_start_proc(), t, DLL_THREAD_DETACH, 0u);
}
void fnvnx_modules_process_detach(uint32_t reserved) {
    const fnvnx_proc *p = fnvnx_start_proc();
    gthread t;
    t.teb = p->main.teb_va; t.esp = p->main.stack_base;
    detach_order(p, t, DLL_PROCESS_DETACH, reserved);
}

/* ---- on demand: LoadLibrary ---- */

/* Map table row k and, depth first in import-directory order, every table module it imports that is not loaded yet; a
 * module joins `batch` after its dependencies (the Windows initialisation order). A module still being loaded (state
 * MAPPED) is not followed again: a cycle. */
static int dyn_map(fnvnx_proc *p, unsigned k, uint16_t *batch, unsigned *nb) {
    fnvnx_loaded *L = &p->mod[k];
    uint32_t rva, size, d;
    int r;
    if (L->state != FNVNX_MOD_UNLOADED) return FNVNX_OK;
    if ((r = load_one(p, k))) { L->state = FNVNX_MOD_FAILED; return r; }
    r = fnvnx_tls_layout_read(&L->img, &L->tls);
    if (!r && L->tls.present) {
        fnvnx_logf(p->os, "fnvnx-start: %s has a TLS directory: threads that exist already have no TLS slot for it, only static modules can", L->name);
        r = FNVNX_E_UNSUPPORTED;
    }
    if (r) { L->state = FNVNX_MOD_FAILED; return r; }
    rva = L->pe.ndirs > FNVNX_DIR_IMPORT ? L->pe.dir[FNVNX_DIR_IMPORT].rva : 0;
    size = L->pe.ndirs > FNVNX_DIR_IMPORT ? L->pe.dir[FNVNX_DIR_IMPORT].size : 0;
    for (d = 0; size && d + 20 <= size; d += 20) {
        const uint8_t *desc = (const uint8_t *)fnvnx_image_ptr(&L->img, L->img.load_base + rva + d, 20);
        const char *dll;
        int j;
        if (!desc) { L->state = FNVNX_MOD_FAILED; return FNVNX_E_RANGE; }
        if (!fnvnx_rd32(desc) && !fnvnx_rd32(desc + 12) && !fnvnx_rd32(desc + 16)) break;
        dll = fnvnx_image_str(&L->img, L->img.load_base + fnvnx_rd32(desc + 12), 256, NULL);
        if (!dll) { L->state = FNVNX_MOD_FAILED; return FNVNX_E_FORMAT; }
        if ((j = table_index(p, dll)) >= 0 && (r = dyn_map(p, (unsigned)j, batch, nb))) return r;
    }
    batch[(*nb)++] = (uint16_t)k;
    return FNVNX_OK;
}

int fnvnx_module_load(fnvnx_proc *p, uint32_t teb, uint32_t esp, const char *name, uint32_t *base) {
    uint16_t batch[FNVNX_MAX_MODULES];
    unsigned nb = 0, i, missing = 0;
    gthread t;
    int k, r;
    if (!p || !name || !base || !p->exe.nmap || !p->modules_ready) return FNVNX_E_ARG;
    if ((k = table_index(p, name)) < 0) { fnvnx_logf(p->os, "fnvnx-start: LoadLibrary: %s is not a module of the table", name); return FNVNX_E_ARG; }
    switch (p->mod[k].state) {
    case FNVNX_MOD_READY:
    case FNVNX_MOD_MAPPED: *base = p->mod[k].img.load_base; return FNVNX_OK;
    case FNVNX_MOD_FAILED: return FNVNX_START_E_DLLINIT;
    default: break;
    }
    r = dyn_map(p, (unsigned)k, batch, &nb);
    for (i = 0; !r && i < nb; i++) {   /* every module of the batch is mapped: bind, protect */
        fnvnx_loaded *L = &p->mod[batch[i]];
        uint32_t n = 0;
        int rb;
        fnvnx_logf(p->os, "fnvnx-start: binding the imports of %s", L->name);
        rb = fnvnx_imports_bind_strict(&L->img, p->os, fnvnx_modules_resolve, p, &n);
        L->imports_bound = n;
        if (rb == FNVNX_E_MISMATCH) missing = 1;
        else if (rb) r = rb;
    }
    if (!r && missing) r = FNVNX_START_E_IMPORTS;
    for (i = 0; !r && i < nb; i++) r = finish_one(p, &p->mod[batch[i]]);
    t.teb = teb; t.esp = esp;
    for (i = 0; !r && i < nb; i++) r = attach_process(p, t, batch[i], 0u);
    if (r) {   /* nothing is unmapped: what this call mapped and did not finish stays, failed */
        for (i = 0; i < nb; i++)
            if (p->mod[batch[i]].state != FNVNX_MOD_READY) p->mod[batch[i]].state = FNVNX_MOD_FAILED;
        if (p->mod[k].state == FNVNX_MOD_MAPPED) p->mod[k].state = FNVNX_MOD_FAILED;
        return r;
    }
    *base = p->mod[k].img.load_base;
    return FNVNX_OK;
}

/* ---- the image list ---- */

uint32_t fnvnx_images_count(const fnvnx_proc *p) { return p->exe.nmap ? 1u + p->ninit : 0u; }

int fnvnx_images_get(const fnvnx_proc *p, uint32_t i, fnvnx_image_info *out) {
    const fnvnx_loaded *L;
    if (!p || !out || i >= fnvnx_images_count(p)) return FNVNX_E_ARG;
    L = i ? &p->mod[p->init_order[i - 1u]] : &p->exe;
    out->name = L->name;
    out->base = L->img.load_base;
    out->size_of_image = L->pe.size_of_image;
    out->entry_va = L->entry_va;
    out->tls_index = L->tls_index;
    out->has_tls = L->tls.present;
    out->map = L->map;
    out->nmap = L->nmap;
    return FNVNX_OK;
}
