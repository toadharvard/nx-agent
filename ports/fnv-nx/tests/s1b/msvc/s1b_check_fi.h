/* s1b_check_fi.h - force-included by tests/s1b/msvc_check.py into MSVC 2008 builds of patched units only.
 *
 * MSVC meaning of the names the S1b patches insert. FTOL1: on the target fnvnx_ftol2 is a function (platform/crt
 * math.c, the x87 conversion with its operand passed); under MSVC x86 the same conversion is the compiler's own
 * float -> __int64 cast, which it compiles to a call of _ftol2_sse with the operand in ST0 - the code the decomp's
 * arg-less helper calls stand for. The patches declare the function as `(fnvnx_ftol2)(double)`, so this macro leaves
 * the declaration alone and only turns the calls into the cast. Must not be used for pristine builds.
 */
#ifndef FNVNX_S1B_CHECK_FI_H
#define FNVNX_S1B_CHECK_FI_H
#define fnvnx_ftol2(x) ((__int64)(x))
/* the int conversion that MSVC compiles to `call __ftol2_sse` (FTOL1 sites define the target helper as
 * `(fnvnx_ftol2_sse)(double)`, untouched by this macro) */
#define fnvnx_ftol2_sse(x) ((int)(x))
#endif
