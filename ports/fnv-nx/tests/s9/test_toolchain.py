#!/usr/bin/env python3
"""tests/s9 - how the toolchain stage provides what a fresh machine lacks (tools/pipeline/build.py provide_*), with
the build scripts replaced by stand-ins that only record their command and leave the files they would make.

    python3 -I tests/s9/test_toolchain.py

Checks: with nothing given, the stage fetches the pinned sources, builds fnvnx-a64, then the DEVKITPRO tree,
in that order, each once (a second run reuses them); a changed build script rebuilds; TC_DL=none never
builds; FNVNX_TC / DEVKITPRO given are used as they are; CROSS=pinned unpacks the cross packages and
CROSS=system leaves PATH alone; the pinned-version check accepts only the packages.tsv version.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'pipeline'))
import build  # noqa: E402


class FakeBuild(build.Build):
    """Build whose sh() records the script and makes the outputs it would leave."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.calls = []

    def sh(self, argv, log, cwd=None, env=None, timeout=None, check=False, quiet=False):
        argv = [str(a) for a in argv]
        name = os.path.relpath(argv[1], build.ROOT) if len(argv) > 1 and argv[0] == 'bash' else argv[0]
        self.calls.append(name)
        if name == 'toolchain/sources.sh':
            for n in ('buildscripts', 'devkita64-rules'):
                os.makedirs(os.path.join(argv[3], n), exist_ok=True)
        elif name == 'toolchain/gcc/build.sh':
            os.makedirs(os.path.join(argv[4], 'bin'))
        elif name == 'toolchain/devkita64/setup.sh':
            os.makedirs(os.path.join(argv[5], 'libnx', 'lib'))
        elif name == 'toolchain/cross/setup.sh':
            os.makedirs(os.path.join(argv[2], 'bin'), exist_ok=True)
        return 0


class Toolchain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fnvnx-s9tc-')
        self.ext = os.path.join(self.tmp, 'ext')
        os.makedirs(os.path.join(self.ext, 'libnx'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, **cfg):
        c = {k: d for k, d, _ in build.CONFIG}
        c.update(BUILD_DIR=os.path.join(self.tmp, 'build'), JOBS='1', CLEAN_WORK='1', FNVNX_TC='', DEVKITPRO='',
                 CROSS_BIN='', PORT_COMMIT='')
        c.update(cfg)
        b = FakeBuild(c)
        b.recs['fetch'] = build.Record(stage='fetch', status='ok', artifacts={'ext': self.ext})
        return b

    def provide(self, b):
        r = build.Record(stage='toolchain', log=os.path.join(b.sdir('toolchain'), 'stage.log'))
        d = b.sdir('toolchain')
        prefix, dkp = build.provide_fnvnx_a64(b, r, d)
        b.plog.close()
        return prefix, dkp

    def test_fresh_machine_builds_once(self):
        b = self.make()
        prefix, dkp = self.provide(b)
        self.assertEqual(b.calls, ['toolchain/sources.sh', 'toolchain/gcc/build.sh', 'toolchain/devkita64/setup.sh'])
        self.assertTrue(prefix.endswith('toolchain/prefix') and dkp.endswith('toolchain/dkp'))
        b = self.make()
        self.assertEqual(self.provide(b), (prefix, dkp))
        self.assertEqual(b.calls, [])

    def test_script_change_rebuilds(self):
        self.provide(self.make())
        saved = build.build_id
        try:
            build.build_id = lambda *p, extra=(): saved(*p, extra=list(extra) + ['changed'])
            b = self.make()
            self.provide(b)
        finally:
            build.build_id = saved
        self.assertEqual(b.calls, ['toolchain/sources.sh', 'toolchain/gcc/build.sh', 'toolchain/devkita64/setup.sh'])

    def test_never_build(self):
        b = self.make(TC_DL='none')
        self.assertEqual([bool(x) for x in self.provide(b)], [False, False])
        self.assertEqual(b.calls, [])

    def test_given_ones_used(self):
        tc, dkp = os.path.join(self.tmp, 'tc'), os.path.join(self.tmp, 'dkp')
        os.makedirs(tc)
        os.makedirs(dkp)
        b = self.make(FNVNX_TC=tc, DEVKITPRO=dkp)
        self.assertEqual(self.provide(b), (tc, dkp))
        self.assertEqual(b.calls, [])

    def test_cross_modes(self):
        b = self.make(CROSS='pinned')
        r = build.Record(stage='toolchain', log=os.path.join(b.sdir('toolchain'), 'stage.log'))
        cb = build.provide_cross(b, r, b.sdir('toolchain'))
        self.assertEqual((b.calls, cb), (['toolchain/cross/setup.sh'], os.path.join(b.out, 'toolchain', 'cross', 'bin')))
        b = self.make(CROSS='system')
        self.assertIsNone(build.provide_cross(b, r, b.sdir('toolchain')))
        self.assertEqual(b.calls, [])
        b = self.make(CROSS_BIN=self.tmp)
        self.assertEqual(build.provide_cross(b, r, b.sdir('toolchain')), self.tmp)
        b.plog.close()

    def test_pin_check(self):
        bindir = os.path.join(self.tmp, 'bin')
        os.makedirs(bindir)
        pins = build.cross_pins()
        gcc = pins['g++-13-aarch64-linux-gnu'].split('cross')[0]
        for ver, want in ((gcc, True), ('13.2.0-23ubuntu4', False)):
            for n, text in (('aarch64-linux-gnu-g++', 'aarch64-linux-gnu-g++ (Ubuntu %s) 13.3.0' % ver),
                            ('aarch64-linux-gnu-ld', 'GNU ld (GNU Binutils for Ubuntu) 2.42')):
                p = os.path.join(bindir, n)
                with open(p, 'w') as f:
                    f.write('#!/bin/sh\necho "%s"\n' % text)
                os.chmod(p, 0o755)
            b = self.make()
            self.assertEqual(build.cross_pin_matches(b, bindir)[0], want)
            b.plog.close()


if __name__ == '__main__':
    unittest.main(verbosity=1)
