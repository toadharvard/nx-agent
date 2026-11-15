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
"""Test of the ODR definition record .fnvnx.odrdef of toolchain/names/fnvnx_names.cc ([basic.def.odr]), built from the tracked source with the
argv of the recipe's plugin stage (plugbuild.py). One row per function emitted as COMDAT: symbol, sha256 of the definition's token sequence,
template arguments, sorted bindings read from the body before genericize. Cases: the same inline function in two units with different -O gives
equal rows; a different token sequence gives different rows; the same tokens with a different overload visible give different rows; every COMDAT
function has a row; a template instantiated in two units whose dependent call resolves to a different declaration gives different rows.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import plugbuild as pb  # noqa: E402

check = pb.Checks('test_odrdef')

PRE = 'void g(long);\n'
INL = 'inline void f(int x) { g(x); }\n'
USE = 'void use(int x) { f(x); }\nvoid (*fp)(int) = f;\n'   # the address is taken: f is emitted at every -O
TMPL = 'template <class T> int t(T x) { return g(x); }\n'


def build_unit(td, so, name, text, flags):
    src = os.path.join(td, name + '.cpp')
    open(src, 'w').write(text)
    obj = os.path.join(td, name + '.o')
    r = pb.compile(src, obj, so, flags=flags)
    check('%s compiles' % name, r.returncode == 0, r.stderr[:2000])
    return obj if r.returncode == 0 else None


def rows(obj):
    out = {}
    for l in pb.section(obj, '.fnvnx.odrdef').splitlines():
        f = l.split('\t')
        check('row has four fields: %r' % l, len(f) == 4, l)
        out[f[0]] = f
    return out


def raw_unit(td, so):
    """the unit of the farm's libstdc++ failure (a header from a -I directory), compiled raw by the pinned driver: refused naming the cause; with -save-temps: builds"""
    drv = pb.driver()
    inc = os.path.join(td, 'rawinc')
    os.makedirs(inc)
    open(os.path.join(inc, 'rh.h'), 'w').write('inline int rcmp(int a) { return a < 3; }\n')
    src = os.path.join(td, 'raw.cc')
    open(src, 'w').write('#include <rh.h>\nint rf(int x) { return rcmp(x); }\nint (*rp)(int) = rcmp;\n')
    base = [drv, '-c', '-O2', '-I' + inc] + sum((['-fplugin=' + p] for p in so), []) + [src]
    r = subprocess.run(base + ['-o', os.path.join(td, 'raw.o')], capture_output=True, text=True, cwd=td)
    check('a raw unit is refused', r.returncode != 0, r.stderr[:600])
    check('the refusal names the cause (not preprocessed), not "could not be lexed again"', 'not preprocessed' in r.stderr and 'lexed again' not in r.stderr, r.stderr[:600])
    r = subprocess.run(base + ['-save-temps=obj', '-o', os.path.join(td, 'raw2.o')], capture_output=True, text=True, cwd=td)
    check('the same unit under -save-temps builds', r.returncode == 0, r.stderr[:600])
    row = [l for l in pb.section(os.path.join(td, 'raw2.o'), '.fnvnx.odrdef').splitlines() if l.startswith('_Z4rcmpi')]
    check('the -save-temps object has the definition row of the header function', len(row) == 1, row)


