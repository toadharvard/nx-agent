/* host_hooks.cpp - what the host test binaries provide for the hooks the platform expects from the product.
 *
 * fnvnx_xc_atexit / fnvnx_xc_onexit / fnvnx_xc_doexit: the guest's onexit table is startup/guest/onexit.c; the host
 * binaries have libc's: handlers go to its atexit list, doexit runs them through exit() or, when quick, ends the
 * process without them. The calls are counted (host_hooks.h) so the tests can see what the CRT forwarded. */
#include "host_hooks.h"

#include <stdlib.h>
#include <unistd.h>

int g_host_atexit_calls, g_host_onexit_calls, g_host_doexit_calls;
int g_host_doexit_code = -1, g_host_doexit_quick = -1, g_host_doexit_retcaller = -1;
void (*g_host_last_handler)(void);

extern "C" int fnvnx_xc_atexit(void (*f)(void))
{
    g_host_atexit_calls++;
    g_host_last_handler = f;
    return f && atexit(f) == 0 ? 0 : -1;
}

extern "C" void (*fnvnx_xc_onexit(void (*f)(void)))(void)
{
    g_host_onexit_calls++;
    g_host_last_handler = f;
    return f && atexit(f) == 0 ? f : NULL;   /* the handler's int result is ignored, as by MSVC */
}

extern "C" void fnvnx_xc_doexit(int code, int quick, int retcaller)
{
    g_host_doexit_calls++;
    g_host_doexit_code = code;
    g_host_doexit_quick = quick;
    g_host_doexit_retcaller = retcaller;
    if (retcaller)
        return;
    if (quick)
        _exit(code);
    exit(code);
}
