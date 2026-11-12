/* test_dispatch_tls.cpp - the dispatch word fnvnx_call_fp (shim/include/fnvl_host.h, defined in platform/win32/thread.cpp):
 * one per host thread, zero in a thread from its creation, written by one thread and never seen by another. Threads are made
 * by the real CreateThread. */
#include "s4_test.h"
#include "fnvl_host.h"

static volatile unsigned g_seen_at_start, g_seen_after_write, g_main_value_at_start;

static DWORD reader(LPVOID arg)
{
    g_seen_at_start = fnvnx_call_fp;               /* the creator stored a value before CreateThread: not inherited */
    fnvnx_call_fp = (unsigned)(uintptr_t)arg;      /* this thread's own word */
    g_seen_after_write = fnvnx_call_fp;
    return 0;
}

TEST(dispatch_tls_per_thread)
{
    fnvnx_call_fp = 0x00401234u;
    HANDLE h = test_CreateThread(NULL, 0, reader, (LPVOID)(uintptr_t)0x00ABCDEFu, 0, NULL);
    CHECK(h != NULL);
    CHECK_EQ(WaitForSingleObject(h, 5000), WAIT_OBJECT_0);
    CHECK_EQ(g_seen_at_start, 0u);
    CHECK_EQ(g_seen_after_write, 0x00ABCDEFu);
    CHECK_EQ(fnvnx_call_fp, 0x00401234u);          /* the creator's word is untouched by the thread */
    CHECK_EQ(sizeof(fnvnx_call_fp), 4u);
    fnvnx_call_fp = 0;
}
