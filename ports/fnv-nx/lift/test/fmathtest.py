#!/usr/bin/env python3
"""Gate for rt/fmath.c (x87 transcendentals: correctly rounded; fprem/fprem1/fscale/fxtract: exact).
usage: fmathtest.py [--n N] [--only fn,fn] [--seed S] [--quick]
(1) every function vs an MPFR oracle (gmpy2, 400 bits, rounded once to binary64) on special values, boundaries and >= N (200000) deterministic inputs
    over all exponents; exact cases (integer x for 2^x-1, power-of-two arguments of the logs) vs exact rational arithmetic;
(2) special operands (zeros, infinities, NaNs, |x| >= 2^63 C2 case) and fprem / fprem1 / fscale / fxtract BIT FOR BIT vs the real x87 of this host
    (family/model/stepping printed), incl. every exponent difference -3..2100 for fprem / fprem1 (partial reduction rule of the reference CPU);
(3) the arena high-water mark (FM_DEBUG traps on overflow of the preallocated temporaries).
FMATH_NSTART=2 forces every call through the doubling loop (stress of the Ziv iteration; the default start is 6 limbs).
Needs gmpy2 (pip install --break-system-packages gmpy2). Exit 1 on any difference."""
import argparse, ctypes, math, multiprocessing as mp, os, random, struct, subprocess, sys, time
import numpy as np
import gmpy2
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.join(HERE, '..', 'rt')
WORK = os.environ.get('FMATH_WORK', '/home/user/l3work/fmath')
gmpy2.get_context().precision = 400
gmpy2.get_context().emax = 1 << 40
gmpy2.get_context().emin = -(1 << 40)

DRIVER = r'''
#include "fmath.h"
typedef unsigned long long u64;
enum { O_SIN, O_COS, O_TAN, O_ATAN2, O_2XM1, O_YL2X, O_YL2XP1, O_SCALE, O_XTRACT, O_FPREM, O_FPREM1, O_SINCOS, O_ACOS, O_ASIN, O_ATAN, O_COSH, O_SINH, O_TANH, O_EXP, O_LOG, O_POW, O_FMOD, O_SQRT };
void run_m(int op, unsigned n, const double *a, const double *b, double *o1, double *o2, unsigned *fl, unsigned *cf) {
  for (unsigned i = 0; i < n; i++) { unsigned f = 0, c = 0; double r = 0, r2 = 0;
    switch (op) {
    case O_SIN: r = fnvl_m_sin(a[i], &f); break;
    case O_COS: r = fnvl_m_cos(a[i], &f); break;
    case O_TAN: r = fnvl_m_tan(a[i], &f); break;
    case O_ATAN2: r = fnvl_m_atan2(a[i], b[i], &f); break;
    case O_2XM1: r = fnvl_m_2xm1(a[i], &f); break;
    case O_YL2X: r = fnvl_m_yl2x(a[i], b[i], &f); break;
    case O_YL2XP1: r = fnvl_m_yl2xp1(a[i], b[i], &f); break;
    case O_SCALE: r = fnvl_m_scale(a[i], b[i], &f); break;
    case O_XTRACT: fnvl_m_xtract(a[i], &r, &r2, &f); break;
    case O_FPREM: r = fnvl_m_fprem(a[i], b[i], 0, &c, &f); break;
    case O_FPREM1: r = fnvl_m_fprem(a[i], b[i], 1, &c, &f); break;
    case O_SINCOS: fnvl_m_sincos(a[i], &r2, &r, &f); break;       /* o1 = cos (ST0), o2 = sin (ST1) like the x87 */
    case O_ACOS: r = fnvl_m_acos(a[i], &f); break;
    case O_ASIN: r = fnvl_m_asin(a[i], &f); break;
    case O_ATAN: r = fnvl_m_atan(a[i], &f); break;
    case O_COSH: r = fnvl_m_cosh(a[i], &f); break;
    case O_SINH: r = fnvl_m_sinh(a[i], &f); break;
    case O_TANH: r = fnvl_m_tanh(a[i], &f); break;
    case O_EXP: r = fnvl_m_exp(a[i], &f); break;
    case O_LOG: r = fnvl_m_log(a[i], &f); break;
    case O_POW: r = fnvl_m_pow(a[i], b[i], &f); break;
    case O_FMOD: r = fnvl_m_fmod(a[i], b[i], &f); break;
    case O_SQRT: r = fnvl_m_sqrt(a[i], &f); break;
    }
    o1[i] = r; o2[i] = r2; fl[i] = f; cf[i] = c | ((op <= O_TAN || op == O_SINCOS) ? (fnvl_m_trig_big(a[i]) ? 0x400 : 0) : 0); } }
unsigned bigflag(double x) { return fnvl_m_trig_big(x); }
extern int fm_nstart;
void set_nstart(int n) { fm_nstart = n; }
/* error-bound check: record the first evaluation (n limbs, A, B) of a call */
extern int fm_nstart; extern void (*fm_hook)(int, const unsigned *, int, int, int, int, int);
static int h_n, h_e, h_s, h_z, h_B, h_r, h_cnt; static unsigned h_m[4096];
static void hook(int n, const unsigned *m, int e, int s, int z, int B, int r) {
  if (h_cnt++) return;
  h_n = n; h_e = e; h_s = s; h_z = z; h_B = B; h_r = r; for (int i = 0; i < n && i < 4096; i++) h_m[i] = m[i]; }
int dbg_eval(int op, double a, double b, int nstart, unsigned *m, int *info) {
  unsigned f = 0, c = 0; double r1, r2; fm_nstart = nstart; fm_hook = hook; h_cnt = 0;
  switch (op) {
  case O_SIN: r1 = fnvl_m_sin(a, &f); break; case O_COS: r1 = fnvl_m_cos(a, &f); break; case O_TAN: r1 = fnvl_m_tan(a, &f); break;
  case O_ATAN2: r1 = fnvl_m_atan2(a, b, &f); break; case O_2XM1: r1 = fnvl_m_2xm1(a, &f); break;
  case O_YL2X: r1 = fnvl_m_yl2x(a, b, &f); break; case O_YL2XP1: r1 = fnvl_m_yl2xp1(a, b, &f); break;
  case O_ACOS: r1 = fnvl_m_acos(a, &f); break; case O_ASIN: r1 = fnvl_m_asin(a, &f); break; case O_ATAN: r1 = fnvl_m_atan(a, &f); break;
  case O_COSH: r1 = fnvl_m_cosh(a, &f); break; case O_SINH: r1 = fnvl_m_sinh(a, &f); break; case O_TANH: r1 = fnvl_m_tanh(a, &f); break;
  case O_EXP: r1 = fnvl_m_exp(a, &f); break; case O_LOG: r1 = fnvl_m_log(a, &f); break; case O_POW: r1 = fnvl_m_pow(a, b, &f); break; default: break; }
  (void)r1; (void)r2; (void)c; fm_hook = 0;
  if (!h_cnt) return 0;
  for (int i = 0; i < h_n; i++) m[i] = h_m[i];
  info[0] = h_n; info[1] = h_e; info[2] = h_s; info[3] = h_z; info[4] = h_B; info[5] = h_r;
  return 1; }
/* the real x87 (fninit: extended precision, round to nearest). o1 = ST0 after (popped first), o2 = ST1; sw captured right after the instruction */
void run_h(int op, unsigned n, const double *a, const double *b, double *o1, double *o2, unsigned *sw) {
  for (unsigned i = 0; i < n; i++) { double x = a[i], y = b[i], r1 = 0, r2 = 0; unsigned short s = 0;
    switch (op) {
    case O_SIN: __asm__ volatile("fninit; fldl %3; fsin; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x) : "st", "st(1)"); break;
    case O_COS: __asm__ volatile("fninit; fldl %3; fcos; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x) : "st", "st(1)"); break;
    case O_TAN: __asm__ volatile("fninit; fldl %3; fptan; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x) : "st", "st(1)"); break;
    case O_ATAN2: __asm__ volatile("fninit; fldl %3; fldl %4; fpatan; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x), "m"(y) : "st", "st(1)"); break;
    case O_2XM1: __asm__ volatile("fninit; fldl %3; f2xm1; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x) : "st", "st(1)"); break;
    case O_YL2X: __asm__ volatile("fninit; fldl %3; fldl %4; fyl2x; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x), "m"(y) : "st", "st(1)"); break;
    case O_YL2XP1: __asm__ volatile("fninit; fldl %3; fldl %4; fyl2xp1; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x), "m"(y) : "st", "st(1)"); break;
    case O_SCALE: __asm__ volatile("fninit; fldl %4; fldl %3; fscale; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x), "m"(y) : "st", "st(1)"); break;
    case O_XTRACT: __asm__ volatile("fninit; fldl %3; fxtract; fnstsw %2; fstpl %1; fstpl %0" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x) : "st", "st(1)"); break;
    case O_FPREM: __asm__ volatile("fninit; fldl %4; fldl %3; fprem; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x), "m"(y) : "st", "st(1)"); break;
    case O_SINCOS: __asm__ volatile("fninit; fldl %3; fsincos; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x) : "st", "st(1)"); break;
    case O_FPREM1: __asm__ volatile("fninit; fldl %4; fldl %3; fprem1; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x), "m"(y) : "st", "st(1)"); break;
    case O_FMOD: __asm__ volatile("fninit; fldl %4; fldl %3; 1: fprem; fnstsw %%ax; testw $0x400, %%ax; jnz 1b; fnstsw %2; fstpl %0; fstpl %1" : "=m"(r1), "=m"(r2), "=m"(s) : "m"(x), "m"(y) : "st", "st(1)", "ax", "cc"); break;
    case O_SQRT: __asm__ volatile("sqrtsd %1, %0" : "=x"(r1) : "x"(x)); break;
    }
    o1[i] = r1; o2[i] = r2; sw[i] = s; } }
'''
OPS = {n: i for i, n in enumerate('sin cos tan atan2 2xm1 yl2x yl2xp1 scale xtract fprem fprem1 sincos acos asin atan cosh sinh tanh exp log pow fmod sqrt'.split())}
LIB = None


