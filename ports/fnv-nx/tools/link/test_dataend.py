#!/usr/bin/env python3
"""Unit test of fnvlink's address kinds after DATA_END moved to the image end (0x014089e4): .rdata/.data/.tls/
.rsrc/.reloc addresses are data, the old bound 0x0200c612 (a bogus units.json value) is outside the image.

    python3 tools/link/test_dataend.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fnvlink  # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


check(fnvlink.kind_of_addr(0x00401000) == 'text', '.text is text')
for a in (0x00fdf000, 0x01183000, 0x011f6238, 0x01272000, 0x01340000, 0x014089e3):
    check(fnvlink.kind_of_addr(a) == 'data' and fnvlink.in_image(a), '%08x is image data' % a)
for a in (0x0200c612, 0x01500000):
    check(fnvlink.kind_of_addr(a) == 'other' and not fnvlink.in_image(a), '%08x is outside the image' % a)
check(not fnvlink.in_image(0x65), 'a small value is not an address')
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
