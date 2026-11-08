#!/usr/bin/env python3
"""tests/s2d - STL1 (tools/rules/s4_stl.py) on a hand-written unit: a `namespace std` copy declaring VC9's helper
templates (_Allocate, _Destroy, fill, swap) and a vector with `static void _Xlen();`, none defined. After the rule
the unit compiles (system aarch64-linux-gnu-g++ -mabi=ilp32, -O0 so nothing is inlined away) with none of them
undefined; a unit that defines them itself is left as it is. Our own code only."""
import collections
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(P, 'tools', 'rules'))
import s4_stl  # noqa: E402

UNIT = r'''
typedef unsigned long size_t;
void *operator new(size_t);
namespace std {
template <class T> void swap(T&, T&);
template <class T> T* _Allocate(size_t, T*);
template <class T> void _Destroy(T*);
template <class I, class T> void fill(I, I, const T&);
struct Aux { int *p; };
template <class _Ty, class _Ax> class vector : public Aux
{
public:
    static void _Xlen();
    _Ty* grow(size_t n) { if (n > 100) _Xlen(); return _Allocate(n, (_Ty*)0); }
};
}
struct A {};
int use(unsigned *a, unsigned *b, std::Aux &x, std::Aux &y)
{
    std::fill(a, b, 7u);
    std::swap(x, y);
    std::vector<unsigned short, A> v;
    unsigned short *q = v.grow(4);
    std::_Destroy(q);
    return (int)q[0];
}
'''
fails = 0


def ok(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else ': ' + str(info)[:600]))
    fails += not cond


def main():
    st = collections.Counter()
    out = s4_stl.r_stl_helper_bodies('src/t.cpp', UNIT, st)
    ok('five helpers get bodies', st['STL1'] == 5 and out.startswith(UNIT), st)
    st2 = collections.Counter()
    ok('a second run adds nothing (defined now)', s4_stl.r_stl_helper_bodies('src/t.cpp', out, st2) == out and
       not st2['STL1'], st2)
    ok('no std copy: unchanged', s4_stl.r_stl_helper_bodies('src/u.cpp', 'int f() { return 1; }\n',
                                                            collections.Counter()) == 'int f() { return 1; }\n')
    cxx = shutil.which('aarch64-linux-gnu-g++')
    if not cxx:
        print('SKIP compile: aarch64-linux-gnu-g++ missing')
    else:
        with tempfile.TemporaryDirectory() as t:
            for name, text in (('before', UNIT), ('after', out)):
                src, obj = os.path.join(t, name + '.cpp'), os.path.join(t, name + '.o')
                open(src, 'w').write(text)
                r = subprocess.run([cxx, '-mabi=ilp32', '-std=gnu++17', '-O0', '-fpermissive', '-c', src, '-o', obj],
                                   capture_output=True, text=True)
                ok('%s compiles' % name, r.returncode == 0, r.stderr)
                und = subprocess.run(['aarch64-linux-gnu-nm', '-u', obj], capture_output=True, text=True).stdout
                hits = [l.split()[-1] for l in und.splitlines() if any(k in l for k in
                                                                      ('_Allocate', '_Destroy', '4fill', '4swap', '_Xlen'))]
                if name == 'before':
                    ok('before: the helpers are undefined (the trap stubs)', len(hits) == 5, hits)
                else:
                    ok('after: none undefined', not hits, hits)
    print('test_stl1: %d failed' % fails)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
