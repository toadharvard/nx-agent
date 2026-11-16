"""Legacy protected-mode integer forms (see forms.py): the BCD adjusts, SALC, the unnamed SAL encoding, SLDT, far RET, LES / LDS, SGDT, LAR and IRETD.  The semantics are the SDM Operation pseudocode (the quotes and their z3 comparison are
sysspec_legacy.py); every value of the declared machine is read from the record (platform/declared/x86sys.json through fnvlift/x86sys.py) and a row the record leaves undecided or does not have raises dsl.UndecidedRow naming it (the
shape is then uncertified, never given an invented answer).
  DAA DAS AAA AAS AAM AAD   SDM 2A:DAA ... AAD.  Outside 64-bit mode (the guest is a 32-bit process, compatibility mode): the Operation's ELSE branch.  AAM with imm8 = 0 is #DE (fault code 1, the same as DIV by zero).
  SALC                      AL := 0xff if CF else 0, no flag (SDM 2A:SALC).
  cleared-#UD encodings     D0 D1 D2 D3 C0 C1 /6 and F6 F7 /1 have no cell in the SDM opcode map (intud.CLEARED_UD): #UD, valid only under L3a's unreachable-or-data certificate of the site (forms.build short-cuts them by encoding).
  SLDT                      the record's sldt row (the null LDTR selector of a new process).
  RETF                      the same-privilege far return: the selector popped is checked like the far CALL / JMP target (forms_flow.far_transfer: the record's cs selector), the faults are the access violation.
  LES LDS                   MOV to ES / DS of the loaded selector (forms_move.seg_load: the record's mov_to_sreg rows).
  SGDT                      the record's sgdt limit and base rows.
  LAR IRETD                 rows the record does not have (a descriptor's access rights for LAR; the NT / VM cases of IRET): UndecidedRow."""
from fnvlift import dsl, x86sys
from fnvlift.dsl import C, V, concat, eq, extract, ite, trunc, udiv, ult, urem, zext
from proof.forms import addoff, ea, handler, mload, pzs, rd, sload, store_op, undef, wr
from fnvlift.faults import BYNAME as _FT
from proof.forms_sys import K_AV, K_UD, store16
K_DE = _FT['DE0']['code']

UNDEF_BCD = 'DAA/DAS: "The OF flag is undefined."  AAA/AAS: "The OF, SF, ZF, and PF flags are undefined."  AAM/AAD: "The OF, AF, and CF flags are undefined."'


def _ax(F): e = F.cur('eax'); return extract(e, 15, 0)
def _put_ax(F, ax): F.reg('eax', concat(extract(F.cur('eax'), 31, 16), ax))
def _put_al(F, al): F.reg('eax', concat(extract(F.cur('eax'), 31, 8), al))


@handler('daa', 'das')
def daa_das(c):
    F = c.F; sub = c.m == 'das'; al = extract(V('eax', 32), 7, 0); cf = V('cf', 1); af = V('af', 1)
    adj = ult(C(9, 8), al & C(15, 8)) | af                                # ((AL AND 0FH) > 9) or AF = 1
    a1 = al - C(6, 8) if sub else al + C(6, 8)
    carry = ult(al, C(6, 8)) if sub else ult(a1, al)                    # the borrow / the carry of AL := AL -/+ 6
    al1 = ite(adj, a1, al); cf1 = ite(adj, cf | carry, C(0, 1))
    hi = ult(C(0x99, 8), al) | cf                                         # (old_AL > 99H) or (old_CF = 1)
    al2 = ite(hi, al1 - C(0x60, 8) if sub else al1 + C(0x60, 8), al1)
    F.flag('cf', (cf1 | hi) if sub else hi)                               # DAS has no ELSE on CF := 1; DAA has CF := 0
    F.flag('af', adj); pzs(F, al2); _put_al(F, al2); undef(F, ('of',), UNDEF_BCD)


