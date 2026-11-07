/* fnvnx_equiv_compat.h - force-included (/FI) into PREPARED units only in S11 MSVC 2008 builds (tools/equiv).
 *
 * MSVC 2008 stand-in for the declarations shim/include/fnvnx_compat.h gives every prepared unit on the target:
 * the names prepare.py DECL1 drops the decomp's own prototypes of without including a header in their place
 * (CRT_HEADERS[''] in tools/prepare.py). Each one is declared the way MSVC's own headers declare it, so the call
 * compiles as it did with the decomp's prototype. The pristine decomp declares these itself, so it does not get
 * this header.
 */
#ifndef FNVNX_EQUIV_COMPAT_H
#define FNVNX_EQUIV_COMPAT_H
/* CRT-NOCPPOVL: the target CRT has no C++ template overloads of the secure functions (strcpy_s(char (&)[N], src),
 * strcat_s, sprintf_s, ...); MSVC 2008 declares them in C++ unless this is 0. A unit that keeps its own template of
 * that form next to DECL1's `using ::strcat_s;` (archive.cpp's fnv_00afad00) was ambiguous under MSVC only. */
#ifndef _CRT_SECURE_CPP_OVERLOAD_SECURE_NAMES
#define _CRT_SECURE_CPP_OVERLOAD_SECURE_NAMES 0
#endif
#ifdef __cplusplus
extern "C" {
#endif
void *__cdecl _alloca(unsigned int);                  /* malloc.h; an intrinsic */
#pragma intrinsic(_alloca)
void _ReadWriteBarrier(void);                         /* intrin.h; compiler barriers, intrinsics */
void _ReadBarrier(void);
void _WriteBarrier(void);
#pragma intrinsic(_ReadWriteBarrier, _ReadBarrier, _WriteBarrier)
void __cdecl operator_delete_sized(void *, unsigned int);   /* the decomp's spelling (engine functions) */
void __cdecl _operator_delete_sized(void *, unsigned int);
#ifdef __cplusplus
}
/* C++14 sized deallocation: implicitly declared in every unit on the target (-std=gnu++17), where DECL1 drops the
 * decomp's own `void operator delete(void*, unsigned int);`. MSVC 2008 has no implicit one. */
void __cdecl operator delete(void *, unsigned int);
void __cdecl operator delete[](void *, unsigned int);
/* ... and the usual unsized forms, also implicit on the target and in MSVC: declared beside the sized ones so that
 * an explicit `operator delete[](p)` still finds them (a declared overload hides MSVC's implicit one;
 * tesland_od_variant_decl_variant, C2660) */
void __cdecl operator delete(void *);
void __cdecl operator delete[](void *);
#endif
/* _CxxThrowException and _ThrowInfo: predefined by the MSVC C++ front end (nothing to declare) */
/* ENUM1-FWD: fnvnx_compat.h's `: int` base of an opaque enum declaration; MSVC 2008 has no opaque declarations, its
 * forward declaration `enum X;` (the decomp's own spelling) declares the same int-based type */
#define FNVNX_ENUM_INT
#endif
