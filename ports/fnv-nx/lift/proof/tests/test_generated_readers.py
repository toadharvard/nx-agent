"""No lift/proof module opens a file that a tools/gen_*.py generator produces from rt/ (a checked-in copy is a second source: rule 2).  The generated names are read from the generators' own FILES tables;
the one reader is proof/genout.gen_dir() (the published GEN output of the root lift/rt), and no second generator exists."""
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

    def test_every_reader_of_a_generated_name_goes_through_genout(self):
        names, bad = generated_names(), []
        for p in sorted(glob.glob(os.path.join(LIFT, 'proof', '*.py')) + glob.glob(os.path.join(LIFT, 'proof', 'tests', '*.py'))):
            if os.path.basename(p) in ('genout.py', 'test_generated_readers.py'):
                continue
            tree = ast.parse(open(p).read())
            uses = any(isinstance(n, ast.Constant) and n.value in names for n in ast.walk(tree))
            goes = any(isinstance(n, ast.Name) and n.id == 'genout' or isinstance(n, ast.alias) and n.name.split('.')[-1] == 'genout' for n in ast.walk(tree))
            if uses and not goes:
                bad.append(os.path.relpath(p, LIFT))
        self.assertEqual(bad, [])

    def test_no_second_generator(self):
        self.assertFalse(os.path.exists(os.path.join(LIFT, 'fnvlift', 'rt' + 'gen.py')))


if __name__ == '__main__':
    unittest.main()
