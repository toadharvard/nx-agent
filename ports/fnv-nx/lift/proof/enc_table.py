"""Concrete encodings of the W8 slice: list of (hex bytes).  Assembled with keystone (PyPI) from the texts below, plus raw
hex for the direction-reversed ALU forms.  prove.py decodes every entry with capstone and takes the form key from it."""
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
_ks = Ks(KS_ARCH_X86, KS_MODE_32)
def asm(t): return bytes(_ks.asm(t)[0])
ALU = ['add', 'adc', 'sub', 'sbb', 'cmp', 'and', 'or', 'xor', 'test']
BASE = {'add': 0x00, 'or': 0x08, 'adc': 0x10, 'sbb': 0x18, 'and': 0x20, 'sub': 0x28, 'xor': 0x30, 'cmp': 0x38}
def encodings():
    T = []; R = []
    for m in ALU:
        T += ['%s bl, cl' % m, '%s dx, bx' % m, '%s ebx, ecx' % m, '%s esi, esi' % m, '%s ah, dl' % m,
              '%s bl, 0x7f' % m, '%s bl, -5' % m, '%s cx, 0x1234' % m, '%s cx, -5' % m, '%s ebx, 0x12345678' % m, '%s ebx, -5' % m,
              '%s al, 0x81' % m, '%s ax, 0x1234' % m, '%s eax, 0x12345678' % m]
        if m in BASE: R += [bytes([BASE[m] + 2, 0xc1]), bytes([BASE[m] + 3, 0xcb])]       # op al,cl / op ecx,ebx (reversed direction)
    T += ['add [ebx+ecx*4+0x12345678], eax', 'add eax, [ebx+ecx*4+0x12345678]', 'mov eax, [ebx+8]', 'mov [ebx+8], eax',
          'mov ebx, ecx', 'mov bl, ch', 'mov dx, si', 'mov ebx, 0x12345678', 'mov cl, 0x81', 'mov dx, 0x8001']
    for m in ('inc', 'dec', 'neg', 'not'): T += ['%s cl' % m, '%s bx' % m, '%s ebx' % m, '%s dh' % m]
    for m in ('inc', 'dec'): R += [bytes([0xff, 0xc3 if m == 'inc' else 0xcb])]
    for m in ('shl', 'shr', 'sar', 'rol', 'ror'):
        for r in ('bl', 'cx', 'ebx', 'ah'):
            T += ['%s %s, cl' % (m, r), '%s %s, 1' % (m, r), '%s %s, 3' % (m, r), '%s %s, 0' % (m, r)]
        T += ['%s bl, 9' % m, '%s cx, 17' % m, '%s ebx, 33' % m, '%s ebx, 31' % m, '%s ecx, cl' % m]
    T += ['imul ebx, ecx', 'imul bx, cx', 'imul ebx, ecx, 0x12345', 'imul ebx, ecx, -3', 'imul bx, cx, 0x1234', 'imul bx, cx, -3',
          'imul eax, eax', 'mul ecx', 'mul cx', 'mul cl', 'mul eax', 'mul edx']
    for m, rs in (('movzx', ('ebx, cl', 'ebx, dx', 'bx, cl', 'ebx, ah')), ('movsx', ('ebx, cl', 'ebx, dx', 'bx, cl', 'ebx, ah'))): T += ['%s %s' % (m, r) for r in rs]
    cc = ['o', 'no', 'b', 'ae', 'e', 'ne', 'be', 'a', 's', 'ns', 'p', 'np', 'l', 'ge', 'le', 'g']
    for c in cc: T += ['set%s cl' % c, 'set%s dh' % c, 'cmov%s ebx, ecx' % c, 'cmov%s bx, cx' % c]
    T += ['lea ebx, [ecx+edx*4+0x12345678]', 'lea ebx, [ecx+8]', 'lea ebx, [ecx+edx]', 'lea ebx, [edx*8+0x100]', 'lea ebx, [ecx+edx*2-4]',
          'lea bx, [ecx+edx*4+0x12345678]', 'lea eax, [eax+eax*2]', 'lea ebx, [esp+0x10]', 'lea ebx, [ebp-4]',
          'bswap ebx', 'bswap eax', 'cdq', 'xchg ebx, ecx', 'xchg bl, cl', 'xchg bx, cx', 'xchg eax, ecx', 'xchg ebx, ebx', 'xchg al, ah']
    for r in ('eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'): T += ['push ' + r, 'pop ' + r]
    return [asm(t) for t in T] + R
