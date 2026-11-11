/* gfn_nocompile.cpp - `make -C tests/s4 gfn-nocompile`: under -DFNVNX_PLATFORM_BUILD a game code pointer (WNDPROC, ...)
 * cannot be called or converted as a native function pointer. CASE 0 is the control (the one call syntax there is: it
 * must compile); CASE 1..4 are the ways around the boundary: each must be rejected by the compiler. */
#include "fnvnx_win32.h"

typedef LRESULT (*raw_wndproc)(HWND, UINT, WPARAM, LPARAM);
LRESULT native_proc(HWND, UINT, WPARAM, LPARAM);

#ifndef CASE
#define CASE 0
#endif

LRESULT use(WNDPROC p, HWND h, UINT m, WPARAM w, LPARAM l)
{
#if CASE == 0
    return p(h, m, w, l);                           /* through the boundary */
#elif CASE == 1
    return ((raw_wndproc)p)(h, m, w, l);            /* a raw function-pointer cast of the value */
#elif CASE == 2
    raw_wndproc q = p;                              /* implicit conversion to a native pointer */
    return q(h, m, w, l);
#elif CASE == 3
    WNDPROC r = native_proc;                        /* a native function address handed in as a game address */
    return r(h, m, w, l);
#elif CASE == 4
    return (*(raw_wndproc *)&p == native_proc) ? 0 : static_cast<raw_wndproc>(p)(h, m, w, l);   /* static_cast */
#endif
}
