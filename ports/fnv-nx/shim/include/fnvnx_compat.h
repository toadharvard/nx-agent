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

/* MSVC semantics the decomp relies on (J-0034): plain char is signed on MSVC x86 but unsigned by default on
 * AArch64. Build with -fsigned-char; also -fno-strict-aliasing -fwrapv -fno-delete-null-pointer-checks
 * -ffp-contract=off (see tools/link/compile.sh). */
#ifdef __CHAR_UNSIGNED__
#error "fnv-nx: plain char must be signed like MSVC (-fsigned-char)"
#endif

/* SSE: one global vector type for every unit (prepare.py rules SSE1-3 remove the decomp's local copies).
 * Included before the keyword macros below so sse2neon sees a clean environment - except __int64, which sse2neon
 * defines itself (as int64_t on AArch64) unless it is already defined: ours comes first, with MSVC's spelling. */
#define __int64 long long
#ifdef __cplusplus
#include "fnvnx_sse2neon.h"   /* sse2neon, with x86 results where it differs */
#include "fnvnx_sse_fast.h"   /* shorter permutes than sse2neon's (S15-simd) */
#include "fnvnx_mmx.h"   /* the MMX intrinsics sse2neon lacks */
#endif

/* Calling conventions: AArch64 has one. Argument-order mismatches that x86 hid (fastcall with a
 * dummy EDX parameter called as thiscall, ...) are fixed by generated bridges, not here. */
#ifdef FNVNX_SDD
/* CC1: the x86 calling convention of a declaration is a fact of the exe's contract (the MSVC name encodes it, a
 * bridge to a lifted body needs it). It changes no AArch64 code, so it is kept as an attribute that the
 * fnvnx_dispatch plugin registers, on the function type, where MSVC's keyword stood. */
#define __cdecl __attribute__((fnvnx_cc("cdecl")))
#define __stdcall __attribute__((fnvnx_cc("std")))
#define __fastcall __attribute__((fnvnx_cc("fast")))
#define __thiscall __attribute__((fnvnx_cc("this")))
#else
#define __cdecl
#define __stdcall
#define __fastcall
#define __thiscall
#endif
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
/* __int64: defined before sse2neon, above */
#define __w64
#define __ptr32
#define __ptr64
#define __unaligned

#define __forceinline inline __attribute__((always_inline))
#define __inline inline
#define __assume(e) ((void)0)
#define __noop(...) ((void)0)
#define __alignof(t) __alignof__(t)
#define _alloca(n) __builtin_alloca(n)   /* the decomp's own _alloca prototypes are dropped by DECL1 */
#define __pragma(x) _Pragma(#x)

/* MSVC compiler intrinsics the decomp declares itself (extern "C" void _ReadWriteBarrier(void); in 600 places,
 * extern "C" void __cdecl __debugbreak(void); and _exception_code(void) in some 50) and calls qualified
 * (::_ReadWriteBarrier()), so they must be functions with C linkage, not macros. */
