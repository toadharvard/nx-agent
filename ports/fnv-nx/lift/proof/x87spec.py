"""x87 FPU specification as z3 terms (task 2.6, OWNER 10).

Reference: the Intel 64 and IA-32 Architectures Software Developer's Manual (SDM), combined volumes
(Vol 1 ch. 4 and ch. 8, Vol 2A/2B instruction pages).  Citation form used on every line:

    SDM 1:8.1.3.2           Vol 1 section 8.1.3.2
    SDM 1:T8-6              Vol 1 table 8-6
    SDM 2A:FADD Op          Vol 2A page "FADD/FADDP/FIADD", its Operation pseudo-code
    SDM 2A:FADD Exc         the same page, "Floating-Point Exceptions" list
    SDM 2A:FADD Flags       the same page, "FPU Flags Affected"

The spec has two parts.
  Part (i)  Shared machinery: TOP and ST(i) indexing, the tag word, FSW (C0-C3, IE..PE, SF, ES, B),
            FCW (masks, PC, RC), the fcomi EFLAGS mapping, the conversions between the memory
            formats (m32fp, m64fp, m80fp, m16int, m32int, m64int, packed BCD) and the register format.
  Part (ii) One function per distinct instruction (the REGISTRY at the end of the file).

Carrier: the exact 80-bit image (SDM 1:8.1.2, 1:T4-3).
  The state of a data register is its 80-bit image (St.R, Array BV3 -> BV80), exactly as FSAVE stores it and as the MMX instructions leave it
  (SDM 1:9.6.2: an MMX write sets bits 64..79 to ones).  No register value is ever rounded on its way into the state: an image that is an unnormal
  or pseudo-NaN (an 'unsupported' encoding, SDM 1:T8-3, which an MMX write can leave behind) is a state like any other.  The tag of a register
  follows its image (tag_of_img: 01 zero; 10 NaN, infinity, denormal or unsupported; 00 otherwise; SDM 1:8.1.7).
  Every instruction has one class, read off the SDM page (Vol 1 8.3 by section, then its Operation):
    * exact on the image: data transfer (FLD, FST, FSTP, FXCH, FCMOV, FILD, FBLD, FIST, FBSTP, FSAVE/FRSTOR, FNSTENV), sign operations (FCHS, FABS),
      classification and comparison (FXAM, FCOM family, FTST, FICOM), FRNDINT, and the constant loads (the constant rounded to a 64-bit significand
      by RC, SDM 1:8.3.4).  They work on the image (and z3's FPSort(15,64), the double extended format, where a value is needed) with no deviation.
    * binary64 (OWNER 8: the CLAUDE.md rule that floating point is plain IEEE): arithmetic and transcendentals.  Such an instruction reads each
      operand as view64(image, FCW) = the image rounded to binary64 by FCW.RC (NaN payload kept, overflow by RC), computes in binary64 (with FCW.PC = 00B
      the result is first rounded to binary32, SDM 1:8.1.5.2: exact for + - * / sqrt because 53 >= 2*24+2), and writes to_m80(result), the exact widening.
      The deviations from the SDM's 80-bit operation are therefore: the 64-bit significand and 15-bit exponent of an operand are rounded to 53 and 11
      bits on the read, the result has 53 bits, FCW.PC = 10B and 11B behave alike, and a denormal operand (#D) is a binary64 subnormal of the view.
  An unsupported operand of an arithmetic-class instruction is the invalid operation: IE (without SF), the QNaN indefinite under the mask (SDM 1:T8-10,
  and the '#IA ... unsupported format' line of each instruction page); of a comparison, IE and the unordered condition codes; of FST m32/m64, FIST,
  FBSTP, FRNDINT: IE and the indefinite; of FXAM: class 000.  The m80 moves FLD m80, FSTP m80, FLD ST(i), FST ST(i), FXCH are exempt (SDM 2A:FLD Exc).
  Bit patterns are used for binary64 values because z3's FP sort has one NaN only, so a z3 FP term cannot carry the sign, quiet bit or
  payload that SDM 1:T4-8 and 1:4.8.3.4 make observable.  z3 FP terms are used only on non-NaN values.
  Unmasked OE/UE of a register result stores the biased result of SDM 1:8.5.4/8.5.5, which is not representable in binary64; every function that
  can raise OE or UE of an arithmetic-class result returns the precondition that the corresponding mask bit is set (Out.pre).  The lifter's fact is
  that every FLDCW/FLDENV/FRSTOR/FXRSTOR site keeps the exceptions masked (CLAUDE.md, owner order 2026-11-14, hardware exceptions).

Return convention.  Every instruction function is  f(s, m=None) -> Out  where s is the machine state
(St), m the memory operand bit-vector for memory forms.  Out.s is the new state, Out.mem the value
stored (or None), Out.wr the Bool (memory was written), Out.ax a value written to AX (fnstsw ax),
Out.eflags the (ZF, PF, CF) written for fcomi/fucomi (OF, SF, AF are cleared, SDM 2A:FCOMI Flags),
Out.pre the Bool (the instruction's behaviour is defined by the SDM and the register format here).
A page that says an exception is signalled with TOP and operands unchanged (SDM 1:8.5.1.1, 8.5.1.2,
8.5.2, 8.5.3) gives the state with the flags set and TOP, tags, registers of the input.
"""
import z3
from z3 import (BitVecVal, Extract, Concat, If, And, Or, Not, ZeroExt, SignExt, LShR,
                UGT, ULT, UGE, ULE)

F64 = z3.Float64()
F32 = z3.Float32()


def K(v, n):
    return BitVecVal(v, n)


# --------------------------------------------------------------------------------------------
# Part (i).  Shared machinery
# --------------------------------------------------------------------------------------------

# --- x87 FPU status word, SDM 1:8.1.3 (Figure 8-4) -----------------------------------------
IE, DE, ZE, OE, UE, PE = 0, 1, 2, 3, 4, 5   # SDM 1:8.1.3.3: exception flags are bits 0..5
SF = 6                                       # SDM 1:8.1.3.4: stack fault flag is bit 6
ES = 7                                       # SDM 1:8.1.3.3: exception summary is bit 7
C0, C1, C2, C3 = 8, 9, 10, 14                # SDM 1:8.1.3 Figure 8-4: C0 bit 8, C1 bit 9, C2 bit 10, C3 bit 14
TOP_LO, TOP_HI = 11, 13                      # SDM 1:8.1.3.1: TOP is bits 11..13
BBIT = 15                                    # SDM 1:8.1.3.3: B is bit 15 (reflects ES)
# --- x87 FPU control word, SDM 1:8.1.5 (Figure 8-6) ----------------------------------------
IM, DM, ZM, OM, UM, PM = 0, 1, 2, 3, 4, 5   # SDM 1:8.1.5.1: mask bits 0..5
PC_LO, PC_HI = 8, 9                          # SDM 1:8.1.5.2: PC is bits 8 and 9
RC_LO, RC_HI = 10, 11                        # SDM 1:8.1.5.3: RC is bits 10 and 11
FCW_INIT = 0x037F                            # SDM 2A:FINIT Op: FPUControlWord <- 037FH
FSW_INIT = 0x0000                            # SDM 2A:FINIT Op: FPUStatusWord <- 0
TW_INIT = 0xFFFF                             # SDM 2A:FINIT Op: FPUTagWord <- FFFFH
# Tag values, SDM 1:8.1.7 (Figure 8-7): 00 valid, 01 zero, 10 special, 11 empty.
TAG_VALID, TAG_ZERO, TAG_SPECIAL, TAG_EMPTY = 0, 1, 2, 3
# PC values, SDM 1:T8-2.
PC_SINGLE, PC_RESERVED, PC_DOUBLE, PC_EXT = 0, 1, 2, 3
# RC values, SDM 1:T4-9.
RC_NEAREST, RC_DOWN, RC_UP, RC_ZERO = 0, 1, 2, 3
MASK_ALL = 0x3F                              # the six exception masks together (bits 0..5)


def bit(w, i):
    """Bool: bit i of vector w is 1."""
    return Extract(i, i, w) == K(1, 1)


def set_bit(w, i, b):
    """w with bit i set to the Bool b."""
    n = w.size()
    m = K(1 << i, n)
    return (w & ~m) | If(b, m, K(0, n))


def top_of(fsw):
    return Extract(TOP_HI, TOP_LO, fsw)                         # SDM 1:8.1.3.1


def with_top(fsw, t):
    return Concat(Extract(15, 14, fsw), t, Extract(10, 0, fsw))   # SDM 1:8.1.3.1: TOP is bits 11..13


def rc_of(fcw):
    return Extract(RC_HI, RC_LO, fcw)                           # SDM 1:8.1.5.3


def pc_of(fcw):
    return Extract(PC_HI, PC_LO, fcw)                           # SDM 1:8.1.5.2


def masked(fcw, i):
    """Bool: exception i (IE..PE) is masked, SDM 1:8.1.5.1 (mask bit 1 = masked)."""
    return bit(fcw, i)


def rm_of(fcw):
    """z3 rounding mode of FCW.RC, SDM 1:T4-9: 00 nearest-even, 01 down, 10 up, 11 toward zero."""
    rc = rc_of(fcw)
    return If(rc == K(RC_NEAREST, 2), z3.RNE(),
              If(rc == K(RC_DOWN, 2), z3.RTN(),
                 If(rc == K(RC_UP, 2), z3.RTP(), z3.RTZ())))


def es_of(fsw, fcw):
    """SDM 1:8.1.3.3: ES is set when any unmasked exception flag is set.  Flags are bits 0..5, masks
    bits 0..5 of the control word (SDM 1:8.1.5.1)."""
    return (Extract(5, 0, fsw) & ~Extract(5, 0, fcw)) != K(0, 6)


def sync_es(fsw, fcw):
    """ES recomputed and B = ES (SDM 1:8.1.3.3: 'The B-bit (bit 15) ... reflects the contents of the ES flag')."""
    e = es_of(fsw, fcw)
    return set_bit(set_bit(fsw, ES, e), BBIT, e)


def cc(fsw, c0=None, c1=None, c2=None, c3=None):
    """fsw with the given condition code flags set to the Bools (None leaves a flag), SDM 1:8.1.3.2."""
    for pos, v in ((C0, c0), (C1, c1), (C2, c2), (C3, c3)):
        if v is not None:
            fsw = set_bit(fsw, pos, v)
    return fsw


def flags_or(fsw, f6):
    """Set exception flag bits (6-bit vector f6), SDM 1:8.1.3.3: flags are sticky."""
    return fsw | ZeroExt(10, f6)


def fl(*names):
    """6-bit exception flag vector with the named flags set."""
    v = 0
    for n in names:
        v |= 1 << n
    return K(v, 6)


NOFLAGS = K(0, 6)


# --- machine state ---------------------------------------------------------------------------
class St:
    """x87 state: R physical register file (Array BV3 -> BV80: the exact 80-bit image of each of the eight
    registers, SDM 1:8.1.2 and 1:T4-3 layout), tag (SDM 1:8.1.7), fsw, fcw, and the pointers of SDM 1:8.1.8, 8.1.9."""
    FIELDS = ("R", "tag", "fsw", "fcw", "fip", "fcs", "fdp", "fds", "fop")

    TRANSIENT = ("sfr",)      # not architectural: this instruction raised a stack fault (SF newly reported); finish() moves it to Out.sf_raised

    def __init__(self, **kw):
        for f in self.FIELDS:
            setattr(self, f, kw[f])
        self.sfr = kw.get("sfr", z3.BoolVal(False))

    @staticmethod
    def fresh(name):
        return St(R=z3.Array(name + ".R", z3.BitVecSort(3), z3.BitVecSort(80)),
                  tag=z3.BitVec(name + ".tag", 16), fsw=z3.BitVec(name + ".fsw", 16),
                  fcw=z3.BitVec(name + ".fcw", 16), fip=z3.BitVec(name + ".fip", 32),
                  fcs=z3.BitVec(name + ".fcs", 16), fdp=z3.BitVec(name + ".fdp", 32),
                  fds=z3.BitVec(name + ".fds", 16), fop=z3.BitVec(name + ".fop", 11))

    def copy(self, **kw):
        d = {f: getattr(self, f) for f in self.FIELDS + self.TRANSIENT}
        d.update(kw)
        return St(**d)

    @staticmethod
    def ite(c, a, b):
        return St(**{f: If(c, getattr(a, f), getattr(b, f)) for f in St.FIELDS + St.TRANSIENT})

    @staticmethod
    def after_finit(r):
        """State after FINIT/FNINIT from register file r, SDM 2A:FINIT Op (the data registers are not changed)."""
        return St(R=r, tag=K(TW_INIT, 16), fsw=K(FSW_INIT, 16), fcw=K(FCW_INIT, 16),
                  fip=K(0, 32), fcs=K(0, 16), fdp=K(0, 32), fds=K(0, 16), fop=K(0, 11))


def top(s):
    return top_of(s.fsw)


def phys(s, i):
    """Physical register of ST(i), SDM 1:8.1.2 'to specify the ith register from TOP in the stack': (TOP+i) mod 8."""
    return (top(s) + K(i, 3)) if isinstance(i, int) else (top(s) + i)


def st80(s, i):
    """The exact 80-bit image of ST(i) (the exact class: data transfer, sign, classification, stores)."""
    return z3.Select(s.R, phys(s, i))


def st(s, i):
    """ST(i) as an arithmetic operand (the arithmetic class, OWNER 8): the image read as binary64, rounded by FCW.RC."""
    return view64(st80(s, i), s.fcw)


def set_st(s, i, v):
    return s.copy(R=z3.Store(s.R, phys(s, i), v))


def tag_at(s, p):
    """Two-bit tag of physical register p, SDM 1:8.1.7 Figure 8-7 (TAG(n) is bits 2n+1:2n)."""
    sh = ZeroExt(13, p) * K(2, 16)
    return Extract(1, 0, LShR(s.tag, sh))


def set_tag_at(s, p, t):
    sh = ZeroExt(13, p) * K(2, 16)
    m = K(3, 16) << sh
    return s.copy(tag=(s.tag & ~m) | (ZeroExt(14, t) << sh))


def is_empty(s, i):
    """SDM 1:8.5.1.1: 'An empty register has a tag value of 11'."""
    return tag_at(s, phys(s, i)) == K(TAG_EMPTY, 2)


def tag_of_img(m):
    """The tag the 80-bit image m gets when loaded (SDM 1:8.1.7: 01 zero; 10 special = 'invalid (NaN, unsupported),
    infinity, or denormal'; 00 valid)."""
    return If(is_zero80(m), K(TAG_ZERO, 2), If(Or(is_unsup80(m), is_nan80(m), is_inf80(m), is_den80(m)), K(TAG_SPECIAL, 2), K(TAG_VALID, 2)))


def write_img(s, i, img):
    """Write the image to ST(i) and tag it from the image (SDM 2A:FLD Op: 'ST(0) <- ...' loads tag the register non-empty)."""
    p = phys(s, i)
    s2 = s.copy(R=z3.Store(s.R, p, img))
    return set_tag_at(s2, p, tag_of_img(img))


def write_reg(s, i, v):
    """Arithmetic-class write (OWNER 8): the binary64 result v widened exactly to its image (to_m80) into ST(i)."""
    return write_img(s, i, to_m80(v))


def push_target_nonempty(s):
    """SDM 1:8.5.1.1 stack overflow: 'An instruction attempts to load a non-empty x87 FPU register'.  A
    push writes physical register TOP-1."""
    return tag_at(s, top(s) - K(1, 3)) != K(TAG_EMPTY, 2)


def do_push_img(s, img):
    """TOP <- TOP-1; ST(0) <- img (SDM 2A:FLD Op: 'TOP <- TOP-1; ST(0) <- ...').  Caller checks overflow."""
    s2 = s.copy(fsw=with_top(s.fsw, top(s) - K(1, 3)))
    return write_img(s2, 0, img)


def do_push(s, v):
    return do_push_img(s, to_m80(v))


def do_pop(s):
    """Mark ST(0) empty and TOP <- TOP+1 (SDM 2A:FSTP Op: 'ST(0) <- ... ; pop'; SDM 1:8.1.7 empty tag 11)."""
    s2 = set_tag_at(s, top(s), K(TAG_EMPTY, 2))
    return s2.copy(fsw=with_top(s2.fsw, top(s2) + K(1, 3)))


