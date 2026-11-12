/* plat_mark.h - marks for unsupported D3D9 states, formats and entry points in the render code, counted by the
 * platform lane (D25 #3 #5; tools/perf/PLAT.md). The recorder is the guest platform library's fnvnx_plat_note
 * (platform/include/fnvnx_win32_ext.h, platform/win32/platstat.cpp). It is declared weak here because the same
 * sources also build into host tools (nx_render, tests/s6n), which do not link it. There the marks do nothing.
 * While recording is off, each mark costs one load.
 *
 *   NX_PLAT_UNSUPPORTED("D3DRS_SRCBLEND", value)   name of the state, format or entry point; the value that is not
 *                                                  supported goes into the detail ("value <n>")
 * Entries are distinct by name and detail, so each unsupported value counts once, however often it is used. */
#ifndef NX_PLAT_MARK_H
#define NX_PLAT_MARK_H
#include <stdio.h>
#ifdef __cplusplus
extern "C" {
#endif
extern volatile int fnvnx_plat_on __attribute__((weak));
void fnvnx_plat_note(int cls, const char *name, unsigned caller, const char *detail) __attribute__((weak));
#ifdef __cplusplus
}
#endif
#define NX_PLAT_CLASS_UNIMPL 0   /* FNVNX_NOTE_UNIMPL */
#define NX_PLAT_UNSUPPORTED(name, value)                                                                              \
    do {                                                                                                              \
        if (&fnvnx_plat_on && fnvnx_plat_on && fnvnx_plat_note) {                                                     \
            char nx_pd_[24];                                                                                          \
            snprintf(nx_pd_, sizeof nx_pd_, "value %u", (unsigned)(value));                                           \
            fnvnx_plat_note(NX_PLAT_CLASS_UNIMPL, (name), (unsigned)(unsigned long)__builtin_return_address(0), nx_pd_); \
        }                                                                                                             \
    } while (0)
#endif
