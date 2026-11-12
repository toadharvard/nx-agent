/* host_hooks.cpp - the two host-side hooks of the lifter runtime (lead L3a), provided by the platform for the
 * product only (platform/build_guest.sh compiles the .cpp files of platform/boundary; the host test build links this one too, for its own test).
 *
 *  fnvl_host_tls_slot: one pointer-sized slot per host thread, zero in every new thread (a __thread object is
 *    zero-initialised for each thread of the guest runtime's TLS, toolchain/guest/threads.c); the runtime keeps the
 *    calling thread's `struct cpu *` there.
 *  fnvl_host_ticks_ns: monotonic nanoseconds of the clock QueryPerformanceCounter / GetTickCount read
 *    (win32/time.cpp: clk_read_ticks; clk_read_ns converts the same counter), so there is one clock; a read
 *    like the others (the virtual clock advances by one quantum).
 *  fnvl_host_world_stop / fnvl_host_world_resume (lifter rule R9, stop and resume every other thread of the process):
 *    declared in shim/include/fnvl_host.h (with the two above), defined in platform/os/os_world.c (all backends and the host tests), where the contract
 *    and its proof are. */
#include "../include/fnvnx_clock.h"
#include "fnvl_host.h"

extern "C" {

struct cpu **fnvl_host_tls_slot(void)
{
    static __thread struct cpu *slot;
    return &slot;
}

unsigned long long fnvl_host_ticks_ns(void) { return clk_read_ns(); }

}
