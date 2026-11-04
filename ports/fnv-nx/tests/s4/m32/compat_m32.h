/* compat_m32.h - force-included into render/nine/d3dx/ for the -m32 host tests (tests/s4/m32) in place of
 * shim/include/fnvnx_compat.h, which is AArch64-ILP32-only (sse2neon). Only the keyword macros d3dx and the shim's
 * D3D9/D3DX headers need: on i386 the data model (ILP32) and the struct layouts equal the game's. */
#ifndef FNVNX_COMPAT_M32_H
#define FNVNX_COMPAT_M32_H
#if !defined(__i386__)
#error "compat_m32.h is for the gcc -m32 host tests"
#endif
#define __cdecl
#define __stdcall
#define __fastcall
#define __thiscall
#define WINAPI
#define APIENTRY
#define CALLBACK
#define WINAPIV
#define STDMETHODCALLTYPE
#define NTAPI
#define __forceinline inline __attribute__((always_inline))
#define __inline inline
#endif
