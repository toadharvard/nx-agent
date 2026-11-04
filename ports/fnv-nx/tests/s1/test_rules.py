#!/usr/bin/env python3
"""tests/s1/test_rules.py - regression cases for S1's prepare rules (tools/prepare.py, tools/rules/s1_compile.py).

    python3 tests/s1/test_rules.py          (no decomp needed; rules that read units.json are not covered here)

Each case runs one rule on a small text and checks the output. Our own snippets, nothing of the decomp.
"""
import importlib.util
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, TOOLS)
import prepare  # noqa: E402

sys.modules['prepare'] = prepare
spec = importlib.util.spec_from_file_location('s1_compile', os.path.join(TOOLS, 'rules', 's1_compile.py'))
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)

FAILS = []


def check(name, got, want_in=(), want_not=()):
    ok = all(w in got for w in want_in) and not any(w in got for w in want_not)
    print('%-52s %s' % (name, 'ok' if ok else 'FAIL'))
    if not ok:
        FAILS.append(name)
        print('---- output\n' + got + '\n----')


def run(rule, text, path='/nonexistent/src/x.cpp'):
    return rule(path, text, Counter())


TRAP = '__builtin_trap()'

# ---- FALL1
check('FALL1 non-void without final return gets a trap',
      run(s1.r_falloff_traps, 'int f(int x)\n{\n    if (x) return 1;\n}\n'), [TRAP])
check('FALL1 empty non-void body gets a trap',
      run(s1.r_falloff_traps, 'struct S\n{\n    virtual const char* V0()\n    {}\n};\n'), [TRAP])
check('FALL1 extern "C" void function: no trap (XC1 initializers)',
      run(s1.r_falloff_traps, 'extern "C" void __fnvnx_dyninit_00f35430(void) { g_x = 1; }\n'), (), [TRAP])
check('FALL1 extern "C" int function without return: trap',
      run(s1.r_falloff_traps, 'extern "C" int f(void) { g_x = 1; }\n'), [TRAP])
check('FALL1 void with calling convention / keep macro: no trap',
      run(s1.r_falloff_traps, 'FNVNX_KEEP void __thiscall A::f(int a)\n{\n    g(a);\n}\n'), (), [TRAP])
check('FALL1 FNV_FORCEINLINE void member: no trap',
      run(s1.r_falloff_traps, 'struct L\n{\n    FNV_FORCEINLINE void Unlock()\n    {\n        n = 0;\n    }\n};\n'),
      (), [TRAP])
check('FALL1 constructor after access label: no trap',
      run(s1.r_falloff_traps, 'class B\n{\n  public:\n    B(bool b)\n    {\n        v = b;\n    }\n};\n'), (), [TRAP])
check('FALL1 ends in return: no trap',
      run(s1.r_falloff_traps, 'int f()\n{\n    return 3;\n}\n'), (), [TRAP])
check('FALL1 class with declarations only, after a long comment: no trap',
      run(s1.r_falloff_traps, '// ' + 'x' * 290 + ' (360 source path: a/b.cpp).\n// more\n\nstruct S\n{\n'
          '    unsigned int __thiscall Read(void* p, unsigned int n);\n};\n'), (), [TRAP])
check('FALL1 inside a #define: untouched',
      run(s1.r_falloff_traps, '#define OPS(T) \\\n    T& operator=(T* p) \\\n    { \\\n        m = p; \\\n    }\n'), (), [TRAP])

# ---- ALLOC1
check('ALLOC1 untagged file-scope operator new[] dropped',
      run(s1.r_global_allocators, 'inline void* operator new[](unsigned int n)\n{\n    return A(n);\n}\n'),
      ['ALLOC1'], ['return A(n)'])
check('ALLOC1 @fnv-tagged operator new kept',
      run(s1.r_global_allocators, '// @fnv 0x00401000\nvoid* operator new(unsigned int n)\n{\n    return A(n);\n}\n'),
      ['return A(n)'], ['ALLOC1'])
check('ALLOC1 placement and debug forms kept',
      run(s1.r_global_allocators, 'inline void* operator new(size_t, void* p)\n{\n    return p;\n}\n'
          'inline void* operator new(size_t n, const char* f, int l)\n{\n    return A(n);\n}\n'), ['return p', 'return A(n)'],
      ['ALLOC1'])

# ---- NEW4
check('NEW4 placement new use gets <new>',
      run(s1.r_placement_include, 'void f(char* b) { new (b) T(); }\n'), ['#include <new>'])
check('NEW4 operator new call alone: nothing',
      run(s1.r_placement_include, 'void* f() { return operator new(4); }\n'), (), ['#include <new>'])

# ---- RCAST1
check('RCAST1 reference cast of a member call result',
      run(s1.r_ref_cast_temporaries, 'x = (const M&)a[i].b.f(n);\n'), ['(const M&)fnvnx_lvalue(a[i].b.f(n))'])
check('RCAST1 reference cast of a plain lvalue untouched',
      run(s1.r_ref_cast_temporaries, 'x = (const M&)a[i].b;\n'), (), ['fnvnx_lvalue'])

check('RCAST1 reference cast of a parenthesized expression',
      run(s1.r_ref_cast_temporaries, 'return ((const V&)(a - b)).Len();\n'), ['(const V&)fnvnx_lvalue((a - b))'])

# ---- SPEC2 is limited to its files; TPL2
got = run(s1.r_namespace_template_decls, 'namespace N {\ntemplate <class T> class A\n{\n  public:\n    void d(T* p)\n'
          '    {\n        D(p);\n    }\n};\ntemplate <class T> inline void D(T* p)\n{\n    p->~T();\n}\n}\n')
