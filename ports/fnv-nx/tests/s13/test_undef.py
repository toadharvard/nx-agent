#!/usr/bin/env python3
"""tests/s13/test_undef.py - the undefined / oracle-fault rows (EQ farm-baseline round, difffuzz-20), synthetic code only.

  provider   a64image.build_many: a C++ name another object of the link defines (a destructor body of the unit's
             zz_bd_<unit>.o) is linked into the unit image instead of becoming a trap stub, and the provider's other
             symbols (a `__fnv_<X>` function) stay local, so the unit's calls of them are still intercepted

Needs capstone.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))

import a64image   # noqa: E402
import causes     # noqa: E402

FAIL = []


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAIL.append(what)


def _asm(d, name, text):
    s, o = os.path.join(d, name + '.s'), os.path.join(d, name + '.o')
    open(s, 'w').write(text)
    subprocess.run(['aarch64-linux-gnu-as', '-mabi=ilp32', s, '-o', o], check=True)
    return o


def test_provider():
    with tempfile.TemporaryDirectory() as d:
        fixed = os.path.join(d, 'link', 'fixed')
        os.makedirs(fixed)
        # the unit calls a C++ name nothing in its object defines, and a function at 0x00402000
        _asm(fixed, 'src_u.cpp', '\t.text\n\t.globl __fnv_00401000\n__fnv_00401000:\n\tbl _Z3foov\n'
             '\tbl __fnv_00402000\n\tret\n')
        # S2d's synthesized bodies for the unit: the name (weak) and a function the unit calls by its address
        _asm(fixed, 'zz_bd_src_u.cpp', '\t.text\n\t.weak _Z3foov\n_Z3foov:\n\tret\n'
             '\t.globl __fnv_00402000\n__fnv_00402000:\n\tret\n')
        img = a64image.build_many(['src/u.cpp'], os.path.join(d, 'link'), os.path.join(d, 'work'), 'u')
        check('_Z3foov' not in img.stubs.values(), 'the provided name is no named stub (%s)' % sorted(img.stubs.values()))
        check(img.syms.get('_Z3foov', 0) >= a64image.A64_BASE, 'the provided name is defined in the image')
        glob = {s.name: s.value for s in a64image.elfsyms.symbols(img.elf) if s.bind != 'local'}
        check(glob.get('__fnv_00402000') == a64image.CODE_STUB + 0x402000,
              'the provider\'s __fnv_00402000 does not capture the unit\'s call: still the code stub (%s)'
              % hex(glob.get('__fnv_00402000', 0)))


def test_isoc23():
    """strtoul and glibc's C23 redirect __isoc23_strtoul are the same C function (0x005afe10)."""
    old = causes.link_symbol
    causes.link_symbol = lambda a: {0x00ec8d33: 'strtoul'}.get(a)
    try:
        check(causes._libc_variant('00ec8d33', 'name:__isoc23_strtoul'), 'strtoul vs __isoc23_strtoul')
        check(not causes._libc_variant('00ec8d33', 'name:__isoc23_strtol'), 'another function: no')
    finally:
        causes.link_symbol = old


if __name__ == '__main__':
    for n, f in sorted(globals().items()):
        if n.startswith('test_'):
            f()
    print('FAIL' if FAIL else 'PASS')
    sys.exit(1 if FAIL else 0)
