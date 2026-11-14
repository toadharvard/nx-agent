#!/usr/bin/env python3
"""Generator of the value test of the float -> integer rule (toolchain/ftoi/fnvnx_ftoi.cc), for the L4 boot machine to run (no run happens here).
Output: a C++ driver. It calls each fixture function of fixture_neon.cpp (the plugin's class: target builtins, vector and scalar conversions, the lrint
families) and of fixture_sse2neon.cpp (the four sse2neon forms through the real fnvnx_compat.h) with lane inputs and checks the bytes it gets against the x86
results computed here, exactly (Fraction arithmetic), from the rule alone: the conversion of the exact value in the rounding named, when it fits the integer
type, else the integer indefinite (bit P-1 set; the unsigned lanes the same pattern). Current-mode families run once per FPCR rounding mode (set from the
driver), so the mode the rule must not assume is the mode of the run. The driver prints one line per failing case and returns the number of failures
(0 = all cases equal x86).
usage: gen_values.py neon|sse2neon OUT.cpp"""
import math
import struct
import sys
from fractions import Fraction

F32 = [0.0, -0.0, 0.5, -0.5, 1.5, -1.5, 2.5, -2.5, 0.49999997, 1.9, -1.9, 16777216.0, 1e-45, 2147483520.0, 2147483648.0, -2147483648.0,
       -2147483904.0, 3e9, -3e9, 1e30, -1e30, float('inf'), float('-inf'), float('nan')]
F64 = [0.0, 0.5, -0.5, 1.5, -2.5, 4503599627370495.5, 2147483647.5, 2147483647.0, 2147483648.0, -2147483648.0, -2147483648.5, -2147483649.0,
       9223372036854774784.0, 9223372036854775808.0, -9223372036854775808.0, -9223372036854777856.0, 1e19, -1e19, 1e300, float('inf'),
       float('-inf'), float('nan')]
# the four sse2neon forms: the boundaries of int32, int16 and int8 as well, in every lane
S2N = [0.0, -0.0, 0.5, -0.5, 1.5, 2.5, -1.5, -2.5, 0.49999997, 32766.5, 32767.0, 32767.5, 32768.0, -32768.0, -32768.5, -32769.0, 127.0, 127.5, 128.0, -128.0,
       -128.5, -129.0, 1e5, -1e5, 8388607.5, 16777216.0, 2147483520.0, 2147483648.0, -2147483648.0, -2147483904.0, 3e9, -3e9, 1e30, -1e30, float('inf'),
       float('-inf'), float('nan')]
FPCR = {'even': 0, 'ceil': 1 << 22, 'floor': 2 << 22, 'trunc': 3 << 22}   # FPCR.RMode


def rnd(x, mode):
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    f = Fraction(x)
    return {'trunc': lambda: math.trunc(f), 'floor': lambda: math.floor(f), 'ceil': lambda: math.ceil(f), 'even': lambda: round(f),
            'away': lambda: (1 if f >= 0 else -1) * math.floor(abs(f) + Fraction(1, 2))}[mode]()