#ifdef __cplusplus
extern "C" {
#endif
/* MSVC's barrier intrinsics only stop the compiler, but the x86 hardware under them is TSO: at such a point every
 * earlier load is ordered before every later access, and every earlier store before every later store. AArch64 needs
 * a real barrier for the same order (S12-ordering, docs/ORDERING.md): _ReadWriteBarrier dmb ish (store -> load is
 * ordered too, which x86 does not do - the cheapest single barrier that covers TSO), _ReadBarrier dmb ishld (load ->
 * load and load -> store), _WriteBarrier dmb ishst (store -> store). Volatile accesses get acquire/release from the
 * fnvnx_order plugin (toolchain/order). FNVNX_COMPILER_BARRIERS=1 keeps the compiler-only barriers (A/B
 * measurement only: not a valid multicore build). */
#ifndef FNVNX_MSVC_NAMES   /* the name pass (toolchain/vtnames: clang with the MSVC ABI) has these as builtins */
#if defined(FNVNX_COMPILER_BARRIERS) && FNVNX_COMPILER_BARRIERS
inline __attribute__((always_inline)) void _ReadWriteBarrier(void) { __asm__ __volatile__("" ::: "memory"); }
inline __attribute__((always_inline)) void _ReadBarrier(void) { __asm__ __volatile__("" ::: "memory"); }
inline __attribute__((always_inline)) void _WriteBarrier(void) { __asm__ __volatile__("" ::: "memory"); }
#else
inline __attribute__((always_inline)) void _ReadWriteBarrier(void) { __asm__ __volatile__("dmb ish" ::: "memory"); }
inline __attribute__((always_inline)) void _ReadBarrier(void) { __asm__ __volatile__("dmb ishld" ::: "memory"); }
inline __attribute__((always_inline)) void _WriteBarrier(void) { __asm__ __volatile__("dmb ishst" ::: "memory"); }
#endif
#endif
/* winnt.h MemoryBarrier (x86: a locked or, a full barrier) and YieldProcessor (pause) */
inline __attribute__((always_inline)) void MemoryBarrier(void) { __asm__ __volatile__("dmb ish" ::: "memory"); }
inline __attribute__((always_inline)) void YieldProcessor(void) { __asm__ __volatile__("yield" ::: "memory"); }
#ifndef FNVNX_MSVC_NAMES   /* the name pass (toolchain/vtnames: clang with the MSVC ABI) has these as builtins */
inline __attribute__((always_inline)) void __debugbreak(void) { __builtin_trap(); }
#endif
/* <stdlib.h> rotates (intrinsics on MSVC; the decomp declares some itself, extern "C" unsigned int _rotl(...)) */
#ifndef FNVNX_MSVC_NAMES   /* the name pass (toolchain/vtnames: clang with the MSVC ABI) has these as builtins */
inline __attribute__((always_inline)) unsigned int _rotl(unsigned int v, int s)
{
    s &= 31;
    return s ? (v << s) | (v >> (32 - s)) : v;
}
inline __attribute__((always_inline)) unsigned int _rotr(unsigned int v, int s)
{
    s &= 31;
    return s ? (v >> s) | (v << (32 - s)) : v;
}
#endif
/* SEH has no exceptions on the target (below): the code of the exception being handled is never asked for */
#ifndef FNVNX_MSVC_NAMES   /* the name pass (toolchain/vtnames: clang with the MSVC ABI) has these as builtins */
inline __attribute__((always_inline)) unsigned long _exception_code(void) { return 0UL; }
#endif
#ifdef __cplusplus
}
#endif

/* SEH: there are no structured exceptions on the target; a __try body runs as a plain block and the __except
 * filter / handler never run (S4 decides whether anything has to raise instead). The decomp has no __finally or
 * __leave. */
#define __try if (1)
#define __except(filter) else if (0 && (filter))
#define GetExceptionCode() 0UL

/* Every definition tagged `// @fnv 0x...` is emitted out of line and never inlined (prepare.py rule KEEP1): it is a
 * function of the original binary, the decomp's calls of it were real calls, and the link step (tools/link) binds
 * each call by address - which may be another unit's definition when the decomp has several copies of that address
 * (S13: an inlined local duplicate differs from the function the address binds to). */
#define FNVNX_KEEP __attribute__((used, noinline))

/* The base of an opaque enum declaration (prepare.py ENUM1: the decomp's MSVC forward declaration `enum X;` becomes
 * `enum X FNVNX_ENUM_INT;`). The S11 gate defines it empty: MSVC's own `enum X;` is that declaration there. */
#define FNVNX_ENUM_INT : int

/* MSVC binds a C-style cast to a non-const reference to a temporary ((T&)Make(x)); prepare.py RCAST1 wraps the
 * temporary in fnvnx_lvalue, which hands back the same object (alive to the end of the full expression). */
