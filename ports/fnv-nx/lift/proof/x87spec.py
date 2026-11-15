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

Register format (OWNER 8, the only deviation from the SDM).
  A data register holds an IEEE 754 binary64 BIT PATTERN (64-bit vector), not the 80-bit double
  extended format.  Arithmetic is binary64 arithmetic, rounded as FCW.RC says; with FCW.PC = 00B the
  result is first rounded to binary32 (SDM 1:8.1.5.2), which is exact for + - * / sqrt because
  53 >= 2*24+2 (a double rounding with the same directed mode is a single rounding).  Bit patterns
  are used because z3's FP sort has one NaN only, so a z3 FP term cannot carry the sign, quiet bit or
  payload that SDM 1:T4-8 and 1:4.8.3.4 make observable.  z3 FP terms are used only on non-NaN values.
  The m80fp memory format is kept bit exact where it is representable in binary64:
    * m80 -> register: round to binary64, round-to-nearest-even (ties to even), the deviation of OWNER 8
      (no SDM flag is raised by FLD m80, SDM 2A:FLD Exc: only #IS / #IA(SNaN not for m80)).  NaN: sign,
      quiet bit (bit 62 of the m80 fraction) and the 51 payload bits below it are kept; if the kept
      fraction would be zero (an SNaN whose set payload bits are all in the dropped part) bit 0 of the
      binary64 fraction is set so the value stays a NaN (SDM 1:T4-3: SNaN fraction is non-zero).
    * register -> m80: exact widening (SDM 1:T4-3 encodings, biases 1023 and 16383 from SDM 1:4.2.2);
      binary64 subnormals are normalised (integer bit 1), as the double extended format has the range.
  Tag word: classification of the register-format value (SDM 1:8.1.7): zero 01, NaN/infinity/denormal
  10, other 00, empty 11.  A binary64 subnormal is "denormal" of SDM 1:T4-3 in this register format.
  Unmasked OE/UE of a register result stores the biased result of SDM 1:8.5.4/8.5.5, which is not
  representable in binary64; every function that can raise OE or UE returns the precondition that the
  corresponding mask bit is set (Out.pre).  The lifter's fact is that every FLDCW/FLDENV/FRSTOR/FXRSTOR
  site keeps the exceptions masked (CLAUDE.md, owner order 2026-11-14, hardware exceptions).

Return convention.  Every instruction function is  f(s, m=None) -> Out  where s is the machine state
(St), m the memory operand bit-vector for memory forms.  Out.s is the new state, Out.mem the value
stored (or None), Out.wr the Bool "memory was written", Out.ax a value written to AX (fnstsw ax),
Out.eflags the (ZF, PF, CF) written for fcomi/fucomi (OF, SF, AF are cleared, SDM 2A:FCOMI Flags),
Out.pre the Bool "the instruction's behaviour is defined by the SDM and the register format here".
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
    """x87 state: R physical register file (Array BV3 -> BV64, SDM 1:8.1.2: eight 80-bit registers,
    here the register format), tag (SDM 1:8.1.7), fsw, fcw, and the pointers of SDM 1:8.1.8, 8.1.9."""
    FIELDS = ("R", "tag", "fsw", "fcw", "fip", "fcs", "fdp", "fds", "fop")

    def __init__(self, **kw):
        for f in self.FIELDS:
            setattr(self, f, kw[f])

    @staticmethod
    def fresh(name):
        return St(R=z3.Array(name + ".R", z3.BitVecSort(3), z3.BitVecSort(64)),
                  tag=z3.BitVec(name + ".tag", 16), fsw=z3.BitVec(name + ".fsw", 16),
                  fcw=z3.BitVec(name + ".fcw", 16), fip=z3.BitVec(name + ".fip", 32),
                  fcs=z3.BitVec(name + ".fcs", 16), fdp=z3.BitVec(name + ".fdp", 32),
                  fds=z3.BitVec(name + ".fds", 16), fop=z3.BitVec(name + ".fop", 11))

    def copy(self, **kw):
        d = {f: getattr(self, f) for f in self.FIELDS}
        d.update(kw)
        return St(**d)

    @staticmethod
    def ite(c, a, b):
        return St(**{f: If(c, getattr(a, f), getattr(b, f)) for f in St.FIELDS})

    @staticmethod
    def after_finit(r):
        """State after FINIT/FNINIT from register file r, SDM 2A:FINIT Op (the data registers are not changed)."""
        return St(R=r, tag=K(TW_INIT, 16), fsw=K(FSW_INIT, 16), fcw=K(FCW_INIT, 16),
                  fip=K(0, 32), fcs=K(0, 16), fdp=K(0, 32), fds=K(0, 16), fop=K(0, 11))


def top(s):
    return top_of(s.fsw)


def phys(s, i):
    """Physical register of ST(i), SDM 1:8.1.2 'ST(i) ... i-th register from the top of stack': (TOP+i) mod 8."""
    return (top(s) + K(i, 3)) if isinstance(i, int) else (top(s) + i)


def st(s, i):
    return z3.Select(s.R, phys(s, i))


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


def tag_of_value(b):
    """Tag the register-format value b gets (SDM 1:8.1.7: 01 zero; 10 special = NaN, infinity, denormal;
    00 valid).  Deviation of OWNER 8: the register format is binary64, so 'denormal' is the binary64
    subnormal."""
    return If(is_zero(b), K(TAG_ZERO, 2), If(Or(is_nan(b), is_inf(b), is_sub(b)), K(TAG_SPECIAL, 2), K(TAG_VALID, 2)))


def write_reg(s, i, v):
    """Write ST(i) and tag it from the value (SDM 2A:FLD Op: 'ST(0) <- ...' loads tag the register non-empty)."""
    p = phys(s, i)
    s2 = s.copy(R=z3.Store(s.R, p, v))
    return set_tag_at(s2, p, tag_of_value(v))


def push_target_nonempty(s):
    """SDM 1:8.5.1.1 stack overflow: 'An instruction attempts to load a non-empty x87 FPU register'.  A
    push writes physical register TOP-1."""
    return tag_at(s, top(s) - K(1, 3)) != K(TAG_EMPTY, 2)


def do_push(s, v):
    """TOP <- TOP-1; ST(0) <- v (SDM 2A:FLD Op: 'TOP <- TOP-1; ST(0) <- ...').  Caller checks overflow."""
    s2 = s.copy(fsw=with_top(s.fsw, top(s) - K(1, 3)))
    return write_reg(s2, 0, v)


def do_pop(s):
    """Mark ST(0) empty and TOP <- TOP+1 (SDM 2A:FSTP Op: 'ST(0) <- ... ; pop'; SDM 1:8.1.7 empty tag 11)."""
    s2 = set_tag_at(s, top(s), K(TAG_EMPTY, 2))
    return s2.copy(fsw=with_top(s2.fsw, top(s2) + K(1, 3)))


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
# SDM 1:T4-3 'QNaN Floating-Point Indefinite': sign 1, exponent all ones, integer bit 1, fraction 10..0


def quieten(b):
    """SDM 1:4.8.3.5: 'an SNaN is converted to a QNaN ... by setting the most-significant fraction bit to 1'."""
    return b | K(1 << 51, 64)


def fp(b):
    """z3 FP term of the bit pattern b (meaningful for non-NaN b only)."""
    return z3.fpBVToFP(b, F64)


def bits(f):
    return z3.fpToIEEEBV(f)


def real(b):
    return z3.fpToReal(fp(b))


def nan_result2(a, b):
    """SDM 1:T4-8, x87 column, for two operands of which at least one is a NaN.  Returns the result
    pattern (a QNaN) and the Bool 'a result was selected by a rule' (False when the SDM leaves the choice
    open: two NaNs of equal significand and different sign)."""
    an, bn = is_nan(a), is_nan(b)
    asn, bsn = is_snan(a), is_snan(b)
    # SNaN and QNaN -> the QNaN operand;  SNaN and a number -> the SNaN converted;  QNaN and number -> the QNaN
    only_a = And(an, Not(bn))
    only_b = And(bn, Not(an))
    mixed_a_q = And(an, bn, Not(asn), bsn)      # a QNaN, b SNaN -> a
    mixed_b_q = And(an, bn, asn, Not(bsn))      # a SNaN, b QNaN -> b
    both_same_kind = And(an, bn, asn == bsn)    # two SNaNs or two QNaNs -> larger significand
    pick_a_larger = UGT(fr(a), fr(b))
    larger = If(pick_a_larger, a, b)
    res = If(only_a, a, If(only_b, b, If(mixed_a_q, a, If(mixed_b_q, b, larger))))
    # equal significand, different sign: the SDM names no rule
    ambiguous = And(both_same_kind, fr(a) == fr(b), sgn(a) != sgn(b))
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
    def __init__(self, s, mem=None, wr=None, ax=None, eflags=None, pre=None):
        self.s = s
        self.mem = mem
        self.wr = wr if wr is not None else z3.BoolVal(False)
        self.ax = ax
        self.eflags = eflags
        self.pre = pre if pre is not None else z3.BoolVal(True)


def finish(s_in, s_out, newflags, mem=None, extra_pre=None, ou_pre=False):
    """Apply the SDM 1:8.5 unmasked-exception rule to the result s_out of an instruction.
    newflags is the 6-bit vector of exception flags the instruction raised.
      * flags are set in FSW either way (SDM 1:8.1.3.3, sticky);
      * an unmasked IE, DE or ZE leaves TOP, tags, registers and the memory operand unchanged
        (SDM 1:8.5.1.1, 8.5.1.2, 8.5.2, 8.5.3: 'the top-of-stack pointer (TOP) and source operands remain
        unchanged');
      * an unmasked PE stores the result and then signals (SDM 1:8.5.6: handler invoked in addition);
      * ES and B are recomputed (SDM 1:8.1.3.3).
    ou_pre adds the OWNER 8 precondition that OE and UE are masked when this instruction raised them
    (the biased result of SDM 1:8.5.4/8.5.5 is not representable in binary64)."""
    fcw = s_in.fcw
    unm = ~Extract(5, 0, fcw)
    abort = (newflags & unm & fl(IE, DE, ZE)) != K(0, 6)
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
    return Out(s_fin, mem=mem, wr=wr, pre=pre)


def cc_copy(src, dst):
    """The condition code and SF bits of src placed into dst (used when an abort keeps old registers)."""
    out = dst
    for pos in (C0, C1, C2, C3):
        out = set_bit(out, pos, bit(src, pos))
    return out


def stack_fault_flags(overflow):
    """SDM 1:8.5.1.1: 'sets the IE flag (bit 0) and the SF flag (bit 6)'.  Returns (6-bit flags, SF Bool);
    the SF bit is outside the 6-bit set so the caller sets it with set_sf."""
    return fl(IE)


def set_sf(fsw):
    return set_bit(fsw, SF, z3.BoolVal(True))


def stack_fault_state(s, overflow):
    """State after a detected stack fault with the exception masked or not: IE, SF set; C1 = 1 for
    overflow, 0 for underflow (SDM 1:8.5.1.1 and 1:8.1.3.2)."""
    f = set_sf(s.fsw)
    f = cc(f, c1=z3.BoolVal(overflow))
    return s.copy(fsw=f)


# --- the fcom/fcomi condition codes -----------------------------------------------------------
def compare(a, b, ordered):
    """Compare register-format values a (ST(0)) and b.  Returns (c3, c2, c0, ie_raised, de_raised).
    SDM 1:T8-6: ST(0) > src 000, ST(0) < src 001, equal 100, unordered 111 (C3, C2, C0).  SDM 1:T8-10 /
    2A:FCOM Exc: IE for an SNaN; for ordered compares (FCOM, FICOM, FTST, FCOMI) also for a QNaN
    (SDM 1:8.5.1.2 last paragraph); DE for a denormal operand (SDM 1:8.5.2)."""
    unordered = Or(is_nan(a), is_nan(b))
    lt = z3.fpLT(fp(a), fp(b))
    eq = z3.fpEQ(fp(a), fp(b))       # +0 == -0 (IEEE), SDM 2A:FCOM Op 'ST(0) = SRC'
    c3 = Or(unordered, eq)
    c2 = unordered
    c0 = Or(unordered, And(Not(unordered), lt))
    ie = Or(is_snan(a), is_snan(b)) if not ordered else unordered
    de = And(Not(unordered), Or(is_sub(a), is_sub(b)))
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


def to_m32(b, rm):
    """register format -> m32fp, SDM 2A:FST Op 'DEST <- ST(0)' with conversion to single precision.
    Returns (bits32, ie, oe, ue, pe, roundup).  NaN: SNaN -> IE and the QNaN (SDM 1:T4-8); the fraction is
    the top 23 bits of the 52 (SDM 1:T4-3 layout); infinity and zero keep their class."""
    nan = is_nan(b)
    q = quieten(b)
    nan32 = Concat(sgn(b), K(0xFF, 8), Extract(51, 29, q))
    exact = real(b)
    r32 = z3.fpToFP(rm, fp(b), F32)
    r32b = bits(r32)
    rr = z3.fpToReal(r32)
    finite = Not(Or(nan, is_inf(b)))
    inexact = And(finite, Not(z3.fpIsInf(r32)), rr != exact)
    oe = And(finite, z3.fpIsInf(r32))
    ub = z3.fpToReal(z3.fpToFP(rm, fp(b), z3.FPSort(30, 24)))
    tiny = And(finite, ub != 0, abs_real(ub) < z3.RealVal(1) / z3.RealVal(2 ** 126))
    ue = And(tiny, inexact)
    roundup = And(finite, Not(z3.fpIsInf(r32)), abs_real(rr) > abs_real(exact))
    out = If(nan, nan32, r32b)
    return out, is_snan(b), oe, ue, Or(inexact, oe), roundup


def to_m64(b):
    """register format -> m64fp: identical pattern (OWNER 8); an SNaN is stored as the QNaN with IE
    (SDM 2A:FST Exc '#IA Source operand is an SNaN'; SDM 1:T4-8)."""
    return If(is_snan(b), quieten(b), b), is_snan(b)


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


def from_m80(m):
    """m80fp -> register format (binary64), round-to-nearest-even; see the module docstring (OWNER 8).
    Layout: bits 79 sign, 78..64 exponent, 63 integer bit, 62..0 fraction (SDM 1:T4-3).  Returns the
    pattern.  Unsupported encodings (SDM 1:T8-3) are the caller's concern (precondition supported_m80)."""
    s = Extract(79, 79, m)
    e = Extract(78, 64, m)
    sig = Extract(63, 0, m)
    frac63 = Extract(62, 0, m)
    # NaN / infinity: exponent 0x7FFF
    inf64 = Concat(s, K(0x7FF, 11), K(0, 52))
    nanf = Extract(62, 11, m)
    nanf = If(nanf == K(0, 52), K(1, 52), nanf)       # keep the value a NaN (module docstring)
    nan64 = Concat(s, K(0x7FF, 11), nanf)
    spec = If(frac63 == K(0, 63), inf64, nan64)
    # finite: effective exponent (SDM 1:8.2.2: denormal biased exponent 0 means 1 - 16383)
    e_eff = If(e == K(0, 15), K(1, 15), e)
    nz = sig != K(0, 64)
    lz = clz(sig, 64)
    sig_n = sig << lz
    # value = sig_n * 2^(e_eff - 16383 - 63 - lz) = 1.x * 2^u with u = e_eff - 16383 - lz
    u = ZeroExt(1, e_eff) - K(16383, 16) - Extract(15, 0, lz)         # 16-bit signed
    top53 = Extract(63, 11, sig_n)
    rem = Extract(10, 0, sig_n)
    up = Or(UGT(rem, K(0x400, 11)), And(rem == K(0x400, 11), Extract(0, 0, top53) == K(1, 1)))
    pat_norm = (ZeroExt(53, Extract(10, 0, u + K(1023, 16))) << K(52, 64)) + ZeroExt(12, Extract(51, 0, top53)) \
        + If(up, K(1, 64), K(0, 64))
    # u > 1023 -> overflow to infinity under round-to-nearest (SDM 1:T4-11 'To nearest: +-infinity')
    ovf = u > K(1023, 16)
    # subnormal binary64 result: u < -1022
    sh = K(-1022, 16) - u                                              # >= 1
    sh_big = UGT(sh, K(60, 16))
    v = ZeroExt(64, sig_n)                                             # 128-bit
    tot = ZeroExt(112, sh) + K(11, 128)                                # right shift count
    n_part = LShR(v, tot)
    lost_mask = (K(1, 128) << tot) - K(1, 128)
    lost = v & lost_mask
    halfb = K(1, 128) << (tot - K(1, 128))
    up_s = Or(UGT(lost, halfb), And(lost == halfb, Extract(0, 0, n_part) == K(1, 1)))
    n_sub = Extract(63, 0, n_part + If(up_s, K(1, 128), K(0, 128)))
    pat_sub = If(sh_big, K(0, 64), n_sub)
    fin = If(Not(nz), Concat(s, K(0, 63)),
             If(ovf, inf64, If(u < K(-1022, 16), Concat(s, K(0, 63)) | pat_sub, Concat(s, K(0, 63)) | pat_norm)))
    # the sign bit of pat_norm / pat_sub is zero; OR above puts s on top
    return If(e == K(0x7FFF, 15), spec, fin)


def supported_m80(m):
    """SDM 1:8.2.2 / 1:T8-3: an m80 encoding is supported when it is a zero, denormal, normal, infinity or
    QNaN/SNaN of SDM 1:T4-3: for a non-zero biased exponent other than 0x7FFF the integer bit must be 1;
    for exponent 0x7FFF the integer bit must be 1; for exponent 0 the integer bit 1 is a pseudo-denormal
    (handled as a denormal, SDM 1:8.2.2) so it is supported too."""
    e = Extract(78, 64, m)
    ib = Extract(63, 63, m) == K(1, 1)
    return If(e == K(0, 15), z3.BoolVal(True), ib)


def int_to_reg(m, n, rm):
    """m (n-bit two's complement) -> register format.  SDM 2A:FILD Op: 'ST(0) <- SignExtend(...)'.
    16 and 32 bit values are exactly representable in binary64; 64 bit values are rounded by rm (OWNER 8)."""
    return bits(z3.fpSignedToFP(rm, m, F64))


def reg_to_int(b, n, rm, truncate=False):
    """register format -> n-bit signed integer for FIST/FISTP/FISTTP.  Returns (value, ie, pe, roundup).
    SDM 2A:FIST Op 'DEST <- Integer(ST(0))' rounded by RC; SDM 2A:FISTTP Op uses truncation.  Out of range,
    NaN or infinity: integer indefinite 10..0 with IE (SDM 1:T8-10, 1:8.2.1, SDM 1:T4-1).  PE when the
    rounding lost a fraction; C1 = rounded up (SDM 1:8.1.3.2)."""
    rmu = z3.RTZ() if truncate else rm
    ri = z3.fpRoundToIntegral(rmu, fp(b))
    lo = -(2 ** (n - 1))
    hi = 2 ** (n - 1) - 1
    rr = z3.fpToReal(ri)
    special = Or(is_nan(b), is_inf(b))
    inrange = And(Not(special), rr >= lo, rr <= hi)
    val = z3.fpToSBV(z3.RTZ(), ri, z3.BitVecSort(n))
    indef = K(1 << (n - 1), n)
    exact = real(b)
    pe = And(inrange, rr != exact)
    roundup = And(inrange, abs_real(rr) > abs_real(exact))
    return If(inrange, val, indef), Not(inrange), pe, roundup


def bcd_to_reg(m, rm):
    """m80 packed BCD (SDM 1:4.7: 18 digits in bytes 0..8, sign bit 79, bits 72..78 ignored) -> register
    format (SDM 2A:FBLD Op).  Digit d_i = bits 4i+3..4i.  The integer < 10^18 < 2^60; rounded with rm into
    binary64 (OWNER 8 deviation, all 18 digit values are exact in the double extended format)."""
    v = K(0, 64)
    for i in range(17, -1, -1):
        d = ZeroExt(60, Extract(4 * i + 3, 4 * i, m))
        v = v * K(10, 64) + d
    f = z3.fpSignedToFP(rm, v, F64)
    mag = bits(f)
    # SDM 2A:FBLD Op: sign from bit 79, '-0' keeps the sign
    return Concat(Extract(79, 79, m), Extract(62, 0, mag))


def bcd_digits_ok(m):
    """FBLD of a digit above 9: SDM 1:4.7 gives no value for it (SDM 2A:FBLD: 'attempting to load the
    indefinite produces an undefined result'), so the precondition is that all 18 digits are decimal."""
    conds = [ULE(Extract(4 * i + 3, 4 * i, m), K(9, 4)) for i in range(18)]
    return And(*conds)


BCD_INDEFINITE = 0xFFFFC000000000000000   # SDM 1:T4-5, 'FFFFC000000000000000H'


def reg_to_bcd(b, rm):
    """register format -> packed BCD for FBSTP (SDM 2A:FBSTP Op 'DEST <- BCD(ST(0))').  Returns
    (m80 pattern, ie, pe, roundup).  Value not representable in 18 digits, NaN or infinity: the BCD
    indefinite and IE (SDM 1:T8-10)."""
    ri = z3.fpRoundToIntegral(rm, fp(b))
    rr = z3.fpToReal(ri)
    special = Or(is_nan(b), is_inf(b))
    limit = 10 ** 18 - 1
    ok = And(Not(special), rr >= -limit, rr <= limit)
    mag_i = z3.fpToSBV(z3.RTZ(), z3.fpAbs(ri), z3.BitVecSort(64))
    dig = []
    x = mag_i
    for i in range(18):
        dig.append(Extract(3, 0, z3.URem(x, K(10, 64))))
        x = z3.UDiv(x, K(10, 64))
    body = Concat(*[dig[i] for i in range(17, -1, -1)])                 # 72 bits, digit 17 first
    sign = Extract(63, 63, b)
    enc = Concat(sign, K(0, 7), body)                                    # 1 + 7 + 72 = 80
    indef = K(BCD_INDEFINITE, 80)
    exact = real(b)
    return If(ok, enc, indef), Not(ok), And(ok, rr != exact), And(ok, abs_real(rr) > abs_real(exact))
