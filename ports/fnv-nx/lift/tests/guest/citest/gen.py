#!/usr/bin/env python3
"""citest generator (a `generated` stage of the lift test root): the expected value of every case of the 15 msvcrt _CI* entries, computed with the MPFR oracle
(tools/mpfr_oracle.py; operand order of _CIatan2 / _CIpow / _CIfmod ST1 first, correctly rounded vs MPFR, exact vs integer arithmetic, the x87 exception bits, the errno rule
of rt/msvcrt_ci.cc) and written as the guest test's table. Writes ONLY /out/cases.inc (the table main.cc includes) and /out/labels.txt (one line per case, the number a failing
guest exit code names).
usage: gen.py <out dir>"""
import math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, '..', '..', '..', 'tools'),): sys.path.insert(0, p)
import mpfr_oracle as O_
import gmpy2
from fractions import Fraction

ENT = 'acos asin atan atan2 cos cosh exp fmod log pow sin sinh sqrt tan tanh'.split()
TWO = ('atan2', 'fmod', 'pow')
# ------------------------------------------------------------------ expectations
def b(x): return O_.u64_(x)
def f(u): return O_.f64_(u)
IND = 0xFFF8000000000000


def fmod_exact(x, y):
    q = Fraction(x) / Fraction(y); t = int(q)                                  # trunc toward zero
    r = Fraction(x) - t * Fraction(y)
    return math.copysign(0.0, x) if r == 0 else float(r)


def expect(name, ops):
    """-> (value bits or the string 'nan', value for the flag check or None). ops in x87 order bottom -> top (the operands of the call)"""
    O = O_.ORACLE
    if name == 'atan2': v = O['atan2'](ops[0], ops[1])
    elif name == 'pow': v = O['pow'](ops[0], ops[1])
    elif name == 'fmod': return b(fmod_exact(ops[0], ops[1])), None
    elif name == 'sqrt':
        with O_.prec(0): v = +gmpy2.sqrt(O_.mf(ops[0]))
    else: v = O[name](ops[0])
    return b(O_.rnd(v)), v


# (entry, operands bottom->top, result bits | None = NaN, errno, note): special / domain cases with hand-given results
INF = math.inf
SPECIAL = [
    ('acos', [2.0], IND, 33), ('acos', [-1.5], IND, 33), ('acos', [INF], IND, 33), ('acos', [1.0], 0, 7), ('acos', [-1.0], b(O_.rnd(+gmpy2.const_pi())), 7),
    ('asin', [1.0000001], IND, 33), ('asin', [-INF], IND, 33), ('asin', [0.0], 0, 7), ('asin', [float('nan')], None, 33), ('acos', [float('nan')], None, 7),
    ('atan', [float('nan')], None, 33), ('atan', [INF], b(O_.rnd(+gmpy2.const_pi() / 2)), 7), ('atan', [-0.0], 1 << 63, 7),
    ('log', [-1.0], IND, 33), ('log', [0.0], b(-INF), 34), ('log', [-0.0], b(-INF), 34), ('log', [1.0], 0, 7), ('log', [INF], b(INF), 7), ('log', [-INF], IND, 33), ('log', [float('nan')], None, 7),
    ('exp', [1000.0], b(INF), 34), ('exp', [-1000.0], 0, 34), ('exp', [0.0], b(1.0), 7), ('exp', [-INF], 0, 7), ('exp', [INF], b(INF), 7), ('exp', [float('nan')], None, 33),
    ('cosh', [1000.0], b(INF), 34), ('cosh', [-INF], b(INF), 7), ('cosh', [0.0], b(1.0), 7), ('sinh', [-1000.0], b(-INF), 34), ('sinh', [INF], b(INF), 7), ('sinh', [-0.0], 1 << 63, 7),
    ('tanh', [30.0], b(1.0), 7), ('tanh', [-INF], b(-1.0), 7), ('tanh', [float('nan')], None, 33),
    ('sqrt', [-4.0], IND, 33), ('sqrt', [-0.0], 1 << 63, 7), ('sqrt', [INF], b(INF), 7), ('sqrt', [-INF], IND, 33), ('sqrt', [float('nan')], None, 33), ('sqrt', [0.0], 0, 7),
    ('sin', [INF], IND, 33), ('cos', [-INF], IND, 33), ('tan', [INF], IND, 33), ('sin', [float('nan')], None, 7), ('sin', [0.0], 0, 7), ('cos', [0.0], b(1.0), 7),
    ('atan2', [1e-300, 1e300], 0, 34), ('atan2', [0.0, -1.0], b(O_.rnd(+gmpy2.const_pi())), 7), ('atan2', [0.0, 0.0], 0, 7), ('atan2', [-INF, INF], b(O_.rnd(-gmpy2.const_pi() / 4)), 7),
    ('atan2', [float('nan'), 1.0], None, 7),
    ('pow', [-8.0, 1.0 / 3], IND, 33), ('pow', [0.0, -1.0], b(INF), 34), ('pow', [-0.0, -3.0], b(-INF), 34), ('pow', [10.0, 400.0], b(INF), 34), ('pow', [10.0, -400.0], 0, 34), ('pow', [2.0, 0.0], b(1.0), 7),
    ('pow', [1.0, float('nan')], b(1.0), 7), ('pow', [float('nan'), 0.0], b(1.0), 7), ('pow', [float('nan'), 2.0], None, 7), ('pow', [-2.0, 3.0], b(-8.0), 7), ('pow', [-2.0, 2.0], b(4.0), 7),
    ('pow', [-INF, 3.0], b(-INF), 7), ('pow', [0.0, INF], 0, 7), ('pow', [0.0, -INF], b(INF), 7), ('pow', [-1.0, INF], b(1.0), 7), ('pow', [3.0, 34.0], b(float(3 ** 34 + 1)) if False else b(O_.rnd(Fraction(3 ** 34))), 7),
    ('fmod', [5.0, 0.0], IND, 33), ('fmod', [INF, 2.0], IND, 33), ('fmod', [5.0, INF], b(5.0), 7), ('fmod', [-0.0, 3.0], 1 << 63, 7), ('fmod', [float('nan'), 2.0], None, 7), ('fmod', [-6.0, 3.0], 1 << 63, 7),
]
VALS1 = {'acos': [0.5, -0.25, 0.9999999999999999, 1e-300], 'asin': [0.5, -0.75, 1.0, 1e-10], 'atan': [1.0, -3.5, 1e300, 1e-300], 'cos': [1.0, 1e22, 0.5],
         'cosh': [1.0, -20.5, 709.5], 'exp': [1.0, -1.5, 709.0, -745.0], 'log': [2.0, 0.1, 1e300, 5e-324], 'sin': [1.0, -2.5, 1e22], 'sinh': [1.0, -0.5, 700.0], 'sqrt': [2.0, 1e-310, 9.0, 1.7976931348623157e308],
         'tan': [1.0, 1.5707963267948966, -100.0], 'tanh': [0.5, -1.0, 3.0]}
