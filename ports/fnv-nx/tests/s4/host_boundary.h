/* host_boundary.h - the counters and test hooks of host_boundary.cpp (the host implementation of shim/include/fnvnx_boundary.h) */
#ifndef FNVNX_TESTS_HOST_BOUNDARY_H
#define FNVNX_TESTS_HOST_BOUNDARY_H
#include <stdint.h>
#include "fnvnx_boundary.h"
#ifdef __cplusplus
extern "C" {
#endif
/* guest calls made while the calling thread held a platform lock (every os_lock/os_trylock of the platform; the game's own
   os_glock family is not counted): the test mains require it to stay 0. The count is kept by the host test backend
   (platform_host/os/os_posix.c calls fnvnx_lockcount_note, defined in host_boundary.cpp); the product has neither. */
extern int g_bnd_lock_violations;
/* the test double of what the loader does at process end after the modules were told: called at the end of fnvnx_bnd_process_detach */
extern void (*g_bnd_process_detach_hook)(void);
/* the test case that runs now (the mains set it): the coverage aid of host_boundary.cpp names it */
extern const char *g_bnd_test;
/* the number of platform locks the calling thread holds now */
unsigned fnvnx_bnd_locks_held(void);
/* the first distinct acquisition sites of the locks that were held at a guest call (module offsets for addr2line), to stdout */
void fnvnx_bnd_print_lock_violations(void);
/* A test that provokes a violation on purpose (the detector's own test) takes a mark first and forgets what was recorded
   since, so that the run-wide count stays the platform's. */
struct fnvnx_bnd_mark { int violations, sites; };
struct fnvnx_bnd_mark fnvnx_bnd_violation_mark(void);
void fnvnx_bnd_violation_forget(struct fnvnx_bnd_mark m);
/* the acquisition sites of the platform locks the calling thread holds now */
void fnvnx_bnd_print_held(void);
extern fnvnx_gaddr (*g_bnd_key_hook)(const char *dll, const char *name, uint32_t ordinal);   /* a bridge row without a platform body */
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
/* the host test definition of fnvnx_reach_violation (loader/include/fnvnx_reach.h; noreturn): it records the fact and
 * throws a ReachHit, so a test can assert "this call reaches fact N" without the process ending. REACH_EXPECT runs
 * stmt and yields the fact it reached, or 0 when it returned. */
struct ReachHit { uint32_t fact; };
extern uint32_t g_reach_last;
extern int g_reach_count;
#define REACH_EXPECT(stmt) ([&]() -> uint32_t { try { stmt; } catch (const ReachHit &h_) { return h_.fact; } return 0; }())
#endif
#endif
