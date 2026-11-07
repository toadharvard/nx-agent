#!/usr/bin/env python3
"""tests/s2d - S5 class 3 (s5-003): constructor-named vtables (`_ZTV<n>FUN_<ctor>`) paired with the vftable their exe
constructor stores (tools/link/exevft.ctor_vftable, fnvlink.ctor_store_pairs), on a synthetic PE32 (hand-written
classes and x86 bytes from tools/link/test_exevft.py's builder; no game content).

    python3 -I tests/s2d/test_ctor_vftables.py     # needs python capstone

Checks: the last `mov dword ptr [reg], imm32` of an RTTI primary vftable start before the first `ret` names the
vftable (an inlined base constructor's earlier store, a store at a displacement - a secondary vptr - , a store
after the return, a non-vftable immediate, an indexed store and a `push offset` do not); the pairing puts a
vftable no native vtable stands for into the vftable map, gives one already paired the shell as a displaced
vtable (exe_vtable_copies' alt), and leaves bound vtables, addresses without a native `??0` constructor and
constructors without a vptr store alone; ctor_vftables.tsv lists every candidate.
"""
import collections
import os
import shutil
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = os.path.join(HERE, '..', '..', 'tools', 'link')
sys.path.insert(0, LINK)
import exevft       # noqa: E402
import fnvlink      # noqa: E402
import test_exevft as tx  # noqa: E402  (the synthetic PE32 builder)
import vtables      # noqa: E402

try:
    import capstone  # noqa: F401
except ImportError:
    capstone = None

T = tx.T


def imm(v):
    return struct.pack('<I', v)


class FakeObj:
    def __init__(self, path):
        self.path = path


class FakeRtti:
    """RttiIndex's where / ents for hand-written groups: [offset-to-top, typeinfo, slots...]"""

    def __init__(self, groups):
        self.where = {z: (FakeObj('/o/%s.o' % z), False) for z in groups}
        self._ents = {z: [vtables.Ent(0, None, 0), vtables.Ent(0, '_ZTI' + z[4:], 0)] +
                      [vtables.Ent(0, 'slot%d' % k, 0) for k in range(n)] for z, n in groups.items()}

    def ents(self, z):
        return self._ents.get(z)


@unittest.skipIf(capstone is None, 'python capstone not installed')
class CtorVftables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='ctorvft-')
        cls.where = {}
        w = cls.where
        tx.make_pe(os.path.join(cls.tmp, 'probe.exe'), lambda rd: tx.build(rd, w))   # the vftable addresses
        B, M, MS, S = w['Base'], w['Multi'], w['Multi{Side}'], w['Side']
        cls.code = {
            # Multi::Multi with Base's constructor inlined: Base's vptr, then Multi's, then {for Side} at +8
            T(17): b'\x56\x8b\xf1' + b'\xc7\x06' + imm(B) + b'\xc7\x06' + imm(M) + b'\xc7\x46\x08' + imm(MS) +
                   b'\x8b\xc6\x5e\xc3',
            # a store after the first return is another function's
            T(20): b'\x8b\xc1' + b'\xc7\x00' + imm(B) + b'\xc3' + b'\xc7\x00' + imm(S) + b'\xc3',
            # no vptr store: a plain immediate, an indexed store, a push offset
            T(23): b'\xc7\x01' + imm(0x12345678) + b'\xc7\x04\x81' + imm(B) + b'\x68' + imm(S) + b'\xc3',
            # Side::Side through another register, `ret 4`
            T(26): b'\x8b\xc1\x89\x08' + b'\xc7\x00' + imm(S) + b'\xc2\x04\x00',
        }
        cls.exe = os.path.join(cls.tmp, 'Synthetic.exe')
        w2 = {}
        tx.make_pe(cls.exe, lambda rd: tx.build(rd, w2), cls.code)
        assert w2 == w
        cls.exev = exevft.scan(cls.exe)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def starts(self):
        return {v for v, e in self.exev.items() if e.td and e.off == 0}

    def test_ctor_vftable(self):
        w, st = self.where, self.starts()
        self.assertEqual(exevft.ctor_vftable(self.exe, T(17), st), w['Multi'])
        self.assertEqual(exevft.ctor_vftable(self.exe, T(20), st), w['Base'])
        self.assertIsNone(exevft.ctor_vftable(self.exe, T(23), st))
        self.assertEqual(exevft.ctor_vftable(self.exe, T(26), st), w['Side'])
        # only the given starts count: without Multi's, the inlined base's store is the last one
        self.assertEqual(exevft.ctor_vftable(self.exe, T(17), st - {w['Multi']}), w['Base'])
        self.assertIsNone(exevft.ctor_vftable(self.exe, 0x00300000, st))   # outside the image

    def test_pairs(self):
        w = self.where
        names = {T(k): '_ZTV12FUN_%08x' % T(k) for k in (17, 20, 23, 26)}
        groups = {z: 1 for z in names.values()}
        groups.update({'_ZTV4Base': 3, '_ZTV12FUN_00401300': 1})
        rtti = FakeRtti(groups)
        native = {T(17): {'msvc': '??0FUN_x@@QAE@XZ'}, T(20): {'msvc': '??0FUN_y@@QAE@XZ'},
                  T(23): {'msvc': '??0FUN_z@@QAE@XZ'}, T(26): {'msvc': '?Init@FUN_w@@QAEXXZ'},
                  0x00401300: {'msvc': '??0FUN_v@@QAE@XZ'}}
        vmap = {w['Base']: ('_ZTV4Base', 8, '/o/base.o', 'primary, the exe\'s RTTI class Base'),
                w['Tmpl']: (names[T(23)], 8, '/o/t.o', 'primary, named')}   # T(23)'s is bound already
        alt, vstats = collections.defaultdict(list), collections.Counter()
        got = fnvlink.ctor_store_pairs(self.exev, vmap, rtti, native, self.exe, alt, vstats, self.tmp)
        z17, z20 = names[T(17)], names[T(20)]
        self.assertEqual(vmap[w['Multi']][:2], (z17, 8))
        self.assertTrue(vmap[w['Multi']][3].startswith('primary'))          # exe_vtable_copies extends primaries
        self.assertEqual(vmap[w['Base']][0], '_ZTV4Base')                    # the vftable's own vtable stays
        self.assertEqual(alt[w['Base']], [(z20, 8, '/o/%s.o' % z20, 'primary')])
        self.assertEqual(got, {(z17, 8): {w['Multi']}, (z20, 8): {w['Base']}})
        self.assertEqual(vstats['ctor-vt-paired'], 1)
        self.assertEqual(vstats['ctor-vt-paired-as-displaced'], 1)
        self.assertEqual(vstats['ctor-vt-bound'], 1)
        self.assertEqual(vstats['ctor-vt-no-native-constructor-at-the-address'], 1)   # T(26): not a ??0
        self.assertEqual(vstats['ctor-vt-no-vftable-store'], 1)                       # 0x00401300: int3
        with open(os.path.join(self.tmp, 'ctor_vftables.tsv')) as f:
            rows = [r.rstrip('\n').split('\t') for r in f][1:]
        self.assertEqual(len(rows), 5)
        self.assertIn([z17, '%08x' % T(17), '%08x' % w['Multi'], 'paired'], rows)
        # a second run adds no duplicate displaced entry
        fnvlink.ctor_store_pairs(self.exev, dict(vmap), rtti, native, self.exe, alt, collections.Counter(), self.tmp)
        self.assertEqual(len(alt[w['Base']]), 1)


if __name__ == '__main__':
    unittest.main()
