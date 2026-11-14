"""Arithmetic / logic / shift / bit / multiply / divide families (see forms.py)."""
from fnvlift.dsl import C, V, ashr, bit, concat, eq, extract, ite, lshr, msb, sext, shl, trunc, udiv, ult, urem, zext
from proof.forms import Op, addoff, af, ea, handler, mload, pzs, rd, store_op, undef, wr

UNDEF_LOGIC = 'AND/OR/XOR/TEST: "The state of the AF flag is undefined."'
UNDEF_MUL = 'MUL/IMUL: "The SF, ZF, AF, and PF flags are undefined."'
UNDEF_SHAF = 'SHL/SHR/SAR/SHLD/SHRD: "The AF flag is undefined" (for non-zero counts).'
UNDEF_SHCF = 'SHL/SHR: "CF ... is undefined for SHL and SHR instructions where the count is greater than or equal to the size (in bits) of the destination operand."'
UNDEF_DIV = 'DIV/IDIV: "The CF, OF, SF, ZF, AF, and PF flags are undefined."'
UNDEF_BT = 'BT/BTS/BTR/BTC: "The OF, SF, AF, and PF flags are undefined." (ZF is unaffected)'
UNDEF_BS = 'BSF/BSR: "The CF, OF, SF, AF, and PF flags are undefined" (ZF = 1 iff source is 0; destination undefined then).'

@handler('add', 'adc', 'sub', 'sbb', 'cmp', 'and', 'or', 'xor', 'test')
def alu(c):
    F, m, d, s = c.F, c.m, c.ops[0], c.ops[1]
    a, b = rd(d), rd(s, d.w); w = d.w; cy = zext(V('cf', 1), w)
    r = {'add': a + b, 'adc': a + b + cy, 'sub': a - b, 'cmp': a - b, 'sbb': a - b - cy, 'and': a & b, 'test': a & b, 'or': a | b, 'xor': a ^ b}[m]
    if m in ('add', 'adc'):
        F.flag('cf', ult(r, a) | (V('cf', 1) & eq(r, a)) if m == 'adc' else ult(r, a))
        F.flag('of', msb((a ^ r) & (b ^ r))); af(F, a, b, r)
    elif m in ('sub', 'cmp', 'sbb'):
        F.flag('cf', ult(a, b) | (V('cf', 1) & eq(a, b)) if m == 'sbb' else ult(a, b))
        F.flag('of', msb((a ^ b) & (a ^ r))); af(F, a, b, r)
    else: F.flag('cf', C(0, 1)); F.flag('of', C(0, 1)); F.undefined('af', UNDEF_LOGIC)
    pzs(F, r)
    if m not in ('cmp', 'test'): wr(F, d, r)

@handler('inc', 'dec')
def incdec(c):
    F, d = c.F, c.ops[0]; a = rd(d); w = d.w; r = a + 1 if c.m == 'inc' else a - 1
    F.flag('of', eq(r, 1 << (w - 1)) if c.m == 'inc' else eq(a, 1 << (w - 1))); af(F, a, C(1, w), r); pzs(F, r); wr(F, d, r)

@handler('neg')
def neg_(c):
    F, d = c.F, c.ops[0]; a = rd(d); r = -a
    F.flag('cf', ~eq(a, 0)); F.flag('of', eq(a, 1 << (d.w - 1))); af(F, a, C(0, d.w), r); pzs(F, r); wr(F, d, r)

@handler('not')
def not_(c): wr(c.F, c.ops[0], ~rd(c.ops[0]))

def count(c, i=1):
    """masked (5-bit) shift count as a 32-bit node: imm / 1 / cl"""
    if len(c.ops) <= i: return C(1, 32)
    return zext(rd(c.ops[i], 8), 32) & 31

