#!/usr/bin/env python3
"""tests/s13/test_crtmalloc.py - S1-rules d20-malloc: the oracle's CRT malloc (0x00ecd1c7) pairs with the native's
MemoryManager::Allocate (0x00aa3e40) and both return the same fresh heap block (no decomp content)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13malloc'))

import conv       # noqa: E402
import fnkeys     # noqa: E402
import world      # noqa: E402
from test_d14 import call_rel, run, stub  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def main():
    cv = conv.Conv('?f@@YAPAXXZ', 'test')                   # void* __cdecl f(void)
    # push 8; call malloc; add esp, 4; mov dword [eax], 5; ret
    ora = b'\x6a\x08' + call_rel(0x00401002, 0x00ecd1c7) + bytes.fromhex('83c404' 'c70005000000' 'c3')
    arm = ('\tstp x29, x30, [sp, #-16]!\n\tmov w0, #8\n\tbl __fnv_00aa3e40\n\tmov w1, #5\n\tstr w1, [x0]\n'
           '\tldp x29, x30, [sp], #16\n\tret\n')
    rx, ra, d = run(cv, ora, arm, world.Case(seed=1), [stub(0x00aa3e40)])
    check(d is None, 'malloc (oracle) vs MemoryManager::Allocate (native): no divergence (%s)' % d)
    check(rx.ret == ra.ret and int.from_bytes(rx.ret[:4], 'little') == world.alloc_result(0),
          'both return the same fresh block (%s %s)' % (rx.ret.hex() if rx.ret else None, ra.ret.hex() if ra.ret else None))
    check('d20-malloc' in fnkeys.change_tags(fnkeys.Facts('?f@@YAPAXXZ', ocalls={0x00ecd1c7})) and
          'd20-malloc' not in fnkeys.change_tags(fnkeys.Facts('?f@@YAPAXXZ', ocalls={0x00401000})),
          'd20-malloc selects the functions whose oracle code calls malloc')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
