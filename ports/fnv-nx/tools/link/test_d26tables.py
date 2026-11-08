#!/usr/bin/env python3
"""W30 (S5 boot, link 3f7a6c35): a table without a D1/D0 pair whose callers are minus-one (D26) is rebuilt in D26's layout.

exe_vtable_copies used to leave a native vtable made from the exe vftable in the exe's slot order (slot k = exe slot k)
when the declaration had no virtual destructor. Callers compiled against D26 (b)'s `virtual ~C()` dispatch minus-one
(D1/D0 at 0/1, exe slot k at k + 1), so BSNiAllocator::Allocate ran an empty slot. Hand-made classes, a synthetic PE32
and hand-assembled vtables (aarch64-linux-gnu-as, ILP32).

    python3 tools/link/test_d26tables.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import exevft   # noqa: E402
import fnvlink  # noqa: E402
import test_exevft as tx   # noqa: E402
import test_d0slot as t0   # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


A0, A1, A2, AF = 0x00401000, 0x00401010, 0x00401020, 0x00401200   # ??_G, f, g, a stdcall free function
OTHER = t0.OTHER


def run(tmp, exe, groups, exev, native, listed=(), called=()):
    vmap = {v: (z, 8, obj, 'primary') for z, (obj, v) in groups.items()}
    have = [types.SimpleNamespace(obj=obj, source=z) for z, (obj, v) in groups.items()]
    bridges, traps = {}, {}
    out = tempfile.mkdtemp(dir=tmp)
    st = fnvlink.exe_vtable_copies(exev, vmap, types.SimpleNamespace(where={}), have, {}, traps, native, bridges, out,
                                   None, exe, {}, None, lambda n: None, set(listed), lambda c: set(c) & set(called))
    return st, bridges, traps


def words(bridges, z):
    return [l.split('.word ')[1] for l in bridges['zzvtext_' + z].split('\n') if '.word ' in l]


def main():
    tmp = tempfile.mkdtemp(prefix='d26t-')
    try:
        if not shutil.which('aarch64-linux-gnu-as'):
            print('skip: no aarch64-linux-gnu-as')
            return 0
        exe = os.path.join(tmp, 'S.exe')
        tx.make_pe(exe, lambda rd: None, {A0: t0.SDD, A1: OTHER, A2: OTHER, AF: OTHER})
        d0, d1 = t0.sym('_ZN1AD0Ev'), t0.sym('_ZN1AD1Ev')
        nat = t0.nat
        native = {A0: dict(unit='u.cpp', sym=d0, variants={'D0': d0, 'D1': d1}, tier='def', msvc='??_GA@@UAEPAXI@Z'),
                  A1: nat('_ZN3Foo1fEv'), A2: nat('_ZN3Foo1gEv'),
                  AF: nat('_Z12FUN_00401200Pvi', msvc='?FUN_00401200@@YGXPAXH@Z')}   # stdcall free function
        V = {n: 0x01010000 + 0x100 * i for i, n in enumerate('ABCDE')}
        exev = {V['A']: exevft.Vft('.?AVFoo@@', 0, [], [A0, A1, A2]),        # call-only placeholder v00
                V['B']: exevft.Vft('.?AVAlloc@@', 0, [], [A0, AF, A2, AF]),   # named slot 0, free fn in slots 1 and 3
                V['C']: exevft.Vft('.?AVUsed@@', 0, [], [A0, A1, A2]),       # placeholder, called by name
                V['D']: exevft.Vft('.?AVPlain@@', 0, [], [A0, A1, A2]),      # named slot 0, nothing says minus-one
                V['E']: exevft.Vft('.?AVPair@@', 0, [], [A0, A1, A2])}       # a real pair: already right
        zb = '_ZTV26FUN_00401200_BSNiAllocator'
        g = {'_ZTV3Foo': (t0.asm_group(tmp, 'a', '_ZTV3Foo', ['_ZN3Foo3v00Ev', '_ZN3Foo3v01Ev', '_ZN3Foo3v02Ev']), V['A']),
             zb: (t0.asm_group(tmp, 'b', zb, ['_ZN26FUN_00401200_BSNiAllocator7DestroyEj', '__fnv_00401200',
                                                '__fnv_00401020', '__fnv_00401200']), V['B']),
             '_ZTV4Used': (t0.asm_group(tmp, 'c', '_ZTV4Used', ['_ZN4Used3v00Ev', '_ZN4Used3v01Ev', '_ZN4Used3v02Ev']), V['C']),
             '_ZTV5Plain': (t0.asm_group(tmp, 'd', '_ZTV5Plain', ['_ZN5Plain7DestroyEj', '__fnv_00401010', '__fnv_00401020']),
                            V['D']),
             '_ZTV4Pair': (t0.asm_group(tmp, 'e', '_ZTV4Pair', ['_ZN4PairD1Ev', '_ZN4PairD0Ev', '__fnv_00401010', '__fnv_00401020']), V['E'])}
        st, br, traps = run(tmp, exe, g, exev, native, listed={V['B']}, called={'_ZN4Used3v00Ev'})
        w = words(br, '_ZTV3Foo')
        check(w[:2] == ['0x0', '_ZTI3Foo'] and w[2:] == ['__fnv_00401000_D1', '__fnv_00401000', '__fnv_00401010',
                                                         '__fnv_00401020'],
              'placeholder v00 (no pair): offset-to-top, typeinfo, D1, D0, then exe slot k at k + 1: %s' % w)
        w = words(br, zb)
        check(w[2:4] == ['__fnv_00401000_D1', '__fnv_00401000'] and w[4] == '__fnvnx_vfree_00401200' and
              w[5] == '__fnv_00401020' and w[6] == '__fnvnx_vfree_00401200',
              'D22-listed class: pair, free-function slots through the this-drop thunk: %s' % w[2:])
        check('mov x0, x1' in br['__fnvnx_vfree_00401200'] and 'b __fnv_00401200' in br['__fnvnx_vfree_00401200'],
              'the thunk shifts the arguments down and tail-calls the free function')
        check('zzvtext__ZTV4Used' not in br, 'a placeholder called by name stays in the exe order')
        check('zzvtext__ZTV5Plain' not in br, 'a named slot 0 with no evidence stays as it is (no copy)')
        check('zzvtext__ZTV4Pair' not in br, 'a table with a pair is left alone')
        check(st['d26-minus-one'] == 2 and st['d26-minus-one-d22-list'] == 1 and
              st['d26-minus-one-callonly-placeholder'] == 1 and st['exe-order-no-evidence'] == 2
              and st['d26-minus-one-BSNiAllocator'] == 1, 'counted: %s' % dict(st))
        os.environ['FNVNX_D26T'] = '0'
        try:
            _, br0, _ = run(tmp, exe, g, exev, native, listed={V['B']})
        finally:
            del os.environ['FNVNX_D26T']
        check('zzvtext__ZTV3Foo' not in br0, 'FNVNX_D26T=0 turns the rebuild off')
        text = ''.join(br[k] for k in sorted(br) if isinstance(br[k], str))
        src = os.path.join(tmp, 'all.S')
        open(src, 'w').write('    .text\n' + text + fnvlink.emit_traps(traps))
        r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', src, '-o', os.path.join(tmp, 'all.o')],
                           capture_output=True, text=True)
        check(r.returncode == 0, 'the tables and the this-drop thunk assemble for ILP32 %s' % r.stderr[:200])
        # D26 (c): the copy and its V row for a vftable no native vtable stands for
        b2, t2 = {}, {}
        got, _ = fnvlink.minus_one_copies({V['A']: 0}, exev, native, {}, exe, lambda n: None, b2, t2,
                                          tempfile.mkdtemp(dir=tmp))
        rows = fnvlink.vmo_vrows(got, {}, exev)
        check(V['A'] in got and rows[V['A']][:3] == ('__fnvnx_vmo_%08x' % V['A'], 8, fnvlink.VTEXT_OBJ),
              'D26 (c): the minus-one copy and its V row are present')
        # the D22 list reader: names and addresses only
        d = os.path.join(tmp, 'recon')
        os.makedirs(os.path.join(d, 'x'))
        open(os.path.join(d, 'x', 'd22.json'), 'w').write(
            '{"format":"fnvnx-d22/1","classes":[{"class":"BSNiAllocator","members":["Destroy"],"vftables":["010a2528"]}]}')
        lst, n = fnvlink.load_d22_list(d)
        check(lst == {0x010a2528} and n == 1 and fnvlink.load_d22_list(None) == (set(), 0), 'load_d22_list reads d22.json')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
