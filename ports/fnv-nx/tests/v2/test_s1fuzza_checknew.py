#!/usr/bin/env python3
"""V2 check of S1-fuzzA 5a6c8c7 (-fcheck-new in the game build): "MSVC 2008 null-tests every new-expression,
placement new included". Both sides compiled from one synthetic file: MSVC 2008 SP1 /O2 under Wine (objdump of the
COFF object) and GCC with compile.sh --print-flags (asm).

    FNV_MSVC9_ROOT=.. FNV_MSVC9_PREFIX=.. SSE2NEON=.. python3 tests/v2/test_s1fuzza_checknew.py   (MSVC part skips without)

  N1  compile.sh's flags carry -fcheck-new
  N2  `new T`, `new (p) T`, and a class operator new: GCC tests the pointer (cbz) before the constructor's store
  N3  MSVC tests it too (test eax, eax; je) in all three
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SRC = '''#include <new>
struct T { int x; T() : x(7) {} };
struct U { int y; U() : y(9) {} static void *operator new(size_t); };
T *plain() { return new T; }
T *place(void *p) { return new (p) T; }
U *cls() { return new U; }
'''
FNS = ('plain', 'place', 'cls')


def main():
    work = tempfile.mkdtemp(prefix='v2-checknew-')   # raises when it cannot create the directory
    fails = 0

    def check(c, what):
        nonlocal fails
        print('%s %s' % ('ok' if c else 'FAIL', what))
        fails += not c
    try:
        src = os.path.join(work, 'n.cpp')
        open(src, 'w').write(SRC)
        os.makedirs(os.path.join(work, 'tree', 'src'))
        flags = [f for f in subprocess.run(['bash', os.path.join(PORT, 'tools', 'link', 'compile.sh'), '--print-flags',
                                            os.path.join(work, 'tree'), os.path.join(work, 'w')],
                                           capture_output=True, text=True).stdout.split('\n') if f]
        check('-fcheck-new' in flags, 'N1 compile.sh carries -fcheck-new')
        asm = os.path.join(work, 'n.s')
        subprocess.run(['aarch64-linux-gnu-g++'] + [f for f in flags if f != '-c'] + ['-S', '-o', asm, src], check=True,
                       capture_output=True)
        text = open(asm).read()
        for fn in FNS:
            m = re.search(r'^_Z\d+%s\w*:(.*?)\.cfi_endproc' % fn, text, re.S | re.M)
            body = m.group(1) if m else ''
            cb, st = body.find('cbz'), body.find('str\tw')
            check(m and 0 <= cb < st, 'N2 GCC %s: null test before the constructor store' % fn)
        if os.environ.get('FNV_MSVC9_ROOT') and os.environ.get('FNV_MSVC9_PREFIX'):
            tool = os.path.join(os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp')), 'tools', 'msvc.py')
            obj = os.path.join(work, 'n.obj')
            subprocess.run([sys.executable, tool, '--compiler', 'sp1', '/nologo', '/c', '/O2', '/EHsc', src, '/Fo' + obj],
                           cwd=work, capture_output=True, timeout=600, env=dict(os.environ, WINEDEBUG='-all'))
            dis = subprocess.run(['objdump', '-d', '-M', 'intel', obj], capture_output=True, text=True).stdout
            for fn in FNS:
                m = re.search(r'<\?%s@@[^>]*>:\n(.*?)(?:\n\n|\Z)' % fn, dis, re.S)
                body = m.group(1) if m else ''
                check(re.search(r'test\s+eax,eax\s*\n.*\bje\b', body) is not None, 'N3 MSVC %s: test eax, eax; je' % fn)
        else:
            print('skip N3: no MSVC 2008')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
