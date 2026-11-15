"""SSE / SSE2 floating-point arithmetic forms: add sub mul div sqrt, scalar (ss, sd) and packed (ps, pd), SDM Vol 2 per mnemonic.  Each lane is one dsl.fp node (the one rule set,
fnvlift/dsl.py fp: SDM Vol 1 4.8.3, 4.9.1, 10.2.3, 11.5) on the source type; the status flags of the lanes are ORed into the sticky MXCSR flags (bits 0-5).  An UNMASKED exception (the new
flag set and its MXCSR mask bit 7-12 clear) is ONE fault, XM (Wine 9.0 fpe_handler: a SIMD exception is STATUS_FLOAT_MULTIPLE_TRAPS, never a per-flag status); on it the exception flags are set in MXCSR and the destination
is unchanged (SDM Vol 1 11.5.2, Vol 3 6.15: dsl Form.fault_xm).  Aligned 128-bit memory operands fault with kind 7 when misaligned (forms_simd.align)."""
from fnvlift import dsl
from fnvlift.dsl import C, V, bit, concat, extract, fp, fp_flags, fp_val, zext
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
