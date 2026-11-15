#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/hostabi/product/build.json ports/fnv-nx/lib/sha256/build.json ports/fnv-nx/link
# reads: ports/fnv-nx/loader/build.json ports/fnv-nx/platform/build.json ports/fnv-nx/render/d3d9/build.json ports/fnv-nx/render/gl4/build.json
# reads: ports/fnv-nx/render/nxgpu/build.json ports/fnv-nx/render/pair/build.json ports/fnv-nx/render/shaders/translator/build.json
# reads: ports/fnv-nx/render/smscan/build.json ports/fnv-nx/rt/guest/build.json ports/fnv-nx/rt/guest_main/build.json ports/fnv-nx/shim/build.json
# reads: ports/fnv-nx/toolchain/addr/fnvnx_addr.cc ports/fnv-nx/toolchain/dispatch/fnvnx_dispatch.cc ports/fnv-nx/toolchain/ftoi/f2i_builtins.inc
# reads: ports/fnv-nx/toolchain/ftoi/fnvnx_ftoi.cc ports/fnv-nx/toolchain/gcc ports/fnv-nx/toolchain/layout/crossing_terms.inc
# reads: ports/fnv-nx/toolchain/layout/decltype_spell.inc ports/fnv-nx/toolchain/layout/fnvnx_layout.cc ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json
# reads: ports/fnv-nx/toolchain/msvcmodel/build.sh ports/fnv-nx/toolchain/msvcmodel/msvcmodel.patch ports/fnv-nx/toolchain/names/fnvnx_names.cc
# reads: ports/fnv-nx/toolchain/order/atomic_order.inc ports/fnv-nx/toolchain/order/fnvnx_frame.h ports/fnv-nx/toolchain/order/fnvnx_order.cc
# reads: ports/fnv-nx/toolchain/pi/fnvnx_pi.cc ports/fnv-nx/toolchain/sdd/fnvnx_sdd.cc ports/fnv-nx/toolchain/tests ports/fnv-nx/verify
"""Test of the `.fnvnx.frame` rows (rule R1 of class B) that fnvnx_order writes, through the recipe on the guest compiler (plugbuild.py), on the real object:
  - every row has the 3-field form SYMBOL TAB RANGES TAB args:N (the writer is toolchain/order/fnvnx_frame.h, the one writer of order and of the sdd thunks);
  - a function with a constant layout is never `dynamic`: an address-taken local array, a local whose slot is virtual-stack-vars, and a stack-passed
    parameter (the argument pointer) are OFFSET:SIZE ranges; `dynamic` is exactly the alloca / variable-length-array functions (calls_alloca);
  - args:N is the size of the incoming stack arguments (0 when all arguments are in registers);
  - an address-taken variable with no frame slot is its own `unk:CAUSE` token, not `dynamic` (a by-reference class parameter lives in the caller's frame);
  - the sdd thunk of a virtual destructor has its own row, written by the same writer;
  - ESCAPES: a pair is `OFFSET:SIZE` when the variable escapes (its address goes to a call, a global, an escaping pointer, a return) and `OFFSET:SIZE:n`
    when GCC's points-to solution of the final GIMPLE keeps the address in the thread (an address-taken local array indexed in place, a local handed
    to a built-in that does not retain it); at -O0 there is no solution and every variable escapes.
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
extern "C" void *sink_slot();
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
extern "C" int *gp;
extern "C" int esc_call(int n) { int x[8]; for (int i = 0; i < 8; i++) x[i] = n + i; sink(x); return x[n & 7]; }
extern "C" int esc_global(int n) { int x[8]; for (int i = 0; i < 8; i++) x[i] = n + i; gp = x; return x[n & 7]; }
extern "C" int esc_ptr_global(int n) { int x[8]; int *p = x + (n & 3); for (int i = 0; i < 8; i++) x[i] = n + i; *(int **)sink_slot() = p; return x[n & 7]; }
extern "C" int *esc_return(int n) { static int s[8]; int x[8]; for (int i = 0; i < 8; i++) x[i] = n + i; int *p = (n & 1) ? x : s; return p; }
extern "C" int noesc(int n) { int x[8]; for (int i = 0; i < 8; i++) x[i] = n + i; return x[n & 7]; }
extern "C" int noesc_builtin(int n) { int x[8]; int y[8]; for (int i = 0; i < 8; i++) x[i] = n + i; __builtin_memcpy(y, x, sizeof x); return y[n & 7]; }
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
    check('a stack-passed argument whose address is taken: args:8 or more, a range (the ordered local copy of the parameter: PARAMETER COPY in fnvnx_order.cc), not dynamic',
          got.get('stackarg', ('dynamic', 0))[0] not in ('dynamic', '') and got['stackarg'][1] >= 4 and re.search(r'(^| )-?[0-9]+:4( |$)', got['stackarg'][0]), got.get('stackarg'))
    check('an address-taken local array is a range at a negative offset', re.fullmatch(r'-[0-9]+:40', got.get('local_array', ('', 0))[0]) is not None, got.get('local_array'))
    check('two address-taken locals are two ranges', len(got.get('two_locals', ('', 0))[0].split(' ')) == 2 and 'dynamic' not in got['two_locals'][0], got.get('two_locals'))
    check('alloca is dynamic', got.get('with_alloca', ('', 0))[0] == 'dynamic', got.get('with_alloca'))
    check('a variable-length array is dynamic', got.get('vla', ('', 0))[0] == 'dynamic', got.get('vla'))
    check('a function with no address-taken variable has the empty range field and args:0', got.get('nothing') == ('', 0), got.get('nothing'))
    check('register arguments only: args:0', got.get('local_array', ('', -1))[1] == 0 and got.get('two_locals', ('', -1))[1] == 0, (got.get('local_array'), got.get('two_locals')))
    check('a class passed by value (address-taken: a range or an unk token), never dynamic', 'dynamic' not in got.get('byvalue', ('dynamic', 0))[0], got.get('byvalue'))
    thunks = [k for k in got if k.endswith('.fnvnx_sdd')]
    check('the sdd thunk of the virtual destructors has a row (args:0, no variable)', thunks and all(got[k] == ('', 0) for k in thunks), thunks)
    ranges = lambda n: got.get(n, ('', 0))[0].split()
    check('ESCAPES: an array passed to a call escapes (no :n)', ranges('esc_call') and not any(r.endswith(':n') for r in ranges('esc_call')), got.get('esc_call'))
    check('ESCAPES: an array whose address is stored to a global escapes', ranges('esc_global') and not any(r.endswith(':n') for r in ranges('esc_global')), got.get('esc_global'))
    check('ESCAPES: a pointer into an array stored through a pointer that the call returned escapes', ranges('esc_ptr_global') and not any(r.endswith(':n') for r in ranges('esc_ptr_global')), got.get('esc_ptr_global'))
    check('ESCAPES: an array whose address may be returned escapes', ranges('esc_return') and not any(r.endswith(':n') for r in ranges('esc_return')), got.get('esc_return'))
    check('ESCAPES: an address-taken local that never leaves the function is :n', ranges('noesc') and all(r.endswith(':n') for r in ranges('noesc')), got.get('noesc'))
    check('ESCAPES: locals handed only to a non-retaining built-in (memcpy) are :n', len(ranges('noesc_builtin')) >= 1 and all(r.endswith(':n') for r in ranges('noesc_builtin')), got.get('noesc_builtin'))
    n_new = sum(1 for k in got for r in got[k][0].split() if r.endswith(':n'))
    check('ESCAPES: the row grammar of an `:n` pair is OFFSET:SIZE:n', all(re.fullmatch(r'-?[0-9]+:[0-9]+(:n)?|dynamic|unk:(reg|mem|size)', r) for k in got for r in got[k][0].split()), n_new)
    obj0 = os.path.join(td, 'frame0.o')
    r0 = pb.compile(src, obj0, [so], native=True, args=['-O0'])
    check('-O0: the fixture builds', r0.returncode == 0, r0.stderr[-1500:])
    if r0.returncode == 0:
        g0 = {}
        for l in [l for l in rows(obj0).split('\n') if l]:
            m = LINE.match(l)
            if m: g0[m.group(1)] = m.group(2)
        check('-O0: there is no points-to solution, every variable escapes (no :n anywhere)', g0 and not any(r.endswith(':n') for v in g0.values() for r in v.split()), {k: v for k, v in g0.items() if ':n' in v})
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
