#!/usr/bin/env python3
"""W23 (S5 round 6, link 97deacd38aec): native `delete` against a vtable in the exe's slot order.

The renderer constructor 0x00e754a0 is declared twice in the decomp: GraphicsRenderer (a virtual destructor) and
FUN_00e754a0 : RendererBase (`virtual void BaseVirtual();` where the exe has the scalar deleting destructor). The
class with the placeholder has no D1/D0, so exe_vtable_copies extended its one native slot with the exe's slots 1.. in
the exe's order: native slot 1 = exe slot 1 = NiRefObject::DeleteThis, whose body is `delete this` = a call of vftable
slot 0 with flags 1. GCC dispatches `delete renderer` (GraphicsRenderer::Create's failure path) through Itanium slot 1,
the D0 slot, so the call landed in DeleteThis, which does `delete this` through slot 1 again: a loop.

d0_slot_fix gives that slot the exe slot 0's D0 (what DeleteThis does in the exe) and moves nothing else, so callers
that number the slots as the exe does (the decomp's call-only placeholders) keep reaching the exe's slots. The test
uses hand-made classes, a synthetic PE32 and hand-assembled vtables (aarch64-linux-gnu-as, ILP32).

    python3 tools/link/test_d0slot.py
"""
import collections
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elfsyms   # noqa: E402
import exevft    # noqa: E402
import fnvlink   # noqa: E402
import test_exevft as tx   # noqa: E402  (the synthetic PE32 builder)

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def sym(name):
    return elfsyms.Sym(name, 0, 0, 'func', 'global', 1, '.text', False)


def nat(name, msvc=None, variants=None):
    return dict(unit='u.cpp', sym=sym(name), variants=variants or {}, tier='def', msvc=msvc)


A0, A1, A2, A3 = 0x00401000, 0x00401010, 0x00401020, 0x00401030     # ??_G, DeleteThis, two virtuals
DELETE_THIS = b'\x85\xc9\x74\x08\x8b\x01\x8b\x10\x6a\x01\xff\xd2\xc3'   # test ecx,ecx; jz; mov eax,[ecx]; ...; ret
DELETE_THIS_2 = b'\x8b\x01\x6a\x01\xff\x10\xc3'                          # mov eax,[ecx]; push 1; call [eax]; ret
SDD = b'\xf6\x44\x24\x04\x01\x74\x00\xc2\x04\x00'                        # a ??_G: test byte [esp+4],1 ... ret 4
OTHER = b'\x33\xc0\xc3'                                                  # xor eax, eax; ret


def asm_group(d, name, z, words):
    """A weak COMDAT vtable group `z`: offset-to-top 0, typeinfo, then the slot symbols (undefined externals)."""
    src = os.path.join(d, name + '.S')
    ws = '\n'.join('    .word %s' % w for w in words)
    with open(src, 'w') as f:
        f.write('    .section .data.rel.ro.%s,"awG",%%progbits,%s,comdat\n    .p2align 3\n    .weak %s\n'
                '    .type %s, %%object\n%s:\n    .word 0\n    .word _ZTI%s\n%s\n    .size %s, .-%s\n' %
                (z, z, z, z, z, z[4:], ws, z, z))
    obj = os.path.join(d, name + '.o')
    r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', src, '-o', obj], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return obj


def run(d, exe, groups, exev, native, per_obj=None):
    """exe_vtable_copies over hand-assembled groups: groups {z: (object file, exe vftable)}."""
    vmap = {v: (z, 8, obj, 'primary') for z, (obj, v) in groups.items()}
    have = [types.SimpleNamespace(obj=obj, source=z) for z, (obj, v) in groups.items()]
    bridges, traps = {}, {}
    out = tempfile.mkdtemp(dir=d)
    st = fnvlink.exe_vtable_copies(exev, vmap, types.SimpleNamespace(where={}), have, per_obj or {}, traps, native,
                                   bridges, out, None, exe, {}, None, lambda n: None)
    return st, bridges, traps, vmap


def words(bridges, z):
    return [l.split('.word ')[1] for l in bridges['zzvtext_' + z].split('\n') if '.word ' in l]


