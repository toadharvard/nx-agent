/* fnvnx_callback.h - how a callback type (a code pointer the game hands to a Win32 import) is declared.
 *
 * Game translation units (no FNVNX_PLATFORM_BUILD) see the ordinary function-pointer type: their callbacks are their
 * own functions. The platform build (tests/s4, tests/s7, platform/build_guest.sh: -DFNVNX_PLATFORM_BUILD for the
 * platform's own sources) sees the same name as fnvnx_guestfn<...> (platform/include/fnvnx_guestfn.h): a pointer-sized
 * guest address whose only use is a call through the boundary, so a direct call of a game code pointer does not compile.
 *   FNVNX_CALLBACK_T(WNDPROC, LRESULT, (HWND, UINT, WPARAM, LPARAM));
 * Both layouts are one pointer, so structs and C-linkage signatures are identical for both sides. */
#ifndef FNVNX_CALLBACK_H
#define FNVNX_CALLBACK_H
#if defined(FNVNX_PLATFORM_BUILD) && defined(__cplusplus)
#include "fnvnx_guestfn.h"
#define FNVNX_CALLBACK_T(name, ret, params) typedef fnvnx_guestfn<ret params> name
#else
#define FNVNX_CALLBACK_T(name, ret, params) typedef ret (*name) params
#endif
#endif
