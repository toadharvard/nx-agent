#!/usr/bin/env python3
"""ctrans.fpflags / fpflags_cvt (the FPSR cumulative bits of an FP operation) against a literal transcription of the Arm pseudocode over exact rationals: FPUnpackBase, FPProcessNaNs, FPAdd,
FPMul, FPDiv, FPSqrt, FPCompare, FPRoundBase (non-altfp), FPConvert, FixedToFP, FPToFixed.  Source: Arm A-profile A64 ISA XML 2023-09 (ISA_A64_xml_A_profile-2023-09.tar.gz sha256 7f2092a1416fa1f8b784e96fbc7cdd67b2ce81f84426968279694f96f1b11596),
shared_pseudocode.xml.  The Python below is the pseudocode, line by line, with Fraction as `real`.  The cases are operand edge classes crossed with every rounding mode, plus a seeded random set; the
z3 terms are evaluated on each.  (The pseudocode is the reference; the terms are the class rule of ctrans.py "the cumulative exception bits of the FPSR".)"""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os, random, sys, unittest
from fractions import Fraction
import z3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..')]
import fnvnx.lift.proof.ctrans as ctrans  # noqa: E402

IOC, DZC, OFC, UFC, IXC = 1, 2, 4, 8, 16
RN, RP, RM, RZ = 0, 1, 2, 3          # FPCR.RMode encoding
FMT = {64: (11, 52, -1022), 32: (8, 23, -126)}      # E, F, minimum_exp (FPBits)


class Fl:
    def __init__(s): s.v = 0
    def exc(s, b): s.v |= b


def unpack(bits, N):      # FPUnpackBase, FPCR.FZ = 0
    E, F, mn = FMT[N]
    sign = bits >> (N - 1); ex = (bits >> F) & ((1 << E) - 1); fr = bits & ((1 << F) - 1)
    if ex == 0:
        if fr == 0: t, v = 'zero', Fraction(0)
        else: t, v = 'denorm', Fraction(2) ** mn * Fraction(fr, 1 << F)
    elif ex == (1 << E) - 1:
        if fr == 0: t, v = 'inf', Fraction(0)
        else: t, v = ('qnan' if fr >> (F - 1) else 'snan'), Fraction(0)
    else:
        t, v = 'norm', Fraction(2) ** (ex - ((1 << (E - 1)) - 1)) * (1 + Fraction(fr, 1 << F))
    return t, sign, (-v if sign else v)


def rounddown(x): return x.numerator // x.denominator


def fpround(op, rounding, N, fl):      # FPRoundBase, AH = 0, FZ = 0, UFE = 0
    E, F, mn = FMT[N]
    assert op != 0
    sign = 1 if op < 0 else 0; mant = -op if op < 0 else op
    exponent = 0
    while mant < 1: mant *= 2; exponent -= 1
    while mant >= 2: mant /= 2; exponent += 1
    biased = max(exponent - mn + 1, 0)
    if biased == 0: mant = mant / Fraction(2) ** (mn - exponent)
    int_mant = rounddown(mant * 2 ** F); error = mant * 2 ** F - int_mant
    if biased == 0 and error != 0: fl.exc(UFC)
    if rounding == RN: up = error > Fraction(1, 2) or (error == Fraction(1, 2) and int_mant & 1)
    elif rounding == RP: up = error != 0 and sign == 0
    elif rounding == RM: up = error != 0 and sign == 1
    else: up = False
    if up:
        int_mant += 1
        if int_mant == 2 ** F: biased = 1
        if int_mant == 2 ** (F + 1): biased += 1; int_mant //= 2
    if biased >= 2 ** E - 1: fl.exc(OFC); error = 1
    if error != 0: fl.exc(IXC)


def process_nans(t1, t2, fl):
    if t1 == 'snan' or t2 == 'snan': fl.exc(IOC)
    return t1 in ('snan', 'qnan') or t2 in ('snan', 'qnan')


