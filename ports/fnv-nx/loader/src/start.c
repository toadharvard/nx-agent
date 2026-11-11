/* The start sequence S1..S8, see include/fnvnx_start.h. */
#include "fnvnx_start.h"
#include "fnvnx_identity.h"
#include "internal.h"

#define PAGE 0x1000u
#define NOINLINE __attribute__((noinline))

static uint32_t page_up(uint32_t v) { return (v + PAGE - 1) & ~(PAGE - 1); }
static uint8_t *host(const fnvnx_proc *p, uint32_t va) { return (uint8_t *)((uintptr_t)va + p->cfg.bias); }

static unsigned scn_prot(uint32_t ch) {
    return ((ch & FNVNX_SCN_MEM_READ) ? FNVNX_PROT_R : 0u) | ((ch & FNVNX_SCN_MEM_WRITE) ? FNVNX_PROT_W : 0u) |
           ((ch & FNVNX_SCN_MEM_EXECUTE) ? FNVNX_PROT_X : 0u);
}

/* the section map entry (not the headers) wholly containing [va, va+size) */
static const fnvnx_start_range *range_of(const fnvnx_proc *p, uint32_t va, uint32_t size) {
    unsigned i;
    for (i = 0; i < p->nmap; i++)
        if (p->map[i].sec != 0xffffu && fnvnx_in(va, size, p->map[i].va, p->map[i].va + p->map[i].size))
            return &p->map[i];
    return NULL;
}

/* S1. Assumption: the whole file fits in memory for the moment of the check (os->alloc of file_size bytes). */
static int s1_identity(fnvnx_proc *p) {
    uint8_t *buf;
    int r;
    if (!p->cfg.file_size) { fnvnx_logf(p->os, "fnvnx-start: empty executable"); return FNVNX_E_FORMAT; }
    buf = (uint8_t *)p->os->alloc(p->os->ctx, p->cfg.file_size);
    if (!buf) return FNVNX_E_NOMEM;
    r = p->os->read(p->os->ctx, 0, buf, p->cfg.file_size) ? FNVNX_E_IO : FNVNX_OK;
    if (!r && fnvnx_image_identity(buf, p->cfg.file_size, p->cfg.sha256)) {
        fnvnx_logf(p->os, "fnvnx-start: identity mismatch: the exe is not the one the image was built from");
        r = FNVNX_START_E_IDENTITY;
    }
    p->os->free(p->os->ctx, buf);
    if (!r) fnvnx_logf(p->os, "fnvnx-start: identity ok");
    return r;
}

/* S3. Windows rule (assumption: the exe is mapped at its preferred base, so no relocation): headers and every
 * section (discardable ones too) at base + rva, VirtualSize page-rounded (SizeOfRawData when VirtualSize is 0),
 * min(raw, virtual) bytes from the file, the rest zero. Every other page of [base, base + SizeOfImage) is claimed
 * with no access, so nothing else can ever be placed inside the image range. */
static int s3_map(fnvnx_proc *p) {
    const fnvnx_pe *pe = &p->pe;
    uint32_t base = pe->image_base, end = base + pe->size_of_image, cur = base;
    unsigned i;
    int r;
    if (!fnvnx_in(base, pe->size_of_image, 0, 0xffff0000u) ||
        (base < FNVNX_START_RESERVED_HI && end > FNVNX_START_RESERVED_LO)) {
        fnvnx_logf(p->os, "fnvnx-start: image range 0x%08x+0x%x not usable", (unsigned)base, (unsigned)pe->size_of_image);
        return FNVNX_E_RANGE;
    }
    r = fnvnx_image_map(&p->img, pe, p->os, base, p->cfg.bias, base, end, FNVNX_MAP_HEADERS | FNVNX_MAP_ALL);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: mapping the image failed: %s", fnvnx_strerror(r)); return r; }
    for (i = 0; i < p->img.nmapped; i++) {   /* ascending: headers, then sections in rva order (pe_open) */
        const fnvnx_mapping *m = &p->img.map[i];
        fnvnx_start_range *q = &p->map[p->nmap++];
        q->va = m->va; q->size = m->size; q->sec = m->sec;
        if (m->sec == 0xffffu) { q->prot = FNVNX_PROT_R; q->name[0] = 0; }
        else {
            q->prot = scn_prot(pe->sec[m->sec].characteristics);
            memcpy(q->name, pe->sec[m->sec].name, sizeof q->name);
        }
        if (m->va > cur) { p->gap[p->ngap].va = cur; p->gap[p->ngap++].size = m->va - cur; }
        cur = m->va + m->size;
    }
    if (end > cur) { p->gap[p->ngap].va = cur; p->gap[p->ngap++].size = end - cur; }
    for (i = 0; i < p->ngap; i++)
        if (p->os->map_fixed(p->os->ctx, (uintptr_t)p->gap[i].va + p->cfg.bias, p->gap[i].size, 0u)) {
            fnvnx_logf(p->os, "fnvnx-start: claiming 0x%08x+0x%x failed", (unsigned)p->gap[i].va, (unsigned)p->gap[i].size);
            return FNVNX_E_MAP;
        }
    fnvnx_logf(p->os, "fnvnx-start: image mapped");
    return FNVNX_OK;
}

