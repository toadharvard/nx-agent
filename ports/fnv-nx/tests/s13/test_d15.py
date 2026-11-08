#!/usr/bin/env python3
"""tests/s13/test_d15.py - difffuzz-15 (fnkeys d15-vcall) on hand-written code (no decomp content).

  pops     an intercepted call of unknown convention whose caller does not clean up: the oracle used to return to
           an argument word (fault:x); the pair learns the callee's `ret 8` from the pushes before the call site
  match    an oracle call through memory the case did not plant matches whatever the native calls at that index
           (it was `oracle-vcall`, ending the comparison); a planted or direct target is still compared
  keys     d15-vcall selects the functions whose oracle closure makes indirect calls

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d15'))

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


# void __stdcall f(void **p): p[0](2, 1) as a stdcall call through a pointer the case fills (pops 8), then p[1]()
ORACLE = bytes.fromhex('56'              # push esi
                       '8b742408'        # mov esi, [esp+8]
                       '6a01' '6a02'     # push 1; push 2
                       'ff16'            # call [esi]
                       'ff5604'          # call [esi+4]
                       '5e'              # pop esi
                       'c20400')         # ret 4
ARM_SAME = ('\tstp x29, x30, [sp, #-32]!\n\tstr x19, [sp, #16]\n\tmov w19, w0\n\tldr w8, [x19]\n'
            '\tmov w0, #2\n\tmov w1, #1\n\tblr x8\n\tldr w8, [x19, #4]\n\tblr x8\n'
            '\tldr x19, [sp, #16]\n\tldp x29, x30, [sp], #32\n\tret\n')
# the native reads its first pointer one word further (its own layout of the unplanted bytes)
ARM_OTHER = ARM_SAME.replace('ldr w8, [x19]\n', 'ldr w8, [x19, #8]\n')


def case():
    return world.Case(seed=9, args={}, mem={world.BUF: struct.pack('<III', 0x41414140, 0x52525250, 0x63636360)})


def pair_for(body, d):
    cv = conv.Conv('?f@@YGXPAPAX@Z', 'test')
    img = build_image(body, d)
    return runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, img, {}), {})


def test_pops():
    with tempfile.TemporaryDirectory() as d:
        p = pair_for(ARM_SAME, d)
        rx, ra, div = p._run(case(), 200000)
        check((rx.error or '').startswith('fault:'), 'without the learned pop the oracle returns to garbage (%s)' %
              rx.error)
        rx, ra, div = p.run(case())
        check(rx.error is None and rx.esp_ok and p.pops.get(0x0040100b) == 8,
              'learned: the callee at 0040100b pops 8 (%s)' % {hex(k): v for k, v in p.pops.items()})
        check(div is None and [c.target for c in rx.calls] == [0x41414140, 0x52525250],
              'then both machines agree (%s)' % (div and div['kind']))
        rx2, _, div2 = p.run(world.Case(seed=10, args={}, mem=case().mem))
        check(rx2.error is None and div2 is None, 'the learned pop holds for the next case')


def test_match():
    with tempfile.TemporaryDirectory() as d:
        p = pair_for(ARM_OTHER, d)
        rx, ra, div = p.run(case())
        check([c.target for c in ra.calls][:1] == [0x63636360] and div is None,
              'unplanted oracle call vs another native target: matched, comparison goes on (%s)' % (
                  div and div['kind']))
        # a direct (known) oracle target is still compared: the vcalls of the pair say 0x41414140 is a direct call
        p._vc, p._vc_key = ({0x00401000, 0x41414140}, set()), len(p.target.x86_code)
        rx, ra, div = p._run(case(), 200000)
        check(div is not None and div['kind'] == 'call-target', 'a known oracle target still differs (%s)' % (
            div and div['kind']))


def test_keys():
    cat = [dict(address='00401000', source='a', calls=['00401100'], tails=[], indirect=0),
           dict(address='00401100', source='a', calls=[], tails=[], indirect=2),
           dict(address='00401200', source='a', calls=[], tails=[], indirect=0)]
    ind = set()
    fnkeys.oracle_closure(cat, ind)
    check(ind == {'00401000', '00401100'}, 'indirect calls through same-unit callees: %s' % sorted(ind))
    check('d15-vcall' in fnkeys.change_tags(fnkeys.Facts('?f@@YAXXZ', oindirect=True)) and
          fnkeys.change_tags(fnkeys.Facts('?f@@YAXXZ')) == [], 'd15-vcall selects only those')
    check(fnkeys.SCOPED == fnkeys.CHANGES[-1].tag.split('-')[0], 'SCOPED names the newest change')


if __name__ == '__main__':
    test_pops()
    test_match()
    test_keys()
    print('%d failures' % len(FAIL))
    sys.exit(1 if FAIL else 0)
