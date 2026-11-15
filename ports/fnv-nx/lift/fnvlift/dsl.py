"""Instruction-semantics DSL: expressions over fixed-width bit-vectors, written ONCE, with two backends:
  to_smt(node)  -> z3 BitVec term (pre-state = z3 variables eax..edi, cf pf af zf sf of, array 'mem' BV32->BV8)
  Form.to_c()   -> C++ function in the lifter's style (u32 locals, M8/M16/M32), unsigned fixed-width operations only.
Semantics (identical in both backends): value of width w is an unsigned integer < 2^w; add/sub/mul wrap; shifts take a
count of the SAME width and are defined for every count (count >= w gives 0, ashr gives the sign fill); udiv by 0 = all
ones, urem by 0 = the dividend (SMT-LIB); eq/ult/slt give a 1-bit value; ite takes a 1-bit condition; load is little endian
and the address wraps modulo 2^32.
A Form is an ordered list of effects.  All expressions read the PRE-state (parallel semantics, loads see the pre-state
memory); effects commit in order, so for two writes to one target the later wins.  Flags not written keep their value.
A flag the SDM leaves undefined is declared with undefined(name, sdm_sentence) and is excluded from any obligation;
flag(name, e, when=c) is a flag defined only when c holds (e.g. OF of a shift only for count 1)."""
import hashlib
import itertools

# x87 / MMX / SSE pre-state (frozen interface, L3b w4 writes against it): name -> width. fr<i> is the PHYSICAL register i (80 bits, never ST(i)); ftag is 8 x 2 bits (physical order, 00 valid 01 zero 10 special 11 empty);
# fsw is the status word WITHOUT TOP (TOP is `ftop`); fop is 11 bits; xmm<i> 128; mxcsr 32.
XW = {**{'fr%d' % i: 80 for i in range(8)}, 'ftop': 3, 'ftag': 16, 'fcw': 16, 'fsw': 16, 'fip': 32, 'fdp': 32, 'fop': 11, 'fcs': 16, 'fds': 16, **{'xmm%d' % i: 128 for i in range(8)}, 'mxcsr': 32}
REGS = ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi')
FLAGS = ('cf', 'pf', 'af', 'zf', 'sf', 'of')
_I = {}
_SERIAL = itertools.count()      # node serial numbers: assigned at construction, in construction order (a deterministic key for per-node tables)
def mask(w): return (1 << w) - 1

class N:
    __slots__ = ('op', 'w', 'a', 'x', 'u')
    def __init__(s, op, w, a, x): s.op, s.w, s.a, s.x, s.u = op, w, a, x, next(_SERIAL)
    def __add__(s, o): return bin_('add', s, o)
    def __radd__(s, o): return bin_('add', o, s)
    def __sub__(s, o): return bin_('sub', s, o)
    def __rsub__(s, o): return bin_('sub', o, s)
    def __mul__(s, o): return bin_('mul', s, o)
    def __and__(s, o): return bin_('and', s, o)
    def __or__(s, o): return bin_('or', s, o)
    def __xor__(s, o): return bin_('xor', s, o)
    def __lshift__(s, o): return bin_('shl', s, o)
    def __rshift__(s, o): return bin_('lshr', s, o)
    def __invert__(s): return mk('not', s.w, (s,))
    def __neg__(s): return mk('neg', s.w, (s,))

def mk(op, w, a=(), x=None):
    k = (op, w, tuple(i.u for i in a), x)
    n = _I.get(k)
    if n is None: n = _I[k] = N(op, w, tuple(a), x)
    return n
def _unwrap(v):
    """an object that stands for a DSL node (attribute `_node`, e.g. the symbolic instruction field of proof/symform.py) -> that node"""
    n = getattr(v, '_node', None)
    return v if n is None else n
def C(v, w):
    """constant of width w; a DSL node (an instruction field computed by a decode adapter) is resized to w instead (truncate / zero-extend)"""
    v = _unwrap(v)
    if isinstance(v, N): return v if v.w == w else (extract(v, w - 1, 0) if v.w > w else mk('zext', w, (v,)))
    return mk('const', w, (), v & mask(w))
def V(name, w):
    assert XW.get(name, w) == w, ('state variable %s has width %d' % (name, XW[name]), w)
    return mk('var', w, (), name)
def fr(i): return V('fr%d' % i, 80)
def xmm(i): return V('xmm%d' % i, 128)
def mm(i):
    """MMX register i as read: the significand of the physical register i (SDM Vol 1 9.5.1, Table 9-1)"""
    return extract(fr(i), 63, 0)
def F(name, w):
    """instruction FIELD variable (the immediate / displacement bytes of an encoding shape): free in a proof, a constant per concrete instruction"""
    return mk('fld', w, (), name)
def bin_(op, x, y):
    x, y = _unwrap(x), _unwrap(y)
    if not isinstance(x, N): x = C(x, y.w)
    if not isinstance(y, N): y = C(y, x.w)
    assert x.w == y.w, (op, x.w, y.w)
    return mk(op, 1 if op in ('eq', 'ult', 'slt') else x.w, (x, y))
def shl(a, c): return bin_('shl', a, c)
def lshr(a, c): return bin_('lshr', a, c)
def ashr(a, c): return bin_('ashr', a, c)
def udiv(a, b): return bin_('udiv', a, b)
def urem(a, b): return bin_('urem', a, b)
def eq(a, b): return bin_('eq', a, b)
def ult(a, b): return bin_('ult', a, b)
def slt(a, b): return bin_('slt', a, b)
def ite(c, a, b):
    assert c.w == 1 and a.w == b.w
    return mk('ite', a.w, (c, a, b))
def zext(a, w): return a if a.w == w else mk('zext', w, (a,))
def sext(a, w): return a if a.w == w else mk('sext', w, (a,))
def extract(a, hi, lo): return a if (hi, lo) == (a.w - 1, 0) else mk('extract', hi - lo + 1, (a,), (hi, lo))
def trunc(a, w): return extract(a, w - 1, 0)
def concat(hi, lo): return mk('concat', hi.w + lo.w, (hi, lo))
def load(addr, w):
    assert addr.w == 32 and w in (8, 16, 32, 64)
    return mk('load', w, (addr,))
def uf(name, w, *args):
    """a value the reference machine model supplies (rt/cpumodel.c, the platform timer): a function of its arguments, named, of width w. z3: an uninterpreted function (a free 64-bit variable
    without arguments); C: the runtime's own function (UF_C).  Two reads of the same name and arguments in one Form are one value (hash-consed): an instruction that needs the same
    read twice (rdtsc's two halves) names it once."""
    return mk('uf', w, tuple(_unwrap(a) for a in args), name)
