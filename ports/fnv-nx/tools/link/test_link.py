#!/usr/bin/env python3
"""Self-tests of the link-by-address matcher on hand-written names (no decomp content).

    python3 tools/link/test_link.py        # needs llvm-undname(-18), c++filt, aarch64-linux-gnu-as
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import abi      # noqa: E402
import abi2     # noqa: E402
import elfsyms  # noqa: E402
import fnvlink  # noqa: E402
import layout   # noqa: E402
import sigs     # noqa: E402
import vslots   # noqa: E402

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
    # V1 defect 3 (repro_cname_abi): a C-named definition (@f@16) is compared too - its parameter types from the
    # defining unit's source - and gets the edx-dummy bridge its C++-named twin gets
    src_c = ('#define __fastcall\nstruct T { int v; };\nextern "C" void __fastcall FUN_00c0ffee(T *self, void *edx_dummy, '
             'int a, int b) { self->v = a * 7 + b; }\n')
    cs = fnvlink.c_name_sig(sigs.parse_msvc('@FUN_00c0ffee@16', None), src_c)
    hn = ['?FUN_00c0ffee@H@@QAEXHH@Z']
    hr = sigs.parse_msvc(hn[0], fnvlink.batch_demangle(fnvlink.UNDNAME, hn)[hn[0]])
    vc = abi.compare(hr, cs) if cs is not None else None
    check(vc is not None and vc.kind == 'bridge' and 'edx-dummy' in vc.classes and
          sorted(vc.moves) == [(('x', 2), ('x', 1), 'I32', 'I32'), (('x', 3), ('x', 2), 'I32', 'I32')],
          'C-named fastcall definition compared: %s' % ((vc and (vc.kind, vc.classes, vc.moves)),))
    check(fnvlink.c_name_sig(sigs.parse_msvc('_FUN_00c10010', None), '') is None, 'cdecl, undeclared: unknown')
    # V1 round 3 (adversarial_cname c2-c4, c8, c9; R3-1, R3-2): real declarations only - comments, strings and
    # preprocessor lines blanked, a declaration context (not `return f(x);`, not a class member), balanced parameter
    # lists (function-pointer parameters), typedefs, bare `unsigned`, the unit's headers through CDecls
    src3 = ('#define __stdcall\ntypedef float real;\nstruct T { int v; void FUN_00c10050(double); };\n'
            'extern "C" float __stdcall FUN_00c10020(void (*cb)(int), int k) { if (cb) cb(k); return k * 0.5f; }\n'
            '// double __stdcall FUN_00c10030(int x);   (decompiler note)\n'
            'const char *note = "float __stdcall FUN_00c10030(int);";\n'
            'extern "C" int __stdcall FUN_00c10030(int x) { return x + 1; }\n'
            'float g4(float v) { return FUN_00c10040(v); }\nfloat g5(float v) { if (v) return FUN_00c10040(-v); '
            'switch ((int)v) { case 1: FUN_00c10040(v); default: break; } return 0; }\n'
            'extern "C" real __stdcall FUN_00c10080(int k) { return k * 0.25f; }\n'
            'extern "C" {\nchar * __stdcall FUN_00c10090(const char *s, unsigned n) { return (char *)s + n; }\n}\n')
    cd3 = fnvlink.CDecls(src3)
    n3 = ['?FUN_00c10020@@YGMPAXH@Z', '?FUN_00c10030@@YGHH@Z', '?FUN_00c10080@@YGMH@Z', '?FUN_00c10090@@YGPADPBDI@Z']
    d3 = fnvlink.batch_demangle(fnvlink.UNDNAME, n3)
    for cn, rn in (('_FUN_00c10020@8', n3[0]), ('_FUN_00c10030@4', n3[1]), ('_FUN_00c10080@4', n3[2]),
                   ('_FUN_00c10090@8', n3[3])):
        c3 = fnvlink.c_name_sig(sigs.parse_msvc(cn, None), cd3)
        v3 = abi.compare(sigs.parse_msvc(rn, d3[rn]), c3) if c3 is not None else None
        check(v3 is not None and v3.kind == 'same', 'C-named %s against %s: %s' % (
            cn, d3[rn], c3 and (c3.raw_ret, c3.raw_params, v3.kind, v3.classes)))
    check(cd3.find('FUN_00c10040') == [] and cd3.find('FUN_00c10050') == [],
          'calls and class members are not declarations: %s %s' % (cd3.find('FUN_00c10040'), cd3.find('FUN_00c10050')))
    # R3-1: undeclared, a stdcall/fastcall name is compared through integer parameters with the other side's own
    # result (no int result assumed: a float callee kept s0); not at all when the other side passes a float
    sf = fnvlink.c_name_standin(sigs.parse_msvc('_FUN_00c10000@8', None), sigs.parse_msvc(n3[0], d3[n3[0]]))
    check(sf is not None and sf.cc == 'stdcall' and len(sf.raw_params) == 2 and abi.ret_class(sf) == 'F32' and
          abi.compare(sigs.parse_msvc(n3[0], d3[n3[0]]), sf).kind == 'same', 'stand-in keeps the result: %s' % (
              (sf and (sf.raw_ret, sf.raw_params)),))
    nf = ['?FUN_00c10001@@YGXM@Z']
    check(fnvlink.c_name_standin(sigs.parse_msvc('_FUN_00c10001@4', None), sigs.parse_msvc(
        nf[0], fnvlink.batch_demangle(fnvlink.UNDNAME, nf)[nf[0]])) is None, 'no stand-in for a float parameter')
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
struct C4 { int a; C4() {} };
struct B4 : A { };
class Q4 { int a; public: int get() const { return a; } };
'''
    with tempfile.TemporaryDirectory() as tree:
        os.makedirs(os.path.join(tree, 'src')); os.makedirs(os.path.join(tree, 'include'))
        open(os.path.join(tree, 'src', 'u.cpp'), 'w').write(unit)
        got = layout.probe_unit(tree, tree, 'src/u.cpp', ['A', 'F3', 'Big', 'NT', 'V', 'ns::P', 'E', 'Ptr<A>',
                                                          'Nope', 'M', 'D4', 'C4', 'B4', 'Q4'])
        want = {'A': (4, 'G1'), 'F3': (12, 'H3x4'), 'Big': (32, 'IND'), 'NT': (4, 'IND'), 'V': (16, 'H2x8'),
                'ns::P': (16, 'G2'), 'E': (1, 'G1'), 'Ptr<A>': (4, 'G1'), 'M': (16, 'H4x4'), 'D4': (32, 'H4x8')}
        for t, (size, cls) in want.items():
            g = got.get(t)
            check(g is not None and (g.size, g.cls) == (size, cls), 'layout %s: %s (want %s %s)' % (t, g, size, cls))
        check(got.get('Nope') is None, 'unnameable type dropped')
        # MSVC C++03 POD (V1 A1/A2): a user constructor, a base class or a private member make a buffer return
        pods = {t: got[t].pod for t in ('A', 'ns::P', 'C4', 'B4', 'Q4', 'NT') if got.get(t)}
        check(pods == {'A': 1, 'ns::P': 1, 'C4': 0, 'B4': 0, 'Q4': 0, 'NT': 0} and got['C4'].trivial == 1,
              'MSVC POD bits: %s (C4 trivial %s)' % (pods, got.get('C4') and got['C4'].trivial))
    # V1 adversarial A1/A2: `C4 f()` vs `void f(C4 *)` - MSVC returns the non-POD C4 through the hidden buffer, so
    # the reference's out pointer is the definition's buffer (sret-explicit bridge), not 'same'
    na = ['?f@@YAXPAUC4@@@Z', '?f@@YA?AUC4@@XZ']
    da = fnvlink.batch_demangle(fnvlink.UNDNAME, na)
    SA = {n: sigs.parse_msvc(n, da[n]) for n in na}
    for pod, want_kind in ((0, 'bridge'), (1, 'same')):
        lc = layout.Layout(4, 4, 1, 0, 'G1', pod)
        for r, d_ in ((na[0], na[1]), (na[1], na[0])):
            pa = abi2.compare(SA[r], SA[d_], lambda t: lc if t == 'C4' else None, lambda t: lc if t == 'C4' else None)
            check(pa is not None and pa.kind == want_kind and (pod or 'sret-explicit' in pa.classes),
                  'C4 pod=%d %s -> %s: %s' % (pod, da[r], da[d_], pa and (pa.kind, pa.classes)))

    # V1 c3_ecx (FP-dead, TP-this-dead): ecx-not-passed / this-not-passed is decided on the definition's x86 code.
    # A hand-made i386 COFF object stands in for a pristine MSVC unit (u0.obj): six functions, one relocation
    funcs = [('?dead@@YIHH@Z', bytes.fromhex('8b442404c20400')),          # mov eax, [esp+4]; ret 4
             ('?rd@@YIHH@Z', bytes.fromhex('8b4108c3')),                  # mov eax, [ecx+8]; ret
             ('?psh@@YIHH@Z', bytes.fromhex('51c7042400000000' '58c3')),  # push ecx; mov [esp], 0; pop eax; ret
             ('?tail@@YIHH@Z', bytes.fromhex('e900000000')),              # jmp ?g@C@@QAEXXZ (thiscall: takes ecx)
             ('?zero@@YIHH@Z', bytes.fromhex('33c98bc1c3')),              # xor ecx, ecx; mov eax, ecx; ret
             ('?br@@YIHH@Z', bytes.fromhex('85c074028b01c3'))]            # test eax, eax; je +2; mov eax, [ecx]; ret
    text, offs = b'', []
    for _, code_ in funcs:
        offs.append(len(text)); text += code_
    names = [n for n, _ in funcs] + ['?g@C@@QAEXXZ']
    strtab = b''.join(n.encode() + b'\0' for n in names)
    sym = b''
    for k, n in enumerate(names):
        so = 4 + sum(len(x) + 1 for x in names[:k])
        sym += struct.pack('<IIIhHBB', 0, so, offs[k] if k < len(funcs) else 0, 1 if k < len(funcs) else 0, 0x20, 2, 0)
    rel = struct.pack('<IIH', offs[3] + 1, len(names) - 1, 0x14)          # REL32 -> ?g@C@@QAEXXZ
    p_text = 20 + 40
    p_rel = p_text + len(text)
    p_sym = p_rel + len(rel)
    coff = struct.pack('<HHIIIHH', 0x14c, 1, 0, p_sym, len(names), 0, 0) + \
        struct.pack('<8sIIIIIIHHI', b'.text', 0, 0, len(text), p_text, p_rel, 0, 1, 0, 0x60000020) + \
        text + rel + sym + struct.pack('<I', 4 + len(strtab)) + strtab
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, 'u0.obj'), 'wb').write(coff)
        units = [{'source': 'src/u.cpp', 'symbols': {}, 'functions': [
            {'address': '%08x' % (0x401000 + 16 * k), 'symbol': n, 'size': len(c_)} for k, (n, c_) in enumerate(funcs)]}]
        code = vslots.Code(types.SimpleNamespace(units=units), tmp)
        takes = {'?g@C@@QAEXXZ': {'ecx'}}.get
        got = [code.entry_use(0x401000 + 16 * k, lambda n, slot=False: takes(n))['ecx'] for k in range(len(funcs))]
        check(got == [None, 'read', 'push', 'pass', None, 'read'], 'incoming ecx use by code: %s' % got)
        check(code.entry_use(0x402000, takes) is None, 'no code: unknown')
        json.dump({'units': units}, open(os.path.join(tmp, 'units.json'), 'w'))
        os.environ['FNVNX_MSVC_CODE'] = tmp
        ms = fnvlink.DemangleCache(os.path.join(tmp, 'msvc.tsv'), fnvlink.UNDNAME)
        msig = {}
        use = fnvlink.msvc_code_use(os.path.join(tmp, 'units.json'), ms,
                                    lambda n: msig.setdefault(n, sigs.parse_msvc(n, ms[n])))
        del os.environ['FNVNX_MSVC_CODE']
        check(use is not None and use(0x401030) == {'ecx': 'pass', 'edx': None},
              'resolve\'s reader: a tail call to a thiscall callee passes ecx: %s' % (use and use(0x401030)))
    ne = ['?h@@YGDD@Z', '?h@S@@QAEEAAUN@@@Z', '?k@@YAXXZ', '?k@@YIPAUN@@PAU1@@Z']
    de = fnvlink.batch_demangle(fnvlink.UNDNAME, ne)
    E = {n: sigs.parse_msvc(n, de[n]) for n in ne}
    v0 = abi.compare(E['?h@@YGDD@Z'], E['?h@S@@QAEEAAUN@@@Z'])
    v1 = abi.compare(E['?h@@YGDD@Z'], E['?h@S@@QAEEAAUN@@@Z'], ecx_unused=True)
    check(v0.kind == 'x86-inconsistent' and v1.kind == 'bridge' and 'ecx-unused' in v1.classes and
          [(d, s) for d, s, _, _ in v1.moves] == [(('x', 1), ('x', 0))],
          'this-not-passed, this unused: bridged (x1 <- x0): %s -> %s %s' % (v0.kind, v1.kind, v1.moves))
    p1 = abi2.compare(E['?h@@YGDD@Z'], E['?h@S@@QAEEAAUN@@@Z'], lambda t: None, lambda t: None, ecx_unused=True)
    check(p1 is not None and p1.kind == 'bridge' and 'ecx-unused' in p1.classes, 'abi2 too: %s' % (p1 and p1.kind))
    v2 = abi.compare(E['?k@@YAXXZ'], E['?k@@YIPAUN@@PAU1@@Z'], ecx_unused=True)
    check(v2.kind == 'same', 'ecx-not-passed, ecx unused, nothing else to move: %s %s' % (v2.kind, v2.classes))
    check(abi.compare(E['?k@@YAXXZ'], E['?k@@YIPAUN@@PAU1@@Z']).kind == 'x86-inconsistent', 'used: still flagged')

    # V1 defect C4 (tools/verify/v1_link/repro_typeinfo_mix.py): two units reconstruct C with different bases; the
    # one that sorts first only names C's typeinfo, the other has the vtable. The linker kept the first typeinfo
    # (one base, 12 bytes) next to the second unit's vtable (two bases): the typeinfo must be the vtable unit's
    ti_a = ('#include <typeinfo>\nstruct B1 { virtual ~B1() {} int x; };\n'
            'struct C : B1 { virtual void f() {} int y; };\nconst std::type_info &ti_from_a() { return typeid(C); }\n')
    ti_b = ('struct B1 { virtual ~B1() {} int x; };\nstruct B2 { virtual void g() {} int z; };\n'
            'struct C : B1, B2 { virtual void f() {} int y; };\nC *make_c() { return new C; }\n')
    with tempfile.TemporaryDirectory() as tmp:
        fixed = os.path.join(tmp, 'fixed')
        os.makedirs(fixed)
        for name, text in (('a_view.cpp', ti_a), ('b_vtable.cpp', ti_b)):
            open(os.path.join(tmp, name), 'w').write(text)
            r = subprocess.run([fnvlink.CC, '-x', 'c++', '-mabi=ilp32', '-O2', '-fPIC', '-c', os.path.join(tmp, name),
                                '-o', os.path.join(fixed, name + '.o')], capture_output=True, text=True)
            check(r.returncode == 0, 'typeinfo units compile: ' + r.stderr[:300])
        rows, cnt = fnvlink.pair_typeinfo(fixed)
        check([r[:4] for r in rows] == [('1C', 'b_vtable.cpp.o', 'a_view.cpp.o', 'a_view.cpp.o')] and
              cnt['undefined'] == 1, 'typeinfo pairing: %s %s' % (rows, dict(cnt)))
        elf = os.path.join(tmp, 'trial.elf')
        subprocess.run([fnvlink.LD, '-m', 'aarch64linux32', '--noinhibit-exec', '-e', '0', '-o', elf,
                        *sorted(os.path.join(fixed, n) for n in os.listdir(fixed))], capture_output=True)
        ti = [s for s in (elfsyms.symbols(elf) if os.path.exists(elf) else []) if s.name == '_ZTI1C' and s.shndx]
        check(len(ti) == 1 and ti[0].size == 32, 'kept typeinfo of C is the vtable unit\'s (two bases, 32 bytes): %s'
              % [(s.size, s.bind) for s in ti])
        check(fnvlink.pair_typeinfo(fixed)[0] == [], 'typeinfo pairing is idempotent')

    print('ok' if ok else 'FAILED')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
