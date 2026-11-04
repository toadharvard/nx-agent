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
import abi2     # noqa: E402
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
    check(v.kind == 'x86-inconsistent' and 'ecx-not-passed' in v.classes and v.moves == [(('x', 2), ('x', 0), 'P', 'P')],
          'stdcall f(void*) -> fastcall(ecx, edx, X): %s %s %s (the passed argument still moves)' % (v.kind, v.classes,
                                                                                                   v.moves))
    # V1 defect 1 (adversarial_abi B1/B2): __fastcall with a hidden result - MSVC passes the buffer as the first stack
    # word and keeps ecx/edx for the arguments, so the reference's explicit third argument is the buffer
    fb = ['?g@@YIXHHPAUP12@@@Z', '?g@@YI?AUP12@@HH@Z']
    db = fnvlink.batch_demangle(fnvlink.UNDNAME, fb)
    rb, dfb = sigs.parse_msvc(fb[0], db[fb[0]]), sigs.parse_msvc(fb[1], db[fb[1]])
    p12 = lambda t: layout.Layout(12, 4, 1, 0, 'G2') if t == 'P12' else None   # noqa: E731
    pb = abi2.compare(rb, dfb, p12, p12)
    check(pb is not None and pb.kind == 'bridge' and pb.def_sret_from == 'p2',
          'fastcall hidden result <- the reference\'s third argument: %s' % ((pb and (pb.kind, pb.def_sret_from)),))
    pb2 = abi2.compare(dfb, rb, p12, p12)
    check(pb2 is not None and pb2.kind == 'bridge' and any(src == ('refsret',) for ps in pb2.pieces.values()
                                                           for _, src, _, _ in ps),
          'explicit buffer <- the reference\'s hidden one (first stack word): %s' % (pb2 and pb2.kind))
    # V1 defect 2 (adversarial_abi C1/C2): a float result read as a double and back - one register file, but the
    # value has to be converted (x86 hands it over exactly in st(0))
    fw = ['?h@@YANXZ', '?h@@YAMXZ']
    dw = fnvlink.batch_demangle(fnvlink.UNDNAME, fw)
    rd, rf = sigs.parse_msvc(fw[0], dw[fw[0]]), sigs.parse_msvc(fw[1], dw[fw[1]])
    for ref_, def_, ins in ((rd, rf, 'fcvt d0, s0'), (rf, rd, 'fcvt s0, d0')):
        v_ = abi.compare(ref_, def_)
        asm_ = abi.emit_bridge('br', 'tgt', v_, 'c') if v_.kind == 'bridge' else ''
        p_ = abi2.compare(ref_, def_, lambda t: None, lambda t: None)
        asm2 = abi2.emit('br', 'tgt', p_, 'c') if p_ is not None and p_.kind == 'bridge' else ''
        check(v_.kind == 'bridge' and 'ret-float-width' in v_.classes and ins in asm_ and ins in (asm2 or ''),
              'float/double result converted (%s): %s %s' % (ins, v_.kind, v_.classes))
    # S13: GetOrLoadXML calls the fastcall-declared FUN_00a0c900 through a __thiscall cast; the definition is the
    # thiscall member (const char*, int): the cast decides (no bridge), the declaration would move the 3rd argument
    src = ('void __fastcall FUN_00a0c900(void*, const char*, MenuValueDescriptor*);\n'
           'struct FUN_00a0c900_shell { void __thiscall FUN_00a0c900(const char*, int); };\n'
           'MenuValueDescriptor* GetOrLoadXML(const char* apFilename, bool abAdd) {\n'
           '    if (abAdd) ((void(__thiscall*)(void*, const char*, MenuValueDescriptor*)) & FUN_00a0c900)(&map, f, x);\n'
           '    ((FUN_00a0c900_shell*)&map)->FUN_00a0c900(f, 1);\n    return x; }\n')
    uc = fnvlink.unit_casts(src)
    check(uc.get('FUN_00a0c900') == ({('thiscall', 'void', 'void*, const char*, MenuValueDescriptor*')}, 0),
          'cast found, the member call is no direct call: %s' % (uc.get('FUN_00a0c900'),))
    ns = ['?FUN_00a0c900@FUN_00a0c900_shell@@QAEXPBDH@Z', '?FUN_00a0c900@@YIXPAXPBDPAUMenuValueDescriptor@@@Z']
    dd = fnvlink.batch_demangle(fnvlink.UNDNAME, ns)
    dfn, ref = sigs.parse_msvc(ns[0], dd[ns[0]]), sigs.parse_msvc(ns[1], dd[ns[1]])
    check(abi.compare(ref, dfn).kind == 'bridge', 'the declaration alone would bridge')
    (cc, ret, params), = uc['FUN_00a0c900'][0]
    v = abi.compare(fnvlink.cast_sig(cc, ret, params, 'FUN_00a0c900'), dfn)
    check(v.kind == 'same', 'through the __thiscall cast the call matches the definition: %s %s' % (v.kind, v.moves))
    uc2 = fnvlink.unit_casts(src + 'void g() { FUN_00a0c900(0, \"s\", 0); }\n')
    check(uc2['FUN_00a0c900'][1] == 1, 'a direct call next to the cast is counted: %s' % (uc2['FUN_00a0c900'],))
    # S9: probe caches keyed on unit content - the unit and every local header it includes, not mtimes or paths
    with tempfile.TemporaryDirectory() as t:
        for d in ('a', 'b'):
            os.makedirs(os.path.join(t, d, 'src'))
            os.makedirs(os.path.join(t, d, 'include'))
            open(os.path.join(t, d, 'src', 'u.cpp'), 'w').write('#include "h.h"\nint f(S *s) { return s->x; }\n')
            open(os.path.join(t, d, 'include', 'h.h'), 'w').write('struct S { int x; };\n')
        ka, kb = layout.unit_key(os.path.join(t, 'a'), 'src/u.cpp'), layout.unit_key(os.path.join(t, 'b'), 'src/u.cpp')
        check(ka == kb, 'same content in another tree: same key')
        open(os.path.join(t, 'b', 'include', 'h.h'), 'w').write('struct S { int pad, x; };\n')
        layout._KEYS.clear(); layout._FILE_SHA.clear()
        check(layout.unit_key(os.path.join(t, 'b'), 'src/u.cpp') != ka, 'an included header changed: another key')
    # S5: data names. A pointer to a function pointer, and a type whose name ends in '_' (llvm-undname drops the
    # space: 'class Setting_x'): the name comes from the decoration
    dn2 = ['?g_apfnA@@3PAP6AXPAX@ZA', '?bKeep@@3VSettingT_Coll_@@A', '?v@fnv_00123456@@3HA']
    dd2 = fnvlink.batch_demangle(fnvlink.UNDNAME, dn2)
    for n_, q_ in zip(dn2, (['g_apfnA'], ['bKeep'], ['fnv_00123456', 'v'])):
        s_ = sigs.parse_msvc(n_, dd2[n_])
        check(s_.kind == 'data' and s_.qual == q_, 'data name %s -> %s (%s)' % (n_, s_.qual, dd2[n_]))
    # a data reference that matches only by identifier (another namespace, an array where MSVC saw a pointer)
    gi = fnvlink.KeyIndex()
    ident = {'v': {0x01000100}, 'w': {0x01000200, 0x01000300}}
    r_ = fnvlink.classify_ref('_ZN12fnv_00aaaaaa1vE', sigs.parse_itanium('_ZN12fnv_00aaaaaa1vE', 'fnv_00aaaaaa::v'),
                              fnvlink.KeyIndex(), gi, gi, set(), None, ident)
    check(r_[:3] == ('addr', 'gref/ident', 0x01000100), 'identifier-only data binding: %s' % (r_,))
    r_ = fnvlink.classify_ref('w', sigs.parse_itanium('w', None), fnvlink.KeyIndex(), gi, gi, set(), None, ident)
    check(r_[0] == 'ambiguous', 'identifier at two addresses is not guessed: %s' % (r_,))
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