def ref_arith(kind, a, b, N, rm):
    fl = Fl(); t1, s1, v1 = unpack(a, N); t2, s2, v2 = unpack(b, N)
    if process_nans(t1, t2, fl): return fl.v
    inf1, inf2, z1, z2 = t1 == 'inf', t2 == 'inf', t1 == 'zero', t2 == 'zero'
    if kind == 'sub': s2 ^= 1; v2 = -v2
    if kind in ('add', 'sub'):
        if inf1 and inf2 and s1 == (s2 ^ 1): fl.exc(IOC)
        elif inf1 or inf2: pass
        elif z1 and z2 and s1 == s2: pass
        else:
            r = v1 + v2
            if r != 0: fpround(r, rm, N, fl)
    elif kind == 'mul':
        if (inf1 and z2) or (z1 and inf2): fl.exc(IOC)
        elif inf1 or inf2 or z1 or z2: pass
        else: fpround(v1 * v2, rm, N, fl)
    else:
        if (inf1 and inf2) or (z1 and z2): fl.exc(IOC)
        elif inf1 or z2:
            if not inf1: fl.exc(DZC)
        elif z1 or inf2: pass
        else: fpround(v1 / v2, rm, N, fl)
    return fl.v


def ref_sqrt(a, N):
    fl = Fl(); t, s, v = unpack(a, N)
    if t in ('snan', 'qnan'):
        if t == 'snan': fl.exc(IOC)
    elif t == 'zero' or (t == 'inf' and s == 0): pass
    elif s == 1: fl.exc(IOC)
    else:       # FPRound(Sqrt(value)): a finite sqrt neither overflows nor underflows; it is exact iff value is a rational square
        n, d = v.numerator, v.denominator
        from math import isqrt
        if not (isqrt(n) ** 2 == n and isqrt(d) ** 2 == d): fl.exc(IXC)
    return fl.v


def ref_cmp(a, b, N, signal):
    fl = Fl(); t1 = unpack(a, N)[0]; t2 = unpack(b, N)[0]
    if t1 in ('snan', 'qnan') or t2 in ('snan', 'qnan'):
        if t1 == 'snan' or t2 == 'snan' or signal: fl.exc(IOC)
    return fl.v


def ref_cvt_ff(a, N, M, rm):      # FPConvert
    fl = Fl(); t, s, v = unpack(a, N)
    if t in ('snan', 'qnan'):
        if t == 'snan': fl.exc(IOC)
    elif t in ('inf', 'zero'): pass
    else: fpround(v, rm, M, fl)
    return fl.v


def ref_fixed_to_fp(x, M, signed, N, rm):      # FixedToFP
    fl = Fl()
    if x: fpround(Fraction(x), rm, N, fl)
    return fl.v


def ref_to_fixed(a, N):      # FPToFixed rounding toward zero, a value in range
    fl = Fl(); t, s, v = unpack(a, N)
    iv = rounddown(v)
    err = v - iv
    if err != 0: fl.exc(IXC)
    return fl.v


