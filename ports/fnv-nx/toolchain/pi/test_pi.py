#!/usr/bin/env python3
"""test_pi.py - synthetic test of the fnvnx_pi plugin (N3 verdict, inline tree, touched types).

    python3 toolchain/pi/test_pi.py

Compiles test_pi.cpp with the plugin and checks the lines of section .fnvnx.n3. Skips without the cross compiler
and its plugin headers.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(['bash', os.path.join(HERE, 'build.sh'), os.path.join(td, 'so')], capture_output=True, text=True)
        if r.returncode != 0:
            print('skip: the plugin does not build here'); return 0
        out = os.path.join(td, 't.s')
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-fplugin=' + r.stdout.strip().splitlines()[-1],
                            '-S', '-o', out, os.path.join(HERE, 'test_pi.cpp')], capture_output=True, text=True)
        if r.returncode != 0:
            print('FAIL compiles', r.stderr[:2000]); return 1
        s = open(out).read()
    sec = s[s.index('.fnvnx.n3'):]
    rows = {}
    for l in re.findall(r'\.ascii\s+"(.*)\\n"', sec[:sec.index('.popsection')]):
        f = l.split('\\t')
        rows[(f[0], f[3])] = f
    want = {
        ('_Z3topP1R', ''): ['ok', '_ZL3midP1R,_ZL4leafP1R', '1R'],     # the inline tree, transitively
        ('_ZL3midP1R', 'inline'): ['ok', '', '1R'],                    # a body seen but not emitted on its own
        ('_ZL4leafP1R', 'inline'): ['ok', '', '1R'],
        ('_Z2dvii', ''): ['div', '', ''],
        ('_Z2dci', ''): ['ok', '', ''],                                # division by a constant is inside
        ('_Z2shjj', ''): ['shift', '', ''],
        ('_Z2vaiz', ''): ['varargs', '', ''],
        ('_Z3volPVi', ''): ['volatile', '', ''],
        ('_Z2uni', ''): ['uninit', '', ''],                            # read early: the optimizer deletes the use
        ('_Z2asi', ''): ['asm', '', ''],
        ('_ZL2cpii', 'constprop.0'): ['ok', '', ''],                   # a clone, under the function's own name
        ('_Z5usecpi', ''): ['ok', '', ''],
        ('_Z2m1v', ''): ['ok', '', '1Q'],
        ('_Z2m2v', ''): ['uninit-mem', '', '1Q'],
        ('_Z2m3v', ''): ['uninit-mem', '', '1Q'],
        ('_Z2m4i', ''): ['uninit-mem', '', '1Q'],
        ('_Z2m5v', ''): ['ok', '', '1Q'],
        ('_ZN1KC2Ev', ''): ['ok', '', '1K'],
        ('_ZN1KD2Ev', ''): ['ok', '', '1K'],
        ('_ZN1KC1Ev', 'alias'): ['ok', '', '1K'],                      # the same body under its other symbol
        ('_ZN1KD1Ev', 'alias'): ['ok', '', '1K'],
    }
    fails = 0
    for k, w in want.items():
        got = rows.get(k)
        if not got or [got[1], got[2], got[4]] != w:
            fails += 1
            print('FAIL', k, 'want', w, 'got', got)
    if set(rows) != set(want):
        fails += 1
        print('FAIL rows', sorted(set(rows) ^ set(want)))
    print('test_pi: %s' % ('FAILED %d' % fails if fails else 'ok'))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
