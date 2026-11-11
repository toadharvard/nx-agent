/* hooks_decl.h - the hooks the product's boundary (boundary_guest.cpp) calls: provided by the loader (lead L1) and the
 * lifter runtime (lead L3a), whose headers are not in this branch. Each declaration is copied from the header named
 * with it, character for character in name and signature; the platform archive's undefined list shows exactly these
 * names (platform/guest_hooks.txt names the provider of each). A change of a signature there is a change here. */
#ifndef FNVNX_BOUNDARY_HOOKS_DECL_H
#define FNVNX_BOUNDARY_HOOKS_DECL_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- origin/claude/fnv-l1-contract: ports/fnv-nx/loader/include/fnvnx_importkey.h */
uint32_t fnvnx_import_key(const char *dll, const char *name, uint32_t ordinal);

/* ---- origin/claude/fnv-l1-contract: ports/fnv-nx/loader/include/fnvnx_start.h (the process record is opaque here) */
struct fnvnx_proc;
typedef struct fnvnx_thread_block {
    uint32_t guard_va;
    uint32_t stack_limit;
    uint32_t stack_base;           /* one past the highest stack address (TEB +0x04) */
    uint32_t teb_va;               /* the 4 KiB TEB page; fs:[0] of the thread */
    uint32_t tls_vec_va;
    uint32_t tls_block_va;
} fnvnx_thread_block;
#define FNVNX_START_E_IMPORTS (-41)   /* import slots without provider */
#define FNVNX_START_E_DLLINIT (-43)   /* a module's DllMain(DLL_PROCESS_ATTACH) returned FALSE */
int fnvnx_thread_block_create(struct fnvnx_proc *p, uint32_t stack_size, fnvnx_thread_block *out);
int fnvnx_thread_block_free(struct fnvnx_proc *p, const fnvnx_thread_block *b);
int fnvnx_module_load(struct fnvnx_proc *p, uint32_t teb, uint32_t esp, const char *name, uint32_t *base);
void fnvnx_modules_thread_attach(const fnvnx_thread_block *b);
void fnvnx_modules_thread_detach(const fnvnx_thread_block *b);
void fnvnx_modules_process_detach(uint32_t reserved);
const struct fnvnx_proc *fnvnx_start_proc(void);
uint32_t fnvnx_lift_enter(uint32_t exe_eip, uint32_t esp, uint32_t teb);

/* ---- origin/claude/fnv-l1-contract: ports/fnv-nx/loader/include/fnvnx_loader.h */
#define FNVNX_OK 0
#define FNVNX_E_ARG (-10)

/* ---- origin/claude/fnv-l3a-lift: ports/fnv-nx/lift/rt/enter.c (the layout of struct fnvnx_guest_ret is the same as
 * struct fnvnx_bnd_ret of fnvnx_boundary.h) */
struct fnvnx_guest_ret { uint32_t eax, edx; double st0; };
void fnvnx_guest_invoke(uint32_t addr, uint32_t ecx, uint32_t nargs, const uint32_t *args, uint32_t kind,
                        struct fnvnx_guest_ret *out);

/* ---- REQUESTED from L3a, not in any header yet: fnvnx_module_load needs the calling guest thread's esp and TEB (the
 * frames of DllMain go below that esp), and the platform has no other way to know them. esp = the guest esp of the
 * import call in progress (c->r[4]), teb = the thread's TEB (fs base). */
void fnvnx_guest_current(uint32_t *esp, uint32_t *teb);

#ifdef __cplusplus
}
#endif
#endif
