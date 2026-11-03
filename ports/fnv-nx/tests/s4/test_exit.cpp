/* test_exit.cpp - atexit / _onexit / doexit / exit forward to S10's onexit table (fnvnx_xc_*, weak in
 * platform/crt/heap.c). The recording definitions below stand in for startup/guest/onexit.c. */
#include "s4_test.h"

#include "fnvnx_msvcrt.h"

namespace {
int g_atexit_calls, g_onexit_calls, g_doexit_calls;
void (*g_last)(void);
int g_code = -1, g_quick = -1, g_retcaller = -1;
void handler(void) {}
int int_handler(void) { return 0; }
} // namespace

extern "C" int fnvnx_xc_atexit(void (*f)(void))
{
    g_atexit_calls++;
    g_last = f;
    return f ? 0 : -1;
}

extern "C" void (*fnvnx_xc_onexit(void (*f)(void)))(void)
{
    g_onexit_calls++;
    g_last = f;
    return f;
}

extern "C" void fnvnx_xc_doexit(int code, int quick, int retcaller)
{
    g_doexit_calls++;
    g_code = code;
    g_quick = quick;
    g_retcaller = retcaller;
}

TEST(crt_exit_forwards_to_onexit_table)
{
    CHECK_EQ(fnvnx_atexit(handler), 0);
    CHECK_EQ(g_atexit_calls, 1);
    CHECK(g_last == handler);
    CHECK(_onexit(int_handler) == int_handler);
    CHECK_EQ(g_onexit_calls, 1);
    CHECK(g_last == (void (*)(void))int_handler);
    fnvnx_doexit(3, 0, 1);
    CHECK(g_code == 3 && g_quick == 0 && g_retcaller == 1);
    fnvnx_cexit();
    CHECK(g_code == 0 && g_quick == 0 && g_retcaller == 1);
    fnvnx_c_exit();
    CHECK(g_quick == 1 && g_retcaller == 1);
    CHECK_EQ(g_doexit_calls, 3);
}