@handler('aaa', 'aas')
def aaa_aas(c):
    F = c.F; sub = c.m == 'aas'; ax = _ax(F); al = extract(ax, 7, 0); af = V('af', 1)
    adj = ult(C(9, 8), al & C(15, 8)) | af
    if sub:
        ax1 = ax - C(6, 16); ah1 = extract(ax1, 15, 8) - C(1, 8); new = concat(ah1, extract(ax1, 7, 0))      # AX := AX - 6; AH := AH - 1
    else: new = ax + C(0x106, 16)                                                                              # AX := AX + 106H
    ax2 = ite(adj, new, ax)
    _put_ax(F, concat(extract(ax2, 15, 8), extract(ax2, 7, 0) & C(15, 8)))                                    # AL := AL AND 0FH
    F.flag('af', adj); F.flag('cf', adj); undef(F, ('of', 'sf', 'zf', 'pf'), UNDEF_BCD)


@handler('aam', 'aad')
def aam_aad(c):
    F = c.F; assert len(c.ops) == 1 and c.ops[0].k == 'i', 'aam/aad without an imm8 operand'; imm = rd(c.ops[0], 8); al = extract(V('eax', 32), 7, 0); ah = extract(V('eax', 32), 15, 8)
    if c.m == 'aam':
        F.fault(K_DE, eq(imm, 0))                                            # SDM 2A:AAM: "#DE If an immediate value of 0 is used."
        q, r = udiv(al, imm), urem(al, imm); _put_ax(F, concat(q, r)); pzs(F, r)
    else:
        r = al + ah * imm; _put_ax(F, concat(C(0, 8), r)); pzs(F, r)      # AL := (tempAL + (tempAH * imm8)) AND FFH; AH := 0
    undef(F, ('of', 'af', 'cf'), UNDEF_BCD)


@handler('salc')
def salc(c): _put_al(c.F, ite(V('cf', 1), C(0xff, 8), C(0, 8)))


def cleared_ud(c):
    """an encoding of intud.CLEARED_UD (no cell in the SDM opcode map): #UD before any operand is read, the one class rule of forms.build (by opcode byte and ModRM.reg, never by the mnemonic)"""
    c.F.fault(K_UD, C(1, 1))


@handler('sldt')
def sldt(c):
    r = x86sys.REC['sldt']
    if 'value' not in r: raise dsl.UndecidedRow('sldt: the record row is undecided: %s' % r.get('why'))
    store16(c, C(r['value'], 16))


def _undecided_seg(c, reg):
    from proof.forms_move import seg_load
    o = c.ops[1]; w = 16 if c.o16 else 32
    sel = zext(mload(ea(o, off=w // 8), 16), 32)                          # LES / LDS r, m16:32 (m16:16): the offset first, the selector after it
    seg_load(c, reg, sel); wr(c.F, c.ops[0], mload(ea(o), w))


@handler('les')
def les(c): _undecided_seg(c, 'es')
@handler('lds')
def lds(c): _undecided_seg(c, 'ds')


@handler('sgdt')
def sgdt(c):
    r = x86sys.REC['sgdt']
    for n in ('limit', 'base'):
        if 'value' not in r[n]: raise dsl.UndecidedRow('sgdt.%s: the record row is undecided: %s' % (n, r[n].get('why')))
    o = c.ops[0]; a = ea(o)
    store_op(c.F, o, a, C(r['limit']['value'], 16)); store_op(c.F, o, addoff(a, C(2, 32)), C(r['base']['value'], 32))


@handler('lar')
def lar(c): raise dsl.UndecidedRow('lar: the record has no row of the descriptors\' access rights (lar_lsl); needs L5')


@handler('iretd', 'iret')
def iretd(c): raise dsl.UndecidedRow('iret: the record has no iret rows (EFLAGS.NT = 1 task return, EFLAGS.VM, the EFLAGS bits restorable at CPL 3); needs L5')


@handler('retf', 'lret')
def retf(c):
    """SDM 2B:RET far, protected mode, same privilege level: EIP := Pop(); CS := Pop(); then ESP += imm16.  RPL must not be below CPL (3): the popped selector must be the record's cs selector (RPL 3, the one ring-3 code
    segment); every other one is #GP, reported as the access violation, before any effect."""
    F = c.F; w = 16 if c.o16 else 32; nb = w // 8; esp = V('esp', 32); extra = c.ops[0].val & 0xffff if c.ops else 0
    eip, sel = sload(esp, w), sload(esp + nb, 16)
    F.fault(K_AV, ~eq(sel, C(x86sys.SEL['cs'], 16)))
    F.reg('esp', esp + (2 * nb + extra)); F.eip = zext(eip, 32)
