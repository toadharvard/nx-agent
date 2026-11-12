"""F2: every memory access of a Form builder goes through ONE address function (forms.ea / addoff / ssa / esa / dsa), which applies the effective
segment and a16.  Static: no forms_*.py except forms.py names dsl.load or calls .store directly.  Dynamic: mload / mstore refuse an untagged address."""
import ast, glob, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
HERE = os.path.join(os.path.dirname(__file__), '..')

class T(unittest.TestCase):
    def test_no_raw_access(self):
        bad = []
        for f in sorted(glob.glob(os.path.join(HERE, 'forms*.py'))):
            if os.path.basename(f) == 'forms.py': continue
            for n in ast.walk(ast.parse(open(f).read())):
                if isinstance(n, ast.ImportFrom) and any(a.name == 'load' for a in n.names): bad.append((f, n.lineno, 'import load'))
                if isinstance(n, ast.Call):
                    g = n.func
                    if isinstance(g, ast.Name) and g.id == 'load': bad.append((f, n.lineno, 'load('))
                    if isinstance(g, ast.Attribute) and g.attr == 'store': bad.append((f, n.lineno, '.store('))
        self.assertEqual(bad, [])
    def test_untagged_refused(self):
        from fnvlift.dsl import V
        from proof import forms
        with self.assertRaises(AssertionError): forms.mload(V('esp', 32) + 4, 32)
if __name__ == '__main__': unittest.main()
