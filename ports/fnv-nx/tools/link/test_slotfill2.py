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

    def base(un, a, otts=None, depth=0):
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
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
