#!/usr/bin/env python3
"""W53: field stores of image vftables (`m_6c = &DAT_…`) confirmed by the exe become hand-written vptr sites.

    FNVNX_EXE=/path/FalloutNV.exe python3 tools/link/test_w53.py

* _hv_unit takes `m_6c = &DAT_010b52a0;` in a function annotated @fnv 0x00b91cb0 as a tentative site (offset None);
* hand_vptr_confirm keeps it at offset 0x6c: the exe function stores 0x010b52a0 at [reg+0x6c], and the table's slot 0
  is an adjustor of 0x6c in front of a deleting destructor;
* outside the window around the named vftables (link 7f0f8526) the site is still taken;
* a tentative site whose table head does not fit its offset is dropped, and so is every one without the exe;
  FNVNX_HVFIELD=0: no tentative sites.
"""
import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402

FAILS = []
TEXT = '''
struct FUN_00b91cb0_class { void* m_6c; FUN_00b91cb0_class(); };
extern void* DAT_010b52a0;
// @fnv 0x00b91cb0
FUN_00b91cb0_class::FUN_00b91cb0_class()
{
    m_6c = &DAT_010b52a0;
}
'''
NEAR = (0x01000000, 0x01300000)


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def hand_of(got):
    h = collections.defaultdict(list)
    for a, cls, fn, fa, off in got:
        h[a].append(('u', cls, fa, off))
    return h


def main():
    got = fnvlink._hv_unit(TEXT, {}, set(), NEAR)
    check(any(a == 0x010b52a0 and off is None and fa == 0x00b91cb0 for a, _, _, fa, off in got),
          '_hv_unit: tentative site %s' % got)
    h = hand_of(got)
    fnvlink.hand_vptr_confirm(h, None)
    check(0x010b52a0 not in h, 'no exe: dropped')
    os.environ['FNVNX_HVFIELD'] = '0'
    try:
        check(not fnvlink._hv_unit(TEXT, {}, set(), NEAR), 'FNVNX_HVFIELD=0: no tentative site')
    finally:
        del os.environ['FNVNX_HVFIELD']
    exe = os.environ.get('FNVNX_EXE') or '/home/user/gd/FalloutNV.exe'
    if not os.path.isfile(exe):
        print('skip exe half: no exe')
    else:
        h = hand_of(got)
        st = collections.Counter()
        fnvlink.hand_vptr_confirm(h, exe, st)
        check(h.get(0x010b52a0) == [('u', 'FUN_00b91cb0_class', 0x00b91cb0, 0x6c)],
              'exe store at +0x6c, adjustor head: kept at 0x6c: %s' % h.get(0x010b52a0))
        far = fnvlink._hv_unit(TEXT, {}, set(), (0x01010000, 0x01090000))
        hf = hand_of(far)
        fnvlink.hand_vptr_confirm(hf, exe)
        check(hf.get(0x010b52a0) == [('u', 'FUN_00b91cb0_class', 0x00b91cb0, 0x6c)],
              'outside the named-vftable window: still a tentative site, confirmed by the exe')
        check(fnvlink.vftable_head(exe, 0x010b5140, 0) and not fnvlink.vftable_head(exe, 0x010b5140, 0x6c),
              'vftable_head: 010b5140 is a primary table (slot 0 a deleting destructor), not one at +0x6c')
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
