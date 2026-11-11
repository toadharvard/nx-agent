/* host_hooks.cpp - the two host-side hooks of the lifter runtime (lead L3a), provided by the platform for the
 * product only (platform/build_guest.sh compiles platform/boundary/*.cpp; the host test build does not).
 *
 *  fnvl_host_tls_slot: one pointer-sized slot per host thread, zero in every new thread (a __thread object is
 *    zero-initialised for each thread of the guest runtime's TLS, toolchain/guest/threads.c); the runtime keeps the
 *    calling thread's `struct cpu *` there.
 *  fnvl_host_ticks_ns: monotonic nanoseconds of the clock QueryPerformanceCounter / GetTickCount read
 *    (win32/time.cpp: os_ticks; os_monotonic_ns converts the same counter), so there is one clock. */
#include "../os/os.h"

struct cpu;

extern "C" {

struct cpu **fnvl_host_tls_slot(void)
{
    static __thread struct cpu *slot;
    return &slot;
}

unsigned long long fnvl_host_ticks_ns(void) { return os_monotonic_ns(); }

}