# --- the 80-bit image: classes, SDM 1:T4-3, 1:8.2.2, 1:T8-3 (layout: sign 79, exponent 78..64, integer bit 63, fraction 62..0) -------
F80 = z3.FPSort(15, 64)       # the double extended format as a z3 FP sort: 15 exponent bits, 64 significand bits (integer bit included), SDM 1:T4-2
INDEF80 = 0xFFFFC000000000000000   # SDM 1:T4-3 'QNaN Floating-Point Indefinite': sign 1, exponent 7FFFH, integer bit 1, fraction 10..0


def i_sgn(m):
    return Extract(79, 79, m)


def i_exp(m):
    return Extract(78, 64, m)


def i_int(m):
    return Extract(63, 63, m)


def i_frac(m):
    return Extract(62, 0, m)


def is_unsup80(m):
    """SDM 1:T8-3 / 1:8.2.2: an unsupported encoding is a non-zero biased exponent with integer bit 0 (an unnormal, or for exponent 7FFFH a
    pseudo-NaN / pseudo-infinity).  Exponent 0 with integer bit 1 is a pseudo-denormal, which SDM 1:8.2.2 gives as 'they are treated as denormals'."""
    return And(i_exp(m) != K(0, 15), i_int(m) == K(0, 1))


def is_nan80(m):
    return And(i_exp(m) == K(0x7FFF, 15), i_int(m) == K(1, 1), i_frac(m) != K(0, 63))


def is_snan80(m):
    return And(is_nan80(m), Extract(62, 62, m) == K(0, 1))        # SDM 1:T4-3: SNaN fraction MSB 0


def is_inf80(m):
    return And(i_exp(m) == K(0x7FFF, 15), i_int(m) == K(1, 1), i_frac(m) == K(0, 63))


def is_zero80(m):
    return And(i_exp(m) == K(0, 15), Extract(63, 0, m) == K(0, 64))


def is_den80(m):
    """SDM 1:T4-3 denormal: exponent 0, non-zero significand (the integer bit may be 1, a pseudo-denormal)."""
    return And(i_exp(m) == K(0, 15), Extract(63, 0, m) != K(0, 64))


def fp80(m):
    """The z3 FPSort(15,64) value of a supported non-NaN image (meaningful for those only): a pseudo-denormal has the value of the normal of the
    smallest exponent with the same fraction (SDM 1:8.2.2: the denormal biased exponent 0 means 1 - 16383)."""
    e = If(And(i_exp(m) == K(0, 15), i_int(m) == K(1, 1)), K(1, 15), i_exp(m))
    return z3.fpFP(i_sgn(m), e, i_frac(m))


def img80(f):
    """The image of an FPSort(15,64) value that is not a NaN."""
    b = z3.fpToIEEEBV(f)
    e = Extract(77, 63, b)
    return Concat(Extract(78, 78, b), e, If(e != K(0, 15), K(1, 1), K(0, 1)), Extract(62, 0, b))


def view64(m, fcw):
    """The arithmetic-class read of an image (OWNER 8: the CLAUDE.md rule that floating point is plain IEEE): the value rounded to binary64 by
    FCW.RC (z3 does the overflow value, the denormal path and the directed modes), as a bit pattern.  NaN: sign, quiet bit and the 51 payload bits
    below it are kept; if the kept fraction would be zero (an SNaN whose set payload bits are all in the dropped part) bit 0 of the binary64 fraction
    is set so the value stays a NaN (SDM 1:T4-3: SNaN fraction is non-zero).  Infinity keeps its sign.  An unsupported encoding reads as junk: every
    arithmetic-class instruction treats it before it uses the value (is_unsup80)."""
    top52 = Extract(62, 11, m)
    nan64 = Concat(i_sgn(m), K(0x7FF, 11), If(top52 == K(0, 52), K(1, 52), top52))
    inf64 = Concat(i_sgn(m), K(0x7FF, 11), K(0, 52))
    fin = bits(z3.fpToFP(rm_of(fcw), fp80(m), F64))
    return If(is_nan80(m), nan64, If(is_inf80(m), inf64, fin))


def _mag_to_img(sign, mag):
    """Image of the integer (-1)^sign * mag (mag: 64-bit unsigned), exactly: the leading one at bit 63 of the significand, exponent 16383 + 63 - lz."""
    lz = clz(mag, 64)
    e = K(16383 + 63, 16) - Extract(15, 0, lz)
    return If(mag == K(0, 64), Concat(sign, K(0, 79)), Concat(sign, Extract(14, 0, e), mag << lz))


def int_to_img(m, n):
    """m (n-bit two's complement) -> image, exact (SDM 2A:FILD Op 'ST(0) := ConvertToDoubleExtendedPrecisionFP(SRC)': every n <= 64 bit integer has
    a 64-bit significand).  Integer zero is +0."""
    sign = Extract(n - 1, n - 1, m)
    mag = If(sign == K(1, 1), -m, m)
    return _mag_to_img(sign, ZeroExt(64 - n, mag))


# --- the register format: classes, SDM 1:T4-3 (binary64 layout: 1 + 11 + 52) -----------------
def sgn(b):
    return Extract(63, 63, b)


def ex(b):
    return Extract(62, 52, b)


def fr(b):
    return Extract(51, 0, b)


def is_nan(b):
    return And(ex(b) == K(0x7FF, 11), fr(b) != K(0, 52))     # SDM 1:T4-3: exponent 11..11, fraction non-zero


def is_snan(b):
    return And(is_nan(b), Extract(51, 51, b) == K(0, 1))     # SDM 1:T4-3: SNaN fraction MSB 0


def is_qnan(b):
    return And(is_nan(b), Extract(51, 51, b) == K(1, 1))     # SDM 1:T4-3: QNaN fraction MSB 1


def is_inf(b):
    return And(ex(b) == K(0x7FF, 11), fr(b) == K(0, 52))


def is_zero(b):
    return And(ex(b) == K(0, 11), fr(b) == K(0, 52))


def is_sub(b):
    return And(ex(b) == K(0, 11), fr(b) != K(0, 52))         # SDM 1:T4-3 denormals: exponent 0, fraction non-zero


QNAN_INDEF = 0xFFF8000000000000
# SDM 1:4.8.3.7 'QNaN Floating-Point Indefinite', encoding SDM 1:T4-3: sign 1, exponent all ones, integer bit 1, fraction 10..0


def quieten(b):
    """SDM 1:4.8.3.5: 'When an SNaN is converted to a QNaN, the conversion is handled by setting the most-significant fraction bit of the SNaN to 1'."""
    return b | K(1 << 51, 64)


def fp(b):
    """z3 FP term of the bit pattern b (meaningful for non-NaN b only)."""
    return z3.fpBVToFP(b, F64)


def bits(f):
    return z3.fpToIEEEBV(f)


def real(b):
    return z3.fpToReal(fp(b))


NAN_TIE = z3.Bool("nd.nan.tie")


def nan_result2(a, b):
    """SDM 1:T4-8, x87 column, for two operands of which at least one is a NaN.  Returns the result
    pattern (a QNaN) and the Bool (a result was selected by a rule) (False when the SDM leaves the choice
    open: two NaNs of equal significand and different sign; the result is then the admissible choice nd.nan.tie: a or b, each is the
    source operand with the larger significand).  The larger significand is read on the binary64 view of the operands (OWNER 8: the fraction is
    the top 52 bits of the 80-bit one).  The instructions with two register operands add nd.nan.tie to Out.nd (CERTS.md, section x87 admissible choices)."""
    an, bn = is_nan(a), is_nan(b)
    asn, bsn = is_snan(a), is_snan(b)
    # SNaN and QNaN -> the QNaN operand;  SNaN and a number -> the SNaN converted;  QNaN and number -> the QNaN
    only_a = And(an, Not(bn))
    only_b = And(bn, Not(an))
    mixed_a_q = And(an, bn, Not(asn), bsn)      # a QNaN, b SNaN -> a
    mixed_b_q = And(an, bn, asn, Not(bsn))      # a SNaN, b QNaN -> b
    both_same_kind = And(an, bn, asn == bsn)    # two SNaNs or two QNaNs -> larger significand
    pick_a_larger = UGT(fr(a), fr(b))
    ambiguous = And(both_same_kind, fr(a) == fr(b), sgn(a) != sgn(b))
    larger = If(ambiguous, If(NAN_TIE, a, b), If(pick_a_larger, a, b))
    res = If(only_a, a, If(only_b, b, If(mixed_a_q, a, If(mixed_b_q, b, larger))))
    # equal significand, different sign: the SDM names no rule (the choice is NAN_TIE above)
    return quieten(res), Not(ambiguous)


# --- exact-real helpers (finite values) ------------------------------------------------------
def abs_real(r):
    return If(r < 0, -r, r)


def min_normal(pc):
    """Smallest normal of the result precision: SDM 1:T4-12 single 2^-126, double 2^-1022 (x87 'taking
    into account precision control', SDM 1:4.9.1.5)."""
    return If(pc == K(PC_SINGLE, 2), z3.RealVal(1) / z3.RealVal(2 ** 126), z3.RealVal(1) / z3.RealVal(2 ** 1022))


def round_exact(pc, rm, exact):
    """The rounded register-format value of the exact real 'exact' under rounding mode rm, with the
    precision of PC (SDM 1:8.1.5.2: 24 bits for 00B, 53 or 64 bits for 10B/11B -> binary64 here), as a
    bit pattern; plus the flags, SDM 1:4.9.1.4-4.9.1.6, 1:8.5.4-8.5.6."""
    r64 = z3.fpRealToFP(rm, exact, F64)
    r32 = z3.fpRealToFP(rm, exact, F32)
    # single precision result widened exactly into the register format
    r_single = z3.fpToFP(z3.RNE(), r32, F64)
    r = If(pc == K(PC_SINGLE, 2), r_single, r64)
    rb = bits(r)
    rr = z3.fpToReal(r)
    inf_res = z3.fpIsInf(r)
    inexact = And(Not(inf_res), rr != exact)
    overflow = inf_res                                    # finite operands only, SDM 1:4.9.1.4
    # tiny: the result rounded with unbounded exponent is nonzero and below the smallest normal
    ub64 = z3.fpRealToFP(rm, exact, z3.FPSort(30, 53))
    ub32 = z3.fpRealToFP(rm, exact, z3.FPSort(30, 24))
    ubr = If(pc == K(PC_SINGLE, 2), z3.fpToReal(ub32), z3.fpToReal(ub64))
    tiny = And(ubr != 0, abs_real(ubr) < min_normal(pc))   # SDM 1:4.9.1.5: non-zero and tiny
    ue = And(tiny, inexact)                               # SDM 1:4.9.1.5 masked: UE only if tiny and inexact
    roundup = And(Not(inf_res), abs_real(rr) > abs_real(exact))   # SDM 1:8.5.6: C1 = rounded up (magnitude)
    return rb, overflow, ue, inexact, roundup, tiny


# --- finishing an instruction: unmasked exceptions, ES/B ---------------------------------------
class Out:
    """Result of one instruction.  undef: 16-bit mask of FSW bits the SDM lists as 'Undefined' (the model
    leaves them unchanged; a comparison ignores them).  mem_undef: mask of memory-image bits the SDM leaves
    reserved.  nd, nd_pre: the SDM leaves some results implementation-dependent (FPREM's N, whether FCMOVcc with a false
    condition reports an underflow, whether FPTAN/FSINCOS push for a NaN or infinite operand).  The spec is then a
    relation: the free variables nd with their admissible range nd_pre.  A Form refines it when for its behaviour
    there is a value of nd within nd_pre for which it equals the spec.  trap: Bool, a #MF is signalled before the instruction executes (SDM 1:8.7.1: a waiting
    instruction with a pending unmasked exception); set by the registry wrapper."""
    def __init__(self, s, mem=None, wr=None, ax=None, eflags=None, pre=None, undef=None, mem_undef=None):
        self.s = s
        self.mem = mem
        self.wr = wr if wr is not None else z3.BoolVal(False)
        self.ax = ax
        self.eflags = eflags
        self.pre = pre if pre is not None else z3.BoolVal(True)
        self.undef = undef if undef is not None else K(0, 16)
        self.mem_undef = mem_undef
        self.trap = z3.BoolVal(False)
        self.raised = K(0, 6)           # the exception flags this instruction raised (IE DE ZE OE UE PE), set by finish()
        self.sf_raised = z3.BoolVal(False)   # it raised a stack fault (the SF flag together with IE)
        self.fcw_in = None              # the control word it ran under
        self.nd = []                    # free variables of the SDM's implementation-dependent choices (module docstring 'Nondeterminism')
        self.nd_pre = z3.BoolVal(True)  # the admissible range of those variables


def finish(s_in, s_out, newflags, mem=None, extra_pre=None, ou_pre=False, abort_set=None):
    """Apply the SDM 1:8.5 unmasked-exception rule to the result s_out of an instruction.
    newflags is the 6-bit vector of exception flags the instruction raised.
      * flags are set in FSW either way (SDM 1:8.1.3.3, sticky);
      * an unmasked IE, DE or ZE leaves TOP, tags, registers and the memory operand unchanged
        (SDM 1:8.5.1.1, 8.5.1.2, 8.5.2, 8.5.3: 'the top-of-stack pointer (TOP) and source operands remain
        unchanged');
      * an unmasked OE/UE with a memory destination also leaves everything unchanged (SDM 1:8.5.4/8.5.5,
        'Destination is a memory location'): the caller passes abort_set = IE|OE|UE for such stores;
      * an unmasked PE stores the result and then signals (SDM 1:8.5.6: handler invoked in addition);
      * ES and B are recomputed (SDM 1:8.1.3.3).
    ou_pre adds the precondition that OE and UE are masked when this instruction raised them (the biased result
    of SDM 1:8.5.4/8.5.5 is not representable in binary64, OWNER 8).  It is not a bare assumption: it is the
    L3a fact `x87-masked-sites` of CLAUDE.md, section Hardware exceptions in lifted code (every fldcw, fldenv, frstor and
    fxrstor site of the exe keeps the exceptions masked, so FCW.OM = FCW.UM = 1 at every x87 instruction).
    CERTS.md, section x87 precondition facts, names it; a certificate that uses ou_pre has that fact in its K."""
    fcw = s_in.fcw
    unm = ~Extract(5, 0, fcw)
    abort = (newflags & unm & (fl(IE, DE, ZE) if abort_set is None else abort_set)) != K(0, 6)
    flagged_out = flags_or(s_out.fsw, newflags)
    s_abort = s_in.copy(fsw=with_top(flags_or(cc_copy(s_out.fsw, s_in.fsw), newflags), top(s_in)))
    s_ok = s_out.copy(fsw=flagged_out)
    s_fin = St.ite(abort, s_abort, s_ok)
    s_fin = s_fin.copy(fsw=sync_es(s_fin.fsw, s_fin.fcw))
    pre = z3.BoolVal(True) if extra_pre is None else extra_pre
    if ou_pre:
        pre = And(pre, Not(And(Extract(OE, OE, newflags) == K(1, 1), Not(masked(fcw, OM)))),
                  Not(And(Extract(UE, UE, newflags) == K(1, 1), Not(masked(fcw, UM)))))
    wr = Not(abort) if mem is not None else z3.BoolVal(False)
    o = Out(s_fin.copy(sfr=z3.BoolVal(False)), mem=mem, wr=wr, pre=pre)
    o.raised, o.sf_raised, o.fcw_in = newflags, s_out.sfr, fcw
    return o


def cc_copy(src, dst):
    """C1 and SF of src placed into dst.  When an exception aborts an instruction, C1 (stack fault direction
    or round-up, SDM 1:8.1.3.2) and SF (SDM 1:8.5.1.1) are those the instruction set; C0, C2, C3 stay
    (SDM 2A:FCOM Table 1-17 note: 'Flags not set if unmasked invalid-arithmetic-operand (#IA) exception is generated.')."""
    out = dst
    for pos in (C1, SF):
        out = set_bit(out, pos, bit(src, pos))
    return out


def stack_fault_flags(overflow):
    """SDM 1:8.5.1.1: 'sets the IE flag (bit 0) and the SF flag (bit 6)'.  Returns (6-bit flags, SF Bool);
    the SF bit is outside the 6-bit set so the caller sets it with set_sf."""
    return fl(IE)


def set_sf(fsw):
    return set_bit(fsw, SF, z3.BoolVal(True))


def stack_fault_state(s, overflow):
    """State after a detected stack fault with the exception masked or not: SF set (IE is raised through
    the flag vector); C1 = 1 for overflow, 0 for underflow (SDM 1:8.5.1.1 and 1:8.1.3.2)."""
    f = set_sf(s.fsw)
    f = cc(f, c1=overflow if isinstance(overflow, z3.BoolRef) else z3.BoolVal(overflow))
    return s.copy(fsw=f, sfr=z3.BoolVal(True))


