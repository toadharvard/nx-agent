#!/usr/bin/env python3
"""tests/s13/test_d18.py - difffuzz-18: uninit-both cases rerun with the same garbage on both machines (no decomp
content). Both functions return an uninitialised stack word (each from its own frame layout): different garbage,
uninit-both; with uniform garbage they agree, so the probe answers 'agree' and fuzz.py keeps no record."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d18'))

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


ORACLE = bytes.fromhex('8b4424c0' 'c3')            # mov eax, [esp-0x40]; ret
ARM_SAME = '\tldur w0, [sp, #-0x80]\n\tret\n'      # another uninitialised word
ARM_DIFF = '\tldur w0, [sp, #-0x80]\n\tadd w0, w0, #1\n\tret\n'


def main():
    world.UNIFORM = True
    try:
        check(world.garbage32(5, 7) == 0 and world.stack_fill(5, 8) == bytes(8) and not world.garbage_words(5, 'x86'),
              'UNIFORM: every planted garbage word is 0')
    finally:
        world.UNIFORM = False
    check(world.garbage32(5, 7) != 0, 'normal garbage again')
    cv = conv.Conv('?f@@YAHXZ', 'test')
    fn = dict(code=ORACLE, address=0x00401000)
    for body, want in ((ARM_SAME, 'agree'), (ARM_DIFF, 'return')):
        with tempfile.TemporaryDirectory() as d:
            t = runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, build_image(body, d), {})
            p = runner.Pair(t, {})
            c = world.Case(seed=3)
            rx, ra, div = p.run(c)
            h = classify.hint(p, t, c, div, fn, 200000) if div else {}
            check(div is not None and h.get('cause') == 'uninit-both' and h.get('uniform') == want,
                  'uninit-both, uniform garbage -> %s (%s)' % (want, h))
    d18 = [c for c in fnkeys.CHANGES if c.tag == 'd18-uninit'][0]
    check(d18.recheck({'hint': {'cause': 'uninit-both'}}) and
          not d18.recheck({'hint': {'cause': 'uninit-arm'}}), 'd18-uninit rechecks old uninit-both')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
