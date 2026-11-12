"""Moves, conversions, stack, EFLAGS and string families (see forms.py)."""
from fnvlift.dsl import REGS, C, V, ashr, bit, concat, eq, extract, ite, load, msb, sext, trunc, ult, zext
from proof.forms import AReg, Op, cond, ea, handler, pzs, rd, wr

LAHF_SDM = 'LAHF/SAHF: AH = SF:ZF:0:AF:0:PF:1:CF'
MODIFIABLE = 0x00240100 | 0x4000      # EFLAGS bits (besides the six, DF) POPFD may change in user mode: TF, NT, AC, ID (IF/IOPL need privilege)
@handler('mov')
def mov(c):
    if any(o.k == 'r' and o.name[:2] in ('cr', 'dr') for o in c.ops):      # MOV to/from CRn / DRn: CPL 0 only (0F 20..23), KiTrap0D's table decides the status
        from proof import forms_sys
        return forms_sys.privileged(c)
    wr(c.F, c.ops[0], rd(c.ops[1], c.ops[0].w))
@handler('movzx', 'movsx')
def movx(c): wr(c.F, c.ops[0], (zext if c.m == 'movzx' else sext)(rd(c.ops[1]), c.ops[0].w))
@handler('setcc')
def setcc(c): wr(c.F, c.ops[0], zext(cond(c.opc & 15), 8))
@handler('cmovcc')
def cmovcc(c): wr(c.F, c.ops[0], ite(cond(c.opc & 15), rd(c.ops[1]), rd(c.ops[0])))
@handler('lea')
def lea(c): wr(c.F, c.ops[0], trunc(ea(c.ops[1]), c.ops[0].w))
@handler('bswap')
def bswap(c):
    a = rd(c.ops[0]); wr(c.F, c.ops[0], concat(concat(extract(a, 7, 0), extract(a, 15, 8)), concat(extract(a, 23, 16), extract(a, 31, 24))))
@handler('cdq')
def cdq(c): c.F.reg('edx', ashr(V('eax', 32), C(31, 32)))
@handler('cwd')
def cwd(c): c.F.reg('edx', concat(extract(V('edx', 32), 31, 16), ashr(extract(V('eax', 32), 15, 0), C(15, 16))))
@handler('cwde')
def cwde(c): c.F.reg('eax', sext(extract(V('eax', 32), 15, 0), 32))
@handler('cbw')
def cbw(c): c.F.reg('eax', concat(extract(V('eax', 32), 31, 16), sext(extract(V('eax', 32), 7, 0), 16)))
@handler('xchg')
def xchg(c): a, b = rd(c.ops[0]), rd(c.ops[1]); wr(c.F, c.ops[0], b); wr(c.F, c.ops[1], a)
@handler('nop')
def nop(c): pass
@handler('xlatb', 'xlat')
def xlat(c):
    b = AReg(c, 'ebx')                    # BX + AL wraps in 16 bits under the 0x67 prefix (SDM XLAT)
    a = b.val + zext(extract(V('eax', 32), 7, 0), 32)
    wr(c.F, Op.reg('al', 8), load(zext(trunc(a, 16), 32) if c.a16 else a, 8))

