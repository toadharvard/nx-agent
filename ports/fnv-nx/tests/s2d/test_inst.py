#!/usr/bin/env python3
"""tests/s2d - the instantiation of a class template's destructor in a host unit (bodies.py inst_line, inst_hosts,
include_closure), on a synthetic tree (no decomp content).

    python3 -I tests/s2d/test_inst.py       # needs c++filt

`NiPointer<Lit>::~NiPointer` is referenced by a unit that never completes `Lit` (it declares the destructor without a
body). The host is a unit whose includes reach the template's body and a definition of `Lit`; the referencing unit,
a unit that never defines `Lit` and one that does not reach the body are no hosts. Names that are not a plain
destructor of a template instantiation over global types get no instantiation line.
"""
import collections
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import bodies    # noqa: E402
import bodycause  # noqa: E402
import sigs      # noqa: E402

R = collections.namedtuple('R', 'name units')


def itanium(n):
    d = subprocess.run(['c++filt', n], capture_output=True, text=True).stdout.strip()
    return sigs.parse_itanium(n, d)


@unittest.skipUnless(shutil.which('c++filt'), 'needs c++filt')
class Inst(unittest.TestCase):
    def test_line(self):
        for n, want in (('_ZN9NiPointerI3LitED1Ev', 'template NiPointer<Lit>::~NiPointer();'),
                        ('_ZN8NiTArrayIi18NiTMallocInterfaceIiEED2Ev',
                         'template NiTArray<int,NiTMallocInterface<int>>::~NiTArray();'),
                        ('_ZN9NiPointerI3LitEC1Ev', None),                      # a constructor
                        ('_ZN9NiPointerI3LitED0Ev', None),                      # the deleting variant
                        ('_ZN9NiPointerIN12fnv_009dfa003LitEED1Ev', None),      # a unit-local copy of the pointee
                        ('_ZN12fnv_009dfa009NiPointerI3LitED1Ev', None),        # a unit-local copy of the template
                        ('_ZN3LitD1Ev', None),                                  # not a template
                        ('_ZN9NiPointerI3LitE3GetEv', None)):                   # not a destructor
            self.assertEqual(bodies.inst_line(itanium(n)), want, n)

    def test_hosts(self):
        with tempfile.TemporaryDirectory() as t:
            for p, text in (
                    ('include/ptr.h', 'template <class T> class NiPointer {\n public:\n  T* p;\n'
                                      '  ~NiPointer() { if (p) p->DecRefCount(); }\n};\n'),
                    ('include/lit.h', 'class Lit { public:\n  void DecRefCount();\n};\n'),
                    ('src/ref.cpp', '#include "ptr.h"\nclass Lit;\ntemplate <> NiPointer<Lit>::~NiPointer();\n'
                                    'struct H { NiPointer<Lit> m; };\nvoid f(H* h) { h->~H(); }\n'),
                    ('src/host.cpp', '#include "ptr.h"\n#include "lit.h"\nint host;\n'),
                    ('src/nolit.cpp', '#include "ptr.h"\nclass Lit;\nint nolit;\n'),
                    ('src/nobody.cpp', '#include "lit.h"\nint nobody;\n')):
                os.makedirs(os.path.dirname(os.path.join(t, p)), exist_ok=True)
                open(os.path.join(t, p), 'w').write(text)
            clo = bodies.include_closure(t, ['src/host.cpp', 'src/nobody.cpp'])
            self.assertEqual(clo['src/host.cpp'], {'src/host.cpp', 'include/ptr.h', 'include/lit.h'})
            name = '_ZN9NiPointerI3LitED1Ev'
            found = bodycause.tree_bodies(t, {('NiPointer', '~NiPointer')})
            self.assertTrue(any(f == 'include/ptr.h' and w.startswith('live') for f, w in found[('NiPointer', '~NiPointer')]))
            units = {'src/ref.cpp', 'src/host.cpp', 'src/nolit.cpp', 'src/nobody.cpp'}
            got = bodies.inst_hosts(t, [R(name, 'src/ref.cpp')], itanium, units, found, {name: ['src/ref.cpp']})
            self.assertEqual(got, {name: ['src/host.cpp']})
            # a class defined nowhere: no host, the name stays a visible trap
            got = bodies.inst_hosts(t, [R('_ZN9NiPointerI5NoDefED1Ev', 'src/ref.cpp')], itanium, units, found, {})
            self.assertEqual(got, {})


if __name__ == '__main__':
    unittest.main()
