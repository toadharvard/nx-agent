#!/usr/bin/env python3
"""Test of the `.fnvnx.frame` rows (rule R1 of class B) that fnvnx_order writes, through the recipe on the guest compiler (plugbuild.py), on the real object:
  - every row has the 3-field form SYMBOL TAB RANGES TAB args:N (the writer is toolchain/order/fnvnx_frame.h, the one writer of order and of the sdd thunks);
  - a function with a constant layout is never `dynamic`: an address-taken local array, a local whose slot is virtual-stack-vars, and a stack-passed
    parameter (the argument pointer) are OFFSET:SIZE ranges; `dynamic` is exactly the alloca / variable-length-array functions;
  - args:N is the size of the incoming stack arguments (0 when all arguments are in registers);
  - an address-taken variable with no frame slot is its own `unk:CAUSE` token, not `dynamic` (a by-reference class parameter lives in the caller's frame);
  - the sdd thunk of a virtual destructor has its own row, written by the same writer.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_frame')
SRC = r'''
extern "C" void sink(void *);
extern "C" int use9(int, int, int, int, int, int, int, int, int *);
extern "C" int stackarg(int a, int b, int c, int d, int e, int f, int g, int h, int i) { sink(&i); return a + b + c + d + e + f + g + h; }
extern "C" int local_array(int n) { char buf[40]; sink(buf); return buf[n & 15]; }
extern "C" int two_locals(int n) { int x = n; long long y = n * 2; sink(&x); sink(&y); return x + (int)y; }
extern "C" int with_alloca(int n) { char *p = (char *)__builtin_alloca(n); sink(p); return p[0]; }
extern "C" int vla(int n) { int v[n]; sink(v); return v[n - 1]; }
extern "C" int nothing(int a) { return a + 1; }
struct S { int a[8]; };
extern "C" int byvalue(S s) { sink(&s); return s.a[0]; }
struct B { virtual ~B(); int x; };
B::~B() { sink(this); }
struct D : B { ~D(); int y; };
D::~D() { sink(&y); }
extern "C" B *mk() { return new D; }
'''
LINE = re.compile(r'^(\S+)\t([^\t]*)\targs:(0|[1-9][0-9]*)$')


def rows(obj):
    out = subprocess.run(['aarch64-linux-gnu-objcopy', '--dump-section', '.fnvnx.frame=/dev/stdout', obj, os.devnull], capture_output=True)
    text = out.stdout.decode()
    return text


def main():
    td = tempfile.mkdtemp(prefix='test-frame-')
    so = pb.build('order', td)
    src = os.path.join(td, 'frame.cpp')
    open(src, 'w').write(SRC)
    obj = os.path.join(td, 'frame.o')
    r = pb.compile(src, obj, [so], native=True)
    check('the fixture builds with the native plugin set', r.returncode == 0, r.stderr[-1500:])
    if r.returncode:
        return check.done()
    text = rows(obj)
    lines = [l for l in text.split('\n') if l]
    check('the object has a .fnvnx.frame section with rows', bool(lines), text[:200])
    got = {}
    bad = [l for l in lines if not LINE.match(l)]
    check('every row is SYMBOL TAB RANGES TAB args:N', not bad, bad[:3])
    for l in lines:
        m = LINE.match(l)
        if m:
            got[m.group(1)] = (m.group(2), int(m.group(3)))
    check('a stack-passed argument: the row has args:8 or more and a range at a positive offset (the argument pointer), not dynamic',
          got.get('stackarg', ('dynamic', 0))[0] not in ('dynamic', '') and got['stackarg'][1] >= 4 and re.search(r'(^| )[0-9]+:4( |$)', got['stackarg'][0]), got.get('stackarg'))
    check('an address-taken local array is a range at a negative offset', re.fullmatch(r'-[0-9]+:40', got.get('local_array', ('', 0))[0]) is not None, got.get('local_array'))
    check('two address-taken locals are two ranges', len(got.get('two_locals', ('', 0))[0].split(' ')) == 2 and 'dynamic' not in got['two_locals'][0], got.get('two_locals'))
    check('alloca is dynamic', got.get('with_alloca', ('', 0))[0] == 'dynamic', got.get('with_alloca'))
    check('a variable-length array is dynamic', got.get('vla', ('', 0))[0] == 'dynamic', got.get('vla'))
    check('a function with no address-taken variable has the empty range field and args:0', got.get('nothing') == ('', 0), got.get('nothing'))
    check('register arguments only: args:0', got.get('local_array', ('', -1))[1] == 0 and got.get('two_locals', ('', -1))[1] == 0, (got.get('local_array'), got.get('two_locals')))
    check('a class passed by value (address-taken: a range or an unk token), never dynamic', 'dynamic' not in got.get('byvalue', ('dynamic', 0))[0], got.get('byvalue'))
    thunks = [k for k in got if k.endswith('.fnvnx_sdd')]
    check('the sdd thunk of the virtual destructors has a row (args:0, no variable)', thunks and all(got[k] == ('', 0) for k in thunks), thunks)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
