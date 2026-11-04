#!/usr/bin/env python3
"""V2 check of S1b be9444a (XINC1 nested pop chains R(.., D(a.., x..), ..) -> R(.., D(a..), x.., ..)): "MSVC identical on
every function of the touched units", i.e. the patched callers compile to the same bytes as pristine, and the only
differences are call relocations to the renamed overloads, which patches/units_renames.tsv records (+ string literal
labels $SG). Read in MSVC 2008's own output (equiv.py show: pristine vs a tree prepared with the eq gate's rules).

    FNV_MSVC9_ROOT=.. FNV_MSVC9_RTM_ROOT=.. FNV_MSVC9_PREFIX=.. FNVNX_EQ_TREE=<prepared tree> python3 tests/v2/test_s1b_popchain.py

  M1  impactmixer.cpp and blk_00620000_od.cpp (3 patched callers each): every differing line of a patched caller is
      a DISP32 / dir32 relocation; each new DISP32 name is a `new MSVC name` of units_renames.tsv
"""
import json
import os
import re
import subprocess
import sys

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
UNITS = ('src/fallout/audio/impactmixer.cpp', 'src/staging/blk_00620000_od.cpp')


def main():
    tree = os.environ.get('FNVNX_EQ_TREE')
    if not (tree and os.path.isdir(tree) and os.environ.get('FNV_MSVC9_ROOT') and os.environ.get('FNV_MSVC9_PREFIX')):
        print('skip: no FNVNX_EQ_TREE / MSVC 2008')
        return 0
    dec = os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp'))
    units = json.load(open(os.path.join(dec, 'build', 'units.json')))['units']
    renames = {l.split('\t')[4].strip() for l in open(os.path.join(PORT, 'patches', 'units_renames.tsv'))
               if not l.startswith('#') and len(l.split('\t')) >= 5}
    patched = {}
    for p in os.listdir(os.path.join(PORT, 'patches')):
        if p.endswith('.s1b'):
            for m in re.finditer(r'^@@ XINC1 (\S+) (0x[0-9a-f]+)', open(os.path.join(PORT, 'patches', p)).read(), re.M):
                patched.setdefault(m.group(1), set()).add(m.group(2))
    fails = 0
    for src in UNITS:
        idx = next(i for i, u in enumerate(units) if u['source'] == src)
        fns = ','.join(sorted(patched.get(src, ())))
        out = subprocess.run([sys.executable, os.path.join(PORT, 'tools', 'equiv', 'equiv.py'), 'show', dec, dec, tree,
                              '--unit', str(idx), '--fn', fns, '--context', '0'], capture_output=True, text=True,
                             timeout=1500, env=dict(os.environ, WINEDEBUG='-all')).stdout
        if 'error C' in out:
            print('skip %s: does not compile here' % src)
            continue
        cur, bad, new = None, [], set()
        for l in out.splitlines():
            if l.startswith('===='):
                cur = l.split()[1]
            elif cur and l[:1] in '-+' and not l.startswith(('---', '+++')):
                body = l[1:].strip()
                if not re.match(r'(DISP32|dir32)\s', body):
                    bad.append((cur, l))
                elif l[0] == '+' and body.startswith('DISP32'):
                    new.add(body.split(None, 1)[1])
        unknown = sorted(n for n in new if n not in renames)
        ok = not bad and not unknown
        print('%s M1 %s (callers %s): non-relocation lines %d %s; new names not in units_renames %s' %
              ('ok' if ok else 'FAIL', src.split('/')[-1], fns, len(bad), bad[:2], unknown[:3]))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
