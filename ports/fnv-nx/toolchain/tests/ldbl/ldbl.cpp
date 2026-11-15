// ldbl.cpp - the data model of `long double` in the game's compiler (msvcmodel): Visual C++ 2008 has it as binary64, a type of its
// own that mangles as 'O' (double is 'N'). Compiled by test_ldbl.py with the product plugins.
#include <float.h>
static_assert(sizeof(long double) == 8 && alignof(long double) == 8, "long double is 8 bytes, as in cl");
static_assert(__LDBL_MANT_DIG__ == 53 && LDBL_MANT_DIG == 53, "binary64 mantissa");
static_assert(__LDBL_MAX_EXP__ == __DBL_MAX_EXP__ && __LDBL_MIN_EXP__ == __DBL_MIN_EXP__, "binary64 exponent range");
static_assert(__LDBL_MAX__ == __DBL_MAX__ && __LDBL_EPSILON__ == __DBL_EPSILON__ && __LDBL_MIN__ == __DBL_MIN__, "binary64 values");
static_assert(sizeof(1.0L) == 8 && sizeof(long double _Complex) == 16, "literals and complex");
template <class A, class B> struct Same { static const bool v = false; };
template <class A> struct Same<A, A> { static const bool v = true; };
static_assert(!Same<long double, double>::v, "a type of its own, apart from double");
long double ld(long double a, double b) { return a + b; }       // cl: ?ld@@YAONON@Z
double dd(double a) { return a; }                                // cl: ?dd@@YANN@Z
