#!/usr/bin/env python3
"""V2 check of S11 480be07's explained.tsv rows, read in MSVC's own output: pristine decomp vs a tree prepared with
the eq gate's rules (equiv.py prepare --rules neutral,FTOL1,STL1,STD1,ANON1,ANON2), one unit compiled by MSVC 2008
under Wine (equiv.py show). Stack-slot offsets and branch targets are normalized (frame layout and code length);
what is left must be exactly what the row says.

    FNV_MSVC9_ROOT=.. FNV_MSVC9_RTM_ROOT=.. FNV_MSVC9_PREFIX=.. FNVNX_EQ_TREE=<prepared tree> \
        python3 tests/v2/test_s11_explained.py          (skip without them; the disassembly stays in memory)

  E1  hkpcollidecapsuleutil 0x00cd6520 X87-SCHEDULE: only two commuted x87 multiplies
      (`fld m; fmul st,st(k)` <-> `fld st(k-1); fmul m`): exact
  E2  hkpcollidecapsuleutil 0x00cd58b0 X87-EXCESS-PRECISION: one float store/reload (the rounding) dropped, one
      commuted fadd, and the compare rewritten from `fxch; fcomp st(1); test ah,0x41; jne` to
      `fcom st(1); fstp st(1); test ah,0x5; jp`: both jump when b <= a or unordered (C0/C3 of b vs a, C0=C2 of a vs b)
"""
import collections
import os
import re
import subprocess
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
UNIT_SRC = 'src/havok/physics/collide/util/hkpcollidecapsuleutil.cpp'
WANT = {
    '0x00cd6520': ({'fld    DWORD PTR [esp+esi*1+0x50]': 2, 'fmul   st,st(3)': 1, 'fmul   st,st(2)': 1},
                   {'fld    st(2)': 1, 'fmul   DWORD PTR [esp+esi*1+0x50]': 2, 'fld    st(1)': 1}),
    '0x00cd58b0': ({'fstp   DWORD PTR [esp+S]': 1, 'fadd   DWORD PTR [esp+S]': 1, 'fld    DWORD PTR [esi+0x1c]': 1,
                    'fxch   st(1)': 1, 'fcomp  st(1)': 1, 'test   ah,0x41': 1, 'jne': 1},
                   {'fadd   DWORD PTR [esi+0x1c]': 1, 'fcom   st(1)': 1, 'fstp   st(1)': 1, 'test   ah,0x5': 1,
                    'jp': 1}),
}


def norm(s):
    s = re.sub(r'\[esp\+0x[0-9a-f]+\]', '[esp+S]', s.strip())
    m = re.match(r'(j\w+|call)\s+[0-9a-f]+ <', s)
    return m.group(1) if m else s


def main():
    tree = os.environ.get('FNVNX_EQ_TREE')
    if not (tree and os.path.isdir(tree) and os.environ.get('FNV_MSVC9_RTM_ROOT') and os.environ.get('FNV_MSVC9_PREFIX')):
        print('skip: no FNVNX_EQ_TREE / MSVC 2008')
        return 0
    import json
    dec = os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp'))
    units = json.load(open(os.path.join(dec, 'build', 'units.json')))['units']
    idx = next(i for i, u in enumerate(units) if u['source'] == UNIT_SRC)
    r = subprocess.run([sys.executable, os.path.join(PORT, 'tools', 'equiv', 'equiv.py'), 'show', dec, dec, tree,
                        '--unit', str(idx), '--fn', ','.join(WANT), '--context', '0'], capture_output=True, text=True,
                       timeout=1500, env=dict(os.environ, WINEDEBUG='-all'))
    fns, cur = {}, None
    for l in r.stdout.splitlines():
        if l.startswith('===='):
            cur = l.split()[1]
            fns[cur] = (collections.Counter(), collections.Counter())
        elif cur and not l.startswith(('---', '+++', '@@')) and l[:1] in '-+':
            fns[cur][0 if l[0] == '-' else 1][norm(l[1:])] += 1
    fails = 0
    for case, (a, want) in zip(('E1', 'E2'), WANT.items()):
        got = fns.get(a)
        if got is None:
            print('FAIL %s %s: no diff printed (%s)' % (case, a, r.stdout.strip()[-200:]))
            fails += 1
            continue
        rem, add = got[0] - got[1], got[1] - got[0]
        # branches only moved: the same jumps on both sides cancel; what is left must be the row's
        rem = {k: v for k, v in rem.items() if v}
        add = {k: v for k, v in add.items() if v}
        ok = rem == want[0] and add == want[1]
        print('%s %s %s: removed %s added %s' % ('ok' if ok else 'FAIL', case, a, rem, add))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
