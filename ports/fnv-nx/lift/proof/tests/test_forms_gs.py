"""an access through GS follows the record's null_seg_access row (x86sys.NULL_SEG): a fault at the instruction while the GS selector is null; lea has no access; the initial gs is read from the record."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
declared_x86sys.configure()
from fnvlift import dsl, faults, inputs, x86sys
from proof import symform


def form(h): return symform.symbuild(h + '/0.0.0.0').form


class Gs(unittest.TestCase):
    def tearDown(self): inputs.reset()

    def test_a_load_and_a_store_through_gs_fault_on_the_null_selector(self):
        for h in ('658b00', '658900', '65ff30'):
            f = form(h); self.assertEqual(f.access & {'gs'}, {'gs'}, h)
            self.assertEqual(f.faults[0][0], faults.BYNAME['AV']['code'], h)

    def test_lea_and_a_non_gs_access_have_no_gs_fault(self):
        self.assertNotIn('gs', form('658d00').access)
        self.assertNotIn('gs', form('8b00').access)
        self.assertEqual(form('8b00').faults, [])

    def test_a_prefix_without_a_memory_operand_has_no_gs_fault(self):
        self.assertNotIn('gs', form('6589c8').access)

    def test_the_fault_condition_reads_the_gs_selector_state(self):
        c = form('658b00').faults[0][1]; st = [c]; names = set()
        while st:
            n = st.pop(); st.extend(n.a)
            if n.op == 'var': names.add(n.x)
        self.assertIn('sel_gs', names)

    def test_the_initial_gs_comes_from_the_record(self):
        self.assertEqual(x86sys.SEL['gs'], x86sys.REC['selectors']['gs']['value'])

    def test_a_record_without_the_row_leaves_the_form_undecided(self):
        row = x86sys.REC.pop('null_seg_access'); old = x86sys.NULL_SEG; x86sys.NULL_SEG = None
        try: self.assertRaises(dsl.UndecidedRow, symform.symbuild, '658b00/0.0.0.0')
        finally: x86sys.REC['null_seg_access'] = row; x86sys.NULL_SEG = old


class CodeStore(unittest.TestCase):
    """a store through a segment register that can hold a code selector is #GP(0) (AV), by the record's code descriptors: one rule for every register"""
    def tearDown(self): inputs.reset()

    def test_every_data_register_has_the_code_fault_and_ss_has_none(self):
        for h, sg in (('8900', 'ds'), ('268900', 'es'), ('648900', 'fs'), ('658900', 'gs'), ('2e8900', 'cs')):
            f = form(h); self.assertIn('codestore:' + sg, f.access, h)
            self.assertIn(faults.BYNAME['AV']['code'], [k for k, _ in f.faults], h)
        f = form('894500'); self.assertNotIn('codestore:ss', f.access); self.assertEqual(f.faults, [])        # [ebp+0] is SS
        f = form('50'); self.assertEqual(f.faults, [])                                                           # push: the implicit stack

    def test_loads_have_no_code_fault(self):
        self.assertEqual(form('8b00').faults, [])
        self.assertEqual(form('8d00').faults, [])

    def test_the_code_indexes_come_from_the_record(self):
        self.assertEqual(x86sys.code_indices('ds'), (3,)); self.assertEqual(x86sys.code_indices('ss'), ()); self.assertEqual(x86sys.code_indices('cs'), (3,))

    def test_a_conditional_store_has_a_conditional_fault(self):
        f = form('0f94c0'); self.assertEqual(f.faults, [])           # setz al: a register
        g = form('0f9400'); self.assertEqual(len(g.faults), 1)       # setz [eax]: the fault only when the store is made


if __name__ == '__main__':
    unittest.main()
