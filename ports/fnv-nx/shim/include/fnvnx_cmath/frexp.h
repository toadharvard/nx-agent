/* fnvnx_cmath/frexp.h - the C function frexp alone, as the C library declares it. prepare.py DECL1 includes this in
 * place of the decomp's own prototype of frexp (MATH_NAMES): <math.h> would add the C++ float / long double overloads,
 * which the unit did not have under MSVC unless it includes <math.h> itself. One header per function, so a unit gets
 * exactly the functions it declares. */
#ifdef __GNUC__
#pragma GCC system_header   /* a later redeclaration in the decomp without noexcept is accepted, as after <math.h> */
#endif
#ifndef FNVNX_CMATH_frexp
#define FNVNX_CMATH_frexp
#if defined(__cplusplus) && !defined(_MSC_VER)
extern "C" double frexp(double, int*) noexcept;
#elif defined(__cplusplus)
extern "C" double __cdecl frexp(double, int*);
#else
double frexp(double, int*);
#endif
#endif
