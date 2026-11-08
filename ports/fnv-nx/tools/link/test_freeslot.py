#!/usr/bin/env python3
"""Unit test of fnvlink.free_slot (S5's class 1, s5-003): an exe vtable slot whose native definition is a decomp
global `FUN_<a>` non-member gets a bridge by its MSVC convention (cdecl/stdcall: `this` dropped; fastcall: edx
inserted as 0). Hand-made names; with aarch64-linux-gnu-as the stubs are assembled.

    python3 tools/link/test_freeslot.py
"""
import collections
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elfsyms   # noqa: E402
import fnvlink   # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def nd(sym, msvc):
    return dict(unit='u.cpp', sym=elfsyms.Sym(sym, 0, 0, 'func', 'global', 1, '.text', False), variants={},
                tier='def', msvc=msvc)


def main():
    br, st = {}, collections.Counter()
    t = fnvlink.free_slot(0x00e8b490, nd('_Z12FUN_00e8b490Piii', '?FUN_00e8b490@@YGXPAHHH@Z'), br, st)
    check(t == '__fnvnx_vfree_00e8b490', 'a stdcall global FUN_ in a slot: a bridge')
    check('mov x0, x1' in br[t] and 'mov x6, x7' in br[t] and 'b __fnv_00e8b490' in br[t], 'stdcall: this dropped')
    t2 = fnvlink.free_slot(0x00401000, nd('_Z12FUN_00401000i', '?FUN_00401000@@YAXH@Z'), br, st)
    check(t2 is not None and 'mov x0, x1' in br[t2], 'cdecl: this dropped')
    t3 = fnvlink.free_slot(0x00402000, nd('_Z12FUN_00402000Pvii', '?FUN_00402000@@YIXPAXHH@Z'), br, st)
    check(t3 is not None and 'mov x2, x1' in br[t3] and 'mov w1, #0' in br[t3] and 'mov x0' not in br[t3],
          'fastcall: this stays in x0, edx inserted as 0')
    check(br[t3].index('mov x7, x6') < br[t3].index('mov x2, x1'), 'fastcall: moves from the top down')
    check(fnvlink.free_slot(0x00403000, nd('_ZN1A12FUN_00403000Ei', '?FUN_00403000@A@@QAEXH@Z'), br, st) is None,
          'a member keeps its binding')
    check(fnvlink.free_slot(0x00404000, nd('_Z6Helperi', '?Helper@@YAXH@Z'), br, st) is None,
          'a named free function is not in the class (S5 scope: FUN_<a>)')
    # R2 (vtgate 4264bfd7): extern "C" stand-ins, the native name is the plain C name
    t4 = fnvlink.free_slot(0x00c73170, nd('FUN_00c73170', '@FUN_00c73170@12'), br, st)
    check(t4 is not None and 'mov w1, #0' in br[t4], 'extern "C" __fastcall @FUN_<a>@N: edx inserted')
    t5 = fnvlink.free_slot(0x00d2dd50, nd('FUN_00d2dd50', '_FUN_00d2dd50@16'), br, st)
    check(t5 is not None and 'mov x0, x1' in br[t5], 'extern "C" __stdcall _FUN_<a>@N: this dropped')
    check(fnvlink.free_slot(0x00d2dd60, nd('Other', '_FUN_00d2dd60@16'), br, st) is None,
          'extern "C" MSVC name with another native name: not taken')
    check(st == collections.Counter({'free-slot-drop': 3, 'free-slot-edx': 2}), 'counts')
    asm = shutil.which('aarch64-linux-gnu-as')
    if asm:
        d = tempfile.mkdtemp()
        open(os.path.join(d, 'b.S'), 'w').write('    .text\n' + ''.join(br.values()))
        r = subprocess.run([asm, '-mabi=ilp32', os.path.join(d, 'b.S'), '-o', os.path.join(d, 'b.o')],
                           capture_output=True, text=True)
        check(r.returncode == 0, 'the bridges assemble for ILP32 %s' % r.stderr[:200])
        shutil.rmtree(d)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
