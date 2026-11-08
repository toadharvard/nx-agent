#!/usr/bin/env python3
"""tests/s2d - S2-bodies stand-in pairing (fnvlink.store_seq_pairs, exevft.vptr_store_seq): a decomp stand-in's vtable
(a name no `_ZTV<n>FUN_<ctor>` rule reads) paired with the exe vftable stored in the same position by the exe
function at the address the stand-in's constructor / destructor is bound to. Synthetic PE32 (tools/link/
test_exevft.py's builder) and hand-written native stores (membervt.vptr_stores is replaced); no game content.

    python3 -I tests/s2d/test_store_seq_pairs.py     # needs python capstone

Checks: the exe function's stores in code order (a repeat once, a displacement or a store after the return
not); a constructor pairs the last store of both sides (all of them when the counts are equal), a destructor the
first; a vtable another pass paired is left; a vftable that has a vtable takes the stand-in as a displaced one;
a stand-in two functions pair with different vftables is a conflict and stays unpaired; seq_vftables.tsv.
"""
import collections
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = os.path.join(HERE, '..', '..', 'tools', 'link')
sys.path.insert(0, LINK)
sys.path.insert(0, HERE)
import exevft            # noqa: E402
import fnvlink           # noqa: E402
import test_ctor_vftables as tc   # noqa: E402
import test_exevft as tx  # noqa: E402

T = tx.T


class Seq(tc.CtorVftables):
    def test_exe_sequence(self):
        w, st = self.where, self.starts()
        self.assertEqual(exevft.vptr_store_seq(self.exe, T(17), st), [w['Base'], w['Multi']])   # +8 is a member
        self.assertEqual(exevft.vptr_store_seq(self.exe, T(20), st), [w['Base']])               # after the ret: not
        self.assertEqual(exevft.vptr_store_seq(self.exe, T(23), st), [])
        self.assertEqual(exevft.vptr_store_seq(self.exe, 0x00300000, st), [])

    def run_pairs(self, stores, vmap, kinds, rtti):
        native = {a: {'sym': mock.Mock(), 'unit': 'u', 'msvc': k} for a, k in kinds.items()}
        for a in native:
            native[a]['sym'].name = 'f%08x' % a

        def fake_stores(obj, name, is_ctor, order):
            order.extend((0, ('vt', z, 8)) for z in stores[int(name[1:], 16)])
        alt, vstats = collections.defaultdict(list), collections.Counter()
        with mock.patch.object(fnvlink.vtables, 'Obj', lambda p: object()), \
                mock.patch.object(fnvlink.membervt, 'vptr_stores', fake_stores):
            got = fnvlink.store_seq_pairs(self.exev, vmap, rtti, native, self.exe, alt, vstats, self.tmp,
                                          {'u': '/o/u.o'}, fnvlink.seq_kind, lambda n: False)
        return got, alt, vstats

    def test_pairs(self):
        w = self.where
        zb, zm, zs, zd = '_ZTV7Standin', '_ZTV8Standin2', '_ZTV9SideShell', '_ZTV4Dtor'
        rtti = tc.FakeRtti({zb: 1, zm: 1, zs: 1, zd: 1, '_ZTV4Base': 3})
        vmap = {w['Base']: ('_ZTV4Base', 8, '/o/base.o', 'primary, the exe\'s RTTI class Base')}
        # T(17): Base then Multi (2 stores); T(26) a destructor of Side; T(20) Base only
        stores = {T(17): [zb, zm], T(26): [zs], T(20): [zd]}
        kinds = {T(17): '??0X@@QAE@XZ', T(26): '??1Y@@QAE@XZ', T(20): '??0Z@@QAE@XZ'}
        got, alt, vst = self.run_pairs(stores, vmap, kinds, rtti)
        # T(17): equal counts: zb <-> Base (already has a vtable: displaced), zm <-> Multi (free: paired)
        self.assertEqual(vmap[w['Multi']][:2], (zm, 8))
        self.assertIn((zb, 8, '/o/%s.o' % zb, 'primary'), alt[w['Base']])
        # T(26) is a destructor: Side's vftable, free
        self.assertEqual(vmap[w['Side']][:2], (zs, 8))
        # T(20) claims zd <-> Base too: another stand-in on the same vftable, displaced
        self.assertIn((zd, 8, '/o/%s.o' % zd, 'primary'), alt[w['Base']])
        self.assertEqual(set(got), {(zb, 8), (zm, 8), (zs, 8), (zd, 8)})
        self.assertEqual(vst['seq-vt-paired'], 2)
        self.assertEqual(vst['seq-vt-paired-as-displaced'], 2)
        rows = [r.rstrip('\n').split('\t') for r in open(os.path.join(self.tmp, 'seq_vftables.tsv'))][1:]
        self.assertIn([zm, '%08x' % T(17), 'ctor', '2/2', '%08x' % w['Multi'], 'paired'], rows)

    def test_counts_differ_and_conflict(self):
        w = self.where
        za = '_ZTV1A'
        rtti = tc.FakeRtti({za: 1})
        # T(17) exe stores [Base, Multi]; the native constructor stores one vtable: the last pair only
        got, alt, vst = self.run_pairs({T(17): [za]}, {}, {T(17): '??0X@@QAE@XZ'}, rtti)
        self.assertEqual(got, {(za, 8): {w['Multi']}})
        # a destructor stores one: the first pair only (Base)
        got, alt, vst = self.run_pairs({T(17): [za]}, {}, {T(17): '??1X@@QAE@XZ'}, rtti)
        self.assertEqual(got, {(za, 8): {w['Base']}})
        # two functions pair the same stand-in with different vftables: no pairing
        vmap = {}
        got, alt, vst = self.run_pairs({T(17): [za], T(26): [za]}, vmap,
                                       {T(17): '??0X@@QAE@XZ', T(26): '??0Y@@QAE@XZ'}, rtti)
        self.assertEqual(got, {})
        self.assertEqual(vmap, {})
        self.assertEqual(vst['seq-vt-conflict'], 2)


if __name__ == '__main__':
    unittest.main()
