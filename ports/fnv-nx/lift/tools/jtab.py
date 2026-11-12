#!/usr/bin/env python3
"""Jump-table edges by a guard rule over decoded code (modlift.py; the same rule for any engine that supplies the accessors).
A `jmp [T + r*4]` (T a relocated displacement, no base register) contributes the words T[0..N) as control-flow edges iff:
  G  its index register r is bounded: walking back over instructions that each have exactly ONE predecessor (the one before it, and none of them a seed: call target,
     relocated code address, PE entry) and none of which writes r (a conditional branch whose fall-through is the chain writes nothing), the chain reaches either `cmp r, imm` immediately followed by
     `ja` / `jae` whose fall-through is the chain (r <= imm resp. r < imm unsigned on the path to the jump): N = imm + 1 resp. imm; or the last write of r: `and r, imm` (r <= imm) or `mov r, imm`: N = imm + 1. A byte index table in between, `movzx r, byte [D + q]` (D relocated), makes N = 1 + the largest of
     the bytes D[0..Nq) where Nq is q's bound found by the same walk;
  W  every word T[0..N) is a relocated dword whose value is the address of a decoded-or-decodable instruction in an executable section.
Anything else is RED (the site is reported with its reason, no edge is invented): no guard, a guard on another register or width, a signed guard, a chain interrupted by another
predecessor / seed / a write of r / a control transfer, a table word that is not a relocated code address.
Soundness: the guard dominates the jump (unique predecessors on the chain; only a seeded instruction is reached by an edge the decoder does not see), so r < N whenever the jump executes;
the words are code addresses by W; hence every possible target is an edge. Assumption used: ASM-006 (OWNER: the exe makes no wild accesses): no computed jump enters the middle of the chain."""
from capstone import x86 as X

FULL = {f: f for f in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp')}
for _f, _s in {'eax': 'ax al ah', 'ebx': 'bx bl bh', 'ecx': 'cx cl ch', 'edx': 'dx dl dh', 'esi': 'si', 'edi': 'di', 'ebp': 'bp', 'esp': 'sp'}.items():
    for _x in _s.split(): FULL[_x] = _f
M32 = 0xffffffff


def writes(ins, reg):
    """True if the instruction may write register reg (full register) or is not understood (conservative)"""
    try: w = [FULL.get(ins.reg_name(r)) for r in ins.regs_access()[1]]
    except Exception: return True
    return reg in w


def guard(site, ins_at, pred1, seeded, reg, rset, u8, lo_hi):
    """-> (N, tail) walking back from the instruction at `site` for register `reg`: (N, None) from a cmp/ja guard, or (None, why). pred1(x) = the unique predecessor of x or None."""
    x = site; ins = ins_at(site)
    while True:
        if seeded(x): return None, 'chain entered by a seed at %x' % x
        p = pred1(x)
        if p is None: return None, 'no unique predecessor of %x' % x
        q = ins_at(p)
        if q is None: return None, 'undecoded predecessor'
        mn = q.mnemonic
        if mn in ('ja', 'jae', 'jnbe', 'jnb', 'jnc'):
            c = pred1(p)
            if c is None or seeded(p): return None, 'no unique predecessor of the guard branch %x' % p
            if q.operands[0].imm & M32 == x: return None, 'the guard branch %x jumps to the chain' % p      # the taken edge is the chain: wrong polarity
            cm = ins_at(c)
            if cm is None or cm.mnemonic != 'cmp' or len(cm.operands) != 2 or cm.operands[0].type != X.X86_OP_REG or cm.operands[0].size != 4 or FULL.get(cm.reg_name(cm.operands[0].reg)) != reg or cm.operands[1].type != X.X86_OP_IMM:
                return None, 'no cmp reg32, imm before the guard branch %x' % p
            imm = cm.operands[1].imm & M32
            if imm >= 1 << 31: return None, 'guard immediate is negative'
            return (imm + 1 if mn in ('ja', 'jnbe') else imm), None
        if mn == 'movzx' and len(q.operands) == 2 and q.operands[0].type == X.X86_OP_REG and FULL.get(q.reg_name(q.operands[0].reg)) == reg:
            m = q.operands[1]
            if m.type != X.X86_OP_MEM or m.size != 1 or bool(m.mem.base) == bool(m.mem.index) or (m.mem.index and m.mem.scale != 1) or m.mem.segment or (p + q.disp_offset) not in rset: return None, 'movzx of the index is no relocated byte table'
            qreg = FULL.get(q.reg_name(m.mem.base or m.mem.index)); D = m.mem.disp & M32
            nq, why = guard(p, ins_at, pred1, seeded, qreg, rset, u8, lo_hi)
            if nq is None: return None, 'byte index table: ' + why
            vals = [u8(D + k) for k in range(nq)]
            if None in vals: return None, 'byte index table outside the image'
            return max(vals) + 1, None
        o0 = q.operands[0] if q.operands else None
        if mn in ('and', 'mov') and len(q.operands) == 2 and o0.type == X.X86_OP_REG and o0.size == 4 and FULL.get(q.reg_name(o0.reg)) == reg and q.operands[1].type == X.X86_OP_IMM:
            return (q.operands[1].imm & M32) + 1, None          # r = r & imm <= imm; r = imm: the last write of r on every path (unique predecessors)
        if mn[0] == 'j' and mn != 'jmp' and mn != 'jecxz' and not mn.startswith('jcxz') and (p + q.size) == x: x = p; continue      # a conditional branch on the chain, its fall-through is the chain: no register is written
        if q.mnemonic in ('call', 'jmp', 'ret') or q.mnemonic[0] == 'j' or writes(q, reg): return None, '%s at %x changes the index register or ends the chain' % (q.mnemonic, p)
        x = p


def resolve(site, ins, ins_at, pred1, seeded, rset, u32, u8, intext, decodable):
    """-> (targets tuple, None) or (None, why). ins = the capstone instruction of the jmp at site."""
    op = ins.operands[0]
    if op.mem.base or op.mem.segment: return None, 'table jump with a base register or segment'
    T = op.mem.disp & M32; reg = FULL.get(ins.reg_name(op.mem.index))
    if (site + ins.disp_offset) not in rset: return None, 'table displacement is not relocated'
    N, why = guard(site, ins_at, pred1, seeded, reg, rset, u8, None)
    if N is None: return None, why
    if N == 0: return None, 'empty table'
    out = []
    for k in range(N):
        a = T + 4 * k
        if a not in rset: return None, 'table word %x is not a relocated dword' % a
        v = u32(a)
        if not intext(v) or not decodable(v): return None, 'table word %x -> %x is no code address' % (a, v)
        out.append(v)
    return tuple(out), None
