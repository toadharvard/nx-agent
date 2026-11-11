/* The start sequence S1..S8, see include/fnvnx_start.h; the lifted PE modules are src/modules.c. */
#include "start_internal.h"
#include "fnvnx_identity.h"

#define PAGE FNVNX_PAGE
#define NOINLINE __attribute__((noinline))

static uint32_t page_up(uint32_t v) { return (v + PAGE - 1) & ~(PAGE - 1); }
uint8_t *fnvnx_host(const fnvnx_proc *p, uint32_t va) { return (uint8_t *)((uintptr_t)va + p->cfg.bias); }

unsigned fnvnx_scn_prot(uint32_t ch) {
    return ((ch & FNVNX_SCN_MEM_READ) ? FNVNX_PROT_R : 0u) | ((ch & FNVNX_SCN_MEM_WRITE) ? FNVNX_PROT_W : 0u) |
           ((ch & FNVNX_SCN_MEM_EXECUTE) ? FNVNX_PROT_X : 0u);
}

/* the section map entry (not the headers) wholly containing [va, va+size) */
const fnvnx_start_range *fnvnx_range_of(const fnvnx_loaded *L, uint32_t va, uint32_t size) {
    unsigned i;
    for (i = 0; i < L->nmap; i++)
        if (L->map[i].sec != 0xffffu && fnvnx_in(va, size, L->map[i].va, L->map[i].va + L->map[i].size))
            return &L->map[i];
    return NULL;
}

/* The reserved address set minus [LO, HI) (the caller checks that): the exe image and the range of EVERY row of the module
 * table, mapped or not. `skip`: -2 leaves the exe out, k >= 0 leaves table row k out (the image being mapped is that
 * one), -1 leaves nothing out. */
int fnvnx_images_overlap(const fnvnx_proc *p, uint32_t base, uint32_t size, int skip) {
    unsigned i;
    uint64_t eb = p->exe.pe.image_base, en = p->exe.pe.size_of_image;
    if (skip != -2 && en && (uint64_t)base < eb + en && (uint64_t)base + size > eb) return 1;
    for (i = 0; i < p->nmod; i++) {
        const fnvnx_module_desc *d = &p->cfg.modules[i];
        if ((int)i != skip && (uint64_t)base < (uint64_t)d->base + d->size_of_image && (uint64_t)base + size > d->base) return 1;
    }
    return 0;
}

/* S1. Assumption: the whole file fits in memory for the moment of the check (os->alloc of file_size bytes). */
static int s1_identity(fnvnx_proc *p) {
    uint8_t *buf;
    int r;
    if (!p->cfg.file_size) { fnvnx_logf(p->os, "fnvnx-start: empty executable"); return FNVNX_E_FORMAT; }
    buf = (uint8_t *)p->os->alloc(p->os->ctx, p->cfg.file_size);
    if (!buf) return FNVNX_E_NOMEM;
    r = p->os->read(p->os->ctx, FNVNX_FILE_EXE, 0, buf, p->cfg.file_size) ? FNVNX_E_IO : FNVNX_OK;
    if (!r && fnvnx_image_identity(buf, p->cfg.file_size, p->cfg.sha256)) {
        fnvnx_logf(p->os, "fnvnx-start: identity mismatch: the exe is not the one the image was built from");
        r = FNVNX_START_E_IDENTITY;
    }
    p->os->free(p->os->ctx, buf);
    if (!r) fnvnx_logf(p->os, "fnvnx-start: identity ok");
    return r;
}

/* S3 / M2. Windows rule: headers and every section (discardable ones too) at base + rva, VirtualSize page-rounded
 * (SizeOfRawData when VirtualSize is 0), min(raw, virtual) bytes from the file, the rest zero. Every other page of
 * [base, base + SizeOfImage) is claimed with no access, so nothing else can ever be placed inside the image range.
 * Assumption: the image is mapped at `base` with no relocation applied yet (a module's relocation follows in M2). */
