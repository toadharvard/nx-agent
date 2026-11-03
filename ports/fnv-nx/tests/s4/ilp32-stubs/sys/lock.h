/* sys/lock.h - stand-in for devkitA64 newlib's <sys/lock.h>, used ONLY to syntax-check os_horizon.c against
 * the libnx headers with the stand-in compiler (glibc has no such header). Layout as in devkitPro newlib. */
#ifndef FNVNX_STUB_SYS_LOCK_H
#define FNVNX_STUB_SYS_LOCK_H
#include <stdint.h>
typedef uint32_t _LOCK_T;
typedef struct {
    _LOCK_T lock;
    uint32_t thread_tag;
    uint32_t counter;
} _LOCK_RECURSIVE_T;
#endif