@handler('push')
def push(c):
    """SDM PUSH: operand size 32, or 16 under the 0x66 prefix (ESP - 2, a 16-bit store; the stack address size is SS.B = 32, the 0x67 prefix does not change it).
    The value of a register operand is its pre-state value (PUSH ESP / SP pushes the value before the decrement)."""
    F, o = c.F, c.ops[0]; w = 16 if c.o16 else 32; v = rd(o, w)
    F.reg('esp', V('esp', 32) - w // 8); F.store(V('esp', 32) - w // 8, v)
@handler('pop')
def pop(c):
    F, o = c.F, c.ops[0]; esp = V('esp', 32); w = 16 if c.o16 else 32; F.reg('esp', esp + w // 8)
    if o.k == 'm': F.store(ea(o, esp + w // 8), load(esp, w))      # SDM POP: ESP as base of the destination is incremented before the address is computed
    else: wr(F, o, load(esp, w))
@handler('pushal', 'pushad')
def pushad(c):
    F, esp = c.F, V('esp', 32)
    for i, r in enumerate(REGS): F.store(esp - 4 * (i + 1), esp if r == 'esp' else V(r, 32))      # SDM: the pre-PUSHAD ESP is pushed
    F.reg('esp', esp - 32)
@handler('popal', 'popad')
def popad(c):
    F, esp = c.F, V('esp', 32)
    for i, r in enumerate(reversed(REGS)):
        if r != 'esp': F.reg(r, load(esp + 4 * i, 32))                                             # the ESP slot is skipped
    F.reg('esp', esp + 32)

def eflags_word():
    f = lambda n, b: zext(V(n, 1), 32) << b
    six = f('cf', 0) | f('pf', 2) | f('af', 4) | f('zf', 6) | f('sf', 7) | f('of', 11) | f('df', 10)
    return six | C(2, 32) | V('flo', 32)
@handler('pushfd', 'pushf')
def pushfd(c):
    F = c.F; F.reg('esp', V('esp', 32) - 4); F.store(V('esp', 32) - 4, eflags_word() & ~C(0x30000, 32))     # SDM PUSHFD: VM and RF are not copied
@handler('popfd', 'popf')
def popfd(c):
    F = c.F; v = load(V('esp', 32), 32); F.reg('esp', V('esp', 32) + 4)
    for f, b in (('cf', 0), ('pf', 2), ('af', 4), ('zf', 6), ('sf', 7), ('of', 11), ('df', 10)): F.flag(f, bit(v, b))
    F.setflo((V('flo', 32) & ~C(MODIFIABLE, 32)) | (v & MODIFIABLE))
@handler('lahf')
def lahf(c):
    F = c.F; f = lambda n: V(n, 1)
    ah = concat(concat(concat(f('sf'), f('zf')), concat(C(0, 1), f('af'))), concat(concat(C(0, 1), f('pf')), concat(C(1, 1), f('cf'))))
    F.reg('eax', concat(concat(extract(V('eax', 32), 31, 16), ah), extract(V('eax', 32), 7, 0)))
@handler('sahf')
def sahf(c):
    F = c.F; ah = extract(V('eax', 32), 15, 8)
    for f, b in (('sf', 7), ('zf', 6), ('af', 4), ('pf', 2), ('cf', 0)): F.flag(f, bit(ah, b))
@handler('clc')
def clc(c): c.F.flag('cf', C(0, 1))
@handler('stc')
def stc(c): c.F.flag('cf', C(1, 1))
@handler('cmc')
def cmc(c): c.F.flag('cf', V('cf', 1) ^ 1)
@handler('cld')
def cld(c): c.F.flag('df', C(0, 1))
@handler('std')
def std(c): c.F.flag('df', C(1, 1))

@handler('movsb', 'movsw', 'movsd', 'stosb', 'stosw', 'stosd', 'lodsb', 'lodsw', 'lodsd', 'scasb', 'scasw', 'scasd', 'cmpsb', 'cmpsw', 'cmpsd')
def string(c):
    """ONE iteration step.  Without a prefix: the body.  With rep/repe/repne: if ecx == 0 nothing happens (eip = next); otherwise the body, ecx - 1,
    and eip stays on the instruction (the step repeats) unless ecx - 1 == 0 or (repe: ZF == 0 / repne: ZF == 1) after the body."""
    F = c.F; op, sz = c.m[:4], {'b': 1, 'w': 2, 'd': 4}[c.m[4]]; w = 8 * sz
    A = {n: AReg(c, n) for n in ('esi', 'edi', 'ecx')}          # address-size registers (CX/SI/DI under the 0x67 prefix)
    esi, edi, ecx = A['esi'].val, A['edi'].val, A['ecx'].val
    act = ~eq(ecx, 0) if c.rep else C(1, 1)
    step = ite(V('df', 1), C(-sz, 32), C(sz, 32)); acc = Op.reg({8: 'al', 16: 'ax', 32: 'eax'}[w], w)
    g = lambda new, old: ite(act, new, old)
    if op in ('movs', 'stos'):
        v = load(esi, w) if op == 'movs' else rd(acc); F.store(edi, g(v, load(edi, w)))
    if op in ('movs', 'cmps', 'lods'): A['esi'].put(F, g(esi + step, esi))
    if op in ('movs', 'cmps', 'scas', 'stos'): A['edi'].put(F, g(edi + step, edi))
    if op == 'lods': wr(F, acc, g(load(esi, w), rd(acc)))
    if op in ('cmps', 'scas'):
        a, b = (load(esi, w), load(edi, w)) if op == 'cmps' else (rd(acc), load(edi, w)); r = a - b
        for f, v in (('cf', ult(a, b)), ('of', msb((a ^ b) & (a ^ r))), ('af', bit(a ^ b ^ r, 4))): F.flag(f, g(v, V(f, 1)))
        pzs(F, r, lambda f, v: g(v, V(f, 1)))
    if c.rep:
        A['ecx'].put(F, g(ecx - 1, ecx)); more = ~eq(ecx - 1, 0)
        if op in ('cmps', 'scas'):
            zf = eq(a - b, 0); more = more & (zf if c.rep == 'repe' else ~zf)
        F.eip = ite(act & more, C(c.addr, 32), C(c.addr + c.ins.size, 32))
