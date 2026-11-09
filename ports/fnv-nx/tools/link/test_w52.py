#!/usr/bin/env python3
"""W52 + cc_census: namespaced / static __stdcall slot functions get the drop bridge; the census helpers.

    FNVNX_EXE=/path/FalloutNV.exe python3 tools/link/test_w52.py

* cc_census.decode / msvc_depth / itanium_depth / native_kind / slot_need on spelled cases (no exe);
* named_free_drop: `?FUN_00aa23e0@fnv_00aa23e0@@YGXPAXHI@Z` (namespaced, ret 0xc, 3 parameters) is a drop; the
  same with a shell-class native symbol is not (a member); free_slot with com=True keeps the bare definition;
* ResizeHeapBlock (9 parameters, ret 0x24): the drop bridge moves the stack argument down;
* MakeZeroHalf (a by-value struct return, ret 8, 1 parameter): a drop.
"""
import collections
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cc_census as cc  # noqa: E402
import fnvlink  # noqa: E402

FAILS = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def nd(name, msvc):
    return {'sym': types.SimpleNamespace(name=name), 'msvc': msvc, 'variants': {}}


def main():
    d = cc.decode('?FUN_00aa23e0@fnv_00aa23e0@@YGXPAXHI@Z')
    check(d == dict(kind='free', cc='stdcall', varargs=False, sret=False), 'decode: namespaced stdcall %s' % d)
    check(cc.decode('?Get@C@@QAE?AUP3@@XZ')['sret'] and cc.decode('?F@@YAHPAXZZ')['varargs'], 'decode: sret, varargs')
    check(cc.decode('?S@C@@SGXH@Z')['kind'] == 'static', 'decode: static member')
    check(cc.msvc_depth('?F@ns@@YGXH@Z') == 2 and cc.itanium_depth('_ZN2ns1FEi') == 2 and
          cc.itanium_depth('_Z1Fi') == 1 and cc.itanium_depth('_ZN2ns1C1fEv') == 3, 'depths')
    k = cc.native_kind(cc.decode('?F@@YGXPAX@Z'), '?F@@YGXPAX@Z', '_ZN7F_shell1FEPv')
    check(k['kind'] == 'member', 'native_kind: a free declaration nested one deeper natively is a shell member')
    sd = cc.decode('?F@ns@@YGXHH@Z')
    check(cc.slot_need(sd, 2, 8, 'cpp') == 'drop' and cc.slot_need(sd, 3, 8, 'cpp') == 'none' and
          cc.slot_need(sd, 2, 8, 'com') == 'none', 'slot_need: drop / explicit this / COM')
    fd = cc.decode('?F@ns@@YIXPAXHH@Z')
    check(cc.slot_need(fd, 3, 4, 'cpp') == 'edx' and cc.slot_need(cc.decode('?F@@YIXPAX@Z'), 1, 0, 'cpp') == 'none',
          'slot_need: edx placeholder; a one-parameter fastcall is ecx alone')
    exe = os.environ.get('FNVNX_EXE') or '/home/user/gd/FalloutNV.exe'
    if not os.path.isfile(exe):
        print('skip exe half: no exe')
    else:
        n1 = nd('_ZN12fnv_00aa23e012FUN_00aa23e0EPvij', '?FUN_00aa23e0@fnv_00aa23e0@@YGXPAXHI@Z')
        check(fnvlink.named_free_drop(0x00aa23e0, n1, exe), 'namespaced stdcall, ret 0xc: drop')
        n2 = nd('_ZN12fnv_00aa23e05Shell12FUN_00aa23e0EPvij', '?FUN_00aa23e0@fnv_00aa23e0@@YGXPAXHI@Z')
        check(not fnvlink.named_free_drop(0x00aa23e0, n2, exe), 'wrapped in a shell class natively: no drop')
        check(fnvlink.free_slot(0x00aa23e0, n1, {}, collections.Counter(), exe, com=True) is None,
              'COM table: the bare definition stays')
        br = {}
        t = fnvlink.free_slot(0x00aa22f0, nd('_Z15ResizeHeapBlockPcPiPhiiiiii',
                                             '?ResizeHeapBlock@@YGPADPADPAHPAEHHHHHH@Z'), br, collections.Counter(), exe)
        body = br.get(t, '')
        check(t and 'ldr x7, [sp]' in body and 'ldr x16, [sp, #8]' in body and 'str x16, [sp, #0]' in body,
              'ResizeHeapBlock (9 parameters): x7 from the stack, one stack slot moved down')
        check(fnvlink.named_free_drop(0x00dbba20, nd('_Z12MakeZeroHalfi', '?MakeZeroHalf@@YG?AVhkHalf@@H@Z'), exe),
              'MakeZeroHalf: a struct return pops its hidden pointer too (ret 8, 1 parameter)')
        os.environ['FNVNX_FREENS'] = '0'
        try:
            check(not fnvlink.named_free_drop(0x00aa23e0, n1, exe), 'FNVNX_FREENS=0: namespaced not dropped')
        finally:
            del os.environ['FNVNX_FREENS']
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
