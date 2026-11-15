"""SSE / SSE2 floating-point arithmetic as z3 terms: add, sub, mul, div, min, max, sqrt on one binary32 / binary64 lane, with the MXCSR (rounding control, DAZ, FTZ,
the six exception flags).  The rows of simdspec.py map these over the lanes of ps / pd and the low lane of ss / sd.

Source (acl2/acl2 3ccb342c): books/rtl/rel11/lib/excps.lisp  sse-binary-spec / sse-binary-pre-comp(-excp, -val) / sse-binary-comp / sse-post-comp / sse-sqrt-spec / ...-pre-comp,
and books/projects/x86isa/machine/instructions/fp/arith-spec.lisp  sse-max/min(-special); the cites are in simdspec.py (acl2_cites.tsv).  The ACL2 text is a case analysis over the
operand classes, the exception flags and the rounded value of the unrounded real result.  Here the same case analysis is over the same classes; the VALUE of the rounded result is
z3's IEEE operation under the rounding mode of MXCSR.RC (OWNER 8: plain IEEE), and the three facts ACL2 gets from the unrounded real result are derived from one exact comparison:
  * after-rounding-with-unbounded-exponent magnitude (the tiny / overflow tests of sse-post-comp: `(rnd u rmode (prec f))`) = the same operation in the sort with 3 more exponent bits
    (and the same significand width): the operands convert to it exactly, and no finite result overflows it;
  * inexact (`(= r u)`) = the result rounded toward minus infinity differs from the one rounded toward plus infinity (equal exactly when u is representable);
  * the denormal rounding `drnd` = the narrow operation itself (z3 rounds into the subnormal range).
The masked-response branches only: the exception masks are all set at every SSE instruction, MXCSR bits 12..7 = 1 (environment row `masks_set`, the one assumption ASM-204 = I-FPMASK of
verify/ASSUMPTIONS.json, removed by L3a's fpstate mask census); an unmasked exception (ACL2 returns NIL, the instruction faults) has no row here: the precondition excludes it.
DAZ: whether MXCSR bit 6 can be set is a property of the reference CPU, the MXCSR_MASK of CPUREF (lift/rt/cpumodel.c, FNVL_CPUREF_MXCSR_MASK; SDM 1:11.6.6).  MXCSR is only written by
ldmxcsr / fxrstor, which fault on a bit outside that mask, so the environment row `mxcsr_in_mask` is (MXCSR & ~mask) == 0, with the mask READ from the file.  A file without it makes every
row of this module `Uncertified`: no constant stands in for it.
"""
import z3
from z3 import And, Concat, Extract, If, Not, Or, BitVecVal, ZeroExt

FMT = {32: (8, 24), 64: (11, 53)}          # (exponent bits, significand bits incl. the implicit one): binary32, binary64 (IEEE 754)
LPN = {32: 0x7F7FFFFF, 64: 0x7FEFFFFFFFFFFFFF}
SPN = {32: 0x00800000, 64: 0x0010000000000000}
OPS = ('add', 'sub', 'mul', 'div')


def K(v, w): return BitVecVal(v, w)


import os, re


class Uncertified(KeyError):
    """a row that cannot be certified because a pinned fact it reads is missing (simdcheck reports it as 'uncertified: <reason>')"""


_CPUMODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'rt', 'cpumodel.c')
_TEST_MASK = []                  # test harness only (tests/test_simdspec.py FpRows: the host's own MXCSR_MASK); simdcheck never sets it


def cpuref_mxcsr_mask():
    if _TEST_MASK: return _TEST_MASK[0]
    m = re.search(r'^#define\s+FNVL_CPUREF_MXCSR_MASK\s+(0[xX][0-9a-fA-F]+)u?\b', open(_CPUMODEL).read(), re.M)
    if not m: raise Uncertified('CPUREF has no mxcsr_mask')
    return int(m.group(1), 16)


