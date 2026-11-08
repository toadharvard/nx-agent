#!/usr/bin/env python3
"""tests/s2d - link class 4 (s5-003 round 2): member vtables. The vftables an exe constructor stores at a
displacement (`mov dword ptr [reg+d], imm32`: its members' and secondary bases' vptrs, exevft.ctor_member_vftables)
are paired with the native vtables its compiled constructor stores at the same offsets (membervt.vptr_stores,
fnvlink.member_store_pairs), on a synthetic PE32 (tools/link/test_exevft.py's builder) and a hand-written AArch64
ILP32 object (no game content).

    python3 -I tests/s2d/test_member_vftables.py     # needs python capstone and aarch64-linux-gnu-as

Checks: the exe side keeps the last store per offset, before the first `ret`, of a known vftable through the object's
register (an esp-based store, a non-vftable immediate and a store after the return do not count); the native side
follows `this` through register copies and `this + c`, PIC (GOT) and non-PIC vtable addresses with their `add`, a
member's out-of-line constructor called with this + d (by its Itanium name or address-bound `__fnv_<a>_C2`), the
last store per offset, and a call clobbering x0..x18; the pairing puts a free vftable into vmap, gives a bound one
the member's vtable as a displaced vtable, keeps a secondary vtable for the slot fill only, lists an offset with no
native store, and leaves a pairing a named rule excludes (membervt.RULES: NiD3DShaderFactory's
NiTFixedStringMap<NiGlobalConstantEntry*>, S5) unmade.
"""
import collections
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = os.path.join(HERE, '..', '..', 'tools', 'link')
sys.path.insert(0, LINK)
import elfsyms      # noqa: E402
import exevft       # noqa: E402
import fnvlink      # noqa: E402
import membervt     # noqa: E402
import test_exevft as tx  # noqa: E402  (the synthetic PE32 builder)
import vtables      # noqa: E402

try:
    import capstone  # noqa: F401
except ImportError:
    capstone = None

T = tx.T
CTOR = T(1)        # the exe constructor (over slot targets: they are only addresses here)
INNER = 0x00401c00  # an address-bound member constructor (`__fnv_00401c00_C2`)

# Outer::Outer, compiled: Inner members at +0x10 (by name) and +0x30 (address-bound), an inlined Base constructor's
# vptr overwritten by Outer's, Outer's secondary vtable at +8, Member at +0x24 through this+0x20 (non-PIC) with a
# Member method called on it after (it constructs nothing), and a Member vptr clobbered by a call before its store
# at +0x40; a store after the return is not the constructor's
ASM = r'''
    .text
    .globl _ZN5OuterC2Ev
    .type _ZN5OuterC2Ev, %function
_ZN5OuterC2Ev:
    stp x29, x30, [sp, #-32]!
    mov x29, sp
    str x19, [sp, #16]
    mov w19, w0
    add w0, w19, #16
    bl _ZN5InnerC1Ev
    add x0, x19, #0x30
    bl __fnv_00401c00_C2
    adrp x1, :got:_ZTV4Base
    ldr w1, [x1, #:got_lo12:_ZTV4Base]
    add w1, w1, #8
    str w1, [x19]
    adrp x2, :got:_ZTV5Outer
    ldr w2, [x2, #:got_lo12:_ZTV5Outer]
    add w3, w2, #8
    add w2, w2, #24
    str w3, [x19]
    str w2, [x19, #8]
    add x4, x19, #0x20
    adrp x5, _ZTV6Member
    add x5, x5, :lo12:_ZTV6Member+8
    stur w5, [x4, #4]
    add x0, x19, #0x24
    bl _ZN6Member5ResetEv
    adrp x1, :got:_ZTV6Member
    ldr w1, [x1, #:got_lo12:_ZTV6Member]
    add w1, w1, #8
    bl memset
    str w1, [x19, #0x40]
    ldr x19, [sp, #16]
    ldp x29, x30, [sp], #32
    ret
    str w3, [x19, #0x50]
    ret
    .size _ZN5OuterC2Ev, .-_ZN5OuterC2Ev

    .section .data.rel.ro._ZTV5Outer,"aw"
    .weak _ZTV5Outer
    .type _ZTV5Outer, %object
_ZTV5Outer:
    .word 0, _ZTI5Outer, f1, f2
    .word -8, _ZTI5Outer, f3
    .size _ZTV5Outer, .-_ZTV5Outer
    .section .data.rel.ro._ZTV4Base,"aw"
    .weak _ZTV4Base
_ZTV4Base:
    .word 0, _ZTI4Base, f1
    .size _ZTV4Base, .-_ZTV4Base
    .section .data.rel.ro._ZTV5Inner,"aw"
    .weak _ZTV5Inner
_ZTV5Inner:
    .word 0, _ZTI5Inner, g1
    .size _ZTV5Inner, .-_ZTV5Inner
    .section .data.rel.ro._ZTV6Member,"aw"
    .weak _ZTV6Member
_ZTV6Member:
    .word 0, _ZTI6Member, h1
    .size _ZTV6Member, .-_ZTV6Member
'''


