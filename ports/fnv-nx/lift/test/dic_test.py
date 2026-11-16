"""data_in_code / reloc-consistency / lifter-reach rules (tools/dic.py, tools/residue.py) on hand-assembled bytes. Run: python3 test/dic_test.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
import capstone, dic, residue
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
N = BAD = 0
def ck(name, got, want):
    global N, BAD
    N += 1
    if got != want: BAD += 1; print('FAIL', name, 'got', got, 'want', want)
T0 = 0x401000
# mov eax,[0x402000] (disp32 relocated at +1.. wait: a1 is moffs: imm-like) ; mov eax,[ebx+0x402000]
code = b'\x8b\x83\x00\x20\x40\x00' + b'\xb8\x00\x30\x40\x00' + b'\x90'
ins = [(T0, 6), (T0 + 6, 5), (T0 + 11, 1)]
ck('operands only', dic.nonoperand_words(md, code, T0, [T0 + 2, T0 + 7], ins)[0], [])
ck('relocation across the opcode', dic.nonoperand_words(md, code, T0, [T0 + 1], ins)[0], [T0 + 1])
ck('relocation across two instructions', dic.nonoperand_words(md, code, T0, [T0 + 4], ins)[0], [T0 + 4])
ck('word in a data range', dic.nonoperand_words(md, code, T0, [T0 + 1], ins, [(T0, T0 + 8, 'jtab', 'x')])[1], [T0 + 1])
# lifter_outside: lifter-only instructions after a call to an NR entry are a dead tail; others are violations
ia = {0x10: (5, 0x80, True), 0x15: (1, None, True), 0x16: (1, None, False), 0x20: (1, None, False)}
ck('dead tail', residue.lifter_outside(ia, [0x10], {0x80}), (1, [0x20], 2))
ck('no NR', residue.lifter_outside(ia, [0x10], set()), (3, [0x15, 0x16, 0x20], 0))
# protect sites: call [iat], and call to a thunk jmp [iat]
imports = {0x403000: 'kernel32.dll!VirtualProtect', 0x403004: 'kernel32.dll!Sleep'}
c2 = b'\xff\x15\x00\x30\x40\x00' + b'\xe8\x00\x00\x00\x00' + b'\xff\x25\x00\x30\x40\x00' + b'\xff\x15\x04\x30\x40\x00'
i2 = [(T0, 6), (T0 + 6, 5), (T0 + 11, 6), (T0 + 17, 6)]
ck('protect sites', dic.protect_sites(md, c2, T0, i2, imports), [(T0, 'kernel32.dll!VirtualProtect', 'iat'), (T0 + 6, 'kernel32.dll!VirtualProtect', 'thunk')])
print('%d checks, %d failed' % (N, BAD)); sys.exit(1 if BAD else 0)
