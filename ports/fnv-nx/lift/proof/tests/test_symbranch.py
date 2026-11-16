#!/usr/bin/env python3
"""A Python decision on a symbolic value has no answer (proof/symform.py SymbolicBranch): every node a builder can receive as a field value (dsl.N, and symform.SV that stands for one) raises SymbolicBranch from
its truth value, every comparison, hash, int(), membership and iteration, so `v in TABLE` / `if v == k` on a shape never silently takes the default branch.  The int shape is the exact mux over the record's
vectors (forms_sys.int_): its instance at every vector equals the concrete instruction's Form, and the shape Form of every key that has a builder is built without a symbolic branch except `enter`
(a nesting level other than 0 has no Form: the concrete builder asserts it)."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, LIFT)
from fnvlift import inputs
inputs.configure(x86sys=os.path.join(LIFT, '..', 'platform', 'declared', 'x86sys.json'), universe='/dev/null', fpstate='/nonexistent', fpdepth='/dev/null')
from fnvlift import dsl
from proof import forms, symform, shape as shp


class T(unittest.TestCase):
    def test_every_python_decision_on_a_node_raises(self):
        n = dsl.V('eax', 32); sv = symform.SV(n, 'imm')
        for v in (n, sv):
            for f in (bool, int, hash, len, list, lambda x: x == 3, lambda x: x != 3, lambda x: x < 3, lambda x: x >= 3, lambda x: x in (1, 2), lambda x: x in {1: 2}, lambda x: 3 in x, lambda x: {x: 1}):
                with self.assertRaises(symform.SymbolicBranch): f(v)

    def test_int_shape_at_every_vector(self):
        sf = symform.symbuild('cd00/1.1.0.0'); n = 0
        for v in range(256):
            raw = bytes([0xcd, v])
            try: forms.build(list(shp.decoder().disasm(raw, 0x401000))[0], 0x401000)
            except (KeyError, AssertionError): continue                          # undecided on the declared OS: no concrete Form, nothing to equal
            ok, why = symform.link_ok(sf, raw, 0x401000); n += 1
            self.assertTrue(ok, (v, why))
        from fnvlift import x86sys
        self.assertEqual(n, 256 - len(x86sys.INT_EXCLUDED))      # every vector but those the record excludes (undecided on the declared OS) has a concrete Form that the shape equals; the count is the record's, not a constant

    def test_excluded_vector_is_red_at_instantiation(self):
        from fnvlift import x86sys
        sf = symform.symbuild('cd00/1.1.0.0')
        self.assertTrue(x86sys.INT_EXCLUDED)
        for v in x86sys.INT_EXCLUDED:
            with self.assertRaises(symform.OutsideDomain): symform.instantiate(sf, bytes([0xcd, v]), 0x401000)

    def test_enter_has_no_shape_form(self):
        with self.assertRaises(symform.SymbolicBranch): symform.symbuild('c8000000/1.3.0.0')


if __name__ == '__main__': unittest.main()
