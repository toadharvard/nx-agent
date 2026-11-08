#!/usr/bin/env python3
"""tests/s2d - vtables.run names the vtable of a class whose destructor is private and never defined (W27, link
3f7a6c35 row _ZTV12FUN_00d6a940, hkpBroadphaseViewer's constructor class). A derived probe class cannot be formed over
a private destructor, and the key-function probe's kx line (the destructor called from outside) is an access error
whose note dropped the kd line too: no data came out ("no vtable: derived probe class does not compile"). With kx
retried away the key function's own vtable group and typeinfo are taken. Hand-written unit, no decomp content.

    python3 -I tests/s2d/test_vtreq_private_dtor.py     # needs aarch64-linux-gnu-g++, as, ld, llvm-undname, c++filt
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import vtables   # noqa: E402

SRC = '''struct Base { virtual ~Base(); virtual void f(); int a; };
struct Priv : Base {
    Priv(int);
    int b;
  private:
    ~Priv();   // never defined
};
Priv::Priv(int x) { b = x; }
'''


class PrivateDtor(unittest.TestCase):
    def test_private_undefined_dtor(self):
        with tempfile.TemporaryDirectory() as t:
            tree, out = os.path.join(t, 'tree'), os.path.join(t, 'out')
            os.makedirs(os.path.join(tree, 'src'))
            os.makedirs(os.path.join(out, 'vtables'))
            open(os.path.join(tree, 'src', 't.cpp'), 'w').write(SRC)
            reqs = {'Priv': ('::Priv', {'vtable-ref'}, ['src/t.cpp'])}
            vtables.write_requests(os.path.join(out, 'vtable_requests.tsv'), reqs)
            open(os.path.join(out, 'rtti_defined.txt'), 'w').write('\n')
            vtables.run(out, tree, 1)
            rows = {l.split('\t')[0]: l.rstrip('\n').split('\t')
                    for l in open(os.path.join(out, 'vtables', 'classes.tsv')) if not l.startswith('#')}
            self.assertEqual(rows['Priv'][1:3], ['src/t.cpp', '4Priv'], rows['Priv'])
            self.assertIn('vtable group (key function)', rows['Priv'][3])
            self.assertNotIn('no vtable', rows['Priv'][3])


if __name__ == '__main__':
    unittest.main()
