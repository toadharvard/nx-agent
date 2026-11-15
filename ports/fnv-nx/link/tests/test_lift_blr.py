#!/usr/bin/env python3
"""Check of link/lift_blr.py (gate disp, check `lift-blr`) on hand-assembled AArch64 ELF32 objects (no compiler, no game data):
the dispatch sequence (hit path: ldar of the site record, lsr #32; miss path: bl fnvnx_dispatch; blr) is green, as is its sibling-call form (br);
red and named: a bare `blr x8` loaded from data, a `br` jump table that does not go through the dispatcher, an argument register, the result of
another call, a hit path whose ldar address has no data relocation, a function without the miss path, and an indirect branch outside any
function symbol.  Run: python3 -I <this file>."""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import lift_blr

fails = []


def check(name, ok, detail=''):
    print('%s %s' % ('PASS' if ok else 'FAIL', name))
    if not ok:
        fails.append(name)
        print('    ', detail)


# instruction words (A64)
NOP, RET = 0xD503201F, 0xD65F03C0
BL = 0x94000000
ADRP = lambda rd: 0x90000000 | rd                       # noqa: E731  relocated
ADD_LO = lambda rd, rn: 0x91000000 | (rn << 5) | rd     # noqa: E731  relocated
LDAR = lambda rt, rn: 0xC8DFFC00 | (rn << 5) | rt       # noqa: E731
LSR32 = lambda rd, rn: 0xD360FC00 | (rn << 5) | rd      # noqa: E731
MOV = lambda rd, rn: 0xAA0003E0 | (rn << 16) | rd       # noqa: E731
LDR = lambda rt, rn: 0xF9400000 | (rn << 5) | rt        # noqa: E731
BLR = lambda rn: 0xD63F0000 | (rn << 5)                 # noqa: E731
BR = lambda rn: 0xD61F0000 | (rn << 5)                  # noqa: E731
CBZ = lambda rt, words: 0xB4000000 | ((words & 0x7ffff) << 5) | rt   # noqa: E731


def obj(funcs, extra_text=b''):
    """funcs: [(name, [(word, reloc symbol name or None)])]  -> ELF32 relocatable bytes with .text, .bss, .rela.text, .symtab"""
    text, syms, rel = b'', [('', 0, 0, 0, 0, 0)], []
    names = ['fnvnx_dispatch', 'other_fn', '.bss']
    sym_of = {}
    # symbols: 0 null, then the functions, then fnvnx_dispatch (undef), other_fn (undef), .bss section symbol
    for nm, ins in funcs:
        syms.append((nm, len(text), 4 * len(ins), 2, 1, 1))
        for w, rs in ins:
            if rs:
                rel.append((len(text), rs))
            text += struct.pack('<I', w)
    text += extra_text
    for n in ('fnvnx_dispatch', 'other_fn'):
        sym_of[n] = len(syms)
        syms.append((n, 0, 0, 0, 0, 1))
    sym_of['.bss'] = len(syms)
    syms.append(('.bss', 0, 0, 3, 2, 0))
    rela = b''.join(struct.pack('<IIi', off, (sym_of[rs] << 8) | 27, 0) for off, rs in rel)
    strt, symb = b'\0', b''
    for nm, v, sz, ty, sx, bind in syms:
        o = len(strt)
        strt += nm.encode() + b'\0' if nm else b''
        symb += struct.pack('<IIIBBH', o if nm else 0, v, sz, (bind << 4) | ty, 0, sx)
    secs = [('.text', 1, 6, text, 0, 0, 4, 0), ('.bss', 8, 3, b'', 0, 0, 8, 0), ('.rela.text', 4, 0, rela, 4, 1, 4, 12),
            ('.symtab', 2, 0, symb, 5, 1, 4, 16), ('.strtab', 3, 0, strt, 0, 0, 1, 0)]
    # section numbers: 1 .text, 2 .bss, 3 .rela.text, 4 .symtab, 5 .strtab, 6 .shstrtab ; rela link=.symtab(4), info=.text(1); symtab link=.strtab(5)
    shs = b'\0'
    nameoff = {}
    for s in secs + [('.shstrtab',)]:
        nameoff[s[0]] = len(shs)
        shs += s[0].encode() + b'\0'
    body, hdr, pos = b'', [(0,) * 10], 52
    for nm, ty, fl, data, link, info, al, es in secs:
        if nm == '.rela.text':
            link, info = 4, 1
        if nm == '.symtab':
            link, info = 5, 1
        hdr.append((nameoff[nm], ty, fl, 0, pos, len(data), link, info, al, es))
        body += data
        pos += len(data)
    hdr.append((nameoff['.shstrtab'], 3, 0, 0, pos, len(shs), 0, 0, 1, 0))
    body += shs
    pos += len(shs)
    sh = b''.join(struct.pack('<IIIIIIIIII', *h) for h in hdr)
    head = b'\x7fELF\x01\x01\x01' + b'\0' * 9 + struct.pack('<HHIIIIIHHHHHH', 1, 183, 1, 0, 0, pos, 0, 52, 0, 0, 40, len(hdr), len(hdr) - 1)
    return head + body + sh


