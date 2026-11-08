#!/usr/bin/env python3
"""tests/s2d - the destructor of a class template over pointers (`NiTArray<Lit*, NiTMallocInterface<Lit*>>`): the
destructor does not look inside the pointee, so the host unit needs a declaration of the class, not a definition
(bodies.py inst_pointees, inst_fwd, inst_hosts, synth_host's `struct X;` items), on a synthetic tree.

    python3 -I tests/s2d/test_inst_ptr.py       # needs c++filt

A class used as a value, as the argument of another template, or inside a scope is no pointee: the host still has to
define it. A class used only as `X*` gets `struct X;` and any unit that reaches the template's body is a host.
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

ARR = '_ZN8NiTArrayIP3Lit18NiTMallocInterfaceIS1_EED2Ev'      # NiTArray<Lit*, NiTMallocInterface<Lit*>>
PTRARR = '_ZN8NiTArrayI9NiPointerI3LitE15NiTNewInterfaceIS2_EED2Ev'   # NiTArray<NiPointer<Lit>, ...>: Lit as a value
NESTED = '_ZN8NiTArrayIPN3Lit3SubE18NiTMallocInterfaceIS2_EED2Ev'    # Lit::Sub*: a scope, not a plain pointee


def itanium(n):
    d = subprocess.run(['c++filt', n], capture_output=True, text=True).stdout.strip()
    return sigs.parse_itanium(n, d)


@unittest.skipUnless(shutil.which('c++filt'), 'needs c++filt')
class InstPtr(unittest.TestCase):
    def test_pointees(self):
        self.assertEqual(bodies.inst_pointees(itanium(ARR)), ['Lit'])
        self.assertEqual(bodies.inst_fwd(itanium(ARR)), ['struct Lit;'])
        self.assertEqual(bodies.inst_pointees(itanium(PTRARR)), [])
        self.assertEqual(bodies.inst_pointees(itanium(NESTED)), [])
        self.assertEqual(bodies.inst_pointees(itanium('_ZN8NiTArrayIi18NiTMallocInterfaceIiEED2Ev')), [])
        self.assertEqual(bodies.inst_pointees(itanium('_ZN3LitD1Ev')), [])      # no instantiation line at all
        self.assertEqual(bodies.inst_pointees(itanium('_ZN8NiTArrayIP3Lit18NiTMallocInterfaceIS1_EEC1Ev')), [])

    def test_hosts(self):
        with tempfile.TemporaryDirectory() as t:
            for p, text in (
                    ('include/arr.h', 'template <class T> struct NiTMallocInterface {};\n'
                                      'template <class T, class A> class NiTArray {\n public:\n  T* data;\n'
                                      '  ~NiTArray() { delete[] (char*)data; }\n};\n'),
                    ('include/lit.h', 'class Lit { public:\n  int v;\n};\n'),
                    ('src/ref.cpp', '#include "arr.h"\nclass Lit;\n'
                                    'template <> NiTArray<Lit*, NiTMallocInterface<Lit*>>::~NiTArray();\n'),
                    ('src/nolit.cpp', '#include "arr.h"\nint nolit;\n'),
                    ('src/nobody.cpp', '#include "lit.h"\nint nobody;\n')):
                os.makedirs(os.path.dirname(os.path.join(t, p)), exist_ok=True)
                open(os.path.join(t, p), 'w').write(text)
            found = bodycause.tree_bodies(t, {('NiTArray', '~NiTArray')})
            units = {'src/ref.cpp', 'src/nolit.cpp', 'src/nobody.cpp'}
            got = bodies.inst_hosts(t, [R(ARR, 'src/ref.cpp')], itanium, units, found, {ARR: ['src/ref.cpp']})
            # nolit.cpp never mentions Lit and is a host (a declaration is added); ref.cpp references, nobody.cpp has
            # no body of the template
            self.assertEqual(got, {ARR: ['src/nolit.cpp']})
            # the same template over Lit as a value still needs Lit defined in the host
            got = bodies.inst_hosts(t, [R(PTRARR, 'src/ref.cpp')], itanium, units, found, {PTRARR: ['src/ref.cpp']})
            self.assertEqual(got, {})

    def test_items(self):
        # synth_host builds `struct Lit;` before the instantiation line (compile_probe drops either on an error)
        calls = {}

        def probe(tree, work, host, items, tag, flags, prelude='', game=False):
            calls['items'] = items
            return None, []
        import vtables
        old = vtables.compile_probe
        vtables.compile_probe = probe
        try:
            bodies.synth_host('/nonexistent', '/nonexistent', 'src/nolit.cpp', [R(ARR, 'src/ref.cpp')], itanium,
                              inline=True)
        finally:
            vtables.compile_probe = old
        lines = [it[2] for it in calls['items']]
        self.assertIn('struct Lit;', lines)
        self.assertLess(lines.index('struct Lit;'), lines.index(bodies.inst_line(itanium(ARR))))


if __name__ == '__main__':
    unittest.main()
