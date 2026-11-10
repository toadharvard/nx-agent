"""The slice's instruction forms in the DSL (fnvlift/dsl.py), built from a capstone-decoded instruction.
build(ins, addr) -> Form.  A builder is written over operand placeholders (Op: register of a class/width, immediate,
memory effective address); a Form is that builder instantiated on one concrete encoding.  SDM = Intel SDM vol. 2 / vol. 1 3.4.3."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from fnvlift.dsl import *
from capstone import x86 as X

R8 = ['al', 'cl', 'dl', 'bl', 'ah', 'ch', 'dh', 'bh']; R16 = ['ax', 'cx', 'dx', 'bx', 'sp', 'bp', 'si', 'di']
PFX = {0x66, 0x67, 0xf0, 0xf2, 0xf3, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65}
SEGS = {'cs', 'ds', 'es', 'ss', 'fs', 'gs'}
def form_key(ins):
    """same as form_key in test/instest.py (prove.py checks the two agree when instest imports)"""
    raw = bytes(ins.bytes); i = 0
    while i < len(raw) and raw[i] in PFX: i += 1
    opc = raw[i:i + (3 if raw[i] == 0x0f and raw[i + 1] in (0x38, 0x3a) else 2 if raw[i] == 0x0f else 1)]
    ks = []
    for o in ins.operands:
        if o.type == X.X86_OP_REG:
            n = ins.reg_name(o.reg)
            ks.append('xmm' if n.startswith('xmm') else 'st' if n.startswith('st') else 'mm' if n.startswith('mm') else 'seg' if n in SEGS
                      else 'r8' if n in R8 else 'r16' if n in R16 else 'r32' if n in ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'] else n)
        elif o.type == X.X86_OP_IMM: ks.append('imm')
        else: ks.append('m%d' % (o.size * 8) + {X.X86_REG_FS: ':fs', X.X86_REG_GS: ':gs'}.get(o.mem.segment, ''))
    return '%s|%s|%s' % (ins.mnemonic, ','.join(ks), opc.hex())

class Op:
    def __init__(s, ins, o):
        s.w = o.size * 8
        if o.type == X.X86_OP_REG: s.k, s.name = 'r', ins.reg_name(o.reg)
        elif o.type == X.X86_OP_IMM: s.k, s.val = 'i', o.imm
        else:
            m = o.mem; assert m.segment == 0
            s.k = 'm'; s.base = ins.reg_name(m.base) if m.base else None
            s.index = ins.reg_name(m.index) if m.index else None; s.scale, s.disp = m.scale, m.disp
    def full(s):                     # (32-bit register, bit position) of a register operand
        n = s.name
        if s.w == 32: return n, 0
        if s.w == 16: return 'e' + n, 0
        return 'e' + n[0] + 'x', (8 if n[1] == 'h' else 0)
def ea(op):
    a = C(op.disp, 32)
    if op.base: a = a + V(op.base, 32)
    if op.index: a = a + V(op.index, 32) * op.scale
    return a
def rd(op, w=None):
    w = w or op.w
    if op.k == 'i': return C(op.val, w)
    if op.k == 'm': return load(ea(op), w)
    r, lo = op.full(); return extract(V(r, 32), lo + op.w - 1, lo)
def wr(F, op, v):
    if op.k == 'm': return F.store(ea(op), v)
    r, lo = op.full(); old = F.cur(r)
    if op.w == 32: F.reg(r, v)
    elif lo == 0: F.reg(r, concat(extract(old, 31, op.w), v))
    else: F.reg(r, concat(concat(extract(old, 31, 16), v), extract(old, 7, 0)))

def pzs(F, r, g=lambda f, v: v):
    x = trunc(r, 8); x = x ^ (x >> 4); x = x ^ (x >> 2); x = x ^ (x >> 1)
    F.flag('pf', g('pf', bit(x, 0) ^ 1)); F.flag('zf', g('zf', eq(r, 0))); F.flag('sf', g('sf', msb(r)))
def af(F, a, b, r): F.flag('af', bit(a ^ b ^ r, 4))
CC = ['of', 'cf', 'zf', 'cf|zf', 'sf', 'pf', 'sf^of', 'zf|sf^of']
def cond(cc):                        # condition code 0..15 as a 1-bit node over the pre-state flags
    f = {n: V(n, 1) for n in ('cf', 'pf', 'zf', 'sf', 'of')}
    c = [f['of'], f['cf'], f['zf'], f['cf'] | f['zf'], f['sf'], f['pf'], f['sf'] ^ f['of'], f['zf'] | (f['sf'] ^ f['of'])][cc >> 1]
    return c ^ 1 if cc & 1 else c
UNDEF_LOGIC = 'AND/OR/XOR/TEST: "The state of the AF flag is undefined."'
UNDEF_MUL = 'MUL/IMUL: "The SF, ZF, AF, and PF flags are undefined."'
UNDEF_SHAF = 'SHL/SHR/SAR: "The AF flag is undefined" (for non-zero counts).'

def alu(F, m, d, s):
    a, b = rd(d), rd(s, d.w); w = d.w; c = zext(V('cf', 1), w)
    r = {'add': a + b, 'adc': a + b + c, 'sub': a - b, 'cmp': a - b, 'sbb': a - b - c, 'and': a & b, 'test': a & b, 'or': a | b, 'xor': a ^ b}[m]
    if m in ('add', 'adc'):
        F.flag('cf', ult(r, a) | (V('cf', 1) & eq(r, a)) if m == 'adc' else ult(r, a))
        F.flag('of', msb((a ^ r) & (b ^ r))); af(F, a, b, r)
    elif m in ('sub', 'cmp', 'sbb'):
        F.flag('cf', ult(a, b) | (V('cf', 1) & eq(a, b)) if m == 'sbb' else ult(a, b))
        F.flag('of', msb((a ^ b) & (a ^ r))); af(F, a, b, r)
    else: F.flag('cf', C(0, 1)); F.flag('of', C(0, 1)); F.undefined('af', UNDEF_LOGIC)
    pzs(F, r)
    if m not in ('cmp', 'test'): wr(F, d, r)

def shift(F, m, d, cnt):
    a, w = rd(d), d.w; n32 = zext(cnt, 32) & 31; n = trunc(n32, w); nz = ~eq(n32, 0)
    keep = lambda f, new: ite(nz, new, V(f, 1))
    if m in ('rol', 'ror'):
        k = n & (w - 1)
        r = (shl(a, k) | lshr(a, C(w, w) - k)) if m == 'rol' else (lshr(a, k) | shl(a, C(w, w) - k))
        cf = bit(r, 0) if m == 'rol' else msb(r)
        of = msb(r) ^ cf if m == 'rol' else msb(r) ^ bit(r, w - 2)
        F.flag('cf', keep('cf', cf)); F.flag('of', keep('of', of), when=ult(n32, 2))     # SDM: OF defined only for 1-bit rotates
    else:
        r = {'shl': shl, 'shr': lshr, 'sar': ashr}[m](a, n); last = {'shl': shl, 'shr': lshr, 'sar': ashr}[m](a, n - 1)
        cf = msb(last) if m == 'shl' else bit(last, 0)
        of = {'shl': msb(r) ^ cf, 'shr': msb(a), 'sar': C(0, 1)}[m]
        F.flag('cf', keep('cf', cf)); F.flag('of', keep('of', of), when=ult(n32, 2))     # SDM: OF defined only for 1-bit shifts
        F.undefined('af', UNDEF_SHAF)
        pzs(F, r, keep)
    wr(F, d, r)

def mul1(F, s):                      # mul r/m: unsigned, AX / DX:AX / EDX:EAX
    w = s.w; p = zext(rd(s), 2 * w) * zext(rd(Op.reg('eax' if w == 32 else 'ax' if w == 16 else 'al', w)), 2 * w)
    hi = extract(p, 2 * w - 1, w); cf = ~eq(hi, 0)
    if w == 8: F.reg('eax', concat(extract(F.cur('eax'), 31, 16), p))
    elif w == 16: F.reg('eax', concat(extract(F.cur('eax'), 31, 16), trunc(p, 16))); F.reg('edx', concat(extract(F.cur('edx'), 31, 16), hi))
    else: F.reg('eax', trunc(p, 32)); F.reg('edx', hi)
    F.flag('cf', cf); F.flag('of', cf)
    for f in ('sf', 'zf', 'af', 'pf'): F.undefined(f, UNDEF_MUL)
def Op_reg(name, w):
    o = Op.__new__(Op); o.k, o.name, o.w = 'r', name, w; return o
Op.reg = staticmethod(Op_reg)

def imul(F, d, a, b):
    w = d.w; p = sext(rd(a), 2 * w) * sext(rd(b, w), 2 * w); r = trunc(p, w)
    cf = ~eq(p, sext(r, 2 * w)); F.flag('cf', cf); F.flag('of', cf)          # SDM: CF=OF=1 when the signed product does not fit
    for f in ('sf', 'zf', 'af', 'pf'): F.undefined(f, UNDEF_MUL)
    wr(F, d, r)

def build(ins, addr):
    """Form for a decoded instruction at addr; key = form_key(ins).  Raises KeyError for mnemonics outside the slice."""
    F = Form(form_key(ins), addr + ins.size); ops = [Op(ins, o) for o in ins.operands]; m = ins.mnemonic
    raw = bytes(ins.bytes); i = 0
    while raw[i] in PFX: i += 1
    opc = raw[i + 1] if raw[i] == 0x0f else raw[i]
    if m in ('add', 'adc', 'sub', 'sbb', 'cmp', 'and', 'or', 'xor', 'test'): alu(F, m, ops[0], ops[1])
    elif m in ('shl', 'shr', 'sar', 'rol', 'ror'):
        cnt = Op(ins, ins.operands[1]) if len(ops) > 1 else None
        shift(F, m, ops[0], C(1, 8) if cnt is None else rd(cnt, 8))
    elif m in ('inc', 'dec'):
        d = ops[0]; a = rd(d); w = d.w; r = a + 1 if m == 'inc' else a - 1
        F.flag('of', eq(r, 1 << (w - 1)) if m == 'inc' else eq(a, 1 << (w - 1))); af(F, a, C(1, w), r); pzs(F, r); wr(F, d, r)
    elif m == 'neg':
        d = ops[0]; a = rd(d); r = -a; F.flag('cf', ~eq(a, 0)); F.flag('of', eq(a, 1 << (d.w - 1))); af(F, a, C(0, d.w), r); pzs(F, r); wr(F, d, r)
    elif m == 'not': wr(F, ops[0], ~rd(ops[0]))
    elif m == 'imul' and len(ops) > 1: imul(F, ops[0], ops[-2] if len(ops) == 3 else ops[0], ops[-1])
    elif m == 'mul': mul1(F, ops[0])
    elif m in ('movzx', 'movsx'): wr(F, ops[0], (zext if m == 'movzx' else sext)(rd(ops[1]), ops[0].w))
    elif m == 'mov': wr(F, ops[0], rd(ops[1], ops[0].w))
    elif m.startswith('set'): wr(F, ops[0], zext(cond(opc & 15), 8))
    elif m.startswith('cmov'): wr(F, ops[0], ite(cond(opc & 15), rd(ops[1]), rd(ops[0])))
    elif m == 'lea': wr(F, ops[0], trunc(ea(ops[1]), ops[0].w))
    elif m == 'bswap':
        a = rd(ops[0]); wr(F, ops[0], concat(concat(extract(a, 7, 0), extract(a, 15, 8)), concat(extract(a, 23, 16), extract(a, 31, 24))))
    elif m == 'cdq': F.reg('edx', ashr(V('eax', 32), C(31, 32)))
    elif m == 'xchg': a, b = rd(ops[0]), rd(ops[1]); wr(F, ops[0], b); wr(F, ops[1], a)
    elif m == 'push': F.reg('esp', V('esp', 32) - 4); F.store(V('esp', 32) - 4, rd(ops[0]))
    elif m == 'pop': F.reg('esp', V('esp', 32) + 4); wr(F, ops[0], load(V('esp', 32), 32))
    else: raise KeyError(m)
    return F
