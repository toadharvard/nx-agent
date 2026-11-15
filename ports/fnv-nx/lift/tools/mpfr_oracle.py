"""mpfr_oracle - the MPFR oracles of the x87 transcendentals and the msvcrt math functions: the true value (an mpfr of 400+ bits, or an exact Fraction where it is
exactly known), its one rounding to binary64 (rnd) and the x87 exception flags that value implies (expect_flags). One definition, used by test/fmathtest.py (rt/fmath.c)
and by the generated stage of the lift tests (tests/guest/*/gen.py), so an expected value is computed in one place. MPFR is the reference; its version is the pinned wheel's (env.json python.modules gmpy2)."""
import math, struct
import gmpy2
from fractions import Fraction

gmpy2.get_context().precision = 400
gmpy2.get_context().emax = 1 << 40
gmpy2.get_context().emin = -(1 << 40)

def u64_(x): return struct.unpack('<Q', struct.pack('<d', x))[0]
def f64_(u): return struct.unpack('<d', struct.pack('<Q', u))[0]

# ------------------------------------------------------------------ oracle (MPFR, 400 bits, one rounding to binary64)
def tofloat(v):
    try:
        return float(v)
    except OverflowError:
        return math.copysign(math.inf, float(v.real if hasattr(v, 'real') else v))


def mf(x): return gmpy2.mpfr(x)
LN2 = None


def exact_float(fr):
    """correctly rounded (nearest even) binary64 of a Fraction; infinity on overflow"""
    try:
        return float(fr)
    except OverflowError:
        return math.inf if fr > 0 else -math.inf


def is_pow2_frac(fr):
    return fr > 0 and (fr.numerator & (fr.numerator - 1)) == 0 and (fr.denominator & (fr.denominator - 1)) == 0


def log2_exact(fr):
    """k if fr == 2^k else None"""
    if not is_pow2_frac(fr):
        return None
    return fr.numerator.bit_length() - fr.denominator.bit_length()


# each oracle returns the true value (an mpfr of 400 bits, or an exact Fraction where the value is exactly known)
def ex_of(x):
    return abs(math.frexp(x)[1]) if x != 0 and math.isfinite(x) else 0


def prec(bits):
    """a context with 400 + bits of precision: the true value can lie within 2^-bits (relative) of a double or of a midpoint without being on it"""
    c = gmpy2.get_context().copy()
    c.precision = 400 + int(bits)
    return c


def o_sin(x, y=0):
    with prec(2 * ex_of(x)): return +gmpy2.sin(mf(x))


def o_cos(x, y=0):
    with prec(2 * ex_of(x)): return +gmpy2.cos(mf(x))


def o_tan(x, y=0):
    with prec(2 * ex_of(x)): return +gmpy2.tan(mf(x))


def o_atan2(y, x):
    with prec(abs(ex_of(y) - ex_of(x)) * 2 + 8): return +gmpy2.atan2(mf(y), mf(x))


def o_2xm1(x, y=0):
    fx = Fraction(x)
    if fx.denominator == 1 and abs(x) < 1100:
        return Fraction(2) ** int(fx) - 1
    with prec(max(0, -x) + 64): return +gmpy2.expm1(mf(x) * gmpy2.log(mf(2)))


def o_yl2x(y, x):
    k = log2_exact(Fraction(x))
    if k is not None:
        return Fraction(y) * k
    with prec(2 * ex_of(x)): return +(mf(y) * gmpy2.log2(mf(x)))


def o_yl2xp1(y, x):
    u = Fraction(x) + 1
    k = log2_exact(u)
    if k is not None:
        return Fraction(y) * k
    with prec(2 * ex_of(x) + 8): return +(mf(y) * gmpy2.log1p(mf(x)) / gmpy2.log(mf(2)))


def rnd(v):
    """correctly rounded binary64 of the true value v"""
    if isinstance(v, Fraction):
        return exact_float(v)
    try:
        return float(v)
    except OverflowError:
        return math.inf if v > 0 else -math.inf


def expect_flags(v, res):
    """PE, OE, UE and C1 (rounded up in magnitude) the instruction must report for true value v and result res"""
    if math.isinf(res):
        return 0x8 | 0x20 | 0x200
    r = Fraction(res) if isinstance(v, Fraction) else mf(res)
    inexact = r != v
    f = 0
    if inexact:
        f |= 0x20
        if (r > v) if v > 0 else (r < v):                 # no abs() / arithmetic on the mpfr: it would round to the context precision
            f |= 0x200
        if (v < Fraction(1, 2 ** 1022) and v > -Fraction(1, 2 ** 1022)) if isinstance(v, Fraction) else (-gmpy2.mpfr(2) ** -1022 < v < gmpy2.mpfr(2) ** -1022):
            f |= 0x10
    return f


ORACLE = {'sin': o_sin, 'cos': o_cos, 'tan': o_tan, 'atan2': o_atan2, '2xm1': o_2xm1, 'yl2x': o_yl2x, 'yl2xp1': o_yl2xp1}


# ------------------------------------------------------------------ msvcrt library functions: oracles (MPFR; sentinels where the value is out of the mpfr exponent range but its rounding is decided by the region)
def tb(x):
    """bits below 1 of the leading bit of x: the extra precision needed to tell 1 + x or x + x^3 from 1 / x"""
    return max(0, -math.frexp(x)[1]) if x != 0 and math.isfinite(x) else 0


def o_acos(x, y=0):
    with prec(2 * tb(x)): return +gmpy2.acos(mf(x))


def o_asin(x, y=0):
    with prec(2 * tb(x)): return +gmpy2.asin(mf(x))


def o_atan(x, y=0):
    with prec(2 * tb(x)): return +gmpy2.atan(mf(x))


def o_cosh(x, y=0):
    with prec(2 * tb(x) + 8): return +gmpy2.cosh(mf(x))


def o_sinh(x, y=0):
    with prec(2 * tb(x) + 8): return +gmpy2.sinh(mf(x))


def o_tanh(x, y=0):
    if abs(x) >= 100:                                  # 1 - tanh < 2^-288: only the side of 1 matters
        with prec(0): return mf(math.copysign(1.0, x)) * (1 - gmpy2.mpfr(2) ** -300)
    with prec(2 * tb(x) + 8): return +gmpy2.tanh(mf(x))


def o_exp(x, y=0):
    if x < -800: return gmpy2.mpfr(2) ** -2000
    if x > 800: return gmpy2.mpfr(2) ** 2000
    with prec(tb(x) + 8): return +gmpy2.exp(mf(x))


def o_log(x, y=0):
    with prec(8): return +gmpy2.log(mf(x))


def o_pow(x, y):
    ax = abs(x)
    neg = x < 0 and int(y) % 2 == 1
    tf = y * math.log(ax)
    if abs(tf) > 6000:
        v = gmpy2.mpfr(2) ** (6000 if tf > 0 else -6000)
    else:
        extra = 8 if abs(tf) >= 1e-3 else (int(-math.log2(abs(tf))) + 64 if tf != 0 else 1300)
        with prec(extra):
            v = +(mf(ax) ** mf(y))
    return -v if neg else v


ORACLE.update({'acos': o_acos, 'asin': o_asin, 'atan': o_atan, 'cosh': o_cosh, 'sinh': o_sinh, 'tanh': o_tanh, 'exp': o_exp, 'log': o_log, 'pow': o_pow})
