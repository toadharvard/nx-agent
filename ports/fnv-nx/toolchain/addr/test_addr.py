#!/usr/bin/env python3
"""test_addr.py - synthetic test of the fnvnx_addr plugin (ADDR32).

    python3 toolchain/addr/test_addr.py

Without the plugin GCC folds the constant of a 32-bit address sum into a 64-bit addressing mode (`uxtw; ldr [x, 64]`);
with it the sum is finished in 32 bits before it is used as an address. Skips without the cross compiler.
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
    return re.sub(r'\s+', ' ', re.sub(r'^\s*\..*$', '', m.group(1), flags=re.M)).strip() if m else ''


def main():
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++'); return 0
    base = [cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fwrapv', '-fno-strict-aliasing', '-S']
    src = os.path.join(HERE, 'test_addr.cpp')
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(['bash', os.path.join(HERE, 'build.sh'), os.path.join(td, 'so')], capture_output=True, text=True)
        if r.returncode != 0:
            print('skip: the plugin does not build here'); return 0
        so = r.stdout.strip().splitlines()[-1]
        res = {}
        for mode, extra in (('plain', []), ('addr', ['-fplugin=' + so])):
            out = os.path.join(td, mode + '.s')
            r = subprocess.run(base + extra + ['-o', out, src], capture_output=True, text=True)
            if r.returncode != 0:
                print('FAIL compiles', mode, r.stderr[:1500]); return 1
            res[mode] = open(out).read()
    fails = []
    def check(name, cond, detail=''):
        if not cond: fails.append(name); print('FAIL', name, detail)
    check('the fact: GCC alone splits the sum', 'uxtw x0, w0 ldr w0, [x0, 64]' in body(res['plain'], '_Z2r1j'), body(res['plain'], '_Z2r1j'))
    a = res['addr']
    check('load: the sum in 32 bits, then the address', body(a, '_Z2r1j') == 'add w0, w0, 64 uxtw x0, w0 ldr w0, [x0] ret', body(a, '_Z2r1j'))
    check('store likewise', body(a, '_Z2r5ji') == 'add w0, w0, 64 uxtw x0, w0 str w1, [x0] ret', body(a, '_Z2r5ji'))
    check('scaled index plus a constant base', 'lsl w0, w0, 2 uxtw x0, w0 ldr w0, [x0]' in body(a, '_Z2r2i'), body(a, '_Z2r2i'))
    check('a pointer that was never an integer is untouched', body(a, '_Z2r3Pii') == body(res['plain'], '_Z2r3Pii'))
    for f in ('_Z2r1j', '_Z2r4j', '_Z2r5ji', '_Z2r6jj'):
        check('%s: no constant folded into the address of a converted integer' % f,
              not re.search(r'uxtw x(\d+), w\d+ (?:ldr|str) w\d+, \[x\1, (?:6[4-9]|[7-9]\d|\d{3,})\]', body(a, f)), body(a, f))
    print('test_addr: %s' % ('FAILED %d' % len(fails) if fails else 'ok'))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
