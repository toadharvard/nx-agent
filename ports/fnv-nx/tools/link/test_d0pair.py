#!/usr/bin/env python3
"""S5 round 5 (the lost exe slot 1): a vtable whose D1 and D0 resolve renames to one native body (word 0 == word 1)
still has its destructor pair found (on the object's own symbols), and a D0 bound to a destroy-only body gets the
D0 stub (that body, then operator delete). Hand-made symbols; with aarch64-linux-gnu-as the stub is assembled.

    python3 tools/link/test_d0pair.py
"""
import collections
import inspect
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elfsyms  # noqa: E402
import exevft   # noqa: E402
import fnvlink  # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


raw = ['_ZN17SettingPointerMapD1Ev', '_ZN17SettingPointerMapD0Ev', '_ZN17SettingPointerMap7HashKeyEj']
renamed = ['__fnv_00404da0', '__fnv_00404da0', '_ZN17SettingPointerMap7HashKeyEj']
check(exevft.dtor_pos(raw, [0, 0, 0]) == 0, 'the pair is found on the raw symbols')
check(exevft.dtor_pos(renamed, [0, 0, 0]) is None, 'renamed to one body it is not (the 91-table shape)')
src = inspect.getsource(fnvlink.exe_vtable_copies)
check('dtor_pos([e.sym for e in ents[2:2 + n]]' in src, 'exe_vtable_copies looks for the pair on the raw symbols')


def nd(sym, msvc=None):
    return dict(unit='u.cpp', sym=elfsyms.Sym(sym, 0, 0, 'func', 'global', 1, '.text', False), variants={},
                tier='def', msvc=msvc)


check(not fnvlink.deleting_native(nd('_ZN18FUN_00404da0_class12FUN_00404da0Ev', '?FUN_00404da0@FUN_00404da0_class@@QAEXXZ'),
                                  lambda n: None), 'a plain destroy-only body is not deleting')
check(fnvlink.deleting_native(nd('_ZN1AD0Ev'), lambda n: None), 'an Itanium D0 is deleting')
check(fnvlink.deleting_native(nd('_ZN1A1fEj', '??_GA@@UAEPAXI@Z'), lambda n: None), 'a ??_G is deleting')
votes = collections.defaultdict(collections.Counter)
votes['??3@YAXPAX@Z'][0x00401030] = 3
check(fnvlink.global_delete(votes, {0x00401030: {}}, {}) == '__fnv_00401030', 'the voted global operator delete')
stub = fnvlink.emit_d0_stub('__fnvnx_d0del_00404da0', '__fnv_00404da0', '__fnv_00401030')
check('bl __fnv_00404da0' in stub and stub.rstrip().splitlines()[-3].strip() == 'b __fnv_00401030',
      'D0 stub: the body, then a tail call of operator delete')
asm = shutil.which('aarch64-linux-gnu-as')
if asm:
    d = tempfile.mkdtemp()
    open(os.path.join(d, 'a.S'), 'w').write('    .text\n' + stub)
    r = subprocess.run([asm, '-mabi=ilp32', os.path.join(d, 'a.S'), '-o', os.path.join(d, 'a.o')],
                       capture_output=True, text=True)
    check(r.returncode == 0, 'the D0 stub assembles for ILP32 %s' % r.stderr[:120])
    shutil.rmtree(d)
print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
