#!/usr/bin/env python3
"""V1 (D14) against J-0123 (56ae24d, TAIL1 writes `typename` before a qualified last base): "no unit regressed".

    python3 tests/v1/test_tail1_typename.py        (from ports/fnv-nx; the compile half needs aarch64-linux-gnu-g++ and
                                                    $SSE2NEON, default ext/sse2neon)

Runs tools/rules/s1_compile.py's r_msvc_base_layout on hand-written class heads (our own code, shaped like the
decomp's) and compiles each result with census.sh's flags and fnvnx_compat.h. Every case compiles before TAIL1
(the input) and must compile after it.

Measured on the head's prepared tree (V1, 133 units: the 131 TAIL1 typename units and the 3 startmenu units that
include startmenu_packet.h, each compiled with and without the typename edit): 126 same, 4 better, 2 WORSE:
indexedgeometry_ob1_variant 2 -> 7 and blk_00ac0000_od_ob1_variant 18 -> 28, both "expected nested-name-specifier"
at FNVNX_MSVC_BASE_END(typename _Ranit<..., typename _Alloc::difference_type, ...>) - the farm census at ff0fd85 shows
the same two units with the same counts (2d912c5 -> ff0fd85).
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import prepare  # noqa: E402
sys.modules['prepare'] = prepare
spec = importlib.util.spec_from_file_location('s1_compile', os.path.join(ROOT, 'tools', 'rules', 's1_compile.py'))
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)

CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FLAGS = ['-mabi=ilp32', '-std=gnu++17', '-fsyntax-only', '-fms-extensions', '-fshort-wchar', '-fpermissive',
         '-fsigned-char', '-include', os.path.join(ROOT, 'shim', 'include', 'fnvnx_compat.h'),
         '-I' + os.path.join(ROOT, 'shim', 'include'), '-I' + os.environ.get('SSE2NEON', os.path.join(ROOT, 'ext', 'sse2neon'))]

PRE = '''namespace N { struct Base { virtual ~Base(); char c; }; struct X {}; template <class T> struct TB { char c; }; }
struct Base { virtual ~Base(); char c; };
template <class A> struct Alloc { typedef int difference_type; typedef const A *const_pointer; };
template <class T, class D, class P> struct Ranit { virtual ~Ranit(); char c; };
template <class T> struct Out { struct In { virtual ~In(); char c; }; };
'''

CASES = {
    # the shape that regressed: a template-id base whose arguments are qualified and span lines
    'multiline-args': PRE + '''template <class T, class A>
struct It : public Ranit<T, typename A::difference_type,
                         typename A::const_pointer>
{
    int a;
};
template struct It<int, Alloc<int> >;
''',
    'oneline-args': PRE + '''template <class T, class A>
struct It : public Ranit<T, typename A::difference_type, typename A::const_pointer>
{
    int a;
};
template struct It<int, Alloc<int> >;
''',
    'qualified': PRE + 'struct U : N::Base\n{\n    int a;\n};\n',
    'global-qualified': PRE + 'struct U : ::Base\n{\n    int a;\n};\n',
    'dependent-nested': PRE + 'template <class T> struct W : Out<T>::In\n{\n    int a;\n};\ntemplate struct W<int>;\n',
    'qualified-template': PRE + 'struct V : N::TB<N::X>\n{\n    int a;\n};\n',
    'multiline-qualified': PRE + 'template <class T> struct W : public Out<\n    T>::In\n{\n    int a;\n};\ntemplate struct W<int>;\n',
}


def compiles(text, d, name):
    p = os.path.join(d, name + '.cpp')
    with open(p, 'w') as f:
        f.write(text)
    r = subprocess.run([CXX] + FLAGS + [p], capture_output=True, text=True)
    return r.returncode == 0, [l for l in r.stderr.splitlines() if ': error:' in l][:2]


def main():
    have_cxx = shutil.which(CXX) is not None
    fails = []
    d = tempfile.mkdtemp()
    try:
        for name, src in CASES.items():
            out = s1.r_msvc_base_layout('/nonexistent/src/x.cpp', src, Counter())
            line = next((l.strip() for l in out.splitlines() if 'FNVNX_MSVC_BASE_END' in l), '(no TAIL1 edit)')
            verdict = 'ok'
            if have_cxx:
                before, _ = compiles(src, d, name + '_before')
                after, errs = compiles(out, d, name + '_after')
                if before and not after:
                    verdict = 'REGRESSED: ' + ' | '.join(e.split('error: ')[-1] for e in errs)
                    fails.append(name)
                elif not before:
                    verdict = 'input does not compile (case broken)'
                    fails.append(name)
            print('%-20s %-70s %s' % (name, line[:70], verdict))
    finally:
        shutil.rmtree(d)
    if not have_cxx:
        print('(no %s: rule output only)' % CXX)
    print('TAIL1 typename: %s' % ('ok' if not fails else 'FAIL ' + ', '.join(fails)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