def imm(v):
    return struct.pack('<I', v)


@unittest.skipIf(capstone is None, 'python capstone not installed')
class MemberVftables(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='membervt-')
        cls.where = w = {}
        tx.make_pe(os.path.join(cls.tmp, 'probe.exe'), lambda rd: tx.build(rd, w))   # the vftable addresses
        B, M, MS, S, TM = w['Base'], w['Multi'], w['Multi{Side}'], w['Side'], w['Tmpl']
        code = (b'\x56\x8b\xf1' + b'\xc7\x06' + imm(B) + b'\xc7\x06' + imm(M) +   # Base's vptr, then Multi's
                b'\xc7\x46\x08' + imm(MS) +             # Multi{Side} at +8: a secondary base
                b'\xc7\x46\x10' + imm(S) +              # members
                b'\xc7\x46\x24' + imm(TM) +
                b'\xc7\x46\x30' + imm(S) +
                b'\xc7\x44\x24\x08' + imm(S) +          # the frame's, not the object's
                b'\xc7\x46\x40' + imm(S) +
                b'\xc7\x46\x44' + imm(0x12345678) +     # not a vftable
                b'\x8b\xc6\x5e\xc3' +                   # mov eax, esi; pop esi; ret
                b'\xc7\x46\x50' + imm(S) + b'\xc3')     # another function's
        cls.exe = os.path.join(cls.tmp, 'Synthetic.exe')
        w2 = {}
        tx.make_pe(cls.exe, lambda rd: tx.build(rd, w2), {CTOR: code})
        assert w2 == w
        cls.exev = exevft.scan(cls.exe)
        src = os.path.join(cls.tmp, 'outer.s')
        with open(src, 'w') as f:
            f.write(ASM)
        cls.obj = os.path.join(cls.tmp, 'outer.o')
        try:
            subprocess.run([vtables.AS, '-mabi=ilp32', src, '-o', cls.obj], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise unittest.SkipTest('no AArch64 ILP32 assembler: %s' % e)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_exe_side(self):
        w = self.where
        got = exevft.ctor_member_vftables(self.exe, CTOR, set(self.exev))
        self.assertEqual(got, {8: w['Multi{Side}'], 0x10: w['Side'], 0x24: w['Tmpl'], 0x30: w['Side'],
                               0x40: w['Side']})
        # the primary store is still ctor_vftable's (the last displacement-0 store)
        starts = {v for v, e in self.exev.items() if e.td and e.off == 0}
        self.assertEqual(exevft.ctor_vftable(self.exe, CTOR, starts), w['Multi'])
        self.assertEqual(exevft.ctor_member_vftables(self.exe, 0x00300000, set(self.exev)), {})   # outside

    def test_native_side(self):
        got = membervt.vptr_stores(vtables.Obj(self.obj), '_ZN5OuterC2Ev')
        self.assertEqual(got, {0: ('vt', '_ZTV5Outer', 8), 8: ('vt', '_ZTV5Outer', 24),
                               0x10: ('ctor', '_ZN5InnerC1Ev'), 0x24: ('vt', '_ZTV6Member', 8),
                               0x30: ('ctor', '__fnv_00401c00_C2')})
        self.assertEqual(membervt.vptr_stores(vtables.Obj(self.obj), '_ZTV5Outer'), {})   # not a function
        where = {'_ZTV5Inner': 1, '_ZTVN2ns1TILi0EEE': 1}
        self.assertEqual(membervt.ctor_class_vtable('_ZN5InnerC2Ev', where), '_ZTV5Inner')
        self.assertEqual(membervt.ctor_class_vtable('_ZN2ns1TILi0EEC1EPv', where), '_ZTVN2ns1TILi0EEE')
        self.assertIsNone(membervt.ctor_class_vtable('_ZN5OtherC1Ev', where))

    def test_rules(self):
        r = membervt.excluded('.?AVNiShaderFactory@@', '.?AV?$NiTFixedStringMap@PAVNiGlobalConstantEntry@@@@')
        self.assertEqual(r.name, 'shader-factory-global-constant-map')
        self.assertIn('0x005e0200', r.why)
        self.assertIsNotNone(membervt.excluded('.?AVNiD3DShaderFactory@@',
                                               '.?AV?$NiTFixedStringMap@PAVNiGlobalConstantEntry@@@@'))
        self.assertIsNone(membervt.excluded('.?AVNiGPUProgramCache@@',
                                            '.?AV?$NiTFixedStringMap@PAVNiGlobalConstantEntry@@@@'))
        self.assertIsNone(membervt.excluded('.?AVNiShaderFactory@@', '.?AV?$NiTPointerMap@HH@@'))

    def pair(self, rules=None):
        w = self.where
        rtti = vtables.RttiIndex()
        rtti.add(vtables.Obj(self.obj), False)
        sym = elfsyms.Sym('_ZN5OuterC2Ev', 0, 0, 'func', 'global', 1, '', False)
        native = {CTOR: dict(unit='outer.cpp', sym=sym, variants={}, msvc='??0Outer@@QAE@XZ'),
                  INNER: dict(unit='outer.cpp', sym=sym._replace(name='_ZN5InnerC2Ev'), variants={},
                              msvc='??0Inner@@QAE@XZ')}
        vmap = {w['Multi']: ('_ZTV5Outer', 8, self.obj, 'primary, the exe\'s RTTI class Multi'),
                w['Side']: ('_ZTV4Side', 8, '/o/side.o', 'primary, the exe\'s RTTI class Side')}
        alt, vstats = collections.defaultdict(list), collections.Counter()
        got = fnvlink.member_store_pairs(self.exev, vmap, rtti, native, self.exe, alt, vstats, self.tmp,
                                         {'outer.cpp': self.obj}, rules)
        with open(os.path.join(self.tmp, 'member_vftables.tsv')) as f:
            rows = {int(r.split('\t')[2], 16): r.rstrip('\n').split('\t') for r in f if not r.startswith('#')}
        return vmap, alt, vstats, got, rows

    def test_pairs(self):
        w = self.where
        vmap, alt, vstats, got, rows = self.pair()
        self.assertEqual(vmap[w['Tmpl']][:2], ('_ZTV6Member', 8))                 # a free vftable: in vmap
        self.assertTrue(vmap[w['Tmpl']][3].startswith('primary'))                  # exe_vtable_copies extends it
        self.assertEqual(vmap[w['Side']][0], '_ZTV4Side')                          # a bound one keeps its vtable
        self.assertEqual(alt[w['Side']], [('_ZTV5Inner', 8, self.obj, 'primary')])   # once for +0x10 and +0x30
        self.assertNotIn(w['Multi{Side}'], vmap)                                    # a secondary: slot fill only
        self.assertEqual(got, {('_ZTV5Outer', 24): {w['Multi{Side}']}, ('_ZTV5Inner', 8): {w['Side']},
                               ('_ZTV6Member', 8): {w['Tmpl']}})
        self.assertEqual(rows[8][3], 'base')
        self.assertEqual(rows[8][10], 'paired as secondary (slot fill only)')
        self.assertEqual(rows[0x10][3], 'member')
        self.assertTrue(rows[0x10][10].startswith('paired as displaced'))
        self.assertTrue(rows[0x30][10].startswith('paired as displaced'))
        self.assertEqual(rows[0x24][10], 'paired')
        self.assertEqual(rows[0x40][10], 'no native vptr store at the offset')
        self.assertEqual(rows[0x24][8:10], ['1', '1'])   # native slots, exe slots
        self.assertEqual(vstats['member-vt-constructors'], 1)
        self.assertEqual(vstats['member-vt-member-offsets'], 4)
        self.assertEqual(vstats['member-vt-base-offsets'], 1)
        self.assertEqual(vstats['member-vt-short'], 1)   # Inner (1 slot) for Side (2)

    def test_rule_excludes(self):
        w = self.where
        rules = (membervt.Rule('test-multi-tmpl', r'^\.\?AVMulti@@$', r'^\.\?AV\?\$Tmpl@H@@$', 'synthetic'),)
        vmap, alt, vstats, got, rows = self.pair(rules)
        self.assertNotIn(w['Tmpl'], vmap)
        self.assertNotIn(('_ZTV6Member', 8), got)
        self.assertEqual(rows[0x24][10], 'excluded: rule test-multi-tmpl')
        self.assertEqual(vstats['member-vt-excluded-test-multi-tmpl'], 1)
        self.assertEqual(vmap.get(w['Side'])[0], '_ZTV4Side')


if __name__ == '__main__':
    unittest.main()
