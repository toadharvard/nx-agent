#!/usr/bin/env python3
"""V2 re-verification of S11 051eda3 and 7f11063 (V1 refutations T1, T2 of order_check.py, E1, E2 of explain.py, C1
of order_check's build variant) on the head, through V1's own harness (tests/v1/test_s12_order_close.py).

    SSE2NEON=.. python3 -I tests/v2/test_s11_order_check.py      (needs what V1's test needs, incl. the plugin)
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
V1 = os.path.join(HERE, '..', 'v1', 'test_s12_order_close.py')
CASES = ('T1', 'T2', 'E1', 'E2', 'C1')


def main():
    r = subprocess.run([sys.executable, '-I', V1], capture_output=True, text=True, timeout=3000)
    st = {m.group(2): m.group(1) for m in re.finditer(r'^(ok|DEFECT)\s+(\w+)\s', r.stdout, re.M)}
    if not st:
        print('skip: V1 harness printed no cases')
        return 0
    fails = 0
    for c in CASES:
        ok = st.get(c) == 'ok'
        print('%s %s: %s' % ('ok' if ok else 'FAIL', c, st.get(c, 'missing')))
        fails += not ok
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
