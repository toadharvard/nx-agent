/* devkitPro newlib's <sys/lock.h> shape, for tests/nx1/syntax.sh only (libnx's mutex.h needs these types). */
#ifndef FNVNX_NX1_SYS_LOCK_H
#define FNVNX_NX1_SYS_LOCK_H
#include <stdint.h>
typedef uint32_t _LOCK_T;
typedef struct { _LOCK_T lock; uint32_t thread_tag; uint32_t counter; } _LOCK_RECURSIVE_T;
typedef uint32_t _COND_T;
#define __LOCK_INITIALIZER ((_LOCK_T)0)
#define __LOCK_INITIALIZER_RECURSIVE ((_LOCK_RECURSIVE_T){__LOCK_INITIALIZER,0,0})
#define __COND_INITIALIZER ((_COND_T)0)
#endif
