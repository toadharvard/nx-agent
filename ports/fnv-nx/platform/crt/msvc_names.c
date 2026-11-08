/* msvc_names.c - CRT and compiler-helper names the game units reference by a spelling no library defines.
 *
 * Each one was a trap stub in the guest link (tools/pipeline/undef.py classes crt_msvc and libc_glibc):
 *   _byteswap_ulong/_byteswap_ushort  MSVC intrinsics a unit declares as functions (fnvnx_compat.h has none)
 *   _memcpy, __memcpy                 the decomp's names for memcpy (an import thunk and its decorated form)
 *   __mbsnicmp, __time64              _mbsnicmp/_time64 under the decorated (extra underscore) name
 *   _ReadWriteBarrier                 a unit's out-of-line declaration of the intrinsic fnvnx_compat.h inlines
 *   __isoc23_sscanf, __isoc23_strtol  glibc's C23 redirections: units compiled by the stand-in compiler against
 *                                     glibc headers (S9) call them; newlib has only the plain functions
 *   __emul, __movsd, __debugbreak     MSVC intrinsics declared as functions by Havok units (out-of-line calls)
 *   xmemcpy                           SpeedTree's memcpy wrapper (TreeModelX, bstreemodel)
 *   __fnv_00401050                    the vector constructor iterator (no EH), reached by the boot
 *   FUN_00401460, FUN_00406d50        CopyMemoryBlock / AppendPreview (crt_symbols.tsv "needs body"): cdecl
 *                                     forwarders in the exe to 0x00ec44d0 memcpy (VC9's, overlap-safe: memmove)
 *                                     and 0x00ec6bd2 strcat_s, with the arguments in order
 *   __fnv_00ec330b, __fnv_00ec78e0,   std::_Lockit::~_Lockit, std::exception::exception() and ~exception() by
 *   __fnv_00ec79a1                    address (R6 recon6-007): units.json places them, no unit emits a body, and
 *                                     paths that spell the address (exit thunks, vtable slots) missed
 *                                     crt_symbols.tsv's providers, so they were trap stubs (code-defmissing)
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
#include "../os/os.h"

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

WEAK long long __emul(int a, int b) { return (long long)a * b; }
WEAK unsigned long long __emulu(unsigned a, unsigned b) { return (unsigned long long)a * b; }
/* rep movsd / rep movsb: forward copies of 32-bit words / bytes, element by element as the string instructions do
 * (an overlapping forward copy repeats the pattern, which memmove would not) */
WEAK void __movsd(unsigned long *d, const unsigned long *s, size_t n)
{
    volatile uint32_t *dd = (volatile uint32_t *)d;
    const volatile uint32_t *ss = (const volatile uint32_t *)s;
    for (size_t i = 0; i < n; i++)
        dd[i] = ss[i];
}
WEAK void __movsb(unsigned char *d, const unsigned char *s, size_t n)
{
    volatile unsigned char *dd = d;
    for (size_t i = 0; i < n; i++)
        dd[i] = s[i];
}
WEAK void *xmemcpy(void *d, const void *s, size_t n) { return memcpy(d, s, n); }
WEAK void *FUN_00401460(void *d, const void *s, unsigned n) { return memmove(d, s, n); }   /* CopyMemoryBlock */
WEAK int FUN_00406d50(char *d, unsigned n, const char *s) { return (int)strcat_s(d, n, s); }   /* AppendPreview */
/* main.cpp (0x0086a850) declares it `FUN_00406d50(...)` with C++ linkage: the same function under that name. All
 * arguments are variadic there, which AAPCS64 passes in x0.. like named ones. */
WEAK int fnvnx_appendpreview_v(char *d, unsigned n, const char *s) __asm__("_Z12FUN_00406d50z");
int fnvnx_appendpreview_v(char *d, unsigned n, const char *s) { return (int)strcat_s(d, n, s); }

/* 0x00401050: MSVC's `vector constructor iterator` without EH (__stdcall, ret 0x10): calls the thiscall
 * constructor on each of `count` elements of `size` bytes. No unit defines the address (crt_symbols.tsv had no
 * row); the boot reaches it from 0x00a61ad0. Native: the constructor takes the element in x0. */
WEAK void __fnv_00401050(void *ptr, unsigned size, int count, void (*ctor)(void *))
{
    for (char *p = (char *)ptr; count-- > 0; p += size)
        ctor(p);
}
/* int 3 in the original: Havok's assertion path (hkgpConvexHull). Without a debugger Windows raises an exception
 * the game's top-level filter turns into a crash. Here it is logged and execution goes on, like the trap stub it
 * replaces: a failed geometry assertion should not end the game. */
WEAK void __debugbreak(void) { os_debug_print("fnvnx: __debugbreak (int 3 in the original) - continuing\n"); }

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

/* R6 recon6-007: the CRT provider by address (thiscall: this in x0; the constructor returns this) */
WEAK void __fnv_00ec330b(void *self) { fnvnx_lockit_dtor(self); }
WEAK void *__fnv_00ec78e0(void *self) { return fnvnx_exception_ctor(self); }
WEAK void __fnv_00ec79a1(void *self) { fnvnx_exception_dtor(self); }
