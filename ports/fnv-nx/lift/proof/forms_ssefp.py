"""SSE / SSE2 floating-point arithmetic forms: add sub mul div sqrt, scalar (ss, sd) and packed (ps, pd), SDM Vol 2 per mnemonic.  Each lane is one dsl.fp node (the one rule set,
fnvlift/dsl.py fp: SDM Vol 1 4.8.3, 4.9.1, 10.2.3, 11.5) on the source type; the status flags of the lanes are ORed into the sticky MXCSR flags (bits 0-5).  An UNMASKED exception (the new
flag set and its MXCSR mask bit 7-12 clear) is ONE fault, XM (Wine 9.0 fpe_handler: a SIMD exception is STATUS_FLOAT_MULTIPLE_TRAPS, never a per-flag status); on it the exception flags are set in MXCSR and the destination
is unchanged (SDM Vol 1 11.5.2, Vol 3 6.15: dsl Form.fault_xm).  Aligned 128-bit memory operands fault with kind 7 when misaligned (forms_simd.align)."""
from fnvlift import dsl
from fnvlift.dsl import C, V, bit, concat, cvt, cvt_flags, cvt_val, eq, extract, fp, fp_flags, fp_val, ite, tab, ult, zext
from fnvlift.faults import BYNAME
from proof.forms import handler, rd, wr
from proof.forms_simd import align, join, lanes

def _unmasked(flags32, mx):
    """some exception flag i (MXCSR bits 0-5) is set whose mask bit i + 7 is clear (SDM Vol 1 11.5.1)"""
    r = bit(flags32, 0) & ~bit(mx, 7)
    for i in range(1, 6): r = r | (bit(flags32, i) & ~bit(mx, 7 + i))
    return r
OPS = {'add': 'add', 'sub': 'sub', 'mul': 'mul', 'div': 'div', 'sqrt': 'sqrt', 'min': 'min', 'max': 'max'}
def arith(c, op, w, packed, pred=None):
    c.aligned = True; d, s = c.ops; mx = V('mxcsr', 32); a = rd(d); n = d.w
    if packed: align(c, s) if s.k == 'm' else None
    b = rd(s)
    if packed: A, B = lanes(a, w), lanes(b, w)
    else: A, B = [extract(a, w - 1, 0)], [rd(s, w)]
    if op == 'sqrt': A = B                        # sqrt reads only the source operand
    nodes = [fp(op, w, x, y, mx) for x, y in zip(A, B)]   # op: arithmetic, min/max, or 'cmp'+predicate (a lane of all ones / zeros)
    flags = nodes and fp_flags(nodes[0])
    for nd in nodes[1:]: flags = flags | fp_flags(nd)
    flags32 = zext(flags, 32)
    vals = [fp_val(nd) for nd in nodes]
    r = join(vals) if packed else concat(extract(a, 127, w), vals[0])
    c.F.fault_xm(_unmasked(flags32, mx))
    c.F.mxcsr(mx | flags32); wr(c.F, d, r)
for _op in OPS:
    for _sfx, _w, _p in (('ss', 32, False), ('sd', 64, False), ('ps', 32, True), ('pd', 64, True)):
        handler(_op + _sfx)(lambda c, op=_op, w=_w, p=_p: arith(c, op, w, p))
for _p in dsl.CMPPRED:
    for _sfx, _w, _pk in (('ss', 32, False), ('sd', 64, False), ('ps', 32, True), ('pd', 64, True)):
        handler('cmp' + _p + _sfx)(lambda c, op='cmp' + _p, w=_w, p=_pk: arith(c, op, w, p))
def comi(c, op, w):
    """COMISS/UCOMISS/COMISD/UCOMISD: ZF PF CF = unordered / equal / less (SDM Vol 2); OF SF AF = 0; IE (QNaN operands only for ucomi) and DE go to MXCSR."""
    c.aligned = False; d, s = c.ops; mx = V('mxcsr', 32); nd = fp(op, w, extract(rd(d), w - 1, 0), rd(s, w), mx)
    v = fp_val(nd); flags32 = zext(fp_flags(nd), 32)
    c.F.fault_xm(_unmasked(flags32, mx))
    c.F.mxcsr(mx | flags32)
    c.F.flag('zf', bit(v, 2)); c.F.flag('pf', bit(v, 1)); c.F.flag('cf', bit(v, 0))
    for f in ('of', 'sf', 'af'): c.F.flag(f, C(0, 1))
