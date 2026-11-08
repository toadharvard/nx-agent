#!/usr/bin/env python3
"""The ABI check of name-addr pairs (S2 s2d-010 2a): references bound by the address in a Ghidra-style name have no
MSVC declaration, so resolve compares both Itanium names (name_addr_sigs) under one convention rule. Equal native
argument shapes are the same call; abi's bridge is taken; any other difference is flagged code-manual. Our own
names only."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi  # noqa: E402
import fnvlink  # noqa: E402
import sigs  # noqa: E402

fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:400]))
    fails += not cond


def verdict(rs, ds):
    """resolve's rule for one pair: same / bridge / flagged"""
    if [c for _, c, _ in abi.a64_locations(rs)] == [c for _, c, _ in abi.a64_locations(ds)]:
        return 'same'
    v = abi.compare(rs, ds)
    return {'same': 'same', 'bridge': 'bridge'}.get(v.kind, 'flagged')


def main():
    pairs = [   # (reference, definition, want)
        ('_ZN12fnv_00ae755012FUN_00c45830EPNS_3ObjE', '_Z12FUN_00c45830P3Obj', 'same'),   # a namespace, not a class
        ('_ZN1B12FUN_00402000Ev', '_Z12FUN_00402000P1B', 'same'),                         # this in x0 = B* in x0
        ('_ZN1B12FUN_00403000Ei', '_Z12FUN_00403000P1Bi', 'same'),                        # natively identical
        ('_ZN1A12FUN_00401000Ef', '_ZN1A12FUN_00401000Ei', 'bridge'),                     # float vs int: v0 vs x1
        ('_ZN1B12FUN_00404000Ei', '_Z12FUN_00404000i', 'flagged'),                        # `this` where an int is read
    ]
    names = [n for p in pairs for n in p[:2]]
    d = fnvlink.batch_demangle(fnvlink.CXXFILT, names)
    for r, f, want in pairs:
        rs, ds = fnvlink.name_addr_sigs(sigs.parse_itanium(r, d[r]), sigs.parse_itanium(f, d[f]))
        ok('%s -> %s: %s' % (d[r], d[f], want), rs is not None and verdict(rs, ds) == want,
           rs and verdict(rs, ds))
    # with the definition's MSVC declaration (resolve uses it when it has one): a member reference against a
    # __fastcall definition with an unused edx gets abi's edx-dummy bridge
    ms = fnvlink.batch_demangle(fnvlink.UNDNAME, ['?FUN_00405000@@YIXPAUArr@@HI_N@Z'])
    dm = sigs.parse_msvc('?FUN_00405000@@YIXPAUArr@@HI_N@Z', ms['?FUN_00405000@@YIXPAUArr@@HI_N@Z'])
    rn = '_ZN3Arr12FUN_00405000Ejb'
    rs, _ = fnvlink.name_addr_sigs(sigs.parse_itanium(rn, fnvlink.batch_demangle(fnvlink.CXXFILT, [rn])[rn]), dm)
    v = abi.compare(rs, dm)
    ok('member -> __fastcall(T *, int edx, ...) with the MSVC definition: the edx-dummy bridge',
       v.kind == 'bridge' and 'edx-dummy' in v.classes, (v.kind, v.classes, v.moves))
    rs, ds = fnvlink.name_addr_sigs(sigs.parse_itanium('_ZN1B12FUN_00402000Ev', d['_ZN1B12FUN_00402000Ev']),
                                    sigs.parse_itanium('_ZN1B12FUN_00402000Ev', d['_ZN1B12FUN_00402000Ev']))
    ok('a class scope is a thiscall member, a fnv_ scope is not',
       rs.member and rs.cc == 'thiscall' and not fnvlink.name_addr_sigs(
           sigs.parse_itanium(pairs[0][0], d[pairs[0][0]]), sigs.parse_itanium(pairs[0][1], d[pairs[0][1]]))[0].member)
    ok('not a function: no pair', fnvlink.name_addr_sigs(sigs.Sig('data', 'x'), ds) == (None, None))
    print('test_nameaddr_abi: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