def build():
    global LIB
    os.makedirs(WORK, exist_ok=True)
    open(WORK + '/driver.c', 'w').write(DRIVER)
    subprocess.check_call(['gcc', '-O2', '-shared', '-fPIC', '-DFM_DEBUG', '-I', RT, RT + '/fmath.c', WORK + '/driver.c', '-o', WORK + '/fmath_t.so'])
    LIB = ctypes.CDLL(WORK + '/fmath_t.so')
    LIB.set_nstart(int(os.environ.get('FMATH_NSTART', '4')))




def arr(v): return np.ascontiguousarray(np.array(v, dtype=np.float64))


def run(fn, which, a, b=None):
    """-> (o1, o2, fl, cf or sw) as numpy arrays; fn = 'm' (fmath) or 'h' (host x87)"""
    n = len(a)
    a = arr(a); b = arr(b if b is not None else np.zeros(n))
    o1 = np.zeros(n); o2 = np.zeros(n); fl = np.zeros(n, np.uint32)
    cf = np.zeros(n, np.uint32)
    P = lambda x: x.ctypes.data_as(ctypes.c_void_p)
    if fn == 'm':
        LIB.run_m(OPS[which], n, P(a), P(b), P(o1), P(o2), P(fl), P(cf))
        return o1, o2, fl, cf
    LIB.run_h(OPS[which], n, P(a), P(b), P(o1), P(o2), P(cf))
    return o1, o2, fl, cf


def bits(a): return np.asarray(a, np.float64).view(np.uint64)


sys.path.insert(0, os.path.join(HERE, "..", "tools"))
from mpfr_oracle import *   # the MPFR oracles (tools/mpfr_oracle.py): u64_, f64_, tofloat, mf, exact_float, prec, o_*, rnd, expect_flags, ORACLE, tb

# ------------------------------------------------------------------ generators (deterministic)
def rnd_double(r, elo, ehi):
    e = r.randint(elo, ehi)
    if e < -1022:   # subnormal
        m = r.getrandbits(52) >> (-1022 - e) if -1022 - e < 52 else r.getrandbits(1)
        return f64_(m | (r.getrandbits(1) << 63)) if m else 5e-324
    return f64_((r.getrandbits(1) << 63) | ((e + 1023) << 52) | r.getrandbits(52))


def lead(r, e):
    """random double with the leading bit exactly at 2^e (e in [-1074, 1023]), random sign"""
    sg = r.getrandbits(1) << 63
    if e >= -1022:
        return f64_(sg | ((e + 1023) << 52) | r.getrandbits(52))
    k = e + 1074
    return f64_(sg | (1 << k) | (r.getrandbits(k) if k else 0))


def nextafter(x, k):
    u = u64_(x)
    s = u >> 63
    m = u & ~(1 << 63)
    m = m + k if not s else m - k
    return f64_(((m & ((1 << 63) - 1)) | (s << 63)) if m >= 0 else 0.0) if m >= 0 else 0.0


def ulpstep(x, k):
    """x moved k units in the last place (magnitude), sign kept"""
    u = u64_(x)
    s = u & (1 << 63)
    m = (u & ~(1 << 63)) + k
    if m < 0 or m >= 0x7ff0000000000000:
        return x
    return f64_(m | s)


def nearest_pi_halves(r, count, kmax):
    out = []
    pi2 = gmpy2.const_pi() / 2
    for _ in range(count):
        k = r.randint(1, kmax)
        out.append(float(k * pi2))
    return out