/* S5. The Windows loader gives the main module TLS index 0; the exe's own code reaches its thread data as
 * ((char **)fs:[0x2c])[index]. Assumptions: the template is host-readable after S6 (its section has IMAGE_SCN_MEM_READ,
 * checked here), every callback lies in an executable section (it is entered through the lifter), and the
 * directory asks for at most page alignment. */
static int s5_tls(fnvnx_proc *p) {
    const fnvnx_start_range *s;
    uint8_t *w;
    uint32_t k;
    int r = fnvnx_tls_layout_read(&p->img, &p->tls);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: TLS directory: %s", fnvnx_strerror(r)); return r; }
    if (!p->tls.present) return FNVNX_OK;
    w = (uint8_t *)fnvnx_image_ptr(&p->img, p->tls.index_va, 4);
    if (!w) return FNVNX_E_RANGE;
    fnvnx_wr32(w, 0);
    if (p->tls.template_size &&
        (!(s = range_of(p, p->tls.template_va, p->tls.template_size)) || !(s->prot & FNVNX_PROT_R))) {
        fnvnx_logf(p->os, "fnvnx-start: TLS template is not in a readable section");
        return FNVNX_START_E_STATE;
    }
    if (p->tls.align > PAGE) { fnvnx_logf(p->os, "fnvnx-start: TLS alignment above a page"); return FNVNX_START_E_STATE; }
    r = fnvnx_image_tls_callbacks(&p->img, p->tls_cb, FNVNX_START_MAX_TLS_CALLBACKS, &k);
    if (r) { fnvnx_logf(p->os, "fnvnx-start: TLS callback list: %s", fnvnx_strerror(r)); return r; }
    if (k > FNVNX_START_MAX_TLS_CALLBACKS) { fnvnx_logf(p->os, "fnvnx-start: more than %u TLS callbacks", FNVNX_START_MAX_TLS_CALLBACKS); return FNVNX_E_NOMEM; }
    p->ntls_cb = k;
    for (k = 0; k < p->ntls_cb; k++)
        if (!(s = range_of(p, p->tls_cb[k], 1)) || !(s->prot & FNVNX_PROT_X)) {
            fnvnx_logf(p->os, "fnvnx-start: TLS callback 0x%08x is not in an executable section", (unsigned)p->tls_cb[k]);
            return FNVNX_START_E_STATE;
        }
    return FNVNX_OK;
}

/* S6. Final protection, applied after S4/S5 wrote into the image: readable if IMAGE_SCN_MEM_READ, writable if
 * IMAGE_SCN_MEM_WRITE, never host-executable (the x86 bytes are data); headers read-only; claimed gaps no access. */
static int s6_protect(fnvnx_proc *p) {
    unsigned i;
    int r = fnvnx_image_finalize(&p->img, p->os);   /* the mapping's prot is the section's R/W bits (FNVNX_MAP_ALL) */
    if (r) return r;
    for (i = 0; i < p->ngap; i++)
        if (p->os->protect(p->os->ctx, (uintptr_t)p->gap[i].va + p->cfg.bias, p->gap[i].size, 0u)) return FNVNX_E_MAP;
    return FNVNX_OK;
}

/* guest memory from the platform; its answer is checked against the rules the platform promised */
static uint32_t alloc_checked(fnvnx_proc *p, uint32_t size) {
    uint32_t va = p->os->alloc_va(p->os->ctx, size, FNVNX_PROT_R | FNVNX_PROT_W);
    uint32_t ib = p->pe.image_base;
    if (!va || (va & (PAGE - 1)) || (uint64_t)va + size > 0x100000000ull ||
        (va < FNVNX_START_RESERVED_HI && va + size > FNVNX_START_RESERVED_LO) ||
        ((uint64_t)va < (uint64_t)ib + p->pe.size_of_image && (uint64_t)va + size > ib)) {
        fnvnx_logf(p->os, "fnvnx-start: guest memory request of 0x%x bytes failed", (unsigned)size);
        return 0;
    }
    return va;
}

/* bytes of a thread's TLS area (vector, then the block at `off`), page-rounded; 0 when it does not fit */
static uint32_t tls_area(const fnvnx_proc *p, uint32_t *off) {
    uint32_t align = p->tls.present ? p->tls.align : 1u;
    *off = (FNVNX_START_TLS_SLOTS * 4u + align - 1) / align * align;   /* the block follows the vector */
    if (p->tls.present && (uint64_t)*off + p->tls.block_size > 0xffff0000u) return 0;
    return page_up(*off + (p->tls.present ? p->tls.block_size : 0u));
}

static int release(fnvnx_proc *p, uint32_t va, uint32_t size) {
    return va && p->os->free_va(p->os->ctx, va, size) ? FNVNX_E_MAP : FNVNX_OK;
}

