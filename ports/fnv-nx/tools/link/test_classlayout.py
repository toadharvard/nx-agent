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
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
