#!/usr/bin/env python3
"""test_msvcmodel.py - the game's compiler has the exe's data model and array new/delete (MSVCMODEL1).

    CXX=<prefix>/bin/aarch64-linux-gnu-g++ python3 toolchain/msvcmodel/test_msvcmodel.py

Compiles test_msvcmodel.cpp (-mabi=ilp32 -O2, exceptions on) and checks the constants the compiler passes against
the pinned cl's code for the same lines (the comments of test_msvcmodel.cpp). Skips when $CXX is a compiler without
msvcmodel.patch (size_t mangled `m`): the distribution's.
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
    return re.sub(r'\s+', ' ', re.sub(r'^\s*\.cfi.*$', '', m.group(1), flags=re.M)) if m else ''


def main():
    cxx = os.environ.get('CXX') or shutil.which('aarch64-linux-gnu-g++')
    if not cxx or not shutil.which(cxx):
        print('skip: no compiler'); return 0
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, 't.s')
        r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fno-pic', '-S', '-o', out,
                            os.path.join(HERE, 'test_msvcmodel.cpp')], capture_output=True, text=True)
        if r.returncode != 0:
            print('FAIL compiles', r.stderr[-2000:]); return 1
        s = open(out).read()
    if '_Z5typesmlml:' in s:
        print('skip: %s is not the msvcmodel compiler (size_t is unsigned long)' % cxx); return 0
    fails = []
    def check(name, cond, detail=''):
        if not cond: fails.append(name); print('FAIL', name, str(detail)[:900])
    check('size_t is unsigned int, ptrdiff_t is int, sizeof and pointer difference have those types', '_Z5typesjiji:' in s,
          re.findall(r'^_Z5types\w*:', s, re.M))
    def const_arg(fn, callee, reg='w0'):
        """the constant moved into reg before the call of callee in fn"""
        b = body(s, fn)
        m = re.search(r'mov %s, (\d+)\b(?:(?!\bbl?\b).)*\bbl? %s\b' % (reg, callee), b)
        return int(m.group(1)) if m else None
    for fn, callee, want in (('_Z3n_dv', '_Znaj', 16), ('_Z4n_a8v', '_Znaj', 28), ('_Z4n_nmv', 'heap_alloc', 60),
                             ('_Z4n_ndv', 'heap_alloc', 72), ('_Z4n_nvv', 'heap_alloc', 88), ('_Z3n_nv', 'heap_alloc', 40)):
        check('%s allocates %d bytes (cl)' % (fn, want), const_arg(fn, callee) == want, body(s, fn))
    for fn, want in (('_Z4d_nmP2NM', 20), ('_Z4d_ndP2ND', 24), ('_Z4d_nvP2NV', 28), ('_Z3d_nP1N', 12)):
        check('%s: the sized operator delete[] gets the element size %d (cl)' % (fn, want),
              const_arg(fn, 'heap_free', 'w1') == want, body(s, fn))
    check('no count before an array of a trivially destructible class', '-4]' not in body(s, '_Z4d_nmP2NM')
          and '-4]' not in body(s, '_Z4d_ndP2ND'), body(s, '_Z4d_nmP2NM'))
    check('the count is the 4 bytes before the array, the block starts there', re.search(r'ldr w\d+, \[x\d+, -4\]', body(s, '_Z4d_nvP2NV'))
          and re.search(r'sub [wx]0, [wx]\d+, #4', body(s, '_Z4d_nvP2NV')), body(s, '_Z4d_nvP2NV'))
    check('an overflowing new[] size is passed as the largest size, nothing is thrown',
          '__cxa_throw_bad_array_new_length' not in s and re.search(r'csinv|mov w0, -1|mov w0, 4294967295', body(s, '_Z5n_varj')),
          body(s, '_Z5n_varj'))
    print('test_msvcmodel: %s' % ('FAILED %d' % len(fails) if fails else 'ok'))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
