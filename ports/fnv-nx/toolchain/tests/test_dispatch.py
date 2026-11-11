#!/usr/bin/env python3
"""Test of toolchain/dispatch/fnvnx_dispatch.cc (DISPATCH1), built from the tracked source with the argv of the recipe's plugin
stage (plugbuild.py), loaded after the sdd and names plugins as in the product flag set. Every indirect call (function pointer,
virtual call, delete-expression, tail call, calls in a loop) checks its own site and calls fnvnx_dispatch on a miss; a direct
call is untouched; the call's shape is the descriptor string; an unknown plugin argument is an error. No network, no game data.
Run: python3 -I <this file> from the checkout root."""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_dispatch')
FIX = os.path.join(HERE, 'dispatch', 'dispatch.cpp')


def body(asm, sym):
    m = re.search(r'^%s:\n(.*?)\.cfi_endproc' % re.escape(sym), asm, re.S | re.M)
    return m.group(1) if m else ''


def checks(s):
    want = {'_Z2c1PFiiEi': 1, '_Z2c2P1Ai': 1, '_Z2c3P1A': 1, '_Z2c4PFiiEi': 1, '_Z2c5PFiiES0_i': 2, '_Z2c6i': 0}
    for f, n in want.items():
        b = body(s, f)
        check('%s: %d dispatch call(s)' % (f, n), len(re.findall(r'\bbl\s+fnvnx_dispatch\b', b)) == n, b)
        check('%s: no indirect call without a site check' % f,
              len(re.findall(r'\b(?:blr|br)\s+x\d+', b)) == n and len(re.findall(r'\bcmp\s+w\d+, w\d+', b)) >= n, b)
    hit = re.sub(r'\s+', ' ', body(s, '_Z2c1PFiiEi'))
    check('hit path: ONE 64-bit load of the site, compare the low half of that register, branch, shift the register',
          re.search(r'ldr x(\d+), \[x\d+, #:lo12:\.LANCHOR0\].*? cmp w0, w\1 bne \.L\d+ lsr x\d+, x\1, 32', hit)
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


def main():
    td = tempfile.mkdtemp(prefix='test-dispatch-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names', 'dispatch')]
        out = os.path.join(td, 't.s')
        r = pb.compile(FIX, out, so, asm=True)
        check('compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            checks(open(out).read())
        r = pb.compile(FIX, os.path.join(td, 'x.s'), so, args=['-fplugin-arg-fnvnx_dispatch-mode=1'], asm=True)
        check('an unknown plugin argument is an error', r.returncode != 0 and 'unknown plugin argument' in r.stderr, r.stderr[:500])
        r = pb.compile(FIX, os.path.join(td, 'y.s'), [so[0], so[2]], asm=True)
        check('without the names plugin the dispatcher says so', r.returncode != 0 and 'fnvnx_names plugin is not loaded' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
