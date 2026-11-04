#!/usr/bin/env python3
"""Self-tests of the link-by-address matcher on hand-written names (no decomp content).

    python3 tools/link/test_link.py        # needs llvm-undname(-18), c++filt, aarch64-linux-gnu-as
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi      # noqa: E402
import fnvlink  # noqa: E402
import layout   # noqa: E402
import sigs     # noqa: E402

# (MSVC decorated, Itanium mangled, expected tier)
PAIRS = [
    ('?Get@Foo@@QAEHH@Z', '_ZN3Foo3GetEi', 'exact'),                       # int Foo::Get(int)
    ('?Size@Foo@@QBEHXZ', '_ZNK3Foo4SizeEv', 'exact'),                     # int Foo::Size() const
    ('?Set@Foo@@QAEXI@Z', '_ZN3Foo3SetEm', 'l1'),                          # unsigned int (MSVC size_t) vs unsigned long
    ('??0Foo@@QAE@XZ', '_ZN3FooC1Ev', 'exact'),                            # Foo::Foo()
    ('??1Foo@@UAE@XZ', '_ZN3FooD2Ev', 'exact'),                            # Foo::~Foo()
    ('??4Foo@@QAEAAV0@ABV0@@Z', '_ZN3FooaSERKS_', 'exact'),               # Foo::operator=(Foo const&)
    ('?Add@?$Arr@PAVFoo@@@@QAEXPAVFoo@@@Z', '_ZN3ArrIP3FooE3AddES1_', 'exact'),  # Arr<Foo*>::Add(Foo*)
    ('?f@ns@@YAXPBDPAX@Z', '_ZN2ns1fEPKcPv', 'exact'),                    # ns::f(char const*, void*)
    ('?cb@@YAXP6AXH@Z@Z', '_Z2cbPFviE', 'exact'),                         # cb(void (*)(int))
    ('?big@@YAX_J@Z', '_Z3bigx', 'exact'),                                # big(long long)
    ('?arr@@YAXQAH@Z', '_Z3arrPi', 'exact'),                              # arr(int* const) -> arr(int*)
    ('?g_count@@3HA', 'g_count', 'cname'),                                 # int g_count
    ('?m_n@Foo@@2HA', '_ZN3Foo3m_nE', 'name'),                             # static int Foo::m_n
    ('_memset', 'memset', 'cname'),
    ('@Fast@8', 'Fast', 'cname'),
    ('_Std@12', 'Std', 'cname'),
    ('?g_fp@@3P6AXXZA', 'g_fp', 'cname'),                                   # void (__cdecl *g_fp)(void)
    ('?s_fp@ns@@3P6GHH@ZA', '_ZN2ns4s_fpE', 'name'),                         # int (__stdcall *ns::s_fp)(int)
]

ok = True


def check(cond, what):
    global ok
    if not cond:
        ok = False
        print('FAIL', what)


def main():
    ms = fnvlink.batch_demangle(fnvlink.UNDNAME, [m for m, _, _ in PAIRS if m.startswith('?')])
    it = fnvlink.batch_demangle(fnvlink.CXXFILT, [i for _, i, _ in PAIRS if i.startswith('_Z')])
    for m, i, tier in PAIRS:
        idx = fnvlink.KeyIndex()
        idx.add(sigs.parse_msvc(m, ms.get(m)), 0x401000)
        got, hit = idx.lookup(sigs.parse_itanium(i, it.get(i)))
        check(got == tier and list(hit) == [0x401000], '%s ~ %s: tier %s (want %s)' % (m, i, got, tier))
    check(sigs.itanium_variant('_ZN3FooC2Ev') == 'C2', 'ctor variant')
    check(sigs.itanium_variant('_ZN3C1E3fooEv') is None, 'no variant in a source name')

    # ABI: definition __fastcall f(Foo*, int edx_dummy, int), reference __thiscall Foo::f(int)
    names = ['?f@@YIHPAVFoo@@HH@Z', '?f@Foo@@QAEHH@Z', '?g@@YAXHM@Z', '?g@@YAXHH@Z', '?h@Foo@@QAEHXZ',
             '?h@@YIHPAVFoo@@@Z', '?k@@YAXPAVFoo@@@Z', '?s@Foo@@QAE?AUVec@@M@Z', '?s@Foo@@QAEPAUVec@@PAU2@M@Z']
    d = fnvlink.batch_demangle(fnvlink.UNDNAME, names)
    S = {n: sigs.parse_msvc(n, d[n]) for n in names}
    v = abi.compare(S['?f@Foo@@QAEHH@Z'], S['?f@@YIHPAVFoo@@HH@Z'])
    check(v.kind == 'bridge' and v.moves == [(('x', 2), ('x', 1), 'I32', 'I32')] and 'edx-dummy' in v.classes,
          'thiscall ref -> fastcall(T*, edx, X) def: %s %s %s' % (v.kind, v.moves, v.classes))
    v = abi.compare(S['?f@@YIHPAVFoo@@HH@Z'], S['?f@Foo@@QAEHH@Z'])
    check(v.kind == 'bridge' and v.moves == [(('x', 1), ('x', 2), 'I32', 'I32')],
          'fastcall(T*, edx, X) ref -> thiscall def: %s %s' % (v.kind, v.moves))
    v = abi.compare(S['?h@@YIHPAVFoo@@@Z'], S['?h@Foo@@QAEHXZ'])
    check(v.kind == 'same', 'fastcall(T*) == thiscall T::h(): %s' % v.kind)
    v = abi.compare(S['?g@@YAXHM@Z'], S['?g@@YAXHH@Z'])
    check(v.kind == 'bridge' and v.moves == [(('x', 1), ('v', 0), 'I32', 'F32')], 'float -> int: %s' % v.moves)
    v = abi.compare(S['?k@@YAXPAVFoo@@@Z'], S['?h@Foo@@QAEHXZ'])
    check(v.kind == 'x86-inconsistent', 'cdecl k(Foo*) vs thiscall: %s' % v.kind)
    # S13: stdcall f(void*) referenced, fastcall f(p0 /*ecx*/, edx dummy, p2) defined - the x86 callee reads the
    # caller's ecx as p0: x86-inconsistent (S1b), not a bridge leaving x0 = the reference's argument
    e2 = ['?e@@YG_NPAX@Z', '?e@@YI_NPAUB@@HPAUO@@@Z', '?e@M@@QAE_NPAU1@@Z']
    de = fnvlink.batch_demangle(fnvlink.UNDNAME, e2)
    SE = {n: sigs.parse_msvc(n, de[n]) for n in e2}
    v = abi.compare(SE['?e@@YG_NPAX@Z'], SE['?e@@YI_NPAUB@@HPAUO@@@Z'])
    check(v.kind == 'x86-inconsistent' and 'ecx-not-passed' in v.classes,
          'stdcall f(void*) -> fastcall(ecx, edx, X): %s %s' % (v.kind, v.classes))
    v = abi.compare(SE['?e@M@@QAE_NPAU1@@Z'], SE['?e@@YI_NPAUB@@HPAUO@@@Z'])
    check(v.kind == 'bridge' and 'edx-dummy' in v.classes, 'thiscall M::f(M*) -> the same: %s %s'
          % (v.kind, v.classes))

    v = abi.compare(S['?s@Foo@@QAE?AUVec@@M@Z'], S['?s@Foo@@QAEPAUVec@@PAU2@M@Z'])
    check(v.kind == 'manual' and 'sret-explicit' in v.classes, 'hidden vs explicit return buffer: %s %s'
          % (v.kind, v.classes))

    # narrow arguments the definition reads as int/pointer: extended in place by the reference's signedness
    nn = ['?n1@@YAXD@Z', '?n1@@YAXH@Z', '?n2@@YAX_N@Z', '?n3@@YAXG@Z', '?n4@@YAXF@Z', '?n5@@YAXPAX@Z']
    dn = fnvlink.batch_demangle(fnvlink.UNDNAME, nn)
    N = {n: sigs.parse_msvc(n, dn[n]) for n in nn}
    ext = {}
    for ref, want in (('?n1@@YAXD@Z', 'sxtb w0, w0'), ('?n2@@YAX_N@Z', 'uxtb w0, w0'), ('?n3@@YAXG@Z', 'uxth w0, w0'),
                      ('?n4@@YAXF@Z', 'sxth w0, w0')):
        v = abi.compare(N[ref], N['?n1@@YAXH@Z'])
        code = abi.emit_bridge('nb_' + ref[1:3] + str(len(ext)), 'tgt', v, 'narrow') if v.kind == 'bridge' else ''
        check(v.kind == 'bridge' and 'narrow-extend' in v.classes and want in code, '%s -> int: %s %s'
              % (ref, v.kind, code))
        ext[ref] = code
    v = abi.compare(N['?n2@@YAX_N@Z'], N['?n5@@YAXPAX@Z'])
    check(v.kind == 'bridge' and v.moves[0][3] == 'uxtb', 'bool -> pointer: %s %s' % (v.kind, v.moves))
    check(abi.compare(N['?n1@@YAXH@Z'], N['?n1@@YAXD@Z']).kind == 'same', 'int -> char needs nothing')

    # bridges assemble
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, 'b.S')
        vb = abi.compare(S['?f@Foo@@QAEHH@Z'], S['?f@@YIHPAVFoo@@HH@Z'])
        vc = abi.Verdict(); vc.moves = [(('x', 1), ('x', 2), 'I32', 'I32'), (('x', 2), ('x', 1), 'P', 'P'),
                                        (('v', 0), ('x', 3), 'F32', 'I32'), (('x', 3), ('stk', 0), 'I32', 'I32')]
        open(src, 'w').write('    .text\n' + abi.emit_bridge('b1', 't1', vb, 'test') +
                             abi.emit_bridge('b2', 't2', vc, 'cycle') + ''.join(ext.values()))
        r = subprocess.run([fnvlink.AS, '-mabi=ilp32', src, '-o', os.path.join(tmp, 'b.o')], capture_output=True,
                           text=True)
        check(r.returncode == 0, 'bridges assemble: ' + r.stderr)
        seq = abi.sequentialize(vc.moves)
        check(any(m[0] == ('x', 16) for m in seq), 'swap goes through x16')
    # layout probe on a hand-written unit
    unit = '''struct A { int a; };
struct F3 { float x, y, z; F3(); };
struct Big { int a[8]; };
struct NT { int a; ~NT(); };
struct V { double a, b; };
namespace ns { struct P { void *p; short s; long long q; }; }
struct E {};
template <class T> struct Ptr { T *p; };
struct M { float a[4]; };
struct D4 { double a, b, c, d; };
'''
    with tempfile.TemporaryDirectory() as tree:
        os.makedirs(os.path.join(tree, 'src')); os.makedirs(os.path.join(tree, 'include'))
        open(os.path.join(tree, 'src', 'u.cpp'), 'w').write(unit)
        got = layout.probe_unit(tree, tree, 'src/u.cpp', ['A', 'F3', 'Big', 'NT', 'V', 'ns::P', 'E', 'Ptr<A>',
                                                          'Nope', 'M', 'D4'])
        want = {'A': (4, 'G1'), 'F3': (12, 'H3x4'), 'Big': (32, 'IND'), 'NT': (4, 'IND'), 'V': (16, 'H2x8'),
                'ns::P': (16, 'G2'), 'E': (1, 'G1'), 'Ptr<A>': (4, 'G1'), 'M': (16, 'H4x4'), 'D4': (32, 'H4x8')}
        for t, (size, cls) in want.items():
            g = got.get(t)
            check(g is not None and (g.size, g.cls) == (size, cls), 'layout %s: %s (want %s %s)' % (t, g, size, cls))
        check(got.get('Nope') is None, 'unnameable type dropped')

    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
