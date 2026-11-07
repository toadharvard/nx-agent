#!/usr/bin/env python3
"""tests/s13/test_boolrepr.py - d19-bool: the bool-repr probe counts an input byte only when the oracle reads that
byte by itself (no decomp content). A bool the MSVC code normalises and GCC returns raw is bool-repr; the top byte of
a word the oracle reads whole (a pointer, an integer) that removes the divergence when set to 1 is not."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13bool'))

import classify   # noqa: E402
import conv       # noqa: E402
import fnkeys     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


MEM = 0x03000000
# int f(bool*): mov eax, [esp+4]; movzx eax, byte [eax]; test eax, eax; setne al; movzx eax, al; ret
BOOL_X86 = bytes.fromhex('8b442404' '0fb600' '85c0' '0f95c0' '0fb6c0' 'c3')
BOOL_A64 = '\tldrb w0, [x0]\n\tret\n'
# int f(int*): mov eax, [esp+4]; mov eax, [eax]; shr eax, 24; ret   (the native keeps bit 0 of the top byte only)
WORD_X86 = bytes.fromhex('8b442404' '8b00' 'c1e818' 'c3')
WORD_A64 = '\tldr w0, [x0]\n\tlsr w0, w0, #24\n\tand w0, w0, #1\n\tret\n'


def hint(sym, oracle, body, mem):
    cv = conv.Conv(sym, 'test')
    fn = dict(code=oracle, address=0x00401000)
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, build_image(body, d), {})
        p = runner.Pair(t, {})
        c = world.Case(seed=3, args={'p0': MEM.to_bytes(4, 'little')}, mem={MEM: mem})
        rx, ra, div = p.run(c)
        return div, (classify.hint(p, t, c, div, fn, 200000) if div else {})


def main():
    div, h = hint('?f@@YAHPA_N@Z', BOOL_X86, BOOL_A64, bytes([5, 0, 0, 0]))
    check(div is not None and h.get('cause') == 'bool-repr' and h.get('bytes') == ['%08x' % MEM],
          'a byte read by itself and normalised by MSVC: bool-repr (%s)' % h)
    div, h = hint('?f@@YAHPAH@Z', WORD_X86, WORD_A64, bytes([0, 0, 0, 3]))
    check(div is not None and h.get('cause') != 'bool-repr',
          'the top byte of a word read whole: not bool-repr (%s)' % h)
    ch = [c for c in fnkeys.CHANGES if c.tag == 'd19-bool']
    check(len(ch) == 1 and ch[0].recheck({'hint': {'cause': 'bool-repr'}}) and
          not ch[0].recheck({'hint': {'cause': 'nan-bits'}}) and fnkeys.SCOPED == 'd19',
          'd19-bool rechecks old bool-repr records; SCOPED d19')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
