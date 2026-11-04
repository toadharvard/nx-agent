/* fnvnx_equiv_fi.h - force-included (/FI) into every unit on BOTH sides of an S11 MSVC 2008 build (tools/equiv).
 *
 * Gives the macros that prepare rules insert their MSVC 2008 meaning. Must not declare anything that emits code or
 * changes a declaration the pristine decomp has: the pristine side never uses these names.
 */
#ifndef FNVNX_EQUIV_FI_H
#define FNVNX_EQUIV_FI_H

/* KEEP1: GCC __attribute__((used)) keeps an out-of-line copy for the link-by-address step; it has no meaning for
 * the code of any function, and MSVC has no counterpart for functions. */
#define FNVNX_KEEP

/* NEW3: C++11 spells what every deallocation function already is (implicitly noexcept); MSVC 2008 predates the
 * keyword, the declaration it applies to is the decomp's unchanged one. */
#define noexcept

/* RCAST1: (T&)fnvnx_lvalue(f(x)) - fnvnx_lvalue hands back the temporary itself (fnvnx_compat.h); MSVC binds the
 * C-style reference cast to the temporary directly, so the identity is the MSVC meaning. A wrong extent of the
 * wrapped expression changes the parse and shows up as a difference. */
#define fnvnx_lvalue(x) (x)

/* TAIL1 (layout rule, only in layout runs): a zero-width bit-field of an integer type aligned like the last base,
 * as in fnvnx_compat.h. MSVC never reuses tail padding, so under MSVC it must change no layout. */
#ifdef __cplusplus
template <unsigned A> struct fnvnx_int_of_align;
template <> struct fnvnx_int_of_align<1> { typedef unsigned char type; };
template <> struct fnvnx_int_of_align<2> { typedef unsigned short type; };
template <> struct fnvnx_int_of_align<4> { typedef unsigned int type; };
template <> struct fnvnx_int_of_align<8> { typedef unsigned __int64 type; };
template <> struct fnvnx_int_of_align<16> { typedef unsigned __int64 type; };
#define FNVNX_MSVC_BASE_END(...) fnvnx_int_of_align<__alignof(__VA_ARGS__)>::type : 0;
#endif

/* FTOL1 (S1b): fnvnx_ftol2(x) is the x87 _ftol2_sse of x; under MSVC the cast compiles to exactly that call */
#ifndef fnvnx_ftol2
#define fnvnx_ftol2(x) ((__int64)(x))
#endif

#endif
