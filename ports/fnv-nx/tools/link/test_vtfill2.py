#!/usr/bin/env python3
"""W46: VTFILL2, a vtable group with secondary bases written as transform(exe) vtable by vtable.

Foo : P, B1 (at +8), B2 (at +16), hand-assembled (aarch64-linux-gnu-as, ILP32) against a synthetic PE32:
* primary: two native placeholders; the exe's primary is ??_G, f, g (minus-one at 0): D1, D0, f, g, one word
  longer than the native vtable, so the group grows through slotfill2.shift_plan and both secondary address points
  move down by 8 (the V rows follow, vtshift.tsv records it);
* B1 {for B1} at +8 (five native slots): the exe slots are `sub ecx, 8; jmp ??_G` (the destructor slot), `sub ecx, 8; jmp f`, a base
  function, and a function MSVC compiled with `this` at B1 (msvc_entry): `__fnvnx_vadjd_<a>_0/1`, `__fnvnx_vadj_<a>`,
  `__fnv_<b>`, `__fnvnx_msvc_<m>`;
* B2 {for B2} at +16, the last vtable: GCC's own `_ZThn16_` D1 / D0 thunks stay, a base function, and one exe slot
  more than the native vtable, which grows at the group's end;
* classlayout's gate over the assembled result: every slot of the three vtables agrees with the exe;
* B1 one slot short: it grows in place and B2's address point moves by 12 (two vtshift.tsv steps);
* without V rows for the secondaries (a displaced copy) they pair with the exe vftables by COL offset; a V row that
  calls the primary vtable `secondary {for X} at +0` (TESWorldSpace) is filled too;
* D26 (c) copies follow classlayout: an exe-order class (hkThreadMemory 0x010cacd0) in the exe's order;
* slotfill2's GOT-load scan follows `mov wD, wN` and TLS descriptor calls (TESWorldSpace, bhkCharacterController).
FNVNX_VTFILL2=0 leaves the group to the builder (vtfill-skipped-secondary), FNVNX_VMOCL=0 the D26 (c) copies.

    python3 tools/link/test_vtfill2.py
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
import classlayout   # noqa: E402
import elfsyms       # noqa: E402
import exevft        # noqa: E402
import fnvlink       # noqa: E402
import test_exevft as tx   # noqa: E402  (the synthetic PE32 builder)

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def nat(name, variants=None, msvc_entry=None):
    s = elfsyms.Sym(name, 0, 0, 'func', 'global', 1, '.text', False)
    d = dict(unit='u.cpp', sym=s, variants=variants or {}, tier='def', msvc=None)
    if msvc_entry:
        d['msvc_entry'] = msvc_entry
    return d


SDD = b'\xf6\x44\x24\x04\x01\x74\x00\xc2\x04\x00'   # a ??_G: test byte [esp+4],1 ... ret 4
OTHER = b'\x33\xc0\xc3'                              # xor eax, eax; ret
A0, AF, AG, B3, M4, B5, B6 = 0x00401000, 0x00401010, 0x00401020, 0x00401030, 0x00401040, 0x00401050, 0x00401060
T0, T1, T2 = 0x00401100, 0x00401110, 0x00401120     # adjustor thunks


def adj(at, n, to):
    """`sub ecx, n; jmp to` at address at."""
    return bytes([0x83, 0xe9, n, 0xe9]) + ((to - (at + 8)) & 0xffffffff).to_bytes(4, 'little')


def asm(d, name, text):
    src = os.path.join(d, name + '.S')
    with open(src, 'w') as f:
        f.write(text)
    obj = os.path.join(d, name + '.o')
    r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', src, '-o', obj], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return obj


def group(d, z, blocks):
    """A weak COMDAT group: blocks [(offset-to-top, [slot symbols])]."""
    ws = []
    for ott, syms in blocks:
        ws += ['    .word %d' % ott, '    .word _ZTI%s' % z[4:]] + ['    .word %s' % s for s in syms]
    return asm(d, 'g', '    .section .data.rel.ro.%s,"awG",%%progbits,%s,comdat\n    .p2align 3\n    .weak %s\n'
                       '    .type %s, %%object\n%s:\n%s\n    .size %s, .-%s\n' % (z, z, z, z, z, '\n'.join(ws), z, z))


def words(bridges, z):
    return [l.split('.word ')[1] for l in bridges['zzvtext_' + z].split('\n') if '.word ' in l]


def main():
    if not shutil.which('aarch64-linux-gnu-as'):
        print('skip: no aarch64-linux-gnu-as')
        return 0
    # slotfill2's shift: the GOT load's uses through a register copy and across a TLS descriptor call
    import slotfill2

    def code(*ws):
        return b''.join(w.to_bytes(4, 'little') for w in ws)
    RET = 0xd65f03c0
    check(slotfill2._add_uses(code(0xb9400021, 0x2a0103e0, 0x11002021, 0x11006003, RET), 0, 0) == [(8, 8), (12, 0x18)],
          'shift: `mov w0, w1` makes w0 a holder (TESWorldSpace ~FUN_00582e30)')
    check(slotfill2._add_uses(code(0xb9400021, 0xb9400002, 0x11000000, 0xd63f0040, 0x11007022, RET), 0, 0) ==
          [(16, 0x1c)], 'shift: w1 lives across a TLS descriptor call (bhkCharacterController ~FUN_00c705c0)')
    check(slotfill2._add_uses(code(0xb9400021, 0xd63f0040, 0x11007022, RET), 0, 0) is None,
          'shift: any other blr while the holder is live is not followed')
    tmp = tempfile.mkdtemp(prefix='vtfill2-')
    try:
        exe = os.path.join(tmp, 'S.exe')
        code = {A0: SDD, AF: OTHER, AG: OTHER, B3: OTHER, M4: OTHER, B5: OTHER, B6: OTHER,
                T0: adj(T0, 8, A0), T1: adj(T1, 8, AF), T2: adj(T2, 16, A0)}
        tx.make_pe(exe, lambda rd: None, code)
        check(classlayout.adjustor_target(exe, T1) == AF and exevft.thunk(exe, T1) == (8, AF),
              'the synthetic adjustor thunk: sub ecx, 8; jmp f')
        z = '_ZTV3Foo'
        d0, d1 = elfsyms.Sym('_ZN3FooD0Ev', 0, 0, 'func', 'global', 1, '.text', False), \
            elfsyms.Sym('_ZN3FooD1Ev', 0, 0, 'func', 'global', 1, '.text', False)
        native = {A0: dict(unit='u.cpp', sym=d0, variants={'D0': d0, 'D1': d1}, tier='def', msvc=None),
                  AF: nat('_ZN3Foo1fEv'), AG: nat('_ZN3Foo1gEv'), B3: nat('_ZN2B11bEv'),
                  M4: nat('_ZN3Foo1mEv', msvc_entry=('_ZThn8_N3Foo1mEv', 8)), B5: nat('_ZN2B21cEv'),
                  B6: nat('_ZN2B21eEv')}
        VP, V1, V2 = 0x01010000, 0x01010100, 0x01010200
        exev = {VP: exevft.Vft('.?AVFoo@@', 0, [], [A0, AF, AG]),
                V1: exevft.Vft('.?AVFoo@@', 8, [], [T0, T1, B3, M4]),
                V2: exevft.Vft('.?AVFoo@@', 16, [], [T2, B5, B6])}
        obj = group(tmp, z, [(0, ['_ZN3Foo3v00Ev', '_ZN3Foo3v01Ev']),
                             (-8, ['_ZN2B13v00Ev', '_ZN2B13v01Ev', '_ZN2B13v02Ev', '_ZN2B13v03Ev', '_ZN2B13v04Ev']),
                             (-16, ['_ZThn16_N3FooD1Ev', '_ZThn16_N3FooD0Ev', '_ZN2B21cEv'])])
        ap1, ap2 = 8 + 4 * 2 + 8, 8 + 4 * 2 + 8 + 4 * 5 + 8   # 24, 52

        def run(env):
            os.environ.update(env)
            try:
                vmap = {VP: (z, 8, obj, 'primary'), V1: (z, ap1, obj, 'secondary {for B1} at +8'),
                        V2: (z, ap2, obj, 'secondary {for B2} at +16')}
                have = [types.SimpleNamespace(obj=obj, source=z)]
                bridges, traps = {}, {}
                out = tempfile.mkdtemp(dir=tmp)
                st = fnvlink.exe_vtable_copies(exev, vmap, types.SimpleNamespace(where={}), have, {}, traps, native,
                                               bridges, out, None, exe, {}, None, lambda n: None)
            finally:
                for k in env:
                    del os.environ[k]
            return st, bridges, traps, vmap, out

        st, br, traps, vmap, out = run({})
        w = words(br, z)
        prim = ['0x0', '_ZTI3Foo', '__fnv_00401000_D1', '__fnv_00401000', '__fnv_00401010', '__fnv_00401020']
        sec1 = ['0xfffffff8', '_ZTI3Foo', '__fnvnx_vadjd_00401100_0', '__fnvnx_vadjd_00401100_1',
                '__fnvnx_vadj_00401110', '__fnv_00401030', '__fnvnx_msvc_00401040']
        sec2 = ['0xfffffff0', '_ZTI3Foo', '_ZThn16_N3FooD1Ev', '_ZThn16_N3FooD0Ev', '__fnv_00401050', '__fnv_00401060']
        check(w == prim + sec1 + sec2, 'VTFILL2: primary, B1 and B2 are transform(exe): %s' % w)
        check(st['vtfill2-tables'] == 1 and st['vtfill2-primary-shifted'] == 1 and st['vtfill2-last-extended'] == 1,
              'VTFILL2: counted (shifted primary, extended last vtable): %s' % {k: v for k, v in st.items()
                                                                                if 'vtfill' in k})
        check(vmap[VP][1] == 8 and vmap[V1][1] == ap1 + 8 and vmap[V2][1] == ap2 + 8 and
              all(vmap[v][2] == fnvlink.VTEXT_OBJ for v in (VP, V1, V2)),
              'VTFILL2: the V rows follow the shift (+8) and name the bridge copy: %s' % vmap)
        vsh = [l.split('\t') for l in open(os.path.join(out, 'vtshift.tsv')) if not l.startswith('#')]
        check(vsh == [[z, '16', '8\n']], 'VTFILL2: vtshift.tsv records the shift from the primary end: %s' % vsh)
        check('sub w0, w0, #8' in br['__fnvnx_vadjd_00401100_1'] and 'b __fnv_00401000' in br['__fnvnx_vadjd_00401100_1']
              and 'b __fnv_00401010' in br['__fnvnx_vadj_00401110'],
              'VTFILL2: the adjustor stubs subtract 8 in front of the D0 / f')

        # the gate over the assembled result
        link = os.path.join(tmp, 'link')
        os.makedirs(os.path.join(link, 'out'))
        os.makedirs(os.path.join(link, 'fixed'))
        asm(os.path.join(link, 'fixed'), 'zz_bridges', ''.join(br.values()))
        with open(os.path.join(link, 'out', 'addrmap.tsv'), 'w') as f:
            for v, (z_, ap_, p_, note_) in sorted(vmap.items()):
                f.write('%08x\tV\t%s\t%s\t%s\t%s\t%d\t\t\n' % (v, z_, z_, p_, note_, ap_))
        lay = {r['vftable']: r for r in classlayout.build(exe, exev)}
        check(lay[V1]['kind'] == 'minus-one' and lay[V1]['dtor_slot'] == 0, 'classlayout: B1 {for B1} minus-one at 0')
        gst, bad = classlayout.gate(link, exe, lay, exev)
        check(gst['gated'] == 3 and gst['mismatch-slots'] == 0 and not gst['slot-unmapped'],
              'gate: 3 native tables, no bad or unmapped slot: %s %s' % (dict(gst), bad))

        # the gate on the builder's result (VTFILL2 off) has bad slots: the measure sees the difference
        st0, br0, _, vmap0, _ = run({'FNVNX_VTFILL2': '0'})
        check(st0['vtfill-skipped-secondary'] == 1 and not st0['vtfill2-tables'] and
              ('zzvtext_' + z not in br0 or words(br0, z) != w),
              'FNVNX_VTFILL2=0: the group is left to the builder: %s' % {k: v for k, v in st0.items() if 'vtfill' in k})
        st1, br1, _, _, _ = run({'FNVNX_VTFILL': '0'})
        check(not any('vtfill' in k for k in st1), 'FNVNX_VTFILL=0: nothing of VTFILL')

        # B1 one native slot short (a vtable before the last grows): B2's address point moves by 8 + 4
        obj_m = group(tmp, '_ZTV3Foo', [(0, ['_ZN3Foo3v00Ev', '_ZN3Foo3v01Ev']),
                                        (-8, ['_ZN2B13v00Ev', '_ZN2B13v01Ev', '_ZN2B13v02Ev', '_ZN2B13v03Ev']),
                                        (-16, ['_ZThn16_N3FooD1Ev', '_ZThn16_N3FooD0Ev', '_ZN2B21cEv'])])
        ap2m = ap2 - 4
        vmap_m = {VP: (z, 8, obj_m, 'primary'), V1: (z, ap1, obj_m, 'secondary {for B1} at +8'),
                  V2: (z, ap2m, obj_m, 'secondary {for B2} at +16')}
        out_m = tempfile.mkdtemp(dir=tmp)
        br_m = {}
        st_m = fnvlink.exe_vtable_copies(exev, vmap_m, types.SimpleNamespace(where={}),
                                         [types.SimpleNamespace(obj=obj_m, source=z)], {}, {}, native, br_m, out_m,
                                         None, exe, {}, None, lambda n: None)
        vsh_m = [l.rstrip('\n').split('\t') for l in open(os.path.join(out_m, 'vtshift.tsv')) if not l.startswith('#')]
        check(words(br_m, z) == w and vmap_m[V1][1] == ap1 + 8 and vmap_m[V2][1] == ap2m + 12 and
              vsh_m == [[z, '16', '8'], [z, str(ap1 + 16), '4']] and st_m['vtfill2-secondary-shifted'] == 1,
              'VTFILL2: a short B1 grows in place, B2 moves by 12, two vtshift steps: %s %s' % (vsh_m, vmap_m))
        check(slotfill2.shifted(ap2m, [(16, 8), (ap1 + 16, 4)]) == ap2m + 12 and
              slotfill2.moved(ap1, [(16, 8), (ap1 + 16, 4)]) == ap1 + 8, 'slotfill2.moved: steps add up')
        os.rename(obj_m, obj_m + '.m')   # restore the five-slot object for the cases below
        obj = group(tmp, z, [(0, ['_ZN3Foo3v00Ev', '_ZN3Foo3v01Ev']),
                             (-8, ['_ZN2B13v00Ev', '_ZN2B13v01Ev', '_ZN2B13v02Ev', '_ZN2B13v03Ev', '_ZN2B13v04Ev']),
                             (-16, ['_ZThn16_N3FooD1Ev', '_ZThn16_N3FooD0Ev', '_ZN2B21cEv'])])

        # a displaced copy (no V row for its secondary vtables): the class's exe vftable at each COL offset
        vmap3 = {VP: (z, 8, obj, 'primary')}
        out3 = tempfile.mkdtemp(dir=tmp)
        br3 = {}
        st3 = fnvlink.exe_vtable_copies(exev, vmap3, types.SimpleNamespace(where={}),
                                        [types.SimpleNamespace(obj=obj, source=z)], {}, {}, native, br3, out3, None,
                                        exe, {}, None, lambda n: None)
        check(words(br3, z) == w and st3['vtfill2-secondary-by-offset'] == 2,
              'VTFILL2: without V rows the secondaries pair by COL offset: %s' % {k: v for k, v in st3.items()
                                                                                    if 'vtfill2' in k})

        # a `secondary {for X} at +0` V row on the primary vtable (TESWorldSpace's _ZTV12FUN_00582e30): VTFILL takes it
        vmap4 = {VP: (z, 8, obj, 'secondary {for P} at +0'), V1: (z, ap1, obj, 'secondary {for B1} at +8'),
                 V2: (z, ap2, obj, 'secondary {for B2} at +16')}
        br4 = {}
        st4 = fnvlink.exe_vtable_copies(exev, vmap4, types.SimpleNamespace(where={}),
                                        [types.SimpleNamespace(obj=obj, source=z)], {}, {}, native, br4,
                                        tempfile.mkdtemp(dir=tmp), None, exe, {}, None, lambda n: None)
        check(words(br4, z) == w and st4['vtfill2-primary-at-0-rows'] == 1 and vmap4[V2][1] == ap2 + 8,
              'VTFILL2: a primary vtable the V row calls `secondary ... at +0` is filled too')

        # D26 (c) copies follow classlayout (hkThreadMemory 0x010cacd0: exe-order, the minus-one copy moved slot 3)
        VX = 0x01010300
        exev[VX] = exevft.Vft('.?AVHk@@', 0, [], [AF, AG, B3, M4])
        lay[VX] = dict(kind='exe-order', dtor_slot=-1)
        b5, t5 = {}, {}
        got, st5 = fnvlink.minus_one_copies({VX: 0, VP: 0}, exev, native, {}, exe, lambda n: None, b5, t5,
                                            tempfile.mkdtemp(dir=tmp), lay)
        wx = [l.split('.word ')[1] for l in b5['zzvmo_%08x' % VX].split('\n') if '.word ' in l]
        check(wx == ['0x0', '0', '__fnv_00401010', '__fnv_00401020', '__fnv_00401030', '__fnv_00401040'],
              'vmo: an exe-order class is copied in the exe order: %s' % wx)
        wp = [l.split('.word ')[1] for l in b5['zzvmo_%08x' % VP].split('\n') if '.word ' in l]
        check(wp == ['0x0', '0', '__fnv_00401000_D1', '__fnv_00401000', '__fnv_00401010', '__fnv_00401020'],
              'vmo: minus-one at 0 stays the D26 (c) copy: %s' % wp)
        os.environ['FNVNX_VMOCL'] = '0'
        try:
            b6 = {}
            fnvlink.minus_one_copies({VX: 0}, exev, native, {}, exe, lambda n: None, b6, {}, tempfile.mkdtemp(dir=tmp),
                                     lay)
        finally:
            del os.environ['FNVNX_VMOCL']
        check('vtrap' in [l for l in b6['zzvmo_%08x' % VX].split('\n') if '.word ' in l][2],
              'FNVNX_VMOCL=0: the minus-one copy as before (a D1 trap at slot 0)')
        del exev[VX]

        # R4's binding file names a slot that would be a trap (secondary vtables too)
        b7, s7 = {}, collections.Counter()
        f7 = fnvlink.vtfill_block([0x00401070, 0x00401080], [None, None], z, 6, 8, native, {}, exe, None, b7, {}, s7,
                                  {0x00401070: ('adj', '', '__fnv_00401010', 8), 0x00401080: ('fn', '_ZN2B11kEv', '', 0)})
        check(f7 == ['__fnvnx_vadj_00401070', '_ZN2B11kEv'] and 'sub w0, w0, #8' in b7['__fnvnx_vadj_00401070'] and
              s7['vtfill2-vtbind-adj'] == 1 and s7['vtfill2-vtbind-fn'] == 1, 'vtbind in a secondary vtable: %s' % f7)

        # a strong definition is never replaced
        os.remove(obj)
        obj2 = asm(tmp, 'g', open(os.path.join(tmp, 'g.S')).read().replace('    .weak %s' % z, '    .globl %s' % z)
                   .replace(',"awG",%%progbits,%s,comdat' % z, ',"aw"'))
        assert obj2 == obj
        st2, br2, _, _, _ = run({})
        check(st2['vtfill-skipped-strong'] == 1 and 'zzvtext_' + z not in br2, 'a strong group is not replaced')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d failed' % len(FAILS) if FAILS else 'all passed')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
