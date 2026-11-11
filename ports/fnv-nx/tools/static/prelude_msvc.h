/* prelude_msvc.h - force-included in place of shim/include/fnvnx_compat.h when clang parses the prepared tree as
 * MSVC x86-32 (--target=i686-pc-windows-msvc -fms-extensions -fms-compatibility) for the static gate
 * (tools/static/run.sh). MSVC keywords (__declspec, __int64, __forceinline, __try, ...) are native there and keep
 * their MSVC meaning; record layouts come from clang's MSVC layout engine. Only what the prepare rules added on top
 * of the decomp is defined here, with the same meaning as in fnvnx_compat.h. */
#ifndef FNVNX_COMPAT_H
#define FNVNX_COMPAT_H
#define FNVNX_STATIC_MSVC 1

#ifdef __cplusplus
/* clang's x86 mm_malloc.h expects the MSVC CRT's aligned heap on this target */
extern "C" void *_aligned_malloc(__SIZE_TYPE__, __SIZE_TYPE__);
extern "C" void _aligned_free(void *);
#include <sse2neon.h> /* tools/static/run.sh points this at clang's own x86 SSE headers */
#endif

/* calling conventions as in fnvnx_compat.h: the prepared tree mixes declarations whose conventions the link step
 * bridges, so they are not checked here */
#define __cdecl
#define __stdcall
#define __fastcall
#define __thiscall
#define _cdecl
#define _stdcall
#define _fastcall
#define WINAPI
#define APIENTRY
#define CALLBACK
#define WINAPIV
#define STDMETHODCALLTYPE
#define NTAPI

#define FNVNX_KEEP __attribute__((used))
#define _alloca(n) __builtin_alloca(n)

#ifdef __cplusplus
template <class T> inline T &fnvnx_lvalue(T &&t) { return t; }
/* TAIL1: in the MSVC layout a zero-width bit-field that does not follow a bit-field is ignored, so this keeps the
 * MSVC layout of the decomp */
template <unsigned A> struct fnvnx_int_of_align;
template <> struct fnvnx_int_of_align<1> { typedef unsigned char type; };
template <> struct fnvnx_int_of_align<2> { typedef unsigned short type; };
template <> struct fnvnx_int_of_align<4> { typedef unsigned int type; };
template <> struct fnvnx_int_of_align<8> { typedef unsigned long long type; };
template <> struct fnvnx_int_of_align<16> { typedef unsigned int type __attribute__((aligned(16))); };
template <> struct fnvnx_int_of_align<32> { typedef unsigned int type __attribute__((aligned(32))); };
template <> struct fnvnx_int_of_align<64> { typedef unsigned int type __attribute__((aligned(64))); };
template <> struct fnvnx_int_of_align<128> { typedef unsigned int type __attribute__((aligned(128))); };
#define FNVNX_MSVC_BASE_END(...) typename fnvnx_int_of_align<alignof(__VA_ARGS__)>::type : 0;
#define FNVNX_MSVC_BASE_END_PACK(N, ...) typename fnvnx_int_of_align<(alignof(__VA_ARGS__) < (N) ? alignof(__VA_ARGS__) : (N))>::type : 0;
/* VFPAD1: MSVC pads its own vfptr already */
#define FNVNX_VFPAD(...)
#define FNVNX_VFPAD_PACK(N, ...)
#endif

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

#ifdef __cplusplus
struct _ThrowInfo;
extern "C" void _CxxThrowException(void *, _ThrowInfo *);
extern "C" void operator_delete_sized(void *, __SIZE_TYPE__);
extern "C" void _operator_delete_sized(void *, __SIZE_TYPE__);
#endif

#include "fnvnx_crt.h"
#include "fnvnx_asm.h"
#endif
