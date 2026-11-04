#!/usr/bin/env python3
"""V2 check of S1-STL b78319e layout claims against MSVC 2008 itself (SP1 and RTM under Wine), not against numbers
the stream wrote down:
  V1  shim/include/fnvnx_vc9.h: sizeof/offsets of VC9 release std::string, vector<int>, map<string, string> and their
      iterators (/MT /Od /EHsc, _SECURE_SCL default) equal the header's static_asserts (28/4/0x14/0x18,
      24/0xc/0x10/0x14, 32/0x18/0x1c, 8/8/8)
  E1  EBO1 (tools/rules/s1_compile.py r_msvc_empty_bases): eight shapes of `struct D : A, B {...}` with A, B empty
      (char[19], int, double, a member of type A, D with its own vptr, char, char/short/int, a member of type B):
      sizeof and member offsets of the rule's output under GCC AArch64 ILP32 equal MSVC's

    FNV_MSVC9_ROOT=.. FNV_MSVC9_RTM_ROOT=.. FNV_MSVC9_PREFIX=.. FNV_DECOMP=<fnv_decomp> python3 tests/v2/test_s1stl_layouts.py
    (skip without the compilers; our own synthetic sources only)
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
STL = r'''
#include <stdio.h>
#include <stddef.h>
#include <string>
#include <vector>
#include <map>
#define OFF(T, m) ((int)(size_t)&reinterpret_cast<const volatile char&>(((T*)0)->m))
struct A : std::string { int s() { return OFF(A, _Bx); } int z() { return OFF(A, _Mysize); } int r() { return OFF(A, _Myres); } };
struct V : std::vector<int> { int f() { return OFF(V, _Myfirst); } int l() { return OFF(V, _Mylast); } int e() { return OFF(V, _Myend); } };
struct M : std::map<std::string, std::string> { int h() { return OFF(M, _Myhead); } int n() { return OFF(M, _Mysize); } };
int main() {
  printf("%d %d %d %d ", (int)sizeof(std::string), A().s(), A().z(), A().r());
  printf("%d %d %d %d ", (int)sizeof(std::vector<int>), V().f(), V().l(), V().e());
  printf("%d %d %d ", (int)sizeof(std::map<std::string, std::string>), M().h(), M().n());
  printf("%d %d %d\n", (int)sizeof(std::vector<int>::iterator), (int)sizeof(std::string::iterator),
         (int)sizeof(std::map<std::string, std::string>::iterator));
}
'''
STL_WANT = [28, 4, 0x14, 0x18, 24, 0xc, 0x10, 0x14, 32, 0x18, 0x1c, 8, 8, 8]
EBO = '''#include <stddef.h>
struct A {}; struct B {};
struct D1 : A, B { char m[19]; };
struct D2 : A, B { int x; };
struct D3 : A, B { double d; };
struct D4 : A, B { A a; int x; };
struct D5 : A, B { virtual void f() {} int x; };
struct D6 : A, B { char c; };
struct D7 : A, B { char c; short s; int i; };
struct D8 : A, B { B b; char c; };
'''
VALS = ['sizeof(D1)', 'offsetof(D1,m)', 'sizeof(D2)', 'offsetof(D2,x)', 'sizeof(D3)', 'offsetof(D3,d)', 'sizeof(D4)',
        'offsetof(D4,a)', 'offsetof(D4,x)', 'sizeof(D5)', 'offsetof(D5,x)', 'sizeof(D6)', 'offsetof(D6,c)',
        'sizeof(D7)', 'offsetof(D7,c)', 'offsetof(D7,s)', 'offsetof(D7,i)', 'sizeof(D8)', 'offsetof(D8,b)',
        'offsetof(D8,c)']


def msvc_run(work, compiler, src, name):
    msvc = os.path.join(os.environ.get('FNV_DECOMP', os.path.join(PORT, 'ext', 'fnv_decomp')), 'tools', 'msvc.py')
    p = os.path.join(work, name + '.cpp')
    open(p, 'w').write(src)
    subprocess.run([sys.executable, msvc, '--compiler', compiler, '/nologo', '/MT', '/Od', '/EHsc', p,
                    '/Fe' + os.path.join(work, name + '.exe'), '/Fo' + os.path.join(work, name + '.obj')],
                   cwd=work, capture_output=True, timeout=600)
    r = subprocess.run(['wine', os.path.join(work, name + '.exe')], capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, WINEDEBUG='-all'))
    return [int(x) for x in re.findall(r'-?\d+', r.stdout)]


def main():
    if not all(os.environ.get(v) for v in ('FNV_MSVC9_ROOT', 'FNV_MSVC9_RTM_ROOT', 'FNV_MSVC9_PREFIX')):
        print('skip: no MSVC 2008 (FNV_MSVC9_ROOT, FNV_MSVC9_RTM_ROOT, FNV_MSVC9_PREFIX)')
        return 0
    work = tempfile.mkdtemp(prefix='v2-s1stl-')   # raises when it cannot create the directory
    fails = 0
    try:
        for c in ('sp1', 'rtm'):
            got = msvc_run(work, c, STL, 'stl_' + c)
            ok = got == STL_WANT
            print('%s V1 %s VC9 layouts %s (header %s)' % ('ok' if ok else 'FAIL', c, got, STL_WANT))
            fails += not ok
        printer = ''.join('printf("%%d ", (int)(%s));' % v for v in VALS)
        msvc = msvc_run(work, 'sp1', '#include <stdio.h>\n' + EBO + 'int main() { %s printf("\\n"); }\n' % printer,
                        'ebo')
        spec = importlib.util.spec_from_file_location('s1_compile', os.path.join(PORT, 'tools', 'rules', 's1_compile.py'))
        s1 = importlib.util.module_from_spec(spec)
        sys.path.insert(0, os.path.join(PORT, 'tools'))
        spec.loader.exec_module(s1)
        out = s1.r_msvc_empty_bases('x.cpp', EBO, Counter())
        g = os.path.join(work, 'g.cpp')
        open(g, 'w').write(out + 'extern "C" { int vals[] = {%s}; }\n' % ','.join('(int)' + v for v in VALS))
        subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-O0', '-w', '-c', g, '-o', g + '.o'], check=True)
        subprocess.run(['aarch64-linux-gnu-objcopy', '-O', 'binary', '-j', '.data', g + '.o', g + '.bin'], check=True)
        gcc = list(struct.unpack('<%di' % len(VALS), open(g + '.bin', 'rb').read()[:4 * len(VALS)]))
        ok = gcc == msvc and out.count('fnvnx_msvc_ebo') == 8
        print('%s E1 EBO1: MSVC %s, GCC ILP32 after the rule %s' % ('ok' if ok else 'FAIL', msvc, gcc))
        fails += not ok
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print('ok' if not fails else 'FAILED')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
