/* host_boundary.h - the counters and test hooks of host_boundary.cpp (the host implementation of platform/include/fnvnx_boundary.h) */
#ifndef FNVNX_TESTS_HOST_BOUNDARY_H
#define FNVNX_TESTS_HOST_BOUNDARY_H
#include <stdint.h>
#include "fnvnx_boundary.h"
#ifdef __cplusplus
extern "C" {
#endif
extern int g_bnd_exports, g_bnd_invokes, g_bnd_threads_created, g_bnd_threads_run, g_bnd_threads_freed,
    g_bnd_threads_freed_unrun, g_bnd_process_detaches;
/* the modules (bases) DisableThreadLibraryCalls told the boundary about */
extern uint32_t g_bnd_disabled[16];
extern int g_bnd_disabled_n;
/* the stand-in for the loader's LoadLibrary: maps a module (the test builds its image and registers it with
 * fnvnx_win32_register_image) and returns 0 + base, or a Win32 error; NULL: no translated module is loadable */
extern uint32_t (*g_bnd_loader)(const char *name, uint32_t *base);
/* the test double of guest_invoke: when set, every boundary call is handed to it INSTEAD of being made natively. It
 * returns 1 when it has answered (`out` filled), 0 to let the native call happen. */
extern int (*g_bnd_invoke_hook)(fnvnx_gaddr addr, uint32_t ecx, uint32_t nargs, const fnvnx_gword *args, uint32_t kind,
                                struct fnvnx_bnd_ret *out);
#ifdef __cplusplus
}
#endif
#endif