check('TPL2 forward-declares a namespace function template used earlier', got,
      ['template <class T> inline void D(T* p); /* fnv-nx TPL2 */'])

# ---- MATH1
check('MATH1 drops a decomp copy of float sqrt(float)',
      run(s1.r_math_overloads, 'inline float __cdecl sqrt(float _X)\n{\n    return sqrtf(_X);\n}\n'), ['#include <math.h>'],
      ['return sqrtf'])

# ---- CTOR1
check('CTOR1 explicit constructor call of a template class',
      run(s1.r_ctor_calls, 'void f() { ((std::vector<char>*)this)->std::vector<char>::vector(); }\n'),
      ['::new ((void*)(std::vector<char>*)(((std::vector<char>*)this))) std::vector<char>()'])

check('CTOR1 a constructor definition after a comment ending in "." is not a call',
      run(s1.r_ctor_calls, '// see the matcher checks.\nA::A(int x)\n    : m(x)\n{}\n'), ['A::A(int x)'], ['::new'])

# ---- CSX1
got = run(s1.r_xbox_cs_asserts, 'FNV_STATIC_ASSERT(sizeof(RecursiveLock) == 0x28);\n'
          'FNV_STATIC_ASSERT(offsetof(RecursiveLock, CriticalSection) == 0x0);\n'
          'FNV_STATIC_ASSERT(offsetof(RecursiveLock, m_uiFirstValue) == 0x1C);\n')
check('CSX1 drops the Xbox-only asserts, keeps the rest', got,
      ['offsetof(RecursiveLock, CriticalSection) == 0x0', 'CSX1'], ['== 0x28', '== 0x1C'])

# ---- LEX1
got = run(s1.r_msvc_tokens, 'extern"C"enum E { A };\nchar pad[0xae-0x34];\nlong long k = 0xff00ffffui64 + 5i64;\n'
          'inline __forceinline void f();\n')
check('LEX1 MSVC token spellings', got, ['extern"C" enum', '0xae -0x34', '0xff00ffffULL', '5LL', '\n__forceinline void f'],
      ['ui64', 'inline __forceinline'])
check('LEX1 strings and comments untouched', run(s1.r_msvc_tokens, 'const char* s = "0xae-1 5i64"; // 0x1e+2\n'),
      ['"0xae-1 5i64"', '// 0x1e+2'])

# ---- STD1
got = run(s1.r_std_struct, 'struct std\n{\n    static float __cdecl abs(float v);\n};\nfloat g(float x) { return std::abs(x); }\n')
check('STD1 decomp struct std -> <cmath>', got, ['#include <cmath> /* fnv-nx STD1 */\n\n\n', 'std::abs(x)'], ['struct std'])
check('STD1 other members: untouched', run(s1.r_std_struct, 'struct std\n{\n    static int Make(int v);\n};\n'),
      ['struct std'], ['<cmath>'])

# ---- SPEC3
got = run(s1.r_namespace_specializations, 'template <class T> class P { T* p; };\nclass R;\nnamespace N {\nclass R\n{\n};\n'
          'template <> class P<R>\n{\n  public:\n    R* p;\n    P(R* q);\n    operator R*() const;\n    int R_count;\n};\n}\n')
check('SPEC3 qualifies the namespace types in a specialization body', got,
      ['template <> class P<R>', 'N::R* p;', 'P(N::R* q);', 'operator N::R*()', 'int R_count;'])
check('SPEC3 specialization of a namespace template: untouched',
      run(s1.r_namespace_specializations, 'namespace N {\nclass R;\ntemplate <class T> class P { T* p; };\n'
          'template <> class P<R>\n{\n    R* p;\n};\n}\n'), ['    R* p;'], ['N::R'])

# ---- STATIC1 unsized arrays of address names
check('STATIC1 unsized DAT_ array becomes a declaration',
      run(s1.r_image_statics, 'const void* DAT_010d0fc4[];\nconst void* local_table[];\n'),
      ['extern const void* DAT_010d0fc4[];', '\nconst void* local_table[];'])

# ---- RCAST1 named casts
check('RCAST1 static_cast to a reference of an operator result',
      run(s1.r_ref_cast_temporaries, 'v = static_cast<const D&>(a - b).Len();\nw = static_cast<const D&>(p->m).Len();\n'),
      ['static_cast<const D&>(fnvnx_lvalue(a - b))', 'static_cast<const D&>(p->m)'])
check('RCAST1 member operator call result',
      run(s1.r_ref_cast_temporaries, 'x = ((Pt&)((Op*)&a)->operator-(b)).Length();\n'),
      ['(Pt&)fnvnx_lvalue(((Op*)&a)->operator-(b))'])

# ---- KEEP1 never in front of a statement
check('KEEP1 tag in front of a statement: no FNVNX_KEEP',
      run(s1.r_keep_definitions, 'bool f()\n{\n// @fnv 0x005c4940\n    if (x)\n        g();\n    return true;\n}\n'),
      (), ['FNVNX_KEEP'])

# ---- NEW1 keeps reference parameters
check('NEW1 size parameter -> size_t, reference parameter kept',
      prepare.r_newdel_size('x.cpp', 'static void* operator new[](unsigned int size, unsigned int& total);\n', Counter()),
      ['operator new[](size_t size, unsigned int& total)'])

# ---- scope heads
spans = prepare.scope_spans('// ' + 'y' * 295 + ' (a) b\n// c\n\nstruct T\n{\n    int f();\n};\n')
check('scope head after a cut comment is a class', str([k for _, _, k, _ in spans]), ['class'], ['function'])

print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
