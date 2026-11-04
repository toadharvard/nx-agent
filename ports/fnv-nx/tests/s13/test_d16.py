#!/usr/bin/env python3
"""tests/s13/test_d16.py - difffuzz-16: MSVC thread-local variables on the oracle (no decomp content).

The MSVC access path fs:[0x2c] -> slot array -> [__tls_index] -> block + SECREL offset reaches a zeroed private block
(not compared, like the native tpidr_el0 block); __tls_index is pinned to 0; coffobj turns a SECREL into the offset
from TLS_IMAGE_START.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13d16'))

import catalog    # noqa: E402
import coffobj    # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402
import x86emu     # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


# int f(int v): t = TLS block; old = t->x (offset 0x10); t->x = v; return old
ORACLE = bytes.fromhex('64a12c000000'      # mov eax, fs:[0x2c]
                       '8b0d98fd2601'      # mov ecx, [__tls_index 0x0126fd98]
                       '8b0488'            # mov eax, [eax + ecx*4]
                       '8b4c2404'          # mov ecx, [esp+4]
                       '8b5010'            # mov edx, [eax+0x10]
                       '894810'            # mov [eax+0x10], ecx
                       '8bc2'              # mov eax, edx
                       'c3')
ARM = '\tmrs x1, tpidr_el0\n\tldr w2, [x1, #0x40]\n\tstr w0, [x1, #0x40]\n\tmov w0, w2\n\tret\n'


def main():
    check(x86emu.TLS_ARRAY >> 12 == x86emu.TEB >> 12 and not any(
        lo <= x86emu.TLS_PTR < hi for lo, hi in world.COMPARED), 'TLS slot array in the TEB page, block private')
    check(coffobj.TLS_IMAGE_START == 0x01272000 and coffobj.TLS_INDEX == 0x0126fd98, 'TLS image constants')
    check(catalog.CATALOG_VERSION >= 2, 'catalog version marks SECREL support')
    cv = conv.Conv('?f@@YAHH@Z', 'test')
    with tempfile.TemporaryDirectory() as d:
        img = build_image(ARM, d)
        pinned = {coffobj.TLS_INDEX: b'\0\0\0\0'}
        p = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, img, {}), pinned)
        for v in (7, 0x12345678):
            # the case even fills __tls_index with fuzz bytes: the pinned 0 wins
            c = world.Case(seed=v, args={'p0': v.to_bytes(4, 'little')}, mem={coffobj.TLS_INDEX: b'\x55' * 4},
                           bg='rand')
            rx, ra, div = p.run(c)
            check(rx.error is None and rx.ret == b'\0\0\0\0' and div is None,
                  'TLS read 0 and store not compared (v=%x): %s %s' % (v, rx.error, div and div['kind']))
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
