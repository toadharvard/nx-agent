/* host_thread_activity.inc - SuspendThread / ResumeThread of another guest thread (FNVNX_PLAT_THREAD_ACTIVITY,
 * host.c platform_ext; host_v2.inc h_thread_activity passes the thread's handle). Horizon pauses a thread at its next
 * scheduling point; Windows' SuspendThread is asynchronous too. The kernel refuses to pause the calling thread
 * (self != 0), so the guest blocks itself instead (platform/win32/thread.cpp). Standalone (only <switch.h>) so
 * tests/s4/backends runs it under qemu. */
/* Synchronous pause (the world stop, platform/os/os_world.c): svcSetThreadActivity returns once the pause is requested; the
 * thread is stopped when svcGetThreadContext3 succeeds, which libnx's svc.h documents for "a thread paused by
 * svcSetThreadActivity" and the kernel allows only for a suspended (descheduled) thread. So a pause waits for it. A thread
 * requested to pause always reaches that state (the kernel interrupts its core, or it is waiting), so the loop ends. */
static u64 host_thread_activity(Handle th, int self, u64 paused) {
    if (self) return MAKERESULT(Module_Libnx, LibnxError_BadInput);
    Result rc = svcSetThreadActivity(th, paused ? ThreadActivity_Paused : ThreadActivity_Runnable);
    if (R_FAILED(rc) || !paused) return rc;
    ThreadContext ctx;
    while (R_FAILED(svcGetThreadContext3(&ctx, th))) svcSleepThread(0);
    return 0;
}
