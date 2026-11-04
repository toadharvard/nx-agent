#!/usr/bin/env python3
"""Unit tests of slotfill2.py (S2c-vt2's slot fill rules), on hand-made address-map entries; no tools needed.

    python3 tools/link/test_slotfill2.py
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import slotfill2   # noqa: E402

ADDR, ENTRY = '__fnv_%08x', '__fnvnx_msvc_%08x'
FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def main():
    native = {0x401000: {'msvc_entry': ('_ZThn8_N1M1fEv', 8)}, 0x402000: {}, 0x403000: {'msvc_entry': ('t', 12)}}

    def base(un, a, otts=None, depth=0, var=None):
        return (ADDR % a, 'same') if a != 0x403000 or un != 'bridged' else ('__fnvnx_br_x', 'bridge')
    st = collections.Counter()
    tf = slotfill2.secondary_entry(base, native, ADDR, ENTRY, st)
    check(tf('s', 0x401000, [-8])[0] == ENTRY % 0x401000, 'secondary vtable at the MSVC base: the _ZThn entry')
    check(tf('s', 0x401000, [-8, -8])[0] == ENTRY % 0x401000, 'two vtables, both at the base: the entry')
    check(tf('s', 0x401000, [0])[0] == ADDR % 0x401000, 'a primary native vtable keeps the full-object entry')
    check(tf('s', 0x401000, [-8, 0])[0] == ADDR % 0x401000, 'vtables at different offsets keep it too')
    check(tf('s', 0x401000, None)[0] == ADDR % 0x401000, 'no offsets (a thunk\'s inner target): unchanged')
    check(tf('s', 0x402000, [-8])[0] == ADDR % 0x402000, 'no MSVC entry: unchanged')
    check(tf('bridged', 0x403000, [-12])[0] == '__fnvnx_br_x', 'a bridge result is not rewritten')
    check(st['secondary_entry'] == 2, 'counted twice (%d)' % st['secondary_entry'])
    # fastcall deleting destructors: `void * __fastcall DestroyNiTriShape(void *, int, unsigned int)` at the exe slot
    import abi
    import sigs
    decl = {'?D@@YIPAXPAXHI@Z': 'void * __fastcall D(void *, int, unsigned int)',
            '?C@@YAPAXPAXHI@Z': 'void * __cdecl C(void *, int, unsigned int)'}
    native2 = {0x404000: {'msvc': '?D@@YIPAXPAXHI@Z'}, 0x405000: {'msvc': '?C@@YAPAXPAXHI@Z'}}

    def base2(un, a, otts=None, depth=0, var=None):
        return None, 'kind differs (function at the exe slot, %s in the vtable)' % sigs.itanium_variant(un)
    stubs = {}
    dem = {'_ZN1XD0Ev': 'X::~X()', '_ZN1XD1Ev': 'X::~X()'}
    tf2 = slotfill2.fastcall_dtor(base2, native2, lambda n: sigs.parse_itanium(n, dem[n]),
                                  lambda n: sigs.parse_msvc(n, decl[n]), abi.classify, stubs, ADDR, st)
    t0, _ = tf2('_ZN1XD0Ev', 0x404000)
    t1, _ = tf2('_ZN1XD1Ev', 0x404000)
    check(t0 == '__fnvnx_vflag3_00404000_1' and 'mov w2, #1' in stubs[t0], 'D0 -> flags 1 in w2 (%s)' % t0)
    check(t1 == '__fnvnx_vflag3_00404000_0' and 'mov w2, #0' in stubs[t1], 'D1 -> flags 0 in w2 (%s)' % t1)
    check(tf2('_ZN1XD0Ev', 0x405000)[0] is None, 'a cdecl (this, int, flags) stand-in stays a gap')
    # enum parameters: the Itanium spelling has no keyword; the MSVC side's `enum X` makes them ints
    dem3 = {'_ZN1B3getEN10ActorValue5IndexE': 'B::get(ActorValue::Index)', '_ZN1B3setE5Point': 'B::set(Point)'}
    it3 = slotfill2.enum_params(lambda n: sigs.parse_itanium(n, dem3[n]), {'ActorValue::Index'}, st)
    s3 = it3('_ZN1B3getEN10ActorValue5IndexE')
    check(s3.raw_params == ['enum ActorValue::Index'] and abi.classify(s3.raw_params[0]) == 'I32',
          'an MSVC enum parameter classifies as I32 (%s)' % s3.raw_params)
    check(it3('_ZN1B3setE5Point').raw_params == ['Point'], 'other types are left alone')
    # a decomp "constructor" at the exe slot: FUN_x::FUN_x(char) is the exe's ??_G taking the flags
    import fnvlink
    decl4 = {'??0FUN_00406000@@QAE@D@Z': 'public: __thiscall FUN_00406000::FUN_00406000(char)',
             '??0FUN_00407000@@QAE@_N@Z': 'public: __thiscall FUN_00407000::FUN_00407000(bool)'}
    native4 = {0x406000: {'msvc': '??0FUN_00406000@@QAE@D@Z'}, 0x407000: {'msvc': '??0FUN_00407000@@QAE@_N@Z'}}
    dem4 = {'_ZN1YD0Ev': 'Y::~Y()', '_ZN1YD1Ev': 'Y::~Y()', '_ZN1Y2m0Eb': 'Y::m0(bool)', '_ZN1Y2m1Ev': 'Y::m1()',
            '_ZN1Y2m2Ef': 'Y::m2(float)'}

    def base4(un, a, otts=None, depth=0, var=None):
        return None, 'kind differs (C1 at the exe slot, %s in the vtable)' % (sigs.itanium_variant(un) or 'function')
    stubs4 = {}
    tf4 = slotfill2.slot_ctor(base4, native4, lambda n: sigs.parse_itanium(n, dem4[n]),
                              lambda n: sigs.parse_msvc(n, decl4[n]), abi.classify, fnvlink.slot_abi, stubs4, ADDR, st)
    t4 = tf4('_ZN1YD0Ev', 0x406000)[0]
    check(t4 == '__fnvnx_vflagc_00406000_1' and 'mov w1, #1' in stubs4[t4], 'D0 -> the "constructor" with flags 1')
    check(tf4('_ZN1YD1Ev', 0x406000)[0] == '__fnvnx_vflagc_00406000_0', 'D1 -> flags 0')
    check(tf4('_ZN1Y2m0Eb', 0x407000)[0] == ADDR % 0x407000, 'a plain slot (bool) -> the "constructor" (bool)')
    check(tf4('_ZN1Y2m1Ev', 0x407000)[0] == ADDR % 0x407000, 'a placeholder slot () -> it too (reads more)')
    check(tf4('_ZN1Y2m2Ef', 0x407000)[0] is None, 'a float slot against a bool stays a gap')
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