#ifdef __cplusplus
template <class T> inline T &fnvnx_lvalue(T &&t) { return t; }
/* RSTR1 (tools/rules/s1_compile.py): a restrict-qualified local pointer passed to a `const X&` parameter. MSVC binds
 * the reference to the local and ignores __restrict; GCC keeps the qualifier and refuses. fnvnx_unrestrict hands
 * back the same object as an unqualified pointer lvalue. */
template <class T> inline T *&fnvnx_unrestrict(T *__restrict &p) { return *(T **)&p; }
template <class T> inline T *const &fnvnx_unrestrict(T *__restrict const &p) { return *(T *const *)&p; }
#endif

/* MSVC's va_list is a char*; the decomp passes va_lists through char* / void* parameters. prepare.py VA1 wraps
 * each such argument in fnvnx_va: a char* / void* parameter receives the address of the real va_list, a va_list
 * parameter the va_list itself (from a va_list or from such an address), and an ordinary char* passed to a char*
 * parameter comes back unchanged. */
#ifdef __cplusplus
#include <stdarg.h>
struct fnvnx_va_ref {
    va_list *p;
    operator char *() const { return (char *)p; }
    operator const char *() const { return (const char *)p; }
    operator void *() const { return (void *)p; }
    operator va_list &() const { return *p; }
};
static inline fnvnx_va_ref fnvnx_va(va_list &v) { return fnvnx_va_ref{&v}; }
static inline fnvnx_va_ref fnvnx_va(const void *p) { return fnvnx_va_ref{(va_list *)p}; }
#endif

/* MSVC class layout: derived members start at sizeof(base), never in its tail padding (prepare.py rule TAIL1
 * puts FNVNX_MSVC_BASE_END(last base) first in every derived class body). A zero-width bit-field of an integer
 * type with the base's alignment moves the next member to roundup(dsize(base), alignof(base)) == sizeof(base). */
#ifdef __cplusplus
template <unsigned A> struct fnvnx_int_of_align;
template <> struct fnvnx_int_of_align<1> { typedef unsigned char type; };
template <> struct fnvnx_int_of_align<2> { typedef unsigned short type; };
template <> struct fnvnx_int_of_align<4> { typedef unsigned int type; };
template <> struct fnvnx_int_of_align<8> { typedef unsigned long long type; };
#ifndef FNVNX_MSVC_NAMES
template <> struct fnvnx_int_of_align<16> { typedef unsigned __int128 type; };
#else   /* no __int128 on the name pass's 32-bit target; same alignment */
template <> struct fnvnx_int_of_align<16> { typedef unsigned int type __attribute__((aligned(16))); };
#endif
template <> struct fnvnx_int_of_align<32> { typedef unsigned int type __attribute__((aligned(32))); };
template <> struct fnvnx_int_of_align<64> { typedef unsigned int type __attribute__((aligned(64))); };
template <> struct fnvnx_int_of_align<128> { typedef unsigned int type __attribute__((aligned(128))); };
#define FNVNX_MSVC_BASE_END(...) typename fnvnx_int_of_align<alignof(__VA_ARGS__)>::type : 0;
/* under #pragma pack(N): MSVC places the base at min(alignof(base), N) (TAIL1-PACK) */
#define FNVNX_MSVC_BASE_END_PACK(N, ...) typename fnvnx_int_of_align<(alignof(__VA_ARGS__) < (N) ? alignof(__VA_ARGS__) : (N))>::type : 0;
/* VFPAD1: MSVC pads a class's own vfptr (no base) to the class's alignment, so its first member sits at
 * alignof(class) (8 or 16), not at 4. The rule puts FNVNX_VFPAD(member types) first in such a body: a zero-width
 * bit-field aligned like the most aligned member type moves the first member there (no other effect). */
template <class... T> struct fnvnx_max_align { static const unsigned value = 1; };
template <class H, class... T> struct fnvnx_max_align<H, T...> {
    static const unsigned r = fnvnx_max_align<T...>::value;
    static const unsigned value = alignof(H) > r ? alignof(H) : r;
};
#define FNVNX_VFPAD(...) typename fnvnx_int_of_align<fnvnx_max_align<__VA_ARGS__>::value>::type : 0;
#define FNVNX_VFPAD_PACK(N, ...) typename fnvnx_int_of_align<(fnvnx_max_align<__VA_ARGS__>::value < (N) ? fnvnx_max_align<__VA_ARGS__>::value : (N))>::type : 0;
#endif

