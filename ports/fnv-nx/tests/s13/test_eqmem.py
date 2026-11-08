#!/usr/bin/env python3
"""tests/s13/test_eqmem.py - EQ memory group (farm baseline 588863, difffuzz-20), on hand-written code (no decomp).

  thunk    (also test_msvcentry) a unit's own `__fnvnx_msvc_<addr>` alias is defined at its thunk
           whenever the thunk is defined, so the oracle's `this` (a secondary base) enters the thunk (0x0093ac20)
  static   a function-local static of a class type (`...@4VBSWin32FileCache@@A`) and one whose function is spelled with
           other back-references inside the static's name (`SAAAV2@XZ` against `SAAAV1@XZ`) pair with the native `_ZZ`
           object, and the native's own guard is no split-global read (0x00aff100, GetRandomGenerator)
  staticptr the address of a paired static: the oracle's image address on one side, the native object on the other
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
os.environ.setdefault('FNVNX_DFWORK', tempfile.mkdtemp(prefix='s13eqmem'))

import a64image   # noqa: E402
import config     # noqa: E402
import conv       # noqa: E402
import engine     # noqa: E402
import runner     # noqa: E402
from test_d14 import build_image  # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def test_thunk():
    src = 'src/x/y.cpp'
    with tempfile.TemporaryDirectory() as d:
        link = os.path.join(d, 'link')
        os.makedirs(os.path.join(link, 'fixed'))
        os.makedirs(os.path.join(link, 'out'))
        s = os.path.join(d, 'u.s')
        open(s, 'w').write('\t.text\n\t.globl __fnv_00401000\n__fnv_00401000:\n\tret\n'
                           '\t.globl _ZThn24_N1C1fEv\n_ZThn24_N1C1fEv:\n\tsub x0, x0, #24\n\tb __fnv_00401000\n')
        subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o',
                        os.path.join(link, 'fixed', config.obj_name(src))], check=True)
        open(os.path.join(link, 'out', 'aliases.ld'), 'w').write('__fnvnx_msvc_00401000 = _ZThn24_N1C1fEv;\n')
        img = a64image.build_many([src], link, os.path.join(d, 'work'), 'u')
        check('__fnvnx_msvc_00401000' in img.syms and
              img.syms['__fnvnx_msvc_00401000'] == img.syms.get('_ZThn24_N1C1fEv'),
              'the unit-own __fnvnx_msvc_<addr> alias is defined at its thunk')


def fake_unit_cache(units):
    uc = engine.UnitCache.__new__(engine.UnitCache)
    uc.units = units
    uc._static_names = None
    uc._static_pairs_key = None
    return uc


def test_static():
    body = ('\t.globl _ZN1C1fEv\n_ZN1C1fEv:\n\tret\n\t.bss\n\t.balign 8\n'
            '\t.globl _ZZN1C1fEvE1s\n\t.type _ZZN1C1fEvE1s, %object\n\t.size _ZZN1C1fEvE1s, 8\n_ZZN1C1fEvE1s:\n\t.space 8\n'
            '\t.globl _ZGVZN1C1fEvE1s\n\t.type _ZGVZN1C1fEvE1s, %object\n\t.size _ZGVZN1C1fEvE1s, 8\n'
            '_ZGVZN1C1fEvE1s:\n\t.space 8\n')
    # the function's own symbol says V1, the static's name says V2 (a back-reference counted in the static's scope)
    unit = dict(functions=[dict(symbol='?f@C@@SAAAV1@XZ', address='0x00401000')],
                symbols={'?f@C@@SAAAV1@XZ': '0x00401000',
                         '?s@?1??f@C@@SAAAV2@XZ@4VD@@A': '0x01100000',
                         '?$S1@?1??f@C@@SAAAV2@XZ@4IA': '0x01100010'})
    check(engine.LOCAL_VALUE.match('?s@?1??f@C@@SAAAV2@XZ@4VD@@A') is not None,
          'a static of a class type (`@4VD@@A`) is a local value')
    with tempfile.TemporaryDirectory() as d:
        img = build_image(body, d)
        pairs, guards = fake_unit_cache([unit]).static_pairs(img)
        check(pairs == [(img.syms['_ZZN1C1fEvE1s'], 0x01100000, 8)], 'the class-typed static pairs (%s)' % pairs)
        check(guards == set(range(0x01100010, 0x01100014)), 'the oracle guard word is known')
        uc = fake_unit_cache([unit])
        uc.static_pairs(img)
        check(uc._native_guards == [(img.syms['_ZGVZN1C1fEvE1s'], 8)], 'the native guard of the static is known')


def test_staticptr():
    cvc = conv.Conv('?f@Foo@@QAEXPAUBar@@@Z', 'test')

    class C:
        def __init__(self, args):
            self.args, self.regs, self.pushed = args, {}, None
    oa, na = 0x011f81e8, 0x0a1b1258
    pairs = [(na, oa, 100)]

    def cmp(px, pa):
        cx = C([0x03000000, 0, px])
        ca = type('A', (), dict(args=[0x03000000, pa] + [0] * 6, fargs=[0] * 8, x8=0, stack=b'', target=0))()
        return runner.compare_args(cvc, cx, ca, agg_ptrs=runner.with_static_pairs(None, pairs))
    check(cmp(oa, na) is None, 'the static\'s address on each side: the same object')
    check(cmp(oa + 8, na + 8) is None, 'the same offset in it: the same')
    check(cmp(oa + 8, na) is not None, 'another offset natively: a difference')
    check(cmp(oa, na + 0x1000) is not None, 'another object natively: a difference')


def main():
    test_thunk()
    test_static()
    test_staticptr()
    print('FAILED: %d' % len(FAIL) if FAIL else 'all ok')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
