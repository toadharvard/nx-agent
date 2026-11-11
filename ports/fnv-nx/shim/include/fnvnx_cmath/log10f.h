/* fnvnx_cmath/log10f.h - the C function log10f alone, as the C library declares it. prepare.py DECL1 includes this in
 * place of the decomp's own prototype of log10f (MATH_NAMES): <math.h> would add the C++ float / long double overloads,
 * which the unit did not have under MSVC unless it includes <math.h> itself. One header per function, so a unit gets
 * exactly the functions it declares. */
#ifdef __GNUC__
#pragma GCC system_header   /* a later redeclaration in the decomp without noexcept is accepted, as after <math.h> */
#endif
#ifndef FNVNX_CMATH_log10f
#define FNVNX_CMATH_log10f
#if defined(__cplusplus) && !defined(_MSC_VER)
extern "C" float log10f(float) noexcept;
#elif defined(__cplusplus)
extern "C" float __cdecl log10f(float);
#else
float log10f(float);
#endif
#endif