VALS2 = {'atan2': [(1.0, 2.0), (2.0, 1.0), (-3.0, -4.0), (1e-5, -7.0)], 'fmod': [(10.0, 3.0), (3.0, 10.0), (-7.5, 2.0), (1e300, 3.0), (5.0, 0.3)], 'pow': [(2.0, 10.0), (10.0, 2.0), (2.0, 0.5), (1.1, 100.0), (-3.0, 5.0), (7.0, -0.5)]}
SENT = [777.0, 888.0]


def main():
    out = sys.argv[1]
    cases = []                      # (entry, vals, expected bits or None(NaN), errno, flagval, label)
    for n in ENT:
        ops = [[x] for x in VALS1[n]] if n not in TWO else [list(p) for p in VALS2[n]]
        for o in ops:
            bits, v = expect(n, o)
            for under in (SENT, [], SENT[:1]):                     # two / zero / one entries beneath the operands
                cases.append((n, under + o, bits, None, v, 'value %s%s depth+%d' % (n, o, len(under))))
        o = [VALS2[n][0][0], VALS2[n][0][1]] if n in TWO else [VALS1[n][0]]
        bits, v = expect(n, o)
        cases.append((n, [float(i + 100) for i in range(8 - len(o))] + o, bits, None, v, '8-deep %s' % n))        # full stack: pop before push
    for n, o, bits, err in SPECIAL:
        cases.append((n, SENT + o, bits, err, None, 'special %s%s' % (n, o)))
    rows = []
    for n, vals, bits, err, v, label in cases:
        nop = 2 if n in TWO else 1
        under = len(vals) - nop
        flags = O_.expect_flags(v, O_.f64_(bits)) if err is None and v is not None else 0xFFFFFFFF      # none: the flags are not checked
        vs = [O_.u64_(x) for x in vals] + [0] * (8 - len(vals))
        rows.append('  { %d, %d, %d, {%s}, %s, %d, 0x%xu, %d },' % (ENT.index(n), len(vals), under, ', '.join('0x%016xull' % x for x in vs),
                     'NAN_BITS' if bits is None else '0x%016xull' % bits, -1 if err is None else err, flags, 1 if bits == IND else 0))
    open(os.path.join(out, 'cases.inc'), 'w').write('#define NCASES %d\n%s\n' % (len(cases), '\n'.join(rows)))
    open(os.path.join(out, 'labels.txt'), 'w').write(''.join('%d\t%s\n' % (i + 1, c[5]) for i, c in enumerate(cases)))


if __name__ == '__main__':
    main()
