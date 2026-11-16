"""every built Form of the shape universe that carries a code-segment store fault has, over the domain of the registers it writes through (both halves: data selectors and code selectors), exactly the SDM fault condition
(sysspec_store.prove_codestore).  The universe is a declared input: the PACK output of lift/liftsrc, its shapes.tsv keys (tests/inputs.json; no default).  Forms are built by the one builder, never listed."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import fnvnx.lift.proof.tests.declared_x86sys as declared_x86sys
declared_x86sys.configure()
from fnvnx.lift.proof import symform, x86sys
import fnvnx.lift.proof.sysspec_gap as sysspec_gap
import fnvnx.lift.proof.sysspec_store as sysspec_store


class Universe(unittest.TestCase):
    def test_every_codestore_form_has_the_sdm_condition(self):
        keys = declared_x86sys.shape_keys()
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