def masks_set(mx): return Extract(12, 7, mx) == K(0x3F, 6)          # ASM-204 (I-FPMASK): IM DM ZM OM UM PM


def mxcsr_in_mask(mx): return (mx & K(~cpuref_mxcsr_mask() & 0xFFFFFFFF, 32)) == K(0, 32)


def rmode(mx):
    rc = Extract(14, 13, mx)
    return If(rc == K(0, 2), z3.RNE(), If(rc == K(1, 2), z3.RTN(), If(rc == K(2, 2), z3.RTP(), z3.RTZ())))


def parts(x, w):
    eb, sb = FMT[w]; fb = sb - 1
    e, f = Extract(w - 2, fb, x), Extract(fb - 1, 0, x)
    ones, z = K((1 << eb) - 1, eb), K(0, eb)
    return {'sgn': Extract(w - 1, w - 1, x), 'nan': And(e == ones, f != K(0, fb)), 'snan': And(e == ones, f != K(0, fb), Extract(fb - 1, fb - 1, x) == K(0, 1)),
            'inf': And(e == ones, f == K(0, fb)), 'zero': And(e == z, f == K(0, fb)), 'den': And(e == z, f != K(0, fb))}


def dazify(x, w, daz):
    p = parts(x, w)
    return If(And(daz, p['den']), Concat(p['sgn'], K(0, w - 1)), x)


def qnanize(x, w): return x | K(1 << (FMT[w][1] - 2), w)
def indef(w): return K((1 << w) - (1 << (FMT[w][1] - 2)), w)        # sign 1, exponent all ones, quiet bit 1 (SDM 4.8.3.5 QNaN floating-point indefinite)


def _fp(x, w): return z3.fpBVToFP(x, z3.FPSort(*FMT[w]))
def _wide(w): return z3.FPSort(FMT[w][0] + 3, FMT[w][1])


def _op(op, rm, a, b):
    return {'add': z3.fpAdd, 'sub': z3.fpSub, 'mul': z3.fpMul, 'div': z3.fpDiv}[op](rm, a, b)


def _post(narrow, wide, w, mx):
    """sse-post-comp: narrow(rm) is the operation in the result format, wide(rm) the same operation with 3 more exponent bits (the tiny and overflow tests of ACL2 use the result
    rounded with unbounded exponent range at the same precision): -> (result bits, flags BV6 [I D Z O U P as bits 0..5]).  The caller guarantees a finite exact value; zero /
    infinite results come out of the operation itself."""
    wsort = wide(z3.RNE()).sort()
    rm = rmode(mx)
    R = narrow(rm); W = wide(rm)
    inex_w = Not(z3.fpEQ(wide(z3.RTN()), wide(z3.RTP())))
    inex_n = Not(z3.fpEQ(narrow(z3.RTN()), narrow(z3.RTP())))
    lpn = z3.fpToFP(z3.RNE(), _fp(K(LPN[w], w), w), wsort); spn = z3.fpToFP(z3.RNE(), _fp(K(SPN[w], w), w), wsort)
    aW = z3.fpAbs(W)
    over = z3.fpGT(aW, lpn)
    tiny = And(Not(z3.fpIsZero(W)), Not(over), z3.fpLT(aW, spn))
    ftz = Extract(15, 15, mx) == K(1, 1)
    sgn = z3.fpIsNegative(W)
    zero_bits = If(sgn, K(1 << (w - 1), w), K(0, w))
    bits = If(And(tiny, ftz), zero_bits, z3.fpToIEEEBV(R))
    P, U, O = K(32, 6), K(16, 6), K(8, 6)
    flags = If(over, O | P, If(tiny, If(ftz, U | P, If(inex_n, U | P, K(0, 6))), If(inex_w, P, K(0, 6))))
    return bits, flags


