#!/usr/bin/env python3
"""odr.py: per-unit vtable / typeinfo names for a stand-in class several units define differently.

    python3 tools/link/test_odr.py     # needs aarch64-linux-gnu-g++ / objcopy / nm

* a.cpp and b.cpp each define their own global `struct SpawnedSub` (EQ's 00e98f20 shape): different bases, different
  inline destructors. Their _ZTV / _ZTI / _ZTS are renamed per unit; the destructors keep their names;
* `struct Same` is identical in both units: never renamed;
* `struct Real` differs too, but the exe has an RTTI class Real: left to VTFILL, never renamed;
* each object's own vptr store reaches its own renamed vtable; FNVNX_ODR=0 renames nothing.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import odr  # noqa: E402

CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
NM = os.environ.get('NM', 'aarch64-linux-gnu-nm')
FAILS = []

A = r'''
struct BaseA { virtual void fa(); };
struct SpawnedSub : BaseA { int x; virtual ~SpawnedSub() { x = 1; } };
struct Same { virtual ~Same() {} int k; };
struct Real { virtual ~Real() { r = 1; } int r; };
Same *mk_same_a() { return new Same; }
Real *mk_real_a() { return new Real; }
void *mk_a() { return new SpawnedSub; }
'''
B = r'''
struct SpawnedSub { virtual ~SpawnedSub() {} virtual int g() { return 2; } int y; };
struct Same { virtual ~Same() {} int k; };
struct Real { virtual ~Real() { r = 2; } int r; };
Same *mk_same_b() { return new Same; }
Real *mk_real_b() { return new Real; }
void *mk_b() { return new SpawnedSub; }
'''


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def syms(p):
    out = subprocess.run([NM, p], capture_output=True, text=True).stdout
    return {l.split()[-1]: l.split()[-2] for l in out.splitlines() if l.split()}


def main():
    if not shutil.which(CXX):
        print('skip: no %s' % CXX)
        return 0
    t = tempfile.mkdtemp(prefix='odr-')
    have = []
    for name, src in (('a', A), ('b', B)):
        cp = os.path.join(t, name + '.cpp')
        open(cp, 'w').write(src)
        op = os.path.join(t, name + '.o')
        subprocess.run([CXX, '-mabi=ilp32', '-O1', '-c', cp, '-o', op], check=True)
        have.append(types.SimpleNamespace(source='src/%s.cpp' % name, obj=op))
    out = os.path.join(t, 'out')
    os.makedirs(os.path.join(out, 'cache'))
    os.environ['FNVNX_ODR'] = '0'
    try:
        st0 = odr.rename([types.SimpleNamespace(**vars(u)) for u in have], out, {'Real'})
    finally:
        del os.environ['FNVNX_ODR']
    check(not st0, 'FNVNX_ODR=0: nothing renamed')
    st = odr.rename(have, out, {'Real'})
    check(st['classes'] == 1 and st['objects'] == 2 and st['symbols'] == 6,
          'one colliding class (SpawnedSub), both objects, _ZTV/_ZTI/_ZTS each: %s' % dict(st))
    for u in have:
        s = syms(u.obj)
        sx = odr.suffix(u.source)
        check(u.obj.startswith(os.path.join(out, 'odr')), '%s: the unit reads the renamed copy' % u.source)
        check(all(('%s%s' % (n, sx)) in s for n in ('_ZTV10SpawnedSub', '_ZTI10SpawnedSub', '_ZTS10SpawnedSub'))
              and '_ZTV10SpawnedSub' not in s,
              '%s: SpawnedSub vtable / typeinfo renamed %s' % (u.source, sx))
        check('_ZN10SpawnedSubD2Ev' in s or '_ZN10SpawnedSubD1Ev' in s, '%s: the destructors keep their names' % u.source)
        check('_ZTV4Same' in s and '_ZTV4Real' in s, '%s: Same (identical) and Real (exe class) keep theirs' % u.source)
    rel = subprocess.run(['aarch64-linux-gnu-readelf', '-rW', have[0].obj], capture_output=True, text=True).stdout
    check('_ZTV10SpawnedSub' + odr.suffix(have[0].source) in rel, 'a.o\'s vptr store relocates to its own vtable')
    rows = [l for l in open(os.path.join(out, 'odr.tsv')) if not l.startswith('#')]
    check(len(rows) == 1 and rows[0].startswith('SpawnedSub\tsrc/a.cpp src/b.cpp\t'), 'odr.tsv: %s' % rows)
    check(odr.class_of('_ZN9NiPointerI3FooED1Ev') is None and odr.class_of('_ZN3Foo5Inner3getEv') is None and
          odr.class_of('_ZNK3Foo3getEv') == 'Foo', 'class_of: plain global classes only')
    shutil.rmtree(t, ignore_errors=True)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
