#!/usr/bin/env python3
"""Unit test of fnvlink.flags0_slot (s2d-008/009): a placeholder `virtual void v00();` slot filled with the exe's
deleting destructor whose native definition reads the flags gets the flags-0 stub; slots that pass their own
arguments, destructor variants and definitions without a flags parameter keep their binding. Hand-made names,
demangled with the parsers resolve uses; no exe needed.

    python3 tools/link/test_flags0.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import elfsyms   # noqa: E402
import fnvlink   # noqa: E402
import sigs      # noqa: E402

FAILS = []


def check(cond, what):
    print('%s %s' % ('ok  ' if cond else 'FAIL', what))
    if not cond:
        FAILS.append(what)


def dem(tool, name):
    r = subprocess.run([tool], input=name + '\n', capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else None


def it(n):
    return sigs.parse_itanium(n, dem('c++filt', n))


def ms(n):
    return sigs.parse_msvc(n, dem('llvm-undname', n) if n.startswith('?') else None)


def nd(sym, msvc):
    return dict(unit='u.cpp', sym=elfsyms.Sym(sym, 0, 0, 'func', 'global', 1, '.text', False), variants={},
                tier='def', msvc=msvc)


def main():
    if not (subprocess.run(['which', 'llvm-undname'], capture_output=True).returncode == 0):
        print('skip: no llvm-undname')
        return 0
    g = nd('_ZN1A12FUN_00401000Ej', '??_GA@@UAEPAXI@Z')
    check(fnvlink.flags0_slot(it('_ZN1A3v00Ev'), g, ms(g['msvc']), None, 0x00401000),
          'a ??_G (this, flags) in a void v00() slot: flags-0 stub')
    adj = nd('_ZN1B12FUN_00401100Ej', '??_GB@@W3AEPAXI@Z')
    check(fnvlink.flags0_slot(it('_ZN1B5DummyEv'), adj, ms(adj['msvc']), None, 0x00401100),
          'an adjustor ??_G (W3) in a placeholder slot: flags-0 stub')
    check(not fnvlink.flags0_slot(it('_ZN1A3setEj'), g, ms(g['msvc']), None, 0x00401000),
          'a slot that passes an argument keeps its binding')
    check(not fnvlink.flags0_slot(it('_ZN1AD1Ev'), g, ms(g['msvc']), None, 0x00401000),
          'a destructor variant slot is the flags stub rule\'s (D1/D0), not this one')
    plain = nd('_ZN1C12FUN_00401200Ev', '?FUN_00401200@C@@QAEXXZ')
    check(not fnvlink.flags0_slot(it('_ZN1C3v00Ev'), plain, ms(plain['msvc']), None, 0x00401200),
          'a definition without a flags parameter reads no w1: kept')
    other = nd('_ZN1D12FUN_00401300Ej', '?FUN_00401300@D@@QAEXI@Z')
    check(not fnvlink.flags0_slot(it('_ZN1D3v00Ev'), other, ms(other['msvc']), None, 0x00401300),
          'an int-taking member without a ??_G name or exe code shape: kept')
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
