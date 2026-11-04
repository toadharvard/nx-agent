"""ecxflow.py: where the incoming ecx of an MSVC function lives at each instruction (must / may sets over all paths).

flow(code) -> {insn offset: (must set, may set)} of 32-bit registers holding the incoming ecx before that insn.
Calls clobber eax/ecx/edx. Stack slots are not tracked except `push ecx` / `pop r` pairs are ignored (unknown).
"""
import capstone
from capstone import x86 as cx

CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CS.detail = True
R32 = {cx.X86_REG_EAX: 'eax', cx.X86_REG_ECX: 'ecx', cx.X86_REG_EDX: 'edx', cx.X86_REG_EBX: 'ebx',
       cx.X86_REG_ESI: 'esi', cx.X86_REG_EDI: 'edi', cx.X86_REG_EBP: 'ebp'}
PART = {cx.X86_REG_AX: 'eax', cx.X86_REG_AL: 'eax', cx.X86_REG_AH: 'eax', cx.X86_REG_CX: 'ecx',
        cx.X86_REG_CL: 'ecx', cx.X86_REG_CH: 'ecx', cx.X86_REG_DX: 'edx', cx.X86_REG_DL: 'edx',
        cx.X86_REG_DH: 'edx', cx.X86_REG_BX: 'ebx', cx.X86_REG_BL: 'ebx', cx.X86_REG_BH: 'ebx',
        cx.X86_REG_SI: 'esi', cx.X86_REG_DI: 'edi', cx.X86_REG_BP: 'ebp'}
ALL = frozenset(R32.values())


def disasm(code):
    return {i.address: i for i in CS.disasm(code, 0)}


def written(ins):
    try:
        _, w = ins.regs_access()
    except capstone.CsError:
        return set()
    out = set()
    for r in w:
        if r in R32:
            out.add(R32[r])
        elif r in PART:
            out.add(PART[r])
    return out


def step(ins, st):
    mn = ins.mnemonic
    ops = ins.operands
    if mn == 'call':
        return st - {'eax', 'ecx', 'edx'}
    if mn == 'mov' and len(ops) == 2 and ops[0].type == cx.X86_OP_REG and ops[0].reg in R32:
        d = R32[ops[0].reg]
        if ops[1].type == cx.X86_OP_REG and ops[1].reg in R32:
            s = R32[ops[1].reg]
            return (st | {d}) if s in st else (st - {d})
        return st - {d}
    if mn == 'xchg' and len(ops) == 2 and all(o.type == cx.X86_OP_REG and o.reg in R32 for o in ops):
        a, b = R32[ops[0].reg], R32[ops[1].reg]
        n = set(st) - {a, b}
        if a in st: n.add(b)
        if b in st: n.add(a)
        return frozenset(n)
    return st - written(ins)


def succs(ins, insns, nxt):
    mn = ins.mnemonic
    if mn in ('ret', 'retn', 'int3', 'ud2', 'hlt'):
        return []
    out = []
    if mn == 'jmp':
        op = ins.operands[0]
        if op.type == cx.X86_OP_IMM and op.imm in insns:
            return [op.imm]
        return []
    if mn.startswith('j') or mn.startswith('loop'):
        op = ins.operands[0]
        if op.type == cx.X86_OP_IMM and op.imm in insns:
            out.append(op.imm)
    if nxt.get(ins.address) is not None:
        out.append(nxt[ins.address])
    return out


def flow(code, entry=frozenset({'ecx'}), noreturn=()):
    insns = disasm(code)
    order = sorted(insns)
    nxt = {a: order[k + 1] if k + 1 < len(order) else None for k, a in enumerate(order)}
    must, may = {0: frozenset(entry)}, {0: frozenset(entry)}
    work = [0]
    while work:
        a = work.pop()
        ins = insns.get(a)
        if ins is None:
            continue
        om, oy = step(ins, must[a]), step(ins, may[a])
        for s in succs(ins, insns, nxt):
            nm = om if s not in must else (must[s] & om)
            ny = oy if s not in may else (may[s] | oy)
            if must.get(s) != nm or may.get(s) != ny:
                must[s], may[s] = nm, ny
                work.append(s)
    return insns, {a: (must[a], may.get(a, frozenset())) for a in must}


def at_reloc(insns, states, off):
    """(instruction containing byte `off`, must, may) or (None, ...)."""
    for a, ins in insns.items():
        if a <= off < a + ins.size:
            m = states.get(a)
            return ins, (m[0] if m else None), (m[1] if m else None)
    return None, None, None
