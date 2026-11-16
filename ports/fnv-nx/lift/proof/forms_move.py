"""Moves, conversions, stack, EFLAGS and string families (see forms.py)."""
from fnvlift import dsl
from fnvlift.dsl import REGS, C, V, ashr, bit, concat, eq, extract, ite, msb, sext, trunc, ult, zext
from proof.forms import AReg, Op, isvec, cond, dsa, ea, esa, handler, mload, mstore, pzs, rd, sload, sstore, store_op, wr

LAHF_SDM = 'LAHF/SAHF: AH = SF:ZF:0:AF:0:PF:1:CF'
MODIFIABLE = 0x00240100 | 0x4000      # EFLAGS bits (besides the six, DF) POPFD may change in user mode: TF, NT, AC, ID (IF/IOPL need privilege)
def seg_load(c, reg, sel):
    """the load of the segment register REG with the 32-bit value SEL (< 2^16): SDM Vol 2 MOV / POP Sreg.  CS is #UD ('#UD If attempting to load the CS register');
    a selector the record's mov_to_sreg rows (fnvlift/x86sys.MOV_TO_SREG: the loadable non-null selectors by register, whether null loads) say cannot be loaded is #GP(selector), which
    the runtime reports as the access violation (the fault kind AV, as every #GP of the declared machine); the other selectors load.  Nothing is written when the fault is raised.
    A register for which the record leaves a selector undecided (mov_to_sreg.undecided: es ds fs gs; ss needs a writable data segment and so decides it) has no Form: UndecidedRow."""
    from fnvlift import x86sys as xs
    from proof.forms_sys import K_AV, K_UD
    if reg == 'cs': c.F.fault(K_UD, C(1, 1)); return
    sels, null = xs.MOV_TO_SREG[reg]; u = xs.REC['mov_to_sreg']['undecided']
    if reg != 'ss' and u['selectors']: raise dsl.UndecidedRow('uncertified: mov_to_sreg.undecided (selectors %s): %s' % (u['selectors'], u['why']))
    k = sel & C(0xfffc, 32); ok = C(0, 1)
    for i in sorted({v & 0xfffc for v in sels}): ok = ok | eq(k, C(i, 32))
    if null: ok = ok | eq(k, C(0, 32))
    if reg == 'ss': ok = ok & eq(sel & C(3, 32), C(xs.SEL['cs'] & 3, 32))      # SS: RPL = CPL, the RPL of the record's cs selector
    c.F.fault(K_AV, eq(ok, C(0, 1)))
    c.F.sel(reg, extract(sel, 15, 0))

@handler('mov')
def mov(c):
    if c.ops[0].k == 'r' and c.ops[0].name in dsl.SEGS:      # MOV Sreg, r/m16 (8E /r), claimed by the operand kind
        o = c.ops[1]; return seg_load(c, c.ops[0].name, zext(rd(o, 16 if o.k == 'm' else o.w) if o.w <= 16 or o.k != 'r' else extract(rd(o), 15, 0), 32))
    if any(o.k == 'r' and o.name[:2] in ('cr', 'dr') for o in c.ops):      # MOV to/from CRn / DRn: CPL 0 only (0F 20..23), KiTrap0D's table decides the status
        from proof import forms_sys
        return forms_sys.privileged(c)
    if c.ops[1].k == 'r' and c.ops[1].name in dsl.SEGS:      # MOV r/m16, Sreg / MOV r32, Sreg (SDM 2B MOV): DEST := the segment selector (the pre-state sel_<reg>, 16 bits), zero-extended into a 32-bit register, a 16-bit quantity in memory
        sel = V('sel_' + c.ops[1].name, 16); return wr(c.F, c.ops[0], zext(sel, c.ops[0].w) if c.ops[0].w > 16 else sel)
    wr(c.F, c.ops[0], rd(c.ops[1], c.ops[0].w))
@handler('movzx', 'movsx')
def movx(c): wr(c.F, c.ops[0], (zext if c.m == 'movzx' else sext)(rd(c.ops[1]), c.ops[0].w))
@handler('setcc')
def setcc(c): wr(c.F, c.ops[0], zext(cond(c.opc & 15), 8))
@handler('cmovcc')
def cmovcc(c): wr(c.F, c.ops[0], ite(cond(c.opc & 15), rd(c.ops[1]), rd(c.ops[0])))
@handler('lea')
def lea(c): wr(c.F, c.ops[0], trunc(ea(c.ops[1], seg=False), c.ops[0].w))      # LEA computes an offset: no segment base (SDM Vol. 2, LEA)
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
    wr(c.F, Op.reg('al', 8), mload(dsa(c, zext(trunc(a, 16), 32) if c.a16 else a), 8))

