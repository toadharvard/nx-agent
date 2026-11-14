/* fnvnx_cmath/fabs.h - the C function fabs alone, as the C library declares it. prepare.py DECL1 includes this in
 * place of the decomp's own prototype of fabs (MATH_NAMES): <math.h> would add the C++ float / long double overloads,
 * which the unit did not have under MSVC unless it includes <math.h> itself. One header per function, so a unit gets
 * exactly the functions it declares. */
#ifdef __GNUC__
#pragma GCC system_header   /* a later redeclaration in the decomp without noexcept is accepted, as after <math.h> */
#endif
#ifndef FNVNX_CMATH_fabs
#define FNVNX_CMATH_fabs
#if defined(__cplusplus) && !defined(_MSC_VER)
extern "C" double fabs(double) noexcept;
#elif defined(__cplusplus)
extern "C" double __cdecl fabs(double);
#else
double fabs(double);
#endif
#endif
