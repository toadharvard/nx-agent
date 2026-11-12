#!/usr/bin/env python3
"""Tests of tools/idxrange.py: the interval of the index of an indexed store, across calls (join over the call sites), unknown where the code does not bound it.   python3 test/idxrange_test.py"""
import os, struct, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import capstone
import idxrange
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
F = 0x1000
def rel(frm, to): return struct.pack('<i', to - (frm + 5))
FUNC = b'\x55\x8b\xec\x8b\x45\x08\xc7\x04\x85\x00\x20\x00\x00\x01\x00\x00\x00\x5d\xc3'          # push ebp; mov ebp,esp; mov eax,[ebp+8]; mov dword [eax*4+0x2000],1; pop ebp; ret
STORE = F + 6
def build(callers_code, pure=None, ret_guard=False):
    mem = {}
    for i, b in enumerate(FUNC): mem[F + i] = b
    at = 0x1100; csites = []; fn = {F: F}
    for blk in callers_code:
        start = at; code = b''
        for piece in blk:
            if piece == 'call': csites.append(at + len(code)); code += b'\xe8' + rel(at + len(code), F)
            else: code += piece
        code += b'\xc3'
        for i, b in enumerate(code): mem[at + i] = b
        fn[start] = start; at += 0x40
    dec = {}; work = [F] + [a for a in range(0x1100, at, 0x40)]
    def ins_at(a): return next(md.disasm(bytes(mem.get(a + i, 0xcc) for i in range(15)), a, 1), None) if a in mem else None
    for w in work:
        a = w
        while a in mem and a not in dec:
            i = ins_at(a); mn = i.mnemonic
            tg = (i.operands[0].imm,) if mn.startswith('j') and i.operands[0].type == 2 else ()
            dec[a] = (i.size, mn, 0, mn not in ('jmp', 'ret'), tg)
            if mn in ('jmp', 'ret'): break
            a += i.size
    pm = {}
    for a, (sz, mn, ft, fall, tg) in dec.items():
        for t in tg: pm.setdefault(t, set()).add(a)
        if fall: pm.setdefault(a + sz, set()).add(a)
    def entry_of(a): return F if a < 0x1100 else 0x1100 + (a - 0x1100) // 0x40 * 0x40
    rg = idxrange.Ranger(dec, ins_at, lambda x: pm.get(x, ()), lambda e: csites if e == F else (), entry_of,
                         lambda e: pure if e == F else 'caller entry', lambda x: x in (0x1100 + 0x40 * k for k in range(8)), lambda a: None)
    return rg
PUSH = lambda v: b'\x6a' + bytes([v])
ADD4 = b'\x83\xc4\x04'
class T(unittest.TestCase):
    def test_join_over_call_sites(s):
        rg = build([[PUSH(3), 'call', ADD4, PUSH(5), 'call', ADD4]])
        lo, hi, src, why = rg.index_range(STORE, 'eax'); s.assertEqual((lo, hi), (3, 5)); s.assertEqual(src, 'param#0@%x' % F)
    def test_unknown_argument_stays_unknown(s):
        rg = build([[PUSH(3), 'call', ADD4], [b'\x51', 'call', ADD4]])            # push ecx: no bound
        lo, hi, src, why = rg.index_range(STORE, 'eax'); s.assertIsNone(lo); s.assertIn('unknown', why)
    def test_address_taken_entry_unknown(s):
        rg = build([[PUSH(3), 'call', ADD4]], pure='a relocated reference names it (address taken)')
        lo, hi, src, why = rg.index_range(STORE, 'eax'); s.assertIsNone(lo)
    def test_no_call_site_unknown(s):
        rg = build([]); lo, hi, src, why = rg.index_range(STORE, 'eax'); s.assertIsNone(lo)
if __name__ == '__main__': unittest.main()
