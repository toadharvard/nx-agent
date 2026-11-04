#!/usr/bin/env python3
"""V2 check of S1-refs 77868c1 (EBO2, tools/rules/s1_compile.py r_vfptr_empty_base): an empty base B of a class that
introduces its own vfptr goes after the vptr, as MSVC lays it out. MSVC 2008 SP1 values were measured under Wine
for this synthetic file and are recorded here (re-measured when FNV_MSVC9_ROOT / FNV_MSVC9_PREFIX are set); the
GCC side is the rule's output under AArch64 ILP32 with the shim's FNVNX_EBO2 (values read from the object's data).

    python3 tests/v2/test_s1refs_ebo2.py      # aarch64-linux-gnu-g++

  B1  the rule fires on B (only a base, used through pointers)
  B2  13 values equal MSVC's: sizeof / member offsets / base offset of D1 (vptr + int), D2 (vptr + char), D4 (vptr
      only), X (B's only base without a vptr: B stays at 0), Y : D1 (derived again)
The rule's guards (B used as an object, a class with B among several bases) are where the zero-size B would differ
from MSVC's 1-byte empty class; V2 checked that a file with those shapes is left alone.
"""
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import Counter

PORT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SRC = r'''#include <stddef.h>
struct E { int get() { return 1; } };
struct D1 : E { virtual void f(); int x; };
struct D2 : E { virtual void f(); char c; };
struct D4 : E { virtual ~D4(); };
struct X : E { int a; };
struct Y : D1 { int y; };
D1 *p1; X *px; Y *py; D4 *p4;
extern "C" int vals[] = {
  (int)sizeof(D1), (int)((char*)(E*)(D1*)64 - (char*)64), (int)offsetof(D1, x),
  (int)sizeof(D2), (int)offsetof(D2, c),
  (int)sizeof(D4), (int)((char*)(E*)(D4*)64 - (char*)64),
  (int)sizeof(X), (int)offsetof(X, a), (int)((char*)(E*)(X*)64 - (char*)64),
  (int)sizeof(Y), (int)offsetof(Y, y), (int)((char*)(E*)(Y*)64 - (char*)64),
};
void D1::f() {} void D2::f() {} D4::~D4() {}
'''
MSVC = [8, 4, 4, 8, 4, 4, 4, 4, 0, 0, 12, 8, 4]


def main():
    spec = importlib.util.spec_from_file_location('s1_compile', os.path.join(PORT, 'tools', 'rules', 's1_compile.py'))
    s1 = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(PORT, 'tools'))
    spec.loader.exec_module(s1)
    work = tempfile.mkdtemp(prefix='v2-ebo2-')   # raises when it cannot create the directory
    fails = 0
    try:
        out = s1.r_vfptr_empty_base(os.path.join(work, 'x.cpp'), SRC, Counter())
        ok = 'FNVNX_EBO2' in out
        print('%s B1 the rule fires on E' % ('ok' if ok else 'FAIL'))
        fails += not ok
        macro = next(l for l in open(os.path.join(PORT, 'shim', 'include', 'fnvnx_compat.h'))
                     if l.startswith('#define FNVNX_EBO2'))
        g = os.path.join(work, 'g.cpp')
        open(g, 'w').write(macro + out)
        subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-O0', '-w', '-c', g, '-o', g + '.o'], check=True)
        subprocess.run(['aarch64-linux-gnu-objcopy', '-O', 'binary', '-j', '.data', g + '.o', g + '.bin'], check=True)
        gcc = list(struct.unpack('<13i', open(g + '.bin', 'rb').read()[:52]))
        want = MSVC
        if os.environ.get('FNV_MSVC9_ROOT') and os.environ.get('FNV_MSVC9_PREFIX'):
            m = os.path.join(work, 'm.cpp')
            open(m, 'w').write('#include <stdio.h>\n' + SRC + 'int main() { for (int i = 0; i < 13; i++) '
                               'printf("%d ", vals[i]); printf("\\n"); }\n')
            tool = os.path.join(os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp')), 'tools', 'msvc.py')
            subprocess.run([sys.executable, tool, '--compiler', 'sp1', '/nologo', '/MT', '/Od', '/EHsc', m,
                            '/Fe' + m[:-4] + '.exe', '/Fo' + m[:-4] + '.obj'], cwd=work, capture_output=True,
                           timeout=600, env=dict(os.environ, WINEDEBUG='-all'))
            r = subprocess.run(['wine', m[:-4] + '.exe'], capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, WINEDEBUG='-all'))
            want = [int(x) for x in re.findall(r'-?\d+', r.stdout)] or MSVC
        ok = gcc == want
        print('%s B2 GCC after EBO2 %s, MSVC %s' % ('ok' if ok else 'FAIL', gcc, want))
        fails += not ok
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
