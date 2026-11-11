/* test_exit.cpp - atexit / _onexit / doexit / exit forward to S10's onexit table (fnvnx_xc_*, platform/crt/heap.c).
 * The host's definitions (host_hooks.cpp) count what arrives. */
#include "s4_test.h"

#include "fnvnx_msvcrt.h"
#include "host_hooks.h"

namespace {
void handler(void) {}
int int_handler(void) { return 0; }
} // namespace

TEST(crt_exit_forwards_to_onexit_table)
{
    CHECK_EQ(fnvnx_atexit(handler), 0);
    CHECK_EQ(g_host_atexit_calls, 1);
    CHECK(g_host_last_handler == handler);
    CHECK(_onexit(int_handler) == int_handler);
    CHECK_EQ(g_host_onexit_calls, 1);
    CHECK(g_host_last_handler == (void (*)(void))int_handler);
    fnvnx_doexit(3, 0, 1);
    CHECK(g_host_doexit_code == 3 && g_host_doexit_quick == 0 && g_host_doexit_retcaller == 1);
    fnvnx_cexit();
    CHECK(g_host_doexit_code == 0 && g_host_doexit_quick == 0 && g_host_doexit_retcaller == 1);
    fnvnx_c_exit();
    CHECK(g_host_doexit_quick == 1 && g_host_doexit_retcaller == 1);
    CHECK_EQ(g_host_doexit_calls, 3);
}