def oracle(x, mode, bits, uns=False, shift=0):
    """x86's result of the conversion of x * 2^shift, rounding MODE, to BITS bits: the rounded integer when it fits, else the indefinite (bit BITS-1 set)"""
    y = x * (1 << shift) if (math.isnan(x) or math.isinf(x)) else Fraction(x) * (1 << shift)
    r = rnd(y, mode) if isinstance(y, Fraction) else None
    lo, hi = (0, (1 << bits) - 1) if uns else (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
    return r if r is not None and lo <= r <= hi else (1 << (bits - 1))


def sat(v, bits):
    return max(-(1 << (bits - 1)), min((1 << (bits - 1)) - 1, v))


def lane_sets(vals, n):
    """each value in each lane against NaN and 7.5 neighbours, and a rotating mix: every lane sees every value"""
    out = []
    for v in vals:
        for p in range(n):
            for filler in (float('nan'), 7.5):
                out.append([v if i == p else filler for i in range(n)])
    for k in range(len(vals)):
        out.append([vals[(k + i) % len(vals)] for i in range(n)])
    return out


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def lanes(vals, bits, fl):
    """little-endian bytes of the lane values (floats if FL, else integers modulo 2^BITS)"""
    if fl:
        return b''.join(struct.pack('<f' if bits == 32 else '<d', v) for v in vals)
    return b''.join((v & ((1 << bits) - 1)).to_bytes(bits // 8, 'little') for v in vals)


# (function, argument C type, float lanes, float bits, result C type, integer lanes, integer bits, rounding, unsigned, shift, FPCR mode names to run)
def neon_cases():
    V4, V2, V4s, V2d = 'float32x4_t', 'float32x2_t', None, 'float64x2_t'
    E = []
    def v(fn, a, n, fb, r, ib, mode, uns=0, shift=0):
        E.append((fn, a, n, fb, r, n, ib, mode, uns, shift, ['even']))
    for fn, mode in (('tr4', 'trunc'), ('nr4', 'even'), ('aw4', 'away'), ('fl4', 'floor'), ('ce4', 'ceil')):
        v(fn, V4, 4, 32, 'int32x4_t', 32, mode)
    for fn, mode in (('utr4', 'trunc'), ('unr4', 'even'), ('uaw4', 'away'), ('ufl4', 'floor'), ('uce4', 'ceil')):
        v(fn, V4, 4, 32, 'uint32x4_t', 32, mode, 1)
    v('tr2', V2, 2, 32, 'int32x2_t', 32, 'trunc'); v('nr2', V2, 2, 32, 'int32x2_t', 32, 'even'); v('utr2', V2, 2, 32, 'uint32x2_t', 32, 'trunc', 1)
    for fn, mode in (('trd', 'trunc'), ('nrd', 'even'), ('awd', 'away'), ('fld', 'floor'), ('ced', 'ceil')):
        v(fn, V2d, 2, 64, 'int64x2_t', 64, mode)
    for fn, mode in (('utrd', 'trunc'), ('unrd', 'even'), ('uawd', 'away'), ('ufld', 'floor'), ('uced', 'ceil')):
        v(fn, V2d, 2, 64, 'uint64x2_t', 64, mode, 1)
    v('fx4', V4, 4, 32, 'int32x4_t', 32, 'trunc', 0, 8); v('ufx4', V4, 4, 32, 'uint32x4_t', 32, 'trunc', 1, 5); v('fxd', V2d, 2, 64, 'int64x2_t', 64, 'trunc', 0, 10)
    v('cv4', V4, 4, 32, 'int32x4_t', 32, 'trunc'); v('ucv4', V4, 4, 32, 'uint32x4_t', 32, 'trunc', 1)
    v('cvd', V2d, 2, 64, 'int64x2_t', 64, 'trunc'); v('ucvd', V2d, 2, 64, 'uint64x2_t', 64, 'trunc', 1)
    def sc(fn, a, fb, r, ib, mode, uns=0, shift=0, fp=('even',)):
        E.append((fn, a, 1, fb, r, 1, ib, mode, uns, shift, list(fp)))
    sc('tr1', 'float', 32, 'int', 32, 'trunc'); sc('nr1', 'float', 32, 'int', 32, 'even'); sc('utr1', 'float', 32, 'unsigned', 32, 'msvc')
    sc('trd1', 'double', 64, 'long long', 64, 'trunc'); sc('nrd1', 'double', 64, 'long long', 64, 'even'); sc('utrd1', 'double', 64, 'unsigned long long', 64, 'msvc')
    sc('fx1', 'float', 32, 'int', 32, 'trunc', 0, 4); sc('ufx1', 'float', 32, 'unsigned', 32, 'trunc', 1, 7); sc('fxd1', 'double', 64, 'long long', 64, 'trunc', 0, 12)
    sc('fs64', 'float', 32, 'long long', 64, 'trunc'); sc('fd32', 'double', 64, 'int', 32, 'trunc')
    sc('lcd', 'double', 64, 'long', 32, 'ceil'); sc('lfd', 'double', 64, 'long', 32, 'floor'); sc('lrdn', 'double', 64, 'long', 32, 'away'); sc('llrf', 'float', 32, 'long long', 64, 'away')
    m4 = ('even', 'ceil', 'floor', 'trunc')
    for fn, a, fb, r, ib in (('lrf', 'float', 32, 'long', 32), ('lrd', 'double', 64, 'long', 32), ('llrd', 'double', 64, 'long long', 64), ('ird', 'double', 64, 'int', 32)):
        sc(fn, a, fb, r, ib, 'current', 0, 0, m4)
    return E


def s2n_cases():
    E = []
    for fn in ('ps2pi', 'ps_pi32'):
        E.append((fn, '__m128', 4, 32, '__m64', 2, 32, 'current', 0, 0, ['even', 'ceil', 'floor', 'trunc']))
    E.append(('ps_pi16', '__m128', 4, 32, '__m64', 4, 16, 'current16', 0, 0, ['even', 'ceil', 'floor', 'trunc']))
    E.append(('ps_pi8', '__m128', 4, 32, '__m64', 8, 8, 'current8', 0, 0, ['even', 'ceil', 'floor', 'trunc']))
    for fn in ('ss2si', 'ss_si32'):
        E.append((fn, '__m128', 4, 32, 'int', 1, 32, 'current', 0, 0, ['even', 'ceil', 'floor', 'trunc']))
    return E


def expect(e, ins, fmode):
    fn, a, nin, fb, r, nout, ib, mode, uns, shift, _ = e
    xs = ins[:nout] if mode not in ('current16', 'current8') else ins[:4]
    if mode == 'msvc':   # a plain scalar unsigned cast is MSVC's (x87 int64, then the low bits)
        return [(oracle(x, 'trunc', 64) if not math.isnan(x) else -(1 << 63)) & ((1 << ib) - 1) for x in xs]
    md = fmode if mode.startswith('current') else mode
    if mode == 'current16':
        return [sat(oracle(x, md, 32), 16) for x in xs]
    if mode == 'current8':
        return [sat(sat(oracle(x, md, 32), 16), 8) for x in xs] + [0] * 4
    return [oracle(x, md, ib, bool(uns), shift) for x in xs]


def driver(kind):
    cases = neon_cases() if kind == 'neon' else s2n_cases()
    vals = {32: [f32(x) for x in (F32 if kind == 'neon' else S2N)], 64: F64}
    out = ['// generated by gen_values.py %s: the value test of the float -> integer rule; run on the boot machine, returns the number of failing cases' % kind,
           '#include <string.h>', '#include <stdio.h>']
    out.append('#include <arm_neon.h>' if kind == 'neon' else '#include <fnvnx_compat.h>')
    for e in cases:
        out.append('extern "C" %s %s(%s%s);' % (e[4], e[0], e[1], ', int32x4_t' if e[0] == 'mp3' else ''))
    out.append('struct Case { const char *fn; unsigned fpcr; unsigned char in[16]; unsigned char want[16]; unsigned nwant; };')
    out.append('static const Case cases[] = {')
    for e in cases:
        fn, a, nin, fb, r, nout, ib, mode, uns, shift, modes = e
        n = nin if fb == 32 and a != 'float32x2_t' and a not in ('float', 'double') else (2 if a == 'float32x2_t' else (1 if a in ('float', 'double') else nin))
        n = {'float32x4_t': 4, 'float32x2_t': 2, 'float64x2_t': 2, 'float': 1, 'double': 1, '__m128': 4}[a]
        for fm in modes:
            for ins in lane_sets(vals[fb], n):
                want = expect(e, ins, fm)
                nb = len(want) * (ib // 8) if mode != 'current8' else 8
                wb = lanes(want, 8, False) if mode == 'current8' else lanes(want, ib, False)
                ib_bytes = lanes(ins, fb, True)
                out.append('  {"%s", %du, {%s}, {%s}, %d},' % (fn, FPCR[fm], ','.join(str(b) for b in ib_bytes.ljust(16, b'\0')),
                                                            ','.join(str(b) for b in wb.ljust(16, b'\0')), nb))
    out.append('};')
    out.append('static unsigned char *call(const char *fn, const unsigned char *in, unsigned char *res) {')
    for e in cases:
        fn, a, nin, fb, r, nout, ib, mode, uns, shift, modes = e
        out.append('  if (!strcmp(fn, "%s")) { %s a; memcpy(&a, in, sizeof a); %s r = %s(a); memcpy(res, &r, sizeof r); return res; }' % (fn, a, r, fn))
    out.append('  return 0;\n}')
    out.append('int main() {\n  unsigned bad = 0;\n  for (const Case &c : cases) {\n    unsigned char res[16] = {0};\n'
               '    __builtin_aarch64_set_fpcr(c.fpcr);\n    call(c.fn, c.in, res);\n    __builtin_aarch64_set_fpcr(0);\n'
               '    if (memcmp(res, c.want, c.nwant)) { bad++; printf("FAIL %s fpcr %08x in", c.fn, c.fpcr); for (int i = 0; i < 16; i++) printf(" %02x", c.in[i]); printf("\\n"); }\n'
               '  }\n  printf("%u failing of %u cases\\n", bad, (unsigned)(sizeof cases / sizeof *cases));\n  return bad ? 1 : 0;\n}')
    return '\n'.join(out) + '\n'


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('neon', 'sse2neon'):
        sys.exit(__doc__)
    open(sys.argv[2], 'w').write(driver(sys.argv[1]))
