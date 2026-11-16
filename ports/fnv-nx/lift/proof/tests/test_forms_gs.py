"""an access through GS follows the record's null_seg_access row (x86sys.NULL_SEG): a fault at the instruction while the GS selector is null; lea has no access; the initial gs is read from the record."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
declared_x86sys.configure()
from fnvnx.lift.fnvlift import dsl, faults, inputs, x86sys
from fnvnx.lift.proof import symform


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


class CodeStoreSpec(unittest.TestCase):
    def setUp(self): declared_x86sys.configure()
    def tearDown(self): inputs.reset()

    def test_prove_codestore_both_halves_and_a_mutated_form(self):
        import copy
        import sysspec_gap
        import sysspec_store
        rec = x86sys.REC
        for h in ('8900', '2e8900', '648900'):
            self.assertEqual(sysspec_store.prove_codestore(form(h), sysspec_gap.decode(h + '/0.0.0.0'), rec), 'proved', h)
        r2 = copy.deepcopy(rec); r2['gdt']['descriptors']['0x20']['type'] = 10      # the data descriptor made code: the Form (built from the real record) no longer matches
        self.assertEqual(sysspec_store.prove_codestore(form('8900'), sysspec_gap.decode('8900/0.0.0.0'), r2), 'differs')

    def test_a_missing_segment_is_reported(self):
        import sysspec_gap
        import sysspec_store
        f = form('8b00')       # a load: no code-store fault
        self.assertEqual(sysspec_store.prove_codestore(f, sysspec_gap.decode('8900/0.0.0.0')), 'no code-segment store fault')


if __name__ == '__main__':
    unittest.main()
