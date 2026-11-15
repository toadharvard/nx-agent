"""No lift/proof module opens a file that a tools/gen_*.py generator produces from rt/ (a checked-in copy is a second source: rule 2).  The generated names are read from the generators' own FILES tables;
a proof module reads them through fnvlift/rtgen.py."""
import ast
import glob
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))


def generated_names():
    out = set()
    for p in glob.glob(os.path.join(LIFT, 'tools', 'gen_*.py')):
        for n in ast.walk(ast.parse(open(p).read())):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'FILES' for t in n.targets) and isinstance(n.value, ast.Dict):
                out |= {k.value for k in n.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return out


def is_rt(a):
    return (isinstance(a, ast.Name) and a.id in ('RT', '_RT')) or (isinstance(a, ast.Constant) and a.value == 'rt')


class T(unittest.TestCase):
    def test_generated_names_are_found(self):
        self.assertIn('rcpref.h', generated_names())

    def test_no_proof_module_joins_rt_with_a_generated_name(self):
        names, bad = generated_names(), []
        for p in sorted(glob.glob(os.path.join(LIFT, 'proof', '*.py'))):
            for n in ast.walk(ast.parse(open(p).read())):
                if isinstance(n, ast.Call) and getattr(n.func, 'attr', '') == 'join' and any(is_rt(a) for a in n.args) and any(isinstance(a, ast.Constant) and a.value in names for a in n.args):
                    bad.append('%s:%d' % (os.path.basename(p), n.lineno))
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
