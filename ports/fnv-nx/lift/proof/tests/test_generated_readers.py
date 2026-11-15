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

    def test_compiled_c_that_includes_a_generated_name_gets_the_generated_dir(self):
        """a C/C++ file of rt/ that `#include`s a generated name (directly, or through a header that does) is compiled by a proof module only with the generator's output on the include path (rtgen)"""
        import re
        names = generated_names(); files = {}
        for p in glob.glob(os.path.join(LIFT, 'rt', '*')):
            if p.endswith(('.c', '.cc', '.cpp', '.h', '.inc')):
                files[os.path.basename(p)] = set(re.findall(r'^\s*#\s*include\s+"([^"]+)"', open(p, errors='replace').read(), re.M))
        needs = {f for f, inc in files.items() if inc & names}
        while True:                                          # closure: a file that includes a file that needs them
            more = {f for f, inc in files.items() if inc & needs} - needs
            if not more:
                break
            needs |= more
        self.assertIn('cpumodel.c', needs)
        bad = []
        for p in sorted(glob.glob(os.path.join(LIFT, 'proof', '*.py'))):
            src = open(p).read(); consts = [n.value for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            compiles = any(c in ('gcc', 'g++', 'cc', 'c++', 'clang', 'clang++') for c in consts)       # the module runs a C compiler
            names_it = any(re.search(r'(^|[/"\s])' + re.escape(f) + r'(["\s]|$)', c) for c in consts for f in needs if f.endswith(('.c', '.cc', '.cpp')))
            if compiles and names_it and 'rtgen' not in src:
                bad.append(os.path.basename(p))
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
