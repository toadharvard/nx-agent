/* fnvnx_boundary.h - the ONE interface between the platform (Win32 imports, native code) and what is "the game".
 *
 * Code addresses that cross the boundary are not native pointers in the product: the game is lifted x86 code whose
 * function pointers are 32-bit guest values (a lifted function's image address, or the import KEY of a platform
 * function). The platform therefore never turns such a value into a call itself and never hands one of its own
 * function addresses to the game itself: it goes through the functions below.
 *
 * Exactly two link-time implementations exist (no weak symbols, no modes, no run-time "is the runtime present"):
 *   platform/boundary/boundary_guest.cpp   the product: loader (L1) + lifter runtime (L3a) hooks
 *   tests/s4/host_boundary.cpp             the host test process, where "the game" is the native test code: an export
 *                                          is its native address, a thread starts natively, there is no loader
 *
 * A "game address" (gaddr) is uintptr_t: 32 bits in the product (ILP32), the native address on the host.
 * Thread contexts: thread_run / thread_release are called on the OS thread that runs the game thread (the OS thread
 * itself is made by os_thread_create in platform/os). */
#ifndef FNVNX_BOUNDARY_H
#define FNVNX_BOUNDARY_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef uintptr_t fnvnx_gaddr;
/* One x86 stack word / register of a boundary call: uintptr_t -- 32 bits in the product (a stack word), pointer-wide on
 * the host, where the game's pointers are native and the "words" are the integer arguments of a native call. */
typedef uintptr_t fnvnx_gword;

/* ---- exports ------------------------------------------------------------------------------------------------ */

/* The value that stands for the platform function (or data object) `native` -- the export `name` (NULL: by ordinal)
 * of the system DLL `dll`, ordinal FNVNX_BOUNDARY_BY_NAME when the export has none -- wherever the game receives it:
 * GetProcAddress, GetWindowLong(GWL_WNDPROC), GetClassLong(GCL_WNDPROC). Product: the import key (one value per
 * function everywhere, pointer equality as on Windows); 0 = the import table has no such row. Host: `native` itself. */
#define FNVNX_BOUNDARY_BY_NAME 0xffffffffu
fnvnx_gaddr fnvnx_bnd_export_value(const char *dll, const char *name, uint32_t ordinal, const void *native);

/* ---- calling game code (platform code calls through fnvnx_guestfn / fnvnx_guest_com_call of fnvnx_guestfn.h, never directly) --------- */

/* the most words one boundary call carries (the host implementation's limit; fnvnx_guestfn.h static_asserts it) */
#define FNVNX_BND_MAX_WORDS 16
struct fnvnx_bnd_ret { fnvnx_gword eax, edx; double st0; };
enum { FNVNX_BND_RET_INT = 0 /* int / void / pointer: eax */, FNVNX_BND_RET_I64 = 1 /* edx:eax */, FNVNX_BND_RET_ST0 = 2 /* float/double */ };

/* Calls the game function at `addr` on the calling thread with the x86 stack words args[0..nargs) (args[0] is the
 * first argument, the one nearest the return address) and `ecx` as the this-pointer of a thiscall. Works for cdecl
 * and stdcall alike. `out` receives the results of the requested `kind`. Host: calls the native function with the
 * words as integer arguments (nargs <= 16; the native callee reads the ones it declares). */
void fnvnx_bnd_guest_invoke(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind,
                            struct fnvnx_bnd_ret *out);

/* ---- game threads -------------------------------------------------------------------------------------------- */

typedef struct fnvnx_bnd_thread fnvnx_bnd_thread;

/* The context of a game thread (product: a loader thread block: stack + guard, TEB, TLS) that will run `start`
 * (a stdcall DWORD (*)(void *)) with a stack of at least stack_size bytes. NULL when it cannot be made. */
fnvnx_bnd_thread *fnvnx_bnd_thread_create(fnvnx_gaddr start, size_t stack_size);

/* On the OS thread of the game thread: notifies the modules (thread attach), runs the start routine with `param`,
 * returns its exit code. Not detached yet: call thread_release(t, 1) afterwards (or from ExitThread). */
uint32_t fnvnx_bnd_thread_run(fnvnx_bnd_thread *t, fnvnx_gaddr param);

/* Gives the context back. notify != 0: the thread ran (thread_run was entered) -- the modules are told of the thread
 * detach first, on the thread's own stack. notify == 0: it never ran (closed while suspended / failed to start). */
void fnvnx_bnd_thread_release(fnvnx_bnd_thread *t, int notify);

/* ---- modules ------------------------------------------------------------------------------------------------- */

/* LoadLibrary of a translated module that is not loaded yet: maps and initialises it (and its dependencies), which
 * registers every image with fnvnx_win32_register_image. Returns 0 and the module's base, or the Win32 error
 * LoadLibrary sets (ERROR_MOD_NOT_FOUND: not a translated module; ERROR_BAD_EXE_FORMAT: file/hash/PE/relocation
 * error or a TLS directory; ERROR_PROC_NOT_FOUND: an import without provider; ERROR_DLL_INIT_FAILED: DllMain
 * returned FALSE). `name` is the module name as the caller gave it. */
uint32_t fnvnx_bnd_module_load(const char *name, uint32_t *base);

/* FreeLibrary of a translated module whose LoadLibrary count (the platform's own, one per successful load it made) has reached 0:
 * the loader releases the platform's hold and unloads the module when nothing else keeps it (fnvnx_module_unload). Returns 0 with
 * *outcome = FNVNX_BND_UNLOADED (the module is gone: remove it from the module table), FNVNX_BND_KEPT (the platform's reference
 * is dropped; the module stays loaded because a live module imports it) or FNVNX_BND_PINNED (a static module: nothing happens;
 * FreeLibrary still returns TRUE); else the Win32 error FreeLibrary sets (ERROR_INVALID_HANDLE: not the base of a loaded
 * module; ERROR_MOD_NOT_FOUND: loaded, but the platform holds no explicit load of it). */
enum { FNVNX_BND_UNLOADED = 0, FNVNX_BND_KEPT = 1, FNVNX_BND_PINNED = 2 };
uint32_t fnvnx_bnd_module_unload(uint32_t base, int *outcome);

/* DisableThreadLibraryCalls of a translated module (the HMODULE is its base): from now on its DllMain and TLS callbacks are not
 * told of thread attach / detach. Returns 0, or the Win32 error (ERROR_MOD_NOT_FOUND: `base` is not the base of a
 * translated module). Idempotent. */
uint32_t fnvnx_bnd_disable_thread_calls(uint32_t base);

/* Process detach of every module (ExitProcess): the DllMain(DLL_PROCESS_DETACH) calls, in the order the loader defines. */
void fnvnx_bnd_process_detach(void);

#ifdef __cplusplus
}
#endif
#endif
