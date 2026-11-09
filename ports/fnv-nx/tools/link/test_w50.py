#!/usr/bin/env python3
"""W50: a named __fastcall free function in an exe vftable slot gets the edx bridge.

    FNVNX_EXE=/path/FalloutNV.exe python3 tools/link/test_w50.py

* fnv_00a93ea0::SetChildAt(NiNode*, int, unsigned, NiAVObject*) (`@@YI`, exe `ret 8`): named_free_edx is true and
  free_slot emits __fnvnx_vfree_00a93ea0 (x1..x6 -> x2..x7, w1 = 0, then __fnv_00a93ea0);
* the same name declared __stdcall (`@@YG`), or FNVNX_FREEEDX=0: no edx bridge;
* the exe-free half (regex) runs without the exe.
"""
import collections
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402

FAILS = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def main():
    m = '?SetChildAt@fnv_00a93ea0@@YIXPAVNiNode@@HIPAVNiAVObject@@@Z'
    check(bool(fnvlink.NAMED_FAST.match(m)) and not fnvlink.NAMED_FAST.match('?Foo@Bar@@QAEXH@Z')
          and bool(fnvlink.NAMED_FAST.match('?Foo@@YIXH@Z')), 'NAMED_FAST: global / namespace fastcall only')
    exe = os.environ.get('FNVNX_EXE') or '/home/user/gd/FalloutNV.exe'
    if not os.path.isfile(exe):
        print('skip exe half: no exe')
    else:
        nd = {'sym': types.SimpleNamespace(name='_ZN12fnv_00a93ea010SetChildAtEP6NiNodeijP10NiAVObject'), 'msvc': m,
              'variants': {}}
        check(fnvlink.named_free_edx(0x00a93ea0, nd, exe), 'SetChildAt: named fastcall, ret 8 = 4 x (4 - 2)')
        br, st = {}, collections.Counter()
        t = fnvlink.free_slot(0x00a93ea0, nd, br, st, exe)
        body = br.get(t, '')
        check(t == '__fnvnx_vfree_00a93ea0' and 'mov x2, x1' in body and 'mov w1, #0' in body and
              'b __fnv_00a93ea0' in body and st['free-slot-named-edx'] == 1, 'free_slot: the edx bridge')
        nd2 = dict(nd, msvc=m.replace('@@YI', '@@YG'))
        check(not fnvlink.named_free_edx(0x00a93ea0, nd2, exe), 'stdcall spelling: not edx')
        os.environ['FNVNX_FREEEDX'] = '0'
        try:
            check(not fnvlink.named_free_edx(0x00a93ea0, nd, exe), 'FNVNX_FREEEDX=0')
        finally:
            del os.environ['FNVNX_FREEEDX']
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
