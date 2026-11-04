#!/usr/bin/env python3
"""V2 check of S11 e44dbc4 (PTR1-UNSIGNED-ZERO rows of explained.tsv), read in MSVC 2008's own output: pristine decomp
vs a tree prepared with the eq gate's rules (FNVNX_EQ_TREE), equiv.py show per function.

    FNV_MSVC9_ROOT=.. FNV_MSVC9_RTM_ROOT=.. FNV_MSVC9_PREFIX=.. FNVNX_EQ_TREE=<prepared tree> python3 tests/v2/test_s11_ptr1.py

  U1-U3  playercharacter 0x00946020, tesrecipe 0x005a8110, blk_00c60000_o2 0x00c69580: the whole difference is one
         conditional jump, ja -> jne or jbe -> je, right after `test r, r` or `cmp x, 0` (CF = 0: the same branch)
"""
import json
import os
import re
import subprocess
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
ROWS = [('U1', 'src/fallout/ai/playercharacter.cpp', '0x00946020', 'ja', 'jne'),
        ('U2', 'src/fallout_shared/tesrecipe.cpp', '0x005a8110', 'jbe', 'je'),
        ('U3', 'src/staging/blk_00c60000_o2.cpp', '0x00c69580', 'jbe', 'je')]


def main():
    tree = os.environ.get('FNVNX_EQ_TREE')
    if not (tree and os.path.isdir(tree) and os.environ.get('FNV_MSVC9_ROOT') and os.environ.get('FNV_MSVC9_PREFIX')):
        print('skip: no FNVNX_EQ_TREE / MSVC 2008')
        return 0
    dec = os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp'))
    units = json.load(open(os.path.join(dec, 'build', 'units.json')))['units']
    fails = 0
    for case, src, addr, old, new in ROWS:
        idx = next(i for i, u in enumerate(units) if u['source'] == src)
        out = subprocess.run([sys.executable, os.path.join(PORT, 'tools', 'equiv', 'equiv.py'), 'show', dec, dec, tree,
                              '--unit', str(idx), '--fn', addr, '--context', '1'], capture_output=True, text=True,
                             timeout=1500, env=dict(os.environ, WINEDEBUG='-all')).stdout.splitlines()
        body = [l for l in out if not l.startswith(('====', '---', '+++', '@@'))]
        rem = [l[1:].split()[0] for l in body if l.startswith('-')]
        add = [l[1:].split()[0] for l in body if l.startswith('+')]
        i = next((k for k, l in enumerate(body) if l.startswith('-')), None)
        before = body[i - 1].strip() if i else ''
        zero = re.match(r'(test\s+(\w+),\s*\2|cmp\s+.*,0x0)$', before) is not None
        ok = rem == [old] and add == [new] and zero
        print('%s %s %s %s: removed %s added %s after `%s`' % ('ok' if ok else 'FAIL', case, src.split('/')[-1], addr,
                                                               rem, add, before))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
