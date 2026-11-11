/* fnvnx_cmath/fmod.h - the C function fmod alone, as the C library declares it. prepare.py DECL1 includes this in
 * place of the decomp's own prototype of fmod (MATH_NAMES): <math.h> would add the C++ float / long double overloads,
 * which the unit did not have under MSVC unless it includes <math.h> itself. One header per function, so a unit gets
 * exactly the functions it declares. */
#ifdef __GNUC__
#pragma GCC system_header   /* a later redeclaration in the decomp without noexcept is accepted, as after <math.h> */
#endif
#ifndef FNVNX_CMATH_fmod
#define FNVNX_CMATH_fmod
#if defined(__cplusplus) && !defined(_MSC_VER)
extern "C" double fmod(double, double) noexcept;
#elif defined(__cplusplus)
extern "C" double __cdecl fmod(double, double);
#else
double fmod(double, double);
#endif
#endif
