/* boundary_guest.cpp - the product's implementation of shim/include/fnvnx_boundary.h (AArch64 ILP32): the game is
 * the lifted x86 code of lead L3a's runtime, the images and threads come from the loader of lead L1 (its headers,
 * loader/include, are included as they are). Host builds use tests/s4/host_boundary.cpp instead; this file is
 * compiled by platform/build_guest.sh only.
 *
 * Preconditions of the loader contract this file relies on (fnvnx_start.h): the process record fnvnx_start_proc()
 * returns is the one fnvnx_start_prepare filled; the thread blocks are made and freed on it; guest memory is at its
 * guest address (host bias 0 on the target). */
#include "fnvnx_boundary.h"
extern "C" {
#include "fnvnx_importkey.h"
#include "fnvnx_start.h"
}   /* the loader's headers declare C functions without guards */

#include <stdlib.h>

extern "C" {

/* The lifter runtime (lead L3a, ports/fnv-nx/lift/rt/enter.c; its header is not in this branch): the layout of
 * struct fnvnx_guest_ret is that of struct fnvnx_bnd_ret. fnvnx_guest_current is the contract requested from L3a:
 * the loader's module calls need the calling guest thread's esp and TEB (the frames of DllMain go below that esp), and
 * only the runtime knows them: esp = the guest esp of the import call in progress, teb = the thread's TEB (fs base). */
struct fnvnx_guest_ret { uint32_t eax, edx; double st0; };
void fnvnx_guest_invoke(uint32_t addr, uint32_t ecx, uint32_t nargs, const uint32_t *args, uint32_t kind,
                        struct fnvnx_guest_ret *out);
void fnvnx_guest_current(uint32_t *esp, uint32_t *teb);

}

static fnvnx_proc *proc(void) { return fnvnx_start_proc(); }

extern "C" {

/* An export's value is its import key: one value per function everywhere (pointer equality as on Windows). The key
 * table is the loader's; the native address is not needed. 0 = no row (the caller fails with ERROR_PROC_NOT_FOUND). */
fnvnx_gaddr fnvnx_bnd_export_value(const char *dll, const char *name, uint32_t ordinal, const void *)
{
    return fnvnx_import_key(dll, name, ordinal);
}

void fnvnx_bnd_guest_invoke(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind,
                            struct fnvnx_bnd_ret *out)
{
    static_assert(sizeof(fnvnx_gword) == 4, "the product is ILP32: a boundary word is one x86 stack word");
    static_assert(sizeof(struct fnvnx_bnd_ret) == sizeof(struct fnvnx_guest_ret), "one layout");
    /* the runtime's word array is uint32_t: copy, so no pointer type is reinterpreted (uintptr_t is not uint32_t on every ILP32 libc) */
    uint32_t w[FNVNX_BND_MAX_WORDS];
    if (nargs > FNVNX_BND_MAX_WORDS)
        nargs = FNVNX_BND_MAX_WORDS;   /* unreachable: fnvnx_guestfn.h static_asserts the word count of every call */
    for (uint32_t i = 0; i < nargs; i++)
        w[i] = (uint32_t)args[i];
    struct fnvnx_guest_ret r = {0, 0, 0.0};
    fnvnx_guest_invoke((uint32_t)addr, ecx, nargs, w, kind, &r);
    if (out) {
        out->eax = r.eax;
        out->edx = r.edx;
        out->st0 = r.st0;
    }
}

struct fnvnx_bnd_thread {
    fnvnx_gaddr start;
    fnvnx_thread_block blk;
};

fnvnx_bnd_thread *fnvnx_bnd_thread_create(fnvnx_gaddr start, size_t stack_size)
{
    fnvnx_bnd_thread *t = (fnvnx_bnd_thread *)calloc(1, sizeof *t);
    if (!t)
        return NULL;
    t->start = start;
    if (stack_size > 0xFFFFFFFFu || fnvnx_thread_block_create(proc(), (uint32_t)stack_size, &t->blk) != FNVNX_OK) {
        free(t);
        return NULL;
    }
    return t;
}

/* The start routine is a stdcall DWORD (*)(void *): at entry [esp] is the return address (the runtime stores the
 * sentinel there) and [esp+4] the parameter. The top of the stack holds both; the stdcall `ret 4` ends above them. */
uint32_t fnvnx_bnd_thread_run(fnvnx_bnd_thread *t, fnvnx_gaddr param)
{
    uint32_t esp = t->blk.stack_base - 16;
    *(uint32_t *)(uintptr_t)(esp + 4) = (uint32_t)param;
    fnvnx_modules_thread_attach(&t->blk);
    return fnvnx_lift_enter((uint32_t)t->start, esp, t->blk.teb_va);
}

void fnvnx_bnd_thread_release(fnvnx_bnd_thread *t, int notify)
{
    if (notify)
        fnvnx_modules_thread_detach(&t->blk);
    fnvnx_thread_block_free(proc(), &t->blk);
    free(t);
}

/* The loader's error codes become the Win32 error LoadLibrary sets (fnvnx_boundary.h). */
uint32_t fnvnx_bnd_module_load(const char *name, uint32_t *base)
{
    uint32_t esp = 0, teb = 0;
    fnvnx_guest_current(&esp, &teb);
    int rc = fnvnx_module_load(proc(), teb, esp, name, base);
    if (rc == FNVNX_OK)
        return 0;
    if (rc == FNVNX_E_ARG)
        return 126;    /* ERROR_MOD_NOT_FOUND: not in the table of translated modules */
    if (rc == FNVNX_START_E_IMPORTS)
        return 127;    /* ERROR_PROC_NOT_FOUND */
    if (rc == FNVNX_START_E_DLLINIT)
        return 1114;   /* ERROR_DLL_INIT_FAILED */
    return 193;        /* ERROR_BAD_EXE_FORMAT: file / hash / PE / relocation error, a TLS directory */
}

/* FreeLibrary at a platform count of 0: fnvnx_module_unload's results become the boundary's outcome or a Win32 error */
uint32_t fnvnx_bnd_module_unload(uint32_t base, int *outcome)
{
    uint32_t esp = 0, teb = 0;
    fnvnx_guest_current(&esp, &teb);
    int rc = fnvnx_module_unload(proc(), teb, esp, base);
    if (rc == FNVNX_OK) { *outcome = FNVNX_BND_UNLOADED; return 0; }
    if (rc == FNVNX_START_UNLOAD_KEPT) { *outcome = FNVNX_BND_KEPT; return 0; }
    if (rc == FNVNX_START_E_PINNED) { *outcome = FNVNX_BND_PINNED; return 0; }
    if (rc == FNVNX_START_E_NOTHELD)
        return 126;    /* ERROR_MOD_NOT_FOUND */
    return 6;          /* ERROR_INVALID_HANDLE: not the base of a loaded module (FNVNX_E_ARG), or any other refusal */
}

/* ExitProcess: DllMain(DLL_PROCESS_DETACH) with a non-NULL lpvReserved (the process is terminating) */
void fnvnx_bnd_process_detach(void)
{
    uint32_t esp = 0, teb = 0;
    fnvnx_guest_current(&esp, &teb);
    fnvnx_modules_process_detach(proc(), teb, esp, 1);
}

/* DisableThreadLibraryCalls: the loader's only refusal is a base that is no table module */
uint32_t fnvnx_bnd_disable_thread_calls(uint32_t base)
{
    return fnvnx_modules_disable_thread_calls(base) == FNVNX_OK ? 0 : 126;   /* ERROR_MOD_NOT_FOUND */
}

} // extern "C"
