#!/usr/bin/env python3
"""tests of the pipeline's boot stage mounting the owner's Fallout_default.ini (R4, recon4-013): with an exe image that
has Fallout_default.ini beside it (gamedata/exe's layout) the emulator gets --file switch/fnv/Fallout_default.ini=...;
without one (S14's synthesized image) only the exe; the ini is in the stage's fingerprint. The emulator is not run:
the argv is captured at b.sh.

    python3 -I tests/t1/test_boot_ini.py
"""
import os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'pipeline'))
import build


class Captured(Exception):
    pass


class FakeB:
    def __init__(self, t, image):
        self.t, self.cfg = t, {'BOOT_IMAGE': image, 'EMU_BUDGET': '1', 'EMU_TIMEOUT': '1', 'BOOT_CENSUS': '',
                                'BOOT': '1', 'PRISTINE_OBJS': ''}
        self.argv = None

    def sdir(self, name):
        d = os.path.join(self.t, name); os.makedirs(d, exist_ok=True); return d

    def rec(self, name):
        return {'method': 'S5 game link'}

    def art(self, stage, name):
        return os.path.join(self.t, stage, name)

    def rel(self, p):
        return p

    def say(self, *a):
        pass

    def sh(self, argv, log, **kw):
        self.argv = argv
        raise Captured()


class BootIni(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix='t1boot.')

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def argv(self, image):
        b = FakeB(self.t, image)
        with self.assertRaises(Captured):
            build.Boot().run(b, {})
        return b.argv

    def test_ini_beside_the_exe_is_mounted(self):
        gd = os.path.join(self.t, 'gamedata'); os.makedirs(gd)
        exe, ini = os.path.join(gd, 'FalloutNV.exe'), os.path.join(gd, 'Fallout_default.ini')
        open(exe, 'w').write('exe'); open(ini, 'w').write('[General]\n')
        a = self.argv(exe)
        self.assertIn('switch/fnv/FalloutNV.exe=' + exe, a)
        self.assertIn('switch/fnv/Fallout_default.ini=' + ini, a)
        self.assertEqual(a[a.index('switch/fnv/Fallout_default.ini=' + ini) - 1], '--file')

    def test_no_ini_no_mount(self):
        sd = os.path.join(self.t, 'synth'); os.makedirs(sd)
        exe = os.path.join(sd, 'synth.exe'); open(exe, 'w').write('exe')
        a = self.argv(exe)
        self.assertIn('switch/fnv/FalloutNV.exe=' + exe, a)
        self.assertFalse(any('Fallout_default.ini' in x for x in a))

    def test_ini_in_fingerprint(self):
        gd = os.path.join(self.t, 'gamedata'); os.makedirs(gd)
        exe, ini = os.path.join(gd, 'FalloutNV.exe'), os.path.join(gd, 'Fallout_default.ini')
        open(exe, 'w').write('exe')
        b = FakeB(self.t, exe)
        orig_up, orig_tree = build.up_id, build.tree_hash
        build.up_id = lambda *a: 'up'; build.tree_hash = lambda *a: 'tree'
        try:
            f0 = build.Boot().fingerprint(b)
            open(ini, 'w').write('[General]\n'); f1 = build.Boot().fingerprint(b)
            open(ini, 'w').write('[General]\nx=1\n'); f2 = build.Boot().fingerprint(b)
        finally:
            build.up_id, build.tree_hash = orig_up, orig_tree
        self.assertNotEqual(f0, f1); self.assertNotEqual(f1, f2)


if __name__ == '__main__':
    unittest.main()
