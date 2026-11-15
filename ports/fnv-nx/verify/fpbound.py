"""fpbound: rule V-FP, derived floating-point comparison bounds (stdlib only, exact Fractions).

Two executions of one source-level computation (x87 original / IEEE ours) each carry, along their own executed
chain, a rigorous absolute bound e on |computed - exact real result of the same chain on the common inputs|.
ACCEPT iff |ours - exe| <= E_ours + E_exe. No tolerance is assigned: every number is derived.
x~, y~: computed operands (exact Fractions), e_x, e_y their bounds (|x - x~| <= e_x), z the computed result,
w the exact result of the op on x~, y~.  Formulas (r = rounding term of the op itself):
  add/sub  e_z = e_x + e_y + r
  mul      e_z = |x~| e_y + |y~| e_x + e_x e_y + r
  div      e_z = (e_x + |x~/y~| e_y) / (|y~| - e_y) + r     requires |y~| > e_y, else UNBOUNDED
  sqrt     e_z = e_x / (2 sqrt(x~ - e_x)) + r   requires x~ > e_x (x~ = e_x = 0 gives 0); sqrt(x~ - e_x) is
           replaced by a rational LOWER bound, so e_z only grows (sound)
  neg/abs  e unchanged;  scale2 (times 2^k): e 2^k + r;  store(fmt): e + r(fmt)
  transc   e_z = lip e_x + max(rel |z| / (1 - rel), floor); lip: caller's Lipschitz bound of f near x~;
           rel: caller's relative bound of the implementation (ours: 2^-p, correctly rounded)
  r = 0 if z == w exactly, else u |z| (u = 2^-p); r = 2^(emin-p) (half the smallest subnormal spacing) when
  |z| < 2^emin.  A bound reaching the overflow threshold (2 - 2^-p) 2^emax makes the value UNBOUNDED.
Proofs (triangle inequality):
  add: |z - (x+y)| <= |z - (x~+y~)| + |x~-x| + |y~-y| <= r + e_x + e_y.
  mul: x~y~ - xy = x~(y~-y) + y(x~-x) and |y| <= |y~| + e_y.
  div: x~/y~ - x/y = (x~-x)/y + (x~/y~)(y-y~)/y with |y| >= |y~| - e_y > 0.
  sqrt: |sqrt(x~)-sqrt(x)| = |x~-x| / (sqrt(x~)+sqrt(x)) and sqrt(x), sqrt(x~) >= sqrt(x~-e_x).
  rounding: nearest p-bit z of w in binade [2^k,2^(k+1)): spacing 2^(k-p+1), |z-w| <= 2^(k-p) <= u|z|; below
  2^emin the spacing is the fixed 2^(emin-p+1), so |z-w| <= 2^(emin-p).
  transc: z = f(x~)(1+d), |d| <= rel => |f(x~)| <= |z|/(1-rel); and |f(x~) - f(x)| <= lip e_x.
Kinds: 'f' finite, '+'/'-' infinity, 'n' NaN (payload/sign not compared), 'u' UNBOUNDED (unstable).
Infinities/NaN from memory or from exact operands are exact. Division by an interval containing 0
(also exactly 0, whose sign is unknown) is UNBOUNDED.
"""
import math
import struct
from collections import namedtuple
from fractions import Fraction as Fr

Fmt = namedtuple("Fmt", "p emin emax")
F32 = Fmt(24, -126, 127)
F64 = Fmt(53, -1022, 1023)
# x87 with a control-word precision: significand p, but always the extended exponent range
X87_24 = Fmt(24, -16382, 16383)
X87_53 = Fmt(53, -16382, 16383)
X87_64 = Fmt(64, -16382, 16383)

_u = lambda f: Fr(1, 2 ** f.p)  # noqa: E731
_tiny = lambda f: Fr(2) ** f.emin  # smallest normal  # noqa: E731
_thr = lambda f: (2 - _u(f)) * Fr(2) ** f.emax  # overflow threshold: largest finite + half ulp  # noqa: E731
_maxfin = lambda f: (2 - 2 * _u(f)) * Fr(2) ** f.emax  # noqa: E731

def _round(w, f, sticky=False):
    """Round-to-nearest-even of Fraction w to f.p bits (subnormals honoured, overflow NOT applied).
    sticky: w is slightly above the given value, so an exact tie rounds up (away from zero)."""
    if w == 0:
        return Fr(0)
    a = abs(w)
    k = a.numerator.bit_length() - a.denominator.bit_length()
    if Fr(2) ** k > a:
        k -= 1
    if Fr(2) ** (k + 1) <= a:
        k += 1
    q = Fr(2) ** (max(k, f.emin) - f.p + 1)
    m, rem = divmod(a, q)
    m = int(m)
    if 2 * rem > q or (2 * rem == q and (sticky or m & 1)):
        m += 1
    return m * q if w > 0 else -(m * q)

