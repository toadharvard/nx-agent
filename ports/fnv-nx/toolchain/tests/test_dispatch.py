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


def body(dis, sym):
    """the instructions and relocation lines of function SYM of the object (objdump -dr), tabs squeezed to one space"""
    return '\n'.join(re.sub(r'\s+', ' ', l.strip()) for l in dis.get(sym, []))


def calls(b, target='fnvnx_dispatch'):
    return len(re.findall(r'R_AARCH64_P32_(?:CALL|JUMP)26 %s\b' % re.escape(target), b))


def checks(obj):
    dis = pb.objdump(obj)
    want = {'_Z2c1PFiiEi': 1, '_Z2c2P1Ai': 1, '_Z2c3P1A': 1, '_Z2c4PFiiEi': 1, '_Z2c5PFiiES0_i': 2, '_Z2c6i': 0}
    for f, n in want.items():
        b = body(dis, f)
        check('%s: %d relocation(s) against fnvnx_dispatch' % (f, n), calls(b) == n, b)
        check('%s: no indirect call without a site check' % f,
              len(re.findall(r'[0-9a-f]+: [0-9a-f]+ (?:blr|br) x\d+', b)) == n and len(re.findall(r'[0-9a-f]+: [0-9a-f]+ cmp w\d+, w\d+', b)) >= n, b)
    c1 = body(dis, '_Z2c1PFiiEi')
    check('hit path: ONE 64-bit load of the site, compare the low half of that register, branch, shift the register',
          re.search(r'ldr x(\d+), \[x\d+\] [0-9a-f]+: R_AARCH64_P32_LDST64_ABS_LO12_NC \.bss.*? cmp w0, w\1 [0-9a-f]+: [0-9a-f]+ b\.ne .*? lsr x\d+, x\1, #32', c1.replace('\n', ' '))
          and len(re.findall(r'R_AARCH64_P32_LDST64_ABS_LO12_NC \.bss', c1)) == 1, c1)
    c6 = body(dis, '_Z2c6i')
    check('a direct call is untouched', calls(c6, '_Z6directi') == 1 and 'fnvnx_dispatch' not in c6, c6)
    n = sum(calls(body(dis, f)) for f in dis)
    sz = pb.section_size(obj, '.bss')
    check('one 8-byte zero site per indirect call', sz == 8 * n and n == 11, '.bss %s bytes, %d dispatch relocations' % (sz, n))

    def desc(f):
        m = re.search(r'R_AARCH64_P32_ADR_PREL_PG_HI21 (\.rodata\.str1\.8)(?:\+0x([0-9a-f]+))?', body(dis, f))
        return m and pb.cstring(obj, m.group(1), int(m.group(2) or '0', 16))
    shapes = {'_Z2c1PFiiEi': 'stack:i:i', '_Z2c2P1Ai': 'this:i:i,i', '_Z2c3P1A': 'this:i:i,i',
              '_Z2d1PFviifE': 'fast:v:i,i,f', '_Z2d2PFxdxE': 'stack:q:d,q',
              '_Z2d3PF2V3S_2P23BigcES_S0_S1_': 'stack:a12Hhf:s12hf,s8g,s32r,i', '_Z2d4PF3Big2NTERS0_': 'stack:a32Hm:s4r',
              '_Z2d5P1BMS_FifdE': 'this:i:i,f,d'}
    for f, want_d in shapes.items():
        check('%s: descriptor %s' % (f, want_d), desc(f) == want_d, repr(desc(f)))


def main():
    td = tempfile.mkdtemp(prefix='test-dispatch-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names', 'dispatch')]
        out = os.path.join(td, 't.o')
        r = pb.compile(FIX, out, so)
        check('compiles', r.returncode == 0, r.stderr[:2000])
        if r.returncode == 0:
            checks(out)
        r = pb.compile(FIX, os.path.join(td, 'x.o'), so, args=['-fplugin-arg-fnvnx_dispatch-mode=1'])
        check('an unknown plugin argument is an error', r.returncode != 0 and 'unknown plugin argument' in r.stderr, r.stderr[:500])
        r = pb.compile(FIX, os.path.join(td, 'y.o'), [so[0], so[2]])
        check('without the names plugin the dispatcher says so', r.returncode != 0 and 'fnvnx_names plugin is not loaded' in r.stderr, r.stderr[:500])
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
