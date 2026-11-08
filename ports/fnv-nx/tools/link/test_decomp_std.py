#!/usr/bin/env python3
"""Unit test of fnvlink.decomp_std: a std:: name whose first component libstdc++ does not declare is the decomp's
own (classified like other decomp names); a libstdc++ name stays runtime; with no identifier set, every std:: name
stays runtime. Needs c++filt only.

    python3 tools/link/test_decomp_std.py
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fnvlink  # noqa: E402
import sigs     # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def it(n):
    d = subprocess.run(['c++filt'], input=n + '\n', capture_output=True, text=True).stdout.strip()
    return sigs.parse_itanium(n, d)


fnvlink.STD_IDS[:] = []
n1 = '_ZNSt30StandardLibrarySingletonObjectC1Ev'
check(not fnvlink.decomp_std(n1, it(n1)), 'no identifier set: stays runtime')
fnvlink.STD_IDS[:] = [{'basic_string', 'char_traits', 'allocator', 'exception', 'string', '__cxx11'}]
check(fnvlink.decomp_std(n1, it(n1)), "the decomp's own std:: class: classified as a decomp name")
n2 = '_ZNKSt9exception4whatEv'
check(not fnvlink.decomp_std(n2, it(n2)), 'a libstdc++ class: runtime')
n3 = '_ZNSt10StringBase5_XlenEv'
check(fnvlink.decomp_std(n3, it(n3)), 'std::StringBase::_Xlen: a decomp name')
check(not fnvlink.decomp_std('_Znwj', it('_Znwj')), 'operator new is not a std:: name')
check(fnvlink.RUNTIME.match(n1), 'RUNTIME still matches the name (decomp_std is the exception)')
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
