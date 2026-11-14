/* fnvnx_vtm.h - VTM1 (S1-rules, S5-boot link 0829588572d1): the decomp models an exe vftable as a struct of function
 * pointers (`struct VTable { void* f0; void* f1; void (__thiscall* f2)(void*); }`, called as `o->vt->f2(o)`). Under
 * D26 a native table is minus-one: GCC's D1 / D0 pair where MSVC has ??_G, so every later slot is one further. VTM1
 * puts FNVNX_VTM1_PAD after the destructor slot's entry: one more pointer under GCC, nothing under MSVC. Included
 * only by the units VTM1 rewrites (never force-included). */
#ifndef FNVNX_VTM_H
#define FNVNX_VTM_H
#if defined(_MSC_VER) && !defined(__clang__) || defined(FNVNX_STATIC_MSVC)
#define FNVNX_VTM1_PAD
#else
#define FNVNX_VTM1_PAD void *fnvnx_vtm1_d0;
#endif
#endif
