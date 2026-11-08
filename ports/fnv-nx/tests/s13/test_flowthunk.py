#!/usr/bin/env python3
"""tests/s13/test_flowthunk.py - the native entry of a secondary-base override comes from the link's aliases.ld (EQ, flow).

MSVC enters an override of a secondary base's virtual with `this` at that base; the native entry is then the link's
`__fnvnx_msvc_<addr>` (an alias of GCC's `_ZThn<d>_` thunk), not `__fnv_<addr>`. A unit's harness image only defines
the aliases its own code references, so arm64emu.entry fell back to the primary body: 11 farm rows (0x0093ac20:
`sub ecx, 0xa4` in the exe, the native got the full object; 0x00402f90, 0x0040e600, 0x00c711d0, ...) read as
thunk-binding / native-extra-calls / native-null-read.
  T1  the oracle takes this at +8 and returns this - 8; the native thunk subtracts 8 and calls the primary body, which
      returns its this: equal when the entry is the alias named in aliases.ld
  T2  without the alias in aliases.ld the primary entry runs and the returns differ (the entry rule is unchanged)
Needs unicorn, capstone, aarch64-linux-gnu-as/ld.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = tempfile.mkdtemp(prefix='s13flowthunk-link')
os.makedirs(os.path.join(LINK, 'out'))
os.environ['FNVNX_DFLINK'] = LINK
os.environ['FNVNX_DFWORK'] = tempfile.mkdtemp(prefix='s13flowthunk')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
sys.path.insert(0, HERE)

import conv       # noqa: E402
import world      # noqa: E402
import test_d14   # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


BODY = ('\tret\n'                                  # the primary body (`this` in x0 is returned: w0 = w0)
        '\t.globl _ZThn8_N1D1fEv\n\t.type _ZThn8_N1D1fEv, %function\n'
        '_ZThn8_N1D1fEv:\n\tsub w0, w0, #8\n\tb __fnv_00401000\n')


def run(this):
    cv = conv.Conv('?f@D@@QAEPAXXZ', 'test')          # void *D::f()
    ora = bytes.fromhex('8d41f8' 'c3')                # lea eax, [ecx - 8]; ret  (MSVC: this is the base subobject)
    return test_d14.run(cv, ora, BODY, world.Case(seed=7, args={'this': this}))


def main():
    this = (0x03000108).to_bytes(4, 'little')
    open(os.path.join(LINK, 'out', 'aliases.ld'), 'w').write('__fnvnx_msvc_00401000 = _ZThn8_N1D1fEv;\n')
    import arm64emu
    getattr(arm64emu, "_MSVC_ALIASES", {}).clear()
    rx, ra, d = run(this)
    check(d is None, 'T1 the alias names the thunk: equal (%s)' % (str(d and (d['kind'], d.get('x86'), d.get('arm'))),))
    open(os.path.join(LINK, 'out', 'aliases.ld'), 'w').write('\n')
    getattr(arm64emu, "_MSVC_ALIASES", {}).clear()
    rx, ra, d = run(this)
    check(d is not None and d['kind'] == 'return', 'T2 no alias: the primary body runs, the returns differ')
    print('test_flowthunk: %d failed' % len(FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
