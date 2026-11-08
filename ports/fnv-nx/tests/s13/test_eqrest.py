#!/usr/bin/env python3
"""tests/s13/test_eqrest.py - EQ's farm-baseline residue (difffuzz-20, hand-built records, no decomp content).

  static-this  a pointer argument that is the address of a function-local static: the oracle passes the static's image
               address, the native its own .bss copy. local-static, not call-args / split-global (0x00aff100)
  jumptab      classify.entry_register_reads follows the cases of a switch jump table inside the function: a register
               a case body reads before writing it (edx pushed behind `jmp [eax*4 + table]`) is a custom register
               (0x00e03170)
  guard-skip   a call-count where the oracle's static guard is set (no calls) and the native's own guard runs the
               initializer: local-static (0x00aff100)
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='eqrest'))

import classify   # noqa: E402
import conv       # noqa: E402
import world      # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


class T:
    local_statics = set(range(0x011f81e8, 0x011f81e8 + 16)) | {0x011f824c}
    conv = conv.Conv('?f@@YAXXZ', 'test')
    source = None


def cause(d, fn):
    try:
        return classify.hint(None, T(), world.Case(seed=1, bg='rand'), d, fn, 0).get('cause')
    except AttributeError:      # falls through to the probes, which need a pair: not local-static
        return 'probe'


def test_jumptab():
    code = bytes.fromhex('48'                   # dec eax          (the case index, in eax)
                         'ff248510104000'       # jmp dword ptr [eax*4 + 0x401010]
                         'c3' + '90' * 7 +      # pad to 0x401010
                         '18104000' '18104000'  # the table: two cases, both at 0x401018
                         '52' 'c3')             # push edx; ret
    cv = conv.Conv('?f@@YAXH@Z', 'test')
    got = classify.entry_register_reads(dict(code=code, address=0x00401000), cv)
    check('edx' in got, 'a register read in a jump-table case is a custom register: %s' % got)


def test_guard():
    fn = dict(code=bytes.fromhex('a14c821f01' 'c3'), address=0x00aff100)      # mov eax, [0x011f824c]; ret
    d = dict(kind='call-count', x86=0, arm=2, arm_local_static=True, arm_private_reads=1,
             arm_private_syms=['_ZGVZN3Foo3barEvE1s'])
    check(cause(d, fn) == 'local-static', 'guard skipped by the oracle, run by the native: local-static (%s)' % cause(d, fn))
    check(cause(dict(d, arm_private_syms=['_ZZN3Foo3barEvE1s']), fn) != 'local-static', 'no guard among the reads: stays')
    check(cause(dict(d, x86=1), fn) != 'local-static', 'the oracle called too: stays')
    fn2 = dict(code=b'\xc3', address=0x00aff100)
    check(cause(d, fn2) != 'local-static', 'the exe code touches no local static: stays')


def main():
    test_jumptab()
    test_guard()
    fn = dict(code=b'\xc3', address=0x00401000)
    d = dict(kind='call-args', arg='this', cls='P', x86='e8811f01', arm='58121b0a', index=0, arm_local_static=True)
    h = cause(d, fn)
    check(h == 'local-static', 'this = a local static: local-static (%s)' % h)
    d2 = dict(d, x86='00901f01')
    h = cause(d2, fn)
    check(h != 'local-static', 'a pointer that is no local static stays (%s)' % h)
    d3 = dict(d, arm_local_static=False)
    h = cause(d3, fn)
    check(h != 'local-static', 'no private-static evidence on the native: stays (%s)' % h)
    print('FAIL' if FAIL else 'PASS')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
