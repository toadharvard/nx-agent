#!/usr/bin/env python3
"""Tests of tools/jtab.py (jump-table edges by a guard rule) on real x86-32 code assembled from test/jtab_test.asm and decoded by capstone; relocations = the displacement fields and the table words
that the assembly names (an image's .reloc would list exactly these).   python3 test/jtab_test.py"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import capstone
from capstone import x86 as X
import jtab
MEM = {}
for base, h in ((0x1000, '83f8030f8717010000ff2485001400009090909090909090909090909090909083f8040f83f7000000ff248500140000909090909090909090909090909090908b442404ff24850014000090909090909090909090909090909090909090909083f8030f87b7000000ff24850014000090909090909090909090909090909090ebe790909090909090909090909090909090909090909090909090909090909083f805777b0fb68800150000ff248d001400009090909090909090909090909083f804775bff248500140000909090909090909090909090909090909090909083e003ff2485001400009090909090909090909090909090909090909090909083f803761bff2485001400009090909090909090909090909090909090909090c390'), (0x1400, '0016000001160000021600000316000078563412'), (0x1500, '000102030300'), (0x1600, 'c3c3c3c3')):
    for i, v in enumerate(bytes.fromhex(h)): MEM[base + i] = v
SW = dict(swa=0x1000, swb=0x1020, swc=0x1040, swd=0x1060, ext=0x1080, swe=0x10a0, swf=0x10c0, swg=0x10e0, swh=0x1100, dflt=0x1120)
CODE = (0x1000, 0x1122)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
def u32(a): return int.from_bytes(bytes(MEM[a + i] for i in range(4)), 'little')
def u8(a): return MEM.get(a)
def ins_at(a):
    if a not in MEM: return None
    return next(md.disasm(bytes(MEM.get(a + i, 0x90) for i in range(15)), a, 1), None)
DEC = {}; JM = []; RSET = {0x1400 + 4 * k for k in range(4)}      # the fifth table word (0x1410) is no relocated dword
for fn in SW.values():
    a = fn
    while a < CODE[1] and a not in DEC:
        i = ins_at(a)
        if i.mnemonic == 'nop': break
        mn = i.mnemonic; tg = ()
        if mn.startswith('j') and i.operands[0].type == X.X86_OP_IMM: tg = (i.operands[0].imm,)
        DEC[a] = (i.size, mn, 0, mn not in ('jmp', 'ret'), tg)
        if i.operands and i.operands[0].type == X.X86_OP_MEM and i.operands[0].mem.disp: RSET.add(a + i.disp_offset)
        if mn == 'movzx': RSET.add(a + i.disp_offset)
        if mn == 'jmp' and i.operands[0].type == X.X86_OP_MEM: JM.append(a)
        if mn in ('jmp', 'ret'): break
        a += i.size
PM = {}
for a, (sz, mn, ft, fall, tg) in DEC.items():
    for t in tg: PM.setdefault(t, set()).add(a)
    if fall: PM.setdefault(a + sz, set()).add(a)
def pred1(x): p = PM.get(x); return next(iter(p)) if p and len(p) == 1 else None
SEEDS = set(SW.values()) | {0x1600, 0x1601, 0x1602, 0x1603}
def site_of(name): return next(a for a in JM if SW[name] <= a < SW[name] + 0x20 or (name == 'swd' and a == 0x1069))
def res(name):
    a = site_of(name); return jtab.resolve(a, ins_at(a), ins_at, pred1, lambda x: x in SEEDS, RSET, u32, u8, lambda v: CODE[0] <= v < CODE[1] or 0x1600 <= v < 0x1604, lambda v: True)
C = (0x1600, 0x1601, 0x1602, 0x1603)

class T(unittest.TestCase):
    def test_cmp_ja(s): s.assertEqual(res('swa'), (C, None))                       # r <= 3
    def test_cmp_jae(s): s.assertEqual(res('swb'), (C, None))                      # r < 4
    def test_and_mask(s): s.assertEqual(res('swg'), (C, None))                     # r & 3 <= 3
    def test_byte_index_table(s): s.assertEqual(res('swe'), (C, None))             # bytes 0 1 2 3 3 0: largest 3, N = 4
    def test_no_guard_red(s): s.assertIsNone(res('swc')[0]); s.assertIn('chain', res('swc')[1])
    def test_second_predecessor_red(s): s.assertIsNone(res('swd')[0])             # ext jumps into the chain between guard and jump
    def test_unrelocated_table_word_red(s): s.assertIn('not a relocated dword', res('swf')[1])      # N = 5, the fifth word is no relocated dword
    def test_wrong_polarity_red(s): s.assertIsNone(res('swh')[0])                 # jbe: the guard branch is no unsigned upper bound
if __name__ == '__main__': unittest.main()