def red_of(funcs, extra_text=b''):
    import capstone
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.detail = True
    return lift_blr.check_object('t.o', obj(funcs, extra_text), cs)


SEQ = [(ADRP(1), '.bss'), (ADD_LO(1, 1), '.bss'), (LDAR(2, 1), None), (LSR32(2, 2), None), (CBZ(9, 3), None), (BL, 'fnvnx_dispatch'),
       (MOV(2, 0), None), (BLR(2), None), (RET, None)]
u, r = red_of([('seq', SEQ)])
check('dispatch sequence (hit: ldar+lsr, miss: bl fnvnx_dispatch): green, 1 site', (u, r) == (1, []), (u, r))
TAIL = SEQ[:7] + [(BR(2), None)]
u, r = red_of([('tail', TAIL)])
check('the sibling-call form (br) of the sequence: green', (u, r) == (1, []), (u, r))
u, r = red_of([('bare', [(ADRP(9), '.bss'), (ADD_LO(9, 9), '.bss'), (LDR(8, 9), None), (BLR(8), None), (RET, None)])])
check('a bare `blr x8` loaded from data: red lift:blr (t.o:bare+c)', (u, r) == (1, ['t.o:bare+c']), (u, r))
u, r = red_of([('jt', [(ADRP(9), '.bss'), (ADD_LO(9, 9), '.bss'), (LDR(2, 9), None), (BR(2), None), (RET, None)])])
check('a `br` jump table that does not go through dispatch: red', (u, r) == (1, ['t.o:jt+c']), (u, r))
u, r = red_of([('arg', [(BLR(8), None), (RET, None)])])
check('blr of an argument register (no definition in the function): red', (u, r) == (1, ['t.o:arg+0']), (u, r))
u, r = red_of([('oc', [(BL, 'other_fn'), (MOV(2, 0), None), (BLR(2), None), (RET, None)])])
check('blr of the result of another call: red', (u, r) == (1, ['t.o:oc+8']), (u, r))
NOREL = [(SEQ[0][0], None), (SEQ[1][0], None)] + SEQ[2:]
u, r = red_of([('norel', NOREL)])
check('hit path whose ldar address has no data relocation: red', (u, r) == (1, ['t.o:norel+1c']), (u, r))
u, r = red_of([('nomiss', [SEQ[0], SEQ[1], SEQ[2], SEQ[3], (BLR(2), None), (RET, None)])])
check('hit path in a function with no `bl fnvnx_dispatch`: red', (u, r) == (1, ['t.o:nomiss+10']), (u, r))
u, r = red_of([('seq', SEQ), ('bare', [(LDR(8, 9), None), (BLR(8), None), (RET, None)])])
check('two functions: only the bare site is red', (u, r) == (2, ['t.o:bare+4']), (u, r))
u, r = red_of([('f', [(RET, None)])], struct.pack('<II', BLR(8), RET))
check('an indirect branch outside every function symbol: red, named by section', (u, r) == (1, ['t.o:.text+4']), (u, r))
u, r = red_of([('f', [(BL, 'other_fn'), (RET, None)])])
check('direct calls and ret are not in the universe', (u, r) == (0, []), (u, r))
sys.exit(1 if fails else 0)
