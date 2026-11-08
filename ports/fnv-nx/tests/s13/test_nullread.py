#!/usr/bin/env python3
"""tests/s13/test_nullread.py - EQ native-null-read class (difffuzz-20): three harness fixes, on synthetic functions
(no decomp content).

1. own copy: the native faults inside its unit's own `FUN_<X>` which the oracle only called out (X outside the oracle
   code, canned result). The pair now runs the oracle's X for real and compares again (00b3ba90: both fault at +0x2d).
2. vtable tail: a synthesized native vtable group's page holds the world's background after the group's last slot, as
   the oracle's memory does after its vftable (was zeros: a call through a slot past the end faulted at 0 natively
   and called a wild address on the oracle; 00511120). It is refilled for every case (the engine is reused).
3. virtual stub: the oracle called slot X of a planted vftable and the native a named stub (a virtual the link binds
   to no definition). That is not an inlined X: X is not followed (it ran the exe's body against the stub's canned
   result, then the native dereferenced the result: 00477560, 009abe40; the verdict is now call-target
   native-undefined).

Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13nr'))

import a64image   # noqa: E402
import arm64emu   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402

FAIL = []
F = 0x00401000
X = 0x00402000


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def image(d, native):
    s = os.path.join(d, 'f.s')
    open(s, 'w').write('\t.text\n\t.globl __fnv_%08x\n\t.type __fnv_%08x, %%function\n__fnv_%08x:\n' % (F, F, F)
                       + native)
    o, elf, ld = os.path.join(d, 'f.o'), os.path.join(d, 'f.elf'), os.path.join(d, 'layout.ld')
    open(ld, 'w').write(a64image.LAYOUT % a64image.A64_BASE)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-static', '-e', '0', '-T', ld, '-o', elf, o],
                   check=True, capture_output=True)
    return a64image.Image(elf, {}, {})


def call_rel(at, target):
    return b'\xe8' + struct.pack('<i', target - (at + 5))


def test_own_copy(d):
    # void f(int *p): X(p) where X reads **p's byte +0x2d. Oracle: the call to X is intercepted (X is no function of
    # its unit); the native calls its unit's own `FUN_00402000` directly. *p is 0, so X faults at 0x2d.
    pre = bytes.fromhex('8b442404' '50')                      # mov eax,[esp+4]; push eax
    oracle = pre + call_rel(F + len(pre), X) + bytes.fromhex('83c404' 'c3')
    x_code = bytes.fromhex('8b442404' '8b00' '8a402d' 'c3')     # mov eax,[esp+4]; mov eax,[eax]; mov al,[eax+0x2d]; ret
    native = ('\tstp x29, x30, [sp, #-16]!\n\tbl _ZN1A12FUN_00402000EPi\n\tldp x29, x30, [sp], #16\n\tret\n'
              '\t.globl _ZN1A12FUN_00402000EPi\n\t.type _ZN1A12FUN_00402000EPi, %function\n'
              '_ZN1A12FUN_00402000EPi:\n\tldr w1, [x0]\n\tldrsb w0, [x1, #0x2d]\n\tret\n')
    t = runner.Target(conv.Conv('?f@@YAXPAH@Z', 'test'), F, {F: oracle}, image(d, native), {})
    t.oracle_code = lambda a: (x_code, {}) if a == X else None
    p = runner.Pair(t, {})
    case = world.Case(seed=1, mem={world.BUF: bytes(4)})
    rx, ra, div = p.run(case)
    check((ra.error or '').startswith('fault:r:0000002d'), 'the native faults inside its own FUN_00402000 (%s)'
          % ra.error)
    check(div is None and rx.error == ra.error,
          'the oracle runs X for real and faults the same way: no divergence (%s / %s, %s)' % (rx.error, ra.error, div))


def test_vtable_tail():
    base, n = 0x0bc00150, 328
    data = bytes(range(1, 256)) * 2
    data = data[:n]
    for seed in (1, 2):
        c = world.Case(seed=seed, bg='rand')
        w = world.World(c, {}, {})
        if seed == 1:
            eng = arm64emu.Arm64Engine(w, _empty_image(), {}, {}, {base: data})
        else:
            eng.reset(w)
        tail = bytes(eng.uc.mem_read(base + n, 8))
        want = w.page_a64(world.page_of(base))[(base + n) - world.page_of(base):][:8]
        check(tail != bytes(8) and tail == want,
              'seed %d: the word past the group end is the world background %s (want %s)' % (seed, tail.hex(),
                                                                                         want.hex()))
        check(bytes(eng.uc.mem_read(base, n)) == data, 'seed %d: the group itself is intact' % seed)
        check(bytes(eng.uc.mem_read(world.page_of(base) + 0x1000, 4)) == w.page_a64(world.page_of(base) + 0x1000)[:4],
              'seed %d: one page past the last group is mapped too' % seed)


_IMG = []


def _empty_image():
    if not _IMG:
        d = tempfile.mkdtemp(prefix='s13nr-img')
        _IMG.append(image(d, '\tret\n'))
    return _IMG[0]


def test_virtual_stub():
    t = runner.Target(None, F, {F: b'\xc3'}, None, {})
    followed = []
    t.oracle_code = lambda a: followed.append(a) or (b'\xc3', {})
    t.vt_slots = {0x00acbb70}
    p = runner.Pair(t, {})
    d = dict(kind='call-target', index=4, x86='00acbb70', arm='name:_ZN10NiAVObject4v003Ev', x86_trace=[], arm_trace=[],
             x86_next=None, arm_next=None, vcall='planted')

    class R:
        error = None
        calls = []
        blocks = set()
        saved_ok = True
    p._run = lambda case, steps: (R(), R(), dict(d))
    rx, ra, got = p.run(world.Case(seed=1))
    check(not followed and got and got['kind'] == 'call-target' and got.get('vcall') == 'planted',
          'a planted slot answered by a named native stub is not followed, the call-target stays (%s, %s)'
          % (followed, got and got['kind']))
    # a direct callee (not a planted slot) the native lacks is still followed as an inlined function
    d2 = dict(d, x86='00acbb71', vcall=None)
    d2.pop('vcall')
    t.x86_code[F] = b'\xc3'
    t.oracle_code = lambda a: followed.append(a) or (b'\xc3', {})
    p2 = runner.Pair(t, {})
    p2._run = lambda case, steps: (R(), R(), dict(d2))
    p2.run(world.Case(seed=1))
    check(0x00acbb71 in followed, 'a direct callee is still followed (%s)' % [hex(a) for a in followed])


def main():
    with tempfile.TemporaryDirectory() as d:
        test_own_copy(d)
    test_vtable_tail()
    test_virtual_stub()
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
