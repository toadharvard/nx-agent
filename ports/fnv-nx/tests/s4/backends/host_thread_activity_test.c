/* host_thread_activity_test.c - the host side of SuspendThread/ResumeThread (toolchain/host/host_thread_activity.inc, which
 * host_v2.inc h_thread_activity calls with the thread's handle; FNVNX_PLAT_THREAD_ACTIVITY) executed: built as a static AArch64 Linux program against libnx's
 * headers with svcSetThreadActivity stubbed, run under qemu-aarch64 (tests/s4/backends/run_qemu.sh). Checks the handle
 * and activity it hands the kernel, that the calling thread (id 0) is refused without a kernel call, and that the
 * kernel's result is returned as it is. */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <switch.h>

static Handle s_handle = 0xdead;
static ThreadActivity s_act = (ThreadActivity)-1;
static int s_calls;
static Result s_rc;
Result svcSetThreadActivity(Handle h, ThreadActivity a) { s_handle = h; s_act = a; s_calls++; return s_rc; }

static int s_ctx_calls, s_ctx_fail;   /* the first s_ctx_fail dumps refuse (the thread is not yet suspended) */
Result svcGetThreadContext3(ThreadContext *c, Handle h) { (void)c; (void)h; return s_ctx_calls++ < s_ctx_fail ? MAKERESULT(1, 59) : 0; }
void svcSleepThread(s64 ns) { (void)ns; }
#include "../../../toolchain/host/host_thread_activity.inc"

static int g_fail;
#define T(c) do { if (!(c)) { g_fail++; printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); } } while (0)

int main(void)
{
    T(host_thread_activity(0x1234, 0, 1) == 0);
    T(s_handle == 0x1234 && s_act == ThreadActivity_Paused && s_calls == 1);
    T(host_thread_activity(0x1234, 0, 0) == 0);
    T(s_act == ThreadActivity_Runnable && s_calls == 2);
    T(host_thread_activity(0, 1, 1) == MAKERESULT(Module_Libnx, LibnxError_BadInput));   /* the calling thread */
    T(s_calls == 2);
    s_rc = MAKERESULT(1, 59);   /* a kernel refusal (KernelError_InvalidState) goes back as it is */
    T(host_thread_activity(0x1234, 0, 1) == MAKERESULT(1, 59));
    s_rc = 0;
    s_ctx_calls = 0; s_ctx_fail = 3;   /* a pause returns only after the kernel gave the context: 4 tries */
    T(host_thread_activity(0x1234, 0, 1) == 0 && s_ctx_calls == 4);
    s_ctx_calls = 0;
    T(host_thread_activity(0x1234, 0, 0) == 0 && s_ctx_calls == 0);   /* a resume asks for no context */
    printf("host_thread_activity: %s\n", g_fail ? "FAIL" : "ok");
    return g_fail != 0;
}
