#!/usr/bin/env python3
"""Sticky exception flags: two shadow sets, one host FPSR (task C4a / B2; rt/fnvl_x87.h "STICKY EXCEPTION FLAGS"). Sequences that interleave x87 and SSE arithmetic and read each family's set,
run on the lifted code (test/mmxtest.py machinery) against the x86 definition: the x87 flags (FSW bits 0-5) and the SSE flags (MXCSR bits 0-5) are separate sticky sets; FNCLEX clears only the x87
set; an operation of one family never shows in the other family's set.
    fpflags.py"""
import os, sys, struct
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import mmxtest as M

DST = M.DST
# mov [ebx],edx (1.0f); mov dword [ebx+4],0; fld dword [ebx]; fld dword [ebx+4]; fdivp st(1),st      1/0 on the x87: ZE (0x04); TOP 0 -> 6 -> 7. The operand comes from a register of the
# guest, not from a constant, so the host compiler cannot fold or hoist the division (assumption ASM-206 of rt/fnvl_x87.h: an FP operation stays between the owner switch and the read)
X87_ZE = bytes([0x89, 0x13, 0xc7, 0x43, 0x04, 0, 0, 0, 0, 0xd9, 0x03, 0xd9, 0x43, 0x04, 0xde, 0xf9])
# movd xmm0,eax; movd xmm1,ecx; mulss xmm0,xmm1    FLT_MAX * 2.0 on the SSE unit: OE | PE (0x28)
SSE_OEPE = bytes([0x66, 0x0f, 0x6e, 0xc0, 0x66, 0x0f, 0x6e, 0xc9, 0xf3, 0x0f, 0x59, 0xc1])
FNSTSW = lambda d: bytes([0xdd, 0x7f, d]) if d else bytes([0xdd, 0x3f])               # fnstsw [edi+d]
STMXCSR = lambda d: bytes([0x0f, 0xae, 0x5f, d]) if d else bytes([0x0f, 0xae, 0x1f])  # stmxcsr [edi+d]


def setup(st):
    st['regs'][0], st['regs'][1], st['regs'][2], st['mxcsr'] = 0x7f7fffff, 0x40000000, 0x3f800000, 0x1f80


def cases():
    w16 = lambda v: struct.pack('<H', v); w32 = lambda v: struct.pack('<I', v)
    out = []
    out.append(('x87 ZE, then SSE OE|PE: each set shows only its own family',
                X87_ZE + SSE_OEPE + FNSTSW(0) + STMXCSR(4) + bytes([0xdb, 0xe2]) + FNSTSW(8) + STMXCSR(12) + X87_ZE + STMXCSR(16) + FNSTSW(20), 0,
                lambda st: {DST: w16(0x3804), DST + 4: w32(0x1fa8), DST + 8: w16(0x3800), DST + 12: w32(0x1fa8), DST + 16: w32(0x1fa8), DST + 20: w16(0x3004)}, setup))
    out.append(('SSE OE|PE first, then x87 ZE (owner changes the other way)',
                SSE_OEPE + X87_ZE + STMXCSR(0) + FNSTSW(4), 0, lambda st: {DST: w32(0x1fa8), DST + 4: w16(0x3804)}, setup))
    out.append(('ldmxcsr replaces the SSE set (the live OE|PE are discarded), fnclex leaves it',
                SSE_OEPE + bytes([0xc7, 0x03, 0x81, 0x1f, 0, 0, 0x0f, 0xae, 0x13]) + STMXCSR(0) + bytes([0xdb, 0xe2]) + STMXCSR(4), 0, lambda st: {DST: w32(0x1f81), DST + 4: w32(0x1f81)}, setup))
    return out


if __name__ == '__main__':
    sys.exit(1 if M.run(cases(), work='/home/user/l3work/fpflags', tag=2000) else 0)
