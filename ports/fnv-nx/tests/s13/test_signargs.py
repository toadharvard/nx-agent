#!/usr/bin/env python3
"""tests/s13/test_signargs.py - difffuzz-20 sign-args: the signedness probe clears an argument byte's top bit only when
the oracle can widen that byte: the top byte of a 1- or 2-byte parameter, or of a declared-int parameter the exe reads
through a 1- or 2-byte [ebp+d] access. The sign bit of a float argument and any bit of an integer read whole are not
sign extensions (0x00e30fb0, 0x00afd390, 0x00abb700 were filed as signedness for that)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13signargs'))

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


# int f(x): push ebp; mov ebp, esp; mov eax, [ebp+8]; movzx ecx, al; shr eax, 31; pop ebp; ret  (native returns 0)
TOP_X86 = bytes.fromhex('55' '89e5' '8b4508' '0fb6c8' 'c1e81f' '5d' 'c3')
TOP_A64 = '\tmov w0, #0\n\tret\n'
# int f(int): push ebp; mov ebp, esp; movsx eax, word [ebp+8]; pop ebp; ret   (the native zero-extends)
SHORTREAD_X86 = bytes.fromhex('55' '89e5' '0fbf4508' '5d' 'c3')
SHORTREAD_A64 = '\tand w0, w0, #0xffff\n\tret\n'


def hint(sym, oracle, body, arg):
    cv = conv.Conv(sym, 'test')
    fn = dict(code=oracle, address=0x00401000)
    with tempfile.TemporaryDirectory() as d:
        t = runner.Target(cv, 0x00401000, {0x00401000: oracle}, build_image(body, d), {})
        p = runner.Pair(t, {})
        c = world.Case(seed=3, args={'p0': arg}, mem={})
        rx, ra, div = p.run(c)
        return div, (classify.hint(p, t, c, div, fn, 200000) if div else {})


def main():
    div, h = hint('?f@@YAHM@Z', TOP_X86, TOP_A64, bytes([0, 0, 0, 0xc0]))
    check(div is not None and h.get('cause') != 'signedness',
          'the sign bit of a float argument: not signedness (%s)' % h)
    div, h = hint('?f@@YAHH@Z', TOP_X86, TOP_A64, bytes([0, 0, 0, 0x80]))
    check(div is not None and h.get('cause') != 'signedness',
          'the top bit of an int read whole: not signedness (%s)' % h)
    div, h = hint('?f@@YAHH@Z', SHORTREAD_X86, SHORTREAD_A64, bytes([0, 0x80, 0, 0]))
    check(div is not None and h.get('cause') == 'signedness' and h.get('bytes') == ['p0+1'],
          'an int the exe reads as a short (movsx word [ebp+8]): signedness on its byte 1 (%s)' % h)
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