def interesting(N):
    E, F, mn = FMT[N]
    out = set()
    for sign in (0, 1):
        for ex in (0, 1, 2, (1 << E) - 2, (1 << E) - 1, (1 << (E - 1)) - 1, (1 << (E - 1)), (1 << (E - 1)) - 2, 2 * F // 3, 1 << (E - 1) + 1 if False else 5):
            for fr in (0, 1, 2, (1 << F) - 1, 1 << (F - 1), (1 << (F - 1)) + 1, (1 << (F - 1)) - 1, 0x5555555555555 & ((1 << F) - 1)):
                out.add((sign << (N - 1)) | (ex << F) | fr)
    return sorted(out)


def term_val(e): return z3.simplify(e).as_long()


class T(unittest.TestCase):
    def cases(self, N, n=250):
        rnd = random.Random(0x4152 + N); base = interesting(N)
        pairs = [(a, b) for a in base[::3] for b in base[1::3]]
        pairs += [(rnd.choice(base), rnd.getrandbits(N)) for _ in range(n)] + [(rnd.getrandbits(N), rnd.getrandbits(N)) for _ in range(n)]
        return pairs

    def test_add_sub_mul_div(self):
        for N in (64, 32):
            t = ('f', N)
            for a, b in self.cases(N):
                for rm in (RN, RP, RM, RZ):
                    for kind in ('add', 'sub', 'mul', 'div'):
                        got = term_val(ctrans.fpflags(kind, [z3.BitVecVal(a, N), z3.BitVecVal(b, N)], t, z3.BitVecVal(rm, 2)))
                        self.assertEqual(got, ref_arith(kind, a, b, N, rm), (N, kind, hex(a), hex(b), rm))

    def test_sqrt(self):
        for N in (64, 32):
            for a, _ in self.cases(N, 80):
                got = term_val(ctrans.fpflags('sqrt', [z3.BitVecVal(a, N)], ('f', N), z3.BitVecVal(0, 2)))
                self.assertEqual(got, ref_sqrt(a, N), (N, hex(a)))
        for x in (4.0, 2.0, 0.25, 1e-300, 5e-324, 1.7976931348623157e308):
            import struct
            a = struct.unpack('<Q', struct.pack('<d', x))[0]
            self.assertEqual(term_val(ctrans.fpflags('sqrt', [z3.BitVecVal(a, 64)], ('f', 64), z3.BitVecVal(0, 2))), ref_sqrt(a, 64), x)

    def test_compares(self):
        for N in (64, 32):
            for a, b in self.cases(N, 150):
                for kind, sig in (('cmp', False), ('cmpe', True)):
                    got = term_val(ctrans.fpflags(kind, [z3.BitVecVal(a, N), z3.BitVecVal(b, N)], ('f', N)))
                    self.assertEqual(got, ref_cmp(a, b, N, sig), (N, kind, hex(a), hex(b)))

    def test_conversions(self):
        rnd = random.Random(7)
        for a, _ in self.cases(64, 150):
            for rm in (RN, RP, RM, RZ):
                self.assertEqual(term_val(ctrans.fpflags_cvt(('f', 64), ('f', 32), z3.BitVecVal(a, 64), z3.BitVecVal(rm, 2))), ref_cvt_ff(a, 64, 32, rm), (hex(a), rm))
        for a, _ in self.cases(32, 150):
            self.assertEqual(term_val(ctrans.fpflags_cvt(('f', 32), ('f', 64), z3.BitVecVal(a, 32), z3.BitVecVal(0, 2))), ref_cvt_ff(a, 32, 64, 0), hex(a))
        xs = [0, 1, -1, 2 ** 53, 2 ** 53 + 1, 2 ** 63 - 1, -2 ** 63, 2 ** 24 + 1, 123456789] + [rnd.getrandbits(63) - (1 << 62) for _ in range(60)]
        for x in xs:
            for rm in (RN, RP, RM, RZ):
                for N in (64, 32):
                    got = term_val(ctrans.fpflags_cvt(('i', 64, 1), ('f', N), z3.BitVecVal(x & (2 ** 64 - 1), 64), z3.BitVecVal(rm, 2)))
                    self.assertEqual(got, ref_fixed_to_fp(x, 64, 1, N, rm), (x, rm, N))
        for a, _ in self.cases(64, 100):
            t, s, v = unpack(a, 64)
            if t in ('norm', 'denorm', 'zero') and abs(v) < 2 ** 62:       # in range: the C obligation excludes NaN and overflow
                self.assertEqual(term_val(ctrans.fpflags_cvt(('f', 64), ('i', 64, 1), z3.BitVecVal(a, 64))), ref_to_fixed(a, 64), hex(a))

    def test_idc_is_never_set(self):
        """FPUnpackBase raises InputDenorm only when FPCR.FZ flushes the input; FZ = 0 (the obligation of every FP op), so bit 7 is 0 on every input, denormals included"""
        for a in (1, 0x000FFFFFFFFFFFFF, 0x8000000000000001):
            for kind in ('add', 'mul', 'div'):
                self.assertEqual(term_val(ctrans.fpflags(kind, [z3.BitVecVal(a, 64)] * 2, ('f', 64), z3.BitVecVal(0, 2))) & 0x80, 0)


import fnvnx.lift.proof.x87carrier as x87carrier  # noqa: E402
try:
    C = x87carrier.carrier(); NOINPUT = None
except x87carrier.NoInput as e:
    C, NOINPUT = None, str(e)


def valid(f):
    s = z3.Solver(); s.set('rlimit', 50_000_000); s.add(z3.Not(f)); return s.check() == z3.unsat


@unittest.skipIf(NOINPUT, NOINPUT or '')
class State(unittest.TestCase):
    """the host FPSR / FPCR as state of the executor (asm MRS / MSR mapped by ctrans.asm_sysreg) and the carrier functions that read or write them"""
    def run_fn(self, fn):
        m = C['machine']; a, cells = m.inputs(fn); m.G.pop('c', None); return a, m.apply(fn, a)

    def test_the_fpsr_functions_translate(self):
        for fn in ('fnvl_fp_x87_flags', 'fnvl_fp_x87_clear', 'fnvl_fp_set_rc_x87', 'fx_live', 'fx_esb', 'fx_flags_clear', 'fown_set', 'fnvl_x87_init'):
            self.assertIn(fn, C['translated'], (fn, C['refused'].get(fn)))

    def test_clear_writes_the_x87_cumulative_bits(self):
        a, r = self.run_fn('fnvl_fp_x87_clear')
        self.assertTrue(valid(r.fpsr == (ctrans.FPSR_IN & z3.BitVecVal(~0x9f & (2 ** 64 - 1), 64))))      # IOC DZC OFC UFC IXC (bits 0-4) and IDC (bit 7) are zero, nothing else changes

    def test_flags_read_the_fpsr(self):
        a, r = self.run_fn('fnvl_fp_x87_flags')
        f = ctrans.FPSR_IN
        bit = lambda i: z3.ZeroExt(31, z3.Extract(i, i, f))
        # the x87 flag word: IE <- IOC, ZE <- DZC, OE <- OFC, UE <- UFC, PE <- IXC (FSW bits 0..5; SDM 1:8.1.3.2); DE is never read from the host (IDC is not raised with FPCR.FZ = 0): rt/fnvl_x87.h fx_de
        want = bit(0) | (bit(1) << 2) | (bit(2) << 3) | (bit(3) << 4) | (bit(4) << 5)
        self.assertTrue(valid(r.ret.e == want))
        self.assertTrue(valid(r.fpsr == f))

    def test_set_rc_is_the_arm_encoding_of_the_x87_mode(self):
        a, r = self.run_fn('fnvl_fp_set_rc_x87')
        rc = a[0].e
        # SDM 1:8.1.5 x87 RC: 0 nearest, 1 down, 2 up, 3 toward zero; Arm ARM FPCR.RMode: 0 RN, 1 RP (up), 2 RM (down), 3 RZ
        arm = z3.If(rc & 3 == 0, z3.BitVecVal(0, 2), z3.If(rc & 3 == 1, z3.BitVecVal(2, 2), z3.If(rc & 3 == 2, z3.BitVecVal(1, 2), z3.BitVecVal(3, 2))))
        self.assertTrue(valid(z3.Extract(23, 22, r.fpcr) == arm))
        self.assertTrue(valid(z3.Concat(z3.Extract(63, 24, r.fpcr), z3.Extract(21, 0, r.fpcr)) == z3.Concat(z3.Extract(63, 24, ctrans.FPCR_INIT), z3.Extract(21, 0, ctrans.FPCR_INIT))))

    def test_fp_op_raises_by_the_pseudocode(self):
        a, r = self.run_fn('fx_round')      # frintn / frintm / frintp / frintz by rc: IOC for a signalling NaN only (FPRoundInt, exact = FALSE)
        v = a[0].e
        self.assertTrue(valid(r.fpsr == (ctrans.FPSR_IN | z3.If(ctrans.snan_(v, ctrans.F64), z3.BitVecVal(1, 64), z3.BitVecVal(0, 64)))))

    def test_live_flags_follow_the_owner(self):
        a, r = self.run_fn('fx_live')
        c = C['machine'].G['c']
        self.assertTrue(valid(r.fpsr == ctrans.FPSR_IN))


if __name__ == '__main__':
    unittest.main()
