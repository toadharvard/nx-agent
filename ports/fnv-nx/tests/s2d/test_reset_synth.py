#!/usr/bin/env python3
"""tests/s2d - fnvlink.reset_synth: before resolve 1 the previous link run's synthesized objects are dropped from what
resolve loads (out/bodies/, vtables/synth.tsv), the vtables probe cache stays, and resolve then loads no synthesized
object. Without it consecutive runs alternated (links 0540aff0 / d1ed15e7).

    python3 -I tests/s2d/test_reset_synth.py
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import bodies    # noqa: E402
import fnvlink   # noqa: E402
import vtables   # noqa: E402


class ResetSynth(unittest.TestCase):
    def test_reset(self):
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, 'bodies'))
            os.makedirs(os.path.join(out, 'vtables'))
            bo, vo = os.path.join(out, 'bodies', 'zz_bd_a.o'), os.path.join(out, 'vtables', 'zz_vt_a.o')
            for p in (bo, vo):
                open(p, 'wb').close()
            open(os.path.join(out, 'bodies', 'synth.tsv'), 'w').write('#h\to\tn\nsrc/a.cpp\t%s\t1\n' % bo)
            open(os.path.join(out, 'bodies', 'bind.tsv'), 'w').write('#name\tclass\ttarget\nX\taddr\t__fnv_00401000\n')
            open(os.path.join(out, 'vtables', 'synth.tsv'), 'w').write('#h\to\nsrc/a.cpp\t%s\n' % vo)
            open(os.path.join(out, 'vtables', 'cache.json'), 'w').write('{}')
            self.assertEqual(len(bodies.load_synth(out)), 1)
            self.assertEqual(len(vtables.load_synth(out)), 1)
            gone = fnvlink.reset_synth(out)
            self.assertEqual(gone, ['bodies/', 'vtables/synth.tsv'])
            self.assertEqual(bodies.load_synth(out), [])
            self.assertEqual(vtables.load_synth(out), [])
            self.assertEqual(bodies.load_bind(out), {})
            self.assertTrue(os.path.exists(os.path.join(out, 'vtables', 'cache.json')))   # the probe cache stays
            self.assertEqual(fnvlink.reset_synth(out), [])   # idempotent


if __name__ == '__main__':
    unittest.main()
