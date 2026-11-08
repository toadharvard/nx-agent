#!/usr/bin/env python3
"""tests/s2d - vtables.run probes a class in the first unit of its host list (the unit that referenced it) before any
greedy batch choice, so a class whose own unit had few open classes is not starved by larger hosts. Hand-written
units, no decomp content.

    python3 -I tests/s2d/test_vtreq_first_host.py      # needs aarch64-linux-gnu-g++, as, ld, llvm-undname, c++filt

Fixture: unit src/t.cpp declares `struct Lone { virtual ~Lone(); int x; };` (key function defined nowhere); eight
other units src/h1..h8.cpp each declare three polymorphic classes of their own that are also requested (host: that
unit alone) and do not see Lone. Lone's host list is [t, h1 .. h8]. Taking the largest host among the next three each
round, Lone is probed in h1, h2, ... and the eight rounds end before src/t.cpp (before the fix: "no host unit could
name the class"); with the first host first it is named in round 1.
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import vtables   # noqa: E402

NH = 8
DUMMY = 3


def source(k):
    if k == 't':
        return 'struct Lone { virtual ~Lone(); int x; };\nint t_use(Lone* p) { return p->x; }\n'
    out = []
    for j in range(DUMMY):
        out.append('struct D%d_%d { virtual ~D%d_%d(); int x; };\nint u%d_%d(D%d_%d* p) { return p->x; }\n'
                   % (k, j, k, j, k, j, k, j))
    return ''.join(out)


class FirstHost(unittest.TestCase):
    def test_first_host_is_probed_first(self):
        with tempfile.TemporaryDirectory() as t:
            tree, out = os.path.join(t, 'tree'), os.path.join(t, 'out')
            os.makedirs(os.path.join(tree, 'src'))
            os.makedirs(os.path.join(out, 'vtables'))
            for k in ['t'] + list(range(1, NH + 1)):
                open(os.path.join(tree, 'src', 't.cpp' if k == 't' else 'h%d.cpp' % k), 'w').write(source(k))
            reqs = {'Lone': ('::Lone', {'typeinfo-ref'}, ['src/t.cpp'] + ['src/h%d.cpp' % k for k in range(1, NH + 1)])}
            for k in range(1, NH + 1):
                for j in range(DUMMY):
                    reqs['D%d_%d' % (k, j)] = ('::D%d_%d' % (k, j), {'vtable-ref'}, ['src/h%d.cpp' % k])
            vtables.write_requests(os.path.join(out, 'vtable_requests.tsv'), reqs)
            open(os.path.join(out, 'rtti_defined.txt'), 'w').write('\n')
            vtables.run(out, tree, 2)
            rows = {l.split('\t')[0]: l.rstrip('\n').split('\t')
                    for l in open(os.path.join(out, 'vtables', 'classes.tsv')) if not l.startswith('#')}
            self.assertEqual(rows['Lone'][1:3], ['src/t.cpp', '4Lone'], rows['Lone'])
            self.assertIn('typeinfo', rows['Lone'][3])
            for k in range(1, NH + 1):
                self.assertEqual(rows['D%d_0' % k][1], 'src/h%d.cpp' % k)


if __name__ == '__main__':
    unittest.main()
