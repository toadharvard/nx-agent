/* runtime_test.cpp - guest program for the runtime's libc/libstdc++ glue (runtime.c), run in the emulator by runtime_e2e.sh.
 *   PHASE 1: what the glue defines for newlib/libsysbase, checked against the contract they state:
 *     __libc_fini            newlib's register_fini tests only that the symbol's address is not null, and then registers
 *                            __libc_fini_array with atexit: the static destructor below must run at exit()
 *     __syscall_clock_getres clock_getres: REALTIME 1000 ns (wall_time_us counts microseconds), MONOTONIC ceil(1e9 / system_tick_freq)
 *     __syscall_clock_settime REALTIME: EPERM (no privilege), MONOTONIC and unknown clocks: EINVAL, an invalid tp: EINVAL
 *     prints `runtime-test: PASS <n> checks` (or a FAIL line per check) and returns 0
 *   PHASE 2: a call of a pure virtual function: __cxa_pure_virtual writes its message and ends the process through the host's
 *     exit slot with code 0xFB (the test script checks the message and the exit code). */
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <ctime>

#include "fnvnx/guest.h"
#include "fnvnx/hostapi.h"

#if PHASE == 1
static int g_n, g_bad;
#define CK(c) do { g_n++; if (!(c)) { g_bad++; std::printf("runtime-test: FAIL line %d: %s\n", __LINE__, #c); } } while (0)

struct Fin {
    ~Fin() { std::printf("runtime-test: static destructor ran at exit\n"); }
} g_fin;

int main() {
    struct timespec r = {7, 7};
    uint64_t f = fnvnx_hcall0(fnvnx_host->system_tick_freq);
    CK(f != 0);
    errno = 0;
    CK(clock_getres(CLOCK_REALTIME, &r) == 0 && r.tv_sec == 0 && r.tv_nsec == 1000);
    CK(clock_getres(CLOCK_MONOTONIC, &r) == 0 && r.tv_sec == 0 && (uint64_t)r.tv_nsec == (1000000000ull + f - 1) / f);
    CK(clock_getres(CLOCK_MONOTONIC, nullptr) == 0);
    errno = 0;
    CK(clock_getres((clockid_t)12345, &r) == -1 && errno == EINVAL);
    struct timespec t = {1, 0};
    errno = 0;
    CK(clock_settime(CLOCK_REALTIME, &t) == -1 && errno == EPERM);
    errno = 0;
    CK(clock_settime(CLOCK_MONOTONIC, &t) == -1 && errno == EINVAL);
    errno = 0;
    CK(clock_settime((clockid_t)12345, &t) == -1 && errno == EINVAL);
    t.tv_nsec = 1000000000;
    errno = 0;
    CK(clock_settime(CLOCK_REALTIME, &t) == -1 && errno == EINVAL);
    if (g_bad) return 1;
    std::printf("runtime-test: PASS %d checks\n", g_n);
    return 0;
}
#else
static volatile int g_calls;
struct Base {
    virtual void f() = 0;
    __attribute__((noinline)) void run() { f(); }
    Base() { run(); }   /* the pure virtual slot of Base is called while Base is being constructed */
};
struct Der : Base {
    void f() override { g_calls = g_calls + 1; }
};
int main() {
    Der d;
    return 0;
}
#endif