def binop(op, a, b, mx, w):
    """one lane of add/sub/mul/div (ACL2 sse-binary-spec): operands BV w, MXCSR BV32 -> (result BV w, flags BV6)"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a, b = dazify(a, w, daz), dazify(b, w, daz)
    pa, pb = parts(a, w), parts(b, w)
    anynan = Or(pa['nan'], pb['nan'])
    sg_eq = pa['sgn'] == pb['sgn']
    undef = {'add': And(pa['inf'], pb['inf'], Not(sg_eq)), 'sub': And(pa['inf'], pb['inf'], sg_eq),
             'mul': And(Or(pa['inf'], pb['inf']), Or(pa['zero'], pb['zero'])),
             'div': Or(And(pa['inf'], pb['inf']), And(pa['zero'], pb['zero']))}[op]
    I, D, Z = K(1, 6), K(2, 6), K(4, 6)
    divz = And(K(1, 1) == K(1 if op == 'div' else 0, 1), pb['zero'], Not(pa['inf']))
    pre = If(Or(pa['snan'], pb['snan']), I, If(anynan, K(0, 6), If(undef, I, If(divz, Z, If(Or(pa['den'], pb['den']), D, K(0, 6))))))
    val_pre = If(pa['nan'], qnanize(a, w), If(pb['nan'], qnanize(b, w), indef(w)))
    has_pre = Or(anynan, undef)
    fa, fb = _fp(a, w), _fp(b, w)
    rm = rmode(mx)
    wide = _wide(w); wa, wb = z3.fpToFP(z3.RNE(), fa, wide), z3.fpToFP(z3.RNE(), fb, wide)
    bits, post = _post(lambda r: _op(op, r, fa, fb), lambda r: _op(op, r, wa, wb), w, mx)
    fin = And(Not(pa['inf']), Not(pb['inf']), Not(And(K(1, 1) == K(1 if op == 'div' else 0, 1), pb['zero'])))
    ieee = z3.fpToIEEEBV(_op(op, rm, fa, fb))                      # infinite operands / a zero divisor / an exact zero: the IEEE result, no post flags
    comp = If(fin, bits, ieee)
    return If(has_pre, val_pre, comp), pre | If(And(Not(has_pre), fin), post, K(0, 6))


def sqrt(a, mx, w):
    """one lane of sqrt (ACL2 sse-sqrt-spec)"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a = dazify(a, w, daz); pa = parts(a, w)
    I, D = K(1, 6), K(2, 6)
    neg = And(Not(pa['zero']), Not(pa['nan']), pa['sgn'] == K(1, 1))
    pre = If(pa['snan'], I, If(pa['nan'], K(0, 6), If(neg, I, If(pa['den'], D, K(0, 6)))))
    has_pre = Or(pa['nan'], neg)
    val_pre = If(pa['nan'], qnanize(a, w), indef(w))
    fa = _fp(a, w); rm = rmode(mx)
    wide = _wide(w); wa = z3.fpToFP(z3.RNE(), fa, wide)
    bits, post = _post(lambda r: z3.fpSqrt(r, fa), lambda r: z3.fpSqrt(r, wa), w, mx)
    special = Or(pa['inf'], pa['zero'])                              # sse-sqrt-comp: the operand itself
    comp = If(special, a, bits)
    return If(has_pre, val_pre, comp), pre | If(And(Not(has_pre), Not(special)), post, K(0, 6))


