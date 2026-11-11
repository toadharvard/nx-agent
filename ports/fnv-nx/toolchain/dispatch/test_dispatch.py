#!/usr/bin/env python3
"""test_dispatch.py - synthetic test of the fnvnx_dispatch plugin (DISPATCH1).

    python3 toolchain/dispatch/test_dispatch.py

Every indirect call (function pointer, virtual call, delete-expression, tail call, calls in a loop) checks its own
site and calls fnvnx_dispatch on a miss; a direct call is untouched. Skips without the cross compiler.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def body(asm, sym):
    m = re.search(r'^%s:\n(.*?)\.cfi_endproc' % re.escape(sym), asm, re.S | re.M)
    return m.group(1) if m else ''


def main():
    cxx = shutil.which(os.environ.get('CXX') or 'aarch64-linux-gnu-g++')   # the compiler the plugins are built for
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    with tempfile.TemporaryDirectory() as td:
        sos = []
        for d in ('sdd', 'names', 'dispatch'):
            r = subprocess.run(['bash', os.path.join(HERE, '..', d, 'build.sh'), os.path.join(td, d)], capture_output=True, text=True)
            if r.returncode != 0:
                print('skip: the plugins do not build here'); return 0
            sos.append('-fplugin=' + r.stdout.strip().splitlines()[-1])
        out = os.path.join(td, 't.s')
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fPIC', '-fno-devirtualize'] + sos +
                           ['-S', '-o', out, os.path.join(HERE, 'test_dispatch.cpp')], capture_output=True, text=True)
        if r.returncode != 0:
            print('FAIL compiles', r.stderr[:2000]); return 1
        s = open(out).read()
    fails = []
    def check(name, cond, detail=''):
        if not cond: fails.append(name); print('FAIL', name, detail)
    want = {'_Z2c1PFiiEi': 1, '_Z2c2P1Ai': 1, '_Z2c3P1A': 1, '_Z2c4PFiiEi': 1, '_Z2c5PFiiES0_i': 2, '_Z2c6i': 0}
    for f, n in want.items():
        b = body(s, f)
        check('%s: %d dispatch call(s)' % (f, n), len(re.findall(r'\bbl\s+fnvnx_dispatch\b', b)) == n, b)
        check('%s: no indirect call without a site check' % f,
              len(re.findall(r'\b(?:blr|br)\s+x\d+', b)) == n and len(re.findall(r'\bcmp\s+w\d+, w\d+', b)) >= n, b)
    hit = re.sub(r'\s+', ' ', body(s, '_Z2c1PFiiEi'))
    check('hit path: ONE 64-bit load of the site, compare the low half, branch, shift',
          re.search(r'ldr (x\d+), \[x\d+, #:lo12:\.LANCHOR0\] cmp w0, w\d+ bne \.L\d+ lsr x\d+, \1, 32', hit)
          and len(re.findall(r'\bldr\b[^\n]*LANCHOR0', body(s, '_Z2c1PFiiEi'))) == 1, hit)
    check('a direct call is untouched', '_Z6directi' in body(s, '_Z2c6i') and 'fnvnx_dispatch' not in body(s, '_Z2c6i'))
    sites = re.findall(r'^fnvnx_site\.\d+:\n\t\.zero\t8$', s, re.M)
    calls = len(re.findall(r'\bbl\s+fnvnx_dispatch\b', s))
    check('one 8-byte zero site per indirect call', len(sites) == calls == 11, '%d sites, %d calls' % (len(sites), calls))
    def desc(f):
        m = re.search(r'adrp\s+x\d+, (\.LC\d+)', body(s, f))
        m = m and re.search(r'^%s:\n\t\.string\t"([^"]*)"' % re.escape(m.group(1)), s, re.M)
        return m.group(1) if m else None
    shapes = {'_Z2c1PFiiEi': 'stack:i:i', '_Z2c2P1Ai': 'this:i:i,i', '_Z2c3P1A': 'this:i:i,i',
              '_Z2d1PFviifE': 'fast:v:i,i,f', '_Z2d2PFxdxE': 'stack:q:d,q',
              '_Z2d3PF2V3S_2P23BigcES_S0_S1_': 'stack:a12:s12h,s8g,s32r,i', '_Z2d4PF3Big2NTERS0_': 'stack:m32:s4r',
              '_Z2d5P1BMS_FifdE': 'this:i:i,f,d'}
    for f, want_d in shapes.items():
        check('%s: descriptor %s' % (f, want_d), desc(f) == want_d, repr(desc(f)))
    print('test_dispatch: %s' % ('FAILED %d' % len(fails) if fails else 'ok'))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
