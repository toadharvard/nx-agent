/* msvc_names.c - CRT and compiler-helper names the game units reference by a spelling no library defines.
 *
 * Each one was a trap stub in the guest link (tools/pipeline/undef.py classes crt_msvc and libc_glibc):
 *   _byteswap_ulong/_byteswap_ushort  MSVC intrinsics a unit declares as functions (fnvnx_compat.h has none)
 *   _memcpy, __memcpy                 the decomp's names for memcpy (an import thunk and its decorated form)
 *   __mbsnicmp, __time64              _mbsnicmp/_time64 under the decorated (extra underscore) name
 *   _ReadWriteBarrier                 a unit's out-of-line declaration of the intrinsic fnvnx_compat.h inlines
 *   __isoc23_sscanf, __isoc23_strtol  glibc's C23 redirections: units compiled by the stand-in compiler against
 *                                     glibc headers (S9) call them; newlib has only the plain functions
 *   FreeBlockImp                      the decomp's name for `call [0x010b7eb0]`: that .rdata word is a vtable slot
 *                                     holding 0x00cb9cb0 = `mov ecx, 0x011f6238; jmp 0x00aa4060`, i.e.
 *                                     MemoryManager::Deallocate(p) on the global heap, which is FormHeap_Free
 *                                     (0x00401030). The callers (SsArr in shadowscenelight) free a buffer with it.
 * All are weak: a definition elsewhere (a library, the game's own) takes precedence. */
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fnvnx_msvcrt.h"

#define WEAK __attribute__((weak))
#undef _byteswap_ulong   /* shim/include/fnvnx_win32.h maps them to builtins for code that includes it */
#undef _byteswap_ushort
#undef _byteswap_uint64

WEAK unsigned long _byteswap_ulong(unsigned long v) { return __builtin_bswap32((uint32_t)v); }
WEAK unsigned short _byteswap_ushort(unsigned short v) { return __builtin_bswap16(v); }
WEAK unsigned long long _byteswap_uint64(unsigned long long v) { return __builtin_bswap64(v); }

WEAK void *_memcpy(void *d, const void *s, size_t n) { return memcpy(d, s, n); }
WEAK void *__memcpy(void *d, const void *s, size_t n) { return memcpy(d, s, n); }

WEAK int __mbsnicmp(const unsigned char *a, const unsigned char *b, size_t n) { return _mbsnicmp(a, b, n); }
WEAK long long __time64(long long *t) { return _time64(t); }

WEAK void _ReadWriteBarrier(void) { __atomic_thread_fence(__ATOMIC_SEQ_CST); }

WEAK int __isoc23_sscanf(const char *s, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int r = vsscanf(s, fmt, ap);
    va_end(ap);
    return r;
}
WEAK long __isoc23_strtol(const char *s, char **end, int base) { return strtol(s, end, base); }

/* FormHeap_Free, native (S2 binds the address); weak so builds without the game objects (host tests) link */
extern void __fnv_00401030(void *p) __attribute__((weak));
WEAK void FreeBlockImp(void *p)
{
    if (p && __fnv_00401030)
        __fnv_00401030(p);
}
