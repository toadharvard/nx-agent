#!/usr/bin/env python3
"""tests/s13/test_wildvt.py - native-wild-private (D24, difffuzz-20), on hand-written code.

A jump to a garbage address inside the native's synthesized vtable region (0x0bc00000..0x0c000000) is a wild call on
both machines: the oracle intercepts every address outside its text, the native used to fault there (the region was
data to read but a fetch faulted: fault:x:0bd7f08d with no oracle error).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13wildvt'))

import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def main():
    cvv = conv.Conv('?f@@YAXXZ', 'test')
    body = ('\tstp x29, x30, [sp, #-16]!\n'
            '\tmovz w16, #0xf08d\n\tmovk w16, #0x0bd7, lsl #16\n\tblr x16\n'
            '\tldp x29, x30, [sp], #16\n\tret\n')
    oracle = bytes.fromhex('b88df0d70b' 'ffd0' 'c3')            # mov eax, 0x0bd7f08d; call eax; ret
    with tempfile.TemporaryDirectory() as td:
        img = build_image(body, td)
        p = runner.Pair(runner.Target(cvv, 0x00401000, {0x00401000: oracle}, img, {}), {})
        rx, ra, dv = p.run(world.Case(seed=1))
        ta = ['%08x' % (c.target & 0xffffffff) for c in ra.calls]
        tx = ['%08x' % (c.target & 0xffffffff) for c in rx.calls]
        check(ra.error is None, 'native: no fault (%s)' % ra.error)
        check(ta == ['0bd7f08d'] and tx == ta, 'both machines make the wild call (%s, %s)' % (tx, ta))
        check(dv is None, 'no divergence (%s)' % dv)
    print('FAIL: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
