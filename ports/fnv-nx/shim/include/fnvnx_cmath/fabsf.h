/* fnvnx_cmath/fabsf.h - the C function fabsf alone, as the C library declares it. prepare.py DECL1 includes this in
 * place of the decomp's own prototype of fabsf (MATH_NAMES): <math.h> would add the C++ float / long double overloads,
 * which the unit did not have under MSVC unless it includes <math.h> itself. One header per function, so a unit gets
 * exactly the functions it declares. */
#ifdef __GNUC__
#pragma GCC system_header   /* a later redeclaration in the decomp without noexcept is accepted, as after <math.h> */
#endif
#ifndef FNVNX_CMATH_fabsf
#define FNVNX_CMATH_fabsf
#if defined(__cplusplus) && !defined(_MSC_VER)
extern "C" float fabsf(float) noexcept;
#elif defined(__cplusplus)
extern "C" float __cdecl fabsf(float);
#else
float fabsf(float);
#endif
#endif
