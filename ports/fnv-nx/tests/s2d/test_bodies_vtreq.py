#!/usr/bin/env python3
"""tests/s2d - vtables.bodies_requests: a `_ZTV` / `_ZTI` that only a synthesized body object (out/bodies/synth.tsv)
references is added to vtable_requests.tsv with that object's host unit; one rtti_defined.txt or a vtables/ object
defines is not; an existing request gets the host added. Synthetic names; needs aarch64-linux-gnu-as and c++filt.

    python3 -I tests/s2d/test_bodies_vtreq.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import vtables   # noqa: E402

AS = shutil.which('aarch64-linux-gnu-as')


def obj(d, name, text):
    s, o = os.path.join(d, name + '.S'), os.path.join(d, name + '.o')
    open(s, 'w').write(text)
    subprocess.run([AS, '-mabi=ilp32', s, '-o', o], check=True)
    return o


@unittest.skipUnless(AS and shutil.which('c++filt'), 'needs aarch64-linux-gnu-as and c++filt')
class BodiesRequests(unittest.TestCase):
    def test_requests(self):
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, 'bodies'))
            os.makedirs(os.path.join(out, 'vtables'))
            bo = obj(os.path.join(out, 'bodies'), 'zz_bd_a',
                     '    .data\n    .word _ZTV4Tail\n    .word _ZTI4IosB\n    .word _ZTV4Done\n    .word _ZTI4Have\n'
                     '    .word _ZTV3Old\n')
            vo = obj(os.path.join(out, 'vtables'), 'zz_vt_x', '    .data\n    .globl _ZTI4Have\n_ZTI4Have:\n    .word 0\n')
            open(os.path.join(out, 'bodies', 'synth.tsv'), 'w').write('#host_unit\tobject\tnames\nsrc/a.cpp\t%s\t3\n' % bo)
            open(os.path.join(out, 'vtables', 'synth.tsv'), 'w').write('#host\tobject\nsrc/x.cpp\t%s\n' % vo)
            open(os.path.join(out, 'rtti_defined.txt'), 'w').write('_ZTV4Done\n')
            vtables.write_requests(os.path.join(out, 'vtable_requests.tsv'), {'Old': ('::Old', {'vtable-ref'}, ['src/o.cpp'])})
            n = vtables.bodies_requests(out)
            reqs = vtables.load_requests(os.path.join(out, 'vtable_requests.tsv'))
        self.assertEqual(n, 3)
        self.assertEqual(reqs['Tail'], ('::Tail', {'bodies-ref'}, ['src/a.cpp']))
        self.assertEqual(reqs['IosB'][2], ['src/a.cpp'])
        self.assertNotIn('Done', reqs)    # rtti_defined
        self.assertNotIn('Have', reqs)    # a vtables/ object defines it
        self.assertEqual(reqs['Old'][2], ['src/o.cpp', 'src/a.cpp'])
        self.assertEqual(reqs['Old'][1], {'vtable-ref', 'bodies-ref'})


if __name__ == '__main__':
    unittest.main()
