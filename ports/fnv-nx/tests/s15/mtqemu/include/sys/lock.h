/* sys/lock.h - stand-in for devkitA64 newlib's <sys/lock.h> for the mtqemu guest (no newlib here): the layouts
 * toolchain/guest/threads.c builds on, as in devkitPro newlib. */
#ifndef FNVNX_MTQ_SYS_LOCK_H
#define FNVNX_MTQ_SYS_LOCK_H
#include <stdint.h>
typedef uint32_t _LOCK_T;
typedef struct {
    _LOCK_T lock;
    uint32_t thread_tag;
    uint32_t counter;
} _LOCK_RECURSIVE_T;
typedef uint32_t _COND_T;
#endif