for _op in ('comi', 'ucomi'):
    for _sfx, _w in (('ss', 32), ('sd', 64)):
        handler(_op + _sfx)(lambda c, op=_op, w=_w: comi(c, op, w))

# ---------------------------------------------------------------- conversions, reciprocal, MXCSR, hints (the spec rows of L3b's simdspec.py, ported to the DSL; one rule per class)
def _xm(c, flags, mx):
    """the sticky flags of the instruction into MXCSR, and the one XM fault for an unmasked one (the same two lines as arith)"""
    flags32 = zext(flags, 32); c.F.fault_xm(_unmasked(flags32, mx)); c.F.mxcsr(mx | flags32)
def _orflags(nodes, f):
    r = f(nodes[0])
    for n in nodes[1:]: r = r | f(n)
    return r
def cvt_i2f(c, w, nlanes, srcw):
    """CVTSI2SS / CVTSI2SD (the low lane, the rest of the destination kept), CVTDQ2PS / CVTDQ2PD / CVTPI2PS (nlanes int32 lanes of the source into nlanes lanes of w bits, SDM Vol 2); the integer source is signed 32 bits"""
    c.aligned = True; d, s = c.ops; mx = V('mxcsr', 32); a = rd(d)
    if s.k == 'm' and s.w == 128: align(c, s)
    b = rd(s, srcw)
    sc = nlanes == 1
    xs = [b] if sc else lanes(b, 32)[:nlanes]
    nodes = [cvt('i2f', x, mx, w) for x in xs]; vals = [cvt_val(n) for n in nodes]
    used = w * nlanes; r = join(vals) if nlanes > 1 else vals[0]
    out = concat(extract(a, 127, used), r) if used < 128 else r
    if c.m == 'cvtpi2ps' and s.k == 'm': c.F.mmx()          # an MMX instruction: SDM Vol 1 9.5.1, spec row _cvt_pi2ps (the register form gets it from build)
    _xm(c, _orflags(nodes, cvt_flags), mx); wr(c.F, d, out)
handler('cvtsi2ss')(lambda c: cvt_i2f(c, 32, 1, 32)); handler('cvtsi2sd')(lambda c: cvt_i2f(c, 64, 1, 32))
handler('cvtdq2ps')(lambda c: cvt_i2f(c, 32, 4, 128)); handler('cvtdq2pd')(lambda c: cvt_i2f(c, 64, 2, 64)); handler('cvtpi2ps')(lambda c: cvt_i2f(c, 32, 2, 64))
def cvt_f2i(c, srcw, nlanes, trunc, packed):
    """CVTSS2SI CVTSD2SI CVTTSS2SI CVTTSD2SI (a general register), CVTPS2DQ CVTTPS2DQ CVTPD2DQ CVTTPD2DQ (the lanes into the low int32 lanes, the rest of the 128 bits zero)"""
    c.aligned = True; d, s = c.ops; mx = V('mxcsr', 32)
    if s.k == 'm' and s.w == 128: align(c, s)
    b = rd(s, srcw * nlanes)
    xs = lanes(b, srcw) if packed else [b]
    nodes = [cvt('f2i', x, mx, 32, trunc) for x in xs]; vals = [cvt_val(n) for n in nodes]
    out = (zext(join(vals), 128) if len(vals) * 32 < 128 else join(vals)) if packed else vals[0]
    _xm(c, _orflags(nodes, cvt_flags), mx); wr(c.F, d, out)
for _t, _pf in ((False, ''), (True, 't')):
    handler('cvt%sss2si' % _pf)(lambda c, t=_t: cvt_f2i(c, 32, 1, t, False)); handler('cvt%ssd2si' % _pf)(lambda c, t=_t: cvt_f2i(c, 64, 1, t, False))
    handler('cvt%sps2dq' % _pf)(lambda c, t=_t: cvt_f2i(c, 32, 4, t, True)); handler('cvt%spd2dq' % _pf)(lambda c, t=_t: cvt_f2i(c, 64, 2, t, True))

