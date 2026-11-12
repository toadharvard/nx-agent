#!/usr/bin/env python3
"""Tests of tools/jtab.py (jump-table edges, one interval rule) on real x86-32 code assembled from test/jtab_test.asm and decoded by capstone, including the CRT memcpy/memmove tails
(`and edx, 3` / `jmp [edx*4+T]`, `sub ecx, 4; jb` wrapping into a table placed before T, `neg ecx`). Relocations = the displacement fields and the table words the assembly names (an image's
.reloc lists exactly these).   python3 test/jtab_test.py"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import capstone
from capstone import x86 as X
import jtab
MEM = {}
for base, h in ((0x1000, '83f8030f8717010000ff2485001400009090909090909090909090909090909083f8040f83f7000000ff248500140000909090909090909090909090909090908b442404ff24850014000090909090909090909090909090909090909090909083f8030f87b7000000ff24850014000090909090909090909090909090909090ebe790909090909090909090909090909090909090909090909090909090909083f805777b0fb68800150000ff248d001400009090909090909090909090909083f804775bff248500140000909090909090909090909090909090909090909083e003ff2485001400009090909090909090909090909090909090909090909083f803761bff2485001400009090909090909090909090909090909090909090c39090909090909090909090909090909090909090909090909090909090909089ca83e203c1e90283f90872d3f3a5ff24950017000090909090909090909090909090909090909090909090909090909090909090909090909090909090909083e9047201c3ff248d2017000090909090909090909090909090909090909090909090909090909090909090909090909090909090909090909090909090909083f9087201c3f7d9ff248d4c1700009090909090909090909090909090909090909090909090909090909090909090909090909090909090909090909090909083e9047201c3ff248d70170000909090909090909090909090909090909090909090909090909090909090909090909090909090909090909090909090909090ba0300000083e9040f82d2feffff21caff249500170000909090909090909090909090909090909090909090909090909090909090909090909090909090909083f9017507b801000000eb05b802000000ff2485001700009090909090909090909090909090909090909090909090909090909090909090909090909090909031c94183f96472faff248d001700009090909090909090909090909090909090'), (0x1400, '0016000001160000021600000316000078563412'), (0x1500, '000102030300'), (0x1600, 'c3c3c3c3'), (0x1700, '00160000011600000216000003160000001600000116000002160000031600009090909090909090909090909090909000160000011600000216000003160000001600000116000002160000031600009090909090909090909090909090909000160000011600007777777703160000')):
    for i, v in enumerate(bytes.fromhex(h)): MEM[base + i] = v
SW = dict(swa=0x1000, swb=0x1020, swc=0x1040, swd=0x1060, ext=0x1080, swe=0x10a0, swf=0x10c0, swg=0x10e0, swh=0x1100, dflt=0x1120,
          m1=0x1140, m2=0x1180, m3=0x11c0, m4=0x1200, m5=0x1240, m6=0x1280, m7=0x12c0)
CODE = (0x1000, 0x12e0)
C = (0x1600, 0x1601, 0x1602, 0x1603)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
def u32(a): return int.from_bytes(bytes(MEM[a + i] for i in range(4)), 'little')
def u8(a): return MEM.get(a)
def ins_at(a):
    if a not in MEM: return None
    return next(md.disasm(bytes(MEM.get(a + i, 0x90) for i in range(15)), a, 1), None)
DEC = {}; JM = []; RSET = {0x1400 + 4 * k for k in range(4)} | {0x1700 + 4 * k for k in range(8)} | {0x1730 + 4 * k for k in range(8)} | {0x1760, 0x1764, 0x176c}   # 0x1410, 0x1768: no relocated dword
work = list(SW.values())
while work:
    a = work.pop()
    while a not in DEC and CODE[0] <= a < CODE[1]:
        i = ins_at(a); mn = i.mnemonic
        if mn == 'nop': break
        tg = (i.operands[0].imm,) if mn.startswith('j') and i.operands[0].type == X.X86_OP_IMM else ()
        DEC[a] = (i.size, mn.split()[-1] if mn.startswith('rep') else mn, 0, mn not in ('jmp', 'ret'), tg)
        if i.operands and i.operands[0].type == X.X86_OP_MEM and i.operands[0].mem.disp: RSET.add(a + i.disp_offset)
        if mn == 'movzx': RSET.add(a + i.disp_offset)
        if mn == 'jmp' and i.operands[0].type == X.X86_OP_MEM: JM.append(a)
        work.extend(tg)
        if mn in ('jmp', 'ret'): break
        a += i.size
PM = {}
for a, (sz, mn, ft, fall, tg) in DEC.items():
    for t in tg: PM.setdefault(t, set()).add(a)
    if fall: PM.setdefault(a + sz, set()).add(a)
SEEDS = set(SW.values()) | set(C)
def site_of(name): return next(a for a in sorted(JM) if SW[name] <= a < SW[name] + 0x40 and (name != 'swa' or a < 0x1020))
def res(name):
    a = site_of(name); return jtab.resolve(a, DEC, ins_at, lambda x: PM.get(x, ()), lambda x: x in SEEDS, RSET, u32, u8, lambda v: CODE[0] <= v < CODE[1] or 0x1600 <= v < 0x1604, 0x1000)

class T(unittest.TestCase):
    def test_cmp_ja(s): s.assertEqual(res('swa'), (C, None))                       # r <= 3
    def test_cmp_jae(s): s.assertEqual(res('swb'), (C, None))                      # r < 4
    def test_and_mask(s): s.assertEqual(res('swg'), (C, None))                     # r & 3 <= 3
    def test_byte_index_table(s): s.assertEqual(res('swe'), (C, None))             # bytes 0 1 2 3 3 0 over r <= 5: [0, 3]
    def test_no_guard_red(s): s.assertIsNone(res('swc')[0]); s.assertIn('unbounded', res('swc')[1])
    def test_seed_in_chain_is_top_red(s): s.assertIsNone(res('swd')[0])           # ext jumps into the chain between guard and jump: a second predecessor, the entry is top
    def test_unrelocated_table_word_red(s): s.assertIn('not a relocated dword', res('swf')[1])      # r <= 4: the fifth word is no relocated dword
    def test_upper_range_red(s): s.assertIsNone(res('swh')[0])                    # jbe falls with r > 3: no upper bound
    def test_memcpy_and_mask_tail(s): s.assertEqual(res('m1'), (C, None))         # and edx, 3; shr; cmp; jb; rep movsd; jmp [edx*4+T]
    def test_sub_jb_wraps_below_T(s): s.assertEqual(res('m2'), (C, None))         # ecx - 4 in [-4, -1] on the carry edge: words T-16..T-4
    def test_neg_below_T(s): s.assertEqual(res('m3'), (C * 2, None))              # ecx in [0, 7] -> neg: [-7, 0]: 8 words ending at T
    def test_wrap_extent_with_unrelocated_word_red(s): s.assertIn('not a relocated dword', res('m4')[1])
    def test_and_register_with_constant(s): s.assertEqual(res('m5'), (C, None))   # edx = 3; edx & ecx <= 3
    def test_join_of_two_paths(s): s.assertEqual(res('m6'), ((0x1601, 0x1602), None))      # eax = 1 on one path, 2 on the other: [1, 2]
    def test_loop_widened_red(s): s.assertIsNone(res('m7')[0])                     # inc in a loop: the interval grows, widening makes it top
if __name__ == '__main__': unittest.main()
