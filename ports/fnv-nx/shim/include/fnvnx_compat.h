/* fnvnx_compat.h - force-included (-include) into every decomp unit when building for the Switch.
 *
 * The decomp is written for MSVC 2008 / x86-32. This header maps the MSVC keyword dialect onto GCC
 * targeting AArch64 ILP32 (32-bit pointers, see docs/ARCHITECTURE.md). It must not change the meaning
 * of any declaration, only make it parse.
 */
#ifndef FNVNX_COMPAT_H
#define FNVNX_COMPAT_H

#if !defined(__aarch64__) || !defined(__ILP32__)
#error "fnv-nx builds the decomp for AArch64 ILP32 only (-mabi=ilp32)"
#endif

/* SSE: one global vector type for every unit (prepare.py rules SSE1-3 remove the decomp's local copies).
 * Included before the keyword macros below so sse2neon sees a clean environment. */
#ifdef __cplusplus
#include <sse2neon.h>
#endif

/* Calling conventions: AArch64 has one. Argument-order mismatches that x86 hid (fastcall with a
 * dummy EDX parameter called as thiscall, ...) are fixed by generated bridges, not here. */
#define __cdecl
#define __stdcall
#define __fastcall
#define __thiscall
#define __clrcall
#define _cdecl
#define _stdcall
#define _fastcall
#define WINAPI
#define APIENTRY
#define CALLBACK
#define WINAPIV
#define STDMETHODCALLTYPE
#define NTAPI

/* Integer keywords. ILP32: long is 32-bit like MSVC, so no change there. */
#define __int8 char
#define __int16 short
#define __int32 int
#define __int64 long long
#define __w64
#define __ptr32
#define __ptr64
#define __unaligned

#define __forceinline inline __attribute__((always_inline))
#define __inline inline
#define __assume(e) ((void)0)
#define __noop(...) ((void)0)
#define __debugbreak() __builtin_trap()
#define __alignof(t) __alignof__(t)
#define _alloca(n) __builtin_alloca(n)
#define __pragma(x) _Pragma(#x)

/* __declspec(x) -> __fnvnx_ds_x ; each supported specifier is defined below. */
#define __declspec(x) __fnvnx_ds_##x
#define __fnvnx_ds_dllimport
#define __fnvnx_ds_dllexport
#define __fnvnx_ds_novtable
#define __fnvnx_ds_nothrow __attribute__((nothrow))
#define __fnvnx_ds_noinline __attribute__((noinline))
#define __fnvnx_ds_noreturn __attribute__((noreturn))
#define __fnvnx_ds_naked __attribute__((naked))
#define __fnvnx_ds_thread __thread
#define __fnvnx_ds_selectany __attribute__((weak))
#define __fnvnx_ds_restrict
#define __fnvnx_ds_noalias
#define __fnvnx_ds_align(n) __attribute__((aligned(n)))
#define __fnvnx_ds_deprecated __attribute__((deprecated))
#define __fnvnx_ds_property(...)
#define __fnvnx_ds_uuid(...)

#endif