# --- the fcom/fcomi condition codes -----------------------------------------------------------
def compare(a, b, ordered):
    """Compare the images a (ST(0)) and b (exact class: the 80-bit values themselves).  Returns (c3, c2, c0, ie_raised, de_raised).
    SDM 1:T8-6: ST(0) > src 000, ST(0) < src 001, equal 100, unordered 111 (C3, C2, C0).  SDM 1:T8-10 /
    2A:FCOM Exc: IE for an SNaN or an unsupported format; for ordered compares (FCOM, FICOM, FTST, FCOMI) also for a QNaN
    (SDM 1:8.5.1.2 last paragraph); DE for a denormal operand (SDM 1:8.5.2).  An unsupported operand is unordered (the masked response
    of SDM 1:T8-10 is the condition code 111)."""
    ua, ub = is_unsup80(a), is_unsup80(b)
    unordered = Or(is_nan80(a), is_nan80(b), ua, ub)
    lt = z3.fpLT(fp80(a), fp80(b))
    eq = z3.fpEQ(fp80(a), fp80(b))       # +0 == -0 (IEEE), SDM 2A:FCOM Op 'ST(0) = SRC'
    c3 = Or(unordered, eq)
    c2 = unordered
    c0 = Or(unordered, And(Not(unordered), lt))
    ie = Or(is_snan80(a), is_snan80(b), ua, ub) if not ordered else unordered
    de = And(Not(unordered), Or(is_den80(a), is_den80(b)))
    return c3, c2, c0, ie, de


def eflags_of(c3, c2, c0):
    """SDM 1:T8-7: ZF, PF, CF <- C3, C2, C0 (greater 0,0,0; less 0,0,1; equal 1,0,0; unordered 1,1,1)."""
    return (c3, c2, c0)


# --- conversions memory format <-> register format -------------------------------------------
def from_m32(m):
    """m32fp (binary32 bits) -> register format.  Non-NaN: exact widening (SDM 1:8.5.2 'automatically
    normalized when converted'; binary64 holds every binary32 including denormals).  NaN: sign, quiet bit
    and payload shifted into the wider fraction (SDM 1:T4-3).  Returns (pattern, de, snan)."""
    s32, e32, f32 = Extract(31, 31, m), Extract(30, 23, m), Extract(22, 0, m)
    nan = And(e32 == K(0xFF, 8), f32 != K(0, 23))
    snan = And(nan, Extract(22, 22, m) == K(0, 1))
    wide = bits(z3.fpToFP(z3.RNE(), z3.fpBVToFP(m, F32), F64))
    nanw = Concat(s32, K(0x7FF, 11), f32, K(0, 29))
    de = And(e32 == K(0, 8), f32 != K(0, 23))          # SDM 1:8.5.2: loading a denormal single raises DE
    return If(nan, nanw, wide), de, snan


def from_m64(m):
    """m64fp -> register format: identical bit pattern (the register format is binary64, OWNER 8).
    DE for a denormal (SDM 1:8.5.2)."""
    return m, is_sub(m), is_snan(m)


def to_mem_fp(m, rm, nbits):
    """image -> m32fp (nbits = 32) or m64fp (nbits = 64), SDM 2A:FST Op 'DEST <- ST(0)' with the conversion to the destination format, rounded by rm
    (exact class: the 80-bit value is rounded once).  Returns (bits, ie, oe, ue, pe, roundup).  NaN: the sign and the top fraction bits, quieted
    (SDM 1:4.8.3.5), IE for an SNaN (SDM 2A:FST Exc '#IA If destination result is an SNaN value or unsupported format'); an unsupported
    encoding: IE and the QNaN indefinite of the format; infinity and zero keep their class."""
    eb, sb = (8, 24) if nbits == 32 else (11, 53)
    FS = z3.FPSort(eb, sb)
    nan, uns = is_nan80(m), is_unsup80(m)
    q = m | K(1 << 62, 80)
    nanb = Concat(i_sgn(m), K((1 << eb) - 1, eb), Extract(62, 62 - (sb - 2), q))
    indef = K(0xFFC00000, 32) if nbits == 32 else K(QNAN_INDEF, 64)
    f = fp80(m)
    r = z3.fpToFP(rm, f, FS)
    finite = Not(Or(nan, is_inf80(m), uns))
    exact = z3.fpToReal(f)
    rr = z3.fpToReal(r)
    inexact = And(finite, Not(z3.fpIsInf(r)), rr != exact)
    oe = And(finite, z3.fpIsInf(r))
    ub = z3.fpToReal(z3.fpToFP(rm, f, z3.FPSort(30, sb)))
    minn = z3.RealVal(1) / z3.RealVal(2 ** (126 if nbits == 32 else 1022))
    tiny = And(finite, ub != 0, abs_real(ub) < minn)                  # SDM 1:4.9.1.5: non-zero and tiny
    ue = And(tiny, inexact)
    roundup = And(finite, Not(z3.fpIsInf(r)), abs_real(rr) > abs_real(exact))
    out = If(uns, indef, If(nan, nanb, z3.fpToIEEEBV(r)))
    return out, Or(is_snan80(m), uns), oe, ue, Or(inexact, oe), roundup


def clz(x, n):
    """Count leading zeros of the n-bit vector x (n a power of two, x non-zero), as a n-bit vector."""
    cnt = K(0, n)
    step = n // 2
    while step >= 1:
        hi = Extract(n - 1, n - step, x)
        zero_hi = hi == K(0, step)
        x = If(zero_hi, x << K(step, n), x)
        cnt = If(zero_hi, cnt + K(step, n), cnt)
        step //= 2
    return cnt


def to_m80(b):
    """register format -> m80fp bit pattern (80-bit vector: sign, exponent 15, integer bit, fraction 63;
    SDM 1:T4-3).  Exact widening: biased exponent e64 - 1023 + 16383 (biases 1023 and 16383, SDM 1:4.2.2);
    a binary64 subnormal is normalised (the extended format has the exponent range)."""
    s, e, f = sgn(b), ex(b), fr(b)
    e15 = ZeroExt(4, e)
    frac63 = Concat(f, K(0, 11))
    # normal: integer bit 1
    norm = Concat(s, e15 - K(1023, 15) + K(16383, 15), K(1, 1), frac63)
    # zero
    zero = Concat(s, K(0, 15), K(0, 1), K(0, 63))
    # infinity / NaN: exponent all ones; integer bit 1 (SDM 1:T4-3: Infinity 1/00..00, QNaN 1/1x..x)
    spec = Concat(s, K(0x7FFF, 15), K(1, 1), frac63)
    # subnormal: value f * 2^-1074; leading zero count of the 64-bit significand f<<11 gives the exponent
    m64 = ZeroExt(12, f)
    lz = clz(m64, 64)
    # f occupies bits 51..0 of m64; the normalised significand has the leading one at bit 63
    # leading one position p = 63 - lz; value = f * 2^-1074 = 2^(p-1074) * 1.x; biased = p - 1074 + 16383
    p = K(63, 64) - lz
    e_sub = Extract(14, 0, p) - K(1074, 15) + K(16383, 15)
    sub = Concat(s, e_sub, Extract(63, 0, m64 << lz))
    return If(is_zero(b), zero, If(Or(is_inf(b), is_nan(b)), spec, If(is_sub(b), sub, norm)))


def int_to_reg(m, n, rm):
    """m (n-bit two's complement) -> register format.  SDM 2A:FILD Op: 'ST(0) := ConvertToDoubleExtendedPrecisionFP(SRC)'.
    16 and 32 bit values are exactly representable in binary64; 64 bit values are rounded by rm (OWNER 8)."""
    return bits(z3.fpSignedToFP(rm, m, F64))


def reg_to_int(m, n, rm, truncate=False):
    """image -> n-bit signed integer for FIST/FISTP/FISTTP (exact class).  Returns (value, ie, pe, roundup).
    SDM 2A:FIST Op 'DEST := Integer(ST(0))' rounded by RC; SDM 2A:FISTTP Op uses truncation.  Out of range,
    NaN, infinity or an unsupported format: integer indefinite 10..0 with IE (SDM 1:T8-10, 1:8.2.1, SDM 1:T4-1).  PE when the
    rounding lost a fraction; C1 = rounded up (SDM 1:8.1.3.2)."""
    rmu = z3.RTZ() if truncate else rm
    f = fp80(m)
    ri = z3.fpRoundToIntegral(rmu, f)
    lo = -(2 ** (n - 1))
    hi = 2 ** (n - 1) - 1
    rr = z3.fpToReal(ri)
    special = Or(is_nan80(m), is_inf80(m), is_unsup80(m))
    inrange = And(Not(special), rr >= lo, rr <= hi)
    val = z3.fpToSBV(z3.RTZ(), ri, z3.BitVecSort(n))
    indef = K(1 << (n - 1), n)
    exact = z3.fpToReal(f)
    pe = And(inrange, rr != exact)
    roundup = And(inrange, abs_real(rr) > abs_real(exact))
    return If(inrange, val, indef), Not(inrange), pe, roundup


def bcd_to_img(m):
    """m80 packed BCD (SDM 1:4.7: 18 digits in bytes 0..8, sign bit 79, bits 72..78 ignored) -> image, exact (SDM 2A:FBLD Op).  Digit d_i = bits
    4i+3..4i.  The integer is below 10^18 < 2^60."""
    v = K(0, 64)
    for i in range(17, -1, -1):
        d = ZeroExt(60, Extract(4 * i + 3, 4 * i, m))
        v = v * K(10, 64) + d
    return _mag_to_img(Extract(79, 79, m), v)          # SDM 2A:FBLD Op: sign from bit 79, '-0' keeps the sign


def bcd_digits_ok(m):
    """True when all 18 digits of the packed BCD m are decimal (SDM 1:4.7: a digit is 0..9)."""
    conds = [ULE(Extract(4 * i + 3, 4 * i, m), K(9, 4)) for i in range(18)]
    return And(*conds)


BCD_INDEFINITE = 0xFFFFC000000000000000   # SDM 1:T4-5, 'FFFFC000000000000000H'


def reg_to_bcd(m, rm):
    """image -> packed BCD for FBSTP (SDM 2A:FBSTP Op 'DEST := BCD(ST(0))'; exact class).  Returns
    (m80 pattern, ie, pe, roundup).  Value not representable in 18 digits, NaN, infinity or an unsupported format: the BCD
    indefinite and IE (SDM 1:T8-10)."""
    f = fp80(m)
    ri = z3.fpRoundToIntegral(rm, f)
    rr = z3.fpToReal(ri)
    special = Or(is_nan80(m), is_inf80(m), is_unsup80(m))
    limit = 10 ** 18 - 1
    ok = And(Not(special), rr >= -limit, rr <= limit)
    mag_i = z3.fpToSBV(z3.RTZ(), z3.fpAbs(ri), z3.BitVecSort(64))
    dig = []
    x = mag_i
    for i in range(18):
        dig.append(Extract(3, 0, z3.URem(x, K(10, 64))))
        x = z3.UDiv(x, K(10, 64))
    body = Concat(*[dig[i] for i in range(17, -1, -1)])                 # 72 bits, digit 17 first
    enc = Concat(i_sgn(m), K(0, 7), body)                               # 1 + 7 + 72 = 80
    indef = K(BCD_INDEFINITE, 80)
    exact = z3.fpToReal(f)
    return If(ok, enc, indef), Not(ok), And(ok, rr != exact), And(ok, abs_real(rr) > abs_real(exact))


# --------------------------------------------------------------------------------------------
# Part (ii).  One function per distinct instruction
# --------------------------------------------------------------------------------------------
TRUE = z3.BoolVal(True)
FALSE = z3.BoolVal(False)
UNDEF_C023 = K((1 << C0) | (1 << C2) | (1 << C3), 16)     # SDM 1:T8-1: 'Undefined' columns C0, C2, C3
UNDEF_C0123 = K((1 << C0) | (1 << C1) | (1 << C2) | (1 << C3), 16)


def fv(ie=FALSE, de=FALSE, ze=FALSE, oe=FALSE, ue=FALSE, pe=FALSE):
    """6-bit exception flag vector from Bools (SDM 1:8.1.3.3 bit positions)."""
    v = NOFLAGS
    for n, b in ((IE, ie), (DE, de), (ZE, ze), (OE, oe), (UE, ue), (PE, pe)):
        v = v | If(b, K(1 << n, 6), NOFLAGS)
    return v


def _c1(s, b):
    return s.copy(fsw=set_bit(s.fsw, C1, b))


def _uf_state(s, dst_idx, pop):
    """Masked stack underflow of an instruction with destination ST(dst_idx): IE, SF set, C1 = 0, the
    destination receives the QNaN floating-point indefinite (SDM 1:8.5.1.1, 1:T8-10); a P-form still pops
    (SDM 2A:FADD Op: 'IF Instruction = FADDP THEN PopRegisterStack')."""
    s2 = stack_fault_state(s, False)
    s2 = write_reg(s2, dst_idx, K(QNAN_INDEF, 64))
    return do_pop(s2) if pop else s2


# --- loads -------------------------------------------------------------------------------
def _push(s, img, flags=NOFLAGS, uf=FALSE):
    """Push the image img (TOP <- TOP-1; ST(0) <- img, SDM 2A:FLD Op).  Stack overflow (SDM 1:8.5.1.1: push onto a non-empty
    register) or, for FLD ST(i), underflow of the source: IE, SF, C1 = overflow; the masked response pushes
    the QNaN indefinite.  Both at once is not specified by the SDM (precondition)."""
    ov = push_target_nonempty(s)
    fault = Or(ov, uf)
    s_f = do_push_img(stack_fault_state(s, ov), K(INDEF80, 80))
    s_n = _c1(do_push_img(s, img), FALSE)
    o = finish(s, St.ite(fault, s_f, s_n), If(fault, fl(IE), flags), extra_pre=Not(And(ov, uf)))
    o.undef = UNDEF_C023
    return o


def fld_m32(s, m):
    """SDM 2A:FLD Op (memory operand), single precision.  #IA for an SNaN (converted to QNaN, SDM 1:T4-8),
    #D for a denormal (SDM 2A:FLD Exc)."""
    v, de, snan = from_m32(m)
    return _push(s, to_m80(If(snan, quieten(v), v)), fv(ie=snan, de=de))


def fld_m64(s, m):
    """SDM 2A:FLD Op (memory operand), double precision (the register format, OWNER 8)."""
    v, de, snan = from_m64(m)
    return _push(s, to_m80(If(snan, quieten(v), v)), fv(ie=snan, de=de))


def fld_m80(s, m):
    """SDM 2A:FLD Op (memory operand), double extended: the image is copied bit for bit (exact class); no #IA, no #D (SDM 2A:FLD Exc:
    '#IA Source operand is an SNaN value or unsupported format. Does not occur if the source operand is in double extended-precision
    floating-point format (FLD m80fp or FLD ST(i)).'), any encoding."""
    return _push(s, m)


def fld_sti(s, i):
    """SDM 2A:FLD Op: 'temp := ST(i)', 'TOP := TOP - 1', 'ST(0) := temp'; #IS when ST(i) is empty."""
    return _push(s, st80(s, i), uf=is_empty(s, i))


def _const(s, lo, nu, e):
    """Push the constant whose 64-bit significand rounds to lo (floor) or lo + 1 (nu: nearest is the upper one), with unbiased exponent e.
    SDM 1:8.3.4 'The constant values have full double extended precision floating-point precision (64 bits)... When loading the constant, the x87 FPU
    rounds the more precise internal constant according to the RC (rounding control) field of the x87 FPU control word. The inexact-result exception
    (#P) is not generated as a result of this rounding, nor is the C1 flag set'.  The class is therefore exact on the image: the constant rounded
    to a 64-bit significand under RC (no binary64 step, no OWNER 8 deviation)."""
    rc = rc_of(s.fcw)
    sig = If(rc == K(RC_NEAREST, 2), K(lo + nu, 64), If(rc == K(RC_UP, 2), K(lo + 1, 64), K(lo, 64)))   # RC_DOWN and RC_ZERO: the floor (the constant is positive)
    return _push(s, Concat(K(0, 1), K(e + 16383, 15), sig))