int fnvnx_thread_block_free(fnvnx_proc *p, const fnvnx_thread_block *b) {
    uint32_t off, total;
    int r, r2;
    if (!p || !b || !p->nmap || !p->os->free_va) return FNVNX_E_ARG;
    total = tls_area(p, &off);
    r = release(p, b->guard_va, b->stack_base - b->guard_va);
    if ((r2 = release(p, b->teb_va, PAGE)) && !r) r = r2;
    if ((r2 = release(p, b->tls_vec_va, total)) && !r) r = r2;
    return r;
}

int fnvnx_thread_block_create(fnvnx_proc *p, uint32_t stack_size, fnvnx_thread_block *out) {
    uint32_t stack = 0, teb = 0, tls = 0, ssize, off, total;
    uint8_t *t;
    int r = FNVNX_E_NOMEM;
    if (!p || !out || !p->nmap || !p->os->alloc_va || !p->os->free_va || !p->os->protect) return FNVNX_E_ARG;
    if (!stack_size || stack_size > 0xffff0000u - PAGE) {
        fnvnx_logf(p->os, "fnvnx-start: stack size 0x%x not usable", (unsigned)stack_size);
        return FNVNX_START_E_STATE;
    }
    ssize = page_up(stack_size);
    if (!(total = tls_area(p, &off))) return FNVNX_START_E_STATE;
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
    out->tls_block_va = p->tls.present ? tls + off : 0u;
    if (p->tls.present && (r = fnvnx_tls_block_init(&p->img, &p->tls, host(p, out->tls_block_va)))) goto fail;
    fnvnx_wr32(host(p, tls), out->tls_block_va);   /* vector slot 0; the module's TLS index is 0 */
    t = host(p, teb);                              /* the page is zeroed by alloc_va */
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

int fnvnx_start_prepare(fnvnx_proc *p, const fnvnx_os *os, const fnvnx_start_cfg *cfg) {
    uint32_t n = 0;
    int r;
    if (!p || !os || !cfg || !os->read || !os->map_fixed || !os->protect || !os->alloc || !os->free || !os->log ||
        !os->alloc_va || !os->free_va || !cfg->resolve)
        return FNVNX_E_ARG;
    memset(p, 0, sizeof *p);
    p->os = os;
    p->cfg = *cfg;
    if ((r = s1_identity(p))) return r;
    if ((r = fnvnx_pe_open(&p->pe, os, cfg->file_size))) {   /* S2 */
        fnvnx_logf(os, "fnvnx-start: PE headers: %s", fnvnx_strerror(r));
        return r;
    }
    p->img.pe = &p->pe;
    if ((r = s3_map(p))) return r;
    r = fnvnx_imports_bind_strict(&p->img, os, cfg->resolve, cfg->resolve_ctx, &n);   /* S4 */
    if (r) { fnvnx_logf(os, "fnvnx-start: imports not bound (%s)", fnvnx_strerror(r)); return r == FNVNX_E_MISMATCH ? FNVNX_START_E_IMPORTS : r; }
    p->imports_bound = n;
    fnvnx_logf(os, "fnvnx-start: imports bound %u", (unsigned)n);
    if ((r = s5_tls(p))) return r;
    /* the entry point is entered through the lifter: it must lie in an executable section */
    p->entry_va = p->pe.image_base + p->pe.entry_rva;
    if (!p->pe.entry_rva || !range_of(p, p->entry_va, 1) || !(range_of(p, p->entry_va, 1)->prot & FNVNX_PROT_X)) {
        fnvnx_logf(os, "fnvnx-start: entry point 0x%08x is not in an executable section", (unsigned)p->entry_va);
        return FNVNX_START_E_STATE;
    }
    if ((r = s6_protect(p))) { fnvnx_logf(os, "fnvnx-start: protecting the image failed"); return r; }
    return fnvnx_thread_block_create(p, p->pe.stack_reserve, &p->main);   /* S7 */
}

/* S8. Guest stack of the main thread: the words under [esp] are rewritten before every call. */
static uint32_t call_guest(const fnvnx_proc *p, uint32_t eip, uint32_t esp, uint32_t a1, uint32_t a2, uint32_t a3) {
    uint8_t *s = host(p, esp);
    fnvnx_wr32(s, FNVNX_START_SENTINEL);
    fnvnx_wr32(s + 4, a1);
    fnvnx_wr32(s + 8, a2);
    fnvnx_wr32(s + 12, a3);
    return fnvnx_lift_enter(eip, esp, p->main.teb_va);
}

NOINLINE uint32_t fnvnx_start_enter(fnvnx_proc *p) {
    uint32_t esp = (p->main.stack_base - 16u) & ~15u, k;
    for (k = 0; k < p->ntls_cb; k++)   /* (hModule, DLL_PROCESS_ATTACH, reserved); the result is ignored */
        (void)call_guest(p, p->tls_cb[k], esp, p->pe.image_base, 1u, 0u);
    fnvnx_logf(p->os, "fnvnx-start: enter %08x", (unsigned)p->entry_va);
    return call_guest(p, p->entry_va, esp, 0u, 0u, 0u);
}
