"""model_sail.seg_domain ties each segment register's selector to its hidden attributes (read through x86sys) and drops what the model does not decide; the Sail clause store_bytes_to_ea (model/memory_accessors.sail:100-103,
`seg_reg == 1 | seg_attrs[w] == 0`, w = bit 1 of data_segment_descriptor_attributesbits, segmentation_structures.sail:84-98) is the reference of the DSL code-segment store fault: z3 checks of the two directions of prove.py
over the domain, on the real Forms.  (No isla here: the clause is transcribed from the Sail source.)"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
declared_x86sys.configure()
import z3
from fnvlift import dsl, x86sys
from proof import model_sail, symform

IDX = {'es': 0, 'cs': 1, 'ss': 2, 'ds': 3, 'fs': 4, 'gs': 5}


def model_fault(s):
    """store_bytes_to_ea: NON-WRITABLE-SEGMENT when seg_reg == 1 or the w bit of the register's hidden attributes is 0"""
    return z3.BoolVal(True) if IDX[s] == 1 else z3.Extract(1, 1, z3.BitVec('attr_' + s, 16)) == 0


def dsl_fault(h):
    f = symform.symbuild(h + '/0.0.0.0').form
    return z3.Or(*[dsl.to_smt(c) == 1 for _, c in f.faults]) if f.faults else z3.BoolVal(False)


def unsat(*c):
    s = z3.Solver(); s.add(*c); return s.check() == z3.unsat


class Tie(unittest.TestCase):
    CASES = (('8900', 'ds'), ('268900', 'es'), ('648900', 'fs'), ('2e8900', 'cs'))

    def test_attributes_follow_the_record_descriptors(self):
        r = x86sys.REC
        self.assertEqual(model_sail.seg_attrs_of(r, 0x23) & 2, 2)      # data W
        self.assertEqual(model_sail.seg_attrs_of(r, 0x1b) >> 3 & 1, 1)  # code
        self.assertEqual(model_sail.seg_attrs_of(r, 0x1b) & 0xf, 0xa)

    def test_both_directions_of_the_store_fault_over_the_domain(self):
        dom = model_sail.seg_domain()
        for h, s in self.CASES:
            d, m = dsl_fault(h), model_fault(s)
            self.assertTrue(unsat(dom, z3.Not(m), d), (h, 'assume => no DSL fault'))        # r1
            self.assertTrue(unsat(dom, z3.Not(d), m), (h, 'no DSL fault => no model fault'))  # r2

    def test_without_the_tie_the_first_direction_is_sat(self):
        for h, s in self.CASES[:3]:
            self.assertFalse(unsat(z3.Not(model_fault(s)), dsl_fault(h)), h)      # a free selector with a data attribute: the DSL faults, the model does not

    def test_a_code_selector_in_a_data_register_is_outside_the_domain(self):
        dom = model_sail.seg_domain()
        self.assertTrue(unsat(dom, z3.BitVec('sel_ds', 16) == 0x1b)); self.assertTrue(unsat(dom, z3.BitVec('sel_gs', 16) == 0))
        self.assertFalse(unsat(dom, z3.BitVec('sel_cs', 16) == 0x1b)); self.assertFalse(unsat(dom, z3.BitVec('sel_ds', 16) == 0x23))

    def test_a_wrong_attribute_is_refused(self):
        dom = model_sail.seg_domain()
        self.assertTrue(unsat(dom, z3.BitVec('sel_ds', 16) == 0x23, z3.Extract(1, 1, z3.BitVec('attr_ds', 16)) == 0))


if __name__ == '__main__':
    unittest.main()
