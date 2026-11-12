#!/usr/bin/env python3
"""Test of toolchain/sdd/fnvnx_sdd.cc (SDD2: one vtable slot per virtual destructor), built from the tracked source with the
argv of the recipe's plugin stage (plugbuild.py). Compiles sdd/sdd.cpp (plain C++) with the product flags and checks the
object (objdump -dr, readelf, section bytes of the recipe's object): table contents in exe order, the slot each virtual call loads, destructor flags, pointers to virtual members,
address-point symbols, the table map; an unknown plugin argument is an error. No network, no game data.
Run: python3 -I <this file> from the checkout root."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_sdd')
FIX = os.path.join(HERE, 'sdd', 'sdd.cpp')


def slot_loads(obj, sym):
    """byte offsets of the loads `ldr wN, [xM, #off]` / `[xM]` that are followed by `mov x16, ...` (the tail-call through a slot), i.e. table slots"""
    return [int(o or '0', 0) for o in re.findall(r'ldr w\d+, \[x\d+(?:, #(-?(?:0x)?[0-9a-f]+))?\] mov x16,', pb.insns(obj, sym))]


def relocs_in(obj, sym):
    """[(type, symbol)] of the relocations that fall inside function SYM of the object"""
    s = pb.symbols_of(obj)[sym]
    return [(t, n) for o, t, n, a in pb.relocs_of(obj).get(s['sec'], []) if s['value'] <= o < s['value'] + s['size']]


def checks(obj):
    syms = pb.symbols_of(obj)
    ins = lambda f: pb.insns(obj, f)  # noqa: E731
    w = lambda s: pb.words(obj, s)  # noqa: E731
    # struct A { virtual int f(); virtual ~A(); virtual int g(); }: slots f 0, ??_G 1, g 2
    check('table M: exe order, one destructor slot, the secondary table too',
          w('_ZTV1M') == ['0', '_ZTI1M', '_ZN1A1fEv', '_ZN1MD1Ev.fnvnx_sdd', '_ZN1A1gEv', '_ZN1M1kEv', '_ZN1M4lastEv',
                          '-8', '_ZTI1M', '_ZThn8_N1MD1Ev.fnvnx_sdd', '_ZThn8_N1M1kEv'], str(w('_ZTV1M')))
    check('delete p: slot 1, flags 1', slot_loads(obj, '_Z5del_aP1A') == [4] and re.search(r'mov w1, #0x1\b', ins('_Z5del_aP1A')))
    check('p->~A(): slot 1, flags 0', slot_loads(obj, '_Z9destroy_aP1A') == [4] and re.search(r'mov w1, #0x0\b', ins('_Z9destroy_aP1A')))
    check('delete through the second base: its slot 0', slot_loads(obj, '_Z6del_s2P2S2') == [0])
    check('g(): slot 2', slot_loads(obj, '_Z6call_gP1A') == [8], str(slot_loads(obj, '_Z6call_gP1A')))
    check('k() through S2: slot 1', slot_loads(obj, '_Z6call_kP2S2') == [4])
    check('M::last(): slot 4', slot_loads(obj, '_Z9call_lastP1M') == [16])
    check('pointer to virtual member: constant', w('table') == ['8', '1', '0', '1'], str(w('table')))
    check('pointer to virtual member: comparison', re.search(r'cmp w0, #0x8\b', ins('_Z2eqM1AFivE')))
    check('pointer to virtual member: template argument', slot_loads(obj, '_Z5use_tP1A') == [8])
    check('instantiated class template', slot_loads(obj, '_Z6use_ttP2TTIiE') == [4] and slot_loads(obj, '_Z6del_ttP2TTIiE') == [0])
    uj = re.findall(r'ldr w\d+, \[x\d+, #(\d+)\]', ins('_Z5use_jP1J'))
    check('inline member bodies: g() slot 2, J::w() slot 3', uj == ['8', '12'], str(uj))
    ap8, ap36, tv = syms['_ZTV1M.fnvnx_ap8'], syms['_ZTV1M.fnvnx_ap36'], syms['_ZTV1M']
    check('address points: own symbols', ap8['value'] == tv['value'] + 8 and ap36['value'] == tv['value'] + 36 and ap8['sec'] == ap36['sec'] == tv['sec'],
          str((ap8, ap36, tv)))
    ctor = [(t, n) for t, n in relocs_in(obj, '_ZN1MC2Ev')]
    check('constructor: one relocation per address point, no immediate',
          sorted(n for t, n in ctor if t == 'R_AARCH64_P32_LD32_GOT_LO12_NC') == ['_ZTV1M.fnvnx_ap36', '_ZTV1M.fnvnx_ap8']
          and not [1 for t, n in ctor if n == '_ZTV1M'], str(ctor))
    th = ins('_ZN1MD1Ev.fnvnx_sdd')
    check('??_E flags 0: the destructor; flags 1: the deleting destructor; returns this',
          'tst w1, #0x3 b.ne L1 bl _ZN1MD1Ev b L5 L1: mov w20, w0 tbnz w21, #1, L2 bl _ZN1MD0Ev b L5' in th
          and 'L5: mov x0, x19' in th, th)
    check('??_E flags 2: n = the word before the array, elements last first, then the global operator delete on bit 0 (cl: ??3)',
          'L2: ldur w22, [x20, #-4] L3: cbz w22, L4 sub w22, w22, #0x1 ldr w9, =16 madd w0, w22, w9, w20 bl _ZN1MD1Ev b L3 '
          'L4: tbz w21, #0, L5 sub w0, w20, #0x4 bl _ZdlPv' in th, th)
    th2 = ins('_ZThn8_N1MD1Ev.fnvnx_sdd')
    check('??_E of the second base: the thunk for flags 0, the adjusted object otherwise',
          'bl _ZThn8_N1MD1Ev b L5 L1: ldr w9, =-8 add w0, w0, w9 mov w20, w0 tbnz w21, #1, L2 bl _ZN1MD0Ev' in th2, th2)
    th3 = ins('_ZN2VBD1Ev.fnvnx_sdd')
    check("??_E frees with the class's own operator delete[]", 'ldr w9, =12 madd' in th3 and 'bl _ZN2VBdaEPv' in th3, th3)
    th4 = ins('_ZN2HKD1Ev.fnvnx_sdd')
    check('placement delete[] only (and a scalar operator delete of the class): the global operator delete, as cl',
          'tbz w21, #0, L5 sub w0, w20, #0x4 bl _ZdlPv' in th4 and '_ZN2HKdlEPv' not in th4.split('L2:')[1], th4)
    th5 = ins('_ZN2SZD1Ev.fnvnx_sdd')
    check('sized delete[]: the member, with the size of one element (cl passes sizeof(the class))',
          re.search(r'tbz w21, #0, L5 ldr w1, =12 sub w0, w20, #0x4 bl _ZN2SZdaEPvj\b', th5), th5)
    na = ins('_Z6new_a8i')
    check('COOKIE1: the array of a class aligned to 8 starts 4 bytes into its block',
          re.search(r'add w\d+, w\d+, #0x4\b', na) and not re.search(r'add w\d+, w\d+, #0x8\b', na), na)
    rel = pb.relocs_of(obj)
    d0 = sorted((sec, n) for sec, rs in rel.items() if sec.startswith('.data.rel.ro._ZTV') for o, t, n, a in rs if 'D0Ev' in n)
    check('no deleting destructor in any table', not d0, d0)
    check('deleting destructors are written out', all(syms.get(n, {}).get('type') == 'FUNC' and syms[n]['sec'] not in ('UND', None) and syms[n]['size'] > 0
                                                     for n in ('_ZN1MD0Ev', '_ZN1BD0Ev')))
    vm = [l for l in pb.section(obj, '.fnvnx.vtmap').splitlines(True) if l.startswith('_ZTV1M')]
    check('table map: one line per address point with its base path',
          vm == ['_ZTV1M\t8\t0\t1M\t1M\n', '_ZTV1M\t36\t8\t1M\t1M;2S2\n'], str(vm))


def main():
    td = tempfile.mkdtemp(prefix='test-sdd-')
    try:
        so = pb.build('sdd', td)
        out = os.path.join(td, 't.o')
        r = pb.compile(FIX, out, [so])
        check('compiles', r.returncode == 0, r.stderr[:3000])
        if r.returncode == 0:
            checks(out)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), [so], args=['-fplugin-arg-fnvnx_sdd-mode=1'])
        check('an unknown plugin argument is an error', r.returncode != 0 and 'unknown plugin argument' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
