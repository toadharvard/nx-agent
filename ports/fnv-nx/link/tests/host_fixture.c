/* TEST FIXTURE of link/tests/real_guest.py: the names the real link needs from the platform runtime and from the game boot, so that the
 * proof guest links without an undefined or weak-undefined symbol.  Not part of the product; each body does the one thing its name says
 * on the emulated host.
 *   - the lifter runtime's platform interface (lift/rt: fnvl_host_ticks_ns, fnvl_host_tls_slot, ExitProcess, UnhandledExceptionFilter,
 *     fnvnx_win32_set_raise_backend);
 *   - the optional hooks guest runtime and libraries reference weakly (runtime.c, threads.c, crtbegin.o, newlib clock calls). */
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

#include "../../startup/guest/fnvnx_xc.h"

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

int fnvnx_guest_main(int argc, char **argv) { return main(argc, argv); }

static unsigned tls_hook_calls;
void fnvnx_guest_thread_tls_hook(void) { tls_hook_calls++; }

int fnvnx_xc_run_initializers(const uint32_t *img_slots, uint32_t img_nslots, uint32_t flags, const fnvnx_xc_hooks *hooks, fnvnx_xc_report *rep) {
    (void)img_slots; (void)img_nslots; (void)flags; (void)hooks;
    *rep = (fnvnx_xc_report){ .mode = FNVNX_XC_MODE_ADDRESS };
    return 0;
}

size_t fnvnx_xc_format_report(const fnvnx_xc_report *rep, char *buf, size_t cap) { return (size_t)snprintf(buf, cap, "xc: called %u\n", rep->called); }

static unsigned cexit_calls;
void fnvnx_xc_cexit(void) { cexit_calls++; }
const uint32_t *fnvnx_xc_image_slots = NULL;
uint32_t fnvnx_xc_image_nslots = 0;
const fnvnx_xc_hooks fnvnx_guest_xc_hooks = { NULL, NULL, NULL };

int __syscall_clock_getres(clockid_t id, struct timespec *tp) {
    if (id != CLOCK_MONOTONIC) { errno = EINVAL; return -1; }
    tp->tv_sec = 0;
    tp->tv_nsec = 53;           /* one tick of the 19.2 MHz system counter */
    return 0;
}
int __syscall_clock_settime(clockid_t id, const struct timespec *tp) { (void)id; (void)tp; errno = EPERM; return -1; }

static const void *frame_info;
void __register_frame_info(const void *begin, void *object) { (void)object; frame_info = begin; }
void *__deregister_frame_info(const void *begin) { return begin == frame_info ? (void *)(frame_info = NULL) : NULL; }