def minmax(op, a, b, mx, w):
    """one lane of min / max (ACL2 sse-max/min): any NaN or both zero -> the second operand (b), IE for a NaN of either kind, DE for a denormal operand"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a, b = dazify(a, w, daz), dazify(b, w, daz)
    pa, pb = parts(a, w), parts(b, w)
    fa, fb = _fp(a, w), _fp(b, w)
    pick = z3.fpGT(fa, fb) if op == 'max' else z3.fpLT(fa, fb)      # false on a NaN and on equal values (zeros included): b
    res = If(pick, a, b)
    flags = If(Or(pa['nan'], pb['nan']), K(1, 6), If(Or(pa['den'], pb['den']), K(2, 6), K(0, 6)))      # the invalid operation takes precedence: no #D with a NaN operand (SDM 1:4.9.2 Floating-Point Exception Priority; the host CPU agrees, ACL2's sse-max/min also sets DE there)
    return res, flags


# --- RCPPS / RCPSS / RSQRTPS / RSQRTSS: the table of the one reference CPU (CLAUDE.md "One fixed reference CPU model"), not a formula ----------------------------------------------
# The SDM bounds only the error (|rel. error| <= 1.5 * 2^-12); the value is the reference CPU's own.  The pinned external is lift/rt/rcptab.c, whose two 2048-entry tables and the
# integer structure around them are the CPU's results for all 2^32 inputs (rt/rcptab.cert.json: made on the reference CPU, judged by gate_fpexact.py).  Here the tables are READ from
# that file, and only when sha256(rcptab.c) is the certificate's source_sha256 and its mismatches are 0 and its CPU is rcpref.h's; otherwise the rows are `Uncertified`.
# The structure (NaN quieting, exponent 0 treated as zero, sign, exponent arithmetic) is the C text's, transcribed in rcp32 / rsqrt32 below and compared with the C function itself
# by tests/test_simdspec.py RcpRows.  MXCSR does not influence either instruction (rcptab.c header: checked on 20e6 inputs x 8 MXCSR values), so no MXCSR state is read or written.
_RT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'rt')
_TAB = []


def rcp_tables():
    if _TAB: return _TAB[0]
    import hashlib, json
    src = open(os.path.join(_RT, 'rcptab.c'), 'rb').read()
    try: cert = json.load(open(os.path.join(_RT, 'rcptab.cert.json')))
    except (OSError, ValueError): raise Uncertified('rcptab.cert.json missing')
    ref = open(os.path.join(_RT, 'rcpref.h')).read()
    cpu = (re.search(r'FNVL_RCP_REF_VENDOR "(\w+)"', ref).group(1), int(re.search(r'FNVL_RCP_REF_FAMILY (\d+)', ref).group(1)),
           int(re.search(r'FNVL_RCP_REF_MODEL (\d+)', ref).group(1)), int(re.search(r'FNVL_RCP_REF_STEPPING (\d+)', ref).group(1)))
    c = cert['cpu']
    if hashlib.sha256(src).hexdigest() != cert['source_sha256'] or cert['mismatches'] != 0 or (c['vendor'], c['family'], c['model'], c['stepping']) != cpu:
        raise Uncertified('rcptab.c is not the certified table of the reference CPU')
    t = src.decode()
    out = []
    for name in ('rcp_tab', 'rsqrt_tab'):
        body = re.search(r'static const unsigned short %s\[2048\] = \{(.*?)\};' % name, t, re.S).group(1)
        v = [int(x, 16) for x in re.findall(r'0x[0-9a-fA-F]+', body)]
        assert len(v) == 2048
        out.append(v)
    _TAB.append(out)
    return out


_ARR = {}


def _lookup(tab, idx):
    k = id(tab)
    if k not in _ARR:
        A = z3.K(z3.BitVecSort(11), K(0, 12))
        for i, v in enumerate(tab): A = z3.Store(A, i, K(v, 12))
        _ARR[k] = A
    return z3.Select(_ARR[k], idx)


def rcp32(x):
    tab = rcp_tables()[0]
    s, e, m = x & K(0x80000000, 32), Extract(30, 23, x), Extract(22, 0, x)
    r = s | ((K(253, 32) - ZeroExt(24, e)) << 23) | (ZeroExt(20, _lookup(tab, Extract(22, 12, x))) << 11)
    return If(e == K(255, 8), If(m != K(0, 23), x | K(0x400000, 32), s),
              If(e == K(0, 8), s | K(0x7F800000, 32), If(z3.UGE(e, K(253, 8)), s, r)))


def rsqrt32(x):
    tab = rcp_tables()[1]
    s, e, m = x & K(0x80000000, 32), Extract(30, 23, x), Extract(22, 0, x)
    idx = Concat(Extract(0, 0, e), Extract(22, 13, x))
    r = (LShR_(K(380, 32) - ZeroExt(24, e), 1) << 23) | (ZeroExt(20, _lookup(tab, idx)) << 11)
    return If(e == K(255, 8), If(m != K(0, 23), x | K(0x400000, 32), If(s != K(0, 32), K(0xFFC00000, 32), K(0, 32))),
              If(e == K(0, 8), s | K(0x7F800000, 32), If(s != K(0, 32), K(0xFFC00000, 32), r)))


def LShR_(a, n): return z3.LShR(a, n)


# --- compares: CMPPS/PD/SS/SD (imm8 bits 2:0 = the predicate, SDM 2A:CMPPS), COMISS/COMISD/UCOMISS/UCOMISD ----------------------------------------------------------------------
# ACL2 fp/cmp-spec.lisp sse-cmp-special / sse-cmp: an unordered result for NaN operands, IE for a signalling NaN (and for any NaN under the signalling predicates lt le nlt nle and under
# COMIS), DAZ read of the operands.  Values are the IEEE comparisons (OWNER 8).
def cmp_lane(pred, a, b, mx, w):
    """pred BV3 -> (mask BV w: all ones / 0, flags BV6)"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a, b = dazify(a, w, daz), dazify(b, w, daz)
    pa, pb = parts(a, w), parts(b, w)
    fa, fb = _fp(a, w), _fp(b, w)
    unord = Or(pa['nan'], pb['nan'])
    eq, lt, le = z3.fpEQ(fa, fb), z3.fpLT(fa, fb), z3.fpLEQ(fa, fb)
    out = If(pred == K(0, 3), And(z3.Not(unord), eq), If(pred == K(1, 3), And(z3.Not(unord), lt), If(pred == K(2, 3), And(z3.Not(unord), le), If(pred == K(3, 3), unord,
            If(pred == K(4, 3), Or(unord, z3.Not(eq)), If(pred == K(5, 3), Or(unord, z3.Not(lt)), If(pred == K(6, 3), Or(unord, z3.Not(le)), z3.Not(unord))))))))
    signalling = Or(pred == K(1, 3), pred == K(2, 3), pred == K(5, 3), pred == K(6, 3))
    inv = Or(pa['snan'], pb['snan'], And(unord, signalling))
    den = And(Or(pa['den'], pb['den']), z3.Not(unord))                  # a NaN operand: no #D (as in the arithmetic rows; the host CPU, SDM 1:4.9.2 priority)
    flags = If(inv, K(1, 6), If(den, K(2, 6), K(0, 6)))
    return If(out, K((1 << w) - 1, w), K(0, w)), flags