CMPPRED = ('eq', 'lt', 'le', 'unord', 'neq', 'nlt', 'nle', 'ord')      # the imm8 predicates of CMPPS / CMPSS (SDM Vol 2 CMPPS table 3-1)
FPOPS = ('add', 'sub', 'mul', 'div', 'sqrt', 'min', 'max') + tuple('cmp' + p for p in CMPPRED) + ('comi', 'ucomi')
def fp(op, w, a, b, mx):
    """SSE scalar floating point at the SOURCE type (w = 32: binary32, 64: binary64) on the bit patterns a, b (w bits; sqrt ignores b) under the MXCSR value mx (32 bits).
    One node of width w + 6: the low w bits are the result's bit pattern, bits w..w+5 the status flags (IE DE ZE OE UE PE, MXCSR bits 0-5) THIS operation raises; use fp_val / fp_flags.
    Rules (SDM Vol 1 4.8.3, 4.9.1, 10.2.3, 11.5; x86 SSE detects tininess AFTER rounding: Berkeley SoftFloat 3e source/8086-SSE/specialize.h line 48 `softfloat_tininessAfterRounding`; the SDM sentence is pending (L3b w4: the Sail model has no SSE FP)): rounding from mx.RC (bits 13-14: 0 nearest-even, 1 down, 2 up, 3 zero);
    DAZ (mx bit 6) reads a denormal operand as a zero of its sign (no DE); a NaN operand gives that operand quieted (the first source if both), an invalid operation without a NaN operand gives the
    negative quiet NaN (the default QNaN); the pre-computation flags IE / ZE / DE are ONE function, dsl._pre, in the priority order of SDM Vol 1 11.5.2.1 (a signalling NaN operand or an invalid operation: IE; a QNaN operand: nothing below; ZE: finite non-zero / 0; DE: a denormal operand with DAZ clear, only when no higher row applies); OE: a finite exact result that
    rounds to infinity (the result is the infinity or the largest finite number as the mode says; PE too); UE: tiny after rounding (the result rounded to the destination precision with UNBOUNDED exponent range is nonzero and below the smallest normal) and (inexact, or UM = mx bit 11 clear); PE: inexact;
    FTZ (mx bit 15) with UM set: a tiny result is the zero of its sign and UE and PE are set.  Flags are those of this operation alone; the caller ORs them into MXCSR (sticky)."""
    assert op in FPOPS and w in (32, 64) and a.w == w and mx.w == 32 and (op == 'sqrt' or b.w == w)
    return mk('fp', w + 6, (a, a if op == 'sqrt' else b, mx), (op, w))
def fp_val(n): assert n.op == 'fp'; return extract(n, n.x[1] - 1, 0)
def fp_flags(n): assert n.op == 'fp'; return extract(n, n.x[1] + 5, n.x[1])
def cvt(kind, a, mx, dstw=32, trunc=False):
    """SSE conversion lane between a signed 32-bit integer and binary32 / binary64 under the MXCSR value mx (32 bits), SDM Vol 2 CVTSI2SS CVTSI2SD CVTDQ2PS CVTDQ2PD CVTPI2PS (kind 'i2f': a is the 32-bit integer, dstw 32 or 64 the float width)
    and CVTSS2SI CVTSD2SI CVTTSS2SI CVTTSD2SI CVTPS2DQ CVTTPS2DQ (kind 'f2i': a is binary32 / binary64 by its width, the result is the 32-bit integer; trunc: round toward zero, else mx.RC).
    One node of width (result width + 6): the low bits the result, then the six status flags this lane raises (IE DE ZE OE UE PE); use cvt_val / cvt_flags.
    i2f: rounded under mx.RC; PE iff inexact (only binary32 can be).  f2i: DAZ (mx bit 6) reads a denormal operand as a zero of its sign; a NaN, an infinity or a rounded value outside int32 is the integer indefinite 0x80000000 with IE (pre-computation row 3 of
    SDM Vol 1 11.5.2.1, dsl._pre); otherwise PE iff the rounded value differs from the operand (SDM lists no #D for these instructions: CVTSD2SI "SIMD Floating-Point Exceptions: Invalid, Precision")."""
    assert kind in ('i2f', 'f2i') and mx.w == 32
    if kind == 'i2f': assert a.w == 32 and dstw in (32, 64); rw = dstw; sw = 32
    else: assert a.w in (32, 64); rw = 32; sw = a.w; dstw = 32
    return mk('cvt', rw + 6, (a, mx), (kind, sw, rw, bool(trunc)))
def cvt_val(n): assert n.op == 'cvt'; return extract(n, n.x[2] - 1, 0)
def cvt_flags(n): assert n.op == 'cvt'; return extract(n, n.x[2] + 5, n.x[2])
RCPTAB = ('rcp', 'rsqrt')
def tab(name, idx):
    """one entry (12 bits) of the reference CPU's RCPPS / RSQRTPS table of rt/rcptab.c (the pinned external, certified by rt/rcptab.cert.json; CLAUDE.md "One fixed reference CPU model"): idx is 11 bits.  z3: the table read from that file
    (only when sha256(rcptab.c) is the certificate's source_sha256 and the certificate has 0 mismatches, else ValueError); C: fnvl_tab_rcp / fnvl_tab_rsqrt (rt/fnvl.h), which read the same file's own function fnvl_rcp32 / fnvl_rsqrt32."""
    assert name in RCPTAB and idx.w == 11
    return mk('tab', 12, (idx,), name)
def msb(a): return extract(a, a.w - 1, a.w - 1)
def bit(a, i): return extract(a, i, i)

# ---------------------------------------------------------------- z3 backend
_M = {}
def mem_array():
    import z3
    return z3.Array('mem', z3.BitVecSort(32), z3.BitVecSort(8))