int fnvnx_loaded_map(fnvnx_proc *p, fnvnx_loaded *L, uint32_t base, int self) {
    const fnvnx_pe *pe = &L->pe;
    uint32_t end = base + pe->size_of_image, cur = base;
    unsigned i;
    int r;
    if (!fnvnx_in(base, pe->size_of_image, 0, 0xffff0000u) ||
        (base < FNVNX_START_RESERVED_HI && end > FNVNX_START_RESERVED_LO) || fnvnx_images_overlap(p, base, pe->size_of_image, self)) {
        fnvnx_logf(p->os, "fnvnx-start: %s: image range 0x%08x+0x%x not usable", L->name, (unsigned)base, (unsigned)pe->size_of_image);
        return FNVNX_E_RANGE;
    }
    r = fnvnx_image_map(&L->img, pe, p->os, base, p->cfg.bias, base, end);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: mapping %s failed: %s", L->name, fnvnx_strerror(r)); return r; }
    for (i = 0; i < L->img.nmapped; i++) {   /* ascending: headers, then sections in rva order (pe_open) */
        const fnvnx_mapping *m = &L->img.map[i];
        fnvnx_start_range *q = &L->map[L->nmap++];
        q->va = m->va; q->size = m->size; q->sec = m->sec;
        if (m->sec == 0xffffu) { q->prot = FNVNX_PROT_R; q->name[0] = 0; }
        else {
            q->prot = fnvnx_scn_prot(pe->sec[m->sec].characteristics);
            memcpy(q->name, pe->sec[m->sec].name, sizeof q->name);
        }
        if (m->va > cur) { L->gap[L->ngap].va = cur; L->gap[L->ngap++].size = m->va - cur; }
        cur = m->va + m->size;
    }
    if (end > cur) { L->gap[L->ngap].va = cur; L->gap[L->ngap++].size = end - cur; }
    for (i = 0; i < L->ngap; i++)
        if (p->os->map_fixed(p->os->ctx, (uintptr_t)L->gap[i].va + p->cfg.bias, L->gap[i].size, 0u)) {
            fnvnx_logf(p->os, "fnvnx-start: claiming 0x%08x+0x%x failed", (unsigned)L->gap[i].va, (unsigned)L->gap[i].size);
            return FNVNX_E_MAP;
        }
    return FNVNX_OK;
}

/* S5 / M3. The Windows loader gives the main module TLS index 0 and every other module with a TLS directory the next
 * index; the code reaches its thread data as ((char **)fs:[0x2c])[index]. Assumptions: the template is host-readable
 * after S6 (its section has IMAGE_SCN_MEM_READ, checked here), every callback lies in an executable section (it is
 * entered through the lifter), and the directory asks for at most page alignment. */
int fnvnx_loaded_tls(fnvnx_proc *p, fnvnx_loaded *L, uint32_t index) {
    const fnvnx_start_range *s;
    uint8_t *w;
    uint32_t k;
    int r = fnvnx_tls_layout_read(&L->img, &L->tls);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: %s: TLS directory: %s", L->name, fnvnx_strerror(r)); return r; }
    if (!L->tls.present) return FNVNX_OK;
    w = (uint8_t *)fnvnx_image_ptr(&L->img, L->tls.index_va, 4);
    if (!w) return FNVNX_E_RANGE;
    L->tls_index = index;
    fnvnx_wr32(w, index);
    if (L->tls.template_size &&
        (!(s = fnvnx_range_of(L, L->tls.template_va, L->tls.template_size)) || !(s->prot & FNVNX_PROT_R))) {
        fnvnx_logf(p->os, "fnvnx-start: %s: TLS template is not in a readable section", L->name);
        return FNVNX_START_E_STATE;
    }
    if (L->tls.align > PAGE) { fnvnx_logf(p->os, "fnvnx-start: %s: TLS alignment above a page", L->name); return FNVNX_START_E_STATE; }
    r = fnvnx_image_tls_callbacks(&L->img, L->tls_cb, FNVNX_START_MAX_TLS_CALLBACKS, &k);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: %s: TLS callback list: %s", L->name, fnvnx_strerror(r)); return r; }
    if (k > FNVNX_START_MAX_TLS_CALLBACKS) { fnvnx_logf(p->os, "fnvnx-start: %s: more than %u TLS callbacks", L->name, FNVNX_START_MAX_TLS_CALLBACKS); return FNVNX_E_NOMEM; }
    L->ntls_cb = k;
    for (k = 0; k < L->ntls_cb; k++)
        if (!(s = fnvnx_range_of(L, L->tls_cb[k], 1)) || !(s->prot & FNVNX_PROT_X)) {
            fnvnx_logf(p->os, "fnvnx-start: %s: TLS callback 0x%08x is not in an executable section", L->name, (unsigned)L->tls_cb[k]);
            return FNVNX_START_E_STATE;
        }
    return FNVNX_OK;
}