def comis(a, b, mx, w, ordered):
    """-> ((zf, pf, cf) BV1 each, flags BV6): COMIS (ordered: any NaN is invalid) / UCOMIS (only a signalling NaN is)"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a, b = dazify(a, w, daz), dazify(b, w, daz)
    pa, pb = parts(a, w), parts(b, w)
    fa, fb = _fp(a, w), _fp(b, w)
    unord = Or(pa['nan'], pb['nan'])
    b1 = lambda c: If(c, K(1, 1), K(0, 1))
    zf, pf, cf = b1(Or(unord, z3.fpEQ(fa, fb))), b1(unord), b1(Or(unord, z3.fpLT(fa, fb)))
    inv = Or(pa['snan'], pb['snan'], And(ordered, unord))
    den = And(Or(pa['den'], pb['den']), z3.Not(unord))
    return (zf, pf, cf), If(inv, K(1, 6), If(den, K(2, 6), K(0, 6)))


# --- conversions (ACL2 fp/convert.lisp, cvt-spec.lisp; SDM 2A:CVTSI2SS ...) -------------------------------------------------------------------------------------------------------
def i2f(i32, mx, w):
    """CVTSI2SS/SD, CVTDQ2PS/PD, CVTPI2PS/PD lane: signed int32 -> binary w under MXCSR.RC; PE when inexact (only binary32 can be)"""
    sort = z3.FPSort(*FMT[w]); rm = rmode(mx)
    R = z3.fpToFP(rm, i32, sort)
    inex = Not(z3.fpEQ(z3.fpToFP(z3.RTN(), i32, sort), z3.fpToFP(z3.RTP(), i32, sort)))
    return z3.fpToIEEEBV(R), If(inex, K(32, 6), K(0, 6))


def f2i(a, mx, w, trunc):
    """CVTSS2SI / CVTTSS2SI / CVTPS2DQ ... lane: binary w -> int32; a NaN, an infinity or a value outside int32 is the integer indefinite 0x80000000 with IE; PE when the value is not an integer"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a = dazify(a, w, daz); pa = parts(a, w); fa = _fp(a, w)
    rm = z3.RTZ() if trunc else rmode(mx)
    r = z3.fpRoundToIntegral(rm, fa)
    sort = z3.FPSort(*FMT[w])
    lo = z3.fpSignedToFP(z3.RNE(), K(0x80000000, 32), sort); hi = z3.FPVal(2147483648.0, sort)          # -2^31 and 2^31 are exact in binary32 / binary64
    inrange = And(Not(pa['nan']), Not(pa['inf']), z3.fpLEQ(lo, r), z3.fpLT(r, hi))
    val = z3.fpToSBV(z3.RTZ(), r, z3.BitVecSort(32))
    res = If(inrange, val, K(0x80000000, 32))
    inex = And(inrange, Not(z3.fpEQ(r, fa)))
    flags = If(inrange, If(inex, K(32, 6), If(pa['den'], K(2, 6), K(0, 6))), K(1, 6))
    return res, flags


