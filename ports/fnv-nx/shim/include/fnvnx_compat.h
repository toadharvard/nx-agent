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
#define _alloca(n) __builtin_alloca(n)   /* the decomp's own _alloca prototypes are dropped by DECL1 */
#define __pragma(x) _Pragma(#x)

/* MSVC compiler intrinsics the decomp declares itself (extern "C" void _ReadWriteBarrier(void); in 600 places) and
 * calls qualified (::_ReadWriteBarrier()), so they must be functions with C linkage, not macros. */
#ifdef __cplusplus
extern "C" {
#endif
inline __attribute__((always_inline)) void _ReadWriteBarrier(void) { __asm__ __volatile__("" ::: "memory"); }
inline __attribute__((always_inline)) void _ReadBarrier(void) { __asm__ __volatile__("" ::: "memory"); }
inline __attribute__((always_inline)) void _WriteBarrier(void) { __asm__ __volatile__("" ::: "memory"); }
#ifdef __cplusplus
}
#endif

/* SEH: there are no structured exceptions on the target; a __try body runs as a plain block and the __except
 * filter / handler never run (S4 decides whether anything has to raise instead). The decomp has no __finally or
 * __leave. */
#define __try if (1)
#define __except(filter) else if (0 && (filter))
#define _exception_code() 0UL
#define GetExceptionCode() 0UL

/* Every definition tagged `// @fnv 0x...` is emitted out of line even where it is inlined everywhere: the link step
 * (tools/link) binds references to it by address (prepare.py rule KEEP1). */
#define FNVNX_KEEP __attribute__((used))

/* MSVC class layout: derived members start at sizeof(base), never in its tail padding (prepare.py rule TAIL1
 * puts FNVNX_MSVC_BASE_END(last base) first in every derived class body). A zero-width bit-field of an integer
 * type with the base's alignment moves the next member to roundup(dsize(base), alignof(base)) == sizeof(base). */
#ifdef __cplusplus
template <unsigned A> struct fnvnx_int_of_align;
template <> struct fnvnx_int_of_align<1> { typedef unsigned char type; };
template <> struct fnvnx_int_of_align<2> { typedef unsigned short type; };
template <> struct fnvnx_int_of_align<4> { typedef unsigned int type; };
template <> struct fnvnx_int_of_align<8> { typedef unsigned long long type; };
template <> struct fnvnx_int_of_align<16> { typedef unsigned __int128 type; };
template <> struct fnvnx_int_of_align<32> { typedef unsigned int type __attribute__((aligned(32))); };
template <> struct fnvnx_int_of_align<64> { typedef unsigned int type __attribute__((aligned(64))); };
template <> struct fnvnx_int_of_align<128> { typedef unsigned int type __attribute__((aligned(128))); };
#define FNVNX_MSVC_BASE_END(...) typename fnvnx_int_of_align<alignof(__VA_ARGS__)>::type : 0;
#endif

/* SAL annotations (sal.h) used without an include in pasted CRT prototypes */
#define _In_
#define _In_opt_
#define _Out_
#define _Out_opt_
#define _Inout_
#define _Inout_opt_
#define _In_z_
#define _In_opt_z_
#define _Printf_format_string_
#define _Check_return_
#define _Ret_maybenull_
#define _Post_z_

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
#define __fnvnx_ds_deprecated(...) __attribute__((deprecated(__VA_ARGS__)))   /* always with a message */
#define __fnvnx_ds_intrin_type
#define __fnvnx_ds_property(...)
#define __fnvnx_ds_uuid(...)

/* Compiler-runtime helpers the decomp calls without declaring them in every unit (prepare.py DECL1 drops its own
 * unsigned-int prototypes of operator_delete_sized). */
#ifdef __cplusplus
struct _ThrowInfo;
extern "C" void _CxxThrowException(void *, _ThrowInfo *);
extern "C" void operator_delete_sized(void *, __SIZE_TYPE__);
extern "C" void _operator_delete_sized(void *, __SIZE_TYPE__);
#endif

/* x87 helper names (fnv_FloatToInt, fnv_SinCos, ...) for units that call them without the decomp's helper header */
/* MSVC-only CRT names (secure CRT, _stricmp, ...) that units call without a declaration of their own */
#include "fnvnx_crt.h"

#define FNVNX_ASM_FNV_HELPERS
#include "fnvnx_asm.h"

#endif
