/* host_thread_activity.inc - SuspendThread / ResumeThread of another guest thread (FNVNX_PLAT_THREAD_ACTIVITY,
 * host.c platform_ext; host_v2.inc h_thread_activity passes the thread's handle). Horizon pauses a thread at its next
 * scheduling point; Windows' SuspendThread is asynchronous too. The kernel refuses to pause the calling thread
 * (self != 0), so the guest blocks itself instead (platform/win32/thread.cpp). Standalone (only <switch.h>) so
 * tests/s4/device runs it under qemu. */
static u64 host_thread_activity(Handle th, int self, u64 paused) {
    if (self) return MAKERESULT(Module_Libnx, LibnxError_BadInput);
    return svcSetThreadActivity(th, paused ? ThreadActivity_Paused : ThreadActivity_Runnable);
}
