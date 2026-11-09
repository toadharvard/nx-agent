#!/usr/bin/env python3
"""W48: a devirtualized direct call to a slot name that is a trap takes the calling class's slot binding.

    python3 tools/link/test_w48.py     # needs aarch64-linux-gnu-g++

* C's constructor calls B::g() (GCC devirtualizes `g()` in a constructor: a `bl _ZN1B1gEv`); the name is a trap in the
  object, and the fill bound it in C's vtable (a `#vtables` row): the object's symbol gets that target;
* a free function calling the trapped name decides nothing (no class): left alone;
* callers of two classes whose vtables disagree: left alone; FNVNX_W48=0: nothing.
"""
import collections
import os
import shutil
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fnvlink  # noqa: E402

CXX = os.environ.get('CXX', 'aarch64-linux-gnu-g++')
FAILS = []

SRC = r'''
struct B { virtual int g(); int y; };
struct C : B { C(); int x; };
C::C() { x = g(); }
struct D : B { D(); int z; };
D::D() { z = g(); }
'''
FREE = r'''
struct B { virtual int g(); int y; };
int call_free(B *b) { return b->B::g(); }
'''


class Unit(types.SimpleNamespace):
    __hash__ = object.__hash__


def check(cond, what):
    print(('ok   ' if cond else 'FAIL ') + what)
    if not cond:
        FAILS.append(what)


def build(t, name, src):
    cp = os.path.join(t, name + '.cpp')
    open(cp, 'w').write(src)
    op = os.path.join(t, name + '.o')
    subprocess.run([CXX, '-mabi=ilp32', '-O1', '-ffunction-sections', '-c', cp, '-o', op], check=True)
    return Unit(source='src/%s.cpp' % name, obj=op)


def run(u, rows, env=None):
    trap = '__fnvnx_vtrap_00000001'
    per_obj = {u.source: {'_ZN1B1gEv': trap}}
    changed, addr_of, rebound = {}, {}, set()
    old = dict(os.environ)
    os.environ.update(env or {})
    try:
        n = fnvlink.direct_call_slot_binds([u], changed, addr_of, per_obj, rebound, {trap: None}, rows)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return n, changed.get(u, {})


def row(vt, a):
    return ('zz_vt.o', 'src/a.cpp#vtables', '_ZN1B1gEv', '%08x' % a, '__fnv_%08x' % a, 'agree', 'same', vt + '+8:0')


def main():
    if not shutil.which(CXX):
        print('skip: no %s' % CXX)
        return 0
    t = tempfile.mkdtemp(prefix='w48-')
    a = build(t, 'a', SRC)
    f = build(t, 'f', FREE)
    check(fnvlink.caller_class('_ZN12fnv_00af7eb012FUN_00af7eb0C1EPKcibb') == '_ZTVN12fnv_00af7eb012FUN_00af7eb0E'
          and fnvlink.caller_class('_ZNK3Foo3getEv') == '_ZTV3Foo' and fnvlink.caller_class('_Z3fooi') is None
          and fnvlink.caller_class('_ZN9NiPointerI3FooED1Ev') is None, 'caller_class')
    n, ch = run(a, [row('_ZTV1C', 0x401000), row('_ZTV1D', 0x401000)])
    check(n == 1 and ch.get('_ZN1B1gEv') == '__fnv_00401000', 'ctor callers of C and D, both bound to 00401000: %s' % ch)
    n, ch = run(a, [row('_ZTV1C', 0x401000), row('_ZTV1D', 0x402000)])
    check(n == 0 and not ch, 'C and D disagree: left a trap')
    n, ch = run(a, [row('_ZTV1C', 0x401000)])
    check(n == 1 and ch.get('_ZN1B1gEv') == '__fnv_00401000', 'only C has a row: its target')
    n, ch = run(f, [row('_ZTV1B', 0x401000)])
    check(n == 0, 'a free caller decides nothing')
    n, ch = run(a, [row('_ZTV1C', 0x401000), row('_ZTV1D', 0x401000)], {'FNVNX_W48': '0'})
    check(n == 0, 'FNVNX_W48=0: nothing')
    shutil.rmtree(t, ignore_errors=True)
    print('%d failed' % len(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
