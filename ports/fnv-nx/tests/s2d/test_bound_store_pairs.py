#!/usr/bin/env python3
"""tests/s2d - W47: shell vtables paired by the stores of any function bound to an exe address
(fnvlink.bound_store_pairs, membervt.vt_store_seq). A decomp shell's vtable that no other pass pairs (no `??_7` name,
no `_ZTV<n>FUN_<ctor>` name, no `??0` / `??1` at the storing function) is stored by a factory (`new` + an inlined
constructor) or by a decomp constructor that the exe inlined; the exe function at the same address stores the vftable
it stands for. Synthetic PE32 (tools/link/test_exevft.py's builder), hand-assembled AArch64 ILP32 code
(aarch64-linux-gnu-as); no game content.

    python3 -I tests/s2d/test_bound_store_pairs.py     # needs python capstone and aarch64-linux-gnu-as

Checks: vt_store_seq reads a store through the result of `operator new` and through an unbound constructor it
calls; a free vftable takes the shell (vmap, `primary`), one that has a vtable takes it as a displaced one (alt);
two stores on both sides pair by position; two functions that pair a shell with disagreeing vftables leave it
unpaired (conflict); a shell another pass paired is left; no exe store / counts that differ pair nothing;
bound_vftables.tsv; FNVNX_BSTORE=0 turns the pass off; exe_vtable_copies writes the shell as transform(exe) (VTFILL).
"""
import collections
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = os.path.join(HERE, '..', '..', 'tools', 'link')
sys.path.insert(0, LINK)
sys.path.insert(0, HERE)
import elfsyms           # noqa: E402
import exevft            # noqa: E402
import fnvlink           # noqa: E402
import membervt          # noqa: E402
import test_ctor_vftables as tc   # noqa: E402
import test_exevft as tx  # noqa: E402
import vtables           # noqa: E402

try:
    import capstone  # noqa: F401
except ImportError:
    capstone = None

T = tx.T


def imm(v):
    return struct.pack('<I', v)


def store(*vs):   # mov eax, ecx; mov dword ptr [eax], imm32 ...; ret
    return b'\x8b\xc1' + b''.join(b'\xc7\x00' + imm(v) for v in vs) + b'\xc3'


def vt(reg, z):
    return '    adrp %s, %s+8\n    add %s, %s, :lo12:%s+8\n' % (reg, z, reg, reg, z)


def fn(name, body):
    return '    .globl %s\n    .type %s, %%function\n%s:\n%s    .size %s, .-%s\n' % (name, name, name, body, name, name)


NEW = '    stp x29, x30, [sp, #-16]!\n    mov w0, #16\n    bl _Znwj\n'
END = '    ldp x29, x30, [sp], #16\n    ret\n'
SRC = '    .text\n    .p2align 2\n' + ''.join([
    fn('factory', NEW + vt('x1', '_ZTV5Shell') + '    str w1, [x0]\n' + END),             # new + inlined ctor
    fn('_ZN6Shell2C2Ev', vt('x1', '_ZTV6Shell2') + '    str w1, [x0]\n    ret\n'),       # the decomp's own ctor
    fn('maker', NEW + '    bl _ZN6Shell2C2Ev\n' + END),                                    # calls it (unbound)
    fn('conf1', vt('x1', '_ZTV4Conf') + '    str w1, [x0]\n    ret\n'),
    fn('conf2', vt('x1', '_ZTV4Conf') + '    str w1, [x0]\n    ret\n'),
    fn('two', '    mov x19, x0\n' + vt('x1', '_ZTV6ShellA') + '    str w1, [x19]\n' + vt('x2', '_ZTV6ShellB') +
       '    str w2, [x19]\n    ret\n'),
    fn('none', vt('x1', '_ZTV6Shell3') + '    str w1, [x0]\n    ret\n'),
    fn('named', vt('x1', '_ZTV5Named') + '    str w1, [x0]\n    ret\n'),
    fn('diff', vt('x1', '_ZTV6Shell4') + '    str w1, [x0]\n    ret\n'),
    fn('member', vt('x1', '_ZTV6Shell5') + '    str w1, [x0, #8]\n    ret\n'),               # +8: not a vptr
])
GROUPS = {'_ZTV5Shell': 2, '_ZTV6Shell2': 2, '_ZTV4Conf': 1, '_ZTV6ShellA': 1, '_ZTV6ShellB': 1, '_ZTV6Shell3': 1,
          '_ZTV5Named': 1, '_ZTV6Shell4': 1, '_ZTV6Shell5': 1, '_ZTV4Base': 3}


