#!/usr/bin/env python3
"""test_s1refs_b.py - V1 (D14) against S1-refs 5e8816f (NEW5, NEW1), 56e41cc (TPL1, RCAST1), e39da35 (NSCALL1,
DUPINT1-RET), e3465bd + 5279bb5 (COND1, RSTR1, STD2 shared renames) and 0a0c0cc (STD2),
all claimed neutral / equivalent to MSVC.

    python3 -I tests/v1/test_s1refs_b.py    -> one line per case, exit 1 when any case shows a defect

Each case runs the rule on our own snippet (a units.json in a temp dir where the rule reads one), compiles the
output with the host g++ (C++17) next to a reference that spells MSVC's meaning, runs both and compares. A case is a
defect when the rewritten code does not compile where the input did, or computes something other than MSVC's meaning.
  N1  NEW5  delete through a base pointer, placement-only class delete      one ::operator delete (MSVC ??_G)
  N2  NEW5  class operator new(size_t) + placement-only delete, ctor throws  MSVC (C4291): no deallocation function
  N3  NEW1  plain `unsigned` size of a class operator delete[]               usual sized delete[] chosen, size passed
  T1  TPL1  out-of-class member of a header's class template, a global       MSVC (no two-phase lookup): base member
            of the same name as the dependent base's member
  T2  TPL1  the same with a local of that name                               the local
  K1  RCAST1 a cast after a comment on the same line                         still rewritten, the value read
  S1  NSCALL1 ADL brings ::_stricmp in through a class argument              N::_stricmp called
  D1  DUPINT1-RET int / char* returns, results unused                        same callee, same bits
  D2  DUPINT1-RET the kept prototype returns a class by value                MSVC called the int overload
  C1  COND1 `c ? false : x.f(a)`, G(bool) + operator bool                    compiles, MSVC's values
  C2  COND1 G's bool constructor is explicit                                 MSVC: the conditional is bool (h(bool))
  C3  COND1 the third operand is `!x.f(a)` (bool), only ends in the call     MSVC: bool ?: bool, compiles
  C4  COND1 the call resolves to the overload returning S (no G(bool))       MSVC: bool, compiles
  R1  RSTR1 restrict local to a `const T&` parameter (T = int*)                       the reference binds the local itself
  A1  STD2  rename, recipe symbol via POST hook, two module copies share it  ?fnvnx_std_abs@@YANM@Z in units.json
  A2  STD2  a float-declared caller of the double definition                 link bridge converts d0 -> s0
  A3  STD2  a same-unit caller of std::abs(float)                            the same value
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, TOOLS)
import prepare  # noqa: E402

sys.modules['prepare'] = prepare


def load_s1(name='s1_compile'):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, 'rules', 's1_compile.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


s1 = load_s1()

PRE = r'''
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cmath>
#include <new>
template <class T> inline T &fnvnx_lvalue(T &&t) { return t; }
template <class T> inline T *&fnvnx_unrestrict(T *__restrict &p) { return *(T **)&p; }
template <class T> inline T *const &fnvnx_unrestrict(T *__restrict const &p) { return *(T *const *)&p; }
#define FNVNX_GLOBAL_DELETE static void operator delete(void *fnvnx_p) noexcept { ::operator delete(fnvnx_p); }
'''


def rule(fn, text, path='/nonexistent/src/x.cpp'):
    return fn(path, text, Counter())


def build_run(work, name, src, extra=()):
    c = os.path.join(work, name + '.cpp')
    exe = os.path.join(work, name)
    with open(c, 'w') as f:
        f.write(PRE + src)
    r = subprocess.run(['g++', '-std=c++17', '-O1', '-w', '-I', work, '-o', exe, c] + list(extra),
                       capture_output=True, text=True)
    if r.returncode:
        lines = [x for x in r.stderr.splitlines() if 'error' in x] or r.stderr.splitlines() or ['compile failed']
        return None, lines[0].replace(work, '')[:160]
    r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
    return r.stdout.strip(), None


def main():
    if not shutil.which('g++'):
        print('skip: no g++')
        return 0
    try:
        work = tempfile.mkdtemp(prefix='v1-s1refs-b-')
    except OSError as e:
        print('mktemp failed: %s' % e)
        return 2
    if not work or not os.path.isdir(work):
        print('mktemp failed')
        return 2
    bad = []

    def case(cid, what, got, want, err=None):
        ok = err is None and got == want
        print('%s: %s - %s%s' % (cid, 'ok' if ok else 'DEFECT', what,
                                 '' if ok else ' (got %r, want %r%s)' % (got, want, ', ' + err if err else '')))
        if not ok:
            bad.append(cid)

    def pair(cid, what, src_in, src_out, main_src):
        """the input under MSVC's meaning (src_in) vs the rule's output (src_out), same main"""
        want, rerr = build_run(work, cid + 'ref', src_in + main_src)
        got, err = build_run(work, cid, src_out + main_src)
        if rerr:
            case(cid, what + ' [reference did not build]', None, None, rerr)
        else:
            case(cid, what, got, want, err)

    COUNT_DELETE = ('int g_del;\nvoid operator delete(void* p) noexcept { ++g_del; std::free(p); }\n'
                    'void operator delete(void* p, std::size_t) noexcept { ++g_del; std::free(p); }\n'
                    'void* operator new(std::size_t n) { return std::malloc(n); }\n')
    try:
        # ---------------------------------------------------------------- NEW5
        n1 = ('struct H\n{\n    virtual ~H() {}\n    static void operator delete(void*, void*) {}\n    int a;\n};\n'
              'struct D : H { int b; ~D() {} };\n')
        out = rule(s1.r_placement_only_delete, n1)
        main = ('int main() { H* p = new D; delete p; H* q = new H; delete q; std::printf("%d\\n", g_del); }\n')
        got, err = build_run(work, 'N1', COUNT_DELETE + out + main)
        case('N1', 'NEW5 deleting destructor through the base frees once via ::operator delete', got, '2', err)

        # TexMap's shape (nitexturingproperty, /EHsc): a class operator new from a pool, the placement delete only.
        # MSVC 2008 (C4291 "no matching operator delete found; memory will not be freed if initialization throws"):
        # a throwing constructor frees nothing. After NEW5 the class has a usual delete, GCC calls it on the throw.
        n2 = ('static char g_pool[256]; int g_pool_used;\n'
              'struct M\n{\n    virtual ~M();\n    int v;\n    M(int x) { if (x < 0) throw 1; v = x; }\n'
              '    static void operator delete(void*, void*) {}\n'
              '    static void* operator new(std::size_t n) { g_pool_used += 16; return g_pool + g_pool_used - 16; }\n'
              '};\nM::~M() {}\n')
        out = rule(s1.r_placement_only_delete, n2)
        # the reference is spelled by hand: GCC rejects the input itself (the error NEW5 exists for), and MSVC's
        # meaning on the throw is "no deallocation function is called" (C4291), i.e. 0 pool blocks handed to delete
        main = ('int g_del_pool;\nvoid operator delete(void* p) noexcept { if ((char*)p >= g_pool && (char*)p < g_pool + 256)'
                ' { ++g_del_pool; return; } std::free(p); }\n'
                'int main() { try { M* m = new M(-1); (void)m; } catch (int) {} std::printf("%d\\n", g_del_pool); }\n')
        got, err = build_run(work, 'N2', out + main)
        case('N2', 'NEW5 throwing constructor of a class with operator new(size_t): pool memory given to delete',
             got, '0', err)

        n3 = ('struct A\n{\n    int x;\n    static void* operator new[](unsigned n) { return std::malloc(n); }\n'
              '    static void operator delete[](void* p, unsigned n) { g_size = n; std::free(p); }\n'
              '    ~A() {}\n    static unsigned g_size;\n};\nunsigned A::g_size;\n')
        out = prepare.r_newdel_size('/nonexistent/src/x.cpp', n3, Counter())
        got, err = build_run(work, 'N3', out + 'int main() { A* a = new A[3]; delete[] a; std::printf("%d\\n", A::g_size != 0); }\n')
        case('N3', 'NEW1 plain unsigned: the usual sized delete[] is the one called', got, '1', err)

        # ---------------------------------------------------------------- TPL1 (a header's class template)
        src = os.path.join(work, 'src')
        os.makedirs(src)
        with open(os.path.join(src, 'tb.h'), 'w') as f:
            f.write('template <class A, class T> class TBase\n{\n  public:\n    int m_count;\n    TBase() : m_count(5) {}\n};\n'
                    'template <class A, class T> class TList : public TBase<A, T>\n{\n  public:\n    int Get();\n'
                    '    int Loc();\n};\n')
        t1 = ('#include "tb.h"\nint m_count = 100;\ntemplate <class A, class T>\nint TList<A, T>::Get()\n{\n'
              '    return m_count;\n}\ntemplate <class A, class T>\nint TList<A, T>::Loc()\n{\n    int m_count = 7;\n'
              '    return m_count;\n}\n')
        out = rule(s1.r_dependent_base_members, t1, os.path.join(src, 'tl.cpp'))
        with open(os.path.join(work, 'tb.h'), 'w') as f:
            f.write(open(os.path.join(src, 'tb.h')).read())
        main = 'int main() { TList<int, int> l; std::printf("%d %d\\n", l.Get(), l.Loc()); }\n'
        got, err = build_run(work, 'T1', out + main)
        case('T1', 'TPL1 header template member: the base member, not the global (MSVC lookup at instantiation)',
             got and got.split()[0], '5', err)
        case('T2', 'TPL1 header template member: a local of the base member\'s name stays the local',
             got and got.split()[-1], '7', err)

        # ---------------------------------------------------------------- RCAST1
        k1 = ('struct V { int x; };\nV Make() { V v; v.x = 3; return v; }\nint f()\n{\n'
              '    // (V&)Make() in a comment\n    int r = /* (V&) */ ((V&)Make()).x;\n    return r;\n}\n')
        out = rule(s1.r_ref_cast_temporaries, k1)
        got, err = build_run(work, 'K1', out + 'int main() { std::printf("%d\\n", f()); }\n')
        case('K1', 'RCAST1 a cast after a comment on the same line is still rewritten', got, '3', err)

        # ---------------------------------------------------------------- NSCALL1, DUPINT1-RET (units.json)
        os.makedirs(os.path.join(work, 'build'))
        units = {'units': [
            {'source': 'src/n.cpp', 'flags': ['/Od'], 'symbols': {'?_stricmp@N@@YAHPBD0@Z': '0x00404dc0'}},
            {'source': 'src/d.cpp', 'symbols': {'_Fmt': '0x00401200', '?Fmt@@YAPADPADIPBDZZ': '0x00401200'}},
            {'source': 'src/r.cpp', 'symbols': {'?Fill@@YA?AUR5@@PADH@Z': '0x00401300', '?Fill@@YAHPADI@Z': '0x00401300'}},
            {'source': 'src/u.cpp', 'flags': ['/MT', '/Od', '/Oy-'], 'symbols': {
                '?Clear@G@@QAEX_N@Z': '0x00845000', '?Reset@G@@QAEXXZ': '0x00644000'},
             'functions': [{'address': '0x00644000', 'symbol': '?Reset@G@@QAEXXZ'}]},
            {'source': 'src/o2.cpp', 'flags': ['/MT', '/O2'], 'symbols': {
                '?Clear@G@@QAEX_N@Z': '0x00845000', '?Reset@G@@QAEXXZ': '0x00644000'},
             'functions': [{'address': '0x00644000', 'symbol': '?Reset@G@@QAEXXZ'}]},
            {'source': 'src/other.cpp', 'flags': ['/Od'], 'symbols': {}, 'functions': [{'address': '0x00845000', 'symbol': '?Clear@FUN_008454f0@@QAEX_N@Z'}]},
            {'source': 'src/guard.cpp', 'symbols': {'?abs@std@@YANM@Z': '0x00408840'},
             'functions': [{'address': '0x00408840', 'symbol': '?abs@std@@YANM@Z', 'size': 20}]}]}
        with open(os.path.join(work, 'build', 'units.json'), 'w') as f:
            json.dump(units, f)
        s1._UNIT_SYMBOLS = s1._UNIT_INFO = s1._ALL_FUNCTIONS = s1._STD2_FNS = None

        ns = ('struct Str { const char* s; operator const char*() const { return s; } };\n'
              'namespace N {\nint _stricmp(const char* a, const char* b);\n'
              'int f(const Str& k)\n{\n    return _stricmp(k, "x");\n}\n}\n'
              'int N::_stricmp(const char*, const char*) { return 1; }\n')
        out = rule(s1.r_namespace_crt_calls, ns, os.path.join(src, 'n.cpp'))
        glob = 'int _stricmp(const char*, const char*) { return 2; }\n'      # the shim's ::_stricmp
        got, err = build_run(work, 'S1', glob + out + 'int main() { Str s = {"y"}; std::printf("%d\\n", N::f(s)); }\n')
        case('S1', 'NSCALL1 class argument brings ::_stricmp by ADL: N::_stricmp called', got, '1', err)

        alias = ('extern "C" int real_fill(char* p, unsigned n) { std::memset(p, \'a\', n); return (int)n; }\n'
                 'asm(".globl %s\\n.set %s, real_fill\\n.globl %s\\n.set %s, real_fill\\n");\n')
        d1 = ('extern "C" int Fmt(char* b, int n, const char* f, ...);\n'
              'char* Fmt(char* d, unsigned int n, const char* f, ...);\n'
              'void g(char* b, unsigned n)\n{\n    Fmt(b, n, "%d", 1);\n    if (n) Fmt(b, 2u, "y");\n}\n'
              'extern "C" int Fmt(char* b, int n, const char* f, ...) { std::memset(b, \'z\', n); return n; }\n'
              'asm(".globl _Z3FmtPcjPKcz\\n.set _Z3FmtPcjPKcz, Fmt\\n");\n')
        out = rule(s1.r_duplicate_int_overloads, d1, os.path.join(src, 'd.cpp'))
        main = 'int main() { char b[8] = {0}; g(b, 5); std::printf("%s\\n", b); }\n'
        pair('D1', 'DUPINT1-RET int / char* results unused: same callee, same bits', d1, out, main)

        d2 = ('struct R5 { int a, b, c, d, e; };\n'
              'R5 Fill(char* p, int n);\nint Fill(char* p, unsigned int n);\n'
              'void h(char* p)\n{\n    Fill(p, 3u);\n}\n' + alias % ('_Z4FillPci', '_Z4FillPci', '_Z4FillPcj', '_Z4FillPcj'))
        out = rule(s1.r_duplicate_int_overloads, d2, os.path.join(src, 'r.cpp'))
        main = 'int main() { char b[8] = {0}; h(b); std::printf("[%s]\\n", b); }\n'
        pair('D2', 'DUPINT1-RET kept prototype returns a class by value (hidden result pointer)', d2, out, main)

        # ---------------------------------------------------------------- COND1
        gate = ('struct G { unsigned char v; G(bool b) : v(b) {} operator bool() const { return v != 0; } };\n'
                'struct X { G f(int a) { return G(a > 2); } };\n')
        c1 = gate + 'int t(X& x, bool c, int a) { if (c ? false : x.f(a)) return 1; return 0; }\n'
        out = rule(s1.r_conditional_bool_gates, c1)
        ref = c1.replace('c ? false : x.f(a)', 'c ? G(false) : x.f(a)')     # MSVC's conversion (5279bb5)
        main = 'int main() { X x; std::printf("%d%d%d\\n", t(x, true, 5), t(x, false, 5), t(x, false, 1)); }\n'
        pair('C1', 'COND1 bool gate: MSVC converts the literal to G', ref, out, main)

        hov = 'int h(bool) { return 1; }\nint h(const G&) { return 2; }\n'
        c2 = gate.replace('G(bool b)', 'explicit G(bool b)') + hov + \
            'int t(X& x, bool c) { return h(c ? false : x.f(5)); }\n'
        out = rule(s1.r_conditional_bool_gates, c2)
        main = 'int main() { X x; std::printf("%d\\n", t(x, true)); }\n'
        pair('C2', 'COND1 explicit G(bool): the conditional is bool, h(bool) called', c2, out, main)

        c3 = gate + 'int t(X& x, bool c) { bool r = c ? false : !x.f(1); return r; }\n'
        out = rule(s1.r_conditional_bool_gates, c3)
        main = 'int main() { X x; std::printf("%d%d\\n", t(x, true), t(x, false)); }\n'
        pair('C3', 'COND1 third operand `!x.f(1)` is bool already', c3, out, main)

        c4 = (gate.replace('struct X {', 'struct S { int v; operator bool() const { return v != 0; } };\nstruct X {')
              .replace('G f(int a) { return G(a > 2); }', 'G f(int a) { return G(a > 2); } S f(float a) { S s; s.v = a > 2; return s; }')
              + 'int t(X& x, bool c) { bool r = c ? false : x.f(5.0f); return r; }\n')
        out = rule(s1.r_conditional_bool_gates, c4)
        main = 'int main() { X x; std::printf("%d%d\\n", t(x, true), t(x, false)); }\n'
        pair('C4', 'COND1 the call resolves to the overload returning S (no bool constructor)', c4, out, main)

        # ---------------------------------------------------------------- RSTR1
        r1 = ('const void* g_seen;\ntemplate <class T> struct Arr { void AddBack(const T& v) { g_seen = &v; } };\n'
              'int t(Arr<int*>& a, int* q)\n{\n    int* __restrict v = q;\n    a.AddBack(v);\n    return g_seen == (const void*)&v;\n}\n')
        out = s1.r_restrict_reference_args('/nonexistent/src/r.cpp', r1, Counter())
        got, err = build_run(work, 'R1', out + 'int main() { Arr<int*> a; int k; std::printf("%d\\n", t(a, &k)); }\n')
        case('R1', 'RSTR1 the const T& parameter (T = int*) binds the restrict local itself', got, '1', err)

        # ---------------------------------------------------------------- STD2
        s1b = load_s1('s1_compile_copy2')            # equiv.py loads the stream modules twice
        s1b._STD2_FNS = None
        a1 = '#include <cmath>\n// @fnv 0x00408840\ndouble std::abs(float value)\n{\n    return std::fabs(value);\n}\n'
        out = s1b.r_std_definitions(os.path.join(src, 'guard.cpp'), a1, Counter())
        s1.rename_std_definitions(work, Counter())   # the first copy's POST hook
        sym = [f['symbol'] for u in json.load(open(os.path.join(work, 'build', 'units.json')))['units']
               for f in u.get('functions', []) if f['address'] == '0x00408840']
        case('A1', 'STD2 rename by copy 2, recipe symbol renamed by copy 1\'s POST hook',
             '%s|%s' % ('double fnvnx_std_abs(float value)' in out, sym), 'True|[\'?fnvnx_std_abs@@YANM@Z\']')
        try:
            sys.path.insert(0, os.path.join(TOOLS, 'link'))
            import abi
            import fnvlink
            import sigs
            names = ['?abs@std@@YAMM@Z', '?fnvnx_std_abs@@YANM@Z', 'FUN_00408840']
            dem = fnvlink.batch_demangle(fnvlink.UNDNAME, names[:2])
            ref = sigs.parse_msvc(names[0], dem.get(names[0]))
            dfn = sigs.parse_msvc(names[1], dem.get(names[1]))
            v = abi.compare(ref, dfn)
            case('A2', 'STD2 float-declared caller of the renamed double definition: result converted',
                 str(v.ret_fix), str(('fcvt', 'F64', 'F32')))
        except Exception as e:  # noqa: BLE001
            case('A2', 'STD2 link bridge check', None, 'ret_fix', 'link tools: %s' % e)
        a3 = out + 'float use(float x) { return (float)std::abs(x); }\n'
        ref3 = a1.replace('std::abs(float value)', 'msvc_std_abs(float value)') + \
            'float use(float x) { return (float)msvc_std_abs(x); }\n'
        main = 'int main() { std::printf("%g %g\\n", use(-2.5f), use(3.0f)); }\n'
        pair('A3', 'STD2 a same-unit std::abs(float) caller', ref3, a3, main)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
