/* TEST FIXTURE of link/tests/real_guest.py: the names the real link needs from the platform runtime and from the game boot, so that the
 * proof guest links without an undefined or weak-undefined symbol.  Not part of the product; each body does the one thing its name says
 * on the emulated host.
 *   - the lifter runtime's platform interface (lift/rt: fnvl_host_ticks_ns, fnvl_host_tls_slot, ExitProcess, UnhandledExceptionFilter,
 *     fnvnx_win32_set_raise_backend). */
#include <stddef.h>
#include <stdint.h>
#include <time.h>

extern int main(int, char **);
extern void _exit(int);

uint64_t fnvl_host_ticks_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

static void *tls_slot;
void **fnvl_host_tls_slot(void) { return &tls_slot; }

static void (*raise_backend)(uint32_t, uint32_t, uint32_t, const uint32_t *);
void fnvnx_win32_set_raise_backend(void (*f)(uint32_t, uint32_t, uint32_t, const uint32_t *)) { raise_backend = f; }

uint32_t UnhandledExceptionFilter(uint32_t pointers) { return pointers == 0 ? 0xffffffffu : 0u; }
void ExitProcess(uint32_t code) { _exit((int)code); }
