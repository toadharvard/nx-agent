"""How x86-32 code uses the address in a relocation: read, write (store / read-modify-write), address taken,
call / jump target. Linear sweep of one function with capstone, then one detailed decode per relocated
instruction. Bytes that do not decode on an instruction boundary (switch tables after the function) are 'table'.
"""
import bisect

import capstone
from capstone import x86 as cx86

WRITE_MNEM = {'fst', 'fstp', 'fist', 'fistp', 'fisttp', 'fnstcw', 'fstcw', 'fnstsw', 'fstsw', 'fnstenv', 'fnsave',
              'fbstp', 'stosd', 'stosb', 'stosw'}
READ = 'read'
STORE = 'store'
RMW = 'rmw'
ADDR = 'addr'
THIS = 'this'        # mov ecx, offset x: the object a __thiscall (constructor) works on
ARG = 'arg'          # push offset x
CALL = 'call'
JUMP = 'jump'
TABLE = 'table'


class Classifier:
    def __init__(self):
        self.lite = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.full = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
        self.full.detail = True

    def boundaries(self, code, start, end):
        """instruction start offsets of a linear sweep over code[start:end]"""
        starts, sizes = [], []
        off = start
        while off < end:
            n = 0
            for addr, size, _m, _o in self.lite.disasm_lite(code[off:end], off):
                starts.append(addr)
                sizes.append(size)
                n += size
            if n == 0:
                break
            off += n
            if off < end:          # undecodable byte: skip it (padding, table)
                off += 1
        return starts, sizes

    def classify(self, code, starts, sizes, roff):
        """use of the 4-byte relocated field at roff"""
        i = bisect.bisect_right(starts, roff) - 1
        if i < 0 or roff + 4 > starts[i] + sizes[i]:
            return TABLE
        s, n = starts[i], sizes[i]
        insn = next(self.full.disasm(code[s:s + n], s), None)
        if insn is None:
            return TABLE
        field = roff - s
        m = insn.mnemonic
        if insn.imm_size == 4 and insn.imm_offset == field:
            if m == 'call':
                return CALL
            if m.startswith('j'):
                return JUMP
            if m == 'push':
                return ARG
            if m == 'mov' and insn.operands and insn.operands[0].type == cx86.X86_OP_REG and \
                    insn.reg_name(insn.operands[0].reg) == 'ecx':
                return THIS
            return ADDR
        if insn.disp_size == 4 and insn.disp_offset == field:
            if m == 'lea':
                return ADDR
            if m in WRITE_MNEM or m.startswith('set'):
                return STORE
            for op in insn.operands:
                if op.type == cx86.X86_OP_MEM:
                    if op.access & capstone.CS_AC_WRITE:
                        return RMW if op.access & capstone.CS_AC_READ else STORE
                    return READ
            return READ
        return TABLE
