/* host_boundary.h - the counters and test hooks of host_boundary.cpp (the host implementation of platform/include/fnvnx_boundary.h) */
#ifndef FNVNX_TESTS_HOST_BOUNDARY_H
#define FNVNX_TESTS_HOST_BOUNDARY_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
extern int g_bnd_exports, g_bnd_invokes, g_bnd_threads_created, g_bnd_threads_run, g_bnd_threads_freed,
    g_bnd_threads_freed_unrun, g_bnd_process_detaches;
/* the stand-in for the loader's LoadLibrary: maps a module (the test builds its image and registers it with
 * fnvnx_win32_register_image) and returns 0 + base, or a Win32 error; NULL: no translated module is loadable */
extern uint32_t (*g_bnd_loader)(const char *name, uint32_t *base);
#ifdef __cplusplus
}
#endif
#endif