def main():
    tmp = tempfile.mkdtemp(prefix='d0slot-')
    try:
        exe = os.path.join(tmp, 'S.exe')
        code = {A0: SDD, A1: DELETE_THIS, A2: OTHER, A3: OTHER, 0x00401040: DELETE_THIS_2, 0x00401050: SDD,
                0x00401100: OTHER, 0x00401060: b'\x8b\x01\x6a\x00\xff\x10\xc3',         # flags 0: not a delete
                0x00401070: b'\x8b\x01\x6a\x01\xff\x10\x90\xc3'}     # a call, then more code: not `delete this` only
        tx.make_pe(exe, lambda rd: None, code)
        check(exevft.delete_this(exe, A1) and exevft.delete_this(exe, 0x00401040),
              'delete_this: `delete this` with and without the null test / the edx form')
        check(not any(exevft.delete_this(exe, a) for a in (A0, A2, 0x00401060, 0x00401070, 0x00300000)),
              'delete_this: a ??_G, a constant leaf, flags 0, a longer body and an unmapped address are not')

        d0, d1 = sym('_ZN1AD0Ev'), sym('_ZN1AD1Ev')
        native = {A0: dict(unit='u.cpp', sym=d0, variants={'D0': d0, 'D1': d1}, tier='def', msvc='??_GA@@UAEPAXI@Z'),
                  A1: nat('_ZN11NiRefObject10DeleteThisEv'), A2: nat('_ZN1A1fEv'), A3: nat('_ZN1A1gEv'),
                  0x00401050: nat('_ZN1B12FUN_00401050Ej', msvc='??_GB@@UAEPAXI@Z'), 0x00401100: nat('_ZN1A1hEv')}
        V = {n: 0x01010000 + 0x100 * i for i, n in enumerate('ABCDEF')}
        exev = {
            V['A']: exevft.Vft('.?AVFoo@@', 0, [], [A0, A1, A2, A3]),           # the loop shape: ??_G, DeleteThis, f, g
            V['B']: exevft.Vft('.?AVBar@@', 0, [], [A0, A1, A2]),               # placeholders v00 v01 v02 (n = 3)
            V['C']: exevft.Vft('.?AVBaz@@', 0, [], [A0, A1, A2]),               # a real D1/D0 pair: already right
            V['D']: exevft.Vft('.?AVQux@@', 0, [], [A0, A2, A3]),               # exe slot 1 is not DeleteThis
            V['E']: exevft.Vft('.?AVFlg@@', 0, [], [0x00401050, 0x00401040, A2]),   # a (this, flags) ??_G
            V['F']: exevft.Vft('.?AVNoD@@', 0, [], [0x00401100, A1, A3]),       # slot 0 is not a destructor
        }
        if not shutil.which('aarch64-linux-gnu-as'):
            print('skip the assembled-vtable checks: no aarch64-linux-gnu-as')
        else:
            g = {
                '_ZTV3Foo': (asm_group(tmp, 'a', '_ZTV3Foo', ['_ZN3Foo11BaseVirtualEv']), V['A']),
                '_ZTV3Bar': (asm_group(tmp, 'b', '_ZTV3Bar', ['__fnv_00401000', '__fnv_00401010', '__fnv_00401020']),
                             V['B']),
                '_ZTV3Baz': (asm_group(tmp, 'c', '_ZTV3Baz', ['_ZN3BazD1Ev', '_ZN3BazD0Ev']), V['C']),
                '_ZTV3Qux': (asm_group(tmp, 'e', '_ZTV3Qux', ['__fnv_00401000', '__fnv_00401020']), V['D']),
                '_ZTV3Flg': (asm_group(tmp, 'f', '_ZTV3Flg', ['_ZN3Flg3v00Ev', '_ZN3Flg3v01Ev', '_ZN3Flg3v02Ev']),
                             V['E']),
                '_ZTV3NoD': (asm_group(tmp, 'g', '_ZTV3NoD', ['_ZN3NoD3v00Ev', '_ZN3NoD3v01Ev']), V['F']),
            }
            st, bridges, traps, vmap = run(tmp, exe, g, exev, native)
            # 1. the loop shape: one placeholder slot, extended from the exe in its order
            w = words(bridges, '_ZTV3Foo')
            check(w[:3] == ['0x0', '_ZTI3Foo', '_ZN3Foo11BaseVirtualEv'], 'Foo: offset-to-top, typeinfo, slot 0 as declared')
            check(w[3] == '__fnv_00401000', 'Foo: native slot 1 is the D0 of the ??_G (was DeleteThis -> a loop): %s' % w[3])
            check(w[4] == '__fnv_00401020' and w[5] == '__fnv_00401030',
                  'Foo: exe slots 2 and 3 stay at native 2 and 3 (callers numbering as the exe still reach them)')
            check(len(w) == 6 and '__fnv_00401010' not in w, 'Foo: DeleteThis is no longer in the table')
            # 2. placeholders v00 v01 v02 all bound by the slot fill (exe order): slot 1 only
            w = words(bridges, '_ZTV3Bar')
            check(w[2:] == ['__fnv_00401000', '__fnv_00401000', '__fnv_00401020'],
                  'Bar: a bound slot 1 (DeleteThis) becomes the D0; slots 0 and 2 are unchanged: %s' % w[2:])
            # 3. controls
            check(words(bridges, '_ZTV3Baz')[2:5] == ['_ZN3BazD1Ev', '_ZN3BazD0Ev', '__fnv_00401010'],
                  'Baz: a real D1/D0 pair is kept as it was (exe slot 1 follows at native 2)')
            check(words(bridges, '_ZTV3Qux')[3] == '__fnv_00401020', 'Qux: exe slot 1 is no DeleteThis: left')
            check(words(bridges, '_ZTV3NoD')[3] == '_ZN3NoD3v01Ev', 'NoD: exe slot 0 is no destructor: left')
            w = words(bridges, '_ZTV3Flg')
            check(w[3] == '__fnvnx_vflag_00401050_1' and 'mov w1, #1' in bridges['__fnvnx_vflag_00401050_1'],
                  'a (this, flags) ??_G: the D0 is the flags-1 stub: %s' % w[3])
            check(st['d0slot-candidates'] == 3 and st['d0slot-fixed'] == 3,
                  'counted: 3 tables fixed: %s' % dict(st))
            check(all(vmap[v][2] == fnvlink.VTEXT_OBJ for v in (V['A'], V['B'], V['E'])),
                  'the fixed tables are the bridge copies the V rows name')
            os.environ['FNVNX_D0SLOT'] = '0'
            try:
                st0, br0, _, _ = run(tmp, exe, g, exev, native)
            finally:
                del os.environ['FNVNX_D0SLOT']
            check(words(br0, '_ZTV3Foo')[3] == '__fnv_00401010' and not st0['d0slot-fixed'],
                  'FNVNX_D0SLOT=0 gives the exe-order table back (DeleteThis at slot 1)')
            # the assembled copy carries the D0 where GCC's `delete` calls
            text = ''.join(bridges[k] for k in sorted(bridges) if isinstance(bridges[k], str))
            src = os.path.join(tmp, 'all.S')
            open(src, 'w').write('    .text\n' + text + fnvlink.emit_traps(traps))
            r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', src, '-o', os.path.join(tmp, 'all.o')],
                               capture_output=True, text=True)
            check(r.returncode == 0, 'the copies assemble %s' % r.stderr[:160])

        # 4. D26 (c): the same exe vftable with no native vtable gets a minus-one copy, D0 already at slot 1, and a V row
        bridges, traps = {}, {}
        vst = collections.Counter()
        out = tempfile.mkdtemp(dir=tmp)
        got, st = fnvlink.minus_one_copies({V['A']: 0}, exev, native, {}, exe, lambda n: None, bridges, traps, out)
        c = words(bridges, '') if False else [l.split('.word ')[1] for l in bridges['zzvmo_%08x' % V['A']].split('\n')
                                              if '.word ' in l]
        check(c[2:5] == ['__fnv_00401000_D1', '__fnv_00401000', '__fnv_00401010'],
              'D26 (c) copy: D1/D0 at 0/1, DeleteThis at native 2 (exe slot 1, one up): %s' % c[2:5])
        rows = fnvlink.vmo_vrows(got, {}, exev)
        check(rows[V['A']][:3] == ('__fnvnx_vmo_%08x' % V['A'], 8, fnvlink.VTEXT_OBJ), 'and its V row at address point 8')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
