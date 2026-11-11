/* host_hooks.h - the counters of host_hooks.cpp */
#ifndef FNVNX_TESTS_HOST_HOOKS_H
#define FNVNX_TESTS_HOST_HOOKS_H
extern int g_host_atexit_calls, g_host_onexit_calls, g_host_doexit_calls;
extern int g_host_doexit_code, g_host_doexit_quick, g_host_doexit_retcaller;
extern void (*g_host_last_handler)(void);
#endif