def gen_trig(r, N):
    xs = []
    xs += [0.5, 1.0, 1.5, 2.0, math.pi / 2, math.pi, 1e-3, 1e-8, 2.0 ** -27, 2.0 ** -28, 2.0 ** -26, 5e-324, 2.0 ** -1022, 2.0 ** 62, 2.0 ** 63 - 1024.0,
           4.6116860184273879e18, 1e18, 1e10, 1e5, float.fromhex('0x1.6ac5b262ca1ffp+849'), 2.0 ** 1023,
           float.fromhex('0x1.fffffffffffffp+1023'), 6381956970095103.0 * 2.0 ** 797]
    for k in (1, 2, 3, 4, 5, 6, 7, 8, 100, 1000, 12345, 1 << 20):
        xs += [float(k * gmpy2.const_pi() / 2)]
    base = nearest_pi_halves(r, 3000, 1 << 20) + nearest_pi_halves(r, 1000, 1 << 62)
    xs += [ulpstep(b, d) for b in base for d in (-1, 0, 1)]
    for k in range(1, 5000):
        xs.append(float(k * gmpy2.const_pi() / 2))
    for e in range(-1074, 63):
        for d in (-1, 0, 1):
            xs.append(ulpstep(2.0 ** e if e >= -1074 else 5e-324, d))
    while len(xs) < N:
        c = r.random()
        if c < 0.5:
            xs.append(rnd_double(r, -1074, 62))
        elif c < 0.75:
            xs.append(r.uniform(-8, 8))
        elif c < 0.9:
            xs.append(r.uniform(-1e6, 1e6))
        else:
            xs.append(rnd_double(r, 0, 1023))      # beyond 2^63: the library function is defined for every finite x
    xs += [-x for x in xs[:len(xs) // 3]]
    return [x for x in xs if math.isfinite(x) and x != 0]


def gen_atan2(r, N):
    ps = []
    sp = [1.0, 2.0, 0.5, 3.0, 1e-300, 1e300, 5e-324, 1.7976931348623157e308, 2.0 ** -1022, 1 / 3]
    for y in sp:
        for x in sp:
            for sy in (1, -1):
                for sx in (1, -1):
                    ps.append((sy * y, sx * x))
    for j in range(0, 9):                          # y/x = j/8 exactly and just around
        for e in (-100, 0, 3, 500):
            x = 2.0 ** e * 1.2345678901234567
            for d in (-2, -1, 0, 1, 2):
                ps.append((ulpstep(x * j / 8, d), x)); ps.append((-ulpstep(x * j / 8, d), -x))
    while len(ps) < N:
        c = r.random()
        if c < 0.5:
            ps.append((rnd_double(r, -1074, 1023), rnd_double(r, -1074, 1023)))
        elif c < 0.8:
            e = r.randint(-1000, 1000); ps.append((rnd_double(r, e - 8, e + 8), rnd_double(r, e - 8, e + 8)))
        else:
            ps.append((r.uniform(-4, 4), r.uniform(-4, 4)))
    return [(y, x) for y, x in ps if math.isfinite(x) and math.isfinite(y) and x != 0 and y != 0]


def gen_2xm1(r, N):
    xs = [2.0 ** -1074, -2.0 ** -1074, 1e-300, 2.0 ** -54, 2.0 ** -53, 2.0 ** -52, 0.5, -0.5, 1.0, -1.0, 2.0, 3.0, 53.0, 54.0, 55.0, 62.0, 63.0, 64.0, 100.0, 1023.0,
          1023.9999999999999, 1024.0, -53.0, -54.0, -55.0, -60.0, -61.0, -1074.0, 0.3, -0.3, 10.5, -10.5, 1023.5]
    for k in range(-70, 70):
        for d in (-1, 0, 1):
            xs.append(ulpstep(float(k), d))
    for e in range(-1074, 11):
        for d in (-1, 0, 1):
            xs.append(ulpstep(2.0 ** e, d)); xs.append(-ulpstep(2.0 ** e, d))
    while len(xs) < N:
        c = r.random()
        if c < 0.5:
            xs.append(rnd_double(r, -1074, 10))
        elif c < 0.8:
            xs.append(r.uniform(-70, 70))
        else:
            xs.append(r.uniform(-1, 1) * 2.0 ** r.randint(-60, 0))
    return [x for x in xs if math.isfinite(x) and x != 0]


def gen_log(r, N, plus):
    ps = []
    ys = [1.0, -1.0, 3.0, 0.5, 1e300, -1e300, 5e-324, 1.7976931348623157e308, 1.4426950408889634, 12345.6789, 1e-300]
    xs = []
    if not plus:
        xs += [2.0, 4.0, 0.5, 3.0, 10.0, 1e-300, 5e-324, 1.7976931348623157e308, 2.0 ** -1022, 1.0000000000000002, 0.9999999999999999, 1 + 2.0 ** -52, 1 - 2.0 ** -53, 1.5, 0.75, 1.4142135623730951, 0.70710678118654757]
        for e in range(-1074, 1024, 7):
            for d in (-1, 0, 1):
                xs.append(ulpstep(2.0 ** e, d))
        for k in range(1, 60):
            xs += [1 + k * 2.0 ** -52, 1 - k * 2.0 ** -53]
    else:
        xs += [1.0, 3.0, 7.0, 2.0 ** 53 - 1, 2.0 ** 52 - 1, 2.0 ** 100 - 2.0 ** 47, 2.0 ** 1000 - 2.0 ** 947, 0.5, -0.5, -0.75, 2.0 ** -1074, 5e-324, 1e-300, 1.7976931348623157e308,
               -1 + 2.0 ** -53, -1 + 2.0 ** -52, -1 + 2.0 ** -1000 if False else -0.999999999999, 0.41421356237309515, -0.29289321881345243, 2.0 ** -52, -2.0 ** -53]
        for e in range(-1074, 1024, 5):
            for d in (-1, 0, 1):
                xs.append(ulpstep(2.0 ** e, d)); xs.append(-ulpstep(2.0 ** -(abs(e) % 60 + 1) , d))
        for k in range(1, 54):
            xs += [2.0 ** k - 1, 2.0 ** -k - 1 + 0.0 if False else -1 + 2.0 ** -k, 1 + 2.0 ** -k]
    for x in xs:
        for y in ys[:4] + [rnd_double(r, -60, 60)]:
            ps.append((y, x))
    while len(ps) < N:
        c = r.random()
        y = rnd_double(r, -80, 80) if r.random() < 0.9 else rnd_double(r, -1074, 1023)
        if plus:
            if c < 0.4:
                x = rnd_double(r, -1074, 1023)
            elif c < 0.7:
                x = r.uniform(-0.99, 4)
            elif c < 0.85:
                x = -1 + r.random() * 2.0 ** -r.randint(1, 60)
            else:
                x = r.uniform(-0.3, 0.42)
            if x <= -1:
                continue
        else:
            if c < 0.5:
                x = abs(rnd_double(r, -1074, 1023))
            elif c < 0.8:
                x = r.uniform(0.0001, 8)
            else:
                x = 1 + r.uniform(-1, 1) * 2.0 ** -r.randint(1, 50)
        ps.append((y, x))
    return [(y, x) for y, x in ps if math.isfinite(x) and math.isfinite(y) and y != 0 and x != 0 and (x > 0 if not plus else x > -1) and x != (1.0 if not plus else 0.0)]


def bound_check(args):
    """the claim |A - v| <= 2^-B |v| of every evaluator (the proof obligation of rt/fmath.c), against MPFR, for the first evaluation at n = nstart limbs"""
    which, pts = args
    if LIB is None:
        build()
    two = which in TWO
    opn = OPS[which]
    m = (ctypes.c_uint * 4096)(); info = (ctypes.c_int * 8)()
    LIB.dbg_eval.argtypes = [ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    bad = []; used = 0; worst = 0.0
    for i, p in enumerate(pts):
        a, b = (p if two else (p, 0.0))
        for ns in (3, 4, 5, 6, 9):
            if not LIB.dbg_eval(opn, a, b, ns, ctypes.addressof(m), ctypes.addressof(info)):
                continue
            n, e, sg, z, B, r = info[0], info[1], info[2], info[3], info[4], info[5]
            if r != 1 or z:
                continue
            v = ORACLE[which](*p) if two else ORACLE[which](p)
            if isinstance(v, Fraction):
                continue
            used += 1
            mi = 0
            for k in range(n - 1, -1, -1):
                mi = (mi << 32) | m[k]
            with prec(32 * n):
                A = gmpy2.mpfr(mi) * gmpy2.mpfr(2) ** (e - 32 * n) * (-1 if sg else 1)
                err = abs(A - v) * gmpy2.mpfr(2) ** B
                worst = max(worst, float(err / abs(v)))
                if err > abs(v):
                    bad.append((p, ns, B, float(err / abs(v))))
    return used, bad, worst


def gen_consts(N=40):
    c = gmpy2.get_context().copy(); c.precision = 32 * N + 200
    with c:
        out = {}
        for name, v, e in (('PI4_T', gmpy2.const_pi() / 4, 2), ('LN2_T', gmpy2.log(gmpy2.mpfr(2)), 0)):
            m = int(v * gmpy2.mpfr(2) ** (32 * N))
            out[name] = [(m >> (32 * i)) & 0xffffffff for i in range(N)]
    return out


def const_check():
    """the pi / ln 2 table of rt/fmath.c equals MPFR's, and both the table path and the series path (Machin, atanh) give the constant at every limb count
    n = 6..60 within the relative error the proofs assume (table: < 2^(1-32n); series: <= 2^(64-32n))"""
    import re
    src = open(RT + '/fmath.c').read()
    bad = []; n_ok = 0
    G = gen_consts()
    for name in ('PI4_T', 'LN2_T'):
        body = re.search(name + r'\[FM_TN\] = \{(.*?)\};', src, re.S).group(1)
        L = [int(x.strip().rstrip('u'), 16) for x in body.replace('\n', ' ').split(',') if x.strip()]
        if L != G[name]:
            bad.append(('table ' + name + ' differs from MPFR',))
    LIB.fm_dbg_const.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
    m = (ctypes.c_uint * 128)(); e = ctypes.c_int()
    c = gmpy2.get_context().copy(); c.precision = 2200
    with c:
        truth = [gmpy2.const_pi(), gmpy2.log(gmpy2.mpfr(2))]
        for n in range(6, 61):
            for which in (0, 1):
                for notable in (0, 1):
                    if notable == 0 and n > 40:
                        continue
                    LIB.fm_dbg_const(which, n, notable, ctypes.addressof(m), ctypes.addressof(e))
                    mi = 0
                    for k in range(n - 1, -1, -1):
                        mi = (mi << 32) | m[k]
                    val = gmpy2.mpfr(mi) * gmpy2.mpfr(2) ** (e.value - 32 * n)
                    rel = abs(val - truth[which]) / truth[which]
                    bound = gmpy2.mpfr(2) ** ((1 if notable == 0 else 64) - 32 * n)
                    n_ok += 1
                    if not rel < bound:
                        bad.append(('const', which, n, notable, float(rel / bound)))
    return n_ok, bad


def split(lst, k):
    return [lst[i::k] for i in range(k)]


def check_chunk(args):
    """-> list of (input, got, want) mismatches for one function chunk"""
    which, pts = args
    if LIB is None:
        build()
    two = which in TWO
    a = [p[0] for p in pts] if two else pts
    b = [p[1] for p in pts] if two else None
    o1, o2, fl, cf = run('m', which, a, b)
    orc = ORACLE[which]
    bad = []
    for i, p in enumerate(pts):
        v = orc(*p) if two else orc(p)
        want = rnd(v)
        got = float(o1[i])
        if u64_(got) != u64_(want):
            bad.append((p, got, want))
        elif int(fl[i]) & 0x23c != expect_flags(v, want):
            bad.append((p, got, want, 'flags %x want %x' % (int(fl[i]) & 0x23c, expect_flags(v, want))))
    return bad


# ------------------------------------------------------------------ host comparisons
def host_cmp(which, pairs, what, mask_sw=0x4700, flmask=0x5, check_res=True):
    """pairs: list of (a, b); compares result bits, C0/C1/C2/C3 and the exception bits in flmask vs the real x87. Returns mismatches."""
    a = [p[0] for p in pairs]; b = [p[1] for p in pairs]
    m1, m2, mfl, mcf = run('m', which, a, b)
    h1, h2, hfl, hsw = run('h', which, a, b)
    bad = []
    for i, p in enumerate(pairs):
        hs = int(hsw[i]) & 0xffff
        hc = hs & mask_sw
        mc = (int(mcf[i]) | (int(mfl[i]) & 0x200 if which == 'scale' else 0)) & mask_sw
        mx = int(mfl[i]) & flmask
        hx = hs & flmask
        r1m, r1h = float(m1[i]), float(h1[i])
        ok = u64_(r1m) == u64_(r1h) and mc == hc and mx == hx
        bad.append((p, r1m, r1h, mc, hc, mx, hx)) if not ok else None
    return bad


def fmtb(b):
    return ' '.join(fmtv(v) for v in b)


def fmtv(v):
    if isinstance(v, tuple):
        return '(' + ','.join(fmtv(w) for w in v) + ')'
    return float.hex(v) if isinstance(v, float) else hex(v)


def fmt(p): return '(' + ', '.join(float.hex(v) for v in p) + ')'


def cpu_id():
    d = {}
    for l in open('/proc/cpuinfo'):
        if ':' in l:
            k, v = [x.strip() for x in l.split(':', 1)]
            if k in ('vendor_id', 'cpu family', 'model', 'stepping', 'microcode', 'model name'):
                d.setdefault(k, v)
    return 'host cpu: %s family %s model %s stepping %s microcode %s (%s)' % (d.get('vendor_id'), d.get('cpu family'), d.get('model'), d.get('stepping'), d.get('microcode'), d.get('model name'))


SPEC = [0.0, -0.0, 1.0, -1.0, 0.5, -0.5, 2.0, -2.0, 3.0, math.inf, -math.inf, float('nan'), -float('nan'), f64_(0x7ff0000000000001), f64_(0xfff0000000000001), f64_(0x7ff8000000000123),
        f64_(0xfff8000000000456), 5e-324, -5e-324, 1e300, -1e300, 2.0 ** 63, -2.0 ** 63, 2.0 ** 63 - 1024.0, 2.0 ** 64, 2.0 ** 62, 1.7976931348623157e308, 2.0 ** -1022, 0.25, 1.5, 100.0, -100.0]


def host_specials():
    """operand classes whose result is exactly defined by the SDM tables: compare bit for bit (and C2 / IE / ZE) with the real x87"""
    tot = bad = 0
    out = []
    one = {'sin': lambda x: not math.isfinite(x) or x == 0 or abs(x) >= 2.0 ** 63, 'cos': lambda x: not math.isfinite(x) or x == 0 or abs(x) >= 2.0 ** 63,
           'tan': lambda x: not math.isfinite(x) or x == 0 or abs(x) >= 2.0 ** 63,
           '2xm1': lambda x: not math.isfinite(x) or x == 0, 'sincos': lambda x: not math.isfinite(x) or x == 0 or abs(x) >= 2.0 ** 63}
    for w, sel in one.items():
        pts = [x for x in SPEC if sel(x)]
        m1, m2, mfl, mcf = run('m', w, pts)
        h1, h2, hfl, hsw = run('h', w, pts)
        for i, x in enumerate(pts):
            tot += 1
            hs = int(hsw[i]) & 0xffff
            big = (hs & 0x400) != 0
            if w == '2xm1' and math.isfinite(x) is False and x == math.inf:
                pass
            # tan/fptan: ST0 = 1.0 pushed, ST1 = tan; for the big case the stack is unchanged; the host's st0 is then x
            if w == 'sincos' and not big:
                hres = None
                ok2 = u64_(float(m1[i])) == u64_(float(h1[i])) and u64_(float(m2[i])) == u64_(float(h2[i]))
                tot += 0
                if not ok2:
                    bad += 1; out.append('sincos %s: got cos %s sin %s host cos %s sin %s' % (float.hex(x), float.hex(float(m1[i])), float.hex(float(m2[i])), float.hex(float(h1[i])), float.hex(float(h2[i]))))
                continue
            if w == 'tan':
                hres = float(h2[i]) if not big else None
            else:
                hres = float(h1[i]) if True else None
            mres = float(m1[i])
            c2m = int(mcf[i]) & 0x400
            c2h = hs & 0x400
            ie_m = int(mfl[i]) & 1; ie_h = hs & 1
            ok = c2m == c2h and ie_m == ie_h
            if not big and hres is not None:
                ok = ok and u64_(mres) == u64_(hres)
            if not ok:
                bad += 1; out.append('%s %s: got %s C2=%d IE=%d host %s C2=%d IE=%d' % (w, float.hex(x), float.hex(mres), c2m >> 10, ie_m, float.hex(hres) if hres is not None else '-', c2h >> 10, ie_h))
    two = {'atan2': lambda y, x: True, 'yl2x': lambda y, x: True, 'yl2xp1': lambda y, x: True, 'scale': lambda y, x: True}
    for w in two:
        pts = []
        for y in SPEC:
            for x in SPEC:
                if w == 'scale':
                    # scale: x finite non-special operands go through the exact path; compare everything (it is exact)
                    pts.append((y, x))
                    continue
                if w == 'yl2xp1' and x <= -1.0:
                    continue                      # outside the instruction's domain: the real x87 leaves garbage; fmath gives the invalid result
                spec = (not math.isfinite(y)) or (not math.isfinite(x)) or y == 0 or x == 0 or (w != 'atan2' and x == 1.0) or False or (w == 'yl2x' and x < 0)
                if spec:
                    pts.append((y, x))
        m1, m2, mfl, mcf = run('m', w, [p[0] for p in pts], [p[1] for p in pts])
        h1, h2, hfl, hsw = run('h', w, [p[0] for p in pts], [p[1] for p in pts])
        for i, (y, x) in enumerate(pts):
            tot += 1
            hs = int(hsw[i]) & 0xffff
            mres, hres = float(m1[i]), float(h1[i])
            fm = int(mfl[i]); mask = 0x1 | (0x4 if w in ('yl2x', 'yl2xp1') else 0)
            ok = u64_(mres) == u64_(hres) and (fm & mask) == (hs & mask)
            if not ok:
                bad += 1; out.append('%s y=%s x=%s: got %s fl=%x host %s sw=%x' % (w, float.hex(y), float.hex(x), float.hex(mres), fm, float.hex(hres), hs))
    return tot, bad, out


def host_exact(r, nper):
    res = {}
    rr = random.Random(r)
    # fprem / fprem1 over D = -3..2100
    for w in ('fprem', 'fprem1'):
        pts = []
        for D in range(-3, 2098):
            for _ in range(nper):
                lo, hi = max(-1074, -1074 - D), min(1023, 1023 - D)
                if hi < lo:
                    continue
                eb = rr.randint(lo, hi)
                pts.append((lead(rr, eb + D), lead(rr, eb)))
        # exact ties / boundaries for fprem1: a = b * (k + 1/2)
        for _ in range(4000):
            b = rnd_double(rr, -200, 200); k = rr.randint(0, 2 ** 20)
            pts.append((b * (k + 0.5) if rr.random() < 0.5 else -b * (k + 0.5), b))
            pts.append((b * k, b)); pts.append((b * k * (1 + 2.0 ** -52), b)); pts.append((rnd_double(rr, -1074, -1060), rnd_double(rr, -1074, -1060)))
        for a in SPEC:
            for b in SPEC:
                pts.append((a, b))
        res[w] = (len(pts), host_cmp(w, pts, w))
    # fscale: ST1 = e (any double) truncated
    pts = []
    for _ in range(200000):
        x = rnd_double(rr, -1074, 1023)
        c = rr.random()
        if c < 0.5: e = float(rr.randint(-2200, 2200))
        elif c < 0.7: e = rr.uniform(-2200, 2200)
        elif c < 0.85: e = rnd_double(rr, -60, 80)
        else: e = rnd_double(rr, -1074, 1023)
        pts.append((x, e))
    for x in SPEC:
        for e in SPEC + [-1074.0, -1075.0, 1024.0, 1023.0, -1022.5, 17.9, -17.9, 0.9999, -0.9999]:
            pts.append((x, e))
    res['scale'] = (len(pts), host_cmp('scale', pts, 'scale', mask_sw=0x4500, flmask=0x1))
    # fscale exception bits and C1 against the exact rational value
    badf = []
    for x, e in pts:
        if not (math.isfinite(x) and math.isfinite(e)) or x == 0:
            continue
        t = int(e)                                          # trunc toward zero
        t = max(-3000, min(3000, t))
        v = Fraction(x) * (Fraction(2) ** t)
        want = rnd(v)
        got, _, fl, _ = run('m', 'scale', [x], [e])
        if u64_(float(got[0])) != u64_(want) or int(fl[0]) & 0x23c != expect_flags(v, want):
            badf.append((x, e, float(got[0]), want, hex(int(fl[0]) & 0x23c), hex(expect_flags(v, want))))
    res['scale flags'] = (len(pts), badf)
    # fxtract
    xs = [rnd_double(rr, -1074, 1023) for _ in range(100000)] + SPEC
    m1, m2, mfl, mcf = run('m', 'xtract', xs)
    h1, h2, hfl, hsw = run('h', 'xtract', xs)
    bad = []
    for i, x in enumerate(xs):
        # host o1 = exponent (ST1, popped second -> stored in r2); see run_h: fstpl %1 first (significand on top) then %0
        if not (u64_(float(m1[i])) == u64_(float(h1[i])) and u64_(float(m2[i])) == u64_(float(h2[i]))) or (int(mfl[i]) & 4) != (int(hsw[i]) & 4) or (int(mfl[i]) & 1) != (int(hsw[i]) & 1):
            bad.append((x, float(m1[i]), float(m2[i]), float(h1[i]), float(h2[i])))
    res['xtract'] = (len(xs), bad)
    return res



NEWF = ('acos', 'asin', 'atan', 'cosh', 'sinh', 'tanh', 'exp', 'log', 'pow')
TWO = ('atan2', 'yl2x', 'yl2xp1', 'pow')
LN2F = None


def ln2m():
    with prec(0): return gmpy2.log(gmpy2.mpfr(2))


def gen_exp(r, N):
    L = ln2m()
    xs = [1.0, -1.0, 0.5, -0.5, 2.0 ** -54, 2.0 ** -55, -2.0 ** -55, -2.0 ** -54, 709.782712893384, 710.0, 709.0, -745.1332191019411, -745.13321910194122, -746.0, -745.0, 700.0, -700.0,
          1e-300, 5e-324, -5e-324, float(L), -float(L), 1e5, -1e5, 1e300, -1e300, 800.0, -800.0, 2.0, 10.0, -10.0, 100.5]
    with prec(0):
        for k in range(-1080, 1030):
            for kk in (k, k + 0.5):
                b = float(kk * L)
                xs += [ulpstep(b, d) for d in (-1, 0, 1)]
    for e in range(-1074, 11):
        for d in (-1, 0, 1):
            xs.append(ulpstep(2.0 ** e, d)); xs.append(-ulpstep(2.0 ** e, d))
    for k in range(-750, 711):
        xs.append(float(k))
    xs += [ulpstep(709.782712893384, d) for d in range(-4, 5)] + [ulpstep(-745.13321910194122, d) for d in range(-4, 5)]
    while len(xs) < N:
        c = r.random()
        if c < 0.4: xs.append(rnd_double(r, -1074, 9))
        elif c < 0.8: xs.append(r.uniform(-750, 712))
        elif c < 0.9: xs.append(r.uniform(-2, 2))
        else: xs.append(r.uniform(-1, 1) * 2.0 ** r.randint(-60, 0))
    return [x for x in xs if math.isfinite(x) and x != 0]


def gen_ln(r, N):
    xs = [2.0, 4.0, 0.5, 3.0, 10.0, 1e-300, 5e-324, 1.7976931348623157e308, 2.0 ** -1022, 1.0000000000000002, 0.9999999999999999, 1.5, 0.75, 1.4142135623730951, 0.7071067811865476, 2.718281828459045, 1e10]
    for e in range(-1074, 1024):
        for d in (-1, 0, 1):
            xs.append(ulpstep(2.0 ** e, d)); xs.append(ulpstep(1.5 * 2.0 ** e, d)); xs.append(ulpstep(0.75 * 2.0 ** e, d))
    for k in range(1, 200):
        xs += [1 + k * 2.0 ** -52, 1 - k * 2.0 ** -53]
    while len(xs) < N:
        c = r.random()
        if c < 0.5: xs.append(abs(rnd_double(r, -1074, 1023)))
        elif c < 0.7: xs.append(r.uniform(0.0001, 8))
        elif c < 0.9: xs.append(1 + r.uniform(-1, 1) * 2.0 ** -r.randint(1, 52))
        else: xs.append(abs(rnd_double(r, -60, 60)))
    return [x for x in xs if math.isfinite(x) and x > 0 and x != 1.0]


def gen_sinh(r, N, kind):
    """kind: 'sinh' / 'cosh' (reduction range up to 711) or 'tanh' (up to 20)"""
    L = ln2m()
    hi = 20.0 if kind == 'tanh' else 711.0
    xs = [1.0, 0.5, 2.0 ** -27, 2.0 ** -26, 2.0 ** -28, 2.0 ** -25, 5e-324, 1e-300, 709.0, 710.0, 710.4758600739439, 711.0, 20.0, 19.0, 19.06, 21.0, 40.0, 1e5, 1e300, 0.3466, 0.34657359027997264]
    for e in range(-1074, 11):
        for d in (-1, 0, 1):
            xs.append(ulpstep(2.0 ** e, d))
    with prec(0):
        for k in range(0, 1025 if kind != 'tanh' else 60):
            b = float((k + 0.5) * L / (2 if kind == 'tanh' else 1))
            xs += [ulpstep(b, d) for d in (-1, 0, 1)]
    xs += [ulpstep(710.4758600739439, d) for d in range(-4, 5)] + [ulpstep(19.06, d) for d in range(-4, 5)] + [ulpstep(20.0, d) for d in range(-4, 5)] + [ulpstep(2.0 ** -27, d) for d in range(-4, 5)] + [ulpstep(2.0 ** -26, d) for d in range(-4, 5)]
    xs = xs + [-x for x in xs]
    while len(xs) < N:
        c = r.random()
        if c < 0.4: xs.append(rnd_double(r, -1074, 4 if kind == 'tanh' else 9))
        elif c < 0.8: xs.append(r.uniform(-hi - 2, hi + 2))
        elif c < 0.9: xs.append(r.uniform(-1, 1))
        else: xs.append(r.uniform(-1, 1) * 2.0 ** r.randint(-40, 0))
    return [x for x in xs if math.isfinite(x) and x != 0]


def gen_atan(r, N):
    xs = [1.0, 0.5, 2.0, 1e-300, 5e-324, 1e300, 1.7976931348623157e308, 2.0 ** -27, 2.0 ** -26, 2.0 ** 54, 2.0 ** 53, 0.125, 0.0625, 8.0, 16.0]
    for j in range(0, 9):
        for d in range(-3, 4):
            b = (2 * j + 1) / 16.0
            xs += [ulpstep(b, d), ulpstep(1 / b, d), ulpstep(j / 8.0 if j else 2.0 ** -30, d), ulpstep(8.0 / j if j else 1e30, d)]
    for e in range(-1074, 1024):
        for d in (-1, 0, 1):
            xs.append(ulpstep(2.0 ** e, d))
    xs += [ulpstep(2.0 ** -27, d) for d in range(-4, 5)]
    xs = xs + [-x for x in xs]
    while len(xs) < N:
        c = r.random()
        if c < 0.5: xs.append(rnd_double(r, -1074, 1023))
        elif c < 0.8: xs.append(r.uniform(-4, 4))
        else: xs.append(r.uniform(-1, 1) * 2.0 ** r.randint(-30, 70))
    return [x for x in xs if math.isfinite(x) and x != 0]


def gen_asin(r, N):
    xs = [1.0, 0.5, 0.7071067811865476, 0.25, 0.75, 2.0 ** -27, 2.0 ** -26, 5e-324, 1e-300, 1 - 2.0 ** -53, 1 - 2.0 ** -52, 0.9999999999999999, 0.0625, 0.125, 0.9, 0.99, 0.999999]
    for e in range(-1074, 0):
        for d in (-1, 0, 1):
            xs.append(ulpstep(2.0 ** e, d))
    for k in range(1, 400):
        xs += [1 - k * 2.0 ** -53, 1 - k * 2.0 ** -52, 0.5 - k * 2.0 ** -54, 0.5 + k * 2.0 ** -53, 0.7071067811865476 + (k - 200) * 2.0 ** -53]
    xs += [ulpstep(2.0 ** -27, d) for d in range(-4, 5)]
    xs = xs + [-x for x in xs]
    while len(xs) < N:
        c = r.random()
        if c < 0.4: xs.append(rnd_double(r, -1074, -1))
        elif c < 0.7: xs.append(r.uniform(-1, 1))
        elif c < 0.85: xs.append(1 - r.random() * 2.0 ** -r.randint(1, 52))
        else: xs.append(-1 + r.random() * 2.0 ** -r.randint(1, 52))
    return [x for x in xs if math.isfinite(x) and abs(x) <= 1.0 and x != 0]


def isqrt_exact(n):
    s = math.isqrt(n)
    return s if s * s == n else None


def gen_pow(r, N):
    ps = []
    # exact families: x = r^(2^j) 2^(e) with 2^j | e (all other (x, y) pairs are irrational / non-dyadic / beyond the lattice)
    for _ in range(N // 5):
        j = r.randint(0, 5)
        m = r.randrange(1, 1 << max(1, 53 // (1 << j)), 2)
        mj = m ** (1 << j)
        if mj >= 1 << 53: continue
        e = (1 << j) * r.randint(-1074 // (1 << j) + 1, 1023 // (1 << j) - 1)
        try: x = math.ldexp(float(mj), e)
        except OverflowError: continue
        if x == 0 or not math.isfinite(x): continue
        a = r.choice([1, 2, 3, 4, 5, 7, 9, 13, 33, 34, 35, 40, 1, 2, 3, -1, -2, -3, -5, 0.5, 1.5, 2 ** 20 + 1]) if j == 0 else r.choice([1, 3, 5, 7, 9, 11, 33, 35, -1, -3, -7, 2 ** 20 + 1, 17])
        y = a / (1 << j)
        ps.append((x, y)); ps.append((-x if r.random() < 0.5 else x, float(r.randint(-40, 40))))
    # half-way points: r^a has 53 / 54 / 55 bits (r odd, x = r^(2^j) 2^e with 2^j | e, y = a / 2^j)
    for _ in range(N // 20):
        j = r.randint(0, 3); rr = r.randrange(3, 1 << r.randint(2, 26 // (1 << j) + 3), 2)
        if rr ** (1 << j) >= 1 << 53: continue
        a = 1
        while (rr ** (a + 1)).bit_length() <= 53 + r.randint(0, 2): a += 1
        e = (1 << j) * r.randint(-1074 // (1 << j) + 1, 1023 // (1 << j) - 1 - 53 // (1 << j))
        try: x = math.ldexp(float(rr ** (1 << j)), e)
        except OverflowError: continue
        if x == 0: continue
        ps.append((x, float(a) if j == 0 else (a | 1) / (1 << j)))
    # powers of two: x = 2^e, y with e y an integer or not
    for _ in range(N // 10):
        e = r.randint(-1074, 1023)
        if e == 0: continue
        y = r.choice([r.randint(-2200, 2200), r.randint(-2200, 2200) / 2.0, r.randint(-2200, 2200) / 4.0, r.randint(-2200, 2200) / 1024.0, r.randint(-60, 60) / 2.0 ** r.randint(1, 20),
                      float(r.randint(1, 2 ** 20)) * 2.0 ** r.randint(-20, 0), 2.0 ** r.randint(-1074, 60), 1.0 / e * r.randint(-3000, 3000)])
        if y != 0: ps.append((2.0 ** e, y))
    # near-exact: neighbours of exact cases
    base = ps[:N // 8]
    for x, y in base:
        for d in (-2, -1, 1, 2): ps.append((ulpstep(x, d), y)); ps.append((x, ulpstep(y, d)))
    # small integer / half-integer exponents of arbitrary x (squares, cubes, sqrt ...): exact when the mantissa is short
    for _ in range(N // 8):
        x = abs(rnd_double(r, -1074, 1023)) if r.random() < 0.5 else float(r.randint(2, 1 << r.randint(2, 53))) * 2.0 ** r.randint(-300, 300)
        y = r.choice([2.0, 3.0, 0.5, 1.5, -1.0, -2.0, 4.0, 10.0, 7.0, 0.25, 0.75, 1.0 / 3, -0.5, 33.0, 34.0, 35.0])
        ps.append((x, y))
    # generic: y ln x moderate
    while len(ps) < N:
        c = r.random()
        x = abs(rnd_double(r, -1074, 1023)) if r.random() < 0.5 else 2.0 ** r.uniform(-60, 60)
        if x == 1.0 or not math.isfinite(x) or x == 0: continue
        if c < 0.35:
            y = r.uniform(-760, 760) / math.log(x) if abs(math.log(x)) > 1e-3 else r.uniform(-30, 30)
        elif c < 0.55: y = rnd_double(r, -60, 12)
        elif c < 0.7:
            x = 1 + r.uniform(-1, 1) * 2.0 ** -r.randint(1, 52); y = rnd_double(r, -20, 62)
            if x == 1.0: continue
        elif c < 0.8: y = float(r.randint(-60, 60))
        elif c < 0.9:
            x = -x; y = float(r.randint(-80, 80)) if r.random() < 0.7 else r.uniform(-30, 30) * 2.0 ** r.randint(0, 40)
            y = float(round(y))
        else: y = rnd_double(r, -1074, 1023)
        if y == 0 or not math.isfinite(y) or not math.isfinite(x): continue
        ps.append((x, y))
    out = []
    for x, y in ps:
        if not (math.isfinite(x) and math.isfinite(y)) or x == 0 or y == 0 or abs(x) == 1.0: continue
        if x < 0 and y != math.floor(y): continue
        out.append((x, y))
    return out[:max(N, 0)] if len(out) >= N else out


GENS_NEW = {'acos': gen_asin, 'asin': gen_asin, 'atan': gen_atan, 'exp': gen_exp, 'log': gen_ln, 'cosh': lambda r, N: gen_sinh(r, N, 'cosh'), 'sinh': lambda r, N: gen_sinh(r, N, 'sinh'),
            'tanh': lambda r, N: gen_sinh(r, N, 'tanh'), 'pow': gen_pow}


def gen_fmod(r, N):
    ps = []
    for _ in range(N):
        c = r.random()
        if c < 0.3: x, y = rnd_double(r, -1074, 1023), rnd_double(r, -1074, 1023)
        elif c < 0.6:
            e = r.randint(-1074, 1000); x, y = lead(r, max(-1074, e + r.randint(-3, 80))), lead(r, e)
        elif c < 0.75:
            y = rnd_double(r, -1074, 1000); k = r.randint(0, 1 << r.randint(1, 50)); x = f64_(u64_(y) & ~(1 << 63)) * k
            x = x if math.isfinite(x) else 1.0
            ps.append((r.choice([-1, 1]) * x, y)); continue
        elif c < 0.85:
            y = rnd_double(r, -1074, 600); k = r.randint(0, 1 << 20); x = (k + 0.5) * y; ps.append((x, y)); continue
        else: x, y = rnd_double(r, -1074, -1000), rnd_double(r, -1074, -1000)
        ps.append((x, y))
    return ps


def gen_sqrt(r, N):
    xs = []
    for _ in range(N):
        c = r.random()
        if c < 0.5: xs.append(abs(rnd_double(r, -1074, 1023)))
        elif c < 0.7:
            m = r.randint(1, 1 << 26); xs.append(math.ldexp(float(m * m), 2 * r.randint(-500, 450)))        # exact squares
        elif c < 0.85:
            m = r.randint(1, 1 << 26); xs.append(ulpstep(math.ldexp(float(m * m), 2 * r.randint(-500, 450)), r.choice([-1, 1])))
        else: xs.append(abs(rnd_double(r, -1074, -1000)))
    xs += [5e-324, 2.0 ** -1022, 1.7976931348623157e308, 2.0, 4.0, 1.0, 1.0000000000000002, 0.9999999999999999]
    return xs


def c99_pow(x, y):
    """pow of C99 Annex F for the special operands (zero, infinity, NaN, +-1), written from the standard's list"""
    nan = float('nan'); inf = math.inf
    if y == 0 or x == 1.0: return 1.0
    if math.isnan(x) or math.isnan(y): return nan
    yint = math.isfinite(y) and y == math.floor(y)
    yodd = yint and abs(y) < 2.0 ** 53 and int(y) % 2 == 1
    if math.isinf(y):
        if abs(x) == 1.0: return 1.0
        return 0.0 if (abs(x) < 1.0) == (y > 0) else inf
    if x == 0:
        neg = math.copysign(1.0, x) < 0 and yodd
        return (-0.0 if neg else 0.0) if y > 0 else (-inf if neg else inf)
    if math.isinf(x):
        if x > 0: return inf if y > 0 else 0.0
        neg = yodd
        return (-inf if neg else inf) if y > 0 else (-0.0 if neg else 0.0)
    if x < 0 and not yint: return nan
    if abs(x) == 1.0: return -1.0 if (x < 0 and yodd) else 1.0
    return None


def new_specials():
    """special operands (zero, infinity, NaN, out of domain, exact boundary values) of the new functions: C99 Annex F values vs numpy (the host libm); NaN-ness, ZE and IE"""
    np.seterr(all='ignore')
    NP = {'acos': np.arccos, 'asin': np.arcsin, 'atan': np.arctan, 'cosh': np.cosh, 'sinh': np.sinh, 'tanh': np.tanh, 'exp': np.exp, 'log': np.log, 'sqrt': np.sqrt}
    S2 = SPEC + [1.0, -1.0, 1e300, -1e300, 746.0, -746.0, 710.0, -710.0, 2.0 ** -1074, 4.0, -4.0, 0.5, 3.0, -3.0, 2.0 ** 52, 2.0 ** 53 + 2, 1e-300, -2.0 ** 60, 0.25]
    tot = 0; out = []
    for w, f in NP.items():
        pts = []
        for x in S2:
            if not math.isfinite(x) or x == 0 or abs(x) >= 1e300 or (w in ('acos', 'asin') and abs(x) >= 1) or (w == 'log' and x in (1.0,)) or (w in ('cosh', 'sinh', 'tanh', 'exp') and abs(x) >= 20) \
               or (w == 'sqrt' and (x < 0 or x in (4.0, 1.0, 0.25, 2.0 ** 52))) or (w == 'log' and x < 0) or (w == 'atan' and abs(x) >= 1e300):
                pts.append(x)
        m1, m2, mfl, mcf = run('m', w, pts)
        for i, x in enumerate(pts):
            tot += 1
            want = float(f(np.float64(x)))
            got = float(m1[i])
            ok = (math.isnan(got) and math.isnan(want)) or (u64_(got) == u64_(want))
            if w in ('cosh', 'sinh', 'tanh', 'exp', 'atan') and math.isfinite(x) and abs(x) < 1e300 and x != 0 and abs(x) < 20:
                ok = True
            nn = not math.isnan(x)
            ie = int(mfl[i]) & 1
            if math.isnan(got) and nn and not ie: ok = False                       # invalid result from numbers: IE
            if w == 'log' and x == 0 and not (int(mfl[i]) & 4): ok = False
            if not ok: out.append('%s %s: got %s fl=%x want %s' % (w, float.hex(x), float.hex(got), int(mfl[i]), float.hex(want)))
    for w, f in (('pow', c99_pow), ('fmod', lambda a, b: float(np.fmod(a, b)))):
        pts = []
        for y in S2:
            for x in S2:
                sp = any((not math.isfinite(v)) or v == 0 for v in (x, y)) or abs(x) == 1.0 or (w == 'pow' and y in (1.0, -1.0, 0.5) and x in (0.25, 4.0, 1.0))
                if sp and not (w == 'pow' and (abs(x) >= 1e300 or abs(y) >= 1e300) and math.isfinite(x) and math.isfinite(y)): pts.append((x, y))
        m1, m2, mfl, mcf = run('m', w, [p[0] for p in pts], [p[1] for p in pts])
        for i, (x, y) in enumerate(pts):
            tot += 1
            want = f(x, y) if w == 'pow' else float(f(x, y))
            if want is None: continue
            got = float(m1[i])
            ok = (math.isnan(got) and math.isnan(want)) or (u64_(got) == u64_(want))
            nonan = not (math.isnan(x) or math.isnan(y))
            if math.isnan(got) and nonan and not (int(mfl[i]) & 1): ok = False
            if w == 'pow' and x == 0 and y < 0 and math.isfinite(y) and not (int(mfl[i]) & 4): ok = False
            if w == 'pow' and want is None: continue
            if not ok: out.append('%s x=%s y=%s: got %s fl=%x want %s' % (w, float.hex(x), float.hex(y), float.hex(got), int(mfl[i]), float.hex(want)))
    return tot, len(out), out


def host_fmod_sqrt(seed, n):
    r = random.Random(seed + 7)
    res = {}
    pts = gen_fmod(r, n)
    for a in SPEC:
        for b in SPEC: pts.append((a, b))
    res['fmod'] = (len(pts), host_cmp('fmod', pts, 'fmod', mask_sw=0, flmask=0))
    xs = gen_sqrt(r, n) + SPEC + [-x for x in gen_sqrt(r, 2000)]
    res['sqrt'] = (len(xs), host_cmp('sqrt', [(x, 0.0) for x in xs], 'sqrt', mask_sw=0, flmask=0))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=200000)
    ap.add_argument('--only', default='')
    ap.add_argument('--seed', type=int, default=20261111)
    ap.add_argument('--nper', type=int, default=40)
    ap.add_argument('--jobs', type=int, default=2)
    ap.add_argument('--gen-consts', action='store_true', help='print the pi/4, ln2 limb tables of rt/fmath.c')
    o = ap.parse_args()
    if o.gen_consts:
        for k, L in gen_consts().items():
            print('static const u32 %s[FM_TN] = {\n%s\n};' % (k, '\n'.join('  ' + ', '.join('0x%08xu' % x for x in L[i:i + 6]) + ',' for i in range(0, len(L), 6))))
        return
    t0 = time.time()
    build()
    print(cpu_id())
    print('oracle: gmpy2 %s / MPFR %s, 400 bits, rounded once to binary64 (exact cases: Fractions)' % (gmpy2.version(), gmpy2.mp_version()))
    fns = [f for f in ORACLE if not o.only or f in o.only.split(',')]
    r = random.Random(o.seed)
    gens = {'sin': lambda: gen_trig(r, o.n), 'cos': lambda: gen_trig(r, o.n), 'tan': lambda: gen_trig(r, o.n), 'atan2': lambda: gen_atan2(r, o.n), '2xm1': lambda: gen_2xm1(r, o.n),
            'yl2x': lambda: gen_log(r, o.n, False), 'yl2xp1': lambda: gen_log(r, o.n, True)}
    for k, g in GENS_NEW.items(): gens[k] = (lambda g=g: g(r, o.n))
    fail = 0
    pool = mp.Pool(o.jobs)
    for f in fns:
        pts = gens[f]()
        chunks = [(f, c) for c in [pts[i:i + 4000] for i in range(0, len(pts), 4000)]]
        bad = [b for part in pool.map(check_chunk, chunks) for b in part]
        print('%-7s inputs %7d  equal %7d  different %d' % (f, len(pts), len(pts) - len(bad), len(bad)), flush=True)
        for b in bad[:5]:
            print('   DIFF', fmt(b[0]) if isinstance(b[0], tuple) else float.hex(b[0]), 'got', float.hex(b[1]), 'want', float.hex(b[2]), *b[3:])
        fail += len(bad)
    nc, badc = const_check()
    print('constants pi, ln2: table text and %d (constant, limb count, path) values vs MPFR, violated %d' % (nc, len(badc)))
    for b in badc[:5]:
        print('   CONST', b)
    fail += len(badc)
    # the error bounds claimed by the evaluators (the proof obligation), checked on a deterministic sample at n = 3, 4, 5, 6, 9 limbs
    for f in fns:
        pts = gens[f]()
        r2 = random.Random(o.seed + 1); smp = r2.sample(pts, min(len(pts), 6000))
        res = pool.map(bound_check, [(f, smp[i::4]) for i in range(4)])
        used = sum(x[0] for x in res); bad = [b for x in res for b in x[1]]
        print('%-7s claimed error bound 2^-B vs MPFR: %6d evaluations, violated %d (worst observed error / claimed bound: %.3g)' % (f, used, len(bad), max(x[2] for x in res)))
        for b in bad[:5]:
            print('   BOUND', b)
        fail += len(bad)
    pool.close()
    tot, bad, out = host_specials()
    print('special operands vs the real x87: %d cases, different %d' % (tot, bad))
    for l in out[:12]:
        print('   DIFF', l)
    fail += bad
    if not o.only or any(k in o.only for k in NEWF):
        tot, nbad, out = new_specials()
        print('msvcrt functions, special operands vs the host libm (numpy): %d cases, different %d' % (tot, nbad))
        for l in out[:12]: print('   DIFF', l)
        fail += nbad
    if not o.only or any(k in o.only for k in ('fmod', 'sqrt')):
        for w, (n, bad) in host_fmod_sqrt(o.seed, o.n).items():
            print('%-7s vs host CPU bit for bit (result): %7d cases, equal %7d, different %d' % (w, n, n - len(bad), len(bad)))
            for b in bad[:6]: print('   DIFF', fmtb(b))
            fail += len(bad)
    if not o.only or any(k in o.only for k in ('fprem', 'scale', 'xtract')):
        res = host_exact(o.seed, o.nper)
        for w, (n, bad) in res.items():
            print('%-7s %s: %7d cases, equal %7d, different %d' % (w.split()[0], 'vs the exact rational value (result bits, PE OE UE C1)' if 'flags' in w else 'vs host x87 bit for bit (result, C0-C3, IE)', n, n - len(bad), len(bad)))
            for b in bad[:6]:
                print('   DIFF', fmtb(b))
            fail += len(bad)
    print('%s in %.0f s' % ('FAIL' if fail else 'PASS', time.time() - t0))
    sys.exit(1 if fail else 0)


if __name__ == '__main__':
    main()
