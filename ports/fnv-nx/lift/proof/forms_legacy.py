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
from fnvlift.dsl import C, V, bit, concat, eq, extract, ite, trunc, udiv, uf, ult, urem, zext
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


def _rec(*keys):
    """the record row at KEYS (fnvlift.x86sys.REC); a row that is undecided or absent raises dsl.UndecidedRow with the record's own reason (the shape is then uncertified, never given an invented answer)"""
    from proof import x86sys as px
    try: return px.get(x86sys.REC, *keys)
    except px.Undecided as e: raise dsl.UndecidedRow(str(e))


UF_ACCESSED = 'gdt_accessed'      # the Accessed bit of a GDT descriptor is processor state (the record, lar_lsl.access_rights.accessed_bit): a value the declared machine supplies, a function of the descriptor's selector offset


def lar_lsl_rows(row):
    """-> [(descriptor selector base, dest rule)] of the descriptors whose lar / lsl answer is ZF = 1.  Every other decided selector class (null, TI = 1, beyond the GDT limit, a descriptor the check refuses) is ZF = 0 with the
    destination unchanged: a record whose such class has another answer is not this Form's rule and is a build error"""
    r = _rec('lar_lsl'); assert r['cpl'] == 3
    for k in ('null_selector', 'ti1', 'beyond_gdt_limit'): assert r[k][row] == {'zf': 0, 'dest': 'unchanged'}, ('lar_lsl.%s.%s' % (k, row), r[k][row])
    out = []
    for off, d in sorted(r['descriptors'].items(), key=lambda kv: int(kv[0], 16)):
        a = d[row]; base = d['selectors'][0]
        assert d['selectors'] == [base, base + 1, base + 2, base + 3] and base == int(off, 16), ('lar_lsl.descriptors.%s.selectors' % off, d['selectors'])
        if a['zf'] == 0: assert a['dest'] == 'unchanged', ('lar_lsl.descriptors.%s.%s' % (off, row), a)
        else: assert a['zf'] == 1 and isinstance(a['dest'], dict), ('lar_lsl.descriptors.%s.%s' % (off, row), a); out.append((base, a['dest']))
    return out


@handler('lar', 'lsl')
def lar_lsl(c):
    """SDM 2A:LAR / LSL (protected mode, CPL 3, the record's lar_lsl rows).  ZF := 1 and DEST := the access rights (LAR) or the byte limit (LSL) of the descriptor, for the selectors of the record's ZF = 1 descriptors; every other selector
    is ZF := 0 with DEST unchanged.  LAR bit 8 is the Accessed bit, processor state: uf gdt_accessed of the descriptor's selector offset (never a constant); bits 19:16 are SDM-undefined and written 0 (DEF0).  The source is the
    selector, 16 bits of a register or of memory; a 16-bit operand writes the low 16 bits of the result only."""
    F = c.F; lar = c.m == 'lar'; o = c.ops[1]
    sel = trunc(rd(o), 16) if o.k == 'r' else mload(ea(o), 16)
    rows = lar_lsl_rows('lar' if lar else 'lsl'); w = 16 if c.o16 else 32
    zf, val = C(0, 1), C(0, 32)
    for base, d in rows:
        hit = eq(sel & C(0xfffc, 16), C(base, 16))
        if lar:
            assert d['mask'] & d['undefined_mask'] == d['undefined_mask'] and d['value_with_accessed_clear'] & ~d['mask'] == 0 and d['value_with_accessed_clear'] & d['accessed_mask'] == 0, d
            acc = uf(UF_ACCESSED, 1, sel & C(0xfff8, 16))
            v = C(d['value_with_accessed_clear'] & ~d['undefined_mask'], 32) | (zext(acc, 32) << 8)
            assert d['accessed_mask'] == 0x100
        else: v = C(d['limit'], 32)
        zf, val = zf | hit, ite(hit, v, val)
    F.flag('zf', zf)
    wr(F, c.ops[0], val if w == 32 else trunc(val, 16), when=zf)


@handler('iretd', 'iret')
def iretd(c):
    """SDM 2A:IRET, protected mode, EFLAGS.NT = 0 and EFLAGS.VM = 0, CPL 3, the record's iret rows (same_privilege_return, eflags_restored): EIP, CS, EFLAGS are popped (three dwords); the one CS the record lets return is
    its returns_to selector (RPL 3 = CPL: no SS:ESP pop); any other selector is #GP (the null selector and every other: the access violation of the declared machine, as every selector fault of the far transfers); EFLAGS takes
    the bits of eflags_restored.loaded_mask from the popped value and keeps the others (IOPL, IF, VM, VIF, VIP are not loaded at CPL 3).  NT = 1 (task return) and CR4.PVI/VME are rows the record leaves undecided: they are outside
    the Form's domain (sysspec_legacy: the proof is `uncertified` for them)."""
    F = c.F; r = _rec('iret')
    if c.o16: raise dsl.UndecidedRow('iret: the record row iret has operand_size 32 only; the 16-bit IRET (0x66 prefix) is not a row')
    assert r['cpl'] == 3 and r['operand_size'] == 32
    ck = r['same_privilege_return']['cs_selector_checks']; ok = ck['returns_to']; mask = r['eflags_restored']['loaded_mask']
    esp = V('esp', 32); eip, cs, v = sload(esp, 32), sload(esp + 4, 32), sload(esp + 8, 32)
    good = C(0, 1)
    for sl in ok: good = good | eq(trunc(cs, 16), C(sl, 16))
    F.fault(K_AV, ~good)
    F.reg('esp', esp + 12); F.eip = eip
    for f, b in (('cf', 0), ('pf', 2), ('af', 4), ('zf', 6), ('sf', 7), ('of', 11), ('df', 10)): F.flag(f, bit(v, b))
    six = sum(1 << b for b in (0, 2, 4, 6, 7, 10, 11)); rest = mask & ~six & ~2      # bit 1 is the always-1 bit (eflags_word), not a stored bit
    F.setflo((V('flo', 32) & ~C(rest, 32)) | (v & C(rest, 32)))


@handler('retf', 'lret')
def retf(c):
    """SDM 2B:RET far, protected mode, same privilege level: EIP := Pop(); CS := Pop(); then ESP += imm16.  RPL must not be below CPL (3): the popped selector must be the record's cs selector (RPL 3, the one ring-3 code
    segment); every other one is #GP, reported as the access violation, before any effect."""
    F = c.F; w = 16 if c.o16 else 32; nb = w // 8; esp = V('esp', 32); extra = c.ops[0].val & 0xffff if c.ops else 0
    eip, sel = sload(esp, w), sload(esp + nb, 16)
    F.fault(K_AV, ~eq(sel, C(x86sys.SEL['cs'], 16)))
    F.reg('esp', esp + (2 * nb + extra)); F.eip = zext(eip, 32)
