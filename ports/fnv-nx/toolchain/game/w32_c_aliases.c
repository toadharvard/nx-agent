/* w32_c_aliases.c - C-linkage Interlocked* for game units that still reference them by their plain C name.
 * The shim (S1) now declares them with C++ linkage and platform_adapt.sh builds S4's sync.cpp that way, so the
 * plain names are otherwise undefined. C file: no overloading, no clash with the shim's C++ declarations. */
#include <stdint.h>

typedef int32_t LONG;   /* ILP32: long is 32-bit, LONG = long in the shim; same width and AAPCS64 class */

LONG InterlockedIncrement(volatile LONG *p) { return __atomic_add_fetch(p, 1, __ATOMIC_SEQ_CST); }
LONG InterlockedDecrement(volatile LONG *p) { return __atomic_sub_fetch(p, 1, __ATOMIC_SEQ_CST); }
LONG InterlockedExchange(volatile LONG *p, LONG v) { return __atomic_exchange_n(p, v, __ATOMIC_SEQ_CST); }
LONG InterlockedExchangeAdd(volatile LONG *p, LONG v) { return __atomic_fetch_add(p, v, __ATOMIC_SEQ_CST); }
LONG InterlockedCompareExchange(volatile LONG *p, LONG x, LONG c) {
    __atomic_compare_exchange_n(p, &c, x, 0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return c;
}
