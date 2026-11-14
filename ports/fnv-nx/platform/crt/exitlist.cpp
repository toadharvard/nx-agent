/* exitlist.cpp - the process's exit-handler list: atexit / _onexit (msvcrt.dll) and the termination sequence of
 * exit / _cexit / _c_exit.
 *
 * Rule (MSVC crt0dat.c doexit, under the CRT's exit lock): the handlers run once each, last registered first, and a
 * handler that registers another one during the run has it run next. Handlers are GAME function pointers: they are
 * kept as values (fnvnx_gaddr) and run through the boundary's guest_invoke, never called as native pointers. One list
 * serves every registration, so the relative order between atexit and _onexit calls is the order of the calls.
 * doexit(code, quick, retcaller): `quick` skips the handlers (_c_exit), `retcaller` returns instead of ending the
 * process (_cexit, _c_exit); otherwise ExitProcess(code) follows. */
#include "../win32/w32.h"
#include "fnvnx_boundary.h"
#include "fnvnx_msvcrt.h"

#include <stdlib.h>

static os_lock_t g_xl_lock = OS_LOCK_INIT;
static fnvnx_gaddr *g_xl;
static size_t g_xl_n, g_xl_cap;

extern "C" int fnvnx_exit_register(fnvnx_gaddr f)
{
    if (!f)
        return -1;
    os_lock(&g_xl_lock);
    int rc = 0;
    if (g_xl_n == g_xl_cap) {
        size_t cap = g_xl_cap ? g_xl_cap * 2 : 32;
        fnvnx_gaddr *t = (fnvnx_gaddr *)realloc(g_xl, cap * sizeof *t);
        if (t)
            g_xl = t, g_xl_cap = cap;
        else
            rc = -1;
    }
    if (!rc)
        g_xl[g_xl_n++] = f;
    os_unlock(&g_xl_lock);
    return rc;
}

extern "C" void fnvnx_doexit(int code, int quick, int retcaller)
{
    for (; !quick;) {   /* LIFO; handlers registered while running are run next */
        os_lock(&g_xl_lock);
        fnvnx_gaddr f = g_xl_n ? g_xl[--g_xl_n] : 0;
        os_unlock(&g_xl_lock);
        if (!f)
            break;
        struct fnvnx_bnd_ret r;
        fnvnx_bnd_guest_invoke(f, 0, 0, NULL, FNVNX_BND_RET_INT, &r);   /* the int result of an _onexit handler is ignored */
    }
    if (retcaller)
        return;
    ExitProcess((UINT)code);
    os_exit_process(code);
}