def _r(z, f):
    """Rounding term of an inexact correctly rounded result z."""
    a = abs(z)
    r = a * _u(f)
    return max(r, Fr(2) ** (f.emin - f.p)) if a < _tiny(f) else r

def _sqrt_lo(a, bits):
    """Rational s <= sqrt(a) (a > 0 Fraction) with at least `bits` significant bits, on a dyadic grid."""
    n, d = a.numerator, a.denominator
    sh = max(0, (2 * bits - (n.bit_length() - d.bit_length())) // 2 + 1)
    return Fr(math.isqrt((n << (2 * sh)) // d), 1 << sh)

class V:
    """A computed value with its bound: kind in 'f','+','-','n','u'; val Fraction (kind 'f'); err Fraction."""
    __slots__ = ("kind", "val", "err")

    def __init__(self, kind, val=None, err=None):
        self.kind, self.val, self.err = kind, val, err

    @staticmethod
    def fin(val, err=0):
        return V("f", Fr(val), Fr(err))

    @staticmethod
    def from_float(x):
        if math.isnan(x):
            return V("n", err=Fr(0))
        if math.isinf(x):
            return V("+" if x > 0 else "-", err=Fr(0))
        return V("f", Fr(x), Fr(0))

    @staticmethod
    def from_f32bits(b):
        return V.from_float(struct.unpack("<f", struct.pack("<I", b))[0])

    @staticmethod
    def from_f64bits(b):
        return V.from_float(struct.unpack("<d", struct.pack("<Q", b))[0])

    @staticmethod
    def from_x87(se, m):
        """80-bit extended: se = sign(1) + exponent(15), m = 64-bit significand with explicit integer bit."""
        s, ex = se >> 15, se & 0x7FFF
        if ex == 0x7FFF:
            return V("n", err=Fr(0)) if m & ((1 << 63) - 1) else V("-" if s else "+", err=Fr(0))
        v = Fr(m) * Fr(2) ** ((ex if ex else 1) - 16383 - 63)
        return V("f", -v if s else v, Fr(0))

UNB = V("u")
NAN = V("n", err=Fr(0))
_inf = lambda s: V("+" if s > 0 else "-", err=Fr(0))  # noqa: E731
_sg = lambda x: 1 if x.kind == "+" else -1  # sign of an infinity  # noqa: E731
def _finish(z, e, f):
    if e is None or abs(z) > _maxfin(f) or abs(z) + e >= _thr(f):
        return UNB
    return V("f", z, e)
def _pre(x, y):
    if x.kind == "u" or y.kind == "u":
        return UNB
    if x.kind == "n" or y.kind == "n":
        return NAN
    return None
def _sure_sign(x):
    """Sign of the real value behind finite x, 0 if the sign is certainly undetermined or zero."""
    return (1 if x.val > 0 else -1) if abs(x.val) > x.err else 0
def neg(x):
    if x.kind == "f":
        return V("f", -x.val, x.err)
    return {"+": _inf(-1), "-": _inf(1)}.get(x.kind, x)
def abs_(x):
    return V("f", abs(x.val), x.err) if x.kind == "f" else (_inf(1) if x.kind in "+-" else x)
def add(x, y, f):
    p = _pre(x, y)
    if p:
        return p
    if x.kind in "+-" or y.kind in "+-":
        if x.kind in "+-" and y.kind in "+-":
            return x if x.kind == y.kind else NAN
        return x if x.kind in "+-" else y
    w = x.val + y.val
    z = _round(w, f)
    return _finish(z, x.err + y.err + (0 if z == w else _r(z, f)), f)

sub = lambda x, y, f: add(x, neg(y), f)  # noqa: E731
def mul(x, y, f):
    p = _pre(x, y)
    if p:
        return p
    if x.kind in "+-" or y.kind in "+-":
        if x.kind in "+-" and y.kind in "+-":
            return _inf(_sg(x) * _sg(y))
        i, o = (x, y) if x.kind in "+-" else (y, x)
        s = _sure_sign(o)
        if s:
            return _inf(_sg(i) * s)
        return NAN if o.val == 0 and o.err == 0 else UNB
    w = x.val * y.val
    z = _round(w, f)
    ax, ay = abs(x.val), abs(y.val)
    return _finish(z, ax * y.err + ay * x.err + x.err * y.err + (0 if z == w else _r(z, f)), f)

def div(x, y, f):
    p = _pre(x, y)
    if p:
        return p
    if x.kind in "+-":
        if y.kind in "+-":
            return NAN
        s = _sure_sign(y)
        return _inf(_sg(x) * s) if s else UNB
    if y.kind in "+-":
        return V("f", Fr(0), Fr(0))
    ay = abs(y.val)
    if ay <= y.err:
        return UNB
    w = x.val / y.val
    z = _round(w, f)
    e = (x.err + abs(w) * y.err) / (ay - y.err) + (0 if z == w else _r(z, f))
    return _finish(z, e, f)

def sqrt(x, f):
    if x.kind in "un":
        return x
    if x.kind in "+-":
        return x if x.kind == "+" else NAN
    a, e = x.val, x.err
    if a + e < 0:
        return NAN
    if a == 0 and e == 0:
        return V("f", Fr(0), Fr(0))
    if a <= e:
        return UNB
    bits = 2 * f.p + 4
    s = _sqrt_lo(a, bits)
    exact = s * s == a
    z = _round(s, f, sticky=not exact)
    r = 0 if exact and z == s else _r(z, f)
    prop = e / (2 * _sqrt_lo(a - e, bits)) if e else Fr(0)
    return _finish(z, prop + r, f)

def scale2(x, k, f):
    """x * 2^k with the rounding term of the target format (0 when the scaling is exact)."""
    if x.kind != "f":
        return x
    w = x.val * Fr(2) ** k
    z = _round(w, f)
    return _finish(z, x.err * Fr(2) ** k + (0 if z == w else _r(z, f)), f)

def store(x, f):
    """Round x to format f (store to float/double memory, conversion to a narrower format)."""
    if x.kind != "f":
        return x
    z = _round(x.val, f)
    return _finish(z, x.err + (0 if z == x.val else _r(z, f)), f)

def transc(x, z, rel, lip, f):
    """Result z (float/Fraction, the implementation's output in format f) of a transcendental of x.
    rel: relative bound of the implementation; lip: Lipschitz bound of the function near x."""
    if x.kind in "un":
        return x
    if x.kind != "f" or isinstance(z, float) and not math.isfinite(z):
        return UNB
    z, rel = Fr(z), Fr(rel)
    if rel >= 1:
        return UNB
    r = rel * abs(z) / (1 - rel)
    if abs(z) < _tiny(f):
        r = max(r, Fr(2) ** (f.emin - f.p))
    return _finish(z, Fr(lip) * x.err + r, f)

def verdict(ours, exe):
    """'accept' | 'reject' | 'unstable' for one compared output."""
    if ours.kind == "u" or exe.kind == "u":
        return "unstable"
    if ours.kind != "f" or exe.kind != "f":
        return "accept" if ours.kind == exe.kind else "reject"
    return "accept" if abs(ours.val - exe.val) <= ours.err + exe.err else "reject"

def case_verdict(verdicts, stable_flags):
    """A test case: 'unstable' if any decision or output is unstable, else reject if any output rejects."""
    if not all(stable_flags) or "unstable" in verdicts:
        return "unstable"
    return "reject" if "reject" in verdicts else "accept"

# ------------------------------------------------------------------ stability of discrete decisions
def _num(x):
    """Fraction for a finite number, None for NaN/inf."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return Fr(x)

def stable_compare(x, ex, y, ey, op):
    """op in lt, le, eq, un: same truth value for every real in [x-ex, x+ex] x [y-ey, y+ey]."""
    if ex is None or ey is None:
        return False
    a, b = _num(x), _num(y)
    if a is None or b is None or op == "un":
        return True  # decided by NaN-ness / infinity alone
    lo1, hi1, lo2, hi2 = a - ex, a + ex, b - ey, b + ey
    if op == "lt":
        return hi1 < lo2 or lo1 >= hi2
    if op == "le":
        return hi1 <= lo2 or lo1 > hi2
    if op == "eq":
        return (ex == 0 and ey == 0 and a == b) or hi1 < lo2 or hi2 < lo1
    raise ValueError(op)

_MODES = {"trunc": math.trunc, "nearest": round, "floor": math.floor, "ceil": math.ceil}  # round: half-even

def stable_to_int(x, ex, mode, width=32):
    """Conversion to a signed integer of `width` bits is stable iff both interval ends convert to the same
    in-range integer (the conversion is monotone); NaN/inf or an end out of range is unstable."""
    a = _num(x)
    if ex is None or a is None:
        return False
    lo, hi = _MODES[mode](a - ex), _MODES[mode](a + ex)
    top = 1 << (width - 1)
    return lo == hi and -top <= lo < top

def stable_round(x, ex, f):
    """Rounding to format f (then compared bitwise) is stable iff both ends round to the same value
    (monotone); an end beyond the largest finite value is the infinity of that sign."""
    a = _num(x)
    if ex is None:
        return False
    if a is None:
        return True

    def rr(v):
        z = _round(v, f)
        return (1 if z > 0 else -1, None) if abs(z) > _maxfin(f) else (0, z)
    return rr(a - ex) == rr(a + ex)