def to_smt(n):
    import z3
    r = _M.get(n.u)
    if r is not None: return r
    A = [to_smt(c) for c in n.a]; o = n.op; one = z3.BitVecVal(1, 1); zero = z3.BitVecVal(0, 1)
    if o == 'const': r = z3.BitVecVal(n.x, n.w)
    elif o == 'var': r = z3.BitVec(n.x, n.w)
    elif o == 'fld': r = z3.BitVec('fld_' + n.x, n.w)
    elif o in ('add', 'sub', 'mul', 'and', 'or', 'xor', 'shl'):
        r = {'add': lambda a, b: a + b, 'sub': lambda a, b: a - b, 'mul': lambda a, b: a * b, 'and': lambda a, b: a & b,
             'or': lambda a, b: a | b, 'xor': lambda a, b: a ^ b, 'shl': lambda a, b: a << b}[o](*A)
    elif o == 'lshr': r = z3.LShR(*A)
    elif o == 'ashr': r = A[0] >> A[1]
    elif o == 'udiv': r = z3.UDiv(*A)
    elif o == 'urem': r = z3.URem(*A)
    elif o == 'not': r = ~A[0]
    elif o == 'neg': r = -A[0]
    elif o == 'eq': r = z3.If(A[0] == A[1], one, zero)
    elif o == 'ult': r = z3.If(z3.ULT(*A), one, zero)
    elif o == 'slt': r = z3.If(A[0] < A[1], one, zero)
    elif o == 'ite': r = z3.If(A[0] == one, A[1], A[2])
    elif o == 'zext': r = z3.ZeroExt(n.w - n.a[0].w, A[0])
    elif o == 'sext': r = z3.SignExt(n.w - n.a[0].w, A[0])
    elif o == 'extract': r = z3.Extract(n.x[0], n.x[1], A[0])
    elif o == 'concat': r = z3.Concat(*A)
    elif o == 'uf':
        r = z3.BitVec('uf_' + n.x, n.w) if not A else z3.Function('uf_' + n.x, *[z3.BitVecSort(c.w) for c in n.a], z3.BitVecSort(n.w))(*A)
    elif o == 'fp': r = _fp_smt(n.x[0], n.x[1], *A)
    elif o == 'cvt': r = _cvt_smt(*n.x, *A)
    elif o == 'tab': r = _tab_smt(n.x, A[0])
    elif o == 'load':
        m = mem_array(); bs = [z3.Select(m, A[0] + i) for i in range(n.w // 8)]
        r = z3.Concat(*reversed(bs)) if len(bs) > 1 else bs[0]
    else: raise ValueError(o)
    _M[n.u] = r
    return r

_RCPT = {}
def _rcp_tables():
    """the two 2048-entry tables of rt/rcptab.c, read from the file when it is the certified one (sha256 = rcptab.cert.json source_sha256, 0 mismatches)"""
    if _RCPT: return _RCPT
    import json, os, re
    rt = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'rt'); src = open(os.path.join(rt, 'rcptab.c'), 'rb').read(); cert = json.load(open(os.path.join(rt, 'rcptab.cert.json')))
    if hashlib.sha256(src).hexdigest() != cert['source_sha256'] or cert['mismatches'] != 0: raise ValueError('rcptab.c is not the certified table of the reference CPU')
    t = src.decode()
    for name in ('rcp', 'rsqrt'):
        body = re.search(r'static const unsigned short %s_tab\[2048\] = \{(.*?)\};' % name, t, re.S).group(1); v = [int(x, 16) for x in re.findall(r'0x[0-9a-fA-F]+', body)]
        assert len(v) == 2048; _RCPT[name] = v
    return _RCPT
def _tab_smt(name, idx):
    import z3
    A = z3.K(z3.BitVecSort(11), z3.BitVecVal(0, 12))
    for i, v in enumerate(_rcp_tables()[name]): A = z3.Store(A, i, z3.BitVecVal(v, 12))
    return z3.Select(A, idx)
def _cvt_smt(kind, sw, rw, trunc, a, mx):
    import z3
    FM = {32: (8, 24), 64: (11, 53)}; BV = z3.BitVecVal
    rc = z3.Extract(14, 13, mx); rm = z3.If(rc == 0, z3.RNE(), z3.If(rc == 1, z3.RTN(), z3.If(rc == 2, z3.RTP(), z3.RTZ())))
    if kind == 'i2f':
        S = z3.FPSort(*FM[rw]); R = z3.fpToFP(rm, a, S)
        inex = z3.Not(z3.fpEQ(z3.fpToFP(z3.RTN(), a, S), z3.fpToFP(z3.RTP(), a, S)))
        return z3.Concat(z3.If(inex, BV(32, 6), BV(0, 6)), z3.fpToIEEEBV(R))
    eb, sb = FM[sw]; fb = sb - 1; S = z3.FPSort(*FM[sw])
    expo, frac = z3.Extract(sw - 2, fb, a), z3.Extract(fb - 1, 0, a)
    nan_ = z3.And(expo == BV((1 << eb) - 1, eb), frac != BV(0, fb)); inf_ = z3.And(expo == BV((1 << eb) - 1, eb), frac == BV(0, fb)); den = z3.And(expo == BV(0, eb), frac != BV(0, fb))
    a1 = z3.If(z3.And(z3.Extract(6, 6, mx) == 1, den), z3.Concat(z3.Extract(sw - 1, sw - 1, a), BV(0, sw - 1)), a); fa = z3.fpBVToFP(a1, S)
    r = z3.fpRoundToIntegral(z3.RTZ() if trunc else rm, fa)
    lo = z3.fpSignedToFP(z3.RNE(), BV(0x80000000, 32), S); hi = z3.FPVal(2147483648.0, S)       # -2^31 and 2^31 are exact in binary32 and binary64
    inrange = z3.And(z3.Not(nan_), z3.Not(inf_), z3.fpLEQ(lo, r), z3.fpLT(r, hi))
    ie, _ze, _de = _pre(z3.BoolVal(False), z3.BoolVal(False), z3.BoolVal(False), z3.Not(inrange), z3.BoolVal(False), z3.BoolVal(False))      # NaN, infinity or out of range: the invalid operation (row 3); no #D for these instructions
    pe = z3.And(z3.Not(ie), z3.Not(z3.fpEQ(r, fa)))
    return z3.Concat(z3.If(ie, BV(1, 6), z3.If(pe, BV(32, 6), BV(0, 6))), z3.If(inrange, z3.fpToSBV(z3.RTZ(), r, z3.BitVecSort(32)), BV(0x80000000, 32)))
def _pre(snan, qnan_op, sig_qnan, other_inv, zdiv, denorm):
    """THE pre-computation exception flags of every SSE floating-point operation (z3 model; the C twin is fx_pre in rt/fnvl.h), in the priority order of SDM Vol 1 11.5.2.1 (Table 11-3, "SIMD
    Floating-Point Exception Priority"): row 1 #I of a signalling NaN operand; row 2 a QNaN operand (no exception, nothing below is checked); row 3 other #I (an invalid operation, or a QNaN operand of an
    instruction whose SDM section lists it as #I: MINPS MAXPS COMISS and the CMPPS predicates lt le nlt nle); row 4 #Z; row 5 #D (a denormal operand with DAZ clear).  A lower row's flag is not set when a higher row applies.
    -> (IE, ZE, DE) as z3 Bools; the post-computation flags (#O #U #P: rows 6-8) are the callers' and require none of these."""
    import z3
    ie = z3.Or(snan, z3.And(qnan_op, sig_qnan), other_inv)
    stop = z3.Or(ie, qnan_op)
    ze = z3.And(z3.Not(stop), zdiv)
    de = z3.And(z3.Not(stop), z3.Not(zdiv), denorm)
    return ie, ze, de
def _fp_smt(op, w, a, b, mx):
    import z3
    S = z3.Float32() if w == 32 else z3.Float64(); eb, sb = (8, 24) if w == 32 else (11, 53); fb = sb - 1; BV = lambda v, k: z3.BitVecVal(v, k)
    sign = lambda x: z3.Extract(w - 1, w - 1, x) == 1
    expo = lambda x: z3.Extract(w - 2, fb, x); frac = lambda x: z3.Extract(fb - 1, 0, x)
    isnan_ = lambda x: z3.And(expo(x) == BV((1 << eb) - 1, eb), frac(x) != BV(0, fb))
    issnan = lambda x: z3.And(isnan_(x), z3.Extract(fb - 1, fb - 1, x) == 0)
    issub = lambda x: z3.And(expo(x) == BV(0, eb), frac(x) != BV(0, fb))
    quiet = lambda x: x | BV(1 << (fb - 1), w)
    daz = z3.Extract(6, 6, mx) == 1; um = z3.Extract(11, 11, mx) == 1; ftz = z3.Extract(15, 15, mx) == 1; rc = z3.Extract(14, 13, mx)
    zero_of = lambda x: z3.Concat(z3.Extract(w - 1, w - 1, x), BV(0, w - 1))
    rd = lambda x: z3.If(z3.And(daz, issub(x)), zero_of(x), x)
    a1, b1 = rd(a), rd(b)
    fa, fb_ = z3.fpBVToFP(a1, S), z3.fpBVToFP(b1, S)
    RM = {'n': z3.RNE(), 'd': z3.RTN(), 'u': z3.RTP(), 'z': z3.RTZ()}
    def run(rm):
        return {'add': lambda: z3.fpAdd(rm, fa, fb_), 'sub': lambda: z3.fpSub(rm, fa, fb_), 'mul': lambda: z3.fpMul(rm, fa, fb_),
                'div': lambda: z3.fpDiv(rm, fa, fb_), 'sqrt': lambda: z3.fpSqrt(rm, fa)}[op]()
    if op in ('min', 'max') or op[:3] == 'cmp' or op in ('comi', 'ucomi'):
        # no rounding, no result flags but IE / DE.  Operands a (the destination) and b (the source) after DAZ.  A NaN operand: IE for MIN / MAX and for the signalling predicates (lt le nlt nle) and COMISS (any NaN),
        # only for a signalling NaN with the quiet predicates (eq unord neq ord) and UCOMISS (SDM Vol 2 MINPS / CMPPS / COMISS / UCOMISS "SIMD Floating-Point Exceptions"); DE for a denormal operand (DAZ clear) when IE is not raised.
        un = z3.Or(isnan_(a1), isnan_(b1)); sn = z3.Or(issnan(a1), issnan(b1))
        sig = op in ('min', 'max', 'comi') or op[3:] in ('lt', 'le', 'nlt', 'nle')
        invalid, _ze, de = _pre(sn, un, sig, z3.BoolVal(False), z3.BoolVal(False), z3.And(z3.Not(daz), z3.Or(issub(a), issub(b))))
        eq_, lt_, le_ = z3.fpEQ(fa, fb_), z3.fpLT(fa, fb_), z3.fpLEQ(fa, fb_)
        ones = BV((1 << w) - 1, w)
        if op in ('min', 'max'):
            bz = z3.And(z3.fpIsZero(fa), z3.fpIsZero(fb_)); pick = lt_ if op == 'min' else z3.fpGT(fa, fb_)
            val = z3.If(un, b1, z3.If(bz, b1, z3.If(pick, a1, b1)))
        elif op in ('comi', 'ucomi'):
            gt_ = z3.fpGT(fa, fb_); zf = z3.Or(un, eq_); cf = z3.Or(un, lt_); pf = un
            val = z3.ZeroExt(w - 3, z3.Concat(z3.If(zf, BV(1, 1), BV(0, 1)), z3.If(pf, BV(1, 1), BV(0, 1)), z3.If(cf, BV(1, 1), BV(0, 1))))
        else:
            t = {'eq': z3.And(z3.Not(un), eq_), 'lt': z3.And(z3.Not(un), lt_), 'le': z3.And(z3.Not(un), le_), 'unord': un, 'neq': z3.Or(un, z3.Not(eq_)), 'nlt': z3.Or(un, z3.Not(lt_)),
                 'nle': z3.Or(un, z3.Not(le_)), 'ord': z3.Not(un)}[op[3:]]
            val = z3.If(t, ones, BV(0, w))
        fl = [invalid, de, z3.BoolVal(False), z3.BoolVal(False), z3.BoolVal(False), z3.BoolVal(False)]
        return z3.Concat(z3.Concat(*[z3.If(f, BV(1, 1), BV(0, 1)) for f in reversed(fl)]), val)
    rm = z3.If(rc == 0, RM['n'], z3.If(rc == 1, RM['d'], z3.If(rc == 2, RM['u'], RM['z'])))
    r, rd_, ru_, rz_ = run(rm), run(RM['d']), run(RM['u']), run(RM['z'])
    anan, bnan = isnan_(a1), (isnan_(b1) if op != 'sqrt' else z3.BoolVal(False))
    exact_in = z3.Not(z3.Or(anan, bnan))
    zdiv = z3.BoolVal(False) if op != 'div' else z3.And(exact_in, z3.fpIsZero(fb_), z3.Not(z3.fpIsZero(fa)), z3.Not(z3.fpIsInf(fa)))
    invalid, ze, de = _pre(z3.Or(issnan(a1), (issnan(b1) if op != 'sqrt' else z3.BoolVal(False))), z3.Or(anan, bnan), z3.BoolVal(False), z3.And(exact_in, z3.fpIsNaN(r)), zdiv,
                           z3.And(z3.Not(daz), z3.Or(issub(a), (issub(b) if op != 'sqrt' else z3.BoolVal(False)))))
    Wd = z3.FPSort(eb + 3, sb)       # the same precision with a wider exponent range: its rounded result is the "result with unbounded exponent range" that defines overflow (SDM Vol 1 4.9.1.4)
    ex = lambda v: z3.fpFPToFP(z3.RNE(), v, Wd)
    rw = {'add': lambda: z3.fpAdd(rm, ex(fa), ex(fb_)), 'sub': lambda: z3.fpSub(rm, ex(fa), ex(fb_)), 'mul': lambda: z3.fpMul(rm, ex(fa), ex(fb_)),
          'div': lambda: z3.fpDiv(rm, ex(fa), ex(fb_)), 'sqrt': lambda: z3.fpSqrt(rm, ex(fa))}[op]()
    maxf = ex(z3.fpBVToFP(BV(((((1 << eb) - 2) << fb) | ((1 << fb) - 1)), w), S))
    oe = z3.And(exact_in, z3.Not(z3.fpIsInf(fa)), z3.Or(op == 'sqrt', z3.Not(z3.fpIsInf(fb_))), z3.Not(zdiv), z3.fpGT(z3.fpAbs(rw), maxf))
    fin = z3.And(exact_in, z3.Not(z3.fpIsNaN(r)))       # the exact result is representable iff rounding down and up agree (an infinite exact result: both are that infinity)
    inexact = z3.Or(oe, z3.And(fin, z3.fpNEQ(rd_, ru_)))
    rzb = z3.fpToIEEEBV(rz_)
    minn = ex(z3.fpBVToFP(BV(1 << fb, w), S))      # the smallest normal in the wide format: tiny AFTER rounding = the unbounded-exponent rounded result (rw) is nonzero and below it
    tiny = z3.And(exact_in, z3.Not(z3.fpIsNaN(r)), z3.Not(z3.fpIsZero(rw)), z3.fpLT(z3.fpAbs(rw), minn))
    ue = z3.And(tiny, z3.Or(inexact, z3.Not(um)))
    ftz_ = z3.And(ftz, um, tiny)
    pe = z3.Or(inexact, ftz_)
    ue = z3.Or(ue, ftz_)
    indef = BV(0xFFC00000 if w == 32 else 0xFFF8000000000000, w)
    rb = z3.fpToIEEEBV(r)
    val = z3.If(anan, quiet(a1), z3.If(bnan, quiet(b1), z3.If(z3.fpIsNaN(r), indef, rb)))
    val = z3.If(ftz_, zero_of(rb), val)
    flags = [invalid, de, ze, oe, ue, pe]
    flags = [z3.If(invalid, z3.BoolVal(False), f) if i else f for i, f in enumerate(flags)]       # IE excludes every other flag
    bits = z3.Concat(*[z3.If(f, BV(1, 1), BV(0, 1)) for f in reversed(flags)])
    return z3.Concat(bits, val)

# ---------------------------------------------------------------- C backend
def T(w): return 'u32' if w <= 32 else 'u64' if w <= 64 else 'u128'
UF_C = {'rdtsc': lambda: 'fnvl_rdtsc()',      # rt/fnvl.h: the platform's monotonic timer (owner decision: rdtsc reads the platform timer)
        **{'cpuid%d' % i: (lambda leaf, sub, i=i: 'fnvl_cpuid_w(%s,%s,%d)' % (leaf, sub, i)) for i in range(4)}}      # rt/cpumodel.c: the one reference CPU's answer, word i of leaf/subleaf
def K(v, w):
    if w > 64: return '((((u128)%#xull) << 64) | %#xull)' % (v >> 64, v & mask(64))
    return '%#x%s' % (v, 'u' if w <= 32 else 'ull')
class Emit:
    macc = 'M'      # memory accessor prefix: M, or LK inside a locked instruction (core.Ctx.macc: the R9 atomic rule works on the private copy LK_BEGIN makes)
    def __init__(s, roots):
        s.cnt, s.order, s.names, s.used = {}, [], {}, set()
        for r in roots: s.walk(r)
        for n in s.order:
            if n.op == 'load' or (s.cnt[n.u] > 1 and n.op not in ('const', 'var')): s.names[n.u] = 't%d' % len(s.names)
    def walk(s, n):
        s.cnt[n.u] = s.cnt.get(n.u, 0) + 1
        if s.cnt[n.u] > 1: return
        for c in n.a: s.walk(c)
        if n.op == 'var': s.used.add(n.x)
        s.order.append(n)
    def ref(s, n):
        if n.u in s.names: return s.names[n.u]
        return s.ex(n)
    def ex(s, n):
        o, w = n.op, n.w; M = K(mask(w), w); t = T(w)
        if o == 'const': return K(n.x, w)
        if o == 'var': return 'in_' + n.x
        a = [s.ref(c) for c in n.a]
        full = w in (32, 64, 128)
        mk_ = (lambda e: '(%s)' % e) if full else (lambda e: '((%s)&%s)' % (e, M))
        if o in ('add', 'sub', 'mul'): return mk_('%s %s %s' % (a[0], {'add': '+', 'sub': '-', 'mul': '*'}[o], a[1]))
        if o in ('and', 'or', 'xor'): return '(%s %s %s)' % (a[0], {'and': '&', 'or': '|', 'xor': '^'}[o], a[1])
        if o == 'not': return '(%s ^ %s)' % (a[0], M)
        if o == 'neg': return mk_('%s - %s' % (K(0, w), a[0]))
        if o == 'shl': return mk_('shl_<%s>(%s,%s,%d)' % (t, a[0], a[1], w))
        if o == 'lshr': return 'lshr_<%s>(%s,%s,%d)' % (t, a[0], a[1], w)
        if o == 'ashr': return 'ashr_<%s>(%s,%s,%d,%s)' % (t, a[0], a[1], w, M)
        if o == 'udiv': return '(%s ? %s / %s : %s)' % (a[1], a[0], a[1], M)
        if o == 'urem': return '(%s ? %s %% %s : %s)' % (a[1], a[0], a[1], a[0])
        sw = n.a[0].w if n.a else 0
        if o in ('eq', 'ult', 'slt') and n.a[0] is n.a[1]: return '(u32)%d' % (o == 'eq')       # x == x / x < x (and -Wtautological-compare)
        if o == 'eq': return '(u32)(%s == %s)' % tuple(a)
        if o == 'ult': return '(u32)0' if n.a[1].op == 'const' and n.a[1].x == 0 else '(u32)(%s < %s)' % tuple(a)      # x < 0 is false (and -Wtype-limits)
        if o == 'slt': S = K(1 << (sw - 1), sw); return '(u32)((%s ^ %s) < (%s ^ %s))' % (a[0], S, a[1], S)
        if o == 'ite': return '(%s ? %s : %s)' % tuple(a)
        if o == 'zext': return '((%s)%s)' % (t, a[0])
        if o == 'sext':
            S = K(1 << (sw - 1), w); e = '((((%s)%s) ^ %s) - %s)' % (t, a[0], S, S)
            return e if full else '(%s & %s)' % (e, M)
        if o == 'extract':
            hi, lo = n.x; e = a[0] if lo == 0 else '(%s >> %d)' % (a[0], lo)
            return '((%s)%s)' % (t, e) if hi == sw - 1 else '((%s)(%s & %s))' % (t, e, M)
        if o == 'concat': return '((((%s)%s) << %d) | %s)' % (t, a[0], n.a[1].w, a[1])
        if o == 'load': return '((%s)%s%d(%s))' % (t, s.macc, w, a[0])
        if o == 'uf': return UF_C[n.x](*a)
        if o == 'cvt': return 'fx_dsl_cvt(%d, %d, %d, %d, %s, %s)' % (0 if n.x[0] == 'i2f' else 1, n.x[1], n.x[2], int(n.x[3]), a[0], a[1])
        if o == 'tab': return 'fnvl_tab_%s(%s)' % (n.x, a[0])
        if o == 'fp': return 'fx_dsl_fp(%d, %d, %s, %s, %s)' % (FPOPS.index(n.x[0]), n.x[1], a[0], a[1], a[2])
        raise ValueError(o)

class InlineEmit(Emit):
    """Emit for code inlined into a lifted function (Form.to_c_inline): the shifts are expanded in place (the lifter's runtime has no shl_/lshr_/ashr_
    templates), so the count (and the shifted value of ashr) is named once, as a temporary, when it is not a constant or a variable."""
    def __init__(s, roots, force=()):
        super().__init__(roots)
        pick = [n.a[1] for n in s.order if n.op in ('shl', 'lshr', 'ashr')] + [n.a[0] for n in s.order if n.op == 'ashr'] + list(force)
        for n in pick:
            if n.op not in ('const', 'var') and n.u not in s.names: s.names[n.u] = 't%d' % len(s.names)
    def ex(s, n):
        if n.op not in ('shl', 'lshr', 'ashr'): return super().ex(n)
        w = n.w; M = K(mask(w), w); t = T(w); a, c = s.ref(n.a[0]), s.ref(n.a[1]); Z = '(%s)0' % t
        if n.op == 'shl': e = '(%s >= %du ? %s : (%s)(%s << %s))' % (c, w, Z, t, a, c); return e if w in (32, 64, 128) else '(%s & %s)' % (e, M)
        if n.op == 'lshr': return '(%s >= %du ? %s : (%s)(%s >> %s))' % (c, w, Z, t, a, c)
        sg = '(((%s >> %d) & 1) != 0)' % (a, w - 1)
        return '(%s >= %du ? (%s ? %s : %s) : (%s)((%s >> %s) | (%s ? (%s ^ (%s >> %s)) : %s)))' % (c, w, sg, M, Z, t, a, c, sg, M, M, c, Z)

PRELUDE = '''typedef unsigned __int128 u128; typedef unsigned char u8; typedef unsigned short u16; typedef unsigned int u32; typedef unsigned long long u64;
template<class T> static inline T shl_(T a, T c, unsigned w) { return c >= w ? (T)0 : (T)(a << c); }
template<class T> static inline T lshr_(T a, T c, unsigned w) { return c >= w ? (T)0 : (T)(a >> c); }
template<class T> static inline T ashr_(T a, T c, unsigned w, T m) { T s = (a >> (w - 1)) & 1; if (c >= w) return s ? m : (T)0; T r = a >> c; if (s) r |= m ^ (m >> c); return r; }
'''
SEGS = ('es', 'cs', 'ss', 'ds', 'fs', 'gs')      # the segment registers in rt/fnvl.h SEG_* order
XIN = ('df', 'flo', 'fs_base', 'gs_base') + tuple('sel_' + s for s in SEGS)          # other pre-state: DF (1 bit), the other EFLAGS bits (32, masked), segment bases, the six segment selectors (16 bits, = c->sel[SEG_*])
SIG = ','.join('[[maybe_unused]] u32& ' + r for r in REGS + FLAGS + XIN) + ',u32& eip'
from fnvlift.faults import FAULTS, FLT_STATUS, BYNAME as _FBY      # the one fault table (fnvlift/faults.json); kind = runtime code
INW = {'df': 1, 'flo': 32, 'fs_base': 32, 'gs_base': 32, **{'sel_' + s: 16 for s in SEGS}}

def xc_lines(kind, i, x, ref):
    """C lines of one x87 / MMX / SSE state effect, on struct cpu as rt/fnvl.h and rt/fnvl_x87.h define it (the helpers fx_dsl_* are the one place that maps the architectural value to the
    carriers): fr<i> = the physical register i (an empty register keeps the written bits, a valid one becomes an M80 carrier), ftop = c->ftop, ftag = fx_dsl_ftag_set (11 frees the register,
    any other tag makes an empty one valid with the bits it holds), fsw = the status word without TOP (sticky flags and C1 in c->fex, ES/B in c->fsw, C0 C2 C3 in the function's c0 c2 c3),
    fptr = c->fip fdp fop fcs fds, xmm<i> = c->x[i], mxcsr = c->mxcsr (the host FPCR rounding mode follows, the flags of the FPSR the SSE family owns are replaced)."""
    if kind == 'fr': return ['fx_dsl_fr_set(%d, %s);' % (i, ref(x))]
    if kind == 'ftop': return ['c->ftop = (%s) & 7u;' % ref(x)]
    if kind == 'ftag': return ['fx_dsl_ftag_set(%s);' % ref(x)]
    if kind == 'fsw': return ['FX_DSL_FSW_SET(%s);' % ref(x)]
    if kind == 'fptr':
        return ['c->%s = %s;' % (f, ref(n) if f != 'fop' else '(%s) & 0x7ffu' % ref(n)) for f, n in zip(('fip', 'fdp', 'fop', 'fcs', 'fds'), x)]
    if kind == 'xmm': return ['fx_dsl_xmm_set(%d, %s);' % (i, ref(x))]
    if kind == 'mxcsr': return ['fx_dsl_mxcsr_set(%s);' % ref(x)]
    raise ValueError(kind)

class Form:
    def __init__(s, key, addr, size):
        s.key, s.regs, s.flags, s.undef, s.stores, s.faults, s.addr = key, [], [], {}, [], [], addr
        s.gaps = []       # model gaps (texts): outputs whose refinement the pinned model cannot state yet; they are not proven by DEF0 (def_check counts them)
        s.uwhen = {}      # flag name -> 1-bit node: the flag is undefined only when it holds (absent = unconditionally undefined); the emitted value is cond ? 0 : prior
        s.swhen = {}      # index in stores -> 1-bit node: the store happens only when it holds (absent = unconditional)     # addr: int, or a node (V('eip', 32)) for a form of a whole shape
        s.eip = C(addr + size, 32); s.pend = {}; s.flo = None
        s.mmw = []        # MMX register writes of a builder, (N, 64-bit value): proof/forms.build hands them to mmx() once the handler is done
        s.fxm = False     # fault_xm(): on the SIMD floating-point exception fault the Form's MXCSR effect (the exception flags) still happens, every other effect does not (SDM Vol 1 11.5.2, Vol 3 6.15)
        s.xs = []         # x87 / MMX / SSE state effects, in order: (kind, index or None, node, ...); the later effect on one target wins (kinds: fr ftop ftag fsw fptr xmm mxcsr)
    def cur(s, name): return s.pend.get(name, V(name, 32))      # pending value, only for composing partial-register writes
    def reg(s, name, v, when=None): assert v.w == 32; s.regs.append((name, v, when)); s.pend[name] = v
    def flag(s, name, v, when=None): assert v.w == 1 and name in FLAGS + ('df',); s.flags.append((name, v, when))
    def setflo(s, v): assert v.w == 32; s.flo = v
    def undefined(s, name, sdm, when=None):
        """the flag NAME is left undefined by the SDM (text SDM, cited). WHEN: a 1-bit node over the pre-state, stated as in the SDM flag table, under which the flag
        is undefined; where it is false the flag keeps its prior value (the SDM leaves it unchanged). None = unconditionally undefined. The condition is not trusted:
        the prover checks cond => model free (value 0 refines) and not cond => the model's value is the prior one."""
        s.undef[name] = sdm
        if when is not None: assert when.w == 1; s.uwhen[name] = when
    def flag_line(s, f, v, w, ref):
        """C text of one flag write. DEF0 (orchestrator, 2026-11-12): a flag output that the pinned model leaves free is the constant 0, so a write with `when` w is
        `w ? v : 0` (v is the value where the SDM defines it)"""
        return '%s = %s;' % (f, ref(v)) if w is None else '%s = %s ? %s : 0;' % (f, ref(w), ref(v))
    def undefined_outputs(s):
        """-> (flag outputs the model leaves free, non-flag outputs the model leaves free): the flags of `undef` and of a write with `when`; the registers of a
        write with `when`. DEF0 gives the flags the value 0, DEFPRIOR gives the registers their pre-state value."""
        fl = sorted(set(s.undef) | {f for f, _, w in s.flags if w is not None})
        rg = sorted({r for r, _, w in s.regs if w is not None})
        return fl, rg
    def store(s, addr, v, when=None):
        if when is not None: assert when.w == 1; s.swhen[len(s.stores)] = when
        s.stores.append((addr, v))
    def fault(s, code, cond): assert cond.w == 1; s.faults.append((code, cond))
    def fault_xm(s, cond):
        """the SIMD floating-point exception (#XM, fault kind XM; Wine 9.0 fpe_handler STATUS_FLOAT_MULTIPLE_TRAPS) when `cond` (an exception flag set whose MXCSR mask bit is clear).
        SDM Vol 1 11.5.2 / Vol 3 6.15: the exception flags are set in MXCSR and the destination is left unchanged; so on this fault the Form's mxcsr effect (xs kind mxcsr) is applied and nothing else"""
        s.fault(_FBY['XM']['code'], cond); s.fxm = True
    def model_gap(s, text): s.gaps.append(text)
    # ---- x87 / MMX / SSE state effects (the whole new value of the target; every expression reads the pre-state)
    def fr(s, i, e): assert 0 <= i < 8 and e.w == 80; s.xs.append(('fr', i, e))
    def ftop(s, e): assert e.w == 3; s.xs.append(('ftop', None, e))
    def ftag(s, e): assert e.w == 16; s.xs.append(('ftag', None, e))
    def fsw(s, e): assert e.w == 16; s.xs.append(('fsw', None, e))
    def fptr(s, fip, fdp, fop, fcs, fds):
        assert (fip.w, fdp.w, fop.w, fcs.w, fds.w) == (32, 32, 11, 16, 16); s.xs.append(('fptr', None, (fip, fdp, fop, fcs, fds)))
    def xmm(s, i, e): assert 0 <= i < 8 and e.w == 128; s.xs.append(('xmm', i, e))
    def mxcsr(s, e): assert e.w == 32; s.xs.append(('mxcsr', None, e))
    def mmx(s, writes=(), emms=False):
        """THE MMX rule (SDM Vol 1 9.5.1, Table 9-1), the one helper every MMX Form uses. writes: (N, e64) pairs, the 64-bit value written to mmN.
        Every MMX instruction except EMMS: mmN = fr<N>[63:0] and the write sets fr<N>[79:64] = 0xFFFF; TOP = 0; every tag valid (00).  EMMS: every tag empty (11), nothing else."""
        if emms:
            assert not writes; s.ftag(C(0xFFFF, 16)); return
        for n, e in writes:
            assert e.w == 64; s.fr(n, concat(C(0xFFFF, 16), e))
        s.ftop(C(0, 3)); s.ftag(C(0, 16))
    def xnodes(s):
        return [x for k, _, e in s.xs for x in (e if k == 'fptr' else (e,))]
    def undef_line(s, f, ref):
        """C text of the DEF0 value of the undefined flag f: 0, or `cond ? 0 : prior` for a conditional one"""
        return '%s = 0;' % f if f not in s.uwhen else '%s = %s ? 0 : %s;' % (f, ref(s.uwhen[f]), ref(V(f, 1)))
    def roots(s):
        return [x for f in sorted(s.uwhen) for x in (s.uwhen[f], V(f, 1))] + [v for _, v, _ in s.regs] + [w for _, _, w in s.regs if w is not None] + [v for _, v, _ in s.flags] + \
               [w for _, _, w in s.flags if w is not None] + [x for st in s.stores for x in st] + [c for _, c in s.faults] + [s.swhen[i] for i in sorted(s.swhen)] + \
               ([s.flo] if s.flo is not None else []) + s.xnodes() + [s.eip]
    def serialize(s):
        """canonical DAG text (hash input of the certificates)"""
        ids, out = {}, []
        def go(n):
            if n.u in ids: return ids[n.u]
            a = [go(c) for c in n.a]; ids[n.u] = i = len(ids)
            out.append('n%d=%s/%d %s %s' % (i, n.op, n.w, ','.join('n%d' % j for j in a), n.x if n.x is not None else ''))
            return i
        nw = lambda w: 'n%d' % go(w) if w is not None else '-'
        for r, v, w in s.regs: out.append('reg %s n%d %s' % (r, go(v), nw(w)))
        for f, v, w in s.flags: out.append('flag %s n%d %s' % (f, go(v), nw(w)))
        for f in sorted(s.undef): out.append('undef ' + f + (' when n%d' % go(s.uwhen[f]) if f in s.uwhen else ''))
        for i, (a, v) in enumerate(s.stores): out.append('store n%d n%d' % (go(a), go(v)) + (' when n%d' % go(s.swhen[i]) if i in s.swhen else ''))
        for k, c in s.faults: out.append('fault %d n%d' % (k, go(c)))
        if s.fxm: out.append('fault_xm')
        if s.flo is not None: out.append('flo n%d' % go(s.flo))
        for k, i, e in s.xs: out.append('x %s %s %s' % (k, '-' if i is None else i, ' '.join('n%d' % go(x) for x in (e if k == 'fptr' else (e,)))))
        out.append('eip n%d @%s' % (go(s.eip), '%x' % s.addr if isinstance(s.addr, int) else 'n%d' % go(s.addr)))
        return '\n'.join(out)
    def sha(s): return hashlib.sha256(s.serialize().encode()).hexdigest()
    def fault_term(s):
        import z3
        r = z3.BitVecVal(0, 8)
        for k, c in reversed(s.faults): r = z3.If(to_smt(c) == 1, z3.BitVecVal(k, 8), r)
        return r
    def post(s):
        """z3 post-state over the pre-state variables.  {'regs','flags'(six+df),'flo','mem','eip','defined'(flags),'rdefined'(regs),'fault'}.
        On a fault the state is unchanged and eip = the instruction address (fields are guarded by fault == 0)."""
        import z3
        regs = {r: z3.BitVec(r, 32) for r in REGS}; flags = {f: z3.BitVec(f, 1) for f in FLAGS + ('df',)}
        defined = {f: (z3.BoolVal(f not in s.undef) if f not in s.uwhen else to_smt(s.uwhen[f]) == 0) for f in flags}; rdef = {r: z3.BoolVal(True) for r in REGS}
        for r, v, w in s.regs:
            regs[r] = to_smt(v)
            if w is not None: rdef[r] = to_smt(w) == 1
        for f, v, w in s.flags:
            flags[f] = to_smt(v)
            if w is not None: defined[f] = to_smt(w) == 1
        mem = mem_array(); flo = to_smt(s.flo) if s.flo is not None else z3.BitVec('flo', 32)
        for k, (a, v) in enumerate(s.stores):
            sv = to_smt(v); m2 = mem
            for i in range(v.w // 8): m2 = z3.Store(m2, to_smt(a) + i, z3.Extract(8 * i + 7, 8 * i, sv))
            mem = z3.If(to_smt(s.swhen[k]) == 1, m2, mem) if k in s.swhen else m2
        fr_ = [z3.BitVec('fr%d' % i, 80) for i in range(8)]; xm_ = [z3.BitVec('xmm%d' % i, 128) for i in range(8)]
        x = {'ftop': z3.BitVec('ftop', 3), 'ftag': z3.BitVec('ftag', 16), 'fsw': z3.BitVec('fsw', 16), 'mxcsr': z3.BitVec('mxcsr', 32),
             'fptr': tuple(z3.BitVec(n, w) for n, w in (('fip', 32), ('fdp', 32), ('fop', 11), ('fcs', 16), ('fds', 16)))}
        pre_x = (list(fr_), list(xm_), dict(x))
        for k, i, e in s.xs:
            if k == 'fr': fr_[i] = to_smt(e)
            elif k == 'xmm': xm_[i] = to_smt(e)
            elif k == 'fptr': x['fptr'] = tuple(to_smt(n) for n in e)
            else: x[k] = to_smt(e)
        eip = to_smt(s.eip); ft = s.fault_term()
        if s.faults:
            nf = ft == 0; g = lambda new, old: z3.If(nf, new, old)
            regs = {r: g(regs[r], z3.BitVec(r, 32)) for r in regs}; flags = {f: g(flags[f], z3.BitVec(f, 1)) for f in flags}
            flo = g(flo, z3.BitVec('flo', 32)); mem = g(mem, mem_array())
            fr_ = [g(a, b) for a, b in zip(fr_, pre_x[0])]; xm_ = [g(a, b) for a, b in zip(xm_, pre_x[1])]
            x = {k: (tuple(g(a, b) for a, b in zip(v, pre_x[2][k])) if k == 'fptr' else z3.If(nf, v, z3.If(ft == _FBY['XM']['code'], v, pre_x[2][k])) if k == 'mxcsr' and s.fxm else g(v, pre_x[2][k])) for k, v in x.items()}
            eip = g(eip, z3.BitVecVal(s.addr, 32) if isinstance(s.addr, int) else to_smt(s.addr))
        return {'regs': regs, 'flags': flags, 'flo': flo, 'mem': mem, 'eip': eip, 'defined': defined, 'rdefined': rdef, 'fault': ft,
                'fr': fr_, 'xmm': xm_, 'ftop': x['ftop'], 'ftag': x['ftag'], 'fsw': x['fsw'], 'fptr': x['fptr'], 'mxcsr': x['mxcsr']}
    def writes_unobserved(s): return s.flo is not None or any(f == 'df' for f, _, _ in s.flags)
    def to_c_inline(s, varc, fault_fn, flo_fn, force=(), macc='M'):
        """C lines of ONE instruction to be placed inside a block of a lifted function: -> (lines, ref) where ref(node) is the C text of a node
        usable AFTER the lines (a named temporary or an expression over the `in_` snapshots).  Order = the Form's: snapshots of the used pre-state
        (varc(name) -> the C expression of a pre-state variable), temporaries for loads and shared sub-expressions (and the `force`d nodes), faults
        (fault_fn(code, cond_node, ref) -> C line), register writes, flag writes (DEF0: a write with `when` w is `w ? v : 0`, a flag the Form declares undefined is 0),
        the other-EFLAGS write (flo_fn(ref of the value) -> C line), stores.  The eip node is not emitted: the caller derives the control transfer from it."""
        e = InlineEmit(s.roots(), force); e.macc = macc; L = []
        for u in sorted(e.used): L.append('const %s in_%s = %s;' % (T(XW.get(u, 32)), u, varc(u)))
        for n in e.order:
            if n.u in e.names: L.append('const %s %s = %s;' % (T(n.w), e.names[n.u], e.ex(n)))
        xm = _FBY['XM']['code']
        for k, c in s.faults:
            if k == xm and s.fxm:      # fault_xm: MXCSR takes the Form's effect just before this fault is raised (an earlier fault of the Form raises first and leaves MXCSR alone)
                L.append('if (%s) { %s }' % (e.ref(c), ' '.join(xc_lines('mxcsr', None, [x for kk, i, x in s.xs if kk == 'mxcsr'][-1], e.ref))))
            L.append(fault_fn(k, c, e.ref))
        cw = lambda w: '' if w is None else 'if (%s) ' % e.ref(w)
        for r, v, w in s.regs: L.append('%s%s = %s;' % (cw(w), r, e.ref(v)))
        for f, v, w in s.flags: L.append(s.flag_line(f, v, w, e.ref))        # DEF0: a flag the model leaves free is the constant 0
        for f in sorted(s.undef): L.append(s.undef_line(f, e.ref))             # DEF0: a flag the Form declares undefined is the constant 0
        if s.flo is not None: L.append(flo_fn(e.ref(s.flo)))
        for k, i, x in s.xs: L.extend(xc_lines(k, i, x, e.ref))
        for k, (a, v) in enumerate(s.stores): L.append('%s%s%d(%s) = %s;' % (('if (%s) ' % e.ref(s.swhen[k])) if k in s.swhen else '', macc, v.w, e.ref(a), e.ref(v)))
        return L, e.ref
    def to_c(s, fname):
        assert not s.xs, 'a Form with x87 / MMX / SSE effects is emitted by to_c_inline only (its state is struct cpu, not the SIG arguments)'
        assert not (set(Emit(s.roots()).used) & set(XW)), 'a Form that reads the x87 / MMX / SSE state is emitted by to_c_inline only'
        e = Emit(s.roots()); L = []
        L.append('static u32 %s(%s) {' % (fname, SIG))
        for u in sorted(e.used): L.append('  const u32 in_%s = %s;' % (u, u))
        for n in e.order:
            if n.u in e.names: L.append('  const %s %s = %s;' % (T(n.w), e.names[n.u], e.ex(n)))
        for k, c in s.faults: L.append('  if (%s) { eip = %s; return %d; }' % (e.ref(c), K(s.addr, 32), k))
        for r, v, w in s.regs: L.append('  %s%s = %s;' % ('' if w is None else 'if (%s) ' % e.ref(w), r, e.ref(v)))      # DEFPRIOR: a register the model leaves free (`when` false) keeps its prior value
        for f, v, w in s.flags: L.append('  ' + s.flag_line(f, v, w, e.ref))
        for f in sorted(s.undef): L.append('  ' + s.undef_line(f, e.ref))
        if s.flo is not None: L.append('  flo = %s;' % e.ref(s.flo))
        for k, (a, v) in enumerate(s.stores): L.append('  %sM%d(%s) = %s;' % (('if (%s) ' % e.ref(s.swhen[k])) if k in s.swhen else '', v.w, e.ref(a), e.ref(v)))
        L.append('  eip = %s;\n  return 0;\n}' % e.ref(s.eip))
        return '\n'.join(L)