def rcp32(x):
    """RCPSS / RCPPS lane: the reference CPU's table (rt/rcptab.c header, structure transcribed from fnvl_rcp32)"""
    sg = x & C(0x80000000, 32); e = extract(x, 30, 23); m = extract(x, 22, 0)
    r = sg | ((C(253, 32) - zext(e, 32)) << C(23, 32)) | (zext(tab('rcp', extract(x, 22, 12)), 32) << C(11, 32))
    return ite(eq(e, C(255, 8)), ite(eq(m, C(0, 23)), sg, x | C(0x400000, 32)), ite(eq(e, C(0, 8)), sg | C(0x7F800000, 32), ite(ult(e, C(253, 8)), r, sg)))
def rsqrt32(x):
    """RSQRTSS / RSQRTPS lane (structure of fnvl_rsqrt32)"""
    sg = x & C(0x80000000, 32); e = extract(x, 30, 23); m = extract(x, 22, 0); neg = ~eq(sg, C(0, 32))
    r = (((C(380, 32) - zext(e, 32)) >> C(1, 32)) << C(23, 32)) | (zext(tab('rsqrt', concat(extract(e, 0, 0), extract(x, 22, 13))), 32) << C(11, 32))
    return ite(eq(e, C(255, 8)), ite(eq(m, C(0, 23)), ite(neg, C(0xFFC00000, 32), C(0, 32)), x | C(0x400000, 32)), ite(eq(e, C(0, 8)), sg | C(0x7F800000, 32), ite(neg, C(0xFFC00000, 32), r)))
def recip(c, fn, scalar):
    """no MXCSR state is read or written (rcptab.c header: the result is a function of the 32 input bits)"""
    c.aligned = True; d, s = c.ops; a = rd(d)
    if not scalar and s.k == 'm': align(c, s)
    b = rd(s, 32 if scalar else 128)
    wr(c.F, d, concat(extract(a, 127, 32), fn(b)) if scalar else join([fn(x) for x in lanes(b, 32)]))
handler('rcpss')(lambda c: recip(c, rcp32, True)); handler('rcpps')(lambda c: recip(c, rcp32, False))
handler('rsqrtss')(lambda c: recip(c, rsqrt32, True)); handler('rsqrtps')(lambda c: recip(c, rsqrt32, False))

def _mxcsr_mask():
    """MXCSR_MASK of the declared CPU: verify/cpuid_model.json `mxcsr_mask` (derived data, SDM Vol 1 11.6.6); a file without it gives no Form (no constant stands in for it)"""
    import json, os
    v = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'verify', 'cpuid_model.json'))).get('mxcsr_mask')
    if v is None: raise KeyError('verify/cpuid_model.json has no mxcsr_mask')
    return int(v, 16)
@handler('ldmxcsr')
def _ldmxcsr(c):
    """SDM Vol 2 LDMXCSR: a bit set outside MXCSR_MASK is #GP(0) (reported as the access violation, fault kind 7, like a misaligned SSE operand); otherwise MXCSR = the operand, which replaces the flags too"""
    v = rd(c.ops[0], 32); c.F.fault(7, ~eq(v & C(~_mxcsr_mask() & 0xFFFFFFFF, 32), C(0, 32))); c.F.mxcsr(v)
@handler('stmxcsr')
def _stmxcsr(c): wr(c.F, c.ops[0], V('mxcsr', 32))
@handler('movntps', 'movntpd', 'movntdq')
def _movnt(c):
    """SDM Vol 2 MOVNTPS: a 16-byte store with a non-temporal hint (the hint is not architectural state); the address must be 16-byte aligned (forms_simd.align)"""
    d, s = c.ops; align(c, d); wr(c.F, d, rd(s))
@handler('prefetchnta', 'prefetcht0', 'prefetcht1', 'prefetcht2')
def _prefetch(c):
    """SDM Vol 2 PREFETCHh: a hint; no register, flag or memory change and no fault whatever the address (the operand is not read)"""
@handler('pause')
def _pause(c):
    """SDM Vol 2 PAUSE: a spin-loop hint that behaves as NOP for architectural state"""
@handler('emms')
def _emms(c):
    """SDM Vol 2 EMMS: every x87 tag is empty (11) and nothing else changes (Form.mmx(emms=True), the MMX rule of SDM Vol 1 9.5.1)"""
    c.F.mmx(emms=True)
