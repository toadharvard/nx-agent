#!/usr/bin/env python3
"""V2 re-verification of S1-refs 03f2dad (V1 refutations N2, C2, C3, D2 of NEW5, COND1, DUPINT1-RET) on the head,
through V1's own test_s1refs_b.py (tests/v1, else origin/claude/fnv-v1's copy).

    python3 -I tests/v2/test_s1refs_fixes.py

The fixed NEW5 writes `FNVNX_NEW5_TAG`, which the game build gets from shim/include/fnvnx_compat.h (-include); V1's
harness compiles the rule's output with a bare g++, so this wrapper prepends the shim's two definitions
(struct fnvnx_new_tag, FNVNX_NEW5_TAG) taken from fnvnx_compat.h itself. Without them N2 fails to compile, not on
the semantics.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
CASES = ('N2', 'C2', 'C3', 'D2')


def main():
    p = os.path.join(PORT, 'tests', 'v1', 'test_s1refs_b.py')
    if os.path.isfile(p):
        src = open(p).read()
    else:
        r = subprocess.run(['git', '-C', PORT, 'show', 'origin/claude/fnv-v1:ports/fnv-nx/tests/v1/test_s1refs_b.py'],
                           capture_output=True, text=True)
        if r.returncode:
            print('skip: no test_s1refs_b.py')
            return 0
        src = r.stdout
    compat = open(os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h')).read()
    defs = [l for l in compat.splitlines() if l.startswith(('struct fnvnx_new_tag', '#define FNVNX_NEW5_TAG'))]
    if len(defs) != 2:
        print('FAIL the shim has no FNVNX_NEW5_TAG definitions')
        return 1
    src = src.replace('        f.write(PRE + src)', '        f.write(%r + PRE + src)' % ('\n'.join(defs) + '\n'))
    tmp = os.path.join(PORT, 'tests', 'v1', '.v2_s1refs_b_%d.py' % os.getpid())   # next to V1's files: its paths
    try:
        open(tmp, 'w').write(src)
        out = subprocess.run([sys.executable, '-I', tmp], capture_output=True, text=True, timeout=1500).stdout
    finally:
        os.remove(tmp)
    st = dict(re.findall(r'^(\w+): (ok|DEFECT)', out, re.M))
    fails = 0
    for c in CASES:
        ok = st.get(c) == 'ok'
        print('%s %s: %s' % ('ok' if ok else 'FAIL', c, st.get(c, 'missing')))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