# Mathematical constants (SDM 2A:FLDL2T/FLDL2E/FLDPI/FLDLG2/FLDLN2 Description: log2 10, log2 e, pi, log10 2, loge 2) as rationals with
# 120 decimal digits, computed here from convergent series in integer arithmetic (no digit string is typed):
#   pi = 16 atan(1/5) - 4 atan(1/239) (Machin), ln 2 = 2 atanh(1/3), ln 10 = 3 ln 2 + 2 atanh(1/9) (ln(5/4) = 2 atanh(1/9)),
#   log2 e = 1/ln 2, log2 10 = ln 10/ln 2, log10 2 = ln 2/ln 10.
# Each series term is truncated, the error of every constant is below 10^-118 (guard digits 40, at most 200 terms of error 10^-160 each).
# The rounding of the constant to binary64 under every RC equals the rounding of the exact value iff no rounding boundary (a binary64 value or
# the midpoint of two) lies within 10^-118 of it; tests/test_x87spec.py checks that distance for each constant and compares the RNE result with
# the Python float constants (math.pi, ...), an independent source.
_DIG, _GUARD = 120, 40
_ONE = 10 ** (_DIG + _GUARD)


def _atan_inv(n):
    """atan(1/n) * _ONE: sum of (-1)^k / ((2k+1) n^(2k+1)), n >= 2"""
    x, k, sgn_, tot = _ONE // n, 0, 1, 0
    while x:
        tot += sgn_ * (x // (2 * k + 1))
        x //= n * n; k += 1; sgn_ = -sgn_
    return tot


def _atanh_inv(n):
    """atanh(1/n) * _ONE: sum of 1 / ((2k+1) n^(2k+1)), n >= 2"""
    x, k, tot = _ONE // n, 0, 0
    while x:
        tot += x // (2 * k + 1)
        x //= n * n; k += 1
    return tot


def _consts():
    pi = 16 * _atan_inv(5) - 4 * _atan_inv(239)
    ln2 = 2 * _atanh_inv(3)
    ln10 = 3 * ln2 + 2 * _atanh_inv(9)
    g = 10 ** _GUARD
    return {"pi": pi // g, "ln2": ln2 // g, "log2e": (_ONE * _ONE // ln2) // g, "log2_10": (ln10 * _ONE // ln2) // g,
            "lg2": (ln2 * _ONE // ln10) // g}


CONST_INT = _consts()                     # name -> floor(value * 10^120)


def _rat(name):
    return z3.RealVal(CONST_INT[name]) / z3.RealVal(10 ** _DIG)


L2T, L2E, PI_, LG2, LN2 = _rat("log2_10"), _rat("log2e"), _rat("pi"), _rat("lg2"), _rat("ln2")


def _sig64(name, e):
    """(lo, nu): the 64-bit significand floor of the constant name (value in [2^e, 2^(e+1))) and whether the nearest significand is lo + 1.
    The constant is CONST_INT / 10^120 with error below 10^-118; tests/test_x87spec.py checks that its distance to a rounding boundary exceeds that."""
    num, den = CONST_INT[name] * 2 ** (63 - e), 10 ** _DIG
    lo = num // den
    assert 2 ** 63 <= lo < 2 ** 64 and 2 * (num - lo * den) != den
    return lo, int(2 * (num - lo * den) > den)


def fld1(s):
    """SDM 2A:FLD1: push +1.0 (D9 E8)."""
    return _push(s, K(0x3FFF8000000000000000, 80))


def fldl2e(s):
    """SDM 2A:FLDL2E: push log2 e (D9 EA); the constant is rounded by FCW.RC, no #P, no C1 (SDM 2A:FLD1 Description)."""
    return _const(s, *_sig64("log2e", 0), 0)


def fldpi(s):
    """SDM 2A:FLDPI: push pi (D9 EB), rounding as FLDL2E."""
    return _const(s, *_sig64("pi", 1), 1)


def fldlg2(s):
    """SDM 2A:FLDLG2: push log10 2 (D9 EC), rounding as FLDL2E."""
    return _const(s, *_sig64("lg2", -2), -2)


def fldln2(s):
    """SDM 2A:FLDLN2: push loge 2 (D9 ED), rounding as FLDL2E."""
    return _const(s, *_sig64("ln2", -1), -1)


def fldz(s):
    """SDM 2A:FLDZ: push +0.0 (D9 EE)."""
    return _push(s, K(0, 80))


def fild(s, m, n):
    """SDM 2A:FILD Op: 'TOP := TOP - 1', 'ST(0) := ConvertToDoubleExtendedPrecisionFP(SRC)', n = 16, 32, 64."""
    return _push(s, int_to_img(m, n))


def fbld(s, m):
    """SDM 2A:FBLD Op: 'TOP := TOP - 1', 'ST(0) := ConvertToDoubleExtendedPrecisionFP(SRC)'.  A digit above 9 is
    outside SDM 1:4.7's packed-BCD format and the SDM gives no result for it (2A:FBLD Description: 'Attempting to load an
    invalid encoding produces an undefined result.'): the loaded image is the free variable
    `nd.fbld.img` (BitVec80, any image), with no precondition and no flag."""
    nd = z3.BitVec("nd.fbld.img", 80)
    o = _push(s, If(bcd_digits_ok(m), bcd_to_img(m), nd))
    o.nd = list(o.nd) + [nd]
    return o


# --- stores ---------------------------------------------------------------------------------
def _store(s, val, indef, flags, up, pop, abort_set):
    """Common tail of a store of ST(0): underflow (ST(0) empty) stores the masked indefinite (SDM 1:8.5.1.1);
    C1 = round-up (SDM 2A:FST Flags) or 0; the pop happens after the store (SDM 2A:FST Op)."""
    uf = is_empty(s, 0)
    s_ok = _c1(s, up)
    s_ok = do_pop(s_ok) if pop else s_ok
    s_uf = stack_fault_state(s, False)
    s_uf = do_pop(s_uf) if pop else s_uf
    o = finish(s, St.ite(uf, s_uf, s_ok), If(uf, fl(IE), flags), mem=If(uf, indef, val), abort_set=abort_set)
    o.undef = UNDEF_C023
    return o


def fst_m32(s, pop):
    """SDM 2A:FST/FSTP Op 'DEST := ST(0)' to m32fp (D9 /2, /3).  #IA SNaN, #O, #U, #P (SDM 2A:FST Exc);
    unmasked IE/OE/UE with a memory destination store nothing (SDM 1:8.5.4, 8.5.5)."""
    val, ie, oe, ue, pe, up = to_mem_fp(st80(s, 0), rm_of(s.fcw), 32)
    return _store(s, val, K(0xFFC00000, 32), fv(ie=ie, oe=oe, ue=ue, pe=pe), up, pop, fl(IE, OE, UE))


def fst_m64(s, pop):
    """SDM 2A:FST/FSTP Op to m64fp (DD /2, /3): the 80-bit value rounded once to binary64 by RC (exact class): #IA, #O, #U, #P as for m32fp."""
    val, ie, oe, ue, pe, up = to_mem_fp(st80(s, 0), rm_of(s.fcw), 64)
    return _store(s, val, K(QNAN_INDEF, 64), fv(ie=ie, oe=oe, ue=ue, pe=pe), up, pop, fl(IE, OE, UE))


def fstp_m80(s):
    """SDM 2A:FSTP Op to m80fp (DB /7): no exceptions other than #IS (SDM 2A:FST Exc: '#IA If destination result is an SNaN value or unsupported format, except when the destination format is in double extended-precision floating-point format.')."""
    return _store(s, st80(s, 0), K(INDEF80, 80), NOFLAGS, FALSE, True, fl(IE, OE, UE))


def fst_sti(s, i, pop):
    """SDM 2A:FST/FSTP Op 'DEST := ST(0)' to ST(i) (DD D0+i, DD D8+i); non-arithmetic (SDM 1:T8-9), so no #IA.
    Stack underflow when ST(0) is empty; the masked response writes the indefinite to ST(i)."""
    uf = is_empty(s, 0)
    s_ok = _c1(write_img(s, i, st80(s, 0)), FALSE)
    s_ok = do_pop(s_ok) if pop else s_ok
    s_uf = write_img(stack_fault_state(s, False), i, K(INDEF80, 80))
    s_uf = do_pop(s_uf) if pop else s_uf
    o = finish(s, St.ite(uf, s_uf, s_ok), If(uf, fl(IE), NOFLAGS))
    o.undef = UNDEF_C023
    return o


def fist(s, n, pop, truncate=False):
    """SDM 2A:FIST/FISTP Op 'DEST := Integer(ST(0))' (RC rounding) and SDM 2A:FISTTP Op (truncation), to an
    n-bit signed integer.  Out of range, NaN, infinity: the integer indefinite and #IA (SDM 1:T8-10)."""
    val, ie, pe, up = reg_to_int(st80(s, 0), n, rm_of(s.fcw), truncate)
    return _store(s, val, K(1 << (n - 1), n), fv(ie=ie, pe=pe), up, pop, fl(IE))


def fbstp(s):
    """SDM 2A:FBSTP Op 'DEST := BCD(ST(0)); PopRegisterStack'."""
    val, ie, pe, up = reg_to_bcd(st80(s, 0), rm_of(s.fcw))
    return _store(s, val, K(BCD_INDEFINITE, 80), fv(ie=ie, pe=pe), up, True, fl(IE))


# --- exchange, sign, free ---------------------------------------------------------------------
def fxch(s, i):
    """SDM 2A:FXCH Op: 'temp := ST(0); ST(0) := SRC; SRC := temp'.  One or both registers empty: they are
    loaded with the QNaN indefinite first, then exchanged (SDM 1:T8-10 row FXCH); #IS, C1 = 0."""
    a0, ai = st80(s, 0), st80(s, i)
    e0, ei = is_empty(s, 0), is_empty(s, i)
    uf = Or(e0, ei)
    a0 = If(e0, K(INDEF80, 80), a0)
    ai = If(ei, K(INDEF80, 80), ai)
    s2 = write_img(write_img(s, 0, ai), i, a0)
    s2 = _c1(s2, FALSE)
    s2 = St.ite(uf, stack_fault_state(s2, False), s2)
    o = finish(s, s2, If(uf, fl(IE), NOFLAGS))
    o.undef = UNDEF_C023
    return o


def _unary_nonarith(s, fn):
    """FCHS / FABS: non-arithmetic (SDM 1:T8-9), ST(0) := fn(ST(0)), C1 = 0, #IS only."""
    uf = is_empty(s, 0)
    s_ok = _c1(write_img(s, 0, fn(st80(s, 0))), FALSE)
    s_uf = write_img(stack_fault_state(s, False), 0, K(INDEF80, 80))
    o = finish(s, St.ite(uf, s_uf, s_ok), If(uf, fl(IE), NOFLAGS))
    o.undef = UNDEF_C023
    return o


def fchs(s):
    """SDM 2A:FCHS Op 'SignBit(ST(0)) := NOT (SignBit(ST(0)))' (D9 E0)."""
    return _unary_nonarith(s, lambda b: b ^ K(1 << 79, 80))


def fabs_(s):
    """SDM 2A:FABS Op 'ST(0) := |ST(0)|' (D9 E1)."""
    return _unary_nonarith(s, lambda b: b & K((1 << 79) - 1, 80))


def ffree(s, i):
    """SDM 2A:FFREE Op 'TAG(i) := 11B': sets the tag of the physical register of ST(i) to empty; the
    contents and TOP are not affected (DD C0+i)."""
    o = Out(set_tag_at(s, phys(s, i), K(TAG_EMPTY, 2)))
    o.undef = UNDEF_C0123
    return o


# --- compare family ----------------------------------------------------------------------------
def _compare(s, b, src_empty, ordered, pops, de_extra=FALSE, ie_extra=FALSE, eflags=False, src_snan=None):
    """FCOM/FUCOM/FICOM/FTST/FCOMI/FUCOMI (SDM 2A:FCOM Op, 2A:FCOMI Op).  The condition codes of SDM 1:T8-6
    (or ZF, PF, CF of SDM 1:T8-7) from compare(); C1 = 0 (SDM 2A:FCOM Flags); stack underflow (ST(0) or the
    source empty): IE, SF, C1 = 0 and C3/C2/C0 (or the status flags) are left undefined (SDM 2A:FCOM Flags
    lists them without a masked-underflow value)."""
    a = st80(s, 0)
    uf = Or(is_empty(s, 0), src_empty)
    c3, c2, c0, ie, de = compare(a, b, ordered)
    ie = Or(ie, ie_extra)
    de = Or(de, de_extra)
    s_ok = s.copy(fsw=cc(s.fsw, c0=c0, c1=FALSE, c2=c2, c3=c3)) if not eflags else _c1(s, FALSE)
    s_uf = stack_fault_state(s, False)
    s2 = St.ite(uf, s_uf, s_ok)
    for _ in range(pops):
        s2 = do_pop(s2)
    flags = If(uf, fl(IE), fv(ie=ie, de=de))
    o = finish(s, s2, flags)
    if eflags:
        abort = (flags & ~Extract(5, 0, s.fcw) & fl(IE, DE)) != K(0, 6)
        o.eflags = (c3, c2, c0, Not(abort), uf)
        o.undef = K(0, 16)
    else:
        o.undef = If(uf, UNDEF_C023, K(0, 16))
    return o


def fcom_m32(s, m, pop):
    """SDM 2A:FCOM/FCOMP Op, m32fp (D8 /2, /3): ordered compare."""
    v, de, snan = from_m32(m)
    return _compare(s, to_m80(v), FALSE, True, 1 if pop else 0, de_extra=de)


def fcom_m64(s, m, pop):
    """SDM 2A:FCOM/FCOMP Op, m64fp (DC /2, /3)."""
    v, de, snan = from_m64(m)
    return _compare(s, to_m80(v), FALSE, True, 1 if pop else 0, de_extra=de)


def fcom_sti(s, i, pop):
    """SDM 2A:FCOM/FCOMP Op, ST(i) (D8 D0+i, D8 D8+i)."""
    return _compare(s, st80(s, i), is_empty(s, i), True, 1 if pop else 0)


def fcompp(s):
    """SDM 2A:FCOMPP Op (DE D9): compare ST(0) with ST(1), pop twice."""
    return _compare(s, st80(s, 1), is_empty(s, 1), True, 2)


def fucom_sti(s, i, pop):
    """SDM 2A:FUCOM/FUCOMP Op (DD E0+i, DD E8+i): unordered compare, #IA only for SNaN."""
    return _compare(s, st80(s, i), is_empty(s, i), False, 1 if pop else 0)


def fucompp(s):
    """SDM 2A:FUCOMPP Op (DA E9): unordered compare ST(0), ST(1), pop twice."""
    return _compare(s, st80(s, 1), is_empty(s, 1), False, 2)


def fcomi_sti(s, i, pop, ordered):
    """SDM 2A:FCOMI/FCOMIP/FUCOMI/FUCOMIP Op (DB F0+i, DF F0+i, DB E8+i, DF E8+i): ZF, PF, CF <- SDM 1:T8-7;
    OF, SF, AF cleared (SDM 2A:FCOMI Description); C1 = 0."""
    return _compare(s, st80(s, i), is_empty(s, i), ordered, 1 if pop else 0, eflags=True)


def ficom(s, m, n, pop):
    """SDM 2A:FICOM/FICOMP Op (DA /2, /3 m32int; DE /2, /3 m16int): ST(0) compared with the integer converted
    to the register format (exact for 16 and 32 bits); ordered compare."""
    return _compare(s, int_to_img(m, n), FALSE, True, 1 if pop else 0)


def ftst(s):
    """SDM 2A:FTST Op (D9 E4): compare ST(0) with +0.0, ordered (SDM 1:T8-8 'TEST' constants)."""
    return _compare(s, K(0, 80), FALSE, True, 0)


def fxam(s):
    """SDM 2A:FXAM Op: 'C1 := sign bit of ST; CASE class: Unsupported 000, NaN 001, Normal 010, Infinity 011,
    Zero 100, Empty 101, Denormal 110 (C3, C2, C0)'.  The class of an empty register is Empty, whatever it
    holds.  Classified on the 80-bit image (exact class).  No exceptions."""
    v = st80(s, 0)
    e = is_empty(s, 0)
    cls = If(e, K(0b101, 3), If(is_unsup80(v), K(0b000, 3), If(is_nan80(v), K(0b001, 3), If(is_inf80(v), K(0b011, 3),
         If(is_zero80(v), K(0b100, 3), If(is_den80(v), K(0b110, 3), K(0b010, 3)))))))
    c3 = Extract(2, 2, cls) == K(1, 1)
    c2 = Extract(1, 1, cls) == K(1, 1)
    c0 = Extract(0, 0, cls) == K(1, 1)
    c1 = i_sgn(v) == K(1, 1)
    o = Out(s.copy(fsw=cc(s.fsw, c0=c0, c1=c1, c2=c2, c3=c3)))
    return o


# --- conditional moves ----------------------------------------------------------------------------
def fcmov(s, i, cond):
    """SDM 2A:FCMOVcc Op 'IF condition TRUE THEN ST(0) := ST(i)'; the source operand is ST(i) (SDM 2A:FCMOVcc
    Description 'The source operand is always in the ST(i) register and the destination operand is always
    ST(0)').  Condition true and ST(i) empty: stack underflow (SDM 2A:FCMOVcc Exc '#IS', SDM 1:8.5.1.1: IE, SF,
    C1 = 0), the masked response is the indefinite in ST(0).  Condition false and ST(i) empty: the SDM Operation
    reads ST(i) only inside THEN and lists #IS 'Stack underflow occurred' without saying whether a false
    condition counts, so both outcomes are admissible: the free Bool nd.fcmov.uf_false (underflow reported, or the
    instruction does nothing); a Form refines the spec by equalling it for one value.  An empty destination is not
    an underflow in the Operation (it is written)."""
    nd_uf = z3.Bool("nd.fcmov.uf_false")
    uf = And(is_empty(s, i), Or(cond, nd_uf))
    s_ok = _c1(St.ite(cond, write_img(s, 0, st80(s, i)), s), FALSE)
    s_uf = St.ite(cond, write_img(stack_fault_state(s, False), 0, K(INDEF80, 80)), stack_fault_state(s, False))
    o = finish(s, St.ite(uf, s_uf, s_ok), If(uf, fl(IE), NOFLAGS))
    o.undef = UNDEF_C023
    o.nd = [nd_uf]
    return o


def fcmov_cond(kind, ef):
    """EFLAGS conditions of SDM 2A:FCMOVcc: B CF=1, E ZF=1, BE CF=1 or ZF=1, U PF=1, NB CF=0, NE ZF=0,
    NBE CF=0 and ZF=0, NU PF=0.  ef = (ZF, PF, CF) as Bools."""
    zf, pf, cf = ef
    return {"b": cf, "e": zf, "be": Or(cf, zf), "u": pf, "nb": Not(cf), "ne": Not(zf),
            "nbe": And(Not(cf), Not(zf)), "nu": Not(pf)}[kind]


# --- basic arithmetic --------------------------------------------------------------------------
def _finite(b):
    return Not(Or(is_nan(b), is_inf(b)))


def arith2(x, y, op, fcw, u=FALSE):
    """x op y on register-format patterns, op in add, sub, mul, div (SDM 2A:FADD/FSUB/FMUL/FDIV Op), with the
    rounding of FCW.RC and FCW.PC.  Returns (result, flags6, roundup).
      NaN operand: SDM 1:T4-8 (x87 column), #IA for an SNaN;
      invalid: SDM 1:T8-10 rows add/sub (unlike/like infinities), mul (inf*0), div (inf/inf, 0/0) -> indefinite;
      divide by zero: SDM 1:8.5.3 / 1:T8-11: infinity signed by the xor of the signs;
      #D: a denormal operand (SDM 1:8.5.2), not reported together with #IA;
      #O, #U, #P: SDM 1:8.5.4-8.5.6 with the thresholds of the result precision (SDM 1:4.9.1.4, 4.9.1.5).
    Exact zero sums: +0, or -0 under round toward -inf (SDM 2A:FADD Description), which is IEEE and what z3
    gives.
    u: an operand image is an unsupported encoding (SDM 1:T8-3): the invalid operation, IE and the QNaN indefinite under the mask
    (SDM 2A:FADD Exc '#IA Operand is an SNaN value or unsupported format', SDM 1:T8-10); the junk value read from such an image is not used."""
    pc, rm = pc_of(fcw), rm_of(fcw)
    A, B = fp(x), fp(y)
    fn = {"add": z3.fpAdd, "sub": z3.fpSub, "mul": z3.fpMul, "div": z3.fpDiv}[op]
    nat = fn(rm, A, B)
    nat32 = z3.fpToFP(z3.RNE(), z3.fpToFP(rm, nat, F32), F64)      # PC = 00B: 24-bit significand, SDM 1:8.1.5.2
    xb = bits(If(pc == K(PC_SINGLE, 2), nat32, nat))
    nan_any = And(Or(is_nan(x), is_nan(y)), Not(u))
    nres, _ = nan_result2(x, y)
    if op == "add":
        inv = And(is_inf(x), is_inf(y), sgn(x) != sgn(y))
    elif op == "sub":
        inv = And(is_inf(x), is_inf(y), sgn(x) == sgn(y))
    elif op == "mul":
        inv = Or(And(is_inf(x), is_zero(y)), And(is_zero(x), is_inf(y)))
    else:
        inv = Or(And(is_inf(x), is_inf(y)), And(is_zero(x), is_zero(y)))
    inv = Or(inv, u)
    ie = Or(Or(is_snan(x), is_snan(y)), And(Not(nan_any), inv))
    de = And(Not(ie), Or(is_sub(x), is_sub(y)))
    ze = And(Not(nan_any), Not(u), op == "div", _finite(x), Not(is_zero(x)), is_zero(y)) if op == "div" else FALSE
    rx, ry = real(x), real(y)
    exact = {"add": rx + ry, "sub": rx - ry, "mul": rx * ry, "div": If(ry == 0, z3.RealVal(0), rx / ry)}[op]
    arith_ok = And(Not(nan_any), Not(inv), _finite(x), _finite(y), Not(ze))
    rr = z3.fpToReal(If(pc == K(PC_SINGLE, 2), nat32, nat))
    isinf_r = z3.fpIsInf(If(pc == K(PC_SINGLE, 2), nat32, nat))
    oe = And(arith_ok, isinf_r)
    inexact = And(arith_ok, Not(isinf_r), rr != exact)
    ub = If(pc == K(PC_SINGLE, 2), z3.fpToReal(z3.fpRealToFP(rm, exact, z3.FPSort(30, 24))),
            z3.fpToReal(z3.fpRealToFP(rm, exact, z3.FPSort(30, 53))))
    tiny = And(arith_ok, ub != 0, abs_real(ub) < min_normal(pc))
    ue = And(tiny, inexact)
    roundup = And(arith_ok, Not(isinf_r), abs_real(rr) > abs_real(exact))
    res = If(nan_any, nres, If(inv, K(QNAN_INDEF, 64), xb))
    return res, fv(ie=ie, de=de, ze=ze, oe=oe, ue=ue, pe=Or(inexact, oe)), roundup


def _arith(s, op, dst, src, src_empty, pop, rev, de_extra=FALSE, src_u=FALSE):
    """Arithmetic with destination ST(dst) and source value src: dest := dest op src (rev: src op dest).
    Stack underflow: the destination or a register source is empty (SDM 1:8.5.1.1)."""
    d = st(s, dst)
    x, y = (src, d) if rev else (d, src)
    r, f, up = arith2(x, y, op, s.fcw, Or(is_unsup80(st80(s, dst)), src_u))
    uf = Or(is_empty(s, dst), src_empty)
    s_ok = _c1(write_reg(s, dst, r), up)
    s_ok = do_pop(s_ok) if pop else s_ok
    s_f = _uf_state(s, dst, pop)
    o = finish(s, St.ite(uf, s_f, s_ok), If(uf, fl(IE), f | fv(de=de_extra)), ou_pre=True)
    o.undef = UNDEF_C023
    return o


def arith_m32(s, m, op, rev):
    """SDM 2A:FADD/FSUB/FSUBR/FMUL/FDIV/FDIVR m32fp (D8 /0,/1,/4../7): ST(0) := ST(0) op m32fp."""
    v, de, snan = from_m32(m)
    return _arith(s, op, 0, v, FALSE, False, rev, de_extra=de)


def arith_m64(s, m, op, rev):
    """The same, m64fp (DC /0,/1,/4../7)."""
    v, de, snan = from_m64(m)
    return _arith(s, op, 0, v, FALSE, False, rev, de_extra=de)


def arith_int(s, m, n, op, rev):
    """SDM 2A:FIADD/FISUB/FISUBR/FIMUL/FIDIV/FIDIVR (DA /0,/1,/4../7 m32int; DE /0,/1,/4../7 m16int):
    the integer is converted to the register format first (SDM 2A:FIADD Op); integer 0 is +0."""
    return _arith(s, op, 0, int_to_reg(m, n, rm_of(s.fcw)), FALSE, False, rev)


def arith_st0_sti(s, i, op, rev):
    """SDM 2A:FADD etc. ST(0), ST(i) (D8 C0+i ...): ST(0) := ST(0) op ST(i)."""
    o = _arith(s, op, 0, st(s, i), is_empty(s, i), False, rev, src_u=is_unsup80(st80(s, i)))
    o.nd = [NAN_TIE]
    return o


def arith_sti_st0(s, i, op, rev, pop):
    """SDM 2A:FADD etc. ST(i), ST(0) (DC C0+i ...) and the P forms (DE C0+i ...): ST(i) := ST(i) op ST(0),
    then PopRegisterStack for FADDP, FSUBP, FSUBRP, FMULP, FDIVP, FDIVRP."""
    o = _arith(s, op, i, st(s, 0), is_empty(s, 0), pop, rev, src_u=is_unsup80(st80(s, 0)))
    o.nd = [NAN_TIE]
    return o


def fsqrt(s):
    """SDM 2A:FSQRT Op 'ST(0) := SquareRoot(ST(0))' (D9 FA).  NaN: SDM 1:T4-8; negative operand except -0:
    IE and the indefinite (SDM 1:T8-10); -0 -> -0; +inf -> +inf.  PC rounding applies (SDM 1:8.1.5.2)."""
    b = st(s, 0)
    rm, pc = rm_of(s.fcw), pc_of(s.fcw)
    nat = z3.fpSqrt(rm, fp(b))
    nat32 = z3.fpToFP(z3.RNE(), z3.fpToFP(rm, nat, F32), F64)
    r = If(pc == K(PC_SINGLE, 2), nat32, nat)
    rb = bits(r)
    u = is_unsup80(st80(s, 0))               # an unsupported encoding: IE and the indefinite (SDM 2A:FSQRT Exc '#IA Source operand is an SNaN value or unsupported format.'; the negative operand: SDM 1:T8-10)
    neg = And(sgn(b) == K(1, 1), Not(is_zero(b)), Not(is_nan(b)))
    ie = Or(is_snan(b), neg, u)
    res = If(u, K(QNAN_INDEF, 64), If(is_nan(b), quieten(b), If(neg, K(QNAN_INDEF, 64), rb)))
    de = And(Not(ie), is_sub(b))
    ok = And(Not(is_nan(b)), Not(neg), _finite(b), Not(u))
    rr = z3.fpToReal(r)
    xr = real(b)
    inexact = And(ok, rr * rr != xr)
    up = And(ok, rr * rr > xr)
    uf = is_empty(s, 0)
    s_ok = _c1(write_reg(s, 0, res), up)
    o = finish(s, St.ite(uf, _uf_state(s, 0, False), s_ok), If(uf, fl(IE), fv(ie=ie, de=de, pe=inexact)),
               ou_pre=True)
    o.undef = UNDEF_C023
    return o


def frndint(s):
    """SDM 2A:FRNDINT Op 'ST(0) := RoundToIntegralValue(ST(0))' (D9 FC), exact class: the 80-bit value is rounded to an integer by RC; infinity
    unchanged; a NaN is quieted ('#IA Source operand is an SNaN value or unsupported format', an unsupported format gives the indefinite);
    #D 'Source operand is a denormal value'; #P when the value was not integral (SDM 2A:FRNDINT Exc); C1 = round-up."""
    m = st80(s, 0)
    rm = rm_of(s.fcw)
    f = fp80(m)
    ri = z3.fpRoundToIntegral(rm, f)
    uns, nan, inf = is_unsup80(m), is_nan80(m), is_inf80(m)
    ok = Not(Or(uns, nan, inf))
    r = If(uns, K(INDEF80, 80), If(nan, m | K(1 << 62, 80), If(inf, m, img80(ri))))
    rr, xr = z3.fpToReal(ri), z3.fpToReal(f)
    pe = And(ok, rr != xr)
    up = And(ok, abs_real(rr) > abs_real(xr))
    ie = Or(is_snan80(m), uns)
    uf = is_empty(s, 0)
    s_ok = _c1(write_img(s, 0, r), up)
    o = finish(s, St.ite(uf, _uf_state(s, 0, False), s_ok),
               If(uf, fl(IE), fv(ie=ie, de=And(Not(ie), is_den80(m)), pe=pe)))
    o.undef = UNDEF_C023
    return o


def pow2r(n):
    """2^n as a Real for the 16-bit signed vector n with |n| <= 4096, as a product over the bits of |n| (exact)."""
    mag = If(n < 0, -n, n)
    p = z3.RealVal(1)
    for j in range(13):
        p = p * If(Extract(j, j, mag) == K(1, 1), z3.RealVal(2 ** (2 ** j)), z3.RealVal(1))
    return If(n < 0, z3.RealVal(1) / p, p)


def fscale(s):
    """SDM 2A:FSCALE Op 'ST(0) := ST(0) * 2RoundTowardZero(ST(1))' (superscript flattened in the text) (D9 FD), special values of SDM 2A:FSCALE
    Table 1-30.  The scale is saturated at +-4096 (any |n| >= 2^12 overflows or underflows every finite
    non-zero binary64, SDM 1:8.5.4/8.5.5 'massive overflow')."""
    a, b = st(s, 0), st(s, 1)
    rm, pc = rm_of(s.fcw), pc_of(s.fcw)
    u = Or(is_unsup80(st80(s, 0)), is_unsup80(st80(s, 1)))
    nan_any = And(Or(is_nan(a), is_nan(b)), Not(u))
    nres, _ = nan_result2(a, b)
    # Table 1-30: columns ST(1) = -inf/+inf; rows ST(0) = +-0/+-inf
    inv = Or(And(is_inf(a), sgn(b) == K(1, 1), is_inf(b)), And(is_zero(a), is_inf(b), sgn(b) == K(0, 1)), u)
    inf_res = And(is_inf(a), Not(inv))                      # +-inf scaled by finite or +inf: unchanged
    zero_res = And(is_zero(a), Not(inv))                    # +-0 scaled by -inf or finite: unchanged
    b_posinf = And(is_inf(b), sgn(b) == K(0, 1))
    b_neginf = And(is_inf(b), sgn(b) == K(1, 1))
    fin_a = And(_finite(a), Not(is_zero(a)))
    n = z3.fpToSBV(z3.RTZ(), fp(b), z3.BitVecSort(32))
    nc = If(n > K(4096, 32), K(4096, 32), If(n < K(-4096, 32), K(-4096, 32), n))
    nsat = If(b_posinf, K(4096, 32), If(b_neginf, K(-4096, 32), nc))
    exact = real(a) * pow2r(Extract(15, 0, nsat))
    r64 = z3.fpRealToFP(rm, exact, F64)
    r32 = z3.fpToFP(z3.RNE(), z3.fpRealToFP(rm, exact, F32), F64)
    rf = If(pc == K(PC_SINGLE, 2), r32, r64)
    rbits = bits(rf)
    # a finite non-zero scaled to exactly zero keeps the sign of a
    rbits = If(z3.fpIsZero(rf), Concat(sgn(a), K(0, 63)), rbits)
    arith_ok = And(Not(nan_any), Not(inv), fin_a, _finite(b) | b_posinf | b_neginf)
    rr = z3.fpToReal(rf)
    isinf_r = z3.fpIsInf(rf)
    oe = And(arith_ok, Not(is_inf(b)), isinf_r)
    inexact = And(arith_ok, Not(is_inf(b)), Not(isinf_r), rr != exact)
    ub = z3.fpToReal(z3.fpRealToFP(rm, exact, z3.FPSort(30, 53)))
    ue = And(arith_ok, Not(is_inf(b)), ub != 0, abs_real(ub) < min_normal(pc), inexact)
    up = And(arith_ok, Not(isinf_r), abs_real(rr) > abs_real(exact))
    res = If(nan_any, nres, If(inv, K(QNAN_INDEF, 64), If(inf_res, a, If(zero_res, a,
         If(is_inf(b), If(b_posinf, Concat(sgn(a), K(0x7FF, 11), K(0, 52)), Concat(sgn(a), K(0, 63))), rbits)))))
    ie = Or(is_snan(a), is_snan(b), And(Not(nan_any), inv))
    de = And(Not(ie), Or(is_sub(a), is_sub(b)))
    uf = Or(is_empty(s, 0), is_empty(s, 1))
    s_ok = _c1(write_reg(s, 0, res), up)
    o = finish(s, St.ite(uf, _uf_state(s, 0, False), s_ok),
               If(uf, fl(IE), fv(ie=ie, de=de, oe=oe, ue=ue, pe=Or(inexact, oe))), ou_pre=True)
    o.undef = UNDEF_C023
    o.nd = [NAN_TIE]
    return o


def uexp(b):
    """Unbiased exponent of a non-zero binary64 pattern as a 16-bit signed vector: normal e - 1023,
    subnormal (leading one at bit p of the fraction) p - 1074 (SDM 1:8.2.2 treats a denormal's exponent as
    the extended-format normalisation of its significand)."""
    f = ZeroExt(12, fr(b))
    p = K(63, 64) - clz(f, 64)
    sub = Extract(15, 0, p) - K(1074, 16)
    nrm = ZeroExt(5, ex(b)) - K(1023, 16)
    return If(ex(b) == K(0, 11), sub, nrm)


def _prem(s, nearest):
    """FPREM (D9 F8) and FPREM1 (D9 F5), SDM 2A:FPREM Op, 2A:FPREM1 Op.
      D := exponent(ST(0)) - exponent(ST(1)); D < 64: Q := trunc (FPREM) or round-to-nearest-even (FPREM1)
      of ST(0)/ST(1); ST(0) := ST(0) - ST(1)*Q; C2 := 0; (C0, C3, C1) := (Q2, Q1, Q0).
      D >= 64: C2 := 1; QQ := trunc((ST(0)/ST(1)) / 2^(D-N)); ST(0) := ST(0) - ST(1)*QQ*2^(D-N).
    N is, in SDM 2A:FPREM Op, 'An implementation-dependent number between 32 and 63': the free 16-bit
    vector nd.prem.N with 32 <= N <= 63, so the spec holds for every D (the whole exponent range) and every admissible
    N; a Form refines it by equalling the spec for one N (a fixed N = 63 is such a Form).  (C0, C3, C1) are not
    assigned by the Operation when D >= 64, so they are listed undefined.  Result exact (SDM 2A:FPREM Description: no #P, RC no effect).
    Special values: SDM 2A:FPREM Table 1-27, 2A:FPREM1 Table 1-28.  Zero result takes the sign of the dividend."""
    a, b = st(s, 0), st(s, 1)
    u = Or(is_unsup80(st80(s, 0)), is_unsup80(st80(s, 1)))
    nan_any = And(Or(is_nan(a), is_nan(b)), Not(u))
    nres, _ = nan_result2(a, b)
    inv = Or(is_inf(a), is_zero(b), u)                    # dividend infinite or modulus zero, or an unsupported operand (SDM 2A:FPREM Exc '#IA Source operand is an SNaN value, modulus is 0, dividend is ∞, or unsupported format.')
    ie = Or(is_snan(a), is_snan(b), And(Not(nan_any), inv))
    de = And(Not(ie), Or(is_sub(a), is_sub(b)))
    passthru = And(Not(nan_any), Not(inv), Or(is_zero(a), is_inf(b)))     # result = ST(0)
    gen = And(Not(nan_any), Not(inv), Not(is_zero(a)), Not(is_inf(b)))
    ra, rb_ = real(a), real(b)
    q = If(rb_ == 0, z3.RealVal(1), ra / rb_)
    Dbv = If(Or(is_zero(a), is_zero(b)), K(0, 16), uexp(a) - uexp(b))     # D as a 16-bit signed vector (|D| <= 2100)
    big = Dbv >= K(64, 16)
    # integer part of q (toward zero) and nearest-even
    tz = If(q >= 0, z3.ToReal(z3.ToInt(q)), -z3.ToReal(z3.ToInt(-q)))
    h = q + z3.RealVal(1) / 2
    fl_h = z3.ToReal(z3.ToInt(h))
    tie = h == fl_h
    ne = If(And(tie, z3.IsInt(fl_h / 2) == False), fl_h - 1, fl_h)
    Q = ne if nearest else tz
    N = z3.BitVec("nd.prem.N", 16)
    sc = If(Dbv - N > K(0, 16), pow2r(Dbv - N), z3.RealVal(1))
    qq = If(q / sc >= 0, z3.ToReal(z3.ToInt(q / sc)), -z3.ToReal(z3.ToInt(-q / sc)))
    R = If(big, ra - rb_ * qq * sc, ra - rb_ * Q)
    rbits = bits(z3.fpRealToFP(z3.RNE(), R, F64))
    rbits = If(z3.fpIsZero(z3.fpRealToFP(z3.RNE(), R, F64)), Concat(sgn(a), K(0, 63)), rbits)
    res = If(nan_any, nres, If(inv, K(QNAN_INDEF, 64), If(passthru, a, rbits)))
    c2 = And(gen, big)
    qa = If(Q < 0, -Q, Q)
    qi = z3.ToInt(qa)
    q0 = z3.IsInt(qi / 2) == False if False else (qi % 2 == 1)
    q1 = (qi / 2) % 2 == 1
    q2 = (qi / 4) % 2 == 1
    complete = And(gen, Not(big))
    uf = Or(is_empty(s, 0), is_empty(s, 1))
    f = cc(s.fsw, c0=If(complete, q2, FALSE), c1=If(complete, q0, FALSE), c2=c2, c3=If(complete, q1, FALSE))
    s_ok = write_reg(s.copy(fsw=f), 0, res)
    o = finish(s, St.ite(uf, _uf_state(s, 0, False), s_ok), If(uf, fl(IE), fv(ie=ie, de=de)))
    o.undef = If(Or(uf, c2), UNDEF_C0123 & ~K(1 << C2, 16), K(0, 16))
    o.nd = [N, NAN_TIE]
    o.nd_pre = And(z3.UGE(N, K(32, 16)), z3.ULE(N, K(63, 16)))
    return o


def fprem(s):
    return _prem(s, False)


def fprem1(s):
    return _prem(s, True)


# --- transcendental functions -----------------------------------------------------------------
# The mathematical functions are declared, not computed: the SDM defines the instructions as
# approximations of them (SDM 2A:FSIN Op 'approximation of the mathematical sin function'); OWNER 8 asks for
# the correctly rounded value at the register type, i.e. round(f(x)) with FCW.RC.  Axioms are the exact
# values the SDM tables give.
_R = z3.RealSort()
SIN = z3.Function("x87_sin", _R, _R)
COS = z3.Function("x87_cos", _R, _R)
TAN = z3.Function("x87_tan", _R, _R)
EXP2M1 = z3.Function("x87_exp2m1", _R, _R)         # 2^x - 1
LOG2 = z3.Function("x87_log2", _R, _R)
ATAN2 = z3.Function("x87_atan2", _R, _R, _R)       # atan2(y, x)
ZR = z3.RealVal(0)
# AXIOMS: every one is a theorem of real analysis, so none stands in for an assumption (the lead's rule); the exact values at 0, 1 and -1
# are what the SDM tables list for them (SDM 2A:F2XM1 Table 1-12; SDM 2A:FSIN Table 1-31 +-0 -> +-0; SDM 2A:FCOS +-0 -> +1; SDM 2A:FPTAN Table 1-29;
# SDM 2A:FYL2X Table 1-44, ST(0) = 1 -> 0).
AXIOMS = [SIN(ZR) == 0,                                        # sin 0 = 0 (power series; the first term vanishes)
          COS(ZR) == 1,                                        # cos 0 = 1 (power series)
          TAN(ZR) == 0,                                        # tan 0 = sin 0 / cos 0 = 0
          EXP2M1(ZR) == 0,                                     # 2^0 - 1 = 0
          EXP2M1(z3.RealVal(1)) == 1,                          # 2^1 - 1 = 1
          EXP2M1(z3.RealVal(-1)) == z3.RealVal(-1) / 2,        # 2^-1 - 1 = -1/2
          LOG2(z3.RealVal(1)) == 0]                            # log2 1 = 0 (2^0 = 1)
TWO63 = z3.RealVal(2 ** 63)


def _tr_flags(rm, exact):
    """(pattern, overflow, UE, PE, round-up) of the exact real result rounded to the register format by rm
    (SDM 1:8.5.4-8.5.6; thresholds of binary64, OWNER 8).  PC is not consulted: SDM 1:8.1.5.2 lists the
    instructions PC affects, and the transcendental ones are not among them."""
    r = z3.fpRealToFP(rm, exact, F64)
    rr = z3.fpToReal(r)
    isinf_r = z3.fpIsInf(r)
    inexact = And(Not(isinf_r), rr != exact)
    ub = z3.fpToReal(z3.fpRealToFP(rm, exact, z3.FPSort(30, 53)))
    tiny = And(ub != 0, abs_real(ub) < min_normal(K(PC_DOUBLE, 2)))
    return bits(r), isinf_r, And(tiny, inexact), Or(inexact, isinf_r), And(Not(isinf_r), abs_real(rr) > abs_real(exact))


ONE64 = K(0x3FF0000000000000, 64)


def _trig(s, kind):
    """FSIN (D9 FE), FCOS (D9 FF), FPTAN (D9 F2), FSINCOS (D9 FB), SDM 2A:FSIN/FCOS/FPTAN/FSINCOS Op:
      IF |ST(0)| < 2^63 THEN C2 := 0; ST(0) := f(ST(0)) [FPTAN: TOP := TOP-1; ST(0) := 1.0;
      FSINCOS: TEMP := fcos(ST(0)); ST(0) := fsin(ST(0)); TOP := TOP-1; ST(0) := TEMP]
      ELSE C2 := 1 (ST(0) unchanged, no exception).
    Special values: SDM 2A:FSIN Table 1-31 (+-0 -> +-0, +-inf -> #IA, NaN -> NaN), 2A:FCOS Table 1-19 (+-0 -> +1),
    2A:FPTAN Table 1-29, 2A:FSINCOS Table 1-32; infinity: IE, the indefinite and C2 cleared (SDM 1:T8-10).
    The function is the exact mathematical one, correctly rounded (OWNER 8): the result is RN(f(x)) with f the
    shared symbol of this module.
    Out of range (|x| >= 2^63): C2 := 1, ST(0) unchanged, no exception, no push (SDM 2A:FPTAN Op ELSE branch,
    2A:FSINCOS Op, 2A:FSIN Description 'The instruction does not raise an exception when the source operand is out
    of range').  Infinity: #IA, masked response the QNaN indefinite (SDM 2A:FSIN Table 1-31, 2A:FPTAN Table 1-29,
    2A:FSINCOS Table 1-32 (*), SDM 2A:FPTAN Exc '#IA Source operand is an SNaN value, ∞, or unsupported format.').  NaN: the NaN rule
    (SDM 1:T4-8; the Tables give NaN -> NaN).  For FPTAN and FSINCOS the SDM Operation pushes only inside its
    'IF ST(0) < 263 THEN' (2^63, superscript flattened) and does not say whether a NaN or an infinity enters that branch, so both are admissible:
    the free Bool nd.trig.push; likewise C2 for those operands ('Set to 1 if outside range (-2^63 < source operand <
    +2^63); otherwise, set to 0', SDM 2A:FPTAN Flags: whether a NaN or an infinity is outside is not said): the free
    Bool nd.trig.c2 (a Form refines the spec by equalling it for one value of each).  When pushed, FPTAN pushes
    1.0 (SDM 2A:FPTAN Op) and FSINCOS pushes the cosine column of its Table 1-32 (the indefinite / the NaN)."""
    b = st(s, 0)
    rm = rm_of(s.fcw)
    x = real(b)
    pushes = kind in ("tan", "sincos")
    u = is_unsup80(st80(s, 0))                           # unsupported encoding: IE and the indefinite, like an infinity (SDM 2A:FPTAN Exc '#IA Source operand is an SNaN value, ∞, or unsupported format.')
    nanb, inf_in = And(is_nan(b), Not(u)), is_inf(b)
    special = Or(nanb, inf_in, u)
    big = And(_finite(b), abs_real(x) >= TWO63)
    zero_in = is_zero(b)
    f_main = {"sin": SIN(x), "cos": COS(x), "tan": TAN(x), "sincos": SIN(x)}[kind]
    mb, _, ue_m, pe_m, up_m = _tr_flags(rm, f_main)
    zero_val = {"sin": b, "cos": ONE64, "tan": b, "sincos": b}[kind]
    r0 = If(u, K(QNAN_INDEF, 64), If(nanb, quieten(b), If(inf_in, K(QNAN_INDEF, 64), If(big, b, If(zero_in, zero_val, mb)))))
    ie = Or(is_snan(b), u, And(Not(nanb), inf_in))
    de = And(Not(ie), is_sub(b))
    in_range = And(Not(special), Not(big))
    pe = And(in_range, Not(zero_in), pe_m)
    ue = And(in_range, Not(zero_in), ue_m)
    up = And(in_range, Not(zero_in), up_m)
    uf = is_empty(s, 0)
    nd_push, nd_c2 = z3.Bool("nd.trig.push"), z3.Bool("nd.trig.c2")
    push_here = If(special, nd_push, in_range) if pushes else FALSE
    ov = And(push_here, push_target_nonempty(s)) if pushes else FALSE
    fault = Or(uf, ov)
    s1 = write_reg(s, 0, r0)
    if pushes:
        cb, _, _, cpe, _ = _tr_flags(rm, COS(x))
        second = ONE64 if kind == "tan" else If(special, r0, If(zero_in, ONE64, cb))
        s1 = St.ite(push_here, do_push(s1, second), s1)
        if kind == "sincos":
            pe = Or(pe, And(in_range, Not(zero_in), cpe))
    s_ok = s1.copy(fsw=cc(s1.fsw, c1=up, c2=If(special, nd_c2, big)))
    s_f = stack_fault_state(s, ov)
    s_f = write_reg(s_f, 0, K(QNAN_INDEF, 64))
    if pushes:
        s_f = St.ite(ov, do_push(s_f, K(QNAN_INDEF, 64)), s_f)
    pre = Not(And(uf, ov))
    o = finish(s, St.ite(fault, s_f, s_ok), If(fault, fl(IE), fv(ie=ie, de=de, ue=ue, pe=pe)),
               ou_pre=True, extra_pre=pre)
    # SDM 2A:FSIN Flags: C0, C3 undefined; C1 round-up (undefined if C2 = 1 for FSIN/FCOS, SDM 2A:FCOS Flags); C2 range
    o.undef = K((1 << C0) | (1 << C3), 16)
    if kind in ("sin", "cos"):
        o.undef = o.undef | If(big, K(1 << C1, 16), K(0, 16))
    o.nd = [nd_push, nd_c2] if pushes else [nd_c2]
    return o


def fsin(s):
    return _trig(s, "sin")


def fcos(s):
    return _trig(s, "cos")


def fptan(s):
    return _trig(s, "tan")


def fsincos(s):
    return _trig(s, "sincos")


def f2xm1(s):
    """SDM 2A:F2XM1 Op 'ST(0) := (2^ST(0) - 1)' (D9 F0), SDM 2A:F2XM1 Table 1-12 (-1..-0 -> -0.5..-0, -0 -> -0,
    +0 -> +0, +0..+1 -> +0..+1).  Outside -1..+1 the SDM says the result is undefined (SDM 2A:F2XM1
    Description): the admissible choice nd.f2xm1.out, any 80-bit image, with the flags and exceptions the SDM gives for that operand (no
    precondition).  NaN: SDM 1:T4-8."""
    b = st(s, 0)
    rm = rm_of(s.fcw)
    x = real(b)
    inr = And(_finite(b), x >= -1, x <= 1)
    mb, _, ue_m, pe_m, up_m = _tr_flags(rm, EXP2M1(x))
    zb = is_zero(b)
    u = is_unsup80(st80(s, 0))
    r0 = If(u, K(QNAN_INDEF, 64), If(is_nan(b), quieten(b), If(zb, b, mb)))
    ie = Or(is_snan(b), u)
    de = And(Not(ie), is_sub(b))
    ok = And(_finite(b), Not(zb), Not(u))
    outr = And(Not(is_nan(b)), Not(inr), Not(u))     # an infinity is outside -1..+1 too
    nd_out = z3.BitVec("nd.f2xm1.out", 80)       # SDM 2A:F2XM1 Description: outside -1..+1 'the result is undefined': any 80-bit image (CERTS.md 'x87 admissible choices')
    uf = is_empty(s, 0)
    s_ok = _c1(write_img(s, 0, If(outr, nd_out, to_m80(r0))), And(ok, up_m))
    o = finish(s, St.ite(uf, _uf_state(s, 0, False), s_ok),
               If(uf, fl(IE), fv(ie=ie, de=de, ue=And(ok, ue_m), pe=And(ok, pe_m))), ou_pre=True,
               )
    o.undef = UNDEF_C023
    o.nd = [nd_out]
    return o


def fyl2x(s):
    """SDM 2A:FYL2X Op 'ST(1) := ST(1) * log2ST(0)', 'PopRegisterStack' (D9 F1), SDM 2A:FYL2X Table 1-44.
    Negative ST(0) (not -0): IE and the indefinite (SDM 2A:FYL2X Exc, SDM 1:T8-10); ST(0) = +-0: ZE and an
    infinity signed opposite to ST(1) (SDM 1:T8-11) when ST(1) is finite non-zero; the products of the
    special logarithms -inf (ST(0) = 0), 0 (ST(0) = 1) and +inf (ST(0) = +inf) with ST(1) follow SDM 1:T8-10
    (0 * inf is invalid) and IEEE signs."""
    x, y = st(s, 0), st(s, 1)
    rm = rm_of(s.fcw)
    u = Or(is_unsup80(st80(s, 0)), is_unsup80(st80(s, 1)))
    nan_any = And(Or(is_nan(x), is_nan(y)), Not(u))
    nres, _ = nan_result2(x, y)
    xr, yr = real(x), real(y)
    x_neg = And(sgn(x) == K(1, 1), Not(is_zero(x)), Not(is_nan(x)))
    x_one = And(_finite(x), xr == 1)
    x_posinf = And(is_inf(x), sgn(x) == K(0, 1))
    neginf = z3.fpMinusInfinity(F64)
    posinf = z3.fpPlusInfinity(F64)
    lsp = If(is_zero(x), neginf, If(x_posinf, posinf, z3.fpZero(F64, False)))
    prod = z3.fpMul(z3.RNE(), fp(y), lsp)
    special = Or(is_zero(x), x_posinf, x_one)
    inv_sp = And(special, Not(nan_any), z3.fpIsNaN(prod))
    ze = And(Not(nan_any), Not(u), is_zero(x), _finite(y), Not(is_zero(y)))
    generic = And(Not(nan_any), Not(u), Not(x_neg), Not(special))
    exact = yr * LOG2(xr)
    gb, goe, gue, gpe, gup = _tr_flags(rm, exact)
    x_lt1 = xr < 1
    gzero = Concat(sgn(y), K(0, 63)) ^ If(x_lt1, K(1 << 63, 64), K(0, 64))
    # ST(1) infinite and ST(0) finite, positive, not 1 (Table 1-44 columns +0<+F<+1 and +F>+1): an exact infinity, no flags;
    # the sign is that of ST(1), flipped when log2(ST(0)) < 0 (xr < 1).  (yr of an infinity is not a real: it is never used for it.)
    yinf = is_inf(y)
    ginf = Concat(sgn(y), K(0x7FF, 11), K(0, 52)) ^ If(x_lt1, K(1 << 63, 64), K(0, 64))
    gres = If(is_zero(y), gzero, If(yinf, ginf, gb))
    res = If(u, K(QNAN_INDEF, 64), If(nan_any, nres, If(x_neg, K(QNAN_INDEF, 64), If(inv_sp, K(QNAN_INDEF, 64),
          If(special, bits(prod), gres)))))
    ie = Or(is_snan(x), is_snan(y), u, And(Not(nan_any), Or(x_neg, inv_sp)))
    de = And(Not(ie), Or(is_sub(x), is_sub(y)))
    gen_nz = And(generic, Not(is_zero(y)), Not(yinf))
    uf = Or(is_empty(s, 0), is_empty(s, 1))
    s_ok = do_pop(_c1(write_reg(s, 1, res), And(gen_nz, gup)))
    s_f = do_pop(_uf_state(s, 1, False))
    o = finish(s, St.ite(uf, s_f, s_ok),
               If(uf, fl(IE), fv(ie=ie, de=de, ze=ze, oe=And(gen_nz, goe), ue=And(gen_nz, gue), pe=And(gen_nz, gpe))),
               ou_pre=True)
    o.undef = UNDEF_C023
    o.nd = [NAN_TIE]
    return o


def fpatan(s):
    """SDM 2A:FPATAN Op 'ST(1) := arctan(ST(1) / ST(0))', 'PopRegisterStack' (D9 F3), special values SDM 2A:FPATAN
    Table 1-26: the angle of the point (X = ST(0), Y = ST(1)), i.e. atan2(Y, X): +-0 or +-pi on the X axis by the
    sign of X, +-pi/2 for X = +-0, +-pi/4 and +-3pi/4 for two infinities; no invalid operation except SNaN.
    The constants are the exact pi multiples, rounded by RC."""
    x, y = st(s, 0), st(s, 1)
    rm = rm_of(s.fcw)
    u = Or(is_unsup80(st80(s, 0)), is_unsup80(st80(s, 1)))
    nan_any = And(Or(is_nan(x), is_nan(y)), Not(u))
    nres, _ = nan_result2(x, y)
    sy = If(sgn(y) == K(1, 1), z3.RealVal(-1), z3.RealVal(1))
    xneg = sgn(x) == K(1, 1)
    y_zero, y_inf = is_zero(y), is_inf(y)
    x_zero, x_inf = is_zero(x), is_inf(x)
    zero_case = Or(And(y_zero, Not(xneg)), And(_finite(y), Not(y_zero), x_inf, Not(xneg)))
    uns = If(y_zero, If(xneg, PI_, ZR),
          If(y_inf, If(x_inf, If(xneg, 3 * PI_ / 4, PI_ / 4), PI_ / 2),
          If(x_zero, PI_ / 2, If(x_inf, If(xneg, PI_, ZR), ZR))))
    generic = And(_finite(x), Not(x_zero), _finite(y), Not(y_zero))
    exact = If(generic, ATAN2(real(y), real(x)), sy * uns)
    rb, oe_, ue_, pe_, up_ = _tr_flags(rm, exact)
    res = If(u, K(QNAN_INDEF, 64), If(nan_any, nres, If(zero_case, Concat(sgn(y), K(0, 63)), rb)))
    ie = Or(is_snan(x), is_snan(y), u)
    de = And(Not(ie), Or(is_sub(x), is_sub(y)))
    live = And(Not(nan_any), Not(u), Not(zero_case))
    uf = Or(is_empty(s, 0), is_empty(s, 1))
    s_ok = do_pop(_c1(write_reg(s, 1, res), And(live, up_)))
    s_f = do_pop(_uf_state(s, 1, False))
    o = finish(s, St.ite(uf, s_f, s_ok), If(uf, fl(IE), fv(ie=ie, de=de, ue=And(live, ue_), pe=And(live, pe_))),
               ou_pre=True)
    o.undef = UNDEF_C023
    o.nd = [NAN_TIE]
    return o


# --- control instructions -------------------------------------------------------------------------
def fwait(s):
    """SDM 2D:FWAIT: 'Causes the processor to check for and handle pending, unmasked, floating-point exceptions before proceeding.'; no state change.  The #MF is the registry
    wrapper's trap (all waiting instructions)."""
    return Out(s)


def fnclex(s):
    """SDM 2A:FNCLEX Op 'FPUStatusWord[0:7] := 0; FPUStatusWord[15] := 0' (DB E2); C0..C3 undefined."""
    f = s.fsw & K(0x7F00, 16)
    f = f & ~K(0x00FF, 16)
    o = Out(s.copy(fsw=f))
    o.undef = UNDEF_C0123
    return o


def fldcw(s, m):
    """SDM 2A:FLDCW Op 'FPUControlWord := SRC' (D9 /5); ES and B are recomputed because the new mask can
    unmask a pending exception (SDM 2A:FLDCW Exc, SDM 1:8.1.3.3); C0..C3 undefined."""
    s2 = s.copy(fcw=m)
    s2 = s2.copy(fsw=sync_es(s2.fsw, s2.fcw))
    o = Out(s2)
    o.undef = UNDEF_C0123
    return o


def fnstcw(s):
    """SDM 2A:FNSTCW Op 'DEST := FPUControlWord' (D9 /7)."""
    o = Out(s, mem=s.fcw, wr=TRUE)
    o.undef = UNDEF_C0123
    return o


def fnstsw_m16(s):
    """SDM 2A:FNSTSW Op 'DEST := FPUStatusWord' to m2byte (DD /7)."""
    o = Out(s, mem=s.fsw, wr=TRUE)
    o.undef = UNDEF_C0123
    return o


def fnstsw_ax(s):
    """SDM 2A:FNSTSW Op 'DEST := FPUStatusWord' with DEST = AX (DF E0)."""
    o = Out(s, ax=s.fsw)
    o.undef = UNDEF_C0123
    return o


# --- environment and state images (SDM 1:8.1.10, Figure 8-9: 32-bit protected mode format) -------------
ENV_BITS = 224
ENV_RESERVED = ((0xFFFF << 16) | (0xFFFF << 48) | (0xFFFF << 80) | (0x1F << 155) | (0xFFFF << 208))
# image layout: byte 0 FCW (+16 reserved), 4 FSW, 8 FTW, 12 FIP, 16 FCS (bits 0..15) and FOP (bits 16..26),
# 20 FDP, 24 FDS (+16 reserved)
SAVE_BITS = ENV_BITS + 8 * 80


def _tagword_image(s):
    """16-bit tag word as FNSTENV stores it (SDM 1:8.1.7: the tag of an occupied register is the class of its
    content: 00 valid, 01 zero, 10 special; empty 11)."""
    parts = []
    for p in range(7, -1, -1):
        t = tag_at(s, K(p, 3))
        cls = tag_of_img(z3.Select(s.R, K(p, 3)))
        parts.append(If(t == K(TAG_EMPTY, 2), K(TAG_EMPTY, 2), cls))
    return Concat(*parts)


def env_image(s):
    def at(v, w, off):
        return ZeroExt(ENV_BITS - w, v) << K(off, ENV_BITS)
    return (at(s.fcw, 16, 0) | at(s.fsw, 16, 32) | at(_tagword_image(s), 16, 64) | at(s.fip, 32, 96)
            | at(s.fcs, 16, 128) | at(s.fop, 11, 144) | at(s.fdp, 32, 160) | at(s.fds, 16, 192))


def _env_fields(m):
    return dict(fcw=Extract(15, 0, m), fsw=Extract(47, 32, m), tagimg=Extract(79, 64, m), fip=Extract(127, 96, m),
                fcs=Extract(143, 128, m), fop=Extract(154, 144, m), fdp=Extract(191, 160, m),
                fds=Extract(207, 192, m))


def _load_env(s, m, regs=None):
    """Set the control, status, pointer registers from the image m.  The tags are used only for empty versus
    non-empty (SDM 1:8.1.7 'FLDENV/FRSTOR'); a non-empty register gets the class of its content."""
    f = _env_fields(m)
    s2 = s.copy(fcw=f["fcw"], fsw=f["fsw"], fip=f["fip"], fcs=f["fcs"], fop=f["fop"], fdp=f["fdp"], fds=f["fds"])
    if regs is not None:
        s2 = s2.copy(R=regs)
    tag = K(0, 16)
    for p in range(8):
        t = Extract(2 * p + 1, 2 * p, f["tagimg"])
        cls = tag_of_img(z3.Select(s2.R, K(p, 3)))
        tag = tag | (ZeroExt(14, If(t == K(TAG_EMPTY, 2), K(TAG_EMPTY, 2), cls)) << K(2 * p, 16))
    s2 = s2.copy(tag=tag)
    return s2.copy(fsw=sync_es(s2.fsw, s2.fcw))


def fldenv(s, m):
    """SDM 2A:FLDENV (D9 /4): loads the whole operating environment (control, status, tag, pointers, opcode) from
    the 28-byte image; the data registers are unchanged; C0..C3 are loaded from the image (SDM 1:T8-1)."""
    return Out(_load_env(s, m))


def fnstenv(s):
    """SDM 2A:FNSTENV Op (D9 /6): the image of control, status, tag word, data pointer, instruction pointer and
    opcode is stored, then all exceptions are masked (SDM 2A:FSTENV Description).  C0..C3 undefined.  The
    reserved bits of the image are not modelled (mem_undef)."""
    img = env_image(s)
    s2 = s.copy(fcw=s.fcw | K(MASK_ALL, 16))
    s2 = s2.copy(fsw=sync_es(s2.fsw, s2.fcw))
    o = Out(s2, mem=img, wr=TRUE, mem_undef=K(ENV_RESERVED, ENV_BITS))
    o.undef = UNDEF_C0123
    return o


def fnsave(s):
    """SDM 2A:FNSAVE Op (DD /6): the environment image followed by ST(0)..ST(7) as m80fp, then the FPU is
    initialised: FCW 037FH, FSW 0, tags FFFFH, pointers 0 (SDM 2A:FINIT Op).  The C flags are saved and
    cleared."""
    img = ZeroExt(SAVE_BITS - ENV_BITS, env_image(s))
    for i in range(8):
        img = img | (ZeroExt(SAVE_BITS - 80, st80(s, i)) << K(ENV_BITS + 80 * i, SAVE_BITS))
    o = Out(St.after_finit(s.R), mem=img, wr=TRUE,
            mem_undef=K(ENV_RESERVED, SAVE_BITS))
    return o


def frstor(s, m):
    """SDM 2A:FRSTOR Op (DD /4): the environment, then ST(0)..ST(7) from the 80-byte area following the 28-byte
    image (physical register of ST(i) from the TOP of the restored status word), each image bit for bit (exact class, any encoding)."""
    f = _env_fields(m)
    top_new = top_of(f["fsw"])
    r = s.R
    for i in range(8):
        r = z3.Store(r, top_new + K(i, 3), Extract(ENV_BITS + 80 * i + 79, ENV_BITS + 80 * i, m))
    return Out(_load_env(s, Extract(ENV_BITS - 1, 0, m), regs=r))


# --------------------------------------------------------------------------------------------
# The registry: one entry per distinct instruction of the pack universe (journal/inbox/l3b-w9-x87-list.md)
# Key: (opcode byte hex, 'mem'|'reg'|'-', ModRM.reg, ModRM.rm where it selects the instruction else None)
# --------------------------------------------------------------------------------------------
NONWAITING = {"fnclex", "fnstcw", "fnstsw m16", "fnstsw ax", "fnstenv", "fnsave"}   # SDM 1:8.7.1


def _mk():
    R = {}

    def add(opc, kind, reg, rm, name, memw, fn, uses_i=False, uses_ef=False):
        R[(opc, kind, reg, rm)] = dict(name=name, mem_bits=memw, fn=fn, uses_i=uses_i, uses_ef=uses_ef)

    add("9b", "-", None, None, "fwait", None, lambda s, m, i, ef: fwait(s))
    ops = [("add", False), ("mul", False), (None, None), (None, None), ("sub", False), ("sub", True),
           ("div", False), ("div", True)]
    names = ["fadd", "fmul", "fcom", "fcomp", "fsub", "fsubr", "fdiv", "fdivr"]
    for r in range(8):
        op, rev = ops[r]
        # D8 memory m32fp, DC memory m64fp
        if op:
            add("d8", "mem", r, None, names[r] + " m32fp", 32, (lambda op, rev: lambda s, m, i, ef: arith_m32(s, m, op, rev))(op, rev))
            add("dc", "mem", r, None, names[r] + " m64fp", 64, (lambda op, rev: lambda s, m, i, ef: arith_m64(s, m, op, rev))(op, rev))
            add("d8", "reg", r, None, names[r] + " st,st(i)", None,
                (lambda op, rev: lambda s, m, i, ef: arith_st0_sti(s, i, op, rev))(op, rev), uses_i=True)
        else:
            pop = (r == 3)
            add("d8", "mem", r, None, names[r] + " m32fp", 32, (lambda pop: lambda s, m, i, ef: fcom_m32(s, m, pop))(pop))
            add("dc", "mem", r, None, names[r] + " m64fp", 64, (lambda pop: lambda s, m, i, ef: fcom_m64(s, m, pop))(pop))
            add("d8", "reg", r, None, names[r] + " st(i)", None,
                (lambda pop: lambda s, m, i, ef: fcom_sti(s, i, pop))(pop), uses_i=True)
        # DA memory m32int, DE memory m16int
        inames = ["fiadd", "fimul", "ficom", "ficomp", "fisub", "fisubr", "fidiv", "fidivr"]
        if op:
            add("da", "mem", r, None, inames[r] + " m32int", 32, (lambda op, rev: lambda s, m, i, ef: arith_int(s, m, 32, op, rev))(op, rev))
            add("de", "mem", r, None, inames[r] + " m16int", 16, (lambda op, rev: lambda s, m, i, ef: arith_int(s, m, 16, op, rev))(op, rev))
        else:
            pop = (r == 3)
            add("da", "mem", r, None, inames[r] + " m32int", 32, (lambda pop: lambda s, m, i, ef: ficom(s, m, 32, pop))(pop))
            add("de", "mem", r, None, inames[r] + " m16int", 16, (lambda pop: lambda s, m, i, ef: ficom(s, m, 16, pop))(pop))
    # DC register forms: SDM places FSUBR at /4 (E0), FSUB at /5 (E8), FDIVR /6 (F0), FDIV /7 (F8)
    dc = {0: ("fadd", "add", False), 1: ("fmul", "mul", False), 4: ("fsubr", "sub", True), 5: ("fsub", "sub", False),
          6: ("fdivr", "div", True), 7: ("fdiv", "div", False)}
    for r, (n, op, rev) in dc.items():
        add("dc", "reg", r, None, n + " st(i),st", None,
            (lambda op, rev: lambda s, m, i, ef: arith_sti_st0(s, i, op, rev, False))(op, rev), uses_i=True)
        add("de", "reg", r, None, n + "p st(i),st", None,
            (lambda op, rev: lambda s, m, i, ef: arith_sti_st0(s, i, op, rev, True))(op, rev), uses_i=True)
    # D9
    add("d9", "mem", 0, None, "fld m32fp", 32, lambda s, m, i, ef: fld_m32(s, m))
    add("d9", "mem", 2, None, "fst m32fp", 32, lambda s, m, i, ef: fst_m32(s, False))
    add("d9", "mem", 3, None, "fstp m32fp", 32, lambda s, m, i, ef: fst_m32(s, True))
    add("d9", "mem", 4, None, "fldenv", ENV_BITS, lambda s, m, i, ef: fldenv(s, m))
    add("d9", "mem", 5, None, "fldcw", 16, lambda s, m, i, ef: fldcw(s, m))
    add("d9", "mem", 6, None, "fnstenv", ENV_BITS, lambda s, m, i, ef: fnstenv(s))
    add("d9", "mem", 7, None, "fnstcw", 16, lambda s, m, i, ef: fnstcw(s))
    add("d9", "reg", 0, None, "fld st(i)", None, lambda s, m, i, ef: fld_sti(s, i), uses_i=True)
    add("d9", "reg", 1, None, "fxch st(i)", None, lambda s, m, i, ef: fxch(s, i), uses_i=True)
    add("d9", "reg", 4, 0, "fchs", None, lambda s, m, i, ef: fchs(s))
    add("d9", "reg", 4, 1, "fabs", None, lambda s, m, i, ef: fabs_(s))
    add("d9", "reg", 4, 4, "ftst", None, lambda s, m, i, ef: ftst(s))
    add("d9", "reg", 4, 5, "fxam", None, lambda s, m, i, ef: fxam(s))
    add("d9", "reg", 5, 0, "fld1", None, lambda s, m, i, ef: fld1(s))
    add("d9", "reg", 5, 2, "fldl2e", None, lambda s, m, i, ef: fldl2e(s))
    add("d9", "reg", 5, 3, "fldpi", None, lambda s, m, i, ef: fldpi(s))
    add("d9", "reg", 5, 4, "fldlg2", None, lambda s, m, i, ef: fldlg2(s))
    add("d9", "reg", 5, 5, "fldln2", None, lambda s, m, i, ef: fldln2(s))
    add("d9", "reg", 5, 6, "fldz", None, lambda s, m, i, ef: fldz(s))
    add("d9", "reg", 6, 0, "f2xm1", None, lambda s, m, i, ef: f2xm1(s))
    add("d9", "reg", 6, 1, "fyl2x", None, lambda s, m, i, ef: fyl2x(s))
    add("d9", "reg", 6, 2, "fptan", None, lambda s, m, i, ef: fptan(s))
    add("d9", "reg", 6, 3, "fpatan", None, lambda s, m, i, ef: fpatan(s))
    add("d9", "reg", 6, 5, "fprem1", None, lambda s, m, i, ef: fprem1(s))
    add("d9", "reg", 7, 0, "fprem", None, lambda s, m, i, ef: fprem(s))
    add("d9", "reg", 7, 2, "fsqrt", None, lambda s, m, i, ef: fsqrt(s))
    add("d9", "reg", 7, 3, "fsincos", None, lambda s, m, i, ef: fsincos(s))
    add("d9", "reg", 7, 4, "frndint", None, lambda s, m, i, ef: frndint(s))
    add("d9", "reg", 7, 5, "fscale", None, lambda s, m, i, ef: fscale(s))
    add("d9", "reg", 7, 6, "fsin", None, lambda s, m, i, ef: fsin(s))
    add("d9", "reg", 7, 7, "fcos", None, lambda s, m, i, ef: fcos(s))
    # DA / DB register forms
    add("da", "reg", 3, None, "fcmovu", None, lambda s, m, i, ef: fcmov(s, i, fcmov_cond("u", ef)), True, True)
    add("da", "reg", 5, 1, "fucompp", None, lambda s, m, i, ef: fucompp(s))
    add("db", "reg", 0, None, "fcmovnb", None, lambda s, m, i, ef: fcmov(s, i, fcmov_cond("nb", ef)), True, True)
    add("db", "reg", 3, None, "fcmovnu", None, lambda s, m, i, ef: fcmov(s, i, fcmov_cond("nu", ef)), True, True)
    add("db", "reg", 4, 2, "fnclex", None, lambda s, m, i, ef: fnclex(s))
    add("db", "reg", 5, None, "fucomi st,st(i)", None, lambda s, m, i, ef: fcomi_sti(s, i, False, False), True)
    add("db", "reg", 6, None, "fcomi st,st(i)", None, lambda s, m, i, ef: fcomi_sti(s, i, False, True), True)
    # DB memory
    add("db", "mem", 0, None, "fild m32int", 32, lambda s, m, i, ef: fild(s, m, 32))
    add("db", "mem", 2, None, "fist m32int", 32, lambda s, m, i, ef: fist(s, 32, False))
    add("db", "mem", 3, None, "fistp m32int", 32, lambda s, m, i, ef: fist(s, 32, True))
    add("db", "mem", 5, None, "fld m80fp", 80, lambda s, m, i, ef: fld_m80(s, m))
    add("db", "mem", 7, None, "fstp m80fp", 80, lambda s, m, i, ef: fstp_m80(s))
    # DD
    add("dd", "mem", 0, None, "fld m64fp", 64, lambda s, m, i, ef: fld_m64(s, m))
    add("dd", "mem", 2, None, "fst m64fp", 64, lambda s, m, i, ef: fst_m64(s, False))
    add("dd", "mem", 3, None, "fstp m64fp", 64, lambda s, m, i, ef: fst_m64(s, True))
    add("dd", "mem", 4, None, "frstor", SAVE_BITS, lambda s, m, i, ef: frstor(s, m))
    add("dd", "mem", 6, None, "fnsave", SAVE_BITS, lambda s, m, i, ef: fnsave(s))
    add("dd", "mem", 7, None, "fnstsw m16", 16, lambda s, m, i, ef: fnstsw_m16(s))
    add("dd", "reg", 0, None, "ffree st(i)", None, lambda s, m, i, ef: ffree(s, i), True)
    add("dd", "reg", 2, None, "fst st(i)", None, lambda s, m, i, ef: fst_sti(s, i, False), True)
    add("dd", "reg", 3, None, "fstp st(i)", None, lambda s, m, i, ef: fst_sti(s, i, True), True)
    add("dd", "reg", 4, None, "fucom st(i)", None, lambda s, m, i, ef: fucom_sti(s, i, False), True)
    add("dd", "reg", 5, None, "fucomp st(i)", None, lambda s, m, i, ef: fucom_sti(s, i, True), True)
    # DE
    add("de", "reg", 3, 1, "fcompp", None, lambda s, m, i, ef: fcompp(s))
    # DF
    add("df", "mem", 0, None, "fild m16int", 16, lambda s, m, i, ef: fild(s, m, 16))
    add("df", "mem", 2, None, "fist m16int", 16, lambda s, m, i, ef: fist(s, 16, False))
    add("df", "mem", 3, None, "fistp m16int", 16, lambda s, m, i, ef: fist(s, 16, True))
    add("df", "mem", 4, None, "fbld m80bcd", 80, lambda s, m, i, ef: fbld(s, m))
    add("df", "mem", 5, None, "fild m64int", 64, lambda s, m, i, ef: fild(s, m, 64))
    add("df", "mem", 6, None, "fbstp m80bcd", 80, lambda s, m, i, ef: fbstp(s))
    add("df", "mem", 7, None, "fistp m64int", 64, lambda s, m, i, ef: fist(s, 64, True))
    add("df", "reg", 4, 0, "fnstsw ax", None, lambda s, m, i, ef: fnstsw_ax(s))
    add("df", "reg", 5, None, "fucomip st,st(i)", None, lambda s, m, i, ef: fcomi_sti(s, i, True, False), True)
    add("df", "reg", 6, None, "fcomip st,st(i)", None, lambda s, m, i, ef: fcomi_sti(s, i, True, True), True)
    return R


REGISTRY = _mk()

# Cleared class (decision of the L3b lead, 2026-11-15): two encodings of the pack have no instruction page in the SDM (SDM 1:8.3.13
# and the opcode map list no FSTP1 or FFREEP), so the spec gives them no behaviour (OWNER 10: the SDM is the reference). They occur at
# exactly 2 sites; the lifter makes each Form.fault(UD) only under L3a's unreachable-or-data certificate, and a reachable non-data site
# goes to the orchestrator.
CLEARED_UD = {("d9", "reg", 3, None): "D9 D8+i (FSTP1; no SDM page)",
              ("df", "reg", 0, None): "DF C0+i (FFREEP; not in the SDM)"}


# --------------------------------------------------------------------------------------------
# Part (iii).  The L3a DSL view of the state and of the faults (fnvlift/dsl.py XW, FAULTS; L3a int3 d935ecda), so that a Form is checked
# against the spec without a renaming layer.
#   DSL name        width   spec state
#   fr0..fr7        80      physical register i: the image R[i] itself
#   ftop            3       TOP, bits 11..13 of the status word
#   ftag            16      the tag word, 2 bits per PHYSICAL register (00 valid 01 zero 10 special 11 empty)
#   fcw             16      the control word
#   fsw             16      the status word WITHOUT TOP (bits 11..13 zero): the exception flags, SF, ES, C0..C3 (C1 included), B
#   fip fdp         32      FIP, FDP
#   fop             11      FOP
#   fcs fds         16      FCS, FDS
# The spec register is the DSL register: every 80-bit pattern is a state (no precondition on the registers).  The DSL fsw has no TOP bits: from_dsl
# replaces bits 11..13 of the status word by ftop.
# --------------------------------------------------------------------------------------------
DSL_WIDTH = {**{"fr%d" % i: 80 for i in range(8)}, "ftop": 3, "ftag": 16, "fcw": 16, "fsw": 16, "fip": 32, "fdp": 32, "fop": 11,
             "fcs": 16, "fds": 16}
TOP_FIELD_MASK = 7 << TOP_LO


def from_dsl(d):
    """St from the DSL pre-state d (dict by the names above).  Physical register i holds the image fr<i> itself."""
    r = z3.K(z3.BitVecSort(3), K(0, 80))
    for i in range(8):
        r = z3.Store(r, K(i, 3), d["fr%d" % i])
    fsw = (d["fsw"] & K(~TOP_FIELD_MASK & 0xFFFF, 16)) | (ZeroExt(13, d["ftop"]) << K(TOP_LO, 16))
    return St(R=r, tag=d["ftag"], fsw=fsw, fcw=d["fcw"], fip=d["fip"], fcs=d["fcs"], fdp=d["fdp"], fds=d["fds"], fop=d["fop"])


def to_dsl(s):
    """The DSL post-state of St s: dict by the names above."""
    d = {"fr%d" % i: z3.Select(s.R, K(i, 3)) for i in range(8)}
    d.update(ftop=top(s), ftag=s.tag, fcw=s.fcw, fsw=s.fsw & K(~TOP_FIELD_MASK & 0xFFFF, 16), fip=s.fip, fdp=s.fdp, fop=s.fop,
             fcs=s.fcs, fds=s.fds)
    return d


# MMX aliases the x87 state (SDM 1:9.5: "The MMX state is aliased to the x87 FPU state").  One copy of the effect, here, on the DSL state names
# above; the SIMD-integer specs import these.  mmN is the low 64 bits of the PHYSICAL register frN: every MMX instruction sets TOS to 0 first
# (SDM 1:9.6.2), so ST(N) and physical register N coincide.
def mmx_read(d, n):
    """mmN: bits 0..63 of fr<n> (SDM 1:9.5 aliasing; SDM 1:9.6.2 'writes ... to the exponent part ... (bits 64 through 79)' fixes bits 0..63 as the MMX data)."""
    return Extract(63, 0, d["fr%d" % n])


def mmx_exec_effect(d):
    """What EVERY MMX instruction other than EMMS does to the x87 state, read or write alike (SDM 1:9.6.2: 'The TOS (top of stack) value of the x87
    FPU status word is set to 0. The entire x87 FPU tag word is set to the valid state (00B in all tag fields).'; SDM 1:9.5.1: 'After each MMX
    instruction, the entire x87 FPU tag word is set to valid (00B).')."""
    r = dict(d)
    r["ftop"] = K(0, DSL_WIDTH["ftop"])
    r["ftag"] = K(0, DSL_WIDTH["ftag"])
    return r


def mmx_write_effect(d, n, value):
    """An MMX instruction that writes mmN := value (64 bits): mmx_exec_effect, then fr<n> := 0xFFFF:value (SDM 1:9.6.2: 'When an MMX instruction
    writes to an MMX register, it writes ones (11B) to the exponent part of the corresponding floating-point register (bits 64 through 79).').
    Other registers keep their bits."""
    r = mmx_exec_effect(d)
    r["fr%d" % n] = z3.Concat(K(0xFFFF, 16), value)
    return r


def emms_effect(d):
    """EMMS: the entire tag word becomes empty, nothing else changes (SDM 1:9.4.8: 'setting the tags in x87 FPU tag word to 11B'; SDM 1:9.5.1:
    'The EMMS instruction ... sets the entire x87 FPU tag word to empty (11B).')."""
    r = dict(d)
    r["ftag"] = K(0xFFFF, DSL_WIDTH["ftag"])
    return r


# DSL fault kinds: the one source is L3a's fault table fnvlift/faults.json (read through fnvlift/faults.py: FAULTS, BYNAME); the order of the unmasked
# checks is that of Wine 9.0 get_fpu_code (dlls/ntdll/unix/signal_i386.c): IE (with SF: STACK_CHECK), then DE, ZE, OE, UE, PE.
import importlib.util as _ilu, os as _os
_spec = _ilu.spec_from_file_location("fnvlift_faults", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "fnvlift", "faults.py"))
_faults = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_faults)
FLT = {n: _faults.BYNAME[n]["code"] for n in ("FLT_IE", "FLT_DE", "FLT_ZE", "FLT_OE", "FLT_UE", "FLT_PE", "FLT_SF")}


def fault_kind(o):
    """8-bit kind of the unmasked x87 exception that instruction result o raises (0 = none): FLT_SF when the unmasked IE is a stack fault
    (SDM 1:8.5.1.1 raises IE with SF), FLT_IE otherwise, then FLT_DE, FLT_ZE, FLT_OE, FLT_UE, FLT_PE.  The unmasked state effects are the spec's
    (finish: unmasked IE/DE/ZE abort, an unmasked PE stores the result and then signals, an unmasked OE/UE of a register result is excluded by
    precondition).  A waiting instruction with a pending unmasked exception traps first (Out.trap) and raises FLT_IE/FLT_SF from the pending flags;
    that case is the trap's, not this kind's."""
    u = o.raised & ~Extract(5, 0, o.fcw_in)
    def has(b): return Extract(b, b, u) == K(1, 1)
    return If(has(IE), If(o.sf_raised, K(FLT["FLT_SF"], 8), K(FLT["FLT_IE"], 8)), If(has(DE), K(FLT["FLT_DE"], 8), If(has(ZE), K(FLT["FLT_ZE"], 8),
           If(has(OE), K(FLT["FLT_OE"], 8), If(has(UE), K(FLT["FLT_UE"], 8), If(has(PE), K(FLT["FLT_PE"], 8), K(0, 8)))))))


def execute(key, s, m=None, i=None, ef=None):
    """Run the instruction of REGISTRY[key].  Adds the trap of a waiting instruction (SDM 1:8.7.1: #MF before
    the next waiting x87 instruction when ES is set); the non-waiting ones are NONWAITING."""
    e = REGISTRY[key]
    o = e["fn"](s, m, i, ef)
    o.trap = FALSE if e["name"] in NONWAITING else bit(s.fsw, ES)
    return o
