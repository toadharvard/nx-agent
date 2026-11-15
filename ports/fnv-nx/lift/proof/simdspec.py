"""MMX / SSE / SSE2 specification as z3 terms (task 2.6, OWNER 10 for what no pinned formal model covers).

The pinned Sail model has no SIMD semantics (every 0F SSE/MMX opcode is 'Opcode Unimplemented' there), so SIMD is certified against this spec, the way x87 is
(x87spec.py, same machinery: the state names, the MMX rule, the fault table).  Sources, in this order:
  * the ACL2 x86isa model at the pinned commit acl2/acl2 3ccb342c, books/projects/x86isa/machine/instructions.  A row's `acl2` cite is file:line of the line
    that decides the value and a quote that is on that line (tools: acl2_cite_check.py DIR checks every cite against the clone);
  * where ACL2 has no instruction (66 0F 6F MOVDQA, ...) the Intel SDM, with `sdm` cite and quote, as x87spec.py does (sdm_cite_check.py path).
Values are plain IEEE (OWNER 8) where a floating-point operation occurs; the integer families are exact.

State (the DSL names, exactly those of Form.post(): proof/forms.py, fnvlift/dsl.py):  d = {'xmm0'..'xmm7': BV128, 'fr0'..'fr7': BV80, 'ftop': BV3, 'ftag': BV16,
'mxcsr': BV32, ...}.  mmN is the low 64 bits of fr<N> (SDM 1:9.5); an MMX write sets bits 79..64 to ones, TOP := 0 and every tag valid (x87spec.mmx_write_effect,
mmx_exec_effect: the one MMX rule).

The operand model is by VALUE: the memory source is the BV `m` the Form loaded at the effective address `a` (the address is certified by the integer path, the
same ea() both sides), a general register source is `gpr[i]`.  An instruction is  run(code, d, m, a, gpr) -> Res  with
  Res.d      the state after the instruction (unchanged when Res.fault holds),
  Res.store  (addr, value, nbits) | None   the one memory write of the instruction,
  Res.gpr    (index, value32) | None       the general register written,
  Res.fault  Bool: the instruction raises the access violation (misaligned 16-byte operand: SDM 2A, 'Exceptions Type 1/4' of the instruction: #GP(0), reported by
             Windows as STATUS_ACCESS_VIOLATION, the fault table's AV code) before any effect.
No other exception exists for these instructions in the declared machine's user mode (CR0.EM = 0, CR0.TS = 0, CR4.OSFXSR = 1: the environment rows of L5's x86sys,
carried as the precondition Res.env over the state variables `cr0_em`, `cr0_ts`, `cr4_osfxsr`; never a constant here).
"""
import os, sys
import z3
from z3 import And, Concat, Extract, If, Not, Or, ULT, UGT, UGE, ULE, ZeroExt, SignExt, LShR
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x87spec as X

K = X.K
AV = X._faults.BYNAME['AV']['code']


class Res:
    def __init__(s, d, store=None, gpr=None, fault=None):
        s.d, s.store, s.gpr = d, store, gpr
        s.fault = z3.BoolVal(False) if fault is None else fault
        s.env = And(K(0, 1) == z3.BitVec('cr0_em', 1), K(0, 1) == z3.BitVec('cr0_ts', 1), K(1, 1) == z3.BitVec('cr4_osfxsr', 1))


