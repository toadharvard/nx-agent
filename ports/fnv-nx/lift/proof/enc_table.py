"""Concrete encodings of the W8 slice: list of (hex bytes).  Assembled with keystone (PyPI) from the texts below, plus raw
hex for the direction-reversed ALU forms.  prove.py decodes every entry with capstone and takes the form key from it."""
from keystone import Ks, KS_ARCH_X86, KS_MODE_32
_ks = Ks(KS_ARCH_X86, KS_MODE_32)
def asm(t): return bytes(_ks.asm(t)[0])
ALU = ['add', 'adc', 'sub', 'sbb', 'cmp', 'and', 'or', 'xor', 'test']
BASE = {'add': 0x00, 'or': 0x08, 'adc': 0x10, 'sbb': 0x18, 'and': 0x20, 'sub': 0x28, 'xor': 0x30, 'cmp': 0x38}
EXTRA_ASM = ['pushal', 'popal', 'pushfd', 'popfd', 'lahf', 'sahf', 'std', 'cld', 'cmc', 'clc', 'stc', 'cwd', 'cbw', 'cwde', 'xlatb', 'leave', 'nop', 'int3', 'ud2',
    'enter 8, 0', 'ret', 'ret 8', 'push 5', 'push -2', 'push 0x12345678', 'push dword ptr [ebx+ecx*4+8]', 'pop dword ptr [ebx+ecx*4+8]', 'pop dword ptr [esp+8]',
    'jmp ebx', 'jmp dword ptr [ebx+8]', 'call ebx', 'call dword ptr [ebx+8]', 'call dword ptr [esp+4]',
    'bt eax, ebx', 'bts eax, ebx', 'btr eax, ebx', 'btc eax, ebx', 'bt ax, bx', 'bts ax, bx', 'bt eax, 5', 'bts eax, 31', 'btr eax, 0', 'btc eax, 17',
    'bt dword ptr [ebx+8], ecx', 'bts dword ptr [ebx+8], ecx', 'btr dword ptr [ebx+8], ecx', 'btc dword ptr [ebx+8], ecx', 'bt dword ptr [ebx+8], 33', 'btc dword ptr [ebx+8], 7',
    'bt word ptr [ebx+8], cx', 'bts word ptr [ebx+8], cx',
    'bsf eax, ebx', 'bsr eax, ebx', 'bsf eax, dword ptr [ebx+4]', 'bsr eax, dword ptr [ebx+4]', 'bsf ax, bx', 'bsr ax, bx',
    'shld eax, ebx, cl', 'shld eax, ebx, 5', 'shld eax, ebx, 1', 'shrd eax, ebx, cl', 'shrd eax, ebx, 5', 'shrd eax, ebx, 1',
    'shld dword ptr [ebx+8], ecx, 3', 'shrd dword ptr [ebx+8], ecx, cl',
    'xadd bl, cl', 'xadd ebx, ecx', 'xadd dword ptr [ebx+8], ecx', 'xadd ebx, ebx', 'cmpxchg bl, cl', 'cmpxchg bx, cx', 'cmpxchg ebx, ecx', 'cmpxchg dword ptr [ebx+8], ecx',
    'imul bl', 'imul bx', 'imul ebx', 'imul dword ptr [ebx+8]', 'imul byte ptr [ebx+8]', 'imul word ptr [ebx+8]', 'mul bl', 'mul bx', 'mul ebx', 'mul dword ptr [ebx+8]',
    'div bl', 'div bx', 'div ebx', 'div dword ptr [ebx+8]', 'idiv bl', 'idiv bx', 'idiv ebx', 'idiv dword ptr [ebx+8]', 'div eax', 'idiv edx',
    'imul ebx, dword ptr [ecx+8]', 'imul ebx, dword ptr [ecx+8], 7', 'imul bx, word ptr [ecx+8]',
    'rcl bl, 1', 'rcl bl, cl', 'rcl bl, 3', 'rcl bx, cl', 'rcl bx, 5', 'rcl ebx, 1', 'rcl ebx, cl', 'rcl ebx, 3', 'rcr bl, 1', 'rcr bl, cl', 'rcr bx, 1', 'rcr bx, cl',
    'rcr ebx, 1', 'rcr ebx, cl', 'rcr ebx, 7', 'rcl dword ptr [ebx+8], 1', 'rcr dword ptr [ebx+8], cl',
    'mov eax, fs:[0x30]', 'mov fs:[0x30], eax', 'mov eax, fs:[ebx+4]', 'add eax, fs:[ebx+4]', 'add fs:[ebx+4], eax', 'cmp dword ptr fs:[0x18], 0',
    'adc [ebx+8], eax', 'sub [ebx+8], ecx', 'sbb ecx, [ebx+8]', 'and [ebx+8], cl', 'or word ptr [ebx+8], cx', 'xor eax, [ebx+8]', 'cmp [ebx+8], eax', 'test [ebx+8], eax',
    'add dword ptr [ebx+8], 5', 'add byte ptr [ebx+8], 5', 'add word ptr [ebx+8], 5', 'sub dword ptr [ebx+8], 0x12345', 'cmp byte ptr [ebx+8], 0x80', 'test byte ptr [ebx+8], 1',
    'inc dword ptr [ebx+8]', 'dec byte ptr [ebx+8]', 'inc word ptr [ebx+8]', 'neg dword ptr [ebx+8]', 'not byte ptr [ebx+8]',
    'shl dword ptr [ebx+8], 3', 'shr word ptr [ebx+8], cl', 'sar byte ptr [ebx+8], 1', 'rol dword ptr [ebx+8], cl', 'ror dword ptr [ebx+8], 5',
    'movzx ebx, byte ptr [ecx+8]', 'movzx ebx, word ptr [ecx+8]', 'movsx ebx, byte ptr [ecx+8]', 'movsx ebx, word ptr [ecx+8]', 'movsx bx, byte ptr [ecx+8]',
    'cmovne ebx, dword ptr [ecx+8]', 'cmovl bx, word ptr [ecx+8]', 'sete byte ptr [ebx+8]', 'mov dword ptr [ebx+8], 0x12345678', 'mov byte ptr [ebx+8], 0x80', 'mov word ptr [ebx+8], cx',
    'mov [ebx+ecx*4+0x12345678], eax', 'xchg [ebx+8], ecx', 'xchg bl, [ebx+8]']
EXTRA_HEX = ['e2fe', 'e2fb', 'e1fe', 'e0fe', 'e3fe', 'e3f0', 'e905000000', 'eb05', 'eb80', 'e8fb104000', 'e800000000', '0f0b', 'c8080000']
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
    T += EXTRA_ASM
    for p_ in ('f3', 'f2', ''):
        for o in (0xa4, 0xa5, 0xa6, 0xa7, 0xaa, 0xab, 0xac, 0xad, 0xae, 0xaf):
            if p_ == 'f2' and o in (0xa4, 0xa5, 0xaa, 0xab, 0xac, 0xad): continue           # repne is only meaningful for cmps/scas
            R.append(bytes.fromhex(p_) + bytes([o])); R.append(bytes.fromhex(p_) + b'\x66' + bytes([o]))
    for cc in range(16): R += [bytes([0x70 + cc, 0x05]), bytes([0x0f, 0x80 + cc, 0x0a, 0, 0, 0]), bytes([0x70 + cc, 0xf0])]       # jcc taken-target forward / backward
    R += [bytes.fromhex(h) for h in EXTRA_HEX]
    return [asm(t) for t in T] + R
