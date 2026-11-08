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
    # S1's rule through R4's slot_abi: a held slot is released when the call reaches the override unchanged
    import sigs
    ms = {'?f@C@@QAEXH@Z': 'public: void __thiscall C::f(int)', '?g@C@@QAEXM@Z': 'public: void __thiscall C::g(float)'}
    m = lambda n_: sigs.parse_msvc(n_, ms[n_])
    nat = {0x401000: dict(msvc='?f@C@@QAEXH@Z'), 0x401010: dict(msvc='?g@C@@QAEXM@Z')}
    H = fnvlink.vtfill_hold_abi
    check(H('_ZN1C1fEPv', 0x401000, nat, m) == 'same' and H('_ZN1C1fEii', 0x401000, nat, m) == 'the slot passes more',
          'vtfill_hold_abi: int / pointer in a W register, a slot passing more -> releasable')
    check(H('_ZN1C1gEi', 0x401010, nat, m).startswith('argument 1 differs') and
          H('_ZN1C1fEv', 0x401000, nat, m).startswith('the definition reads more') and
          H('__fnv_00401000', 0x401000, nat, m) is None, 'vtfill_hold_abi: a float, a longer definition stay held')
    import collections
    fnvlink.VTFILL_HOLD.clear()
    fnvlink.VTFILL_HOLD.update({('_ZTV1C', 0), ('_ZTV1C', 1)})
    try:
        import elfsyms

        def ent(n_, a_):
            sy = elfsyms.Sym(n_, 0, 0, 'func', 'global', 1, '.text', False)
            return dict(unit='u.cpp', sym=sy, variants={}, tier='def', msvc=nat[a_]['msvc'], msvc_entry=None)
        full = {0x401000: ent('_ZN1C1fEi', 0x401000), 0x401010: ent('_ZN1C1gEf', 0x401010)}

        def blk(env):
            os.environ.update(env)
            try:
                st = collections.Counter()
                w = fnvlink.vtfill_block([0x401000, 0x401010], ['__fnv_00402000', '__fnv_00402010'], '_ZTV1C', 2, None,
                                         full, {}, None, m, {}, {}, st,
                                         raw=['_ZN1C1fEPv', '_ZN1C1gEi'])
            finally:
                for k in env:
                    del os.environ[k]
            return w, st
        w, st = blk({})
        check(w == ['__fnv_00401000', '__fnv_00402010'] and st['vtfill-abi-released'] == 1 and
              st['vtfill-abi-hold'] == 1, 'vtfill_block: slot 0 released (same), slot 1 held (float): %s %s' % (w, st))
        w0, st0 = blk({'FNVNX_ABIREL': '0'})
        check(w0 == ['__fnv_00402000', '__fnv_00402010'] and st0['vtfill-abi-hold'] == 2,
              'FNVNX_ABIREL=0: both held')
    finally:
        fnvlink.VTFILL_HOLD.clear()
    # deleting_dtor: the function's own first return decides (MagicCaster slot 9 0x008d0600 ends `ret 8`; the byte
    # search found the next function's `ret 4`)
    import test_exevft as tx
    import tempfile as _tf
    stub = bytes.fromhex('558bec51894dfc8be55dc20800')                             # ... ret 8
    gdel = bytes.fromhex('f644240401568bf1740656e8000000008bc65ec20400')           # ??_G: test flags; delete; ret 4
    getter = bytes.fromhex('b8105d1f01c3')                                        # mov eax, imm; ret
    pe_ = os.path.join(_tf.mkdtemp(), 'd.exe')
    tx.make_pe(pe_, lambda rd: None, {0x401000: stub + gdel, 0x401040: getter + gdel, 0x401080: gdel})
    check(not exevft.deleting_dtor(pe_, 0x401000) and exevft.deleting_dtor(pe_, 0x401000 + len(stub)),
          'deleting_dtor: a ret-8 function followed by a ??_G is not one; the ??_G is')
    check(not exevft.deleting_dtor(pe_, 0x401040) and exevft.deleting_dtor(pe_, 0x401080),
          'deleting_dtor: a getter followed by a ??_G (NiShaderInstanceDescriptor slot 0) is not one')
    os.environ['FNVNX_DELDTOR_STRICT'] = '0'
    try:
        check(exevft.deleting_dtor(pe_, 0x401000), 'FNVNX_DELDTOR_STRICT=0: the old byte search (the false positive)')
    finally:
        del os.environ['FNVNX_DELDTOR_STRICT']
    # R2: the Havok ??_G frees through its router; the destroy call before the flags test is still the D1's body
    def rel(src, dst):
        return (dst - (src + 5) & 0xffffffff).to_bytes(4, 'little')
    G, X, R = 0x401100, 0x401000, 0x401080
    hk = (b'\x56\x8b\xf1\xe8' + rel(G + 3, X) + b'\xf6\x44\x24\x08\x01\x74\x0a\xe8' + rel(G + 15, R) +
          b'\x8b\x10\x56\xff\x52\x08\x8b\xc6\x5e\xc2\x04\x00')
    pe2 = os.path.join(_tf.mkdtemp(), 'h.exe')
    tx.make_pe(pe2, lambda rd: None, {X: b'\xc3', R: b'\xc3', G: hk})
    check(exevft.deleting_dtor(pe2, G) and exevft.deleting_dtor_callee(pe2, G) is None and
          exevft.deleting_dtor_destroy(pe2, G) == X, 'deleting_dtor_destroy: call ~X; test; router free -> X')
    check(exevft.deleting_dtor_destroy(pe2, X) is None, 'deleting_dtor_destroy: not a ??_G -> None')
    import collections as _c
    import elfsyms
    d0 = elfsyms.Sym('_ZN1HD0Ev', 0, 0, 'func', 'global', 1, '.text', False)
    xd = elfsyms.Sym('_ZN1H7DestroyEv', 0, 0, 'func', 'global', 1, '.text', False)
    nat2 = {G: dict(unit='h.cpp', sym=d0, variants={'D0': d0}, tier='def', msvc=None),
            X: dict(unit='h.cpp', sym=xd, variants={}, tier='def', msvc=None)}
    st2 = _c.Counter()
    pair = fnvlink.vmo_dtor_slots(G, nat2, lambda n_: None, {}, {}, st2, ('_ZTV1H', 2), pe2)
    check(pair == ('__fnv_00401000', '__fnv_00401100') and st2['slot0-d0-destroy'] == 1,
          'vmo_dtor_slots: a native D0 alone pairs with the ??_G\'s destroy body as D1: %s' % (pair,))
    os.environ['FNVNX_D0DESTROY'] = '0'
    try:
        check(fnvlink.vmo_dtor_slots(G, nat2, lambda n_: None, {}, {}, _c.Counter(), ('_ZTV1H', 2), pe2) is None,
              'FNVNX_D0DESTROY=0: as before (no pair)')
    finally:
        del os.environ['FNVNX_D0DESTROY']
    # a named global __stdcall function in a member slot (BSNiAllocator 0x00aa2240): `this` dropped when the exe's
    # return pops exactly its parameters
    F2, F1 = 0x401000, 0x401010
    pe3 = os.path.join(_tf.mkdtemp(), 'f.exe')
    tx.make_pe(pe3, lambda rd: None, {F2: b'\x8b\x44\x24\x04\xc2\x08\x00', F1: b'\x8b\x44\x24\x04\xc2\x04\x00'})
    fs = elfsyms.Sym('_Z3fooRjS_', 0, 0, 'func', 'global', 1, '.text', False)
    ndf = dict(sym=fs, msvc='?foo@@YGXAAI0@Z', variants={})
    check(fnvlink.named_free_drop(F2, ndf, pe3) and not fnvlink.named_free_drop(F1, ndf, pe3),
          'named_free_drop: ret 8 for 2 parameters drops this; ret 4 (the object among them) does not')
    check(not fnvlink.named_free_drop(F2, dict(ndf, msvc='?foo@@YAXAAI0@Z'), pe3),
          'named_free_drop: cdecl (no exe pop count to check) is left')
    b3, s3 = {}, _c.Counter()
    check(fnvlink.free_slot(F2, ndf, b3, s3, pe3) == '__fnvnx_vfree_00401000' and s3['free-slot-named'] == 1 and
          'mov x0, x1' in b3['__fnvnx_vfree_00401000'], 'free_slot: the named stdcall slot gets the drop bridge')
    os.environ['FNVNX_FREENAMED'] = '0'
    try:
        check(fnvlink.free_slot(F2, ndf, {}, _c.Counter(), pe3) is None, 'FNVNX_FREENAMED=0: as before')
    finally:
        del os.environ['FNVNX_FREENAMED']
    # D22-USED counts a placeholder slot code calls by name, never a real destructor (link 0e8b2eb8's HighProcess shell)
    NP = fnvlink.named_placeholder
    called = {'_ZN12FUN_008d98e0D1Ev', '_ZN3Foo3v00Ev'}
    check(not NP('_ZN12FUN_008d98e0D1Ev', called) and NP('_ZN3Foo3v00Ev', called) and not NP('_ZN3Foo3v00Ev', set())
          and not NP(None, called), 'named_placeholder: a called D1 is no placeholder; a called v00 is')
    os.environ['FNVNX_D22DTOR'] = '0'
    try:
        check(NP('_ZN12FUN_008d98e0D1Ev', called), 'FNVNX_D22DTOR=0: a called D1 counts as before')
    finally:
        del os.environ['FNVNX_D22DTOR']
    # hand_ambiguous_names: a stand-in name whose stores write several image vftables does not pick a native vtable
    V = exevft.Vft
    hand = {0x10a1458: [('u1.cpp', 'StandIn', 0x401000, 0)], 0x1070660: [('u2.cpp', 'StandIn', 0x402000, 0)],
            0x1010000: [('u3.cpp', 'Foo', 0x403000, 0)], 0x1010100: [('u3.cpp', 'Bar', 0x403100, 0)],
            0x1010200: [('u4.cpp', 'Bar', 0x403200, 0)]}
    ev = {0x10a1458: V('.?AVNiFogProperty@@', 0, [], [1]), 0x1070660: V('.?AVSkyObject@@', 0, [], [1]),
          0x1010100: V('.?AVBar@@', 0, [], [1]), 0x1010200: V('.?AVBase@@', 0, [], [1])}
    amb = fnvlink.hand_ambiguous_names(hand, ev)
    check(amb == {(0x10a1458, 'StandIn', 0), (0x1070660, 'StandIn', 0), (0x1010200, 'Bar', 0)},
          'hand_ambiguous_names: StandIn x2 and Bar\'s store of Base\'s vftable; Foo and Bar\'s own stay: %s' % amb)
    os.environ['FNVNX_HANDAMB'] = '0'
    try:
        check(fnvlink.hand_ambiguous_names(hand, ev) == set(), 'FNVNX_HANDAMB=0: the name decides as before')
    finally:
        del os.environ['FNVNX_HANDAMB']
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
