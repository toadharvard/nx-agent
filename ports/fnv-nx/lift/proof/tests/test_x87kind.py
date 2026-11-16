#!/usr/bin/env python3
"""the carrier kind as state (x87kind.py): the emitted compare macro (fv_cmp, ctrans from the .i) equals the spec reading over EVERY pair of the enum's kinds: the condition codes, IE and C1 close;
DE closes for every pair too (rt fx_de, L3a 56f9b080c; before it 13 of 28 pairs were open); the loaders' no-SNaN lemma; the DE defect's universe."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..'), os.path.join(HERE, '..', '..')]
import fnvnx.lift.proof.x87carrier as x87carrier
try:
    x87carrier.carrier(); NOINPUT = None
except x87carrier.NoInput as e:
    NOINPUT = str(e)
import fnvnx.lift.proof.x87kind as K


@unittest.skipIf(NOINPUT, NOINPUT or '')
class Kind(unittest.TestCase):
    def test_compare_over_every_kind_pair(self):
        res = K.compare_obligations(120000)
        kinds = K.kinds()
        self.assertEqual(len(res), len(kinds) * (len(kinds) + 1) // 2)
        for p, r in res.items():
            self.assertEqual((r['codes'], r['IE+C1'], r['DE']), ('unsat', 'unsat', 'unsat'), p)      # with rt fx_de (L3a 56f9b080c) every field closes, DE included

    def test_loaders_do_not_make_an_snan(self):
        self.assertEqual(K.ctor_no_snan(), {'fv_ld32': 'unsat', 'fv_ld64': 'unsat'})

    def test_no_de_defect_is_left(self):
        self.assertEqual(K.compare_de_defects(120000), {})


if __name__ == '__main__':
    unittest.main()
