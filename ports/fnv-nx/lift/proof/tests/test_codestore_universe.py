"""every built Form of the shape universe that carries a code-segment store fault has, over the domain of the registers it writes through (both halves: data selectors and code selectors), exactly the SDM fault condition
(sysspec_store.prove_codestore).  The universe is a declared input: FNVNX_SHAPE_KEYS = a file with one shape key per line (no default; unset is a KeyError).  Forms are built by the one builder, never listed."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
declared_x86sys.configure()
from proof import symform, x86sys
import sysspec_gap
import sysspec_store


class Universe(unittest.TestCase):
    def test_every_codestore_form_has_the_sdm_condition(self):
        keys = [l.strip() for l in open(os.environ['FNVNX_SHAPE_KEYS']) if l.strip()]
        rec = x86sys.load(); built = proved = 0; bad = []
        for k in keys:
            try: f = symform.symbuild(k).form
            except Exception: continue
            built += 1
            if not any(a.startswith('codestore:') for a in getattr(f, 'access', ())): continue
            ins = sysspec_gap.decode(k)
            r = sysspec_store.prove_codestore(f, ins, rec, rep=ins.mnemonic.startswith('rep'))
            if r == 'proved': proved += 1
            else: bad.append(k)
        print('built %d, with a code-segment store fault %d: proved %d, differs %d %s' % (built, proved + len(bad), proved, len(bad), bad[:10]))
        self.assertTrue(proved > 0); self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
