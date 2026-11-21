"""certbasis.prover_files / prover_data_files: the prover identity is the closure of import statements (relative ones too) plus the data files of the packages; a dynamic load is an error."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from fnvnx.lift.proof import certbasis  # noqa: E402


class Closure(unittest.TestCase):
    def test_tree(self):
        f = certbasis.prover_files()
        for need in ('proof/prove.py', 'proof/bounds.py', 'fnvlift/dsl.py', 'fnvlift/core.py', 'fnvlift/inputs.py', 'fnvlift/f_flow.py'):      # the last three are reached only through `from . import`
            self.assertIn(need, f)
        self.assertIn('fnvlift/faults.json', certbasis.prover_data_files())
        self.assertNotIn('proof/sail_pins.txt', certbasis.prover_data_files())

    def test_dynamic_and_relative(self):
        old = certbasis.LIFT
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'pk'))
            for n, src in (('__init__.py', ''), ('a.py', 'from . import b\n'), ('b.py', 'X = 1\n'), ('c.py', 'import importlib\n'), ('e.py', 'exec("1")\n')):
                open(os.path.join(d, 'pk', n), 'w').write(src)
            certbasis.LIFT = d
            try:
                self.assertEqual(certbasis._imports('pk/a.py'), {'pk/__init__.py', 'pk/b.py'})
                for bad in ('pk/c.py', 'pk/e.py'):
                    with self.assertRaises(certbasis.DynamicLoad): certbasis._imports(bad)
            finally: certbasis.LIFT = old


if __name__ == '__main__': unittest.main()
