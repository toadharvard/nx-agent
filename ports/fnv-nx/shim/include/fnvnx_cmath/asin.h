/* fnvnx_cmath/asin.h - the C function asin alone, as the C library declares it. prepare.py DECL1 includes this in
 * place of the decomp's own prototype of asin (MATH_NAMES): <math.h> would add the C++ float / long double overloads,
 * which the unit did not have under MSVC unless it includes <math.h> itself. One header per function, so a unit gets
 * exactly the functions it declares. */
#ifdef __GNUC__
#pragma GCC system_header   /* a later redeclaration in the decomp without noexcept is accepted, as after <math.h> */
#endif
#ifndef FNVNX_CMATH_asin
#define FNVNX_CMATH_asin
#if defined(__cplusplus) && !defined(_MSC_VER)
extern "C" double asin(double) noexcept;
#elif defined(__cplusplus)
extern "C" double __cdecl asin(double);
#else
double asin(double);
#endif
#endif
