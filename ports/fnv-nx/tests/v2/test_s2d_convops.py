#!/usr/bin/env python3
"""V2 check of S2d 8c2cfc2 (sigs.parse_msvc reads conversion operators to pointer types): names MSVC 2008 SP1 and GCC
(AArch64 ILP32) emit for the same synthetic class (recorded here: tests/v2 keeps no decomp text), paired through the
link's own keys.

    python3 tests/v2/test_s2d_convops.py      # llvm-undname, c++filt

  K1  each MSVC conversion operator pairs at the exact tier with exactly its Itanium twin (const kept apart:
      operator A*() and operator A*() const)
  note a conversion to a function-pointer type (`operator FN()`, typedef void (*FN)()) still parses as data and
      pairs with nothing; not counted (the claim names pointer types, and the decomp spells none that way here)
"""
import os
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'link'))
import fnvlink  # noqa: E402
import sigs  # noqa: E402

PAIRS = [  # MSVC 2008 SP1 decoration, GCC Itanium name (struct A; struct C; template NiPointer<T>)
    ('??B?$NiPointer@UA@@@@QBEPAUA@@XZ', '_ZNK9NiPointerI1AEcvPS0_Ev'),
    ('??BC@@QAEAAHXZ', '_ZN1CcvRiEv'),
    ('??BC@@QAEPAPAUA@@XZ', '_ZN1CcvPP1AEv'),
    ('??BC@@QAEPAUA@@XZ', '_ZN1CcvP1AEv'),
    ('??BC@@QBEPAUA@@XZ', '_ZNK1CcvP1AEv'),
    ('??BC@@QBEPBDXZ', '_ZNK1CcvPKcEv'),
    ('??BC@@QBEPBQBUA@@XZ', '_ZNK1CcvPKPK1AEv'),
    ('??BC@@QBE_NXZ', '_ZNK1CcvbEv'),
]
FNPTR = ('??BC@@QAEP6AXXZXZ', '_ZN1CcvPFvvEEv')


def main():
    ms = [m for m, _ in PAIRS] + [FNPTR[0]]
    it = [i for _, i in PAIRS] + [FNPTR[1]]
    dm = fnvlink.batch_demangle(fnvlink.UNDNAME, ms)
    di = fnvlink.batch_demangle(fnvlink.CXXFILT, it)
    ex = lambda s: {k for t, k in s.keys() if t == 'exact'} if s is not None and getattr(s, 'kind', None) == 'func' else set()
    ik = {i: ex(sigs.parse_itanium(i, di[i])) for i in it}
    fails = 0
    for m, want in PAIRS:
        k = ex(sigs.parse_msvc(m, dm[m]))
        hits = [i for i in it if k & ik[i]]
        ok = hits == [want]
        print('%s K1 %s -> %s' % ('ok' if ok else 'FAIL', m, hits))
        fails += not ok
    s = sigs.parse_msvc(FNPTR[0], dm[FNPTR[0]])
    print('note function-pointer conversion %s: kind %s, pairs %s' % (FNPTR[0], getattr(s, 'kind', None),
                                                                     bool(ex(s) & ik[FNPTR[1]])))
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
