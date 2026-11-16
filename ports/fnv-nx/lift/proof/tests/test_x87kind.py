#!/usr/bin/env python3
"""the carrier kind as state (x87kind.py): the emitted compare macro (fv_cmp, ctrans from the .i) equals the spec reading over EVERY pair of the enum's kinds: the condition codes, IE and C1 close;
DE closes for every pair without a binary32/binary64 carrier, and is reported open (rt DE, L3a) for the rest; the loaders' no-SNaN lemma; the DE defect's universe."""
import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, '..'), os.path.join(HERE, '..', '..')]
import x87carrier
try:
    x87carrier.carrier(); NOINPUT = None
except x87carrier.NoInput as e:
    NOINPUT = str(e)
import x87kind as K


@unittest.skipIf(NOINPUT, NOINPUT or '')
class Kind(unittest.TestCase):
    def test_compare_over_every_kind_pair(self):
        res = K.compare_obligations(120000)
        kinds = K.kinds()
        self.assertEqual(len(res), len(kinds) * (len(kinds) + 1) // 2)
        for p, r in res.items():
            self.assertEqual((r['codes'], r['IE+C1']), ('unsat', 'unsat'), p)
            binary = {'FK_F32', 'FK_F64'} & set(p)
            if not binary:
                self.assertEqual(r['DE'], 'unsat', p)            # no binary carrier: DE of the 80-bit image is DE at the type
            self.assertIn(r['DE'], ('unsat', 'sat'), p)
        open_de = sorted(p for p, r in res.items() if r['DE'] == 'sat')
        print('open: rt DE (L3a): %d of %d kind pairs: %s' % (len(open_de), len(res), open_de))

    def test_loaders_do_not_make_an_snan(self):
        self.assertEqual(K.ctor_no_snan(), {'fv_ld32': 'unsat', 'fv_ld64': 'unsat'})

    def test_de_defects_are_the_open_pairs(self):
        d = K.compare_de_defects(120000)
        res = K.compare_obligations(120000)
        self.assertEqual(sorted(d), sorted(p for p, r in res.items() if r['DE'] == 'sat'))
        for p, c in d.items():
            self.assertIsInstance(c, dict, p)
            self.assertNotEqual(c['emitted_DE'], c['spec_DE'], p)


if __name__ == '__main__':
    unittest.main()
