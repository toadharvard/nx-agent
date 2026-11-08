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
# the second test: a name no libstdc++ library defines (an MSVC STL helper or member the unit declares without a body)
fnvlink.STD_EXPORTS[:] = []
n4 = '_ZSt8_DestroyItEvPT_'            # std::_Destroy<unsigned short>(unsigned short*): `_Destroy` is a libstdc++ id
fnvlink.STD_IDS[:] = [{'_Destroy', 'swap', 'fill', 'vector', 'bad_alloc', 'bad_cast', 'exception', 'basic_string', 'terminate'}]
check(not fnvlink.decomp_std(n4, it(n4)), 'no export set: a libstdc++ identifier stays runtime')
fnvlink.STD_EXPORTS[:] = [{'_ZNSt9exceptionD1Ev', '_ZSt9terminatev'}]
check(fnvlink.decomp_std(n4, it(n4)), 'libstdc++ declares _Destroy but defines no such symbol: a decomp name')
n5 = '_ZNSt6vectorIjSaIjEE5_XlenEv'    # the MSVC STL copy's thrower of a libstdc++ class
check(fnvlink.decomp_std(n5, it(n5)), 'std::vector<T>::_Xlen: a decomp name')
n6 = '_ZNSt8bad_castC1EPKc'            # MSVC's bad_cast(const char*)
check(fnvlink.decomp_std(n6, it(n6)), 'std::bad_cast(const char*): a decomp name')
check(not fnvlink.decomp_std('_ZSt9terminatev', it('_ZSt9terminatev')), 'an exported libstdc++ symbol: runtime')
n7 = '_ZNSt9bad_allocC1EPKc'           # defined by platform/crt/msvc_std_names.cpp
check(n7 in fnvlink.platform_std_names() and not fnvlink.decomp_std(n7, it(n7)), 'a platform/crt name: runtime')
check(not fnvlink.decomp_std('__errno_location', it('__errno_location')), 'not a std:: name')
fnvlink.STD_EXPORTS[:] = [set()]
check(not fnvlink.decomp_std(n4, it(n4)), 'empty export set: unchanged')
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