/* the entry point is entered through the lifter: it must lie in an executable section */
int fnvnx_loaded_entry(fnvnx_proc *p, fnvnx_loaded *L, int required) {
    const fnvnx_start_range *s;
    L->entry_va = 0;
    if (!L->pe.entry_rva && !required) return FNVNX_OK;
    L->entry_va = L->img.load_base + L->pe.entry_rva;
    if (!L->pe.entry_rva || !(s = fnvnx_range_of(L, L->entry_va, 1)) || !(s->prot & FNVNX_PROT_X)) {
        fnvnx_logf(p->os, "fnvnx-start: entry point 0x%08x of %s is not in an executable section", (unsigned)L->entry_va, L->name);
        return FNVNX_START_E_STATE;
    }
    return FNVNX_OK;
}

/* S6 / M4. Final protection, applied after S4/S5 wrote into the image: readable if IMAGE_SCN_MEM_READ, writable if
 * IMAGE_SCN_MEM_WRITE, never host-executable (the x86 bytes are data); headers read-only; claimed gaps no access. */
int fnvnx_loaded_protect(fnvnx_proc *p, fnvnx_loaded *L) {
    unsigned i;
    int r = fnvnx_image_finalize(&L->img, p->os);   /* the mapping's prot is the section's R/W bits */
    if (r) return r;
    for (i = 0; i < L->ngap; i++)
        if (p->os->protect(p->os->ctx, (uintptr_t)L->gap[i].va + p->cfg.bias, L->gap[i].size, 0u)) return FNVNX_E_MAP;
    return FNVNX_OK;
}

/* The reserved address set: [FNVNX_START_RESERVED_LO, HI), the exe image and the range of every row of the module table
 * (mapped or not). 1 when [va, va+size) intersects it or does not lie in the 32-bit address space. */
int fnvnx_address_reserved(const fnvnx_proc *p, uint32_t va, uint32_t size) {
    return (uint64_t)va + size > 0x100000000ull || (va < FNVNX_START_RESERVED_HI && (uint64_t)va + size > FNVNX_START_RESERVED_LO) ||
           fnvnx_images_overlap(p, va, size, -1);
}

/* guest memory from the platform; its answer is checked against the rules the platform promised */
static uint32_t alloc_checked(fnvnx_proc *p, uint32_t size) {
    uint32_t va = p->os->alloc_va(p->os->ctx, size, FNVNX_PROT_R | FNVNX_PROT_W);
    if (!va || (va & (PAGE - 1)) || fnvnx_address_reserved(p, va, size)) {
        fnvnx_logf(p->os, "fnvnx-start: guest memory request of 0x%x bytes failed", (unsigned)size);
        return 0;
    }
    return va;
}

/* The image that owns TLS index idx (0: the exe, which may have no TLS directory), or NULL. */
static const fnvnx_loaded *tls_owner(const fnvnx_proc *p, uint32_t idx) {
    unsigned i;
    if (idx == 0) return &p->exe;
    for (i = 0; i < p->nmod; i++)
        if (p->mod[i].state != FNVNX_MOD_UNLOADED && p->mod[i].tls.present && p->mod[i].tls_index == idx) return &p->mod[i];
    return NULL;
}

/* A thread's TLS area: the vector of tls_slots words, then the block of every image with a TLS directory at its own
 * alignment, in index order. off[idx] = offset of the block of index idx (0 when there is none). Returns the page-rounded
 * size, or 0 when it does not fit. */
static uint32_t tls_plan(const fnvnx_proc *p, uint32_t off[FNVNX_MAX_MODULES + 1u]) {
    uint32_t cur = p->tls_slots * 4u, idx;
    for (idx = 0; idx < p->tls_slots; idx++) {
        const fnvnx_loaded *L = tls_owner(p, idx);
        off[idx] = 0;
        if (!L || !L->tls.present) continue;
        cur = (cur + L->tls.align - 1) / L->tls.align * L->tls.align;
        if ((uint64_t)cur + L->tls.block_size > 0xffff0000u) return 0;
        off[idx] = cur;
        cur += L->tls.block_size;
    }
    return page_up(cur);
}

static int release(fnvnx_proc *p, uint32_t va, uint32_t size) {
    return va && p->os->free_va(p->os->ctx, va, size) ? FNVNX_E_MAP : FNVNX_OK;
}

int fnvnx_thread_block_free(fnvnx_proc *p, const fnvnx_thread_block *b) {
    uint32_t off[FNVNX_MAX_MODULES + 1u], total;
    int r, r2;
    if (!p || !b || !p->exe.nmap || !p->os->free_va) return FNVNX_E_ARG;
    total = tls_plan(p, off);
    r = release(p, b->guard_va, b->stack_base - b->guard_va);
    if ((r2 = release(p, b->teb_va, PAGE)) && !r) r = r2;
    if ((r2 = release(p, b->tls_vec_va, total)) && !r) r = r2;
    return r;
}