def main():
    td = tempfile.mkdtemp(prefix='test-odrdef-')
    try:
        so = [pb.build(n, td) for n in ('sdd', 'names')]
        O2, O0 = pb.PRODUCT_FLAGS, pb.PRODUCT_FLAGS + ['-O0']
        a = build_unit(td, so, 'a', PRE + INL + USE, O2)
        b = build_unit(td, so, 'b', PRE + INL + USE, O0)
        c = build_unit(td, so, 'c', PRE + 'inline void f(int x) { g(x + 1); }\n' + USE, O2)
        d = build_unit(td, so, 'd', PRE + 'void g(int);\n' + INL + USE, O2)
        S = 'struct S {};\n'
        ta = build_unit(td, so, 'ta', S + 'int g(S);\n' + TMPL + 'int u(S s) { return t(s); }\nint (*tp)(S) = t<S>;\n', O2)
        tb = build_unit(td, so, 'tb', S + 'int g(const S &);\n' + TMPL + 'int u(S s) { return t(s); }\nint (*tp)(S) = t<S>;\n', O2)
        PK = ('template <class... A> int pk(A... a) { return sizeof...(A); }\ntemplate <int... N> int pn() { return sizeof...(N); }\n'
              'int (*p1)(int, double) = pk<int, double>;\nint (*p2)() = pn<1, 2, 3>;\nint (*p4)() = pn<>;\n')
        pk = build_unit(td, so, 'pk', PK, O2)   # an argument pack is a template argument that no mangling accepts: it is the I...E of its elements
        # a variably-modified type (a local array with a run-time bound: a SAVE_EXPR in its domain) has no mangled name; the farm's libstdc++ failed on `mangling save_expr`
        # in the instance std::__floating_to_chars_precision<double>. A template with such a local array, a pointer to it, decltype of an expression, a static local array and
        # __builtin_unreachable at the end, instantiated for two types: the unit builds and each instance has its row
        VM = ('template <class T> int vf(int n, T v)\n{\n  T buf[n];\n  T (*pbuf)[n] = &buf;\n  decltype(v + v) w = v;\n  static const T tab[sizeof(T) * 2 + 1] = {};\n'
              '  for (int i = 0; i < n; i++) buf[i] = tab[i % 2] + w;\n  if (n > 100) return (*pbuf)[0] == v;\n  if (n > 50) __builtin_unreachable();\n  return 1;\n}\n'
              'int (*pv1)(int, double) = vf<double>;\nint (*pv2)(int, int) = vf<int>;\n')
        vm = build_unit(td, so, 'vm', VM, O2)
        # default arguments are part of the call, so of the bindings: the same call with a different default expression is a different row
        DA = 'int k1(); int k2();\nint h(int = {k});\n'
        INLH = 'inline int fh() { return h(); }\nint (*ph)() = fh;\n'
        da = build_unit(td, so, 'da', DA.format(k='k1()') + INLH, O2)
        db = build_unit(td, so, 'db', DA.format(k='k2()') + INLH, O2)
        ca = build_unit(td, so, 'ca', 'struct W { int m; W(int v) : m(v) {} int get() const { return m; } };\nint (W::*pg)() const = &W::get;\nW mk(int v) { return W(v); }\n', O0)
        # the three destructors (D0 deleting, D1 complete, D2 base) of a class whose destructor the compiler defines
        DT = ('namespace dsh { struct BF { virtual ~BF() {} virtual void f() = 0; };\n struct Stage : BF { void f() {} };\n struct AbsStage : BF { using BF::BF; virtual int in(int) = 0; }; struct Conc : AbsStage { Conc() : AbsStage() {} int in(int x) { return x; } void f() {} };\n'
              ' struct Def : BF { ~Def() = default; void f() {} };\n struct Over : BF { ~Over() override; void f() {} }; inline Over::~Over() {} }\n'
              'dsh::BF *mk0() { return new dsh::Conc; }\ndsh::BF *mk1() { return new dsh::Stage; }\ndsh::BF *mk2() { return new dsh::Def; }\ndsh::BF *mk3() { return new dsh::Over; }\n'
              'void del(dsh::BF *p) { delete p; }\n')
        dt = build_unit(td, so, 'dt', DT, O0)
        # ODRDEF-PP: the farm's runtime build compiles raw sources with the driver. A raw unit (include chain and macros only parse_in holds) is refused with its
        # cause; the same unit through the driver's two steps (-save-temps) builds, and a unit with a -I header through the recipe's PP and CC stages builds.
        raw_unit(td, so)
        if not all((a, b, c, d, ta, tb, pk, vm, da, db, ca, dt)):
            return check.done()
        ra, rb, rc, rd, rta, rtb = map(rows, (a, b, c, d, ta, tb))
        F = '_Z1fi'
        check('every COMDAT function has a row', F in ra and '_Z1tI1SEiT_' in rta, (sorted(ra), sorted(rta)))
        check('the same inline function in two units with different -O: equal rows', F in rb and ra[F] == rb[F], (ra.get(F), rb.get(F)))
        check('a function that is not COMDAT has no row', '_Z3usei' not in ra, sorted(ra))
        check('a different token sequence: different rows (hash)', ra[F][1] != rc[F][1], (ra[F], rc[F]))
        check('the same tokens, a different overload visible: same hash, different bindings', ra[F][1] == rd[F][1] and ra[F][3] != rd[F][3], (ra[F], rd[F]))
        T = '_Z1tI1SEiT_'
        check('an instantiation: the template argument is mangled', rta[T][2] == '1S', rta[T])
        check('an instantiation: the template definition\'s hash is equal in both units', rta[T][1] == rtb[T][1], (rta[T], rtb[T]))
        check('a dependent call resolved to a different declaration at instantiation: different rows', rta[T][3] != rtb[T][3], (rta[T], rtb[T]))
        rvm = [k for k in rows(vm) if k.startswith('_Z2vf')]
        check('a variably-modified local array: both instances of the template have a definition row', len(rvm) == 2, rvm)
        rpk = rows(pk)
        targs = sorted(r[2] for r in rpk.values())
        check('argument packs: pk<int, double> is IidE, pn<1, 2, 3> is ILi1ELi2ELi3EE, pn<> is IE', targs == sorted(['ILi1ELi2ELi3EE', 'IE', 'IidE']), targs)
        rda, rdb, rca = rows(da), rows(db), rows(ca)
        check('a default argument: same tokens, the callee of the default expression is a binding', rda['_Z2fhv'][1] == rdb['_Z2fhv'][1]
              and '_Z2k1v' in rda['_Z2fhv'][3] and '_Z2k2v' in rdb['_Z2fhv'][3] and rda['_Z2fhv'][3] != rdb['_Z2fhv'][3], (rda['_Z2fhv'], rdb['_Z2fhv']))
        EMPTY = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
        check('a written member has tokens (a ctor with an initializer, a const member); no row has an empty-token hash unless the compiler defined it',
              rca['_ZN1WC1Ei'][1] != EMPTY and rca['_ZNK1W3getEv'][1] != EMPTY and rca['_ZN1WC1Ei'][1] != rca['_ZNK1W3getEv'][1], rca)
        rdt = rows(dt)
        want = {'_ZN3dsh5StageD%dEv' % k for k in (0, 1, 2)} | {'_ZN3dsh8AbsStageD%dEv' % k for k in (0, 1, 2)} | {'_ZN3dsh3DefD%dEv' % k for k in (0, 1, 2)} | {'_ZN3dsh4OverD%dEv' % k for k in (0, 1, 2)}
        have = {k for k in rdt if k in want}
        check('destructors D0/D1/D2 of an implicit, a defaulted and an out-of-line class: a row each, the compiler-defined ones with the empty token sequence',
              have >= {'_ZN3dsh5StageD1Ev', '_ZN3dsh8AbsStageD0Ev', '_ZN3dsh3DefD1Ev', '_ZN3dsh4OverD1Ev'} and all(rdt[k][1] == EMPTY for k in have if 'Over' not in k), (sorted(rdt), have))
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
