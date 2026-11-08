#!/usr/bin/env python3
"""tests/s13/test_uniform_fault.py - d26-uniform: a fault on one machine that disappears when every planted garbage
word is 0 on both is uninit-x86 (the oracle read garbage), not oracle-fault (no decomp content)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13uni'))

import classify   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


# mov eax, [esp-0x40] (garbage); test eax, eax; jz +5; mov eax, [0] (null read when the garbage is nonzero);
# xor eax, eax; ret
ORACLE = bytes.fromhex('8b4424c0' '85c0' '7405' 'a100000000' '31c0' 'c3')
NATIVE = '\tmov w0, #0\n\tret\n'


def main():
    cv = conv.Conv('?f@@YAHXZ', 'test')
    fn = dict(code=ORACLE, address=0x00401000)
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, build_image(NATIVE, d), {})
        p = runner.Pair(t, {})
        c = world.Case(seed=3)
        rx, ra, div = p.run(c)
        check(div is not None and div.get('kind') == 'error' and str(div.get('x86')).startswith('fault:'),
              'the oracle faults on the garbage word, the native returns (%s)' % div)
        h = classify.hint(p, t, c, div, fn, 200000) if div else {}
        check(h.get('cause') == 'uninit-x86' and h.get('uniform') == 'agree',
              'uniform garbage: no divergence -> uninit-x86 (%s)' % h)
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