@handler('shl', 'shr', 'sar', 'rol', 'ror')
def shift(c):
    F, m, d = c.F, c.m, c.ops[0]; a, w = rd(d), d.w; n32 = count(c); n = trunc(n32, w); nz = ~eq(n32, 0)
    keep = lambda f, new: ite(nz, new, V(f, 1)); one = ult(n32, 2)
    if m in ('rol', 'ror'):
        k = n & (w - 1)
        r = (shl(a, k) | lshr(a, C(w, w) - k)) if m == 'rol' else (lshr(a, k) | shl(a, C(w, w) - k))
        cf = bit(r, 0) if m == 'rol' else msb(r)
        of = msb(r) ^ cf if m == 'rol' else msb(r) ^ bit(r, w - 2)
        F.flag('cf', keep('cf', cf)); F.flag('of', keep('of', of), when=one)     # SDM: OF defined only for 1-bit rotates; no flag changes for count 0
    else:
        f = {'shl': shl, 'shr': lshr, 'sar': ashr}[m]; r = f(a, n); last = f(a, n - 1)
        cf = msb(last) if m == 'shl' else bit(last, 0)
        of = {'shl': msb(r) ^ cf, 'shr': msb(a), 'sar': C(0, 1)}[m]
        # SHL/SHR: CF is undefined when the masked count >= the operand size (SDM); SAR is defined for every count
        F.flag('cf', keep('cf', cf), when=ult(n32, w) if m != 'sar' else None); F.flag('of', keep('of', of), when=one)
        F.undefined('af', UNDEF_SHAF); pzs(F, r, keep)
    wr(F, d, r)

@handler('rcl', 'rcr')
def rotc(c):
    F, m, d = c.F, c.m, c.ops[0]; a, w = rd(d), d.w; W = w + 1; n32 = count(c); nz = ~eq(n32, 0)
    x = concat(V('cf', 1), a); k = urem(zext(n32, W) if W > 32 else trunc(n32, W), C(W, W))
    r2 = (shl(x, k) | lshr(x, C(W, W) - k)) if m == 'rcl' else (lshr(x, k) | shl(x, C(W, W) - k))
    r = trunc(r2, w); cf = msb(r2)
    of = msb(r) ^ cf if m == 'rcl' else msb(r) ^ bit(r, w - 2)
    F.flag('cf', ite(nz, cf, V('cf', 1))); F.flag('of', ite(nz, of, V('of', 1)), when=ult(n32, 2)); wr(F, d, r)

@handler('shld', 'shrd')
def shld(c):
    F, d, s = c.F, c.ops[0], c.ops[1]; assert d.w == 32; a, b = rd(d), rd(s); n = count(c, 2); nz = ~eq(n, 0)
    keep = lambda f, new: ite(nz, new, V(f, 1))
    if c.m == 'shld': r = shl(a, n) | lshr(b, C(32, 32) - n); cf = msb(shl(a, n - 1))
    else: r = lshr(a, n) | shl(b, C(32, 32) - n); cf = bit(lshr(a, n - 1), 0)
    F.flag('cf', keep('cf', cf)); F.flag('of', keep('of', msb(r) ^ msb(a)), when=ult(n, 2)); F.undefined('af', UNDEF_SHAF); pzs(F, r, keep); wr(F, d, r)

