#!/usr/bin/env python3
"""test_msvcentry.py - the image defines a secondary-base override's __fnvnx_msvc_<addr> entry whenever its thunk is
defined, also when the unit never names it (R2, farm baseline 588863: 0x0040e3c0 MagicItem::FUN_0040e3c0 on its
MagicBase at +0x18; without the alias arm64emu.entry fell back to the primary __fnv_<addr>, `this` = the full object,
and the native read the vptr at this+0x18 where the exe reads [this]).
  M1  thunk defined, alias not referenced            -> `__fnvnx_msvc_0040e3c0 = _ZThn24_...;`
  M2  thunk not defined in this image                 -> no line
  M3  a referenced undefined alias still resolves once (no duplicate line)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import a64image  # noqa: E402

TH = '_ZThn24_N9MagicItem12FUN_0040e3c0EPS_'
AL = {'__fnvnx_msvc_0040e3c0': TH}
FAIL = 0


def check(name, got, want):
    global FAIL
    FAIL += got != want
    print('%s: %s (%s)' % (name, 'ok' if got == want else 'FAIL', got))


def main():
    line = '__fnvnx_msvc_0040e3c0 = %s;' % TH
    got = a64image.alias_lines(set(), set(), {TH, '__fnv_0040e3c0'}, {}, dict(AL))
    check('M1', line in got, True)
    got = a64image.alias_lines(set(), set(), {'__fnv_0040e3c0'}, {}, dict(AL))
    check('M2', any(l.startswith('__fnvnx_msvc_') for l in got), False)
    got = a64image.alias_lines(set(), {'__fnvnx_msvc_0040e3c0'}, {TH}, {}, dict(AL))
    check('M3', got.count(line), 1)
    print('test_msvcentry: %d failed' % FAIL)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