def lanes(v, e):                    # low lane first
    return [Extract((i + 1) * e - 1, i * e, v) for i in range(v.size() // e)]


def join(ls):
    r = ls[-1]
    for x in reversed(ls[:-1]): r = Concat(r, x)
    return r


def bmap(e, a, b, f): return join([f(x, y) for x, y in zip(lanes(a, e), lanes(b, e))])


ONES = lambda e: K((1 << e) - 1, e)


# --- the decode of an encoding (prefix 66/F2/F3, 0F, opcode, ModRM, imm8): the SDM's own fields, no capstone ---------------------------------------
class Enc:
    def __init__(s, code):
        i, s.pfx = 0, ''
        while code[i] in (0x66, 0xF2, 0xF3, 0x2E, 0x36, 0x3E, 0x26, 0x64, 0x65, 0x67):    # legacy prefixes; the last of 66/F2/F3 is the mandatory prefix (SDM 2A:2.1.1)
            if code[i] in (0x66, 0xF2, 0xF3): s.pfx = '%02X' % code[i]
            i += 1
        assert code[i] == 0x0F, code.hex()
        s.op = code[i + 1]; s.modrm = code[i + 2]; s.mod, s.reg, s.rm = s.modrm >> 6, (s.modrm >> 3) & 7, s.modrm & 7
        s.mem = s.mod != 3
        s.imm = None              # run() sets the immediate operand (a BV8, symbolic in a shape)


def xmm(d, n): return d['xmm%d' % n]
def mm(d, n): return X.mmx_read(d, n)


def put_xmm(d, n, v): r = dict(d); r['xmm%d' % n] = v; return r
def put_mm(d, n, v): return X.mmx_write_effect(d, n, v)


def with_fault(r, f):
    """a faulting instruction changes nothing (SDM 2A: the exception is raised before the instruction's effects)"""
    r.fault = f
    return r


# --- the rows: (prefix, opcode) -> handler -------------------------------------------------------------------------------------------------------
ROWS = {}                           # (pfx, op, mod3 or None) -> (name, fn, cites)


import collections
Row = collections.namedtuple('Row', 'name fn cites form mw al imm')


def row(pfx, op, name, cites, mod3=None, form='rm', mw=None, al=None, imm=False):
    """form: which ModRM forms the SDM defines for the row: 'rm' both, 'm' memory only, 'r' register only (the other is #UD: the spec has no row, run() raises)"""
    def deco(fn):
        for p in (pfx if isinstance(pfx, tuple) else (pfx,)):
            ROWS[(p, op, mod3)] = Row(name, fn, cites, form, mw, (mw == 128) if al is None else al, imm)
        return fn
    return deco


def misaligned(a): return (a & K(15, 32)) != K(0, 32)


def _src(e, d, m, w):
    """the second operand (mm/xmm register or memory) of width w"""
    if e.mem: return m
    return xmm(d, e.rm) if w == 128 else mm(d, e.rm)


def _w(e, a, m, d, wide, f, aligned=True):
    """dst := f(dst, src) over the operand width: SSE register when wide, else MMX"""
    w = 128 if wide else 64
    src = _src(e, d, m, w); dst = xmm(d, e.reg) if wide else mm(d, e.reg)
    out = f(dst, src)
    r = Res(put_xmm(d, e.reg, out) if wide else put_mm(d, e.reg, out))
    if wide and e.mem and aligned: r.fault = misaligned(a)
    if not wide: r.d = put_mm(d, e.reg, out)
    return r


# cite = (file, def-inst name, quote): the first line of that def-inst's body that contains the quote (acl2_cites.tsv holds the resolved file:line, regenerated by
# acl2_cite_check.py DIR --write and checked by it without --write)
def cite(f, inst, q): return (f, inst, q)


# ==== logical (ACL2 logical.lisp) ====================================================================================================================
def _logic(fn, ins, q, ops):
    for (pfx, op, wide) in ops:
        row(pfx, op, ins, [cite('logical.lisp', 'x86-andp?/andnp?/orp?/xorp?/pand/pandn/por/pxor-Op/En-RM' if wide else 'x86-pand/pandn/por/pxor-mmx', q)], mw=128 if wide else 64)(
            lambda e, d, m, a, g, fn=fn, wide=wide: _w(e, a, m, d, wide, fn))


_logic(lambda x, y: x & y, 'andps', '(logand xmm xmm/mem)', [('', 0x54, True), ('66', 0x54, True), ('66', 0xDB, True)])
_logic(lambda x, y: x & y, 'pand', '(logand src1 src2)', [('', 0xDB, False)])
_logic(lambda x, y: ~x & y, 'andnps', '(logand (lognot xmm) xmm/mem)', [('', 0x55, True), ('66', 0x55, True), ('66', 0xDF, True)])
_logic(lambda x, y: ~x & y, 'pandn', '(logand (lognot src1) src2)', [('', 0xDF, False)])
_logic(lambda x, y: x | y, 'orps', '(logior xmm xmm/mem)', [('', 0x56, True), ('66', 0x56, True), ('66', 0xEB, True)])
_logic(lambda x, y: x | y, 'por', '(logior src1 src2)', [('', 0xEB, False)])
_logic(lambda x, y: x ^ y, 'xorps', '(logxor xmm xmm/mem)', [('', 0x57, True), ('66', 0x57, True), ('66', 0xEF, True)])
_logic(lambda x, y: x ^ y, 'pxor', '(logxor src1 src2)', [('', 0xEF, False)])


# ==== padd / psub / pcmp (ACL2 padd.lisp, psub.lisp, pcmp.lisp) ==========================================================================================
def _sat_s(e, add):
    def g(x, y):
        w = e + 1; r = (SignExt(1, x) + SignExt(1, y)) if add else (SignExt(1, x) - SignExt(1, y))
        hi, lo = K((1 << (e - 1)) - 1, w), K(-(1 << (e - 1)) & ((1 << w) - 1), w)
        return Extract(e - 1, 0, If(r > hi, hi, If(r < lo, lo, r)))              # signed compares: z3's BV > < are signed
    return g


def _sat_u(e, add):
    def g(x, y):
        w = e + 1
        if add:
            r = ZeroExt(1, x) + ZeroExt(1, y); return Extract(e - 1, 0, If(UGT(r, K((1 << e) - 1, w)), K((1 << e) - 1, w), r))
        return If(ULT(x, y), K(0, e), x - y)
    return g


def _simd_int(name, fn, e, mmx_op, sse_op, file, inst_mmx, inst_sse, quote_mmx, quote_sse, mmx_ok=True):
    """one integer lane family: MMX (no prefix) and SSE2 (66) forms with the same opcode"""
    if mmx_ok:
        row('', mmx_op, name, [cite(file, inst_mmx, quote_mmx)], mw=64)(lambda ee, d, m, a, g, fn=fn, e=e: _w(ee, a, m, d, False, lambda x, y: bmap(e, x, y, fn)))
    row('66', sse_op, name, [cite(file, inst_sse, quote_sse)], mw=128)(lambda ee, d, m, a, g, fn=fn, e=e: _w(ee, a, m, d, True, lambda x, y: bmap(e, x, y, fn)))


for k, e, op in (('b', 8, 0xFC), ('w', 16, 0xFD), ('d', 32, 0xFE), ('q', 64, 0xD4)):
    _simd_int('padd' + k, lambda x, y: x + y, e, op, op, 'padd.lisp', 'x86-paddb/paddw/paddd/paddq-mmx', 'x86-paddb/paddw/paddd/paddq-sse',
              '(#x%02x (simd-add-spec (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (simd-add-spec (* 8 operand-size) %02d src1 src2))' % (op, e))
for k, e, op in (('b', 8, 0xF8), ('w', 16, 0xF9), ('d', 32, 0xFA), ('q', 64, 0xFB)):
    _simd_int('psub' + k, lambda x, y: x - y, e, op, op, 'psub.lisp', 'x86-psubb/psubw/psubd-mmx', 'x86-psubb/psubw/psubd/psubq-sse',
              '(#x%02x (simd-sub-spec (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (simd-sub-spec (* 8 operand-size) %02d src1 src2))' % (op, e), mmx_ok=(e != 64))
for k, e, op in (('b', 8, 0xEC), ('w', 16, 0xED)):
    _simd_int('padds' + k, _sat_s(e, True), e, op, op, 'padd.lisp', 'x86-paddsb/paddsw-mmx', 'x86-paddsb/paddsw-mmx',
              '(#x%02x (simd-add-signed-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (simd-add-signed-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e))
for k, e, op in (('b', 8, 0xDC), ('w', 16, 0xDD)):
    _simd_int('paddus' + k, _sat_u(e, True), e, op, op, 'padd.lisp', 'x86-paddusb/paddusw-mmx', 'x86-paddusb/paddusw-mmx',
              '(#x%02x (simd-add-unsigned-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (simd-add-unsigned-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e))
for k, e, op in (('b', 8, 0xE8), ('w', 16, 0xE9)):
    _simd_int('psubs' + k, _sat_s(e, False), e, op, op, 'psub.lisp', 'x86-psubsb/psubsw-mmx', 'x86-psubsb/psubsw-mmx',
              '(#x%02x (simd-sub-signed-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (simd-sub-signed-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e))
for k, e, op in (('b', 8, 0xD8), ('w', 16, 0xD9)):
    _simd_int('psubus' + k, _sat_u(e, False), e, op, op, 'psub.lisp', 'x86-psubusb/psubusw-mmx', 'x86-psubusb/psubusw-mmx',
              '(#x%02x (simd-sub-unsigned-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (simd-sub-unsigned-sat-spec (* 8 operand-size) %02d src1 src2))' % (op, e))
for k, e, op in (('b', 8, 0x74), ('w', 16, 0x75), ('d', 32, 0x76)):
    _simd_int('pcmpeq' + k, lambda x, y, e=e: If(x == y, ONES(e), K(0, e)), e, op, op, 'pcmp.lisp', 'x86-pcmpeqb/pcmpeqw/pcmpeqd-mmx', 'x86-pcmpeqb/pcmpeqw/pcmpeqd-sse',
              '(#x%02x (pcmpeq (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (pcmpeq (* 8 operand-size) %02d src1 src2))' % (op, e))
for k, e, op in (('b', 8, 0x64), ('w', 16, 0x65), ('d', 32, 0x66)):
    _simd_int('pcmpgt' + k, lambda x, y, e=e: If(x > y, ONES(e), K(0, e)), e, op, op, 'pcmp.lisp', 'x86-pcmpgtb/pcmpgtw/pcmpgtd-mmx', 'x86-pcmpgtb/pcmpgtw/pcmpgtd-sse',
              '(#x%02x (pcmpgt (* 8 operand-size) %02d src1 src2))' % (op, e), '(#x%02x (pcmpgt (* 8 operand-size) %02d src1 src2))' % (op, e))


# ==== data transfer (ACL2 move-mmx.lisp, move-sse.lisp) ==================================================================================================
def _mov_load128(aligned):
    def f(e, d, m, a, g):
        v = m if e.mem else xmm(d, e.rm); r = Res(put_xmm(d, e.reg, v))
        if e.mem and aligned: r.fault = misaligned(a)
        return r
    return f


def _mov_store128(aligned):
    def f(e, d, m, a, g):
        v = xmm(d, e.reg)
        if e.mem:
            r = Res(d, store=(a, v, 128))
            if aligned: r.fault = misaligned(a)
            return r
        return Res(put_xmm(d, e.rm, v))
    return f


def _mmx_state(d): return X.mmx_exec_effect(d)


def _reg_or(e, d, m, wide=True, w=128): return m if e.mem else (xmm(d, e.rm) if w == 128 else (Extract(w - 1, 0, xmm(d, e.rm)) if wide else mm(d, e.rm)))


# SSE 128-bit moves.  Aligned forms fault on a misaligned memory operand (ACL2 'Exceptions Type 1': inst-ac? t); the unaligned ones do not.
MOV128 = [  # pfx, load op, store op, name, aligned, inst-load, inst-store, quote-load, quote-store
    ('', 0x28, 0x29, 'movaps', True, 'x86-movaps/movapd-Op/En-RM', 'x86-movaps/movapd-Op/En-MR', '0F 28: MOVAPS xmm1, xmm2/m128', '0F 29: MOVAPS xmm2/m128, xmm1'),
    ('66', 0x28, 0x29, 'movapd', True, 'x86-movaps/movapd-Op/En-RM', 'x86-movaps/movapd-Op/En-MR', '66 0F 28: MOVAPD xmm1, xmm2/m128', '66 0F 29: MOVAPD xmm2/m128, xmm1'),
    ('', 0x10, 0x11, 'movups', False, 'x86-movups/movupd/movdqu-Op/En-RM', 'x86-movups/movupd/movdqu-Op/En-MR', '0F 10: MOVUPS xmm1, xmm2/m128', '0F 11: MOVUPS xmm2/m128, xmm1'),
    ('66', 0x10, 0x11, 'movupd', False, 'x86-movups/movupd/movdqu-Op/En-RM', 'x86-movups/movupd/movdqu-Op/En-MR', '66 0F 10: MOVUPD xmm1, xmm2/m128', '66 0F 11: MOVUPD xmm2/m128, xmm1'),
    ('F3', 0x6F, 0x7F, 'movdqu', False, 'x86-movups/movupd/movdqu-Op/En-RM', 'x86-movups/movupd/movdqu-Op/En-MR', 'F3 0F 6F: MOVDQU xmm1, xmm2/m128', 'F3 0F 7F: MOVDQU xmm2/m128, xmm1'),
    ('66', 0x6F, 0x7F, 'movdqa', True, 'x86-movdqa-to-xmm', 'x86-movdqa-from-xmm', '66 0F 6F /r   MOVDQA xmm1, xmm2/m128', '66 0F 7F /r   MOVDQA xmm2/m128, xmm1'),
]
for pfx, lo, so, nm, al, il, is_, ql, qs in MOV128:
    row(pfx, lo, nm, [cite('move-sse.lisp', il, ql)], mw=128, al=al)(_mov_load128(al))
    row(pfx, so, nm, [cite('move-sse.lisp', is_, qs)], mw=128, al=al)(_mov_store128(al))


def _movs(sz, nm, quote_rm, quote_mr):
    """MOVSS / MOVSD: register source keeps the high part of the destination; memory source clears it (ACL2 move-sse.lisp, the quoted Intel text); the store writes sz bits"""
    def ld(e, d, m, a, g):
        low = Extract(sz - 1, 0, xmm(d, e.rm)) if not e.mem else m
        if e.mem: return Res(put_xmm(d, e.reg, ZeroExt(128 - sz, low)))
        return Res(put_xmm(d, e.reg, Concat(Extract(127, sz, xmm(d, e.reg)), low)))
    def st(e, d, m, a, g):
        low = Extract(sz - 1, 0, xmm(d, e.reg))
        if e.mem: return Res(d, store=(a, low, sz))
        return Res(put_xmm(d, e.rm, Concat(Extract(127, sz, xmm(d, e.rm)), low)))
    return ld, st


for pfx, sz, nm in (('F3', 32, 'movss'), ('F2', 64, 'movsd')):
    ld, st = _movs(sz, nm, '', '')
    row(pfx, 0x10, nm, [cite('move-sse.lisp', 'x86-movss/movsd-Op/En-RM', 'of the destination operand is cleared to all 0s')], mw=sz)(ld)
    row(pfx, 0x11, nm, [cite('move-sse.lisp', 'x86-movss/movsd-Op/En-MR', '%s xmm2/m%d, xmm1' % (nm.upper(), sz))], mw=sz)(st)


def _half(lo_of_dst, from_high):
    """MOVLPS/MOVLPD/MOVHPS/MOVHPD load: one 64-bit half of the destination := m64; the other half is unchanged"""
    def f(e, d, m, a, g):
        dst = xmm(d, e.reg); src = m if e.mem else (Extract(127, 64, xmm(d, e.rm)) if from_high else Extract(63, 0, xmm(d, e.rm)))
        return Res(put_xmm(d, e.reg, Concat(Extract(127, 64, dst), src) if lo_of_dst else Concat(src, Extract(63, 0, dst))))
    return f


def _half_store(hi):
    def f(e, d, m, a, g): return Res(d, store=(a, Extract(127, 64, xmm(d, e.reg)) if hi else Extract(63, 0, xmm(d, e.reg)), 64))
    return f


for pfx, nm in (('', 'movlps'), ('66', 'movlpd')):
    row(pfx, 0x12, nm, [cite('move-sse.lisp', 'x86-movlps/movlpd-Op/En-RM', '0F 12: MOVLPS xmm, m64' if not pfx else '66 0F 12: MOVLPD xmm, m64')], form='m', mw=64)(_half(True, False))
    row(pfx, 0x13, nm, [cite('move-sse.lisp', 'x86-movlps/movlpd-Op/En-MR', '0F 13: MOVLPS m64, xmm' if not pfx else '66 0F 13: MOVLPD m64, xmm')], form='m', mw=64)(_half_store(False))
for pfx, nm in (('', 'movhps'), ('66', 'movhpd')):
    row(pfx, 0x16, nm, [cite('move-sse.lisp', 'x86-movhps/movhpd-Op/En-RM', '0F 16: MOVHPS xmm, m64' if not pfx else '66 0F 16: MOVHPD xmm, m64')], form='m', mw=64)(_half(False, False))
    row(pfx, 0x17, nm, [cite('move-sse.lisp', 'x86-movhps/movhpd-Op/En-MR', '0F 17: MOVHPS m64, xmm' if not pfx else '66 0F 17: MOVHPD m64, xmm')], form='m', mw=64)(_half_store(True))
# register forms of the same opcodes: MOVHLPS (0F 12 /r mod 3): dst[63:0] := src[127:64]; MOVLHPS (0F 16 /r mod 3): dst[127:64] := src[63:0]
row('', 0x12, 'movhlps', [cite('move-sse.lisp', 'x86-movhlps-sse', 'NP 0F 12 /r    MOVHLPS xmm1, xmm2')], mod3=True, form='r')(_half(True, True))
row('', 0x16, 'movlhps', [cite('move-sse.lisp', 'x86-movlhps-sse', 'NP 0F 16 /r    MOVLHPS xmm1, xmm2')], mod3=True, form='r')(_half(False, False))


# MOVD / MOVQ
def _movd_in(wide):
    """MOVD mm, r/m32 (0F 6E) and MOVD xmm, r/m32 (66 0F 6E): the 32-bit source zero-extended into the register"""
    def f(e, d, m, a, g):
        v = m if e.mem else g[e.rm]
        return Res(put_xmm(d, e.reg, ZeroExt(96, v)) if wide else put_mm(d, e.reg, ZeroExt(32, v)))
    return f


def _movd_out(wide):
    def f(e, d, m, a, g):
        v = Extract(31, 0, xmm(d, e.reg) if wide else mm(d, e.reg)); ds = d if wide else _mmx_state(d)
        return Res(ds, store=(a, v, 32)) if e.mem else Res(ds, gpr=(e.rm, v))
    return f


row('', 0x6E, 'movd', [cite('move-mmx.lisp', 'x86-movd/movq-to-mmx', 'NP         0F 6E /r    MOVD mm, r/m32')], mw=32)(_movd_in(False))
row('66', 0x6E, 'movd', [cite('move-sse.lisp', 'x86-movd/movq-to-xmm', '66       0F 6E /r: MOVD xmm, r/m32')], mw=32)(_movd_in(True))
row('', 0x7E, 'movd', [cite('move-mmx.lisp', 'x86-movd/movq-from-mmx', 'NP         0F 7E /r    MOVD r/m32, mm')], mw=32)(_movd_out(False))
row('66', 0x7E, 'movd', [cite('move-sse.lisp', 'x86-movd/movq-from-xmm', '66       0F 7E /r: MOVD r/m32, xmm')], mw=32)(_movd_out(True))


def _movq_mm_load(e, d, m, a, g): return Res(put_mm(d, e.reg, m if e.mem else mm(d, e.rm)))
def _movq_mm_store(e, d, m, a, g):
    v = mm(d, e.reg)
    return Res(_mmx_state(d), store=(a, v, 64)) if e.mem else Res(put_mm(d, e.rm, v))
def _movq_x_load(e, d, m, a, g): return Res(put_xmm(d, e.reg, ZeroExt(64, m if e.mem else Extract(63, 0, xmm(d, e.rm)))))
def _movq_x_store(e, d, m, a, g):
    v = Extract(63, 0, xmm(d, e.reg))
    return Res(d, store=(a, v, 64)) if e.mem else Res(put_xmm(d, e.rm, ZeroExt(64, v)))


row('', 0x6F, 'movq', [cite('move-mmx.lisp', 'x86-movq-from-mmx/mem', '   NP 0F 6F /r    MOVQ mm, mm/m64')], mw=64)(_movq_mm_load)
row('', 0x7F, 'movq', [cite('move-mmx.lisp', 'x86-movq-to-mmx/mem', 'NP 0F 7F /r    MOVQ mm/m64, mm')], mw=64)(_movq_mm_store)
row('F3', 0x7E, 'movq', [cite('move-sse.lisp', 'x86-movq-from-xmm/mem', 'F3       0F 7E /r: MOVQ xmm1, xmm2/m64')], mw=64)(_movq_x_load)
row('66', 0xD6, 'movq', [cite('move-sse.lisp', 'x86-movq-to-xmm/mem', '66       0F D6 /r: MOVQ xmm2/m64, xmm1')], mw=64)(_movq_x_store)


# MOVMSKPS / MOVMSKPD / PMOVMSKB: the sign bits of the lanes of the register source, zero-extended into a general register
def _signs(v, e): return join([Extract(e - 1, e - 1, x) for x in lanes(v, e)])


def _movmsk(e_bits, wide, ds):
    def f(e, d, m, a, g):
        v = _signs(xmm(d, e.rm) if wide else mm(d, e.rm), e_bits)
        return Res(d if ds else _mmx_state(d), gpr=(e.reg, ZeroExt(32 - v.size(), v)))
    return f


row('', 0x50, 'movmskps', [cite('move-sse.lisp', 'x86-movmskps-sse', 'NP 0F 50 /r   MOVMSKPS reg, xmm')], form='r', mw=None)(_movmsk(32, True, True))
row('66', 0x50, 'movmskpd', [cite('move-sse.lisp', 'x86-movmskpd-sse', '66 0F 50 /r   MOVMSKPD reg, xmm')], form='r', mw=None)(_movmsk(64, True, True))


# ==== shuffles, unpacks, interleaves (ACL2 fp/shuffle-and-unpack.lisp, punpck.lisp, pshuf.lisp) =========================================================
def _interleave(e_bits, hi, wide):
    """PUNPCK{L,H}{BW,WD,DQ,QDQ}: the low (high) half of the destination and of the source interleaved, destination element first (SDM 2B:PUNPCKHBW..PUNPCKLQDQ;
    ACL2 punpckl: a-element, then b-element).  MMX: the 32 bits of each half; SSE: the 64 bits"""
    def f(e, d, m, a, g):
        w = 128 if wide else 64
        def go(x, y):
            h = w // 2
            xa = Extract(w - 1, h, x) if hi else Extract(h - 1, 0, x); ya = Extract(w - 1, h, y) if hi else Extract(h - 1, 0, y)
            return join([z for p, q in zip(lanes(xa, e_bits), lanes(ya, e_bits)) for z in (p, q)])
        return _w(e, a, m, d, wide, go)
    return f


for k, eb, lo_op, hi_op in (('bw', 8, 0x60, 0x68), ('wd', 16, 0x61, 0x69), ('dq', 32, 0x62, 0x6A)):
    row('', lo_op, 'punpckl' + k, [cite('punpck.lisp', 'x86-punpckl-mmx', '(#x%02X (punpckl (* 8 operand-size) %02d src1 src2))' % (lo_op, eb))], mw=64)(_interleave(eb, False, False))
    row('66', lo_op, 'punpckl' + k, [cite('punpck.lisp', 'x86-punpckl-sse', '(#x%02X (punpckl (* 8 operand-size) %02d src1 src2))' % (lo_op, eb))], mw=128)(_interleave(eb, False, True))
    row('', hi_op, 'punpckh' + k, [cite('punpck.lisp', 'x86-punpckh-mmx', '(#x%02X (punpckl (* 8 operand-size) %02d src1 src2))' % (hi_op, eb))], mw=64)(_interleave(eb, True, False))
    row('66', hi_op, 'punpckh' + k, [cite('punpck.lisp', 'x86-punpckh-sse', '(#x%02X (punpckl (* 8 operand-size) %02d src1 src2))' % (hi_op, eb))], mw=128)(_interleave(eb, True, True))
row('66', 0x6C, 'punpcklqdq', [cite('punpck.lisp', 'x86-punpckl-sse', '(#x6C (punpckl (* 8 operand-size) 64 src1 src2))')], mw=128)(_interleave(64, False, True))
row('66', 0x6D, 'punpckhqdq', [cite('punpck.lisp', 'x86-punpckh-sse', '(#x6D (punpckl (* 8 operand-size) 64 src1 src2))')], mw=128)(_interleave(64, True, True))
# UNPCKLPS / UNPCKHPS are the doubleword interleave of the xmm halves, UNPCKLPD / UNPCKHPD the quadword one (ACL2 fp/shuffle-and-unpack.lisp: the dword0..3 / qword0..1 selections)
row('', 0x14, 'unpcklps', [cite('fp/shuffle-and-unpack.lisp', 'x86-unpck?ps-Op/En-RM', '0F 14: UNPCKLPS xmm1, xmm2/m128')], mw=128)(_interleave(32, False, True))
row('', 0x15, 'unpckhps', [cite('fp/shuffle-and-unpack.lisp', 'x86-unpck?ps-Op/En-RM', '0F 15: UNPCKHPS xmm1, xmm2/m128')], mw=128)(_interleave(32, True, True))
row('66', 0x14, 'unpcklpd', [cite('fp/shuffle-and-unpack.lisp', 'x86-unpck?pd-Op/En-RM', '66 0F 14: UNPCKLPD xmm1, xmm2/m128')], mw=128)(_interleave(64, False, True))
row('66', 0x15, 'unpckhpd', [cite('fp/shuffle-and-unpack.lisp', 'x86-unpck?pd-Op/En-RM', '66 0F 15: UNPCKHPD xmm1, xmm2/m128')], mw=128)(_interleave(64, True, True))


def _sel(v, e, idx):
    """the lane idx (a BV) of v, lane width e: the lanes are the SDM's 'DEST[..]' fields indexed by the immediate bits"""
    ls = lanes(v, e); r = ls[0]
    for i in range(1, len(ls)): r = If(idx == K(i, idx.size()), ls[i], r)
    return r


def _shufps(e, d, m, a, g):
    dst, src = xmm(d, e.reg), (m if e.mem else xmm(d, e.rm)); im = e.imm
    two = lambda hi, lo: Extract(hi, lo, im)
    r = Res(put_xmm(d, e.reg, Concat(_sel(src, 32, two(7, 6)), _sel(src, 32, two(5, 4)), _sel(dst, 32, two(3, 2)), _sel(dst, 32, two(1, 0)))))
    if e.mem: r.fault = misaligned(a)
    return r


def _shufpd(e, d, m, a, g):
    dst, src = xmm(d, e.reg), (m if e.mem else xmm(d, e.rm)); im = e.imm
    r = Res(put_xmm(d, e.reg, Concat(_sel(src, 64, Extract(1, 1, im)), _sel(dst, 64, Extract(0, 0, im)))))
    if e.mem: r.fault = misaligned(a)
    return r


row('', 0xC6, 'shufps', [cite('fp/shuffle-and-unpack.lisp', 'x86-shufps-Op/En-RMI', '(result (merge-4-u32s dword3 dword2 dword1 dword0))')], mw=128, imm=True)(_shufps)
row('66', 0xC6, 'shufpd', [cite('fp/shuffle-and-unpack.lisp', 'x86-shufpd-Op/En-RMI', '(result (merge-2-u64s qword1 qword0))')], mw=128, imm=True)(_shufpd)


def _pshuf(kind):
    """PSHUFD (66 0F 70): dword i := src dword imm[2i+1:2i]; PSHUFLW (F2): the low four words likewise, the high quadword copied; PSHUFHW (F3): the high four words likewise,
    the low quadword copied; PSHUFW (0F 70, mm): the four words of the mm source.  The immediate is the full byte (2 bits per destination lane), never masked wider"""
    def f(e, d, m, a, g):
        src = m if e.mem else (mm(d, e.rm) if kind == 'w' else xmm(d, e.rm)); im = e.imm; sel = lambda i: Extract(2 * i + 1, 2 * i, im)
        if kind == 'd': out = Concat(*[_sel(src, 32, sel(i)) for i in (3, 2, 1, 0)])
        elif kind == 'lw': out = Concat(Extract(127, 64, src), *[_sel(Extract(63, 0, src), 16, sel(i)) for i in (3, 2, 1, 0)])
        elif kind == 'hw': out = Concat(*[_sel(Extract(127, 64, src), 16, sel(i)) for i in (3, 2, 1, 0)], Extract(63, 0, src))
        else: out = Concat(*[_sel(src, 16, sel(i)) for i in (3, 2, 1, 0)])
        if kind == 'w': return Res(put_mm(d, e.reg, out))
        r = Res(put_xmm(d, e.reg, out))
        if e.mem: r.fault = misaligned(a)
        return r
    return f


row('66', 0x70, 'pshufd', [cite('pshuf.lisp', 'x86-pshufd', '(result (pshufd src imm))')], mw=128, imm=True)(_pshuf('d'))
row('F2', 0x70, 'pshuflw', [cite('pshuf.lisp', 'x86-pshuflw', '(result (logapp 64 (pshufw (loghead 64 src) imm)')], mw=128, imm=True)(_pshuf('lw'))
row('F3', 0x70, 'pshufhw', [cite('pshuf.lisp', 'x86-pshufhw', '(pshufw (logtail 64 src) imm)))')], mw=128, imm=True)(_pshuf('hw'))


# ==== execution ===============================================================================================================================================
def run(code, d, m=None, a=None, gpr=None, imm=None):
    """the spec of one encoding (bytes) on the state d: Res"""
    e = Enc(code)
    k = (e.pfx, e.op, e.mod == 3) if (e.pfx, e.op, e.mod == 3) in ROWS else (e.pfx, e.op, None)
    if k not in ROWS: raise KeyError('no row for %s' % code.hex())
    form = ROWS[k].form
    if (form == 'm' and not e.mem) or (form == 'r' and e.mem): raise KeyError('form not defined for %s' % code.hex())
    e.imm = imm
    return ROWS[k].fn(e, d, m, a, gpr)


def mem_width(code):
    """the width in bits of the memory operand of an encoding (None for a row without one)"""
    e = Enc(code)
    k = (e.pfx, e.op, e.mod == 3) if (e.pfx, e.op, e.mod == 3) in ROWS else (e.pfx, e.op, None)
    return ROWS[k].mw


def mem_aligned(code):
    """True when a misaligned memory operand of the encoding raises the access violation (the row's alignment rule: ACL2 inst-ac? / SDM 'Exceptions Type 1/4')"""
    e = Enc(code)
    k = (e.pfx, e.op, e.mod == 3) if (e.pfx, e.op, e.mod == 3) in ROWS else (e.pfx, e.op, None)
    return ROWS[k].al


def mem_imm(code):
    """True when the encoding ends in an imm8 operand (the row's `imm`)"""
    e = Enc(code)
    k = (e.pfx, e.op, e.mod == 3) if (e.pfx, e.op, e.mod == 3) in ROWS else (e.pfx, e.op, None)
    return ROWS[k].imm
