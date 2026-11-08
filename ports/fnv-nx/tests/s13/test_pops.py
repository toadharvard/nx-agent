#!/usr/bin/env python3
"""tests/s13/test_pops.py - pop learning with many intercepted call sites (pops, difffuzz-20), on hand-written code.

  sequential  five planted `call [esi+4k]` sites of different pops (8, 4, 0, 12, 4): the joint search of the first
              three sites left the later ones to their guess, and the fourth passes its arguments as a `sub esp, 12`
              block that the push count missed, so the oracle stayed derailed. The sites are now fixed one at a
              time in run order, and a clean run (no fault, esp balanced, every call returning into the code) is
              what ends the search.
  subesp      _pushes_before counts a `sub esp, N` argument block (but not the prologue's frame)

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13pops'))

import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


ORACLE = bytes.fromhex('56'                  # push esi
                       '8b742408'            # mov esi, [esp+8]
                       '56' '56' 'ff16'      # push esi; push esi; call [esi]        (stdcall, pops 8)
                       '56' 'ff5604'         # push esi; call [esi+4]                (pops 4)
                       'ff5608'              # call [esi+8]                          (pops 0)
                       '83ec0c'              # sub esp, 12                           (an argument block)
                       '893424' '89742404' '89742408'
                       'ff560c'              # call [esi+12]                         (pops 12)
                       '56' 'ff5610'         # push esi; call [esi+16]               (pops 4)
                       '5e'                  # pop esi
                       'c20400')             # ret 4
NATIVE = ('\tstp x29, x30, [sp, #-32]!\n\tstr x19, [sp, #16]\n\tmov w19, w0\n'
          + ''.join('\tldr w8, [x19, #%d]\n\tmov w1, w19\n\tblr x8\n' % (4 * k) for k in range(5))
          + '\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n\tret\n')
WORDS = (0x41414140, 0x52525250, 0x63636360, 0x74747470, 0x85858580)


def case(seed=9):
    return world.Case(seed=seed, args={}, mem={world.BUF: struct.pack('<5I', *WORDS)})


def test_sequential():
    with tempfile.TemporaryDirectory() as d:
        cv = conv.Conv('?f@@YGXPAPAX@Z', 'test')
        p = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, build_image(NATIVE, d), {}), {})
        rx, ra, div = p.run(case())
        got = sorted(p.pops.values())
        check(rx.esp_ok and not (rx.error or '').startswith('fault:'),
              'the run is clean and balanced (%s, esp_ok %s)' % (rx.error, rx.esp_ok))
        check(got == [4, 4, 8, 12], 'learned pops 8, 4, 12, 4 (0 is the default): %s' % got)
        check([c.target for c in rx.calls] == list(WORDS), 'all five planted calls ran, no derail calls')
        check(div is None, 'then both machines agree (%s)' % (div and div['kind']))


def test_subesp():
    # push ebp; mov ebp, esp; sub esp, 0x40; sub esp, 0xc; call [esi]: the frame is not an argument block
    code = bytes.fromhex('55' '89e5' '83ec40' '83ec0c' 'ff16' 'c3')
    p = runner.Pair(runner.Target(None, 0x00401000, {0x00401000: code}, None, {}), {})
    n = p._pushes_before(0x00401000 + 11)
    check(n == 3, 'a `sub esp, 12` block counts three words, the frame none: %d' % n)


if __name__ == '__main__':
    test_sequential()
    test_subesp()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
