#!/usr/bin/env python3
"""tests/s13/test_ilkslow.py - S1-rules: the shim's misaligned Interlocked path (fnvnx_interlocked_slow, platform/
win32/sync.cpp) runs as the Interlocked function it performs, not as a traced call (no decomp content). The oracle
increments a misaligned word with lock xadd; the native tail-calls fnvnx_interlocked_slow(0, p, 0, 0)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13ilk'))

import a64image   # noqa: E402
import conv       # noqa: E402
import fnkeys     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


MEM = 0x03000000
# int f(long*): mov ecx, [esp+4]; mov eax, 1; lock xadd [ecx], eax; inc eax; ret   (InterlockedIncrement inline)
ORACLE = bytes.fromhex('8b4c2404' 'b801000000' 'f00fc101' '40' 'c3')
NATIVE = '\tmov w1, w0\n\tmov w0, #0\n\tmov w2, #0\n\tmov w3, #0\n\tb fnvnx_interlocked_slow\n'


def image(d):
    s = os.path.join(d, 'f.s')
    open(s, 'w').write('\t.text\n\t.globl __fnv_00401000\n\t.type __fnv_00401000, %function\n__fnv_00401000:\n' + NATIVE)
    o, elf, ld = os.path.join(d, 'f.o'), os.path.join(d, 'f.elf'), os.path.join(d, 'layout.ld')
    open(ld, 'w').write(a64image.LAYOUT % a64image.A64_BASE)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-static', '-e', '0', '-T', ld, '-o', elf, o,
                    '--defsym', 'fnvnx_interlocked_slow=0x%x' % a64image.NAMED_STUB], check=True, capture_output=True)
    return a64image.Image(elf, {a64image.NAMED_STUB: 'fnvnx_interlocked_slow'}, {})


def main():
    cv = conv.Conv('?f@@YAJPAJ@Z', 'test')
    with tempfile.TemporaryDirectory() as d:
        p = runner.Pair(runner.Target(cv, 0x00401000, {0x00401000: ORACLE}, image(d), {}), {})
        c = world.Case(seed=3, args={'p0': (MEM + 1).to_bytes(4, 'little')}, mem={MEM: bytes([0, 7, 0, 0, 0, 0, 0, 0])})
        rx, ra, div = p.run(c)
    check(div is None, 'misaligned increment: no divergence, no traced call (%s)' % div)
    check(ra.ret is not None and ra.ret == rx.ret and not ra.calls, 'native result as the oracle\'s, no call traced '
          '(%s %s)' % (ra.ret.hex() if ra.ret else None, ra.calls))
    check('d20-ilk' in fnkeys.change_tags(fnkeys.Facts('?f@@YAXXZ', nrefs={'fnvnx_interlocked_slow'})) and
          'd20-ilk' not in fnkeys.change_tags(fnkeys.Facts('?f@@YAXXZ', nrefs={'memcpy'})),
          'd20-ilk selects the functions whose native code names fnvnx_interlocked_slow')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
