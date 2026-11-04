#!/usr/bin/env python3
"""tests/s13/test_d17.py - difffuzz-17: function-local statics paired with the oracle's image copy (no decomp content).

int f(int v) { static int s; s += v; return s; }: MSVC keeps s at an image address, GCC as _ZZ1fiE1s in .bss. Paired,
both start zero and the native store counts at the oracle address: equal code agrees, a planted bug is a mem-write.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d17'))

import conv       # noqa: E402
import engine     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []
S = 0x011d0000


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


ORACLE = bytes.fromhex('a1' + S.to_bytes(4, 'little').hex() +      # mov eax, [s]
                       '03442404' +                                # add eax, [esp+4]
                       'a3' + S.to_bytes(4, 'little').hex() +      # mov [s], eax
                       'c3')
ARM = ('\tadrp x1, _ZZ1fiE1s\n\tadd x1, x1, :lo12:_ZZ1fiE1s\n\tldr w2, [x1]\n\tadd w0, w2, w0\n%s'
       '\tstr w0, [x1]\n\tret\n\t.bss\n\t.p2align 2\n\t.type _ZZ1fiE1s, %%object\n\t.size _ZZ1fiE1s, 4\n'
       '_ZZ1fiE1s:\n\t.zero 4\n')


def run(body, paired, bg='rand'):
    cv = conv.Conv('?f@@YAHH@Z', 'test')
    with tempfile.TemporaryDirectory() as d:
        img = build_image(body, d)
        t = runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, img, {}, None,
                          () if paired else range(S, S + 16))
        if paired:
            t.static_pairs, t.static_guards = [(img.syms['_ZZ1fiE1s'], S, img.sizes['_ZZ1fiE1s'])], set()
        p = runner.Pair(t, {S: bytes(4)} if paired else {})
        return p.run(world.Case(seed=1, args={'p0': (5).to_bytes(4, 'little')}, bg=bg))


def main():
    m = engine.LOCAL_VALUE.match('?s_count@?1??Get@Foo@@QAEHXZ@4HA')
    check(m and m.groups() == ('s_count', '?Get@Foo@@QAEHXZ'), 'MSVC local static: variable and function')
    g = engine.LOCAL_GUARD.match('?$S1@?1??Get@Foo@@QAEHXZ@4IA')
    check(g and g.group(1) == '?Get@Foo@@QAEHXZ' and not engine.LOCAL_VALUE.match('?$S1@?1??Get@Foo@@QAEHXZ@4IA'),
          'MSVC guard word')
    rx, ra, d = run(ARM % '', False, 'zero')
    check(d is not None and d['kind'] == 'local-static', 'unpaired: the old soft local-static (%s)' % (d and d['kind']))
    rx, ra, d = run(ARM % '', True)
    check(d is None and rx.ret == b'\x05\0\0\0' and ra.writes.get(S) == 5,
          'paired: both start zero, the native store counts at the oracle address')
    rx, ra, d = run(ARM % '\tadd w0, w0, #1\n', True)
    check(d is not None and d['kind'] == 'return', 'paired: a planted bug is found (%s)' % (d and d['kind']))
    rx, ra, d = run(ARM.replace('\tstr w0, [x1]\n', '') % '', True)
    check(d is not None and d['kind'] == 'mem-write' and d['addr'] == '%08x' % S,
          'paired: a missing native store is a mem-write at the static (%s)' % (d and d.get('addr')))
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
