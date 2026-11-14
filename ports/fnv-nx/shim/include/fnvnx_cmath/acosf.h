/* fnvnx_cmath/acosf.h - the C function acosf alone, as the C library declares it. prepare.py DECL1 includes this in
 * place of the decomp's own prototype of acosf (MATH_NAMES): <math.h> would add the C++ float / long double overloads,
 * which the unit did not have under MSVC unless it includes <math.h> itself. One header per function, so a unit gets
 * exactly the functions it declares. */
#ifdef __GNUC__
#pragma GCC system_header   /* a later redeclaration in the decomp without noexcept is accepted, as after <math.h> */
#endif
#ifndef FNVNX_CMATH_acosf
#define FNVNX_CMATH_acosf
#if defined(__cplusplus) && !defined(_MSC_VER)
extern "C" float acosf(float) noexcept;
#elif defined(__cplusplus)
extern "C" float __cdecl acosf(float);
#else
float acosf(float);
#endif
#endif