int fnvnx_thread_block_create(fnvnx_proc *p, uint32_t stack_size, fnvnx_thread_block *out) {
    uint32_t stack = 0, teb = 0, tls = 0, ssize, off[FNVNX_MAX_MODULES + 1u], total, idx;
    uint8_t *t;
    int r = FNVNX_E_NOMEM;
    if (!p || !out || !p->exe.nmap || !p->os->alloc_va || !p->os->free_va || !p->os->protect) return FNVNX_E_ARG;
    if (!stack_size || stack_size > 0xffff0000u - PAGE) {
        fnvnx_logf(p->os, "fnvnx-start: stack size 0x%x not usable", (unsigned)stack_size);
        return FNVNX_START_E_STATE;
    }
    ssize = page_up(stack_size);
    if (!(total = tls_plan(p, off))) return FNVNX_START_E_STATE;
    memset(out, 0, sizeof *out);
    if (!(stack = alloc_checked(p, ssize + PAGE)) || !(teb = alloc_checked(p, PAGE)) ||
        !(tls = alloc_checked(p, total)))
        goto fail;
    r = FNVNX_E_MAP;
    if (p->os->protect(p->os->ctx, (uintptr_t)stack + p->cfg.bias, PAGE, 0u)) goto fail;   /* guard */
    out->guard_va = stack;
    out->stack_limit = stack + PAGE;
    out->stack_base = stack + PAGE + ssize;
    out->teb_va = teb;
    out->tls_vec_va = tls;
    out->tls_block_va = p->exe.tls.present ? tls + off[0] : 0u;
    for (idx = 0; idx < p->tls_slots; idx++) {   /* every block from its image's template; the vector slot is its address */
        const fnvnx_loaded *L = tls_owner(p, idx);
        if (!L || !L->tls.present) continue;
        if ((r = fnvnx_tls_block_init(&L->img, &L->tls, fnvnx_host(p, tls + off[idx])))) goto fail;
        fnvnx_wr32(fnvnx_host(p, tls) + 4u * idx, tls + off[idx]);
    }
    t = fnvnx_host(p, teb);                        /* the page is zeroed by alloc_va */
    fnvnx_wr32(t + 0x00, 0xffffffffu);             /* end of the SEH chain */
    fnvnx_wr32(t + 0x04, out->stack_base);
    fnvnx_wr32(t + 0x08, out->stack_limit);
    fnvnx_wr32(t + 0x18, teb);                     /* NT_TIB.Self */
    fnvnx_wr32(t + 0x2c, tls);                     /* ThreadLocalStoragePointer */
    return FNVNX_OK;
fail:   /* nothing stays allocated when the block cannot be made */
    (void)release(p, stack, ssize + PAGE);
    (void)release(p, teb, PAGE);
    (void)release(p, tls, total);
    memset(out, 0, sizeof *out);
    return r;
}

/* S4 / M2: every image's import slots (the exe's, then each module's), one resolver (fnvnx_modules_resolve). Every
 * slot without a provider is listed; the start is refused after all images were visited. */
static int s4_bind(fnvnx_proc *p) {
    unsigned i, missing = 0;
    for (i = 0; i <= p->nmod; i++) {
        fnvnx_loaded *L = i ? &p->mod[i - 1] : &p->exe;
        uint32_t n = 0;
        int r;
        if (i && L->state == FNVNX_MOD_UNLOADED) continue;   /* on demand: bound by fnvnx_module_load */
        if (p->nmod) fnvnx_logf(p->os, "fnvnx-start: binding the imports of %s", L->name);
        r = fnvnx_imports_bind_strict(&L->img, p->os, fnvnx_modules_resolve, p, &n);
        L->imports_bound = n;
        if (r == FNVNX_E_MISMATCH) missing = 1;
        else if (r) { fnvnx_logf(p->os, "fnvnx-start: imports of %s not bound (%s)", L->name, fnvnx_strerror(r)); return r; }
    }
    if (missing) { fnvnx_logf(p->os, "fnvnx-start: imports not bound (%s)", fnvnx_strerror(FNVNX_E_MISMATCH)); return FNVNX_START_E_IMPORTS; }
    fnvnx_logf(p->os, "fnvnx-start: imports bound %u", (unsigned)p->exe.imports_bound);
    for (i = 0; i < p->nmod; i++)
        if (p->mod[i].state != FNVNX_MOD_UNLOADED)
            fnvnx_logf(p->os, "fnvnx-start: module %s imports bound %u", p->mod[i].name, (unsigned)p->mod[i].imports_bound);
    return FNVNX_OK;
}