/* -fno-sized-deallocation (W41) stops GCC declaring the sized global operator delete. The decomp still calls
 * ::operator delete(p, n) explicitly in about 45 units (MSVC 2008 declared it), so declare it here; libsupc++ defines
 * it (forwards to operator delete(void*)). Implicit delete-expressions stay unsized, as W41 wants. */
#if defined(__cplusplus) && !defined(__cpp_sized_deallocation)
#include <cstddef>
void operator delete(void *, std::size_t) noexcept;
void operator delete[](void *, std::size_t) noexcept;
#endif

/* prepare.py rule NEW5: first member of a class whose only scalar operator delete is the placement form; its deleting
 * destructor frees through the global ::operator delete(void*), the function MSVC 2008 called there. */
#ifdef __cplusplus
#define FNVNX_GLOBAL_DELETE static void operator delete(void *fnvnx_p) noexcept { ::operator delete(fnvnx_p); }
/* NEW5: a defaulted tag parameter on the class's own operator new(size_t): a placement form without a matching
 * delete, so a throwing constructor frees nothing (MSVC 2008, C4291), while the deleting destructor uses the above */
struct fnvnx_new_tag {};
#define FNVNX_NEW5_TAG , fnvnx_new_tag = fnvnx_new_tag()
/* EBO2: first in an empty base whose derived class introduces a vfptr: not empty (MSVC puts it after the vfptr),
 * still 0 bytes */
#define FNVNX_EBO2 char fnvnx_ebo2[0];
/* IDTOR1: a body-less destructor declaration for a class whose implicit destructor the recipe binds (GCC calls the
 * bound D1 by name instead of defining it inline); MSVC's implicit one already compiles to that call */
#define FNVNX_IDTOR1(cls) public: ~cls();
/* HKRO1: Havok hkReferencedObject's getClassType / calcContentStatistics (exe slots 1 / 2) */
#define FNVNX_HKRO1 virtual const void* GetClassType() const; virtual void CalcContentStatistics(void*, const void*) const;
/* A raw exe vftable slot index is the native index: the compiler gives a virtual destructor its one slot, as in the
 * exe (SDD2, toolchain/sdd). The two names are kept for text that still spells an index through them. */
#define FNVNX_VSLOT(k) (k)
#define FNVNX_D26_SLOT(k) (k)
/* EMPTY1: a declared-only empty-string object is the temporary T("") the exe builds */
#define FNVNX_EMPTY1(T, name) T("")
/* DTORB1: a destructor stand-in calls the address the recipe binds its ??1 to (the link's __fnv_<a>), the
 * original body unreachable */
#define FNVNX_DTORB_BEGIN(a) { extern void fnvnx_dtorb_##a(void*) __asm__("__fnv_" #a); fnvnx_dtorb_##a((void*)this); } if (0) {
#define FNVNX_DTORB_END }
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
/* MSVC <crtdefs.h>: _CRT_ALIGN(x) is __declspec(align(x)) (a local copy of the __m128 union uses it) */
#ifndef _CRT_ALIGN
#define _CRT_ALIGN(x) __attribute__((aligned(x)))
#endif

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

/* Patch class MT1 (docs/PATCHES.md): the one gate, FNVNX_MT1_ON, is defined in fnvnx_mt1.h */
#include "fnvnx_mt1.h"
/* MT1 cull sync (patches/mt1_cull_sync.s1b, docs/PERF.md 6.11): wait for this frame's cell update on AI linear
 * thread 2 before the world render culls (toolchain/game/boot.cpp) */
#ifdef __cplusplus
extern "C"
#endif
void fnvnx_mt1_wait_cell_update(void);

#define FNVNX_ASM_FNV_HELPERS
#include "fnvnx_asm.h"


#endif
