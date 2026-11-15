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
    def test_access_addresses_come_from_address_functions(self):
        """the address argument of every mload / mstore in forms_*.py is a call of ea / addoff / ssa / esa / dsa, or a name assigned from one
        in the same function (or the `addr` parameter of store_op, whose callers pass such an address).  The registry check of forms.mload is only a guard: nodes are hash-consed, so a raw
        node equal to a tagged one is not distinguishable at run time; this static rule is the enforcement."""
        AF = {'ea', 'addoff', 'ssa', 'esa', 'dsa'}; IDX = {'mload': 0, 'mstore': 1}      # sload / sstore apply ssa themselves (the stack)
        bad = []
        for f in sorted(glob.glob(os.path.join(HERE, 'forms*.py'))):
            tree = ast.parse(open(f).read())
            for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                okn = {a.arg for a in fn.args.args if a.arg == 'addr'}
                for n in ast.walk(fn):
                    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and getattr(n.value.func, 'id', None) in AF:
                        okn |= {t.id for t in n.targets if isinstance(t, ast.Name)}
                for n in ast.walk(fn):
                    if isinstance(n, ast.Call) and getattr(n.func, 'id', None) in IDX and os.path.basename(f) != 'forms.py' or (isinstance(n, ast.Call) and getattr(n.func, 'id', None) in IDX and fn.name not in ('rd', 'sload', 'sstore', 'store_op', 'mload', 'mstore')):
                        a = n.args[IDX[n.func.id]]
                        if not ((isinstance(a, ast.Call) and getattr(a.func, 'id', None) in AF) or (isinstance(a, ast.Name) and a.id in okn)): bad.append((f, n.lineno))
        self.assertEqual(bad, [])
if __name__ == '__main__': unittest.main()
