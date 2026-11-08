#!/usr/bin/env python3
"""tests/s13/test_flowfloat.py - float libm wrappers of the exe run untraced on the oracle (EQ, flow rows).

The exe's ceilf / floorf wrappers (0x004e4430, 0x00406cc0: `fld dword [ebp + 8]` ... a float function) are float
functions GCC turns into one instruction (frintp / frintm). The oracle traced the call, the native has none: a
native-inlined call-target divergence (11 farm rows: 0x00724800, 0x00724dd0, 0x007253a0, 0x007a6920, ...).
  F1  engine.crt_builtins gives a provider row named ceilf / floorf the builtin (it skipped every `f` name)
  F2  oracle `call 0x004e4430` (ceilf wrapper) vs native frintp: no divergence, no traced call
  F3  oracle `call 0x00406cc0` (was mapped as the double floor: read 8 bytes at [esp + 4]) vs native frintm: equal
Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
sys.path.insert(0, HERE)                                 # test_d14 (its helpers), also under python -I
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13flowfloat'))

import conv       # noqa: E402
import engine     # noqa: E402
import libcalls   # noqa: E402
import world      # noqa: E402
import test_d14   # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def flt(x):
    return struct.pack('<f', x)


def main():
    # F1: the provider rows of the link name the exe addresses ceilf / floorf / cosf
    fake = engine.UnitCache.__new__(engine.UnitCache)
    fake._prov_rows = [('ceilf', 0x004e4430), ('floorf', 0x00406cc0), ('cosf', 0x004e4470), ('sprintf', 0x00ec623a)]
    fake.providers = lambda: {}
    saved = dict(libcalls.X86)
    libcalls.X86.pop(0x004e4430, None)
    libcalls.X86.pop(0x004e4470, None)
    try:
        engine.UnitCache.crt_builtins(fake)
        check(libcalls.X86.get(0x004e4430) == 'ceilf' and libcalls.X86.get(0x004e4470) == 'cosf' and
              0x00ec623a not in libcalls.X86, 'F1 provider ceilf / cosf are builtins, sprintf is not')
        check(libcalls.X86.get(0x00406cc0) == 'floorf', 'F1 0x00406cc0 is the float floor')
        cv = conv.Conv('?f@@YAMM@Z', 'test')                    # float __cdecl f(float)
        # oracle: fld dword [esp+4]; push ecx; fstp dword [esp]; call W; add esp,4; ret
        for wrapper, insn in ((0x004e4430, 'frintp'), (0x00406cc0, 'frintm')):
            head = bytes.fromhex('d9442404' '51' 'd91c24')
            ora = head + test_d14.call_rel(0x00401000 + len(head), wrapper) + bytes.fromhex('83c404' 'c3')
            arm = '\t%s s0, s0\n\tret\n' % insn
            for x in (1.25, -0.5, 3.0, 2.0e9, -7.75):
                rx, ra, d = test_d14.run(cv, ora, arm, world.Case(seed=1, args={'p0': flt(x)}))
                check(d is None and not rx.calls and not ra.calls,
                      'F2/F3 %08x(%r): builtin on the oracle, equal to %s (%s)' % (
                          wrapper, x, insn, d and (d['kind'], d.get('x86'), d.get('arm'))))
    finally:
        libcalls.X86.clear()
        libcalls.X86.update(saved)
    print('test_flowfloat: %d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
