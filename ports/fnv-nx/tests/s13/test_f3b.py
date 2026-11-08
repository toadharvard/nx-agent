#!/usr/bin/env python3
"""tests/s13/test_f3b.py - F3b (difffuzz-20): the AArch64 stub range is background memory outside the slots of the
stubs the image binds, as the same addresses are on the oracle; synthetic functions (no decomp content).

Was native-stub-data: a pointer chase through background words landed on a stub page (0x0860d648, no stub there; or
exactly on a bound code stub, 0x08815300 = __fnv_00815300); the native load faulted, the oracle read background. Only
a data symbol the link leaves unbound becomes a named stub, so now:
1. a load from the code-stub range, away from any bound stub or right on one, reads the oracle's background word;
2. so does a load on a page a call already mapped (the page held `brk #0` everywhere: 0xd4200000 natively);
3. a call of a noreturn callee through its code stub on that background page still ends the run cleanly;
4. a load from the synthesized vtable region outside its groups reads background;
5. a load from a bound named stub still faults, on a fresh page or one mapped earlier (the native reads an unbound
   data symbol: native-stub-data stays a real bug).
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13f3b'))

import a64image   # noqa: E402
import conv       # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402

FAIL = []
F = 0x00401000
G = 0x00402000              # a callee the image binds as the code stub CODE_STUB + G
THROW = 0x00ec7894          # libcalls.NORETURN_X86: _CxxThrowException, also bound as a code stub
MEM = 0x03000000
NAMED = a64image.NAMED_STUB
BRK = struct.pack('<I', 0xd4200000)


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def image(d, native, name, stubs):
    s = os.path.join(d, name + '.s')
    open(s, 'w').write(''.join('\t.globl __fnv_%08x\n\t.set __fnv_%08x, 0x%08x\n' % (x, x, a64image.CODE_STUB + x)
                               for x in (G, THROW))
                       + '\t.text\n\t.globl __fnv_%08x\n\t.type __fnv_%08x, %%function\n__fnv_%08x:\n' % (F, F, F)
                       + native)
    o, elf, ld = os.path.join(d, name + '.o'), os.path.join(d, name + '.elf'), os.path.join(d, 'layout.ld')
    open(ld, 'w').write(a64image.LAYOUT % a64image.A64_BASE)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-static', '-e', '0', '-T', ld, '-o', elf, o],
                   check=True, capture_output=True)
    return a64image.Image(elf, dict(stubs), {})


def mov32(reg, v):
    return '\tmov %s, #0x%x\n\tmovk %s, #0x%x, lsl #16\n' % (reg, v & 0xffff, reg, v >> 16)


def copy_word(d, name, addr, call=False, stubs=None, pre=None, callee=G):
    """void f(void): [optionally call callee;] [a dead load of *(int *)pre;] *(int *)MEM = *(int *)addr."""
    ox = b'\xa1' + struct.pack('<I', addr) + b'\xa3' + struct.pack('<I', MEM) + b'\xc3'
    nat = mov32('w1', addr) + '\tldr w2, [x1]\n' + mov32('w0', MEM) + '\tstr w2, [x0]\n'
    if pre is not None:
        ox = b'\x8b\x0d' + struct.pack('<I', pre) + ox
        nat = mov32('w3', pre) + '\tldr w4, [x3]\n' + nat
    if call:
        ox = b'\xe8' + struct.pack('<i', callee - (F + 5)) + ox
        nat = ('\tstp x29, x30, [sp, #-16]!\n' + mov32('w16', a64image.CODE_STUB + callee) + '\tblr x16\n' + nat + '\tldp x29, x30, [sp], #16\n')
    p = runner.Pair(runner.Target(conv.Conv('?f@@YAXXZ', 'test'), F, {F: ox}, image(d, nat + '\tret\n', name,
                                                                                    stubs or {}), {}), {})
    return p.run(world.Case(seed=7, bg='rand'))


def main():
    with tempfile.TemporaryDirectory() as d:
        word = lambda r: bytes(r.writes.get(MEM + i, 0) for i in range(4))   # noqa: E731  the copied word
        a = 0x0860d648
        rx, ra, div = copy_word(d, 'away', a)
        check(div is None and ra.error is None and word(ra) == word(rx),
              'a load from the code-stub range away from a bound stub reads background (%s, %s)' % (ra.error, div))

        a = a64image.CODE_STUB + G + 0x800
        rx, ra, div = copy_word(d, 'samepage', a, call=True)
        check(div is None and ra.error is None and len(ra.calls) == 1 and ra.calls[0].target == G
              and word(ra) == word(rx) != BRK,
              'after a call to __fnv_%08x its page reads background outside the slot (%s, %s)' % (G, ra.error, div))

        rx, ra, div = copy_word(d, 'throw', a64image.CODE_STUB + G + 0x800, call=True, callee=THROW)
        check(div is None and ra.error is None and ra.noreturn == '_CxxThrowException' and rx.noreturn,
              'a noreturn call through its code stub ends the run on both machines (%s, %s, %s)'
              % (ra.error, ra.noreturn, div))

        a = 0x0bd9b87c
        rx, ra, div = copy_word(d, 'vtregion', a)
        check(div is None and ra.error is None, 'a load from the vtable region outside its groups reads background '
                                                '(%s, %s)' % (ra.error, div))

        rx, ra, div = copy_word(d, 'boundcode', a64image.CODE_STUB + G)
        check(div is None and ra.error is None and word(ra) == word(rx),
              'a load right on the bound code stub __fnv_%08x reads background (%s, %s)' % (G, ra.error, div))

        rx, ra, div = copy_word(d, 'boundnamed', NAMED + 0x24, stubs={NAMED + 0x20: 'DAT_011f426c'})
        check(rx.error is None and ra.error == 'fault:r:%08x' % (NAMED + 0x24),
              'a load from a bound named stub faults natively (%s)' % ra.error)

        rx, ra, div = copy_word(d, 'boundnamed2', NAMED + 0x24, pre=NAMED + 0x800, stubs={NAMED + 0x20: 'DAT_011f426c'})
        check(rx.error is None and ra.error == 'fault:r:%08x' % (NAMED + 0x24),
              'so does one on a stub page a background load mapped earlier (%s)' % ra.error)
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
