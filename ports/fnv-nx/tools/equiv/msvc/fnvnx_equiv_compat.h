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
#endif
/* _CxxThrowException and _ThrowInfo: predefined by the MSVC C++ front end (nothing to declare) */
#endif
