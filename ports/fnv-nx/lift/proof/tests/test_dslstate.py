"""Every free variable of a Form is a declared DSL state input (dsl.V is the one constructor; rule 8 of the 2026-11-15 order), and the string spec claims by the decoder's opcode and prefixes."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
from fnvlift import dsl, inputs
from proof import stringspec, symform


class State(unittest.TestCase):
    def setUp(self): declared_x86sys.configure()
    def tearDown(self): inputs.reset()

    def test_every_declared_input_is_accepted_at_its_width(self):
        for n in dsl.REGS + ('eip',) + tuple(dsl.XW) + tuple(dsl.XIN) + dsl.FLAGS: self.assertIsNotNone(dsl.state_width(n), n)
        self.assertIsNone(dsl.state_width('ees'))

    def test_a_form_with_an_undeclared_free_variable_raises(self):
        f = dsl.Form('k', 0, 1); f.reg('eax', dsl.V('eax', 32)); f.check_state()
        f.reg('ebx', dsl.V('ees', 32)); self.assertRaises(AssertionError, f.check_state)
        g = dsl.Form('k', 0, 1); g.flag('cf', dsl.V('tmp', 1)); self.assertRaises(AssertionError, g.check_state)
        

    def test_a_register_write_names_a_gpr(self):
        f = dsl.Form('k', 0, 1); self.assertRaises(AssertionError, f.reg, 'ees', dsl.C(0, 32))

    def test_a_segment_register_destination_without_a_decided_row_has_no_form(self):
        for k in ('8e00/0.0.0.0', '6607/0.0.0.0'): self.assertRaises(dsl.UndecidedRow, symform.symbuild, k)      # es: mov_to_sreg.undecided
        self.assertEqual([x[:2] for x in symform.symbuild('8ed0/0.0.0.0').form.xs], [('sel', 2)])

    def test_mov_from_sreg_reads_the_selector_input(self):
        f = symform.symbuild('8c0424/0.0.0.0').form
        names = set()
        def walk(n):
            if n.op == 'var': names.add(n.x)
            for a in n.a: walk(a)
        for st in f.stores:
            for x in st: walk(x)
        self.assertIn('sel_es', names)


class StringClaim(unittest.TestCase):
    def setUp(self): declared_x86sys.configure()
    def tearDown(self): inputs.reset()

    def test_a_modrm_byte_equal_to_a_prefix_value_is_not_a_prefix(self):
        r, n = stringspec.check('f20f106500/0.0.2.1'); self.assertEqual(r, 'not-covered')       # SSE movsd: another instruction, ModRM 0x65 is not gs
        self.assertIsNone(stringspec.decode('f20f10c1/0.0.0.0'))

    def test_the_decoders_prefixes_decide(self):
        for k in ('65a4/0.0.0.0', '67a4/0.0.0.0', '36a5/0.0.0.0'): self.assertEqual(stringspec.check(k)[0], 'uncertified', k)
        for k in ('a4/0.0.0.0', 'f3a4/0.0.0.0', 'f3aa/0.0.0.0'): self.assertEqual(stringspec.check(k)[0], 'proved', k)


if __name__ == '__main__':
    unittest.main()
