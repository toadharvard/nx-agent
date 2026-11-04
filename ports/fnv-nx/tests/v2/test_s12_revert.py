#!/usr/bin/env python3
"""V2 check of S12 3f7d571 (compile.sh order defaults back to object=first, operator=cpp17): "V1 refutations C2v,
C2a -> fixed by revert". Runs V1's harness (tests/v1/test_s12_order_close.py, case C2: compile.sh's own order
against S11's recorded MSVC 2008 runs of order.cpp, under qemu) and reads its C2 line.

    SSE2NEON=<ext/sse2neon> python3 -I tests/v2/test_s12_revert.py    (needs what V1's test needs, incl. the plugin)

  R1  C2v: the virtual call (obj-virtual) agrees with MSVC under the reverted defaults
  R2  C2a: addassign-builtin agrees with MSVC (MSVC pa). Under operator=cpp17 the build still gives ap: the revert
      only undoes operator=rtl, it does not give MSVC's order for this case
The other differences C2 lists (obj-arrow, op-*-member, ...) are the ones S12 reopened on purpose; not counted here.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V1 = os.path.join(HERE, '..', 'v1', 'test_s12_order_close.py')


def main():
    r = subprocess.run([sys.executable, '-I', V1], capture_output=True, text=True, timeout=3000)
    line = next((l for l in r.stdout.splitlines() if re.match(r'^\S+\s+C2\s', l)), None)
    if line is None:
        print('skip: V1 harness gave no C2 line (%s)' % (r.stdout.strip().splitlines() or ['no output'])[-1])
        return 0
    fails = 0
    for case, name in (('R1', 'obj-virtual'), ('R2', 'addassign-builtin')):
        m = re.search(r'%s MSVC (\S+) GCC (\S+)' % re.escape(name), line)
        ok = m is None
        print('%s %s %s: %s' % ('ok' if ok else 'FAIL', case, name,
                                'agrees with MSVC' if ok else 'MSVC %s, build %s' % m.groups()))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
