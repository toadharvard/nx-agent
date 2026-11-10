#!/usr/bin/env python3
"""test_sdd1.py - synthetic tests of prepare rule SDD1 (rules/sdd1.py, shim/include/fnvnx_sdd.h).

    python3 rules/tests/test_sdd1.py

1. The rule's text edits on small inputs (placement by syntax, line count kept).
2. With aarch64-linux-gnu-g++ present: sdd1_semantics.cpp compiles with and without FNVNX_SDD; with it there is no
   deleting destructor (D0), `delete p` on a polymorphic class is one virtual call, each __sdd calls the operator
   delete that the class's scope lookup finds, and a non-polymorphic class keeps its size and its plain delete.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
import sdd1

FAILS = []


def check(name, cond, detail=''):
    if not cond:
        FAILS.append(name)
        print('FAIL', name, detail)


def rw(text):
    m = sdd1.mask(text)
    st = Counter()
    out = sdd1.rewrite(text, m, sdd1.classes(text, m), st)
    check('line count', out.count('\n') == text.count('\n'), repr(text))
    return out, st


def text_tests():
    out, st = rw('struct A {\n  virtual ~A();\n  virtual int f();\n};\n')
    check('vdtor', 'FNVNX_VDTOR(A) FNVNX_SDD_VIRTUAL ~A();' in out and st['SDD1-VDTOR'] == 1, out)
    out, st = rw('class B : public A, private ns::M<int, X> {\n  inline virtual ~B() {}\n};\n')
    check('vdtor bases', 'FNVNX_VDTOR(B, A, ns::M<int, X>) inline FNVNX_SDD_VIRTUAL ~B() {}' in out, out)
    out, st = rw('struct C : A {\n  ~C();\n  int x;\n};\n')
    check('derived with a destructor', out.endswith('  int x;\n FNVNX_SDD_DERIVED(C, A) };\n') and '~C();' in out, out)
    out, st = rw('struct D : A { int y; };\n')
    check('derived without a destructor', 'int y;  FNVNX_SDD_DERIVED(D, A) };' in out and st['SDD1-DERIVED'] == 1, out)
    out, st = rw('struct E { ~E(); int f(); };\nstruct F;\nenum class G : int { a };\ntemplate <class T> struct H { T t; };\n')
    check('no base, no virtual destructor: untouched', 'FNVNX' not in out, out)
    out, st = rw('struct P : A {\n  virtual ~P() = 0;\n};\n')
    check('pure', '~P() FNVNX_SDD_PURE(= 0);' in out and st['SDD1-PURE'] == 1, out)
    out, st = rw('struct O { struct I : A { int i; }; virtual ~O(); };\n')
    check('nested', 'int i;  FNVNX_SDD_DERIVED(I, A) }' in out and 'FNVNX_VDTOR(O) FNVNX_SDD_VIRTUAL ~O();' in out, out)
    out, st = rw('void f(A* p, A& r, T* q) {\n  p->~A();\n  r.~A();\n  a.b[i]->c()->~A();\n  reinterpret_cast<A*>(q)->~A();\n'
                 '  this->A::~A();\n  q->~T();\n  x->~M<int, U>();\n}\n')
    for want in ('FNVNX_DESTROY(p, A);', 'FNVNX_DESTROY_REF(r, A);', 'FNVNX_DESTROY(a.b[i]->c(), A);',
                 'FNVNX_DESTROY(reinterpret_cast<A*>(q), A);', 'this->A::~A();', 'FNVNX_DESTROY(q, T);',
                 'FNVNX_DESTROY(x, M<int, U>);'):
        check('destroy ' + want, want in out, out)
    check('destroy counts', st['SDD1-DESTROY'] == 6 and st['SDD1-DESTROY-MISSED'] == 0, str(st))
    out, st = rw('void A::operator delete(void* p) { g(p); }\nvoid f(void* p) {\n  A::operator delete(p);\n'
                 '  ns::B::operator delete(this, 0x1c);\n  ::operator delete(p);\n}\n')
    check('opdelete', 'FNVNX_OPDELETE1(p, A);' in out and 'FNVNX_OPDELETE2(this, 0x1c, ns::B);' in out
          and 'void A::operator delete(void* p) { g(p); }' in out and '  ::operator delete(p);' in out, out)
    out, st = rw('// struct X : A { };\nconst char* s = "struct Y : A { }; p->~A();";\n')
    check('comments and strings', 'FNVNX' not in out, out)
    out, st = rw('struct : A { int z; } anon;\n')
    check('anonymous class with a base is counted', st['SDD1-ANON'] == 1, str(st))


def calls(asm, sym):
    m = re.search(r'^%s:\n(.*?)\.cfi_endproc' % re.escape(sym), asm, re.S | re.M)
    return re.findall(r'^\s+(?:bl|b|blr|br)\s+(\S+)', m.group(1), re.M) if m else ['<no such function>']


def compile_tests():
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('skip: no aarch64-linux-gnu-g++')
        return
    neon = os.environ.get('SSE2NEON', os.path.join(ROOT, 'ext', 'sse2neon'))
    if not os.path.isfile(os.path.join(neon, 'sse2neon.h')):
        print('skip: no sse2neon (tools/fetch.sh), which fnvnx_compat.h includes')
        return
    base = [cxx, '-I' + neon, '-mabi=ilp32', '-std=gnu++17', '-O2', '-fsigned-char', '-fms-extensions', '-fno-sized-deallocation',
            '-fcheck-new', '-fshort-wchar', '-fPIC', '-fno-devirtualize-speculatively', '-I' + os.path.join(ROOT, 'shim', 'include'), '-S']
    src = os.path.join(HERE, 'sdd1_semantics.cpp')
    with tempfile.TemporaryDirectory() as td:
        res = {}
        for mode, flag in (('sdd', ['-DFNVNX_SDD=1']), ('plain', [])):
            out = os.path.join(td, mode + '.s')
            r = subprocess.run(base + flag + ['-include', os.path.join(ROOT, 'shim', 'include', 'fnvnx_compat.h'), '-o', out, src],
                               capture_output=True, text=True)
            check('compiles ' + mode, r.returncode == 0, r.stderr[:2000])
            res[mode] = open(out).read() if r.returncode == 0 else ''
    s, p = res['sdd'], res['plain']
    if not s or not p: return
    check('plain mode keeps the deleting destructors', 'D0Ev:' in p and '__sddE' not in p)
    check('no deleting destructor', 'D0Ev' not in s)
    for f in ('_Z5del_aP1A', '_Z5del_bP1B', '_Z6del_b2P2B2', '_Z5del_dP1D', '_Z5del_mP1M', '_Z6del_caPK1A', '_Z6kill_pP1P'):
        check('delete is one virtual call: ' + f, calls(s, f) == ['x16'], str(calls(s, f)))
    check('explicit destructor call is virtual', calls(s, '_Z9destroy_aP1A') == ['x16'])
    check('B frees with its own operator delete', calls(s, '_ZN1B5__sddEj')[-1:] == ['other_free'])
    check('B2 inherits B\'s operator delete', calls(s, '_ZN2B25__sddEj')[-1:] == ['other_free'])
    check('M frees through A\'s base Mem (sized)', calls(s, '_ZN1M5__sddEj') == ['_ZN1MD1Ev', 'sized_free'])
    check('D frees with the global operator delete', calls(s, '_ZN1D5__sddEj')[-1:] == ['_ZdlPv'])
    check('non-polymorphic delete: destructor then sized delete',
          calls(s, '_Z6del_n1P2N1') == ['_ZN2N1D1Ev', 'sized_free'] and calls(s, '_Z6del_n2P2N2')[-1:] == ['sized_free'],
          str(calls(s, '_Z6del_n1P2N1')))
    vt = re.search(r'^_ZTV1M:\n((?:\s+\.\w+\s+\S+\n)+)', s, re.M).group(1).split()[1::2]
    check('M\'s table: one destructor slot, exe order', vt[2:5] == ['_ZN1M5__sddEj', '_ZN1A1fEv', '_ZN1M1kEv'], str(vt))


if __name__ == '__main__':
    text_tests()
    compile_tests()
    print('test_sdd1: %s' % ('FAILED %d' % len(FAILS) if FAILS else 'ok'))
    sys.exit(1 if FAILS else 0)