@unittest.skipIf(capstone is None or not shutil.which('aarch64-linux-gnu-as'), 'needs capstone and the assembler')
class BoundStores(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='bstore-')
        w = cls.where = {}
        tx.make_pe(os.path.join(cls.tmp, 'probe.exe'), lambda rd: tx.build(rd, w))
        B, M, S = w['Base'], w['Multi'], w['Side']
        code = {T(20): store(S), T(21): store(B), T(22): store(M), T(23): store(B, M), T(24): b'\xc3',
                T(25): store(S), T(26): store(S), T(27): store(B, M), T(28): store(S)}
        cls.exe = os.path.join(cls.tmp, 'Synthetic.exe')
        w2 = {}
        tx.make_pe(cls.exe, lambda rd: tx.build(rd, w2), code)
        assert w2 == w
        cls.exev = exevft.scan(cls.exe)
        src = os.path.join(cls.tmp, 'u.S')
        open(src, 'w').write(SRC)
        cls.obj = os.path.join(cls.tmp, 'u.o')
        subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', src, '-o', cls.obj], check=True)
        cls.at = {'factory': T(20), 'maker': T(21), 'conf1': T(22), 'two': T(23), 'none': T(24), 'conf2': T(25),
                  'named': T(26), 'diff': T(27), 'member': T(28)}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def native(self):
        return {a: dict(unit='u', sym=elfsyms.Sym(n, 0, 0, 'func', 'global', 1, '', False), variants={}, msvc=None)
                for n, a in self.at.items()}

    def run_pass(self, vmap=None):
        w = self.where
        vmap = {w['Base']: ('_ZTV4Base', 8, '/o/base.o', 'primary, the exe\'s RTTI class Base'),
                w['Tmpl']: ('_ZTV5Named', 8, '/o/n.o', 'primary, named')} if vmap is None else vmap
        alt, vstats = collections.defaultdict(list), collections.Counter()
        got = fnvlink.bound_store_pairs(self.exev, vmap, tc.FakeRtti(GROUPS), self.native(), self.exe, alt, vstats,
                                        self.tmp, {'u': self.obj})
        return got, vmap, alt, vstats

    def test_native_sequence(self):
        o = vtables.Obj(self.obj)
        self.assertEqual(membervt.vt_store_seq(o, 'factory'), [('_ZTV5Shell', 8)])
        self.assertEqual(membervt.vt_store_seq(o, 'maker'), [])
        self.assertEqual(membervt.vt_store_seq(o, 'maker', lambda c: membervt.vt_store_seq(o, c)),
                         [('_ZTV6Shell2', 8)])
        self.assertEqual(membervt.vt_store_seq(o, 'two'), [('_ZTV6ShellA', 8), ('_ZTV6ShellB', 8)])
        self.assertEqual(membervt.vt_store_seq(o, 'member'), [])
        self.assertEqual(membervt.vt_store_seq(o, 'nosuch'), [])

    def test_pairs(self):
        w = self.where
        got, vmap, alt, vst = self.run_pass()
        # factory: `new` + the shell's vptr, the exe stores Side (free): paired in vmap as a primary
        self.assertEqual(vmap[w['Side']][:2], ('_ZTV5Shell', 8))
        self.assertTrue(vmap[w['Side']][3].startswith('primary'))
        # maker: through the unbound constructor; Base has its own vtable: displaced
        self.assertIn(('_ZTV6Shell2', 8, '/o/_ZTV6Shell2.o', 'primary'), alt[w['Base']])
        self.assertEqual(vmap[w['Base']][0], '_ZTV4Base')
        # two: two stores on both sides, by position (ShellA <-> Base displaced, ShellB <-> Multi)
        self.assertIn(('_ZTV6ShellA', 8, '/o/_ZTV6ShellA.o', 'primary'), alt[w['Base']])
        self.assertEqual(vmap[w['Multi']][:2], ('_ZTV6ShellB', 8))
        # conf1 / conf2: Multi and Side for one shell: a conflict, unpaired
        self.assertNotIn(('_ZTV4Conf', 8), got)
        self.assertFalse(any(x[0] == '_ZTV4Conf' for zs in alt.values() for x in zs))
        # named: another pass paired it (Tmpl): left
        self.assertEqual(vmap[w['Tmpl']][0], '_ZTV5Named')
        self.assertNotIn(('_ZTV5Named', 8), got)
        # none (no exe store), diff (1 native / 2 exe stores, not a constructor), member (+8): nothing
        for z in ('_ZTV6Shell3', '_ZTV6Shell4', '_ZTV6Shell5'):
            self.assertNotIn((z, 8), got)
        self.assertEqual(set(got), {('_ZTV5Shell', 8), ('_ZTV6Shell2', 8), ('_ZTV6ShellA', 8), ('_ZTV6ShellB', 8)})
        self.assertEqual((vst['bstore-vt-paired'], vst['bstore-vt-paired-as-displaced'], vst['bstore-vt-conflict']),
                         (2, 2, 2))
        self.assertEqual((vst['bstore-vt-no-exe-store'], vst['bstore-vt-counts-differ']), (1, 1))
        rows = [r.rstrip('\n').split('\t') for r in open(os.path.join(self.tmp, 'bound_vftables.tsv'))][1:]
        self.assertIn(['_ZTV5Shell', '%08x' % T(20), '-', '1/1', '%08x' % w['Side'], 'paired'], rows)
        self.assertIn(['_ZTV6Shell4', '%08x' % T(27), '-', '1/2', '', 'counts differ'], rows)
        self.assertTrue(any(r[0] == '_ZTV4Conf' and r[5].startswith('conflict') for r in rows))

    def test_off(self):
        os.environ['FNVNX_BSTORE'] = '0'
        try:
            got, vmap, alt, vst = self.run_pass()
        finally:
            del os.environ['FNVNX_BSTORE']
        self.assertEqual((dict(got), dict(alt), len(vmap)), ({}, {}, 2))

    def test_prefix(self):
        V = exevft.Vft
        ev = {1: V('.?AVA@@', 0, [], [10, 11]), 2: V('.?AVB@@', 0, [], [10, 11, 12]), 3: V('.?AVC@@', 0, [], [10, 13])}
        self.assertTrue(fnvlink._prefix_ok(ev, {1, 2}))
        self.assertFalse(fnvlink._prefix_ok(ev, {1, 3}))
        self.assertFalse(fnvlink._prefix_ok(ev, {2, 3}))

    def test_vtfill_writes_the_shell(self):
        """The pairing feeds exe_vtable_copies: the shell factory stores (one placeholder slot, the exe's Side has
        two) is written as transform(exe Side)."""
        w = self.where
        d = tempfile.mkdtemp(dir=self.tmp)
        src = os.path.join(d, 'g.S')
        open(src, 'w').write('    .section .data.rel.ro._ZTV5Shell,"awG",%progbits,_ZTV5Shell,comdat\n'
                             '    .p2align 3\n    .weak _ZTV5Shell\n    .type _ZTV5Shell, %object\n_ZTV5Shell:\n'
                             '    .word 0\n    .word _ZTI5Shell\n    .word _ZN5Shell3v00Ev\n'
                             '    .size _ZTV5Shell, .-_ZTV5Shell\n')
        g = os.path.join(d, 'g.o')
        subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', src, '-o', g], check=True)
        rtti = types.SimpleNamespace(where={'_ZTV5Shell': (types.SimpleNamespace(path=g), False)},
                                     ents=lambda z: vtables.Obj(g).ents(z) if z == '_ZTV5Shell' else None)
        alt, vstats, vmap = collections.defaultdict(list), collections.Counter(), {}
        native = {T(20): self.native()[T(20)]}
        fnvlink.bound_store_pairs(self.exev, vmap, rtti, native, self.exe, alt, vstats, d, {'u': self.obj})
        self.assertEqual(vmap[w['Side']][:3], ('_ZTV5Shell', 8, g))
        sym = lambda n: elfsyms.Sym(n, 0, 0, 'func', 'global', 1, '.text', False)   # noqa: E731
        nat = {T(4): dict(unit='g', sym=sym('_ZN4Side1fEv'), variants={}, tier='def', msvc=None),
               T(5): dict(unit='g', sym=sym('_ZN4Side1gEv'), variants={}, tier='def', msvc=None)}
        have = [types.SimpleNamespace(obj=g, source='g')]
        bridges, traps = {}, {}
        os.environ['FNVNX_VTFILL'] = '1'
        try:
            st = fnvlink.exe_vtable_copies(self.exev, vmap, rtti, have, {}, traps, nat, bridges, d, None, self.exe,
                                           {}, alt, lambda n: None)
        finally:
            del os.environ['FNVNX_VTFILL']
        self.assertGreaterEqual(st['vtfill-tables'], 1, dict(st))
        ws = [x.split('.word ')[1] for x in bridges['zzvtext__ZTV5Shell'].split('\n') if '.word ' in x]
        self.assertEqual(ws[2:], ['__fnv_%08x' % T(4), '__fnv_%08x' % T(5)])


if __name__ == '__main__':
    unittest.main()
