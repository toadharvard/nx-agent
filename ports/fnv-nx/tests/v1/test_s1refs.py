#!/usr/bin/env python3
"""test_s1refs.py - V1 (D14) against S1-refs c1039e7 (RREF1-CLASS, RREF2, ADDR1, LANE1), claimed neutral/platform.

    python3 -I tests/v1/test_s1refs.py      -> one line per case, exit 1 when any case shows a defect

Each case runs the rule on our own snippet, compiles the output with the host g++ (C++17, the shim's
fnvnx_lvalue) next to a reference that spells MSVC's meaning by hand, and runs both. A case is a defect when the
rewritten code does not compile, or computes something other than the MSVC meaning.
  L1  (&v.m128_f32)[0] -> lane 0's address                      LANE1 output == &v + 0
  L2  (&v.m128_f32)[1] -> the NEXT vector's lanes (float(*)[4]) LANE1 output == (char*)&v + 16
  L3  (0)[&v.m128_f32] (index first)                            == &v
  A1  (const V*)&_mm_andnot_ps(m, x) passed as an argument      the callee reads the vector's value
  A2  sizeof(int*)&_mm_movemask_ps(x): binary &, after `*)`     rewritten or not, the value is unchanged
  P1  a != E(0), E::operator!=(E&) mutating its operand         the same result and the same side effects
  P1  also a - E(1) with E::operator-(E&) in the same unit       the same value
  C1  RREF1-CLASS: the class's Read(V&) hides a free Read(V*)   compiles; the member is called
"""
import os
import shutil
import subprocess
import sys
import tempfile
import importlib.util
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, TOOLS)
import prepare  # noqa: E402

sys.modules['prepare'] = prepare
spec = importlib.util.spec_from_file_location('s1_compile', os.path.join(TOOLS, 'rules', 's1_compile.py'))
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)

PRE = r'''
#include <xmmintrin.h>
#include <cstdio>
#include <cstring>
template <class T> inline T &fnvnx_lvalue(T &&t) { return t; }
'''


def rule(fn, text):
    return fn('/nonexistent/src/x.cpp', text, Counter())


def build_run(work, name, src):
    c = os.path.join(work, name + '.cpp')
    exe = os.path.join(work, name)
    with open(c, 'w') as f:
        f.write(PRE + src)
    r = subprocess.run(['g++', '-std=c++17', '-O2', '-msse2', '-o', exe, c], capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr.strip().splitlines()[0] if r.stderr.strip() else 'compile failed'
    r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
    return r.stdout.strip(), None


def main():
    if not shutil.which('g++'):
        print('skip: no g++')
        return 0
    try:
        work = tempfile.mkdtemp(prefix='v1-s1refs-')
    except OSError as e:
        print('mktemp failed: %s' % e)
        return 2
    if not work or not os.path.isdir(work):
        print('mktemp failed')
        return 2
    bad = []

    def case(cid, what, got, want, err=None):
        ok = err is None and got == want
        print('%s: %s - %s%s' % (cid, 'ok' if ok else 'DEFECT', what,
                                 '' if ok else ' (got %r, want %r%s)' % (got, want, ', ' + err if err else '')))
        if not ok:
            bad.append(cid)

    try:
        # LANE1. SSE1 turns v.m128_f32 into ((float*)&(v)); the decomp's (&v.m128_f32)[i] becomes
        # (&((float*)&(v)))[i]. MSVC: &v.m128_f32 is float(*)[4], so [i] is the i-th 16-byte array.
        lanes = [('L1', '(&((float*)&(v)))[0]', 0), ('L2', '(&((float*)&(v)))[1]', 16), ('L3', '(0)[&((float*)&(v))]', 0)]
        for cid, expr, off in lanes:
            body = 'char* f(__m128* pv) { __m128& v = *pv; return (char*)(%s); }\n' % expr
            out = rule(s1.r_lane_array_address, body)
            src = out + ('int main() { static __m128 a[2]; std::printf("%ld\\n", (long)(f(&a[0]) - (char*)&a[0])); }\n')
            got, err = build_run(work, cid, src)
            case(cid, 'LANE1 %s' % expr, got, str(off), err)

        # ADDR1
        a1 = ('struct V { float x, y, z, w; };\nfloat g(const V* p) { return p->x + p->w; }\n'
              'float f(__m128 m, __m128 x) { return g((const V*)&_mm_andnot_ps(m, x)); }\n')
        out = rule(s1.r_address_of_temporaries, a1)
        src = out + ('int main() { __m128 m = _mm_set_ps(0, 0, 0, 0), x = _mm_set_ps(4, 3, 2, 1);'
                     ' std::printf("%g\\n", f(m, x)); }\n')
        got, err = build_run(work, 'A1', src)
        case('A1', 'ADDR1 address of an intrinsic result as an argument', got, '5', err)
        a2 = 'int f(__m128 x) { int r = sizeof(int*)&_mm_movemask_ps(x); return r; }\n'
        out = rule(s1.r_address_of_temporaries, a2)
        src = out + 'int main() { std::printf("%d\\n", f(_mm_set_ps(-1, -1, -1, -1))); }\n'
        got, err = build_run(work, 'A2', src)
        case('A2', 'ADDR1 binary & after `*)` keeps its value', got, str(8 & 15), err)

        # RREF2
        p1 = ('struct E { E(int v) : m(v) {} bool operator!=(E& o) { o.m++; return m != o.m - 1; }'
              ' E operator-(E& o) { return E(m - o.m); } E operator*(int k) { return E(m * k); } int m; };\n'
              'int calls;\nbool f(E* p) { return p[0] != E(0); }\n'
              'int g(E a, int k) { return (a - E(1)).m * k; }\n')
        out = rule(s1.r_operator_temporaries, p1)
        src = out + ('int main() { E e(3); std::printf("%d %d %d\\n", (int)f(&e), e.m, g(E(10), 3)); }\n')
        # the reference: MSVC binds the temporary; spelled as a named object with the same lifetime
        ref = p1.replace('p[0] != E(0)', '[&]{ E t(0); return p[0] != t; }()').replace(
            '(a - E(1))', '([&]{ E t(1); return a - t; }())')
        want, rerr = build_run(work, 'P1ref', ref + 'int main() { E e(3); std::printf("%d %d %d\\n", (int)f(&e), e.m, g(E(10), 3)); }\n')
        got, err = build_run(work, 'P1', src)
        case('P1', 'RREF2 operator!=(E&) / operator-(E&) with temporaries', got, want, err or rerr)

        # RREF1-CLASS
        c1 = ('struct V { int x; };\nint Read(void* p, V* v) { return -1; }\n'
              'struct R { int Read(void* p, V& v) { v.x = 7; return v.x; } int Use(); };\n'
              'V Make() { V v; v.x = 1; return v; }\nint R::Use() { return Read(0, Make()); }\n')
        out = rule(s1.r_temporaries_to_references, c1)
        src = out + 'int main() { R r; std::printf("%d\\n", r.Use()); }\n'
        got, err = build_run(work, 'C1', src)
        case('C1', 'RREF1-CLASS member Read(V&) hides the free Read(V*)', got, '7', err)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
