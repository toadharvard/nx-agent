#!/usr/bin/env python3
"""Test of the ODR definition record .fnvnx.odrdef of toolchain/names/fnvnx_names.cc ([basic.def.odr]), built from the tracked source with the
argv of the recipe's plugin stage (plugbuild.py). One row per function emitted as COMDAT: symbol, sha256 of the definition's token sequence,
template arguments, sorted bindings read from the body before genericize. Cases: the same inline function in two units with different -O gives
equal rows; a different token sequence gives different rows; the same tokens with a different overload visible give different rows; every COMDAT
function has a row; a template instantiated in two units whose dependent call resolves to a different declaration gives different rows.
No network, no game data.  Run: python3 -I <this file> from the checkout root."""
import os
import shutil
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
        if not all((a, b, c, d, ta, tb, pk)):
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
        rpk = rows(pk)
        targs = sorted(r[2] for r in rpk.values())
        check('argument packs: pk<int, double> is IidE, pn<1, 2, 3> is ILi1ELi2ELi3EE, pn<> is IE', targs == sorted(['ILi1ELi2ELi3EE', 'IE', 'IidE']), targs)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    return check.done()


if __name__ == '__main__':
    sys.exit(main())
