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


def run(tmp, exe, groups, exev, native, listed=(), called=(), defined=(), per_obj=None, alt=None, outdir=None):
    vmap = {v: (z, 8, obj, 'primary') for z, (obj, v) in groups.items()}
    have = [types.SimpleNamespace(obj=obj, source=z) for z, (obj, v) in groups.items()]
    bridges, traps = {}, {}
    out = outdir or tempfile.mkdtemp(dir=tmp)
    st = fnvlink.exe_vtable_copies(exev, vmap, types.SimpleNamespace(where={}), have, per_obj or {}, traps, native, bridges,
                                   out, None, exe, {}, alt, lambda n: None, set(listed), lambda c: set(c) & set(called),
                                   defined_fn=lambda c: set(c) & set(defined))
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
        V = {n: 0x01010000 + 0x100 * i for i, n in enumerate('ABCDEFGH')}
        exev = {V['A']: exevft.Vft('.?AVFoo@@', 0, [], [A0, A1, A2]),        # call-only placeholder v00
                V['B']: exevft.Vft('.?AVAlloc@@', 0, [], [A0, AF, A2, AF]),   # named slot 0, free fn in slots 1 and 3
                V['C']: exevft.Vft('.?AVUsed@@', 0, [], [A0, A1, A2]),       # placeholder, called by name
                V['D']: exevft.Vft('.?AVPlain@@', 0, [], [A1, A1, A2]),      # named slot 0, no deleting dtor in the exe (D26DS)
                V['E']: exevft.Vft('.?AVPair@@', 0, [], [A0, A1, A2]),       # a real pair: already right
                V['F']: exevft.Vft('.?AVFlag@@', 0, [], [A0, A1, A2]),       # slot 0 the single flags-0 stub
                V['G']: exevft.Vft('.?AVThingCO@@', 0, [], [A1, A1, A2, A2]),  # COM: QueryInterface / AddRef / Release
                V['H']: exevft.Vft('.?AVUnused@@', 0, [], [A0, A1, A2])}     # slot 0 `unused`: no body, any name
        zb = '_ZTV26FUN_00401200_BSNiAllocator'
        g = {'_ZTV3Foo': (t0.asm_group(tmp, 'a', '_ZTV3Foo', ['_ZN3Foo3v00Ev', '_ZN3Foo3v01Ev', '_ZN3Foo3v02Ev']), V['A']),
             zb: (t0.asm_group(tmp, 'b', zb, ['_ZN26FUN_00401200_BSNiAllocator7DestroyEj', '__fnv_00401200',
                                                '__fnv_00401020', '__fnv_00401200']), V['B']),
             '_ZTV4Used': (t0.asm_group(tmp, 'c', '_ZTV4Used', ['_ZN4Used3v00Ev', '_ZN4Used3v01Ev', '_ZN4Used3v02Ev']), V['C']),
             '_ZTV5Plain': (t0.asm_group(tmp, 'd', '_ZTV5Plain', ['_ZN5Plain7DestroyEj', '__fnv_00401010', '__fnv_00401020']),
                            V['D']),
             '_ZTV4Flag': (t0.asm_group(tmp, 'f', '_ZTV4Flag', ['_ZN4Flag3v00Ev', '_ZN4Flag3v01Ev', '_ZN4Flag3v02Ev']), V['F']),
             '_ZTV7ThingCO': (t0.asm_group(tmp, 'g', '_ZTV7ThingCO', ['_ZN7ThingCO14QueryInterfaceEv', '_ZN7ThingCO6AddRefEv',
                                                                        '_ZN7ThingCO7ReleaseEv']), V['G']),
             '_ZTV6Unused': (t0.asm_group(tmp, 'h', '_ZTV6Unused', ['_ZN6Unused6unusedEv', '_ZN6Unused3v01Ev', '_ZN6Unused3v02Ev']),
                             V['H']),
             '_ZTV4Pair': (t0.asm_group(tmp, 'e', '_ZTV4Pair', ['_ZN4PairD1Ev', '_ZN4PairD0Ev', '__fnv_00401010', '__fnv_00401020']), V['E'])}
        dfn = {'_ZN26FUN_00401200_BSNiAllocator7DestroyEj', '_ZN5Plain7DestroyEj'}   # slot-0 functions with a body
        outd = tempfile.mkdtemp(dir=tmp)
        fl0 = {'_ZTV4Flag': {'_ZN4Flag3v00Ev': '__fnvnx_vflag_00401000_0'}}
        st, br, traps = run(tmp, exe, g, exev, native, listed={V['B']}, called={'_ZN4Used3v00Ev'}, defined=dfn,
                            per_obj=fl0, outdir=outd, alt={V['A']: [('_ZTV12FUN_00bb83d0', 8, 'x.o', 'primary')]})
        w = words(br, '_ZTV3Foo')
        check(w[:2] == ['0x0', '_ZTI3Foo'] and w[2:] == ['__fnv_00401000_D1', '__fnv_00401000', '__fnv_00401010',
                                                         '__fnv_00401020'],
              'call-only placeholder v00 (no pair, D26 rule): offset-to-top, typeinfo, D1, D0, then exe slot k at k + 1: %s' % w)
        w = words(br, zb)
        check(w[2:4] == ['__fnv_00401000_D1', '__fnv_00401000'] and w[4] == '__fnvnx_vfree_00401200' and
              w[5] == '__fnv_00401020' and w[6] == '__fnvnx_vfree_00401200',
              'D22-listed class: pair, free-function slots through the this-drop thunk: %s' % w[2:])
        check('mov x0, x1' in br['__fnvnx_vfree_00401200'] and 'b __fnv_00401200' in br['__fnvnx_vfree_00401200'],
              'the thunk shifts the arguments down and tail-calls the free function')
        check('zzvtext__ZTV4Used' not in br, 'a placeholder called by name stays in the exe order')
        check('zzvtext__ZTV5Plain' not in br, 'a slot 0 defined in code, with no list evidence, stays as it is (no copy)')
        w = words(br, '_ZTV4Flag')
        check(w[2:4] == ['__fnv_00401000_D1', '__fnv_00401000'] and w[4:] == ['__fnv_00401010', '__fnv_00401020'],
              'a synthesized table with a single flags-0 slot 0 becomes D1/D0 + shift: %s' % w)
        w = words(br, '_ZTV6Unused')
        check(w[2:4] == ['__fnv_00401000_D1', '__fnv_00401000'] and len(w) == 6,
              'slot 0 `unused` with no body is minus-one whatever its name: %s' % w)
        w = words(br, '_ZTV7ThingCO') if 'zzvtext__ZTV7ThingCO' in br else []
        check(not w or (w[2] == '_ZN7ThingCO14QueryInterfaceEv' and st['d26-minus-one-tables'] == 4),
              'a COM interface is left alone (no D1/D0 pair, slot 0 stays QueryInterface): %s' % w)
        check('zzvtext__ZTV4Pair' not in br, 'a table with a pair is left alone')
        check(st['d26-minus-one'] == 4 and st['d26-minus-one-d22-list'] == 1 and
              st['d26-minus-one-d26-callonly'] == 2 and st['d26-minus-one-d26-undefined'] == 1 and
              st['exe-order-no-evidence'] == 3 and st['exe-order-com'] == 1 and st['exe-order-d22-used'] == 1 and
              st['exe-order-defined-slot0'] == 1 and st['d26-minus-one-BSNiAllocator'] == 1, 'counted: %s' % dict(st))
        tsv = [l.rstrip('\n').split('\t') for l in open(os.path.join(outd, 'exe_order_classes.tsv')) if not l.startswith('#')]
        by = {(r[0], r[2]) for r in tsv}
        check(('Used', 'd22-used') in by and ('Plain', 'defined-slot0') in by and ('ThingCO', 'com') in by and
              ('Foo', 'd26-callonly') not in {(r[0], r[2]) for r in tsv} and all(len(r) == 4 for r in tsv) and
              any(r[1] == '%08x' % V['C'] and r[3] == '_ZTV4Used' for r in tsv),
              'out/exe_order_classes.tsv names the exe-order tables (class, vftable, reason, symbol): %s' % tsv)
        # a named slot 0 with no list evidence (BSNiAllocator::Destroy unlisted) stays in the exe's order
        _, bu, _ = run(tmp, exe, g, exev, native, listed=set(), called={'_ZN4Used3v00Ev'}, defined=dfn)
        check('zzvtext__' + zb not in bu and 'zzvtext__ZTV3Foo' in bu,
              'unlisted: the named slot 0 stays in exe order, the call-only placeholder is still minus-one')
        # V2 (seams triage): a family whose destructor is exe slot 2 (two placeholders before it). A pair-less table with the
        # same two exe slots is minus-one from slot 2 (D1/D0 there, exe slot k at k + 1 after it), though its slot 0 is a
        # name some code calls and the unit defines (the Actor / Projectile / TESForm family: destructor at exe slot 4)
        exf = {V['A']: exevft.Vft('.?AVBaseF@@', 0, [], [A1, A2, A0, A1]),    # has a real pair at 2: the evidence
               V['B']: exevft.Vft('.?AVKidF@@', 0, [], [A1, A2, A0, A1, A2]),  # pair-less, same prefix
               V['C']: exevft.Vft('.?AVOddF@@', 0, [], [A2, A1, A0, A1]),      # pair-less, another prefix: no evidence
               V['D']: exevft.Vft('.?AVNoDtorF@@', 0, [], [A1, A2, A1, A1])}   # same prefix, slot 2 no destructor
        gf = {'_ZTV5BaseF': (t0.asm_group(tmp, 'fa', '_ZTV5BaseF', ['__fnv_00401010', '__fnv_00401020', '_ZN5BaseFD1Ev',
                                                                     '_ZN5BaseFD0Ev', '__fnv_00401010']), V['A']),
              '_ZTV4KidF': (t0.asm_group(tmp, 'fb', '_ZTV4KidF', ['_ZN4KidF3v00Ev', '_ZN4KidF3v01Ev']), V['B']),
              '_ZTV4OddF': (t0.asm_group(tmp, 'fc', '_ZTV4OddF', ['__fnv_00401020', '__fnv_00401010']), V['C']),
              '_ZTV8NoDtorF': (t0.asm_group(tmp, 'fd', '_ZTV8NoDtorF', ['__fnv_00401010', '__fnv_00401020']), V['D'])}
        stf, brf, _ = run(tmp, exe, gf, exf, native, called={'_ZN4KidF3v00Ev'}, defined={'_ZN4KidF3v00Ev'})
        w = words(brf, '_ZTV4KidF') if 'zzvtext__ZTV4KidF' in brf else []
        check(w[2:] == ['__fnv_00401010', '__fnv_00401020', '__fnv_00401000_D1', '__fnv_00401000', '__fnv_00401'
                        '010', '__fnv_00401020'] and stf['d26-minus-one-d26-family-dtor'] == 1,
              'a pair-less table of a family with the destructor at exe slot 2: D1/D0 there, the rest at k + 1: %s' % w)
        nomin = all('__fnv_00401000_D1' not in ''.join(brf.get('zzvtext_' + z, '')) for z in
                    ('_ZTV4OddF', '_ZTV8NoDtorF', '_ZTV5BaseF'))
        check(nomin and stf['d26-minus-one'] == 1,
              'no evidence for the prefix, no destructor at the slot, or a pair already: no minus-one copy')
        os.environ['FNVNX_D26F'] = '0'
        try:
            _, brf0, _ = run(tmp, exe, gf, exf, native, called={'_ZN4KidF3v00Ev'}, defined={'_ZN4KidF3v00Ev'})
        finally:
            del os.environ['FNVNX_D26F']
        check('__fnv_00401000_D1' not in brf0.get('zzvtext__ZTV4KidF', ''), 'FNVNX_D26F=0 turns the family rule off')
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
        # an empty slot-0 body (`virtual void v00() {}` of a stand-in class) is a placeholder, not a defined slot 0
        sfile = os.path.join(tmp, 'defs.S')
        open(sfile, 'w').write(
            '    .section .text._ZN4Stub3v00Ev,"axG",%progbits,_ZN4Stub3v00Ev,comdat\n'
            '    .weak _ZN4Stub3v00Ev\n    .type _ZN4Stub3v00Ev, %function\n_ZN4Stub3v00Ev:\n    ret\n'
            '    .size _ZN4Stub3v00Ev, .-_ZN4Stub3v00Ev\n'
            '    .section .text._ZN4Real3v00Ev,"axG",%progbits,_ZN4Real3v00Ev,comdat\n'
            '    .weak _ZN4Real3v00Ev\n    .type _ZN4Real3v00Ev, %function\n_ZN4Real3v00Ev:\n    mov w0, #1\n    ret\n'
            '    .size _ZN4Real3v00Ev, .-_ZN4Real3v00Ev\n')
        dobj = os.path.join(tmp, 'defs.o')
        r = subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', sfile, '-o', dobj], capture_output=True, text=True)
        check(r.returncode == 0, 'the code_defs fixture assembles %s' % r.stderr[:200])
        check(fnvlink.code_defs([dobj], {'_ZN4Stub3v00Ev', '_ZN4Real3v00Ev', '_ZN5Nope3v00Ev'}) == {'_ZN4Real3v00Ev'},
              'code_defs: a bare-ret slot 0 is a placeholder, a real body is a definition')
        tr = os.path.join(tmp, 'tree'); os.makedirs(os.path.join(tr, 'build'), exist_ok=True)
        open(os.path.join(tr, 'build', 'd22_applied.tsv'), 'w').write(
            'file\tclass\tmember\tkind\tvftables\n' 'a.cpp\tBSNiAllocator\tDestroy\tlisted\t010a2528\n'
            'b.cpp\tFoo\tv00\tcallonly\t\n')
        check(fnvlink.load_d22_applied(tr) == {0x010a2528} and fnvlink.load_d22_applied(None) == set(),
              'load_d22_applied reads the listed rows of build/d22_applied.tsv')
        # D26DS (orchestrator 11:49): a defined slot 0 that is the exe's deleting destructor goes minus-one
        nm = ['_ZN18FUN_00cc8da0_Class12FUN_00cc8da0Eb', '_ZN1X1fEv']
        c = lambda dd: fnvlink.d26_classify('_ZTV1X', 0x010ca06c, nm, None, set(), set(), {nm[0]}, dd)
        check(c(True) == 'd26-defined-deldtor' and c(False) == 'defined-slot0',
              'd26_classify: defined slot 0 + exe deleting dtor -> d26-defined-deldtor, else defined-slot0')
        os.environ['FNVNX_D26DS'] = '0'
        check(c(True) == 'defined-slot0', 'd26_classify: FNVNX_D26DS=0 keeps defined-slot0')
        del os.environ['FNVNX_D26DS']
        check(fnvlink.d26_classify('_ZTV1X', 1, nm, None, set(), {nm[0]}, {nm[0]}, True) == 'd22-used',
              'd26_classify: a slot 0 named by code stays d22-used')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
