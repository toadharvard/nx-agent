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
The masked-response branches only (ASM-204: the exception masks of the declared machine are all set: MXCSR bits 12..7 = 1, the environment row `masks_set`); an unmasked exception
(ACL2 returns NIL, the instruction faults) has no row here: the precondition excludes it.
"""
import z3
from z3 import And, Concat, Extract, If, Not, Or, BitVecVal, ZeroExt

FMT = {32: (8, 24), 64: (11, 53)}          # (exponent bits, significand bits incl. the implicit one): binary32, binary64 (IEEE 754)
LPN = {32: 0x7F7FFFFF, 64: 0x7FEFFFFFFFFFFFFF}
SPN = {32: 0x00800000, 64: 0x0010000000000000}
OPS = ('add', 'sub', 'mul', 'div')


def K(v, w): return BitVecVal(v, w)


def masks_set(mx): return Extract(12, 7, mx) == K(0x3F, 6)          # ASM-204: IM DM ZM OM UM PM


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


def _post(opf, w, narrow_rm, mx, opnds):
    """sse-post-comp on the operands `opnds` (FP values in the narrow sort) of the lane function opf(rm, *ops): (result bits, flags BV6 [I D Z O U P as bits 0..5]).
    The caller guarantees finite operands and a non-zero exact result path; zero / infinite results come out of the IEEE operation itself."""
    nar = z3.FPSort(*FMT[w]); wide = _wide(w)
    wops = [z3.fpToFP(z3.RNE(), o, wide) for o in opnds]
    R = opf(narrow_rm, *opnds)                                         # the rounded result in the real format
    W = opf(narrow_rm, *wops)                                          # rounded with unbounded exponent range (same precision)
    inex_w = Not(z3.fpEQ(opf(z3.RTN(), *wops), opf(z3.RTP(), *wops)))
    inex_n = Not(z3.fpEQ(opf(z3.RTN(), *opnds), opf(z3.RTP(), *opnds)))
    lpn = z3.fpToFP(z3.RNE(), _fp(K(LPN[w], w), w), wide); spn = z3.fpToFP(z3.RNE(), _fp(K(SPN[w], w), w), wide)
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
    bits, post = _post(lambda r, x, y: _op(op, r, x, y), w, rm, mx, [fa, fb])
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
    bits, post = _post(lambda r, x: z3.fpSqrt(r, x), w, rm, mx, [fa])
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
