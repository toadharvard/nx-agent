"""SSE / SSE2 floating-point arithmetic forms: add sub mul div sqrt, scalar (ss, sd) and packed (ps, pd), SDM Vol 2 per mnemonic.  Each lane is one dsl.fp node (the one rule set,
fnvlift/dsl.py fp: SDM Vol 1 4.8.3, 4.9.1, 10.2.3, 11.5) on the source type; the status flags of the lanes are ORed into the sticky MXCSR flags (bits 0-5).  An UNMASKED exception (the new
flag set and its MXCSR mask bit 7-12 clear) is a fault of one kind per NTSTATUS in Wine 9.0 get_fpu_code's order (IE, DE, ZE, OE, UE, PE; fnvlift/faults.json FLT_*), and a fault leaves the whole
state, MXCSR included, unchanged.  Aligned 128-bit memory operands fault with kind 7 when misaligned (forms_simd.align)."""
from fnvlift import dsl
from fnvlift.dsl import C, V, bit, concat, extract, fp, fp_flags, fp_val, zext
from fnvlift.faults import BYNAME
from proof.forms import handler, rd, wr
from proof.forms_simd import align, join, lanes

_FLT = [BYNAME[n]['code'] for n in ('FLT_IE', 'FLT_DE', 'FLT_ZE', 'FLT_OE', 'FLT_UE', 'FLT_PE')]       # flag bit i of MXCSR 0-5, mask bit i + 7
OPS = {'add': 'add', 'sub': 'sub', 'mul': 'mul', 'div': 'div', 'sqrt': 'sqrt'}
def arith(c, op, w, packed):
    c.aligned = True; d, s = c.ops; mx = V('mxcsr', 32); a = rd(d); n = d.w
    if packed: align(c, s) if s.k == 'm' else None
    b = rd(s)
    if packed: A, B = lanes(a, w), lanes(b, w)
    else: A, B = [extract(a, w - 1, 0)], [rd(s, w)]
    if op == 'sqrt': A = B                        # sqrt reads only the source operand
    nodes = [fp(op, w, x, y, mx) for x, y in zip(A, B)]
    flags = nodes and fp_flags(nodes[0])
    for nd in nodes[1:]: flags = flags | fp_flags(nd)
    flags32 = zext(flags, 32)
    vals = [fp_val(nd) for nd in nodes]
    r = join(vals) if packed else concat(extract(a, 127, w), vals[0])
    for i, k in enumerate(_FLT): c.F.fault(k, bit(flags32, i) & ~bit(mx, 7 + i))
    c.F.mxcsr(mx | flags32); wr(c.F, d, r)
for _op in OPS:
    for _sfx, _w, _p in (('ss', 32, False), ('sd', 64, False), ('ps', 32, True), ('pd', 64, True)):
        handler(_op + _sfx)(lambda c, op=_op, w=_w, p=_p: arith(c, op, w, p))
