#!/usr/bin/env python3
"""check_vftbl1.py - checks VFTBL1_SITES (s1_compile.py) against the player's exe and lists candidate sites.

    python3 tools/rules/check_vftbl1.py [--exe FalloutNV.exe] [UNITS_JSON]

Every address in VFTBL1_SITES must be an exe vftable with an RTTI complete object locator (tools/link/exevft.py); exit 1
when one is not. With units.json (default ext/fnv_decomp/build/units.json) it also prints the data names units.json binds
to an exe RTTI vftable address that is not a site (`??_7` names excluded; `DAT_` and C names only). Most are
references (an extern or a hand vptr store, which need no rule); a new site is one the decomp defines with an initialised
stand-in table. Names and addresses only are printed; nothing of
the exe is stored in the repository (the scan needs the exe, agent-only, D3).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'link'))
sys.path.insert(0, os.path.dirname(HERE))
import exevft  # noqa: E402
import prepare  # noqa: E402,F401  (s1_compile imports it)
from rules import s1_compile  # noqa: E402


def main():
    args = sys.argv[1:]
    exe = None
    if '--exe' in args:
        k = args.index('--exe')
        exe = args[k + 1]
        del args[k:k + 2]
    path = exevft.exe_path(exe)
    if not path:
        sys.exit('check_vftbl1: no exe (--exe, FNVNX_EXE or PLAYER_EXE)')
    vfts = {a: e for a, e in exevft.scan(path).items() if e.td}
    bad = sorted(a for a in s1_compile.VFTBL1_SITES if a not in vfts)
    for a in sorted(s1_compile.VFTBL1_SITES):
        print('site %08x %s' % (a, vfts[a].td if a in vfts else 'NOT AN RTTI VFTABLE'))
    uj = args[0] if args else os.path.join(os.path.dirname(os.path.dirname(HERE)), 'ext', 'fnv_decomp', 'build',
                                           'units.json')
    if os.path.exists(uj):
        cand = set()
        for u in json.load(open(uj))['units']:
            for n, a in u.get('symbols', {}).items():
                a = int(a, 16)
                if a in vfts and a not in s1_compile.VFTBL1_SITES and not n.startswith('??_7') and (
                        s1_compile.DATA_NAME.match(n) or (n.startswith('_') and n[1:].isidentifier())):
                    cand.add((a, n, u['source']))
        for a, n, src in sorted(cand):
            print('candidate %08x %s %s (%s)' % (a, vfts[a].td, n, src))
        print('%d sites, %d not RTTI vftables; %d candidate bindings' % (len(s1_compile.VFTBL1_SITES), len(bad), len(cand)))
    else:
        print('%d sites, %d not RTTI vftables' % (len(s1_compile.VFTBL1_SITES), len(bad)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
