#!/usr/bin/env python3
"""Tests of tools/ehflow.py (callee-saved proofs, R2) on real x86-32 code: the machine code below is the assembly in tools/tests/ehflow_test.asm, decoded by capstone like the images' code.
No double of a product symbol: Flow is the product class, only the image container (decoded instructions, no relocations) is provided here.   python3 test/ehflow_test.py"""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools'))
import capstone
from capstone import x86 as cx
from ehflow import Flow, CALLEE
CODE = {
    0x1000: '680020000064ff35000000008b442410896c24108d6c241029c4535657a1003000003145fc31e8508965e8ff75f88b45fcc745fcfeffffff8945f88d45f064a300000000c3',   # prolog
    0x1100: '8b4df064890d00000000595f5f5e5b89ec5d51c3',   # epilog
    0x1200: 'c3',   # g
    0x1300: '6a106800400000e8f4fcffffe8effeffffe8eafdffffc3',   # fa
    0x1400: '6a106800400000e8f4fbffff8d45d450e8ebfdffff59e8e5fcffffc3',   # fb
    0x1500: '6a106800400000e8f4faffffc745d405000000e8e8fbffffc3',   # fc
    0x1600: '53e8fafbffff5bc3',   # fd
    0x1700: '5354e8f9faffff595bc3',   # fe
    0x1800: '53e8fa0000005bc3',   # ff
    0x1900: '31dbc3',   # gb
}
SYM = {'prolog': 4096, 'epilog': 4352, 'g': 4608, 'fa': 4864, 'fb': 5120, 'fc': 5376, 'fd': 5632, 'fe': 5888, 'ff': 6144, 'gb': 6400}

class Img:
    """the decoded image: insns[a] = (size, mnemonic, 0, falls through, direct jmp/jcc targets); a call is no edge"""
    def __init__(s):
        s.mem = {}
        for a, h in CODE.items():
            for i, v in enumerate(bytes.fromhex(h)): s.mem[a + i] = v
        s.imports = {}; s.insns = {}
        s.cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); s.cs.detail = True
        for a, h in CODE.items():
            x = a
            for ins in s.cs.disasm(bytes.fromhex(h), a):
                mn = ins.mnemonic; tg = ()
                if mn.startswith('j') and ins.operands and ins.operands[0].type == cx.X86_OP_IMM: tg = (ins.operands[0].imm,)
                s.insns[ins.address] = (ins.size, mn, 0, mn not in ('ret', 'jmp'), tg)

class F(Flow):
    def __init__(s):
        s.X = Img(); s.flow_init(); s.cs = s.X.cs
    def dis(s, a):
        sz = s.X.insns[a][0] if a in s.X.insns else None
        if sz is None: return None
        r = list(s.cs.disasm(bytes(s.X.mem[a + i] for i in range(sz)), a)); return r[0] if r else None
    def extent(s, n):
        out = set(); st = [n]
        while st:
            x = st.pop()
            if x in out or x not in s.X.insns: continue
            out.add(x); sz, mn, ft, fall, tg = s.X.insns[x]; st.extend(tg)
            if fall: st.append(x + sz)
        return out

class T(unittest.TestCase):
    def setUp(s): s.f = F()
    def pres(s, name): return set(s.f.summary(SYM[name])[0])
    def test_leaf_preserves_all(s): s.assertEqual(s.pres('g'), set(CALLEE))
    def test_writer_does_not_preserve(s): s.assertEqual(s.pres('gb'), {'esi', 'edi', 'ebp'})
    def test_prolog_epilog_pair_proven(s):
        r = s.f.summary(SYM['fa']); s.assertEqual((set(r[0]), r[1]), (set(CALLEE), 0))      # the save slots persist across the call between the helpers; the frame is torn down balanced
    def test_save_slot_address_passed_to_callee_not_proven(s): s.assertEqual(s.pres('fb'), {'ebx', 'esi', 'ebp'})     # (3): the address of the edi save slot is formed; only that slot is lost
    def test_store_overlapping_save_slot_not_proven(s): s.assertEqual(s.pres('fc'), {'ebx', 'esi', 'ebp'})            # (1): a constant-offset store replaces the slot value
    def test_push_pop_around_call_proven(s): s.assertEqual(s.pres('fd'), set(CALLEE))
    def test_push_pop_with_escaping_slot_address_not_proven(s): s.assertEqual(s.pres('fe'), {'esi', 'edi', 'ebp'})
    def test_callee_that_clobbers_is_not_masked_by_push_pop_of_it(s): s.assertEqual(s.pres('ff'), set(CALLEE) - set())      # ff: push ebx / call gb / pop ebx: restored from the slot, proven
if __name__ == '__main__': unittest.main()
