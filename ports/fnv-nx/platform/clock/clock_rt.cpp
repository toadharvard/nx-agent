/* clock_rt.cpp - the real-time services of fnvnx_clock.h, linked with both clocks. Nothing here feeds a value the guest can
 * observe: diagnostic timers (the watchdog and memory-lane dumps), polling back-off, and the real blocking of a wait (a wait
 * still waits for the real signal; what the thread's virtual time becomes is the clock's business). */
#include "../include/fnvnx_clock.h"
#include "../os/os_clock.h"

extern "C" {

uint64_t clk_rt_ns(void) { return os_monotonic_ns(); }
void clk_rt_sleep_ns(uint64_t ns) { os_sleep_ns(ns); }
int clk_rt_cond_timedwait(os_cond_t *c, os_lock_t *l, uint64_t timeout_ns) { return os_cond_timedwait(c, l, timeout_ns); }
uint64_t clk_rt_deadline(uint64_t timeout_ns) { return os_monotonic_ns() + timeout_ns; }

int clk_rt_left(uint64_t deadline, uint64_t *left_ns)
{
    uint64_t now = os_monotonic_ns();
    if (now >= deadline)
        return 0;
    *left_ns = deadline - now;
    return 1;
}

}