int fnvnx_start_prepare(fnvnx_proc *p, const fnvnx_os *os, const fnvnx_start_cfg *cfg) {
    int r;
    if (!p || !os || !cfg || !os->read || !os->map_fixed || !os->protect || !os->alloc || !os->free || !os->log ||
        !os->alloc_va || !os->free_va || !cfg->resolve || !cfg->exe_name || (cfg->nmodules && !cfg->modules))
        return FNVNX_E_ARG;
    if (cfg->nmodules > FNVNX_MAX_MODULES) {
        fnvnx_logf(os, "fnvnx-start: %u modules, at most %u are supported", (unsigned)cfg->nmodules, FNVNX_MAX_MODULES);
        return FNVNX_START_E_STATE;
    }
    memset(p, 0, sizeof *p);
    p->os = os;
    p->cfg = *cfg;
    p->nmod = cfg->nmodules;
    p->exe.name = cfg->exe_name;
    p->exe.file = FNVNX_FILE_EXE;
    if ((r = s1_identity(p))) return r;
    if ((r = fnvnx_pe_open(&p->exe.pe, os, FNVNX_FILE_EXE, cfg->file_size))) {   /* S2 */
        fnvnx_logf(os, "fnvnx-start: PE headers: %s", fnvnx_strerror(r));
        return r;
    }
    p->exe.img.pe = &p->exe.pe;
    if ((r = fnvnx_loaded_map(p, &p->exe, p->exe.pe.image_base, -2))) return r;   /* S3 */
    fnvnx_logf(os, "fnvnx-start: image mapped");
    if ((r = fnvnx_modules_map(p))) return r;                                    /* M2: files, hashes, map, relocate */
    if ((r = s4_bind(p))) return r;                                              /* S4 */
    if ((r = fnvnx_loaded_tls(p, &p->exe, 0))) return r;                         /* S5, M3 */
    p->tls_slots = 1u;
    if ((r = fnvnx_modules_prepare_tls(p))) return r;
    if ((r = fnvnx_loaded_entry(p, &p->exe, 1))) return r;
    if ((r = fnvnx_modules_finish(p))) return r;
    if ((r = fnvnx_loaded_protect(p, &p->exe))) { fnvnx_logf(os, "fnvnx-start: protecting the image failed"); return r; }   /* S6 */
    return fnvnx_thread_block_create(p, p->exe.pe.stack_reserve, &p->main);   /* S7 */
}

/* S8. Guest stack of a thread: the words under [esp] are rewritten before every call. */
uint32_t fnvnx_call_guest(const fnvnx_proc *p, const fnvnx_thread_block *b, uint32_t eip, uint32_t a1, uint32_t a2, uint32_t a3) {
    return fnvnx_call_guest_at(p, b->teb_va, b->stack_base, eip, a1, a2, a3);
}

uint32_t fnvnx_call_guest_at(const fnvnx_proc *p, uint32_t teb, uint32_t esp_now, uint32_t eip, uint32_t a1, uint32_t a2, uint32_t a3) {
    uint32_t esp = (esp_now - 16u) & ~15u;
    uint8_t *s = fnvnx_host(p, esp);
    fnvnx_wr32(s, FNVNX_START_SENTINEL);
    fnvnx_wr32(s + 4, a1);
    fnvnx_wr32(s + 8, a2);
    fnvnx_wr32(s + 12, a3);
    return fnvnx_lift_enter(eip, esp, teb);
}

NOINLINE uint32_t fnvnx_start_enter(fnvnx_proc *p) {
    uint32_t k;
    if (!p->modules_ready && fnvnx_start_init_modules(p)) return FNVNX_START_EXIT_DLLINIT;
    for (k = 0; k < p->exe.ntls_cb; k++)   /* (hModule, DLL_PROCESS_ATTACH, reserved); the result is ignored */
        (void)fnvnx_call_guest(p, &p->main, p->exe.tls_cb[k], p->exe.pe.image_base, 1u, 0u);
    fnvnx_logf(p->os, "fnvnx-start: enter %08x", (unsigned)p->exe.entry_va);
    return fnvnx_call_guest(p, &p->main, p->exe.entry_va, 0u, 0u, 0u);
}
