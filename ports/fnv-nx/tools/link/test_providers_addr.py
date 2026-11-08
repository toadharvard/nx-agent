#!/usr/bin/env python3
"""Unit test of fnvlink.provider_or_addr and ADDR_NAME (R4 recon4-014): generated code and `__fnv_<a>` spellings
reach S4's provider for an address the native build does not define; a natively defined address stays
`__fnv_<a>`, and one with neither stays `__fnv_<a>` (a visible trap).

    python3 tools/link/test_providers_addr.py
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


native = {0x00401000: {}}
providers = {0x004f6e40: ('acosf', 'libm: acosf'), 0x00401000: ('memcpy', 'libc: memcpy')}
check(fnvlink.provider_or_addr(0x004f6e40, native, providers) == 'acosf', 'no native definition: the provider')
check(fnvlink.provider_or_addr(0x00401000, native, providers) == '__fnv_00401000', 'a native definition wins')
check(fnvlink.provider_or_addr(0x00402000, native, providers) == '__fnv_00402000', 'neither: the address symbol')
check(fnvlink.ADDR_NAME.match('__fnv_004f6e40') and not fnvlink.ADDR_NAME.match('__fnv_004f6e40_D1'),
      'ADDR_NAME: only the plain address symbol')
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
