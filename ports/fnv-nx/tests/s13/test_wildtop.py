#!/usr/bin/env python3
"""tests/s13/test_wildtop.py - a wild call to the last bytes of the address space (difffuzz-20 farm baseline), on
hand-written code.

A call through a garbage word 0xffffff80 (a function-pointer field the fuzzer filled with -128) is an intercepted wild
call on both machines. The oracle's block starting there ran past 4 GiB, wrapped to address 0 and faulted
(fault:x:00000000) while the native made the call (0x00aff640, BSFileW::ReadWideString's read handler).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13wildtop'))

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
            '\tmovz w16, #0xff80\n\tmovk w16, #0xffff, lsl #16\n\tblr x16\n'
            '\tldp x29, x30, [sp], #16\n\tret\n')
    oracle = bytes.fromhex('b880ffffff' 'ffd0' 'c3')            # mov eax, 0xffffff80; call eax; ret
    with tempfile.TemporaryDirectory() as td:
        img = build_image(body, td)
        p = runner.Pair(runner.Target(cvv, 0x00401000, {0x00401000: oracle}, img, {}), {})
        rx, ra, dv = p.run(world.Case(seed=1))
        ta = ['%08x' % (c.target & 0xffffffff) for c in ra.calls]
        tx = ['%08x' % (c.target & 0xffffffff) for c in rx.calls]
        check(ra.error is None, 'native: no fault (%s)' % ra.error)
        check(ta == ['ffffff80'] and tx == ta, 'both machines make the wild call at the end of the address space (%s, %s)' % (tx, ta))
        check(dv is None, 'no divergence (%s)' % dv)
    print('FAIL: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
