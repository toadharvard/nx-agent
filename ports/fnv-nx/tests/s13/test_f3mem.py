#!/usr/bin/env python3
"""tests/s13/test_f3mem.py - F3 (d21-f3mem): four harness fixes of the memory class, on synthetic functions (no decomp
content).

1. a CRT builtin faults where the machine's own access would: the oracle's memcpy(dst, NULL, 8) and the native's
   inlined 8-byte load from NULL both fault (was native-null-read: the builtin mapped the null page);
2. a canned call result in either private range is moved out of it, the same on both machines;
3. a stored word in the code-stub range that is no stub the image binds stays as it is (was store-width: the
   native's copy of a canned 0x0805ddf1 was rewritten to 0x0005ddf1);
4. a clean oracle run whose only difference is a lost callee-saved register learns the pop of its unknown
   callee-pops call (was callee-saved x86=False: `pop esi` read the argument word the call left).
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13f3'))

import a64image   # noqa: E402
import conv       # noqa: E402
import fnkeys     # noqa: E402
import runner     # noqa: E402
import world      # noqa: E402

FAIL = []
F = 0x00401000
MEM = 0x03000000


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def image(d, native, name):
    s = os.path.join(d, name + '.s')
    open(s, 'w').write('\t.text\n\t.globl __fnv_%08x\n\t.type __fnv_%08x, %%function\n__fnv_%08x:\n' % (F, F, F)
                       + native)
    o, elf, ld = os.path.join(d, name + '.o'), os.path.join(d, name + '.elf'), os.path.join(d, 'layout.ld')
    open(ld, 'w').write(a64image.LAYOUT % a64image.A64_BASE)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    subprocess.run(['aarch64-linux-gnu-ld', '-m', 'aarch64linux32', '-static', '-e', '0', '-T', ld, '-o', elf, o],
                   check=True, capture_output=True)
    return a64image.Image(elf, {}, {})


def pair(d, name, mangled, oracle, native):
    return runner.Pair(runner.Target(conv.Conv(mangled, 'test'), F, {F: oracle}, image(d, native, name), {}), {})


def call_rel(at, target):
    return b'\xe8' + struct.pack('<i', target - (at + 5))


def main():
    with tempfile.TemporaryDirectory() as d:
        # 1. void f(void): memcpy((void*)0x03000000, NULL, 8) / native: the inlined load from NULL
        pre = b'\x6a\x08' + b'\x6a\x00' + b'\x68' + struct.pack('<I', MEM)
        oracle = pre + call_rel(F + len(pre), 0x00401460) + b'\x83\xc4\x0c' + b'\xc3'
        p = pair(d, 'nullcpy', '?f@@YAXXZ', oracle,
                 '\tmov w1, #0\n\tldr x2, [x1]\n\tmov w0, #0x3000000\n\tstr x2, [x0]\n\tret\n')
        rx, ra, div = p.run(world.Case(seed=1))
        check(div is None and (rx.error or '').startswith('fault:r:0000') and (ra.error or '').startswith('fault:'),
              'memcpy from NULL faults on the oracle as the native load does (%s / %s, %s)' % (rx.error, ra.error, div))

        # 2. canned results out of the private ranges
        c = world.Case(seed=1, rets=[struct.pack('<II', 0x0a08e901, 0x0e001234) + bytes(8),
                                     struct.pack('<II', 0x03001000, 0x0c000000) + bytes(8)])
        w0, w1 = struct.unpack_from('<II', c.ret_bytes(0))
        v0, v1 = struct.unpack_from('<II', c.ret_bytes(1))
        check((w0, w1, v0, v1) == (0x4a08e901, 0x4e001234, 0x03001000, 0x0c000000),
              'canned words in A64_PRIV / X86_PRIV move out (bit 30), others stay (%08x %08x %08x %08x)'
              % (w0, w1, v0, v1))

        # 3. void f(int *p): *p = 0x0805ddf1 (a data word in the code-stub range, no stub the image binds)
        oracle = b'\x8b\x44\x24\x04' + b'\xc7\x00' + struct.pack('<I', 0x0805ddf1) + b'\xc3'
        p = pair(d, 'stubword', '?f@@YAXPAH@Z', oracle,
                 '\tmov w1, #0xddf1\n\tmovk w1, #0x0805, lsl #16\n\tstr w1, [x0]\n\tret\n')
        rx, ra, div = p.run(world.Case(seed=1))
        check(div is None, 'a stored 0x0805ddf1 compares as itself on both machines (%s)' % div)

        # 4. void f(int fn): push ebp; mov ebp, esp; push esi; mov eax, [ebp+8]; push 0; call eax; pop esi;
        #    mov esp, ebp; pop ebp; ret - the callee (wild, unknown) pops its argument (stdcall)
        oracle = bytes.fromhex('55' '8bec' '56' '8b4508' '6a00' 'ffd0' '5e' '8be5' '5d' 'c3')
        p = pair(d, 'calleepop', '?f@@YAXH@Z', oracle,
                 '\tstp x29, x30, [sp, #-16]!\n\tmov x2, x0\n\tmov w0, #0\n\tblr x2\n\tldp x29, x30, [sp], #16\n'
                 '\tret\n')
        rx, ra, div = p.run(world.Case(seed=1, args={'p0': struct.pack('<I', 0x00500000)}))
        check(div is None and rx.saved_ok and len(rx.calls) == 1 and len(ra.calls) == 1,
              'the unknown callee-pops call learns its pop: no callee-saved divergence (%s, calls %d/%d)'
              % (div, len(rx.calls), len(ra.calls)))

    check(fnkeys._f3_mem(dict(divergence=dict(kind='callee-saved', x86=False, arm=True))) and
          not fnkeys._f3_mem(dict(divergence=dict(kind='call-target', x86='00401000', arm='00402000'),
                                  case=dict(seed=1, rets=['00000000' * 4]))),
          'd21-f3mem rechecks callee-saved records, not an unrelated call-target one')
    print('%d failures' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
