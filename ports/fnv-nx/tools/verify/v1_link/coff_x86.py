"""coff_x86.py - V1-link: i386 COFF reader (MSVC 2008 objects) and an entry-register use analysis.

    obj = Coff(path); obj.func(decorated name) -> (code bytes, {offset: target symbol name})
    entry_reads(code, relocs, conv_of) -> {'ecx': kind, 'edx': kind}

kind (strongest over all paths from the entry, before the register is written):
  'read'     an instruction reads the incoming value (operand, address base/index, cl/ch/cx sub-registers),
  'pass'     a call / tail jump to a callee that takes it (thiscall / fastcall target, by its decorated name),
             or an indirect call while the register is unwritten,
  'push'     only `push reg` (MSVC also uses push ecx to reserve a stack word),
  None       never used before it is written.
xor r, r / sub r, r count as writes. Calls write ecx and edx (caller-saved). Own code, no tools/link.
"""
import struct

import capstone
from capstone import x86 as cx

CS = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
CS.detail = True

SUB = {'ecx': {cx.X86_REG_ECX, cx.X86_REG_CX, cx.X86_REG_CL, cx.X86_REG_CH},
       'edx': {cx.X86_REG_EDX, cx.X86_REG_DX, cx.X86_REG_DL, cx.X86_REG_DH}}
FULL = {'ecx': {cx.X86_REG_ECX, cx.X86_REG_CX}, 'edx': {cx.X86_REG_EDX, cx.X86_REG_DX}}
RANK = {None: 0, 'push': 1, 'pass': 2, 'read': 3}


class Coff:
    def __init__(self, path):
        b = open(path, 'rb').read()
        self.b = b
        mach, nsec, _, symptr, nsym, opt, _ = struct.unpack_from('<HHIIIHH', b, 0)
        if mach == 0 and nsec == 0xffff:
            raise ValueError('bigobj not supported: ' + path)
        self.secs = []
        for i in range(nsec):
            o = 20 + opt + 40 * i
            name = b[o:o + 8].rstrip(b'\0').decode('latin-1')
            vsize, vaddr, size, ptr, relptr, _, nrel, _, flags = struct.unpack_from('<IIIIIIHHI', b, o + 8)
            self.secs.append((name, size, ptr, relptr, nrel, flags))
        strtab = symptr + 18 * nsym
        self.syms = []
        i = 0
        while i < nsym:
            o = symptr + 18 * i
            raw = b[o:o + 8]
            if raw[:4] == b'\0\0\0\0':
                off = struct.unpack_from('<I', raw, 4)[0]
                e = b.index(b'\0', strtab + off)
                name = b[strtab + off:e].decode('latin-1')
            else:
                name = raw.rstrip(b'\0').decode('latin-1')
            value, secno, typ, sclass, naux = struct.unpack_from('<IhHBB', b, o + 8)
            self.syms.append((name, value, secno, typ, sclass))
            for _ in range(naux):
                self.syms.append(None)
            i += 1 + naux
        self.by_name = {}
        for k, s in enumerate(self.syms):
            if s and s[2] > 0:
                self.by_name.setdefault(s[0], k)

    def relocs(self, secno):
        name, size, ptr, relptr, nrel, flags = self.secs[secno - 1]
        out = {}
        for k in range(nrel):
            va, si, typ = struct.unpack_from('<IIH', self.b, relptr + 10 * k)
            s = self.syms[si]
            out[va] = (s[0] if s else '?', typ)
        return out

    def func(self, name):
        k = self.by_name.get(name)
        if k is None:
            return None
        sname, value, secno, typ, sclass = self.syms[k]
        sec = self.secs[secno - 1]
        data = self.b[sec[2]:sec[2] + sec[1]]
        # end: next function symbol in the same section, else the section end
        end = len(data)
        for s in self.syms:
            if s and s[2] == secno and value < s[1] < end and (s[3] & 0x20):
                end = s[1]
        rel = {va - value: t for va, t in self.relocs(secno).items() if value <= va < end}
        return data[value:end], rel


def _reads_writes(ins):
    try:
        r, w = ins.regs_access()
    except capstone.CsError:
        return set(), set()
    r, w = set(r), set(w)
    ops = ins.operands
    if ins.mnemonic in ('xor', 'sub') and len(ops) == 2 and ops[0].type == cx.X86_OP_REG and \
            ops[1].type == cx.X86_OP_REG and ops[0].reg == ops[1].reg:
        r.discard(ops[0].reg)                       # zeroing idiom
    if len(ops) == 2 and ops[0].type == cx.X86_OP_REG and ops[1].type == cx.X86_OP_IMM and \
            ((ins.mnemonic == 'or' and ops[1].imm & 0xffffffff == 0xffffffff) or
             (ins.mnemonic == 'and' and ops[1].imm == 0)):
        r.discard(ops[0].reg)                       # or r, -1 / and r, 0: the old value does not matter
    return r, w


def entry_reads(code, rel, conv_of):
    """conv_of(symbol name) -> set of registers ('ecx', 'edx') that callee takes as parameters (or None: unknown)."""
    result = {'ecx': None, 'edx': None}
    insns = {}
    for ins in CS.disasm(code, 0):
        insns[ins.address] = ins
    order = sorted(insns)
    nxt = {a: order[i + 1] if i + 1 < len(order) else None for i, a in enumerate(order)}

    def note(reg, kind):
        if RANK[kind] > RANK[result[reg]]:
            result[reg] = kind
    seen = set()
    work = [(0, frozenset(('ecx', 'edx')))]
    while work:
        a, live = work.pop()
        while a is not None and a in insns:
            key = (a, live)
            if key in seen:
                break
            seen.add(key)
            if not live:
                break
            ins = insns[a]
            mn = ins.mnemonic
            r, w = _reads_writes(ins)
            target = None
            for off in range(a, a + ins.size):
                if off in rel:
                    target = rel[off][0]
            if mn == 'call' or (mn == 'jmp' and target is not None):
                takes = conv_of(target) if target is not None else None
                if target is None:
                    op = ins.operands[0] if ins.operands else None
                    for reg in live:
                        if op is not None and op.type == cx.X86_OP_REG and op.reg in SUB[reg]:
                            note(reg, 'read')
                        elif op is not None and op.type == cx.X86_OP_MEM and (op.mem.base in SUB[reg] or
                                                                            op.mem.index in SUB[reg]):
                            note(reg, 'read')
                        else:
                            note(reg, 'pass')      # indirect call: the callee may take ecx/edx
                else:
                    for reg in live:
                        if takes is None or reg in takes:
                            note(reg, 'pass')
                if mn == 'jmp':
                    break
                live = frozenset()                  # caller-saved: written by the call
                a = nxt[a]
                continue
            for reg in live:
                if r & SUB[reg]:
                    note(reg, 'push' if mn == 'push' else 'read')
            live = frozenset(x for x in live if not (w & FULL[x]))
            if mn in ('ret', 'retn', 'int3', 'ud2', 'hlt'):
                break
            if mn == 'jmp':
                op = ins.operands[0]
                if op.type == cx.X86_OP_IMM:
                    a = op.imm
                    continue
                for reg in live:
                    if op.type == cx.X86_OP_MEM and (op.mem.base in SUB[reg] or op.mem.index in SUB[reg]):
                        note(reg, 'read')
                break                               # jump table: switch targets are not followed
            if mn.startswith('j') or mn.startswith('loop'):
                op = ins.operands[0]
                if op.type == cx.X86_OP_IMM:
                    work.append((op.imm, live))
            a = nxt[a]
    return result
