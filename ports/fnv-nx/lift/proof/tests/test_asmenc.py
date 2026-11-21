#!/usr/bin/env python3
"""asl / asmenc / ctasm: the A64 instruction semantics of the asm templates are the pinned Arm A64 ISA XML's ASL (asl.py), the encoding chosen by asmenc.py.
Without FNVNX_A64_ISA_TGZ (the pinned ISA_A64_xml_A_profile-2023-09.tar.gz, sha256 checked on load) the tests SKIP with that reason.
The evaluator is checked against independently stated values at every operand width asmenc uses; the definition of an instruction stays the XML's text."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))
import os, sys, unittest
import z3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..')]
import asl, asmenc   # noqa: E402

try:
    asl.xml('movz.xml'); NOINPUT = None
except asl.NoInput as e:
    NOINPUT = str(e)


def state(**regs):
    s = asl.State(); s.mem = z3.Array('m', z3.BitVecSort(64), z3.BitVecSort(8)); s.pc = z3.BitVecVal(0x1000, 64); s.nextpc = z3.BitVecVal(0x1004, 64)
    for k, v in regs.items(): s.R[int(k[1:])] = v
    return s


def run(mn, ops, st, pc=0x1000):
    f, e, fl = asmenc.encode(mn, ops, pc, {})
    return asl.run_encoding(f, e, fl, st)


def val(e): return z3.simplify(e).as_long()


@unittest.skipIf(NOINPUT, NOINPUT or '')
class Rows(unittest.TestCase):
    def test_every_cited_file_and_encoding_exists(self):
        shapes = {'mov': [('reg', 'w', 0), ('reg', 'w', 1)], 'movz': [('reg', 'w', 0), ('imm', 1), ('imm', 16)], 'movk': [('reg', 'x', 0), ('imm', 1), ('imm', 48)], 'tst': [('reg', 'w', 0), ('imm', 7)],
                  'b': [('label', 0x1008)], 'b.ne': [('label', 0x1008)], 'bfxil': [('reg', 'w', 0), ('reg', 'w', 1), ('imm', 0), ('imm', 16)], 'dmb': [('opt', 'ishld')]}
        for mn in asmenc.SUPPORTED:
            for w in 'wx':
                ops = shapes.get(mn) or [('reg', w, 0), ('mem', 1)]
                if mn in ('ldarb', 'ldarh', 'ldrb', 'ldrh', 'stlrb', 'stlrh', 'strb', 'strh') and w == 'x': continue
                f, e, fl = asmenc.encode(mn, ops, 0x1000, {})
                ic, en, dec, ex = asl.encoding(f, e)
                self.assertTrue(dec.strip() or ex.strip(), mn)
                for k in fl: self.assertIn(k, ic['boxes'], '%s: field %s' % (mn, k))
                st = state(x0=z3.BitVec('a', 64), x1=z3.BitVec('b', 64)); st.cond = z3.BoolVal(True)
                asl.run_encoding(f, e, fl, st)

    def test_aliases_are_the_documented_ones(self):
        for alias, base in (('mov_orr_log_shift.xml', 'ORR'), ('tst_ands_log_imm.xml', 'ANDS'), ('tst_ands_log_shift.xml', 'ANDS'), ('bfxil_bfm.xml', 'BFM')):
            self.assertIn(base, asl.text(asl.xml(alias)[:4000]), alias)

    def test_values(self):
        a, b = z3.BitVec('a', 64), z3.BitVec('b', 64)
        r = run('movz', [('reg', 'w', 0), ('imm', 0x1234), ('imm', 16)], state(x0=a)); self.assertEqual(val(r.R[0]), 0x12340000)
        r = run('movz', [('reg', 'x', 0), ('imm', 0x1234), ('imm', 48)], state(x0=a)); self.assertEqual(val(r.R[0]), 0x1234 << 48)
        r = run('movk', [('reg', 'w', 0), ('imm', 0xabcd), ('imm', 16)], state(x0=z3.BitVecVal(0xffff00001234, 64))); self.assertEqual(val(r.R[0]), 0xabcd1234)
        r = run('mov', [('reg', 'w', 0), ('reg', 'w', 1)], state(x0=a, x1=z3.BitVecVal(0x1122334455667788, 64))); self.assertEqual(val(r.R[0]), 0x55667788)
        r = run('bfxil', [('reg', 'w', 0), ('reg', 'w', 1), ('imm', 8), ('imm', 8)], state(x0=z3.BitVecVal(0xaabbccdd, 64), x1=z3.BitVecVal(0x11223344, 64))); self.assertEqual(val(r.R[0]), 0xaabbcc33)
        for mask in (1, 3, 7, 0xff, 0xf0f0f0f0, 0x80000000):
            r = run('tst', [('reg', 'w', 0), ('imm', mask)], state(x0=z3.BitVecVal(0x10, 64)))
            self.assertEqual(z3.simplify(r.nzcv['Z'].e).as_long(), 1 if (0x10 & mask) == 0 else 0, hex(mask))
        r = run('tst', [('reg', 'w', 0), ('reg', 'w', 1)], state(x0=z3.BitVecVal(0x10, 64), x1=z3.BitVecVal(0x01, 64))); self.assertEqual(z3.simplify(r.nzcv['Z'].e).as_long(), 1)

    def test_memory_widths(self):
        m = z3.Array('m', z3.BitVecSort(64), z3.BitVecSort(8)); addr = z3.BitVecVal(0x100, 64)
        for i in range(8): m = z3.Store(m, addr + i, z3.BitVecVal(0x10 + i, 8))
        for mn, w, nb in (('ldar', 'w', 4), ('ldar', 'x', 8), ('ldarb', 'w', 1), ('ldarh', 'w', 2), ('ldr', 'w', 4), ('ldr', 'x', 8), ('ldrb', 'w', 1), ('ldrh', 'w', 2)):
            st = state(x0=z3.BitVecVal(-1 & (2 ** 64 - 1), 64), x1=addr); st.mem = m; st.cond = z3.BoolVal(True)
            r = run(mn, [('reg', w, 0), ('mem', 1)], st)
            self.assertEqual(val(r.R[0]), int.from_bytes(bytes(0x10 + i for i in range(nb)), 'little'), mn + w)
        for mn, w, nb in (('stlr', 'w', 4), ('stlr', 'x', 8), ('stlrb', 'w', 1), ('stlrh', 'w', 2), ('str', 'w', 4), ('str', 'x', 8), ('strb', 'w', 1), ('strh', 'w', 2)):
            st = state(x0=z3.BitVecVal(0x8877665544332211, 64), x1=addr); st.mem = z3.Array('z', z3.BitVecSort(64), z3.BitVecSort(8)); st.cond = z3.BoolVal(True)
            r = run(mn, [('reg', w, 0), ('mem', 1)], st)
            for i in range(nb): self.assertEqual(val(z3.Select(r.mem, addr + i)), 0x11 * (i + 1) if i < 8 else 0, mn + w)
            self.assertEqual(len(r.ub), 1 if mn.startswith('stlr') and nb > 1 else 0, mn)     # an ordered access of nb > 1 bytes: the alignment obligation

    def test_branch_and_condition(self):
        st = state(x0=z3.BitVec('a', 64)); st.cond = z3.BoolVal(True)
        st = run('tst', [('reg', 'w', 0), ('imm', 3)], st)
        r = run('b.ne', [('label', 0x1010)], st)
        taken = z3.simplify(r.nextpc == 0x1010)
        s = z3.Solver(); s.add(taken != (z3.Extract(1, 0, z3.Extract(31, 0, st.R[0])) != 0)); self.assertEqual(s.check(), z3.unsat)
        r = run('b', [('label', 0x1008)], state()); self.assertEqual(val(r.nextpc), 0x1008)

    def test_dmb_option_crm_from_the_file(self):
        self.assertEqual((asmenc.dmb_crm('ish'), asmenc.dmb_crm('ishld'), asmenc.dmb_crm('ishst')), (0b1011, 0b1001, 0b1010))

    def test_mnemonic_set_equals_the_closure(self):
        """asmenc.SUPPORTED = the mnemonics of the asm templates of the closure (ctasm.mnemonics() after the closure's statements are converted); FNVNX_PP and the emission inputs are needed"""
        try:
            import x87exec
            x87exec.Ctx(bytes.fromhex('d802'), 0)       # the accessor classes
            x87exec.Ctx(bytes.fromhex('dfe0'), 0)       # fnstsw ax: FSW_AX
        except Exception as e:
            self.skipTest('closure inputs: %s' % str(e)[:100])
        ca = [m for n, m in sys.modules.items() if n.endswith('ctasm') and hasattr(m, 'ASMS')][0]
        self.assertEqual(ca.mnemonics(), set(asmenc.SUPPORTED))


if __name__ == '__main__':
    unittest.main()
