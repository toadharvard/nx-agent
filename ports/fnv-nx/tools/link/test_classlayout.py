#!/usr/bin/env python3
"""Tests of classlayout.py: names, the decision's slot transform, the native slot reading, and decide() on stubbed
exe evidence (no exe needed)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import classlayout as cl  # noqa: E402
import exevft  # noqa: E402

FAILS = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def main():
    check(cl.simple_name('.?AVFoo@@') == ('Foo', 'Foo'), 'simple_name: plain class')
    check(cl.simple_name('.?AVInner@Outer@@') == ('Inner', 'Outer::Inner'), 'simple_name: nested class')
    check(cl.simple_name('.?AV?$NiPointer@VNiNode@@@@')[0] == 'NiPointer', 'simple_name: template')
    m1 = dict(kind='minus-one', dtor_slot=0)
    check(cl.expected(m1, [1, 2, 3]) == [('dtor', 1), ('dtor', 1), 2, 3], 'expected: minus-one at 0')
    m4 = dict(kind='minus-one', dtor_slot=2)
    check(cl.expected(m4, [1, 2, 3, 4]) == [1, 2, ('dtor', 3), ('dtor', 3), 4], 'expected: minus-one at 2')
    check(cl.expected(dict(kind='exe-order', dtor_slot=-1), [1, 2]) == [1, 2], 'expected: exe order')
    check(cl.sym_addr('__fnv_00401000', {}) == (0x401000, 'addr'), 'sym_addr: address symbol')
    check(cl.sym_addr('__fnvnx_vflag_00401000_1', {}) == (0x401000, 'addr'), 'sym_addr: flags stub')
    check(cl.sym_addr('__fnvnx_vtrap_1234abcd', {})[1] == 'trap', 'sym_addr: trap')
    check(cl.sym_addr('__cxa_pure_virtual', {})[1] == 'pure', 'sym_addr: pure')
    check(cl.sym_addr('_ZN3Foo1fEv', {'_ZN3Foo1fEv': 0x401020}) == (0x401020, 'addr'), 'sym_addr: bound name')
    check(cl.sym_addr('_ZN3Foo1gEv', {})[1] == 'unmapped', 'sym_addr: unbound name')
    # decide() on stubbed evidence
    saved = (cl.is_deldtor, cl.trivial)
    try:
        cl.is_deldtor = lambda exe, a: (a == 0x40, 'exe-deldtor' if a == 0x40 else '')
        cl.trivial = lambda exe, a: a == 0x10
        V = exevft.Vft
        check(cl.decide('x', V('.?AVA@@', 0, [('.?AVA@@', 0)], [0x40, 0x20]))[:2] == ('minus-one', 0),
              'decide: deleting dtor at slot 0')
        d = cl.decide('x', V('.?AVB@@', 0, [('.?AVB@@', 0)], [0x10, 0x10, 0x20, 0x30, 0x40, 0x50]))
        check(d[:2] == ('minus-one', 4) and 'after-real' in d[3], 'decide: TESForm shape, dtor at slot 4: %s' % (d,))
        check(cl.decide('x', V('.?AVC@@', 0, [('.?AVC@@', 0)], [0x20, 0x30]))[:2] == ('exe-order', -1),
              'decide: no deleting dtor -> exe order')
        check(cl.decide('x', V('.?AVD@@', 0, [('.?AVD@@', 0), ('.?AUIUnknown@@', 0)], [0x40]))[0] == 'com',
              'decide: IUnknown in the hierarchy -> com, even with a dtor shape')
    finally:
        cl.is_deldtor, cl.trivial = saved
    # R4's VTFILL binding file (fnv-nx-build recon/vtbind): found under FNVNX_RECON by its header
    import tempfile, fnvlink
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, 'vtbind'))
    open(os.path.join(d, 'vtbind', 'bind.tsv'), 'w').write(
        '# fnvnx-vtbind/1\n# exe_address\tkind\tsymbol\ttarget\tn\ttables\n00401130\tfn\t_ZN1A1fEv\t\t0\t2\n'
        '00408b40\tadj\t__fnvnx_vadj_00408b40\t__fnv_00408c00\t8\t1\n')
    open(os.path.join(d, 'other.tsv'), 'w').write('# something else\n00401000\tfn\tx\t\t0\t1\n')
    vb = fnvlink.load_vtbind(d)
    check(vb == {0x401130: ('fn', '_ZN1A1fEv', '', 0), 0x408b40: ('adj', '__fnvnx_vadj_00408b40', '__fnv_00408c00', 8)},
          'load_vtbind: fn and adj rows from recon/vtbind: %s' % vb)
    stub = fnvlink.emit_adj_stub('__fnvnx_vadj_00408b40', '__fnv_00408c00', 8)
    check('sub w0, w0, #8' in stub and 'b __fnv_00408c00' in stub, 'an adj row becomes this -= n; b target')
    open(os.path.join(d, 'vtbind', 'overrides.tsv'), 'w').write(
        '# fnvnx-vtbind-override/1: x\n# table\texe_vftable\tslot\texe_function\tnative_symbol\tnative_now\tabi\n'
        '_ZTV1A\t01010000\t5\t00401000\t_ZN1A1fEv\t__fnv_00402000\tsame-params\n'
        '_ZTV1B\t01010100\t7\t00401100\t_ZN1B1gEf\t__fnv_00402100\tabi-check: (float) vs (int)\n')
    check(fnvlink.load_vtfill_holds(d) == {('_ZTV1B', 7)}, 'load_vtfill_holds: only the abi-check rows are held')
    # S1's release rule: integer-class parameters by position, override no longer than the slot
    R = fnvlink.abi_releasable
    check(R('abi-check: (int) vs (void*)') and R('abi-check: () vs (TESObjectREFR*)') and
          R('abi-check: (int, void*, unsigned char, int, void*) vs (int, int, int, int, int)') and
          R('abi-check: (unsigned int) vs (hkEntityD085*)') and R('abi-check: (Foo<a, b> const&) vs (int)'),
          'abi_releasable: integer-class params, override not longer -> released')
    check(not R('abi-check: (BaseFormComponent*) vs ()') and not R('abi-check: (void*, void*) vs (hkpWorld*)') and
          not R('abi-check: (float) vs (int)') and not R('abi-check: (int) vs (double)') and
          not R('abi-check: (NiPoint3) vs (int)') and not R('abi-check: (TESGrass*, Ns::Argument) vs (TESForm*, int)')
          and not R('abi-check: odd'), 'abi_releasable: longer override, floats, by-value classes stay held')
    with open(os.path.join(d, 'vtbind', 'overrides.tsv'), 'a') as f:
        f.write('_ZTV1C\t01010200\t3\t00401200\t_ZN1C1hEi\t__fnv_00402200\tabi-check: (int) vs (void*)\n')
    check(fnvlink.load_vtfill_holds(d) == {('_ZTV1B', 7)} and fnvlink.VTFILL_RELEASED == {('_ZTV1C', 3)},
          'load_vtfill_holds: a releasable abi-check row is not held')
    os.environ['FNVNX_ABIREL'] = '0'
    try:
        check(fnvlink.load_vtfill_holds(d) == {('_ZTV1B', 7), ('_ZTV1C', 3)}, 'FNVNX_ABIREL=0: every abi-check held')
    finally:
        del os.environ['FNVNX_ABIREL']
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