@handler('push')
def push(c):
    """SDM PUSH: operand size 32, or 16 under the 0x66 prefix (ESP - 2, a 16-bit store; the stack address size is SS.B = 32, the 0x67 prefix does not change it).
    The value of a register operand is its pre-state value (PUSH ESP / SP pushes the value before the decrement)."""
    F, o = c.F, c.ops[0]; w = 16 if c.o16 else 32
    if o.k == 'r' and o.name in dsl.SEGS:      # PUSH Sreg: ESP -= operand size, a 16-bit store of the selector; the upper half of a 32-bit slot is NOT written (pinned model x86-push-segment-register: 'a 16-bit move, leaving the upper portion of the stack location unmodified')
        F.reg('esp', V('esp', 32) - w // 8); sstore(F, V('esp', 32) - w // 8, V('sel_' + o.name, 16)); return
    v = rd(o, w)
    F.reg('esp', V('esp', 32) - w // 8); sstore(F, V('esp', 32) - w // 8, v)
@handler('pop')
def pop(c):
    F, o = c.F, c.ops[0]; esp = V('esp', 32); w = 16 if c.o16 else 32
    if o.k == 'r' and o.name in dsl.SEGS:      # POP Sreg: the word (a dword with operand size 32) at the stack top, its low 16 bits are the selector; ESP += operand size
        seg_load(c, o.name, zext(extract(sload(esp, w), 15, 0), 32)); F.reg('esp', esp + w // 8); return
    F.reg('esp', esp + w // 8)
    if o.k == 'm': store_op(F, o, ea(o, esp + w // 8), sload(esp, w))      # SDM POP: ESP as base of the destination is incremented before the address is computed
    else: wr(F, o, sload(esp, w))
@handler('pushal', 'pushad', 'pushaw')
def pushad(c):
    """SDM PUSHA/PUSHAD: the eight registers in the order EAX ECX EDX EBX ESP EBP ESI EDI, the pre-instruction ESP as the ESP slot; 16-bit words under the 0x66 prefix (PUSHA), ESP -= 8 * size"""
    F, esp = c.F, V('esp', 32); w = 16 if c.o16 else 32; n = w // 8
    for i, r in enumerate(REGS): sstore(F, esp - n * (i + 1), trunc(V(r, 32), w))
    F.reg('esp', esp - 8 * n)
@handler('popal', 'popad', 'popaw')
def popad(c):
    """SDM POPA/POPAD: the ESP slot is skipped; under the 0x66 prefix only the low words are written (bits 31:16 are kept)"""
    F, esp = c.F, V('esp', 32); w = 16 if c.o16 else 32; n = w // 8
    for i, r in enumerate(reversed(REGS)):
        if r == 'esp': continue
        v = sload(esp + n * i, w); F.reg(r, v if w == 32 else concat(extract(V(r, 32), 31, 16), v))
    F.reg('esp', esp + 8 * n)

def eflags_word():
    f = lambda n, b: zext(V(n, 1), 32) << b
    six = f('cf', 0) | f('pf', 2) | f('af', 4) | f('zf', 6) | f('sf', 7) | f('of', 11) | f('df', 10)
    return six | C(2, 32) | V('flo', 32)
@handler('pushfd', 'pushf')
def pushfd(c):
    F = c.F; F.reg('esp', V('esp', 32) - 4); sstore(F, V('esp', 32) - 4, eflags_word() & ~C(0x30000, 32))     # SDM PUSHFD: VM and RF are not copied
@handler('popfd', 'popf')
def popfd(c):
    F = c.F; v = sload(V('esp', 32), 32); F.reg('esp', V('esp', 32) + 4)
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
    if any(isvec(o) for o in c.ops):             # the SSE forms of movsd / cmpsd share the mnemonic with the string ops
        from proof import forms_simd
        if c.m == 'movsd': return forms_simd.movsd_sse(c)
        if c.m == 'cmpsd': from proof import forms_ssefp; return forms_ssefp.cmp_imm(c, 64, False)
        raise KeyError(c.m)
    """ONE iteration step.  Without a prefix: the body.  With rep/repe/repne: if ecx == 0 nothing happens (eip = next); otherwise the body, ecx - 1,
    and eip stays on the instruction (the step repeats) unless ecx - 1 == 0 or (repe: ZF == 0 / repne: ZF == 1) after the body."""
    F = c.F; op, sz = c.m[:4], {'b': 1, 'w': 2, 'd': 4}[c.m[4]]; w = 8 * sz
    A = {n: AReg(c, n) for n in ('esi', 'edi', 'ecx')}          # address-size registers (CX/SI/DI under the 0x67 prefix)
    esi, edi, ecx = A['esi'].val, A['edi'].val, A['ecx'].val
    act = ~eq(ecx, 0) if c.rep else C(1, 1)
    step = ite(V('df', 1), C(-sz, 32), C(sz, 32)); acc = Op.reg({8: 'al', 16: 'ax', 32: 'eax'}[w], w)
    g = lambda new, old: ite(act, new, old)
    S, D = (lambda: mload(dsa(c, esi), w)), (lambda: mload(esa(edi), w))     # the string source is DS-default (override applies), the destination ES (flat)
    if op in ('movs', 'stos'):
        v = S() if op == 'movs' else rd(acc); mstore(F, esa(edi), g(v, D()), fwhen=act)
    if op in ('movs', 'cmps', 'lods'): A['esi'].put(F, g(esi + step, esi))
    if op in ('movs', 'cmps', 'scas', 'stos'): A['edi'].put(F, g(edi + step, edi))
    if op == 'lods': wr(F, acc, g(S(), rd(acc)))
    if op in ('cmps', 'scas'):
        a, b = (S(), D()) if op == 'cmps' else (rd(acc), D()); r = a - b
        for f, v in (('cf', ult(a, b)), ('of', msb((a ^ b) & (a ^ r))), ('af', bit(a ^ b ^ r, 4))): F.flag(f, g(v, V(f, 1)))
        pzs(F, r, lambda f, v: g(v, V(f, 1)))
    if c.rep:
        A['ecx'].put(F, g(ecx - 1, ecx)); more = ~eq(ecx - 1, 0)
        if op in ('cmps', 'scas'):
            zf = eq(a - b, 0); more = more & (zf if c.rep == 'repe' else ~zf)
        F.eip = ite(act & more, C(c.addr, 32), C(c.addr + c.ins.size, 32))