def f2f_up(a32, mx):
    """CVTSS2SD / CVTPS2PD lane: exact widening; a signalling NaN is IE and quieted, a quiet NaN keeps its payload (shifted into the wider field), a denormal operand is DE (DAZ clear)"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a = dazify(a32, 32, daz); pa = parts(a, 32)
    nanbits = Concat(Extract(31, 31, a), K(0x7FF, 11), Extract(22, 0, a) | K(1 << 22, 23), K(0, 29))
    res = If(pa['nan'], nanbits, z3.fpToIEEEBV(z3.fpToFP(z3.RNE(), _fp(a, 32), z3.Float64())))
    flags = If(pa['snan'], K(1, 6), If(pa['den'], K(2, 6), K(0, 6)))
    return res, flags


def f2f_down(a64, mx):
    """CVTSD2SS / CVTPD2PS lane: rounding under MXCSR.RC with the overflow / underflow / precision flags of sse-post-comp; a NaN keeps sign and the top of its payload, quieted"""
    daz = Extract(6, 6, mx) == K(1, 1)
    a = dazify(a64, 64, daz); pa = parts(a, 64); fa = _fp(a, 64)
    nanbits = Concat(Extract(63, 63, a), K(0xFF, 8), Extract(51, 29, a) | K(1 << 22, 23))
    wide = z3.FPSort(15, 24)
    bits, post = _post(lambda r: z3.fpToFP(r, fa, z3.Float32()), lambda r: z3.fpToFP(r, fa, wide), 32, mx)
    special = Or(pa['inf'], pa['zero'])
    ieee = z3.fpToIEEEBV(z3.fpToFP(rmode(mx), fa, z3.Float32()))
    res = If(pa['nan'], nanbits, If(special, ieee, bits))
    flags = If(pa['snan'], K(1, 6), If(pa['nan'], K(0, 6), If(special, K(0, 6), post | If(pa['den'], K(2, 6), K(0, 6)))))
    return res, flags