@handler('bt', 'bts', 'btr', 'btc')
def bt(c):
    F, d, s = c.F, c.ops[0], c.ops[1]; w = d.w; lg = {16: 4, 32: 5}[w]; off = rd(s, w)
    if d.k == 'r': addr, bn, val = None, off & (w - 1), rd(d)
    else:
        addr = ea(d)
        if s.k == 'r': addr = addoff(addr, sext(ashr(off, C(lg, w)), 32) * (w // 8))       # bit-string: signed offset selects the word
        bn = off & (w - 1); val = mload(addr, w)
    F.flag('cf', bit(lshr(val, bn), 0)); undef(F, ('of', 'sf', 'af', 'pf'), UNDEF_BT)
    msk = shl(C(1, w), bn)
    if c.m != 'bt':
        nv = {'bts': val | msk, 'btr': val & ~msk, 'btc': val ^ msk}[c.m]
        if addr is None: wr(F, d, nv)
        else: store_op(F, d, addr, nv)

@handler('bsf', 'bsr')
def bs(c):
    F, d, s = c.F, c.ops[0], c.ops[1]; w = d.w; a = rd(s); r = C(0, w)
    for i in (range(w - 1, -1, -1) if c.m == 'bsf' else range(w)): r = ite(bit(a, i), C(i, w), r)
    z = eq(a, 0); F.flag('zf', z); undef(F, ('cf', 'of', 'sf', 'af', 'pf'), UNDEF_BS)
    wr(F, d, ite(z, rd(d), r), when=~z)

@handler('xadd')
def xadd(c):
    F, d, s = c.F, c.ops[0], c.ops[1]; a, b = rd(d), rd(s); r = a + b
    F.flag('cf', ult(r, a)); F.flag('of', msb((a ^ r) & (b ^ r))); af(F, a, b, r); pzs(F, r)
    wr(F, s, a); wr(F, d, r)                       # SDM: TEMP <- SRC+DEST; SRC <- DEST; DEST <- TEMP (dest last when they alias)

@handler('cmpxchg')
def cmpxchg(c):
    F, d, s = c.F, c.ops[0], c.ops[1]; w = d.w; acc = Op.reg({8: 'al', 16: 'ax', 32: 'eax'}[w], w)
    a, dv = rd(acc), rd(d); r = a - dv; e = eq(a, dv)
    F.flag('cf', ult(a, dv)); F.flag('of', msb((a ^ dv) & (a ^ r))); af(F, a, dv, r); pzs(F, r)
    wr(F, acc, ite(e, a, dv)); wr(F, d, ite(e, rd(s), dv))   # SDM: if equal DEST <- SRC else ACC <- DEST (memory dest is written back either way)

@handler('mul')
def mul1(c):
    F, s = c.F, c.ops[0]; w = s.w; acc = Op.reg({8: 'al', 16: 'ax', 32: 'eax'}[w], w)
    p = zext(rd(s), 2 * w) * zext(rd(acc), 2 * w); hi = extract(p, 2 * w - 1, w); cf = ~eq(hi, 0)
    wide(F, w, p); F.flag('cf', cf); F.flag('of', cf); undef(F, ('sf', 'zf', 'af', 'pf'), UNDEF_MUL)

def wide(F, w, p):                   # store a 2w-bit result into AX / DX:AX / EDX:EAX
    if w == 8: F.reg('eax', concat(extract(F.cur('eax'), 31, 16), p))
    elif w == 16: F.reg('eax', concat(extract(F.cur('eax'), 31, 16), trunc(p, 16))); F.reg('edx', concat(extract(F.cur('edx'), 31, 16), extract(p, 31, 16)))
    else: F.reg('eax', trunc(p, 32)); F.reg('edx', extract(p, 63, 32))

@handler('imul')
def imul(c):
    F, ops = c.F, c.ops
    if len(ops) == 1:                # one-operand: AX / DX:AX / EDX:EAX = acc * r/m, signed
        s = ops[0]; w = s.w; acc = Op.reg({8: 'al', 16: 'ax', 32: 'eax'}[w], w)
        p = sext(rd(s), 2 * w) * sext(rd(acc), 2 * w); wide(F, w, p); cf = ~eq(p, sext(trunc(p, w), 2 * w))
    else:
        d = ops[0]; a, b = ops[-2] if len(ops) == 3 else ops[0], ops[-1]; w = d.w
        p = sext(rd(a), 2 * w) * sext(rd(b, w), 2 * w); r = trunc(p, w); cf = ~eq(p, sext(r, 2 * w)); wr(F, d, r)
    F.flag('cf', cf); F.flag('of', cf)          # SDM: CF=OF=1 when the signed product does not fit the destination
    undef(F, ('sf', 'zf', 'af', 'pf'), UNDEF_MUL)

@handler('div', 'idiv')
def div(c):
    F, s = c.F, c.ops[0]; w = s.w; sg = c.m == 'idiv'; W = 2 * w
    if w == 8: N = zext(rd(Op.reg('ax', 16)), 16)
    else: N = concat(rd(Op.reg('edx' if w == 32 else 'dx', w)), rd(Op.reg('eax' if w == 32 else 'ax', w)))
    D = sext(rd(s), W) if sg else zext(rd(s), W)
    if sg:
        sn, sd = msb(N), msb(D); an = ite(sn, -N, N); ad = ite(sd, -D, D)
        qu, ru = udiv(an, ad), urem(an, ad); q = ite(sn ^ sd, -qu, qu); r = ite(sn, -ru, ru)
        ovf = ~eq(q, sext(trunc(q, w), W))
    else: q, r = udiv(N, D), urem(N, D); ovf = ~eq(extract(q, W - 1, w), 0)
    F.fault(1, eq(D, 0) | ovf)                                  # SDM: #DE when the divisor is 0 or the quotient overflows
    qd, rm = trunc(q, w), trunc(r, w)
    if w == 8: F.reg('eax', concat(extract(F.cur('eax'), 31, 16), concat(rm, qd)))
    elif w == 16: F.reg('eax', concat(extract(F.cur('eax'), 31, 16), qd)); F.reg('edx', concat(extract(F.cur('edx'), 31, 16), rm))
    else: F.reg('eax', qd); F.reg('edx', rm)
    undef(F, ('cf', 'of', 'sf', 'zf', 'af', 'pf'), UNDEF_DIV)
