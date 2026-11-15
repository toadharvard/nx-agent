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
void ov(long double) {}                                          // cl: ?ov@@YAXO@Z   (an overload apart from double's)
void ov(double) {}                                               // cl: ?ov@@YAXN@Z

// what a program does with a long double: arithmetic, comparisons, every conversion, varargs. No object may call a TFmode
// soft-float entry of libgcc for it (test_ldbl.py: the symbols of libgcc_tf.txt).
typedef __builtin_va_list va_list;
long double l_arith(long double a, long double b) { return (a + b) * (a - b) / (a * b) + -a; }
int l_cmp(long double a, long double b) { return (a < b) + 2 * (a <= b) + 4 * (a == b) + 8 * (a != b) + 16 * (a > b) + 32 * (a >= b) + 64 * (a != a); }
long double l_from(int i, unsigned u, long long q, unsigned long long uq, float f, double d) { return i + (long double)u + q + uq + f + d; }
int l_i(long double a) { return (int)a; }
unsigned l_u(long double a) { return (unsigned)a; }
long long l_q(long double a) { return (long long)a; }
unsigned long long l_uq(long double a) { return (unsigned long long)a; }
float l_f(long double a) { return (float)a; }
double l_d(long double a) { return (double)a; }
long double l_pow(long double a, int n) { return __builtin_powil(a, n); }
long double l_va(int n, ...) { va_list ap; __builtin_va_start(ap, n); long double s = 0; while (n--) s += __builtin_va_arg(ap, long double); __builtin_va_end(ap); return s; }
long double l_vcall() { return l_va(3, 1.0L, 2.0L, 3.5L); }
long double _Complex l_cmul(long double _Complex a, long double _Complex b) { return a * b; }
long double _Complex l_cdiv(long double _Complex a, long double _Complex b) { return a / b; }
long double l_abs(long double a) { return __builtin_fabsl(a); }
