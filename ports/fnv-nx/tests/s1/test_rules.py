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
check('FALL1 extern "C" void function: no trap',
      run(s1.r_falloff_traps, 'extern "C" void f(void) { g_x = 1; }\n'), (), [TRAP])
check('FALL1 extern "C" int function without return: trap',
      run(s1.r_falloff_traps, 'extern "C" int f(void) { g_x = 1; }\n'), [TRAP])
check('FALL1 void with calling convention / keep macro: no trap',
      run(s1.r_falloff_traps, 'FNVNX_KEEP void __thiscall A::f(int a)\n{\n    g(a);\n}\n'), (), [TRAP])
check('FALL1 FNV_FORCEINLINE void member: no trap',
      run(s1.r_falloff_traps, 'struct L\n{\n    FNV_FORCEINLINE void Unlock()\n    {\n        n = 0;\n    }\n};\n'),
      (), [TRAP])
check('FALL1 constructor after access label: no trap',
      run(s1.r_falloff_traps, 'class B\n{\n  public:\n    B(bool b)\n    {\n        v = b;\n    }\n};\n'), (), [TRAP])
check('FALL1 void member of a class template with two nested template arguments: no trap',
      run(s1.r_falloff_traps, 'FNVNX_KEEP void L<A<unsigned int>,P<B> >::AddTail(const P<B>& e)\n{\n    n = 1;\n}\n'),
      (), [TRAP])
check('FALL1 int member of the same template: trap',
      run(s1.r_falloff_traps, 'int L<A<unsigned int>,P<B> >::Count()\n{\n    n = 1;\n}\n'), [TRAP])
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
got = run(s1.r_namespace_template_decls, 'namespace N {\ntemplate <class T> class A\n{\n  public:\n    void d(T* p)\n'
          '    {\n        D(p);\n    }\n};\ntemplate <class T> inline void D(T* p)\n{\n    p->~T();\n}\n'
          'template <> inline void D(char*)\n{\n}\n}\n')
check('TPL2 never forward-declares an explicit specialization (template <>) ahead of its primary', got,
      ['template <class T> inline void D(T* p); /* fnv-nx TPL2 */'], ['template <> inline void D(char*);'])

# ---- S1-STL (blk_00ac0000): TPL2 specializations, TPL3, TYPN1, INST1, COPY1, DECL3
got = run(s1.r_namespace_template_decls, 'namespace N {\ntemplate <class T> class A\n{\n  public:\n    void d(T* p)\n'
          '    {\n        D(p);\n    }\n};\ntemplate <> inline void D<int>(int* p)\n{\n}\n'
          'template <class T> inline void D(T* p)\n{\n    p->~T();\n}\n}\n')
check('TPL2 declares the primary, not the explicit specialization', got,
      ['template <class T> inline void D(T* p); /* fnv-nx TPL2 */'], ['void D<int>(int* p); /* fnv-nx TPL2'])
got = run(s1.r_forward_template_calls, 'namespace N {\nstruct S { void m(int* a) { ::N::cp(a, a); } };\n'
          'template <class T> inline T* cp(T* a, T* b) { return a; }\n}\n')
check('TPL3 (B) declares a namespace template called qualified before its definition', got,
      ['namespace N { template <class T> inline T* cp(T* a, T* b); } /* fnv-nx TPL3 */ namespace N {'])
got = run(s1.r_forward_template_calls, 'namespace N {\nstruct S { void m(int* a) { ::N::cp(a, Later()); } };\n'
          'struct Later {};\ntemplate <class T> inline T* cp(T* a, Later b) { return a; }\n}\n')
check('TPL3 (B) leaves a signature that names a type not seen yet', got, (), ['fnv-nx TPL3'])
got = run(s1.r_dependent_typenames, 'namespace N {\ntemplate <class _Iter> void f(_Iter a)\n'
          '{ g(a, _Iter_cat<iterator_traits<_Iter>::iterator_category>()); }\n}\n')
check('TYPN1 adds typename before a dependent library type member in a template argument', got,
      ['_Iter_cat<typename iterator_traits<_Iter>::iterator_category>()'])
got = run(s1.r_dependent_typenames, 'template <class _Iter> void f(_Iter a) { g(h<iterator_traits<int>::value_type>()); }\n')
check('TYPN1 leaves a non-dependent qualified name', got, (), ['typename'])
_inst = ('namespace other {\ntemplate <class T> class vector { public: void ins(const T* w, unsigned int n, const T& v); };\n}\n'
         'namespace rstd {\ntemplate <class T> class vector\n{\n  public:\n    typedef unsigned int size_type;\n'
         '    void ins(const T* w, size_type n, const T& v);\n};\n}\n')
got = run(s1.r_instantiation_size_types, _inst + 'template void rstd::vector<int>::ins(const int*, unsigned int,\n'
          '                                     const int&);\n')
check('INST1 size_type from the class template of the qualifier\'s namespace (not the first `vector`)', got,
      ['template void rstd::vector<int>::ins(const int*, rstd::vector<int>::size_type, const int&);'])
got = run(s1.r_instantiation_size_types, _inst + 'template void other::vector<int>::ins(const int*, unsigned int, const int&);\n')
check('INST1 leaves a member declared with unsigned int', got,
      ['template void other::vector<int>::ins(const int*, unsigned int, const int&);'], ['::size_type, const int&'])
got = run(s1.r_copy_ctor_base_casts, 'struct A { int a; };\nstruct B : A { B(const B& o); B(A a); };\n'
          'struct X : B { X(const X& o); };\nX::X(const X& o) : B(o) {}\n')
check('COPY1 casts to the base whose constructor also takes its own base by value', got,
      ['X::X(const X& o) : B(static_cast<const B&>(o)) {}'])
got = run(s1.r_copy_ctor_base_casts, 'struct A { int a; };\nstruct B : A { B(const B& o); };\n'
          'struct X : B { X(const X& o); };\nX::X(const X& o) : B(o) {}\n')
check('COPY1 leaves a base with only its copy constructor', got, ['X::X(const X& o) : B(o) {}'])
got = run(prepare.r_crt_typedefs, 'typedef _iobuf FILE;\ntypedef __int64 fpos_t;\nFILE* f; fpos_t p; const char* s = "FILE";\n')
check('DECL3 spells FILE as _iobuf and renames MSVC fpos_t, strings untouched', got,
      ['_iobuf* f; fnvnx_msvc_fpos_t p;', 'typedef __int64 fnvnx_msvc_fpos_t;', '"FILE"'], ['typedef _iobuf FILE'])

got = prepare.r_platform_protos('/x/src/w.cpp', 'extern "C" size_t __cdecl wcslen(const wchar_t* s);\nint g();\n', Counter())
check('DECL1 a <wchar.h> prototype becomes the header', got, ['#include <wchar.h> /* fnv-nx DECL1 */\nint g();'], ['wcslen'])
# DECL1-CONV: a stored / returned result keeps the decomp's declared type where it differs in kind from the platform's
got = prepare.r_platform_protos('/x/src/c.cpp', '__declspec(dllimport) unsigned int __stdcall CreateMutexA(int, int, const void*);\n'
                                'extern "C" char* __cdecl strrchr(const char* s, int c);\n'
                                'void f(const char* n) { unsigned int m; m = CreateMutexA(0, 0, n); char* p = strrchr(n, 47);\n'
                                '  g(CreateMutexA(0, 0, n)); }\n', Counter())
check('DECL1-CONV a stored handle result is converted to the decomp type; one passed on is not', got,
      ['m = ((unsigned int)CreateMutexA(0, 0, n));', 'char* p = ((char*)strrchr(n, 47));', 'g(CreateMutexA(0, 0, n));'])
got = prepare.r_platform_protos('/x/src/v.cpp', 'int g();\nnamespace std {\nfloat abs(float);\n}\nfloat h(float x) { return std::abs(x); }\n', Counter())
check('DECL1-STDABS a std::abs prototype becomes fnvnx_cmath/std_abs.h', got,
      ['#include "fnvnx_cmath/std_abs.h" /* fnv-nx DECL1 */', 'std::abs(x)'], ['float abs(float);', '<stdlib.h>'])

# ---- STL2: units with the real container headers use the VC9-layout shim
_stl2 = ('// std::string in a comment\n#include <string>\n#include <vector>\nnamespace std {\ntemplate <> class allocator<E>\n'
         '{ typedef size_t size_type; };\n}\nstruct S { std::string s; ::std::vector<int> v; };\n'
         'const char* n = "std::string";\nvoid f() { stdext::unchecked_copy(a, b, c); x::std::y z; }\n')
got = run(s1.r_vc9_containers, _stl2)
check('STL2 includes fnvnx_vc9.h in place of the container headers, line count kept', got,
      ['#include "fnvnx_vc9.h" /* fnv-nx STL2 */\n/* fnv-nx STL2: #include <vector> */\n'], ['#include <string>'])
check('STL2 spells std / ::std / stdext as fnvnx_vc9 in code, not in comments, strings or nested names', got,
      ['struct S { fnvnx_vc9::string s; ::fnvnx_vc9::vector<int> v; };', 'fnvnx_vc9::stdext::unchecked_copy(',
       '// std::string in a comment', '"std::string"', 'x::std::y z;', 'namespace fnvnx_vc9 {'])
check('STL2 size_t in the unit\'s own namespace std is MSVC\'s (unsigned int)', got, ['typedef _Sizet size_type;'])
check('STL2 line count kept', str(got.count('\n') == _stl2.count('\n')), ['True'])
check('STL2 leaves a unit without the real container headers', run(s1.r_vc9_containers,
      '#include "rstd.h"\nstd::string s;\n'), ['std::string s;'], ['fnvnx_vc9'])
got = s1.r_specialization_decls('/x/src/speedtree/projectedshadow_ob1_variant.cpp',
      'void a();\n#include "h.h"\n#include <memory>\ntemplate <class T> struct B;\nvoid g() {}\n'
      'template <> X<char>::X(int);\n', Counter())
check('SPEC1 never prefixes a preprocessor line: after the directives that follow its anchor', got,
      ['#include <memory>\ntemplate <> X<char>::X(int); /* fnv-nx SPEC1 */ template <class T> struct B;'])

got = run(s1.r_dependent_base_members, 'template <class T> struct V\n{\n    struct A {};\n    struct CI\n    {\n'
          '        A* aux;\n        T* ptr;\n    };\n    struct I : CI\n    {\n'
          '        I& operator+=(int n) { if (!aux) f(); ptr += n; return *this; }\n        void g(int ptr) { ptr = 1; }\n'
          '    };\n};\nstruct P { int q; };\nstruct O : P { void h() { q = 1; } };\n')
check('TPL1-NESTED this-> for members of a sibling member class used as base in a class template', got,
      ['if (!this->aux) f(); this->ptr += n;', 'void g(int ptr) { ptr = 1; }', 'void h() { q = 1; }'])

# ---- EBO1: MSVC gives the empty base optimization to the first of two empty bases only
_ebo = ('struct A\n{\n    void f();\n};\nstruct B\n{\n    static void* operator new(unsigned int);\n};\n'
        'namespace n { struct B { int x; }; }\nstruct D : A, B\n{ FNVNX_MSVC_BASE_END(B)\n    unsigned char m[19];\n    D();\n};\n'
        'struct E : A, B\n{\n    E();\n};\nstruct V { virtual ~V(); };\nstruct F : A, V\n{\n    int x;\n};\n')
got = run(s1.r_msvc_empty_bases, _ebo)
check('EBO1 a class with two empty bases starts its members at 1 (the base in another namespace ignored)', got,
      ['struct D : A, B\n{ char fnvnx_msvc_ebo; /* fnv-nx EBO1 */ FNVNX_MSVC_BASE_END(B)'])
check('EBO1 leaves a class without data and one with a non-empty base', got,
      ['struct E : A, B\n{\n    E();', 'struct F : A, V\n{\n    int x;'])

# ---- MATH1
check('MATH1 drops a decomp copy of float sqrt(float)',
      run(s1.r_math_overloads, 'inline float __cdecl sqrt(float _X)\n{\n    return sqrtf(_X);\n}\n'), ['#include <math.h>'],
      ['return sqrtf'])

check('MATH1 in a namespace that takes the C fabs: the copy goes',
      run(s1.r_math_overloads, 'namespace N {\nusing ::fabs; /* fnv-nx DECL1 */\ninline float fabs(float f)\n{\n'
          '    return (float)fabs((double)f);\n}\nfloat g(float x) { return fabs(x); }\n}\n'),
      ['/* fnv-nx MATH1 */', 'float g(float x)'], ['(float)fabs((double)f)', '#include <math.h>'])
check('MATH1 next to the namespace\'s own extern "C" prototype: the copy stays (MSVC found it)',
      run(s1.r_math_overloads, 'namespace N {\nextern "C" double cos(double);\ninline float __cdecl cos(float _X)\n{\n'
          '    return cosf(_X);\n}\n}\n'), ['return cosf(_X);'], ['MATH1'])
check('MATH1 in a namespace of its own: the overload stays',
      run(s1.r_math_overloads, 'namespace N {\ninline float fabs(float f)\n{\n    return f < 0 ? -f : f;\n}\n}\n'),
      ['return f < 0 ? -f : f;'], ['MATH1'])

# ---- IFX1-SSE: a dropped `__if_not_exists(_mm_*)` prototype gets fnvnx_target_sse.h (MSVC's intrinsics in the gate)
_ix = run(s1.r_if_exists, 'extern "C" {\n__if_not_exists(_mm_mul_ps)\n{\n    __m128 _mm_mul_ps(__m128 a, __m128 b);\n}\n}\n')
check('IFX1-SSE a dropped SSE prototype includes fnvnx_target_sse.h', _ix,
      ['#include "fnvnx_target_sse.h" /* fnv-nx IFX1 */'], ['_mm_mul_ps(__m128 a'])
check('IFX1-SSE a kept body includes nothing',
      run(s1.r_if_exists, '__if_not_exists(MyOwnThing)\n{\n    int MyOwnThing;\n}\n'), ['int MyOwnThing;'], ['fnvnx_target_sse'])

# ---- CALL1-RET: a `return f(...);` statement is a call, not the file's own prototype of f
_c1 = run(s1.r_win32_call_args, '#include "fnvnx_win32.h"\nint h(void* k, int c) {\n    return CallNextHookEx((void*)k, c, 0, 0);\n}\n')
check('CALL1-RET a returned Win32 call gets its pointer arguments cast', _c1, ['CallNextHookEx((HHOOK)((void*)k)'])

# ---- CALL1-NS: a call qualified by a namespace that only has DECL1's `using ::name;` is the platform function
_c2 = run(s1.r_win32_call_args, '#include "fnvnx_win32.h"\nnamespace D {\nusing ::CallNextHookEx; /* fnv-nx DECL1 */\n}\n'
          'int h(void* k, int c) {\n    return D::CallNextHookEx((void*)k, c, 0, 0);\n}\n')
check('CALL1-NS a call qualified by a namespace with DECL1\'s using-declaration gets its casts', _c2,
      ['D::CallNextHookEx((HHOOK)((void*)k)'])

# ---- MATH1-OWN: a file's own static C math function, once MATH1 brings <math.h>, is renamed with its calls
_m1 = run(s1.r_math_overloads, 'extern "C++"\n{\n    inline float __cdecl fabs(float _X)\n    {\n        return ((float)fabs((double)(_X)));\n    }\n}\n'
          'static __forceinline float sqrtf(float x)\n{\n    return (float)sqrt(x);\n}\nfloat g(float s) { return 1.0f / sqrtf(s); }\n')
check('MATH1-OWN the unit\'s own static sqrtf is renamed where <math.h> comes in', _m1,
      ['#include <math.h> /* fnv-nx MATH1 */', 'static __forceinline float fnvnx_own_sqrtf(float x)', '1.0f / fnvnx_own_sqrtf(s)'])

# ---- CTOR1
check('CTOR1 explicit constructor call of a template class',
      run(s1.r_ctor_calls, 'void f() { ((std::vector<char>*)this)->std::vector<char>::vector(); }\n'),
      ['::new ((void*)(std::vector<char>*)(((std::vector<char>*)this))) std::vector<char>()'])

check('CTOR1 a constructor definition after a comment ending in "." is not a call',
      run(s1.r_ctor_calls, '// see the matcher checks.\nA::A(int x)\n    : m(x)\n{}\n'), ['A::A(int x)'], ['::new'])

# ---- CSX1 / CSX2
got = run(s1.r_xbox_cs_asserts, 'FNV_STATIC_ASSERT(sizeof(RecursiveLock) == 0x28);\n'
          'FNV_STATIC_ASSERT(offsetof(_FILETIME, dwHighDateTime) == 0x0);\n')
check('CSX1 drops only the Xbox _FILETIME order now', got, ['sizeof(RecursiveLock) == 0x28', 'CSX1'], ['dwHighDateTime) =='])
got = run(s1.r_critical_section_tail, 'class RecursiveLock\n{\n  public:\n    _RTL_CRITICAL_SECTION CriticalSection;  // +0x0\n'
          '    unsigned long m_uiFirstValue;  // +0x1C\n};\nFNV_STATIC_ASSERT(offsetof(RecursiveLock, CriticalSection) == 0x0);\n'
          'FNV_STATIC_ASSERT(offsetof(RecursiveLock, m_uiFirstValue) == 0x1C);\n'
          'struct PcLock\n{\n    _RTL_CRITICAL_SECTION cs;\n    int n;\n};\nFNV_STATIC_ASSERT(offsetof(PcLock, cs) == 0);\n'
          'FNV_STATIC_ASSERT(offsetof(PcLock, n) == 24);\n')
check('CSX2 gives a section the class asserts as 28 bytes its 4 bytes', got,
      ['_RTL_CRITICAL_SECTION CriticalSection; unsigned int CriticalSection_fnvnx_tail;', '    _RTL_CRITICAL_SECTION cs;\n    int n;'])

# ---- LEX1
got = run(s1.r_msvc_tokens, 'extern"C"enum E { A };\nchar pad[0xae-0x34];\nlong long k = 0xff00ffffui64 + 5i64;\n'
          'inline __forceinline void f();\n')
check('LEX1 MSVC token spellings', got, ['extern"C" enum', '0xae -0x34', '0xff00ffffULL', '5LL', '\n__forceinline void f'],
      ['ui64', 'inline __forceinline'])
check('LEX1 functional casts to multi-word types',
      run(s1.r_msvc_tokens, 'void f() {\n    key = unsigned long(-1);\n    return key != unsigned long(-1);\n'
          '    p = reinterpret_cast<void*>(unsigned int(id));\n    construct(n, unsigned int());\n'
          '    unsigned long (*fp)(int) = 0;\n}\n'),
      ['key = ((unsigned long)(-1));', 'key != ((unsigned long)(-1));', 'reinterpret_cast<void*>(((unsigned int)(id)))',
       'construct(n, ((unsigned int)(0)));', 'unsigned long (*fp)(int) = 0;'])
check('LEX1 declarators and function types are not casts',
      run(s1.r_msvc_tokens, 'struct S { unsigned int (__cdecl* m_pfnRead)(void*); };\nD<unsigned char(unsigned int)> d;\n'
          'x = reinterpret_cast<unsigned int (T::*)()>(p);\nvoid f(int a, unsigned long (b));\n'),
      ['unsigned int (__cdecl* m_pfnRead)(void*);', 'D<unsigned char(unsigned int)> d;',
       'reinterpret_cast<unsigned int (T::*)()>(p);', 'unsigned long (b));'], ['((unsigned'])
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

check('RCAST1 a cast operand and a declarator are left alone',
      run(s1.r_ref_cast_temporaries, 'a.Add((void* const&)(void*)this);\nbool GetAt(const char*, void(__cdecl*&)());\n'),
      (), ['fnvnx_lvalue'])

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

check('KEEP1 tag above an access label: FNVNX_KEEP after the label',
      run(s1.r_keep_definitions, 'class C {\n// @fnv 0x00cd3ad0\npublic:\n    C() {}\n};\n'), ['public:\n    FNVNX_KEEP C() {}'])

check('KEEP1 tag in front of a member initializer list: no FNVNX_KEEP',
      run(s1.r_keep_definitions, 'A::A(int n)\n// @fnv 0x00c31bd0\n    : Base(n)\n{}\n'), (), ['FNVNX_KEEP'])

# ---- NEW1 keeps reference parameters
check('NEW1 size parameter -> size_t, reference parameter kept',
      prepare.r_newdel_size('x.cpp', 'static void* operator new[](unsigned int size, unsigned int& total);\n', Counter()),
      ['operator new[](size_t size, unsigned int& total)'])

# ---- DTOR1
DTOR1_HEAD = 'template <class T> class NiPointer\n{\n    T* m_pObject;\n    ~NiPointer()\n    {\n        if (m_pObject)\n            m_pObject->DecRefCount();\n    }\n};\nclass Qa;\nclass Ra;\nclass Sa;\nnamespace N { class Qa : public Base {}; }\nclass Base { void DecRefCount(); };\nclass Ra : public Base {};\nclass Sa { void DecRefCount(); };\nstruct B { virtual void k(); NiPointer<Qa> q; NiPointer<Ra> r; NiPointer<Sa> s; };\n'
import tempfile
_d = tempfile.mkdtemp()
os.makedirs(os.path.join(_d, 'src'))
with open(os.path.join(_d, 'src', 'b.h'), 'w') as _h: _h.write(DTOR1_HEAD)
_unit = os.path.join(_d, 'src', 'b.cpp')
check('DTOR1 an include-only unit (a header unit): the declarations after its includes',
      run(s1.r_incomplete_pointee_dtors, '#include "b.h"\n// end', _unit),
      ['// end\ntemplate <> NiPointer<Qa>::~NiPointer(); /* fnv-nx DTOR1 */'], ['NiPointer<Ra>::~'])
check('DTOR1 an include-only unit with nothing to declare: unchanged',
      run(s1.r_incomplete_pointee_dtors, '#include "none.h"', os.path.join(_d, 'src', 'e.cpp')) == '#include "none.h"' and 'x' or '',
      ['x'])
got = run(s1.r_incomplete_pointee_dtors, '#include "b.h"\n// k\nvoid B::k() {}\n', _unit)
check('DTOR1 declares the destructor for a pointee never completed at file scope', got,
      ['template <> NiPointer<Qa>::~NiPointer(); /* fnv-nx DTOR1 */'], ['NiPointer<Ra>::~', 'NiPointer<Sa>::~'])

with open(os.path.join(_d, 'src', 'c.h'), 'w') as _h:
    _h.write(DTOR1_HEAD.replace('template <class T> class NiPointer', '#define NiPointer X\ntemplate <class T> class NiPointer')
             + '#undef NiPointer\ntemplate <class T> class NiPointer\n{\n    T* m_pObject;\n};\n')
check('DTOR1 a unit-local NiPointer without that destructor: nothing',
      run(s1.r_incomplete_pointee_dtors, '#include "c.h"\nvoid B::k() {}\n', os.path.join(_d, 'src', 'c.cpp')), (), ['DTOR1'])

# ---- TPL1
got = run(s1.r_dependent_base_members,
          'template<class T> struct Store {\n T* data; int size; int capacity;\n void own(T* p,int n) {data=p;size=n;}\n'
          ' typedef T value;\n};\n'
          'template<class T> struct Arr : Store<T> {\n int extra;\n Arr(int count) {\n  own(0,count);\n  if(size>0) data[0]=T();\n'
          '  int cap=capacity; extra=cap;\n }\n void f(int size) { data[size]=T(); other.size=1; }\n'
          ' static void g() { int capacity=0; }\n};\n'
          'template<class T> void Arr<T>::h() { capacity = 0; }\n')
check('TPL1 this-> for dependent base members', got,
      ['  this->own(0,count);', 'if(this->size>0) this->data[0]', 'int cap=this->capacity; extra=cap;',
       'void f(int size) { this->data[size]=T(); other.size=1; }', 'static void g() { int capacity=0; }',
       'void Arr<T>::h() { this->capacity = 0; }'], ['this->extra', 'this->value', 'this->size]'])
check('TPL1 base with nested template arguments',
      run(s1.r_dependent_base_members, 'template <class T, class A> class Arr\n{\n  public:\n    T* m_pBase;\n'
          '    unsigned short m_usMaxSize, m_usSize;\n};\ntemplate <class T> class Prim : public Arr<T, Alloc<T> >\n{\n'
          '  public:\n    void SetAt(unsigned int i)\n    {\n        if (i >= m_usSize) m_usSize = i + 1;\n    }\n};\n'),
      ['if (i >= this->m_usSize) this->m_usSize = i + 1;'])
with open(os.path.join(_d, 'src', 'lb.h'), 'w') as _h:
    _h.write('template <class A, class T> class ListBase\n{\n  public:\n    T* m_pkHead;\n    unsigned int m_uiCount;\n};\n')
check('TPL1 dependent base defined in an included header',
      run(s1.r_dependent_base_members, '#include "lb.h"\ntemplate <class T> class PList : public ListBase<int, T>\n{\n'
          '  public:\n    PList()\n    {\n        m_pkHead = 0;\n    }\n};\n', os.path.join(_d, 'src', 'pl.h')),
      ['        this->m_pkHead = 0;'])
check('TPL1 a local declared with a template type keeps its name',
      run(s1.r_dependent_base_members, 'template <class C> struct Base { C* buffer; };\ntemplate <class C> struct Of : Base<C>\n{\n'
          '    void f(C& fb)\n    {\n        B<C, T>* buffer = &fb;\n        buffer->open();\n    }\n};\n'),
      ['B<C, T>* buffer = &fb;', '        buffer->open();'], ['this->buffer'])
check('TPL1 non-dependent base: untouched',
      run(s1.r_dependent_base_members, 'struct Base { int size; };\ntemplate<class T> struct D : Base {\n'
          ' void f() { size = 0; }\n};\n'), (), ['this->'])

# ---- STL1
check('STL1 MSVC library internals get fnvnx_msvcstl.h',
      run(s1.r_msvc_library_internals, 'void f(int* p) { _SCL_SECURE_VALIDATE(p != 0); g(std::_Checked_base(p)); }\n'),
      ['#include "fnvnx_msvcstl.h" /* fnv-nx STL1 */\n'])
check('STL1 a unit with its own copies: untouched',
      run(s1.r_msvc_library_internals, '#define _SCL_SECURE_VALIDATE(c) (void)0\nvoid f(int* p) { _SCL_SECURE_VALIDATE(p); }\n'),
      (), ['fnvnx_msvcstl.h'])
_STL1_AUX = ('#include <vector>\nstruct Store : std::_Container_base_aux_alloc_real< std::allocator<int*> > {\n'
             '    typedef ::std::vector<int*>::const_iterator CIt;\n    typedef std::vector<int*, std::allocator<int*> >::iterator It;\n'
             '    typedef std::vector<std::pair<int, int> >::const_iterator PIt;\n'
             '    std::allocator<int*> _Alval;\n    int** first; int** last; int** limit;\n'
             '    Store(std::allocator<int*> a) : std::_Container_base_aux_alloc_real< std::allocator<int*> >(a), _Alval(a) {}\n'
             '    CIt begin() const { return CIt(first, this); }\n'
             '    int** copy(CIt a, CIt b, int** out) { return stdext::unchecked_uninitialized_copy(a, b, out, _Alval); }\n'
             '    void destroy() { std::_Destroy_range(first, last, _Alval); }\n};\nstd::vector<int> plain;\n')
got = run(s1.r_msvc_library_internals, _STL1_AUX)
check('STL1 AUX: a container derived from _Container_base_aux_alloc_real gets the AUX part and MSVC iterator names', got,
      ['#define FNVNX_MSVCSTL_AUX 1 /* fnv-nx STL1 */\n#include "fnvnx_msvcstl.h"\n#include <vector>\n',
       'typedef ::std::_Vector_const_iterator< int*, ::std::allocator< int* > > CIt;',
       'typedef ::std::_Vector_iterator< int*, std::allocator<int*> > It;',
       'typedef ::std::_Vector_const_iterator< std::pair<int, int>, ::std::allocator< std::pair<int, int> > > PIt;',
       'std::vector<int> plain;'])
check('STL1 AUX: a unit with its own _Container_base_aux copy gets no AUX part',
      run(s1.r_msvc_library_internals, 'namespace std { class _Container_base_aux { }; }\n' + _STL1_AUX), (),
      ['FNVNX_MSVCSTL_AUX', '_Vector_const_iterator<'])

# ---- SAL1 class attributes
check('SAL1 drops the attributes of the annotation classes',
      run(s1.r_sal_attributes, 'enum A { N };\n[repeatable][source_annotation_attribute(Parameter)] struct PreAttribute\n{\n};\n'
          '[repeatable]\n[source_annotation_attribute(Parameter)]\nstruct PostAttribute;\n'),
      ['enum A { N };\nstruct PreAttribute\n', '};\n\n\nstruct PostAttribute;'], ['[repeatable]', 'source_annotation'])

# ---- LINK1 declarator lists
check('LINK1 C-linkage declarator list over two lines',
      run(s1.r_c_data_linkage, 'extern "C" char str_a[], str_b[],\n    str_c[];\nextern "C" int f(int);\n'),
      ['extern char str_a[], str_b[],\n    str_c[];', 'extern "C" int f(int);'])

# ---- SSE3 / SSE1 forms
check('SSE3 __m64 members', prepare.r_sse_members('x.cpp', 'short s = v.m64_i16[2];\n', Counter()), ['((short*)&(v))[2]'])
check('SSE1 local struct __m128 removed',
      prepare.r_sse_local_types('x.cpp', 'namespace Q {\nstruct __declspec(intrin_type) __declspec(align(16)) __m128\n{\n'
                                '    float x, y, z, w;\n};\n}\n', Counter()), ['SSE1'], ['float x, y'])

# ---- ANON1
check('ANON1 names an unnamed struct in a return type',
      run(s1.r_anonymous_return_types, 'struct G\n{\n    struct\n    {\n        float f;\n    }* Float();\n};\n'),
      ['struct fnvnx_anon_Float {', 'fnvnx_anon_Float* Float('], ['}* Float'])

# ---- ENUM1
_e = tempfile.mkdtemp()
os.makedirs(os.path.join(_e, 'src'))
with open(os.path.join(_e, 'src', 'a.h'), 'w') as _h:
    _h.write('enum DIALOGUE_TYPE;\nenum WIDE;\nenum WIDE { W = 0x80000000 };\n')
s1._OPAQUE_NAMES = None
got = run(s1.r_opaque_enums, 'enum DIALOGUE_TYPE;\nenum DIALOGUE_TYPE\n{\n    A = 0\n};\nenum WIDE;\nenum Other { B };\n',
          os.path.join(_e, 'src', 'b.cpp'))
s1._OPAQUE_NAMES = None
check('ENUM1 opaque enum gets int as its base, and so does its definition', got,
      ['enum DIALOGUE_TYPE FNVNX_ENUM_INT;', 'enum DIALOGUE_TYPE : int\n{', 'enum WIDE FNVNX_ENUM_INT;', 'enum Other { B };'])
s1._OPAQUE_NAMES = None
check('ENUM1 a definition int cannot hold keeps its form',
      run(s1.r_opaque_enums, 'enum WIDE { W = 0x80000000 };\n', os.path.join(_e, 'src', 'c.cpp')), ['enum WIDE { W'])
s1._OPAQUE_NAMES = None

# ---- DECL1-NSADL: a namespace's own C++ copy of a CRT name stays its own; argument-dependent lookup must not reach ::name
_st = Counter()
got = prepare.r_platform_protos('/x/src/ns.cpp', 'int strcpy_s(char* d, unsigned int n, const char* s);\n'
                                'namespace N {\nstruct S : Base { operator const char*() const; };\n'
                                'int strcpy_s(char* d, unsigned int n, const char* s);\n'
                                'void f(char* b, S& s) { strcpy_s(b, 4, s); if (!strcpy_s(b, 4, "x")) return; p->strcpy_s(b);'
                                ' N::strcpy_s(b, 4, s); // strcpy_s(b)\n}\n}\nvoid g(char* b) { strcpy_s(b, 4, "y"); }\n', _st)
check('DECL1-NSADL: unqualified calls in the namespace keeping its own prototype are parenthesized', got,
      ['{ (strcpy_s)(b, 4, s); if (!(strcpy_s)(b, 4, "x")) return; p->strcpy_s(b); N::strcpy_s(b, 4, s); // strcpy_s(b)',
       'int strcpy_s(char* d, unsigned int n, const char* s);\nvoid f', 'void g(char* b) { strcpy_s(b, 4, "y"); }'])
check('DECL1-NSADL counted', str(_st['DECL1-NSADL']), ['2'])

# ---- DECL1 math: the decomp's float / double declarations
got = prepare.r_platform_protos('/x/src/m.cpp', 'extern "C" double __cdecl sqrt(double);\nfloat __cdecl floor(float);\n'
                                'namespace N {\nextern "C" double fabs(double);\n}\nfloat g(float x) { return sqrt(x); }\n',
                                Counter())
check('DECL1 declares the C math function itself (no <math.h> overloads), keeps a float overload of the decomp', got,
      ['#include "fnvnx_cmath/fabs.h" /* fnv-nx DECL1 */\n#include "fnvnx_cmath/sqrt.h" /* fnv-nx DECL1 */\n'
       'float __cdecl floor(float);\nnamespace N {\nextern "C" double fabs(double);\n}'], ['<math.h>', 'using ::fabs'])
# in a namespace the C function stays alone: a using-declaration would bring <math.h>'s float overload in (S11)
_st = Counter()
got = prepare.r_platform_protos('/x/src/n.cpp', '#include <math.h>\nnamespace N {\nextern "C" double __cdecl fabs(double);\n'
                                'float f(float x) { return (float)fabs(x); }\n}\n', _st)
check('DECL1 keeps a namespace\'s extern "C" prototype of the C math function, header on top', got,
      ['#include "fnvnx_cmath/fabs.h" /* fnv-nx DECL1 */\n#include <math.h>\nnamespace N {\nextern "C" double __cdecl fabs(double);'],
      ['using ::fabs'])
check('DECL1-MATHNS counted', str(_st['DECL1-MATHNS']), ['1'])
got = prepare.r_platform_protos('/x/src/s.cpp', 'namespace N {\nextern "C" {\ndouble __cdecl fabs([SA_Pre(Null=SA_No)]'
                                '[SA_Pre(Deref=1,Valid=SA_Yes,Access=SA_Read)]double _X);\n}\n}\n', Counter())
check('DECL1 reads the C signature through attribute SAL (commas inside [...])', got,
      ['#include "fnvnx_cmath/fabs.h" /* fnv-nx DECL1 */', 'double __cdecl fabs([SA_Pre(Null=SA_No)]'], ['using ::fabs'])
# where a translation unit of the file includes <math.h>, the decomp's float overload is MSVC's <math.h> one: dropped
import json as _json
import tempfile
_m = tempfile.mkdtemp()
os.makedirs(os.path.join(_m, 'src')); os.makedirs(os.path.join(_m, 'build'))
_json.dump({'units': [{'source': 'src/u.cpp', 'symbols': {'_floor': '0x00406cc0'}},
                      {'source': 'src/v.cpp', 'symbols': {}}]}, open(os.path.join(_m, 'build', 'units.json'), 'w'))
open(os.path.join(_m, 'src', 'u_decls.h'), 'w').write('extern "C" double floor(float);\n')
open(os.path.join(_m, 'src', 'u.cpp'), 'w').write('#include "u_decls.h"\nbool f(float a, float b) { return floor(a) != floor(b); }\n')
open(os.path.join(_m, 'src', 'v_decls.h'), 'w').write('float cos(float);\n')
open(os.path.join(_m, 'src', 'v.cpp'), 'w').write('#include <math.h>\n#include "v_decls.h"\n')
_saved = (prepare._GAME_FILES, prepare._INC_GRAPH, dict(prepare._MATH_SEEN), dict(prepare._GAME_FLOAT_RET))
prepare._GAME_FILES, prepare._INC_GRAPH = None, None
prepare._MATH_SEEN.clear(); prepare._GAME_FLOAT_RET.clear()
_h = os.path.join(_m, 'src', 'u_decls.h')
got = prepare.r_game_bound_names(_h, open(_h).read(), Counter())
got2 = prepare.r_game_bound_names(os.path.join(_m, 'src', 'u.cpp'), open(os.path.join(_m, 'src', 'u.cpp')).read(), Counter())
_v = os.path.join(_m, 'src', 'v_decls.h')
got3 = prepare.r_platform_protos(_v, open(_v).read(), Counter())
prepare._GAME_FILES, prepare._INC_GRAPH = _saved[0], _saved[1]
prepare._MATH_SEEN.clear(); prepare._MATH_SEEN.update(_saved[2])
prepare._GAME_FLOAT_RET.clear(); prepare._GAME_FLOAT_RET.update(_saved[3])
check('GAME1 renames a C-linkage float math prototype the unit binds, with the game function\'s float return',
      got + got2, ['extern "C" float fnvnx_game_floor(float); /* fnv-nx GAME1: the game function returns float */',
                   'return fnvnx_game_floor(a) != fnvnx_game_floor(b);'])
check('DECL1 drops the decomp\'s float overload where a unit includes <math.h>', got3,
      ['#include "fnvnx_cmath/cos.h" /* fnv-nx DECL1 */'], ['float cos(float)'])

# the one-function math headers DECL1 includes: one per name, declaring the C function
for _n in sorted(prepare.MATH_NAMES):
    _r, _ps = prepare.C_MATH_SIG[_n]
    _hp = os.path.join(os.path.dirname(os.path.abspath(prepare.__file__)), '..', 'shim', 'include', 'fnvnx_cmath', _n + '.h')
    if not os.path.exists(_hp) or 'extern "C" %s %s(%s) noexcept;' % (_r, _n, ', '.join(_ps)) not in open(_hp).read():
        check('fnvnx_cmath/%s.h declares the C function' % _n, '', ['x'])

# ---- DECL1 with a trailing comment
check('DECL1 drops a CRT prototype that ends in a // comment',
      prepare.r_platform_protos('/x/src/a.h', 'extern "C" char* __cdecl strchr(const char*, int);  // CRT strchr (x)\n',
                                Counter()), ['#include <string.h> /* fnv-nx DECL1 */'], ['__cdecl strchr'])

check('SSE1 elaborated __m128 references',
      prepare.r_sse_local_types('x.cpp', 'typedef union __m128 hkQuadReal;\nvoid f(struct __m128* p);\n', Counter()),
      ['typedef __m128 hkQuadReal;', 'void f(__m128* p);'])
got = prepare.r_sse_local_types('x.cpp', 'union __declspec(intrin_type) __declspec(align(16)) __m128\n{\n    struct\n    {\n'
                                '        float x, y, z, w;\n    } lanes;\n};\nfloat f(__m128 v, __m128* p) { return v.lanes.x '
                                '+ p->lanes . w + g(v).lanes.y; }\n', Counter())
check('SSE1 named lane struct of a removed local __m128: v.lanes.x -> element access', got,
      ['((float*)&(v))[0] + ((float*)(p))[3] + ((float*)&(g(v)))[1]; }'], ['lanes'])
got = prepare.r_sse_local_types('x.cpp', 'union __declspec(intrin_type) __m128\n{\n    struct { float x, y, z, w; } lanes;\n};\n'
                                'struct Q { int lanes; };\nfloat f(__m128 v) { return v.lanes.x; }\n', Counter())
check('SSE1 lane struct: left alone when the unit declares another member of that name', got, ['v.lanes.x'])
got = prepare.r_sse_local_types('x.cpp', 'struct __declspec(intrin_type) __declspec(align(16)) __m128 { float x, y, z, w; };\n'
                                'typedef __m128 Quad;\nstruct V { Quad q; float& at(int i) { return (&q.x)[i]; } };\n'
                                'struct B { __m128 a, lim, c; };\nstruct P { float x; } pt;\n'
                                'float f(const B* b, V v, __m128 r) { return b->lim.y + v.q.z + r.w + pt.x; }\n', Counter())
check('SSE1 lanes that were members of a removed struct __m128: q.x -> element access', got,
      ['(&((float*)&(q))[0])[i]', '((float*)&(b->lim))[1] + ((float*)&(v.q))[2] + ((float*)&(r))[3] + pt.x'])
got = prepare.r_sse_local_types('x.cpp', 'union __declspec(intrin_type) __m128\n{\n    struct\n    {\n        float x, y, z, w;\n'
                                '    };\n};\nstruct A { __m128 q; };\nstruct C { Pt q; };\n'
                                'float f(A a, C c) { return a.q.x + c.q.x; }\n', Counter())
check('SSE1 anonymous lane struct: left alone for a name also declared with another type', got,
      ['a.q.x + c.q.x'])
check('SSE3 element access behind a subscripted parenthesized operand',
      prepare.r_sse_members('x.cpp', 'float& at(int r, int c) { return (&c0)[c].q.m128_f32[r]; }\n', Counter()),
      ['return ((float*)&((&c0)[c].q))[r];'])
check('SSE2 drops each of several prototypes in one extern "C" block on one line',
      prepare.r_sse_local_protos('x.cpp', 'extern "C" { __m128 _mm_set_ps(float,float,float,float); __m128 '
                                 '_mm_setr_ps(float,float,float,float); __m128 _mm_load_ss(const float*); }\nint a;\n',
                                 Counter()), ['int a;'], ['_mm_', 'extern'])
check('SSE2 drops `T(_mm_x)(...)` prototypes (no space before the parenthesized name)',
      prepare.r_sse_local_protos('x.cpp', 'namespace P {\nextern "C"\n{\n    ::__m128(_mm_load_ss)(const float* p);\n'
                                 '    ::__m128(_mm_sub_ps)(::__m128 a, ::__m128 b);\n}\n}\n', Counter()),
      ['namespace P {\n\n}'], ['_mm_load_ss', 'extern'])
got = prepare.r_sse_local_protos('x.cpp', 'int a;\nstatic __m128 _mm_set1_ps(const float& f) {__m128 a=_mm_load_ss(&f);\n'
                                  'return _mm_shuffle_ps(a,a,0);}\nstruct S { __m128 _mm_x(int) { return 0; } };\nint b;\n',
                                  Counter())
check('SSE2 drops a local intrinsic definition at namespace scope, same line count', got,
      ['int a;\n/* fnv-nx SSE2: local intrinsic definition removed */\n\nstruct S { __m128 _mm_x(int) { return 0; } };'],
      ['_mm_set1_ps'])
check('SSE4 (_mm_x)( after a name keeps them apart',
      prepare.r_sse_qualified('x.cpp', 'T(_mm_load_ss)(p); f((_mm_add_ps)(a, b));\n', Counter()),
      ['T _mm_load_ss(p); f(_mm_add_ps(a, b));'])
check('FALL1 _CRT_ALIGN(16) in a union head is a class head, not a function',
      run(s1.r_falloff_traps, 'typedef union __declspec(intrin_type) _CRT_ALIGN(16) Q\n{\n    float f[4];\n} Q;\n'), (), [TRAP])
check('SSE1 qualified elaborated __m128 references (`union ::__m128`)',
      prepare.r_sse_local_types('x.cpp', 'typedef union ::__m128 hkQuadReal;\nunion :: __m128;\nvoid f(struct N::__m128i* p);\n'
                                'union __m128_like { int a; };\n', Counter()),
      ['typedef __m128 hkQuadReal;\n\nvoid f(__m128i* p);', 'union __m128_like { int a; };'], ['::'])

check('DECL1 drops a prototype whose head is on the line above',
      prepare.r_platform_protos('/x/src/a.cpp', 'int a;\nextern "C" __declspec(dllimport) HANDLE\n    __stdcall CreateThread(void*, '
                                'unsigned long, LPTHREAD_START_ROUTINE, void*, unsigned long, unsigned long*);\nvoid g();\n',
                                Counter()), ['int a;\n#include "fnvnx_win32.h" /* fnv-nx DECL1 */\n\nvoid g();'], ['HANDLE'])

# ---- ELAB1
check('ELAB1 elaborated uses of a class named like a Win32 function',
      run(s1.r_function_named_classes, 'struct HeapAlloc\n{\n    int n;\n};\nvoid f(HeapAlloc* a, HeapAlloc& b);\n'
          'void* g() { return HeapAlloc(0, 0, 4); }\n'),
      ['void f(struct HeapAlloc* a, struct HeapAlloc& b);', 'return HeapAlloc(0, 0, 4);', 'struct HeapAlloc\n{'])

check('DECL1 drops a prototype with several __declspec',
      prepare.r_platform_protos('/x/src/a.h', 'extern "C" __declspec(dllimport) __declspec(nothrow) __declspec(noalias) long '
                                '__stdcall InterlockedIncrement(long*);\n', Counter()),
      ['#include "fnvnx_win32.h" /* fnv-nx DECL1 */'], ['noalias'])

check('SSE1 MSVC union initializer of a __m128',
      prepare.r_sse_local_types('x.cpp', 'typedef __m128 Quad;\nstatic const __m128 a = {{-0.0f, 1.0f, 2.0f, 3.0f}};\n'
                                'static const Quad b = { { 0.5f, 0.5f, 0.5f, 0.5f } };\n', Counter()),
      ['__m128 a = {-0.0f, 1.0f, 2.0f, 3.0f};', 'Quad b = { 0.5f, 0.5f, 0.5f, 0.5f };'])

check('ELAB1 renames a typedef named like a Win32 function',
      run(s1.r_function_named_classes, 'typedef unsigned int(__fastcall* VirtualQuery)(void* object);\n'
          'unsigned int f(void* p) { return ((VirtualQuery)(*(void***)p)[3])(p); }\n'),
      ['(__fastcall* fnvnx_t_VirtualQuery)(void* object);', '((fnvnx_t_VirtualQuery)(*(void***)p)[3])(p)'])

check('PTR1-DEREF leaves the compare of a pointee (int* p: *p <= 0 is signed), keeps pointer compares',
      run(s1.r_pointer_zero_compares, 'bool f(int* type, char** pp, char* q)\n{\n    if (*type > 0 && *type <= 3) return q > 0;\n'
          '    return *pp > 0 || a * q <= 0;\n}\n'),
      ['if (*type > 0 && *type <= 3) return q != 0;', 'return *pp != 0'], ['*type != 0', '*type == 0'])

# ---- GOTO1
got = run(s1.r_goto_scalar_inits, 'int f(V& v, int e)\n{\n    if (e) goto out;\n    {\n        float dw = v.d(3), di = v.d(e);\n'
          '        int n = 4;\n        char *p = 0, *q = p;\n        return (int)(dw + di) + n + (p == q);\n    }\nout:\n    return 0;\n}\n')
check('GOTO1 splits declarator lists declarator by declarator', got,
      ['float dw, di; dw = v.d(3); di = v.d(e);', 'int n; n = 4;', 'char *p, *q; p = 0; q = p;'])
got = run(s1.r_goto_scalar_inits, 'int g(const H* h, int e)\n{\n    if (e) goto out;\n    {\n        const int n = h->n;\n'
          '        __m128 ang = _mm_load_ss(&h->f);\n        const ns::Hdr* hd = h->hd;\n        static const int k = 3;\n'
          '        return n + k + (hd != 0);\n    }\nout:\n    return 0;\n}\n')
check('GOTO1 also splits SSE vectors, const scalars (dropping their const) and pointers to const', got,
      ['int n; n = h->n;', '__m128 ang; ang = _mm_load_ss(&h->f);', 'const ns::Hdr* hd; hd = h->hd;',
       'static const int k = 3;'])
got = run(s1.r_goto_scalar_inits, 'int g(H* h, int e)\n{\n    const int slots = 4;\n    const unsigned m = (1u << 3) - 1;\n'
          '    const int n = h->n;\n    const float f = 4;\n    int a[slots];\n    if (e) goto out;\n    a[0] = n & m;\n'
          '    return a[0] + (int)f;\nout:\n    return 0;\n}\n')
check('GOTO1-CONST leaves a const integer literal no goto crosses, splits the rest', got,
      ['    const int slots = 4;', '    const unsigned m = (1u << 3) - 1;', 'int n; n = h->n;', 'float f; f = 4;'],
      ['static const'])
got = run(s1.r_goto_scalar_inits, 'int g(H* h, int e)\n{\n    if (e) goto out;\n    const int slots = 4;\n'
          '    { const int k = 2; h->n = k; }\n    int a[slots];\n    a[0] = h->n;\nout:\n    return e ? 0 : slots;\n}\n')
check('GOTO1-CONST: a crossed const integer literal becomes static const (still a constant)', got,
      ['    static const int slots = 4;', '{ const int k = 2; h->n = k; }'])

# ---- GOTO2
got = run(s1.r_goto_crossed_scopes, 'int f(const P* p, int n)\n{\n    if (n < 2) goto out;\n    Cmp cmp = {0};\n'
          '    Vec& v = p->v;\n    sort(p, n, cmp, v);\nout:\n    return n;\n}\n')
check('GOTO2 (b) closes the crossed scopes before the label', got,
      ['    { Cmp cmp = {0};\n    Vec& v = p->v;\n    sort(p, n, cmp, v);\n} out:\n    return n;'])
got = run(s1.r_goto_crossed_scopes, 'int f(const P* p, int n)\n{\n    if (n < 2) goto out;\n    Cmp cmp = {0};\n'
          '    sort(p, n, cmp);\nout:\n    return cmp.k + n;\n}\n')
check('GOTO2 (b) keeps a scope that is used after the label', got, ['    Cmp cmp = {0};\n'], ['{ Cmp'])
got = run(s1.r_goto_crossed_scopes, 'static float dot(V a, V b) { return a.x * b.x; }\n'
          'bool f(V a, V b, float r)\n{\n    if (r < 0) goto miss;\n    const float& d = dot(a, b);\n'
          '    if (d > r) { miss: return false; }\n    return d < 2 * r;\n}\n')
check('GOTO2 (a) copies a const scalar reference to a by-value result', got, ['float d; d = dot(a, b);'],
      ['const float&'])
got = run(s1.r_goto_crossed_scopes, 'static const float& dot(V a, V b);\n'
          'bool f(V a, V b, float r)\n{\n    if (r < 0) goto miss;\n    const float& d = dot(a, b);\n'
          '    if (d > r) { miss: return false; }\n    return d < 2 * r;\n}\n')
check('GOTO2 (a) leaves a reference to a returned reference', got, ['const float& d = dot(a, b);'])
got = run(s1.r_goto_crossed_scopes, 'R* f(R* res, const S* s, int op)\n{\n    switch (op) {\n'
          '    case 1: if (s->lo > 3) goto reject; break;\n    case 2: {\n        Res a(false), b(false);\n'
          '        if (s->hi) { a = g(s); } else { reject: res->v = 0; return res; }\n        res->v = a.v | b.v;\n'
          '    }\n    }\n    return res;\n}\n')
check('GOTO2 (c) clones a straight return tail into the crossing goto', got,
      ['case 1: if (s->lo > 3) { res->v = 0; return res; } break;', 'reject: res->v = 0; return res;'], ['goto reject'])
got = run(s1.r_goto_crossed_scopes, 'R* f(R* res, const S* s, int op)\n{\n    R* tmp = 0;\n    switch (op) {\n'
          '    case 1: if (s->lo > 3) goto reject; break;\n    case 2: {\n        Res a(false), b(false);\n'
          '        if (s->hi) { a = g(s); } else { reject: tmp->v = 0; return tmp; }\n        res->v = a.v | b.v;\n'
          '    }\n    }\n    return res;\n}\n')
check('GOTO2 (c) does not clone a tail that names a local', got, ['goto reject;'])
got = run(s1.r_goto_crossed_scopes, 'int f(int n)\n{\n    Obj o(n);\n    if (n) goto out;\n    o.run();\nout:\n'
          '    return n;\n}\n')
check('GOTO2 leaves declarations before the goto alone', got, ['    Obj o(n);\n    if (n) goto out;'], ['{ '])

# ---- ANON3
got = run(s1.r_local_anonymous_unions, 'void f(E* e)\n{\n    for (;;)\n    {\n        union\n        {\n            Stream st;\n'
          '            struct\n            {\n                Body *a, *b;\n                float w[2];\n            };\n'
          '        };\n        st.read(e); g(a, b, w[1]);\n    }\n}\nstruct K { union { int i; struct { short lo, hi; }; }; };\n')
check('ANON3 puts a local anonymous union with an anonymous struct in a struct, same lines', got,
      ['        struct fnvnx_anon_u0 { union\n', '        }; } fnvnx_anon_v0; decltype(fnvnx_anon_v0.a)& a = fnvnx_anon_v0.a; '
       'decltype(fnvnx_anon_v0.b)& b = fnvnx_anon_v0.b; decltype(fnvnx_anon_v0.w)& w = fnvnx_anon_v0.w; '
       'decltype(fnvnx_anon_v0.st)& st = fnvnx_anon_v0.st;\n        st.read(e);',
       'struct K { union { int i; struct { short lo, hi; }; }; };'])
check('ANON3 leaves a local anonymous union without an anonymous struct',
      run(s1.r_local_anonymous_unions, 'void f() { union { int i; float x; }; i = 1; }\n'), ['void f() { union { int i;'],
      ['fnvnx_anon'])

# ---- MSCV1
import shutil as _shutil
_m = tempfile.mkdtemp()
os.makedirs(os.path.join(_m, 'src'))
os.makedirs(os.path.join(_m, 'build'))
with open(os.path.join(_m, 'build', 'units.json'), 'w') as _h:
    _h.write(_json.dumps({'units': [{'source': 'src/a.cpp', 'compiler': 'rtm'}, {'source': 'src/b.cpp', 'compiler': 'sp1'}]}))
_src = '#if _MSC_VER >= 1500\nint v;\n#endif\nint f() { if (_MSC_FULL_VER >= 150030729) return 1; return _MSC_VER; } // _MSC_VER\n'
check('MSCV1 an rtm unit sees cl 15.00.21022 in code, #if lines and comments stay',
      run(s1.r_msc_version_values, _src, os.path.join(_m, 'src', 'a.cpp')),
      ['#if _MSC_VER >= 1500\n', 'if (150021022 >= 150030729) return 1; return 1500; } // _MSC_VER'])
check('MSCV1 an sp1 unit sees cl 15.00.30729', run(s1.r_msc_version_values, _src, os.path.join(_m, 'src', 'b.cpp')),
      ['if (150030729 >= 150030729)'])
check('MSCV1 a file that is no unit is left alone', run(s1.r_msc_version_values, _src, os.path.join(_m, 'src', 'c.h')),
      ['if (_MSC_FULL_VER >= 150030729)'])
_shutil.rmtree(_m, ignore_errors=True)

# ---- QUAD1
_q = ('struct __declspec(align(16)) PQ { float x, y, z, w; operator __m128() const { return *(const __m128*)this; } };\n'
      'class SR { public: SR(const __m128& v) { m = v; } SR(float f); private: PQ m; };\n'
      'struct V4 { PQ m_q; SR at(int i) const { switch (i) { case 0: return m_q; default: return _mm_set1_ps(i); } }\n'
      '  PQ raw() const { return m_q; } };\n')
got = run(s1.r_quad_returns, _q)
check('QUAD1 writes out the conversion to __m128 for a class built from __m128', got,
      ['case 0: return (__m128)m_q;', 'PQ raw() const { return m_q; }'])
check('QUAD1 leaves a return type that is constructible from the class itself',
      run(s1.r_quad_returns, _q.replace('SR(float f);', 'SR(const PQ& q);')), ['case 0: return m_q;'])

# ---- TAIL1
check('TAIL1 writes typename in front of a qualified (possibly dependent) base',
      run(s1.r_msvc_base_layout, 'template <class V, class B> struct T : public List<V, B>::Item\n{\n    int a;\n};\n'
          'struct U : N::Base\n{\n};\nstruct W : Base<N::X>\n{\n};\n'),
      ['FNVNX_MSVC_BASE_END(typename List<V, B>::Item)', 'FNVNX_MSVC_BASE_END(typename N::Base)',
       'FNVNX_MSVC_BASE_END(Base<N::X>)'])
check('TAIL1 no typename for a template base whose arguments (over two lines) hold the ::',
      run(s1.r_msvc_base_layout, 'template <class T, class A>\nclass I : public R<T, typename A::d,\n'
          '                        typename A::p>\n{\n};\ntemplate <class T> struct J : ::G<T>::K<int>\n{\n};\n'),
      ['FNVNX_MSVC_BASE_END(R<T, typename A::d,\n                        typename A::p>)',
       'FNVNX_MSVC_BASE_END(typename ::G<T>::K<int>)'], ['(typename R<'])
check('TAIL1 names the last base without its access specifier',
      run(s1.r_msvc_base_layout, 'class D : public NiMemObject\n{\n  public:\n    int x;\n};\n'
          'struct E : B, public T<int, char>\n{\n    int y;\n};\n'),
      ['{ FNVNX_MSVC_BASE_END(NiMemObject)', '{ FNVNX_MSVC_BASE_END(T<int, char>)'], ['(public'])

# ---- ANON2
got = run(s1.r_anonymous_aggregate_members,
          'struct String\n{\n    char* text;\n    ~String();\n    unsigned int Len();\n};\nstruct Extra\n{\n    union\n    {\n'
          '        struct\n        {\n            int value;\n            String activationText;\n        };\n        int other;\n'
          '    };\n};\nvoid f(Extra* slot) { if (slot->activationText.Len()) slot->value = 0; }\n')
check('ANON2 raw storage for a member with a destructor in an anonymous struct of a union', got,
      ['alignas(String) unsigned char activationText[sizeof(String)]; /* fnv-nx ANON2 */',
       'if ((*(String*)(slot->activationText)).Len())', 'int value;'])

# the member declared in a header, used in the unit; another class of the unit has its own member of that name
_a = tempfile.mkdtemp()
os.makedirs(os.path.join(_a, 'src', 'anim'))
open(os.path.join(_a, 'src', 'anim', 'interp_unit.h'), 'w').write(
    'class NiPoint3\n{\n  public:\n    float x;\n    NiPoint3& operator=(const NiPoint3& o);\n};\n'
    'class NiPoint3Interpolator\n{\n  public:\n    union\n    {\n        struct\n        {\n            NiPoint3 m_kValue;\n'
    '            int m_uiLast;\n        };\n        int state;\n    };\n    bool Update(NiPoint3& value);\n};\n')
_unit = os.path.join(_a, 'src', 'anim', 'interp.cpp')
_saved, s1._ANON2_NAMES = s1._ANON2_NAMES, None
got = run(s1.r_anonymous_aggregate_members,
          '#include "interp_unit.h"\n// fills m_kValue\nbool NiPoint3Interpolator::Update(NiPoint3& value)\n{\n'
          '    value = m_kValue;\n    return m_kValue.x != 0;\n}\nstruct Shell : public NiPoint3Interpolator\n{\n'
          '    bool Eq(NiPoint3Interpolator* other) { return &m_kValue == &other->m_kValue; }\n};\n'
          'class Other\n{\n  public:\n    NiPoint3 m_kValue;\n    void CloneTo(Other* pkDest);\n};\n'
          'void Other::CloneTo(Other* pkDest)\n{\n    pkDest->m_kValue = m_kValue;\n}\n', _unit)
s1._ANON2_NAMES = _saved
check('ANON2 converts the uses in a file that includes the member, only where they are that member', got,
      ['// fills m_kValue\n', 'value = (*(NiPoint3*)(m_kValue));', 'return (*(NiPoint3*)(m_kValue)).x != 0;',
       'return &(*(NiPoint3*)(m_kValue)) == &(*(NiPoint3*)(other->m_kValue));', '    NiPoint3 m_kValue;\n',
       'pkDest->m_kValue = m_kValue;'])

# ---- INC1
_i = tempfile.mkdtemp()
os.makedirs(os.path.join(_i, 'src', 'ai'))
open(os.path.join(_i, 'src', 'ai', 'actor.h'), 'w').write('// unit header\n')
check('INC1 spells an include as the local file is spelled',
      run(s1.r_include_case, '#include "Actor.h"\n#include "Other.h"\n#include <Windows.h>\n', os.path.join(_i, 'src', 'ai', 'actor.cpp')),
      ['#include "actor.h"', '#include "Other.h"', '#include <Windows.h>'])

# ---- PTR1
check('PTR1 ordered compares of a function / pointer with 0',
      run(s1.r_pointer_zero_compares, '__int64 GetTime64(__int64* p);\nvoid f(Q* q)\n{\n    int n = 3;\n    Q* c = q;\n'
          '    if (!(GetTime64 > 0)) return;\n    if (c > 0 && n > 0 && q <= 0) return;\n}\n'),
      ['!(GetTime64 != 0)', 'c != 0 && n > 0 && q == 0'])

# ---- REC1-RETARGET: a placed function's recipe symbol moves to the recon's new decoration, only from the expected old
_u = {'source': 'src/x.cpp', 'functions': [{'address': '0x008d1e50', 'symbol': '?F@S@@QAEMXZ', 'size': 31}],
      'symbols': {'?F@S@@QAEMXZ': '0x008d1e50'}}
_r1 = s1.recon_retarget(_u, {'address': '0x008D1E50', 'old': '?F@S@@QAEMXZ', 'new': '?F@S@@QAEPAXXZ'})
check('REC1-RETARGET moves the placed symbol', repr((_r1, _u['functions'][0]['symbol'], sorted(_u['symbols']))),
      ["(1, '?F@S@@QAEPAXXZ', ['?F@S@@QAEPAXXZ'])"])
_r2 = s1.recon_retarget(_u, {'address': '0x008d1e50', 'old': '?F@S@@QAEMXZ', 'new': '?G@@'})
check('REC1-RETARGET leaves another old symbol alone', repr((_r2, _u['functions'][0]['symbol'])), ["(0, '?F@S@@QAEPAXXZ')"])

# ---- STAND1 (needs a units.json: a small tree of its own; the symbol cache is reset around it)
import json as _json
_s = tempfile.mkdtemp()
os.makedirs(os.path.join(_s, 'src', 'staging')); os.makedirs(os.path.join(_s, 'build'))
_json.dump({'units': [{'source': 'src/staging/u.cpp', 'symbols': {'??0FUN_00632ec0@@QAE@XZ': '0x00632ec0',
                                                                 '?Helper@@YAHXZ': '0x00401230', '?Copy@@YAPAHU?$It@H@ns@@0@Z': '0x00401230',
                                                                 '?Make@V@@QAEPAU1@H@Z': '0x00401230'}}]},
           open(os.path.join(_s, 'build', 'units.json'), 'w'))
_saved, s1._UNIT_SYMBOLS = s1._UNIT_SYMBOLS, None
got = run(s1.r_stand_in_definitions,
          'struct FUN_00632ec0\n{\n    unsigned char m_data[0x114];\n    FUN_00632ec0();\n};\nFUN_00632ec0::FUN_00632ec0()\n{}\n'
          'inline int Helper()\n{\n    return 0;\n}\nvoid f() { new FUN_00632ec0; Helper(); }\n'
          'int* __cdecl Copy(ns::It<int> a,\n    ns::It<int> b)\n{\n    return 0;\n}\n__declspec(noinline) V*\nV::Make(int n)\n{\n    return 0;\n}\n'
          'namespace fnv_00632ec0 {\nstruct FUN_00632ec0 { FUN_00632ec0(); };\n// @fnv 0x00632ec0\n'
          'FUN_00632ec0::FUN_00632ec0()\n{\n    ((int*)this)[0] = 1;\n}\n}\n// @fnv 0x00401230\nint Other()\n{\n    return 1;\n}\n',
          os.path.join(_s, 'src', 'staging', 'u.cpp'))
s1._UNIT_SYMBOLS = _saved
check('STAND1 drops the stand-ins of tagged addresses', got,
      ['/* fnv-nx STAND1: FUN_00632ec0::FUN_00632ec0 is the function at the tagged address */\n',
       'int Helper(); /* fnv-nx STAND1 */', '((int*)this)[0] = 1;', 'void f() { new FUN_00632ec0; Helper(); }',
       'int* __cdecl Copy(ns::It<int> a,\n    ns::It<int> b); /* fnv-nx STAND1 */', '/* fnv-nx STAND1: V::Make is'],
      ['__declspec(noinline) V*\n/*', 'FUN_00632ec0::FUN_00632ec0()\n{}'])

# STAND1 keeps a tagged definition whose own name the unit binds to its address, with pragmas, a type declaration
# or a whole type between the tag and the head, or a head over two lines (R6 0x004ab230, 0x0087ce70, 0x00660400, 0x00a8a200;
# R1 0x0046ffd0); an untagged stand-in after such a tagged function still goes
_s = tempfile.mkdtemp()
os.makedirs(os.path.join(_s, 'src', 'staging')); os.makedirs(os.path.join(_s, 'build'))
_json.dump({'units': [{'source': 'src/staging/u.cpp', 'symbols': {
    '?Get@Bip@@QAEPAHH@Z': '0x00401000', '?Map@@YAPAU?$M@H@@XZ': '0x00401010', '?Next@@YAPAU?$L@H@@PAU1@@Z': '0x00401020',
    '?Set@Cls@@QAEXXZ': '0x00401030', '?Stand@Cls@@QAEXXZ': '0x00401030', '?Pool@@YAXXZ': '0x00401060'}}]},
           open(os.path.join(_s, 'build', 'units.json'), 'w'))
_saved, s1._UNIT_SYMBOLS = s1._UNIT_SYMBOLS, None
got = run(s1.r_stand_in_definitions,
          '// @fnv 0x00401000\n#pragma optimize("", off)\n#pragma optimize("y", off)\nint* Bip::Get(int i)\n{\n    return p + i;\n}\n'
          '#pragma optimize("", on)\n// @fnv 0x00401010\nM<int>* __cdecl\n    Map()\n{\n    return &g;\n}\n'
          '// @fnv 0x00401020\ntemplate <class T> struct L;\nL<int>* Next(L<int>* a)\n{\n    return a->n;\n}\n'
          '// @fnv 0x00401030\ntemplate <> struct A<void*>\n{\n    static void __cdecl Free(void**);\n};\n'
          'void __thiscall Cls::Set()\n{\n    *(int*)this = 1;\n}\nvoid Cls::Stand()\n{\n}\n'
          '// @fnv 0x00401060\ntemplate <class T> struct P\n{\n    struct E\n    {\n        E* next;\n    };\n    static E* ms;\n};\n'
          'void Pool()\n{\n    pooled = 1;\n}\n',
          os.path.join(_s, 'src', 'staging', 'u.cpp'))
s1._UNIT_SYMBOLS = _saved
_saved, s1._UNIT_SYMBOLS = s1._UNIT_SYMBOLS, None
_json.dump({'units': [{'source': 'src/staging/u.cpp', 'symbols': {
    '?Find@@YAPAUA@@PAU1@H@Z': '0x00401040', '?Find@@YAPAUB@@PAU1@H@Z': '0x00401040',
    '?Pick@@YAHH@Z': '0x00401050', '?Pick@@YAHN@Z': '0x00401050'}}]}, open(os.path.join(_s, 'build', 'units.json'), 'w'))
got4 = run(s1.r_static_stand_ins,
           '// @fnv 0x00401040\nstatic __declspec(noinline) A* Find(A* a, int n)\n{\n    return a + n;\n}\n'
           'static __declspec(noinline) B* Find(B* b,\n                                    int n)\n{\n    return b->pick(n);\n}\n'
           '// @fnv 0x00401050\nstatic int Pick(int k)\n{\n    return k;\n}\nstatic int Pick(double d);\n'
           'static int Pick(double d)\n{\n    return 2;\n}\n', os.path.join(_s, 'src', 'staging', 'u.cpp'))
s1._UNIT_SYMBOLS = _saved
check('STAND4 an untagged static overload of a tagged address becomes an extern declaration (R6 0x00cb3d10)', got4,
      ['__declspec(noinline) B* Find(B* b,\n                                    int n); /* fnv-nx STAND4 0x00401040 */',
       'return a + n;', 'return 2;', 'static int Pick(double d);'], ['b->pick(n)'])
check('STAND1 keeps tagged definitions behind pragmas, declarations, a type or a two-line head', got,
      ['return p + i;', 'return &g;', 'return a->n;', '*(int*)this = 1;', 'pooled = 1;',
       '/* fnv-nx STAND1: Cls::Stand is the function at the tagged address */'], ['fnv-nx STAND1: Bip', 'Map();'])

# STAND1 keeps an untagged definition the unit's own units.json `functions` place, even when another definition
# carries the address's tag (R2: caravanmenu 0x0073b730)
_s = tempfile.mkdtemp()
os.makedirs(os.path.join(_s, 'src', 'staging')); os.makedirs(os.path.join(_s, 'build'))
_json.dump({'units': [{'source': 'src/staging/u.cpp',
                       'functions': [{'address': '0x0073b730', 'symbol': '?Run@Hand@@QAEXPAH@Z', 'size': 168}],
                       'symbols': {'?Run@Hand@@QAEXPAH@Z': '0x0073b730', '?Stand@Hand@@QAEXXZ': '0x0073b730'}}]},
           open(os.path.join(_s, 'build', 'units.json'), 'w'))
_saved, s1._UNIT_SYMBOLS = s1._UNIT_SYMBOLS, None
got = run(s1.r_stand_in_definitions,
          'struct Card\n{\n// @fnv 0x0073b730\n    virtual char* Run_card()\n    {\n        return 0;\n    }\n};\n'
          'struct Hand { void Run(int* p); void Stand(); };\nvoid Hand::Run(int* p)\n{\n    p[0] = 7;\n}\n'
          'void Hand::Stand()\n{\n}\n', os.path.join(_s, 'src', 'staging', 'u.cpp'))
s1._UNIT_SYMBOLS = _saved
check('STAND1 keeps a definition its unit places, whatever carries the tag', got,
      ['p[0] = 7;', '/* fnv-nx STAND1: Hand::Stand is the function at the tagged address */'],
      ['fnv-nx STAND1: Hand::Run'])
_json.dump({'units': [{'source': 'src/staging/u.cpp',
                       'functions': [{'address': '0x00b26260', 'symbol': '?Sort@@YAXPAH@Z', 'size': 40}],
                       'symbols': {'?Sort@@YAXPAH@Z': '0x00b26260', '?Sort@@YAXPAN@Z': '0x00b26260'}}]},
           open(os.path.join(_s, 'build', 'units.json'), 'w'))
_saved, s1._UNIT_SYMBOLS = s1._UNIT_SYMBOLS, None
got = run(s1.r_stand_in_definitions,
          '// @fnv 0x00b26260\nvoid Sort(int* p)\n{\n    p[0] = 1;\n}\nvoid Sort(double* p)\n{\n    p[0] = 2;\n}\n',
          os.path.join(_s, 'src', 'staging', 'u.cpp'))
s1._UNIT_SYMBOLS = _saved
check('STAND1 still drops the untagged overload of a tagged, placed name', got,
      ['p[0] = 1;', 'void Sort(double* p); /* fnv-nx STAND1 */'], ['p[0] = 2;'])

# ---- STAND2 (an address another unit defines; only units without auto-inlining, only non-inline, non-virtual)
_s = tempfile.mkdtemp()
os.makedirs(os.path.join(_s, 'src', 'staging')); os.makedirs(os.path.join(_s, 'build'))
_od, _o2 = ['/MT', '/Od', '/Oy-', '/GS'], ['/MT', '/O2', '/GS']
_syms = {'?Clear@G@@QAEX_N@Z': '0x00845000', '?Reset@G@@QAEXXZ': '0x00644000', '?Free@@YAHH@Z': '0x00845100',
         '?Keep@@YAHH@Z': '0x00845100', '?Loc@@YAHH@Z': '0x00845100', '?Vir@G@@UAEXXZ': '0x00845200',
         '?Mine@G@@QAEXXZ': '0x00644100', '?Two@G@@QAEXH@Z': '0x00845000', '?Two@G@@QAEXM@Z': '0x00644100'}
_json.dump({'units': [
    {'source': 'src/staging/u.cpp', 'flags': _od, 'symbols': _syms,
     'functions': [{'address': '0x00644000'}, {'address': '0x00644100'}]},
    {'source': 'src/staging/o2.cpp', 'flags': _o2, 'symbols': _syms, 'functions': []},
    {'source': 'src/staging/other.cpp', 'flags': _od, 'symbols': {},
     'functions': [{'address': '0x00845000'}, {'address': '0x00845100'}, {'address': '0x00845200'}]}]},
    open(os.path.join(_s, 'build', 'units.json'), 'w'))
_STAND2_SRC = ('struct G\n{\n    int* p;\n    void Clear(bool);\n    void Reset();\n    virtual void Vir();\n    void Mine();\n'
               '    void Two(int);\n    void Two(float);\n};\nvoid G::Clear(bool b)\n{\n    delete[] p;\n}\n'
               'int Free(int a)\n{\n    return a;\n}\ninline int Keep(int a)\n{\n    return a;\n}\n'
               'static int Loc(int a)\n{\n    return a;\n}\nvoid G::Vir()\n{\n}\nvoid G::Mine()\n{\n}\n'
               'void G::Two(int)\n{\n}\n// @fnv 0x00644000\nvoid G::Reset()\n{\n    p = 0;\n    Clear(true);\n}\n')
_saved = s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
got = run(s1.r_stand_in_definitions_elsewhere, _STAND2_SRC, os.path.join(_s, 'src', 'staging', 'u.cpp'))
got_o2 = run(s1.r_stand_in_definitions_elsewhere, _STAND2_SRC, os.path.join(_s, 'src', 'staging', 'o2.cpp'))
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = _saved
check('STAND2 drops a stand-in of an address another unit defines (/Od)', got,
      ['/* fnv-nx STAND2: G::Clear is the function at the tagged address */\n', 'int Free(int a); /* fnv-nx STAND2 */',
       'inline int Keep(int a)\n{', 'static int Loc(int a)\n{', 'void G::Vir()\n{', 'void G::Mine()\n{',
       'void G::Two(int)\n{', 'void G::Reset()\n{\n    p = 0;\n    Clear(true);'],
      ['delete[] p'])
check('STAND2 leaves units MSVC compiled with auto-inlining', got_o2, ['delete[] p', 'int Free(int a)\n{'], ['STAND2'])
check('STAND2 keeps the line count', 'lines %d' % got.count('\n'), ['lines %d' % _STAND2_SRC.count('\n')])

# ---- STAND4, lone static noinline stand-ins (W6-EQ: hkpcylindershape 0x00d57aa0 / 0x00d57b20; units.json of its own)
_s4 = tempfile.mkdtemp()
os.makedirs(os.path.join(_s4, 'src')); os.makedirs(os.path.join(_s4, 'build'))
_S4SYMS = {'?Zero@@YAMXZ': '0x00d571d0', '?Same@ns@@YAMXZ': '0x00d571d0', '?Tagged@@YAHXZ': '0x00401230',
           '?Mine@@YAHXZ': '0x00401240', '?Aligned@@YAHXZ': '0x00d571d0'}
_json.dump({'units': [
    {'source': 'src/u.cpp', 'flags': ['/MT', '/O2'], 'symbols': _S4SYMS, 'functions': [{'address': '0x00401230'}]},
    {'source': 'src/other.cpp', 'flags': ['/MT', '/Od'], 'symbols': {}, 'functions': [{'address': '0x00d571d0'}]}]},
    open(os.path.join(_s4, 'build', 'units.json'), 'w'))
_saved = (s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS)
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
got = run(s1.r_static_stand_ins,
          'static __declspec(noinline) float Zero()\n{\n    return 0.0f;\n}\n'
          'namespace ns {\nstatic __declspec(noinline) float Same()\n{\n    return G;\n}\n}\n'
          '// @fnv 0x00401230\nint Real()\n{\n    return 1;\n}\n'
          'static __declspec(noinline) int Tagged()\n{\n    return 2;\n}\n'
          'static __declspec(noinline) int Mine()\n{\n    return 3;\n}\n'
          'static __declspec(noinline) __declspec(align(16)) int Aligned()\n{\n    return 4;\n}\n'
          'static __declspec(noinline) int NoAddress()\n{\n    return 5;\n}\n',
          os.path.join(_s4, 'src', 'u.cpp'))
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = _saved
check('STAND4 a lone static noinline stand-in bound to an address another unit defines (any flags)', got,
      ['__declspec(noinline) float Zero(); /* fnv-nx STAND4 0x00d571d0 */',
       '__declspec(noinline) float Same(); /* fnv-nx STAND4 0x00d571d0 */',
       '__declspec(noinline) int Tagged(); /* fnv-nx STAND4 0x00401230 */'],
      ['return 0.0f', 'return G', 'return 2'])
check('STAND4 keeps a noinline stand-in whose address nobody else defines, other declspecs, unbound names', got,
      ['return 3;', 'return 4;', 'return 5;'])

# ---- STAND3 (a units.json of its own: an /Od unit, an /O2 unit and the decompiled addresses of a third)
_s3 = tempfile.mkdtemp()
os.makedirs(os.path.join(_s3, 'src')); os.makedirs(os.path.join(_s3, 'build'))
_STAND3_SYMS = {'??1Mem@@QAE@XZ': '0x0040c110', '??0Guard@@QAE@PAH@Z': '0x00528cb0', '?Helper@@YAHXZ': '0x00401230',
                '?Get@Box@@QAEHXZ': '0x00401240', '?Put@Box@@QAEHXZ': '0x00401240', '??1Mem@ns@@QAE@XZ': '0x00500000', '?Two@@YAHH@Z': '0x00401230',
                '?Two@@YAHM@Z': '0x00401240', '?Virt@Box@@UAEHXZ': '0x00401240', '??1Der@@QAE@XZ': '0x0040c110',
                '?Tagged@@YAHXZ': '0x00401230'}
_json.dump({'units': [
    {'source': 'src/od.cpp', 'flags': ['/MT', '/Od', '/Oy-', '/GS', '/EHsc', '/Gy'], 'symbols': _STAND3_SYMS,
     'functions': []},
    {'source': 'src/o2.cpp', 'flags': ['/MT', '/O2', '/GS', '/EHsc', '/Gy'], 'symbols': _STAND3_SYMS, 'functions': []},
    {'source': 'src/ob1.cpp', 'flags': ['/MT', '/Od', '/Oy-', '/GS', '/EHsc', '/Gy', '/Ob1'], 'symbols': _STAND3_SYMS,
     'functions': []},
    {'source': 'src/other.cpp', 'flags': ['/MT', '/Od'], 'symbols': {},
     'functions': [{'address': a, 'symbol': '?f@@YAXXZ', 'size': 4}
                   for a in ('0x0040c110', '0x00528cb0', '0x00401230', '0x00401240')]}]},
           open(os.path.join(_s3, 'build', 'units.json'), 'w'))
_STAND3_SRC = ('struct Mem\n{\n    int a;\n    ~Mem()\n    {\n        Free(this);\n    }\n};\n'
               'struct Guard\n{\n    int* p;\n    Guard(int* q) : p(q)\n    {\n        Lock(q);\n    }\n};\n'
               'inline int Helper()\n{\n    return 0;\n}\n'
               'struct Box\n{\n    int Get();\n    int Put();\n    virtual int Virt()\n    {\n        return 1;\n    }\n};\n'
               'inline int Box::Get()\n{\n    return 2;\n}\nint Box::Put()\n{\n    return 5;\n}\n'
               'namespace ns {\nstruct Mem\n{\n    ~Mem()\n    {\n        Free(this);\n    }\n};\n}\n'
               'int Two(int)\n{\n    return 3;\n}\n'
               'struct Base\n{\n    virtual ~Base();\n};\nstruct Der : Base\n{\n    ~Der()\n    {\n        Free(this);\n    }\n};\n'
               '// @fnv 0x00401230\nint Tagged()\n{\n    return 4;\n}\n'
               'template <class T> struct Tm\n{\n    ~Tm()\n    {\n        Free(this);\n    }\n};\n'
               'void use() { Mem m; Guard g(0); Helper(); }\n')
_saved = (s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS)
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
got = run(s1.r_inline_stand_ins, _STAND3_SRC, os.path.join(_s3, 'src', 'od.cpp'))
got_o2 = run(s1.r_inline_stand_ins, _STAND3_SRC, os.path.join(_s3, 'src', 'o2.cpp'))
got_ob1 = run(s1.r_inline_stand_ins, _STAND3_SRC, os.path.join(_s3, 'src', 'ob1.cpp'))
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = _saved
check('STAND3 in-class destructor bound to a decompiled address of another unit becomes a declaration', got,
      ['    ~Mem(); /* fnv-nx STAND3 0x0040c110 */\n\n\n\n};'], ['        Free(this);\n    }\n};\nstruct Guard'])
check('STAND3 constructor: declaration without its initializer list', got,
      ['    Guard(int* q); /* fnv-nx STAND3 0x00528cb0 */'], ['p(q)'])
check('STAND3 free inline function keeps a plain declaration', got,
      ['int Helper(); /* fnv-nx STAND3 0x00401230 */'], ['inline int Helper'])
check('STAND3 inline out-of-class member definition goes, a non-inline one is STAND2\'s', got,
      ['/* fnv-nx STAND3: Box::Get is the function at 0x00401240 */', 'int Box::Put()\n{\n    return 5;'], ['return 2;'])
check('STAND3 keeps: virtual, address no unit decompiles, two overloads at two addresses, tagged, template, '
      'destructor of a derived class', got,
      ['virtual int Virt()\n    {\n        return 1;', 'namespace ns {\nstruct Mem\n{\n    ~Mem()\n    {', 'int Two(int)\n{',
       '// @fnv 0x00401230\nint Tagged()\n{', 'template <class T> struct Tm\n{\n    ~Tm()\n    {',
       'struct Der : Base\n{\n    ~Der()\n    {'])
check('STAND3 line count unchanged', got, [], []) if got.count('\n') == _STAND3_SRC.count('\n') else \
    check('STAND3 line count unchanged', 'lines %d != %d' % (got.count('\n'), _STAND3_SRC.count('\n')), ['never'])
check('STAND3 leaves an /O2 unit alone (MSVC may inline there)', got_o2, [_STAND3_SRC], [])
check('STAND3 leaves an /Od /Ob1 unit alone (/Ob1 inlines in-class and inline definitions)', got_ob1, [_STAND3_SRC], [])

# ---- STR1 uses the declaration in scope
got = run(s1.r_string_array_address, 'extern char str_01082ab8[];\nvoid f() { g(&str_01082ab8); }\nnamespace N {\n'
          'extern char str_01082ab8;\nvoid h() { g(&str_01082ab8); }\n}\nextern "C" char str_a0000000[], str_b0000000[],\n'
          '    str_c0000000[];\nvoid k() { const char* p = str_c0000000; g(&str_c0000000); }\n')
check('STR1 rewrites only where the array is the declaration in scope', got,
      ['void f() { g(str_01082ab8); }', 'void h() { g(&str_01082ab8); }', 'g(str_c0000000); }'])

# ---- VTBL1
got = run(s1.r_stand_in_interfaces, 'int a;\nstruct FUN_00aa2230_BSNiAllocator\n{\n    virtual void m_virtual0();\n'
          '    FUN_00aa2230_BSNiAllocator();\n};\nint b;\n', '/x/src/staging/blk_00aa0000_o2.cpp')
check('VTBL1 BSNiAllocator gets its 10 slots, same line count', got,
      ['virtual void* Allocate(', 'virtual void Slot9();', 'AllocateZeroedMemory(size, alignment', '}\n\n\n\n\nint b;'],
      ['m_virtual0'])

got = run(s1.r_stand_in_interfaces, 'int a;\nstruct NiStandardAllocator\n{\n    static void* __cdecl operator new(size_t);\n'
          '    virtual void func();\n};\nint b;\n', '/x/src/gamebryo2.2/corelibs/nimain/nigpuprogramcache.cpp')
check('VTBL1 NiStandardAllocator gets its 10 slots, 7 / 8 Initialize / Shutdown, same line count', got,
      ['inline virtual void* Destroy(unsigned int flags);', 'FUN_00aa3150(p, event, size);',
       'virtual void Initialize() { fnv_00aa3430::AllocateStandardAllocator(); }',
       'virtual void Shutdown() { DestroySmallAllocPools(); }', 'virtual void Slot9();', '}\n\n\n\n\nint b;'],
      ['func()'])
got = run(s1.r_stand_in_interfaces, 'struct StdAllocator\n{\n    virtual ~StdAllocator();\n    virtual void* Allocate();\n};\n',
          '/x/src/gamebryo2.2/corelibs/nisystem/nistandardallocator.cpp')
check('VTBL1 StdAllocator: the destructor is MSVC slot 0, one plain slot', got,
      ['    virtual void* Destroy(unsigned int flags);   /* fnv-nx VTBL1', '    virtual void* Allocate();'], ['~StdAllocator'])

got = run(s1.r_stand_in_interfaces, 'struct FUN_00aa7400 : HeapBlockBase\n{\n    int m_1c;\n    HeapBlockLink m_20;\n'
          '    FUN_00aa7400(unsigned int a, unsigned int b);\n    virtual void v00();\n};\nint z;\n',
          '/x/src/staging/blk_00aa0000_od_ob1_variant.cpp')
check('VTBL1 the FUN_00aa7400 heap gets the AbstractHeap slots, same line count', got,
      ['virtual void v00(); /* fnv-nx VTBL1', 'virtual void v2();', 'virtual void v13(void* value); };', '\nint z;'])
check('VTBL1 FUN_00aa7400 keeps the line count', str(got.count('\n')), ['8'])

got = run(s1.r_stand_in_interfaces, 'class M : public MB\n{\n};\nclass A660A0Map : public A660A0MapBase\n{\n  public:\n'
          '    __forceinline A660A0Map() throw() : A660A0MapBase(59)\n    {}\n    virtual ~A660A0Map();\n};\nint z;\n',
          '/x/src/gamebryo2.2/corelibs/nimain/nistream.cpp')
check('VTBL1 A660A0Map: NiTMapBase slots 1-6 after the destructor, declared only', got,
      ['virtual ~A660A0Map(); /* fnv-nx VTBL1', 'virtual unsigned int KeyToHashIndex(const char* key) const; '
       'virtual bool IsKeysEqual(const char* key1, const char* key2) const; virtual void SetValue(',
       'virtual void ClearValue(void* item); virtual void* NewItem(); virtual void DeleteItem(void* item); };',
       '\nint z;'], ['{ return', 'FUN_'])
check('VTBL1 A660A0Map keeps the line count', str(got.count('\n')), ['11'])
got = run(s1.r_stand_in_interfaces, 'class A660A0Map : public A660A0MapBase\n{\n    virtual ~A660A0Map();\n};\n',
          '/x/src/gamebryo2.2/corelibs/nimain/nistream_rtm_variant.cpp')
check('VTBL1 A660A0Map: only in nistream.cpp', got, [], ['VTBL1'])

# ---- FRIEND1
src = ('namespace N {\nint a;\nclass A : public B\n{\n    friend class W;\n    W* m_w;\n};\nclass W { int x; };\n}\n'
       'struct T;\nclass D { friend struct T; };\ntypedef int Q;\nclass E { friend class Q; };\n'
       'void f() { class L { friend class M; }; }\n// class X { friend class Z; };\n'
       'class G { friend class H; class I { friend class H; }; };\nunion H { int a; };\n'
       'template <class P> class K { friend class P; };\ntemplate <class T2> class R;\nclass S { friend class R; };\n')
got = run(s1.r_friend_class_visibility, src, '/nonexistent/src/f.cpp')
check('FRIEND1 forward-declares a friend class declared nowhere before', got,
      ['int a;\n class W; /* fnv-nx FRIEND1 */ class A : public B', '}; }\n// class X { friend class Z; };\n union H; /* fnv-nx FRIEND1 */ class G {'],
      ['class T; /*', 'class Q; /*', 'class M; /*', 'class Z; /*', 'class P; /*', 'class R; /*', 'union H; union H;'])
check('FRIEND1 two declarations only (not T, Q, M, Z, P, R)', str(got.count('fnv-nx FRIEND1')), ['2'])
check('FRIEND1 keeps the line count', str(got.count('\n')), [str(src.count('\n'))])
got = run(s1.r_friend_class_visibility, '#include "a.h"\nclass A\n{\n    friend class W;\n    W* m_w;\n};\n', '/nonexistent/src/f.cpp')
check('FRIEND1 at the start of a file stays off the #include line', got,
      ['#include "a.h"\n class W; /* fnv-nx FRIEND1 */ class A\n'])

# ---- INIT1
got = run(s1.r_static_initializers, 'int a;\n// @fnv 0x00a8a5f0\nvoid* FUN_00a8a5f0_class::FUN_00a8a5f0()\n{\n'
          '    void* old = (void*)DAT_011f5b84;\n    if (!old)\n    {\n        g();\n    }\n    return this;\n}\nint b;\n',
          '/x/src/staging/blk_00a80000_o2.cpp')
check('INIT1 one static NiMainSDM after its constructor, same line count', got,
      ['    return this;\n} /* fnv-nx INIT1', 'static FUN_00a8a5f0_class fnvnx_init1_NiMainSDM; static void* const '
       'fnvnx_init1_NiMainSDM_ctor = fnvnx_init1_NiMainSDM.FUN_00a8a5f0();\nint b;\n'], ["\\'"])
check('INIT1 only in the constructor\'s unit', run(s1.r_static_initializers, '// @fnv 0x00a8a5f0\nvoid* '
      'FUN_00a8a5f0_class::FUN_00a8a5f0()\n{\n}\n', '/x/src/staging/blk_00a80000_o2_noehsc_variant.cpp'), [], ['INIT1'])

# ---- ALIGN1
got = run(s1.r_class_alignment, '__declspec(align(16)) struct S_V4 { float x, y, z, w; };\n'
          '__declspec(align(16))class H:public C\n{\n};\n__declspec(align(16))struct TAU32;\n'
          'void f() {\n    __declspec(align(16)) struct Buf\n    {\n        F4 a;\n    } buf;\n}\n'
          'struct A { __declspec(align(16)) struct Atoms m_kBridgeAtoms; __declspec(align(8)) volatile int t; };\n'
          'template <class T> __declspec(align(16)) __declspec(novtable) struct X { T a; };\n')
check('ALIGN1 moves align behind the class key of definitions and forward declarations', got,
      ['struct __declspec(align(16)) S_V4 { float', 'class __declspec(align(16)) H:public C\n{',
       'struct __declspec(align(16)) TAU32;', '    struct __declspec(align(16)) Buf\n    {',
       '__declspec(align(16)) struct Atoms m_kBridgeAtoms;', '__declspec(align(8)) volatile int t;',
       'template <class T> __declspec(novtable) struct __declspec(align(16)) X { T a; };'])
check('ALIGN1 keeps the line count', str(got.count('\n')), ['13'])

# ---- ENUM2
got = run(s1.r_enum_int_values, 'enum\n{\n    CHANGE_SCRIPT_VARIABLES = 0x80000000, // = 0x90000000\n    B = 4\n};\n'
          'enum ShaderParameter { SP_NONE = 0xffffffff, SP_X = 0x80000000u, SP_Y = -1 };\n'
          'enum Small { S0 = 1 };\nenum Expr { E0 = 0x80000000 | 1 };\nenum class Sc { Q = 0x80000000 };\n'
          'enum Huge { H = 0x100000000 };\nenum Based : unsigned { U = 0x80000000 };\n')
check('ENUM2 gives enums with values >= 2^31 MSVC\'s int', got,
      ['enum : int\n{\n    CHANGE_SCRIPT_VARIABLES = (int)0x80000000, // = 0x90000000\n    B = 4\n};',
       'enum ShaderParameter : int { SP_NONE = (int)0xffffffff, SP_X = (int)0x80000000u, SP_Y = -1 };',
       'enum Small { S0 = 1 };', 'enum Expr { E0 = 0x80000000 | 1 };', 'enum class Sc { Q = 0x80000000 };',
       'enum Huge { H = 0x100000000 };', 'enum Based : unsigned { U = 0x80000000 };'])

# ---- BIND1 (units.json)
_c = Counter()
_data = {'units': [{'source': 'src/staging/blk_00aa0000_od_ob1_variant.cpp', 'symbols': {'?x@@3HA': '0x1'}},
                   {'source': 'src/other.cpp', 'symbols': {}}]}
s1.bind_units_data(_data, _c)
s1.bind_units_data(_data, _c)
check('BIND1 binds the stand-in names once, in their unit only', repr(_data) + ' %d' % _c['BIND1'],
      ["'?Allocate@MemoryManagerAA3880@@QAEPAXI@Z': '0x00aa3e40'", "'?g_kMemoryAA3880@@3UMemoryManagerAA3880@@A': '0x011f6238'",
       "'?entryNpos@@3IA': '0x010173f0'", "'symbols': {}}]} 4", "'?x@@3HA': '0x1'",
       "'?smallPools@fnv_00aafc80@@3PAPAUDeallocPool@1@A': '0x011f67b8'"])
# W28: the memory manager of src/bssystem/archive.cpp (top-down 109) and the small-pool table (44) are bound in their units
_c = Counter()
_data = {'units': [{'source': 'src/bssystem/archive.cpp', 'symbols': {}}]}
s1.bind_units_data(_data, _c)
check('BIND1 binds archive.cpp\'s g_memoryManager_af5480 to the memory manager', repr(_data['units'][0]['symbols']),
      ["'?g_memoryManager_af5480@fnv_00af5480@@3UMemoryManager_af5480@1@A': '0x011f6238'"])
check('BIND1 rows are all well-formed (MSVC data names at image data addresses)',
      repr([r for r in s1.BIND1_REFS if not (r[1].startswith('?') and r[2].startswith('0x') and
                                              0x00401000 <= int(r[2], 16) < 0x02100000)]), ['[]'])

# ---- NATIVE1 (render R3): the drop list's matching, shared by prepare's drop_native_units and tools/rules/native1.py
_spec = importlib.util.spec_from_file_location('native1', os.path.join(HERE, '..', '..', 'tools', 'rules', 'native1.py'))
native1 = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(native1)
_d = tempfile.mkdtemp()
open(os.path.join(_d, 'drop.txt'), 'w').write('# N1\nsrc/gamebryo2.2/corelibs/niconsolerenderer/*  # whole dir\nsrc/b.cpp\n')
open(os.path.join(_d, 'ov.tsv'), 'w').write('# x\n00bca760\tnx_a\tthiscall\nvslot:??_7X@@6B@:3\tnx_b\t\n')
_data = {'units': [{'source': 'src/a.cpp'}, {'source': 'src/gamebryo2.2/corelibs/niconsolerenderer/x.cpp',
                    'functions': [{'address': '0x00bca760'}, {'address': '0x00bca800'}]}, {'source': 'src/b.cpp'}]}
_kept, _gone = native1.drop_units(_data, native1.load_list(os.path.join(_d, 'drop.txt')))
check('NATIVE1 drops listed units and glob matches, reports the addresses no override covers',
      repr([u['source'] for u in _kept['units']]) + ' %d ' % len(_gone) + repr(native1.uncovered(_gone, os.path.join(_d, 'ov.tsv'))),
      ["['src/a.cpp'] 2 [12363776]"])
check('NATIVE1 with no list file drops nothing', repr(native1.load_list(os.path.join(_d, 'none.txt'))), ['[]'])

# ---- shim packing: fnvnx_win32.h included inside a decomp #pragma pack(push, 4) region (DECL1 / DECL2, 16 sites)
import re, shutil as _shutil, subprocess as _subprocess
if _shutil.which('aarch64-linux-gnu-g++'):
    _pk = os.path.join(tempfile.mkdtemp(), 'pk.cpp')
    open(_pk, 'w').write('#pragma pack(push, 4)\n#include "fnvnx_win32.h"\nstruct Inner { int a; LARGE_INTEGER b; };\n'
                         '#pragma pack(pop)\nstatic_assert(alignof(LARGE_INTEGER) == 8 && alignof(ULARGE_INTEGER) == 8 && '
                         'alignof(MEMORYSTATUSEX) == 8 && sizeof(MEMORYSTATUSEX) == 64, "SDK packing");\n'
                         'static_assert(__builtin_offsetof(Inner, b) == 4, "the region packs its own structs");\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-fsyntax-only',
                          '-I' + os.path.join(HERE, '..', '..', 'shim', 'include'), _pk], capture_output=True, text=True)
    check('fnvnx_win32.h keeps the SDK packing inside a pack(4) region', _r.stderr or 'ok', ['ok'])
    # STL1 AUX: the rule's output compiles against the shim, with MSVC's x86 vector layout (TAIL1's base end by hand)
    _sa = os.path.join(tempfile.mkdtemp(), 'stl1aux.cpp')
    _compat = open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h')).read()
    _base_end = re.search(r'template <unsigned A> struct fnvnx_int_of_align;.*?#define FNVNX_MSVC_BASE_END[^\n]*\n',
                          _compat, re.S).group(0)    # the compat header's macro (the whole header needs sse2neon)
    open(_sa, 'w').write(_base_end + run(s1.r_msvc_library_internals, _STL1_AUX.replace(
        '{\n    typedef ::std', '{ FNVNX_MSVC_BASE_END(std::_Container_base_aux_alloc_real< std::allocator<int*> >)\n    typedef ::std', 1))
        + '#pragma GCC diagnostic ignored "-Winvalid-offsetof"\n'
          'static_assert(sizeof(std::_Container_base_aux_alloc_real< std::allocator<int*> >) == 8 && '
          '__builtin_offsetof(Store, _Alval) == 8 && __builtin_offsetof(Store, first) == 12 && '
          '__builtin_offsetof(Store, last) == 16 && __builtin_offsetof(Store, limit) == 20, "MSVC vector layout");\n'
          'static_assert(sizeof(Store::CIt) == 8 && __builtin_offsetof(Store::CIt, _Myptr) == 4, "_Myaux, _Myptr");\n'
          'int** g(Store& s, Store::CIt e, std::vector<int>* q, std::allocator<std::vector<int> >& a) {\n'
          '    s.destroy(); std::_Destroy_range(q, q + 1, a);\n'
          '    std::_Vector_iterator<std::vector<int>, std::allocator<std::vector<int> > > i(q, 0);\n'
          '    stdext::unchecked_uninitialized_copy(i, i + 1, q + 2, a);\n'
          '    return s.copy(s.begin(), e, s.first); }\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-fsyntax-only', '-Werror',
                          '-I' + os.path.join(HERE, '..', '..', 'shim', 'include'), _sa], capture_output=True, text=True)
    check('STL1 AUX compiles against fnvnx_msvcstl.h with MSVC x86 layouts', _r.stderr or 'ok', ['ok'])

# ---- STATIC1 namespace statics (S13: bound by their plain name; roulettemenu, hudmainmenu)
_n = tempfile.mkdtemp()
os.makedirs(os.path.join(_n, 'src')); os.makedirs(os.path.join(_n, 'build'))
_json.dump({'units': [{'source': 'src/r.cpp', 'symbols': {
    '_s_fSpinTime': '0x011da904', '_s_iLastState': '0x011a081c', '_s_szName': '0x01080000', '_g_pInst': '0x011d96c0',
    '?g_pInst@@3PAUH@@A': '0x011d96c0', '?g_pInst@fnv_1@@3PAUH@@A': '0x011d96c0', '_s_kList': '0x011d0000'}}]},
           open(os.path.join(_n, 'build', 'units.json'), 'w'))
_src = ('struct H;\nstruct L { L(); };\nextern H* g_pInst;\nstatic H* g_pInst;\nnamespace fnv_1 {\nextern H* g_pInst;\n}\n'
        'namespace fnv_007bd2a0 {\nstatic float s_fSpinTime;\nstatic int s_iLastState = 4;\n'
        'static const char s_szName[] = "x";\nstatic H* g_pInst;\nstatic L s_kList;\nvoid f() { s_fSpinTime = 1; }\n}\n')
open(os.path.join(_n, 'src', 'r.cpp'), 'w').write(_src)
_saved = (s1._IMAGE_NAMES, dict(s1._IMAGE_CNAMES), dict(s1._IMAGE_ADDRS))
s1._IMAGE_NAMES = None; s1._IMAGE_CNAMES.clear(); s1._IMAGE_ADDRS.clear()
got = run(s1.r_image_statics, _src, os.path.join(_n, 'src', 'r.cpp'))
s1._IMAGE_NAMES = _saved[0]; s1._IMAGE_CNAMES.clear(); s1._IMAGE_CNAMES.update(_saved[1])
s1._IMAGE_ADDRS.clear(); s1._IMAGE_ADDRS.update(_saved[2])
check('STATIC1 gives namespace statics bound by their plain name C linkage (image variables declared only)', got,
      ['extern "C" { extern float s_fSpinTime; }', 'extern "C" { int s_iLastState = 4; }',
       'extern "C" { extern const char s_szName[] = "x"; }', 'extern "C" { extern H* g_pInst; }',
       'extern "C" { L s_kList; }', 'H* g_pInst;\nnamespace fnv_1', 'void f() { s_fSpinTime = 1; }'])

# ---- VA2
got = run(s1.r_va_enum_args, 'enum Index : int { A };\nvoid f(va_list args) {\n    Index d = va_arg(args, Index);\n'
          '    const char* n = va_arg(args, const char*);\n    int i = va_arg(args, int);\n}\n')
check('VA2 reads an enum through ... as the int it was passed as', got,
      ['Index d = (Index)va_arg(args, int);', 'va_arg(args, const char*)', 'int i = va_arg(args, int);'])

# ---- VLIT1
_v = tempfile.mkdtemp()
os.makedirs(os.path.join(_v, 'src')); os.makedirs(os.path.join(_v, 'build'))
_json.dump({'units': [{'source': 'src/n.cpp', 'symbols': {'$SG2541': '0x0101212c'}, 'functions': []},
                      {'source': 'src/k.cpp', 'symbols': {}, 'functions': [{'address': '0x00404b40', 'symbol': '?f@@YAXXZ'}]}]},
           open(os.path.join(_v, 'build', 'units.json'), 'w'))
_saved, s1._VLIT = s1._VLIT, None
_st = Counter()
got = s1.r_vptr_literals(os.path.join(_v, 'src', 'n.cpp'), '#include "x.h"\nvoid* F::Init()\n{\n    m_p = "@K@";\n    return this;\n}\n', _st)
got2 = s1.r_vptr_literals(os.path.join(_v, 'src', 'n.cpp'), 'void f() { m_p = "@K@"; g("text"); }\n', _st)
s1._VLIT = _saved
check('VLIT1 stores the vftable address the unit binds the misread literal to; ambiguous sites are listed', got + got2 + ' %d' % _st['VLIT1-LIST'],
      ['#include "x.h"\nextern "C" char DAT_0101212c; /* fnv-nx VLIT1 */ void* F::Init()\n',
       'm_p = (char*)&DAT_0101212c; /* fnv-nx VLIT1: "@K@" */\n', 'void f() { m_p = "@K@"; g("text"); }', ' 1'])
check('VLIT1 keeps the line count', str(got.count('\n')), ['6'])
check('VLIT1 binds its DAT name in the unit (BIND1 hook)', repr(sorted(s1.VLIT1_BINDS)),
      ["('src/n.cpp', '_DAT_0101212c', '0x0101212c')"])
s1.VLIT1_BINDS.clear()

# ---- SSE2 with the decomp's own name for __m128
got = prepare.r_sse_local_protos('/x/src/a.cpp', 'typedef __m128 ClosestPointQuad;\nClosestPointQuad _mm_shuffle_ps(ClosestPointQuad, ClosestPointQuad, unsigned int);\n'
                                 'extern "C" ::hkQuadReal __cdecl _mm_load_ps(const float*);\n'
                                 'ClosestPointQuad f(ClosestPointQuad a) {\n    return _mm_add_ps(a, a);\n}\n', Counter())
check('SSE2 drops intrinsic prototypes with a typedef return type, keeps statements', got,
      ['typedef __m128 ClosestPointQuad;\n', '    return _mm_add_ps(a, a);'], ['_mm_shuffle_ps', '_mm_load_ps'])

# ---- SSE2 parenthesized prototype names, SSE4 unqualified using-declarations
got = prepare.r_sse_local_protos('/x/src/a.cpp', 'extern "C" ::hkQuadReal (_mm_load_ps)(const float*);\nint k;\n', Counter())
got2 = prepare.r_sse_qualified('/x/src/a.cpp', 'namespace N {\nusing ::_mm_add_ps;\nusing _mm_mul_ps;\nusing ::hkpVehicleInstance;\n}\n', Counter())
check('SSE2 drops (_mm_x)(...) prototypes, SSE4 unqualified using-declarations of intrinsics', got + got2,
      ['int k;', 'using ::hkpVehicleInstance;'], ['_mm_load_ps', '_mm_add_ps', '_mm_mul_ps'])

# ---- scope heads
spans = prepare.scope_spans('// ' + 'y' * 295 + ' (a) b\n// c\n\nstruct T\n{\n    int f();\n};\n')
check('scope head after a cut comment is a class', str([k for _, _, k, _ in spans]), ['class'], ['function'])

# ---- DECL1-FOPEN: an _iobuf fopen prototype gives way to the shim's fopen -> fnvnx_iob_fopen (S1-crt)
_fo = prepare.r_platform_protos('/x/src/a.h', 'struct _iobuf;\nextern "C" _iobuf* fopen(const char*, const char*);\n'
                                'extern "C" int fclose(_iobuf*);\nvoid g();\n', Counter())
check('DECL1-FOPEN _iobuf fopen -> fnvnx_iobuf_fopen.h, same lines', _fo,
      ['struct _iobuf;\n#include "fnvnx_iobuf_fopen.h" /* fnv-nx DECL1 */\n#include "fnvnx_iobuf.h" /* fnv-nx DECL1 */\n'
       'void g();'], ['fopen(const'])
check('DECL1-FOPEN a fopen prototype without _iobuf is left alone',
      prepare.r_platform_protos('/x/src/a.cpp', 'extern "C" void* __cdecl fopen(const char*, const char*);\n', Counter()),
      ['void* __cdecl fopen(const char*, const char*);'], ['fnv-nx'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _fc = os.path.join(tempfile.mkdtemp(), 'fo.cpp')
    open(_fc, 'w').write(_fo + '#include <fstream>\n#include <type_traits>\n'
                         'static_assert(std::is_same<decltype(fopen("a", "rb")), _iobuf*>::value, "the game FILE");\n'
                         'static_assert(std::is_same<decltype(std::fopen("a", "rb")), _iobuf*>::value, "std:: too");\n'
                         'static_assert(std::is_same<decltype(::freopen("a", "rb", stdin)), FILE*>::value, "libc");\n'
                         'int h(const char* p) { _iobuf* f = fopen(p, "rb"); return f ? fclose(f) : -1; }\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-fsyntax-only', '-Werror',
                          '-I' + os.path.join(HERE, '..', '..', 'shim', 'include'), _fc], capture_output=True, text=True)
    check('DECL1-FOPEN output compiles: fopen and std::fopen are the _iobuf one', _r.stderr or 'ok', ['ok'])

# ---- DECL2 the decomp's merged SDK record `struct _RTL_CRITICAL_SECTION_DEBUG {...} RTL_CRITICAL_SECTION` (S1-crt)
_cs = ('extern "C"{\ntypedef struct _RTL_CRITICAL_SECTION_DEBUG{\nWORD Type;\nstruct _RTL_CRITICAL_SECTION*CriticalSection;\n'
       'DWORD Flags;\n}RTL_CRITICAL_SECTION,*PRTL_CRITICAL_SECTION;\ntypedef RTL_CRITICAL_SECTION CRITICAL_SECTION;}\n'
       'class Lock { CRITICAL_SECTION m_kSection; };\n')
_csg = run(s1.r_sdk_types, _cs)
check('DECL2 drops a _RTL_CRITICAL_SECTION_DEBUG copy named RTL_CRITICAL_SECTION, same lines', _csg,
      ['#include "fnvnx_win32.h" /* fnv-nx DECL2 */\nextern "C"{\n/* fnv-nx DECL2 */\n\n\n\n\n'
       'typedef RTL_CRITICAL_SECTION CRITICAL_SECTION;}'], ['WORD Type'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _cc = os.path.join(tempfile.mkdtemp(), 'cs.cpp')
    open(_cc, 'w').write(_csg + 'static_assert(sizeof(Lock) == 24, "the PC section (D15)");\n'
                         'RTL_CRITICAL_SECTION_DEBUG* d; PRTL_CRITICAL_SECTION_DEBUG e = d;\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-fsyntax-only', '-Werror',
                          '-I' + os.path.join(HERE, '..', '..', 'shim', 'include'), _cc], capture_output=True, text=True)
    check('DECL2 critical-section output compiles; CRITICAL_SECTION is 24 bytes', _r.stderr or 'ok', ['ok'])

# ---- COM1: a unit's own IUnknown keeps its slots under its own name (S1-crt, bsshader)
_com = ('struct IUnknown\n{\n    virtual int QueryInterface(void*, void**);\n    virtual unsigned long AddRef();\n'
        '    virtual unsigned long Release();\n};\ntypedef char _sizecheck_IUnknown[sizeof(IUnknown) == 4 ? 1 : -1];\n'
        'struct ID3DXBuffer : public IUnknown\n{\n    virtual void* GetBufferPointer();\n'
        '    virtual int QueryInterface(void*, void**);\n};\n// IUnknown in a comment\n')
_comg = run(s1.r_own_iunknown, _com)
check('COM1 renames the own IUnknown, its base use and sizeof', _comg,
      ['struct fnvnx_unit_IUnknown\n{', '_sizecheck_IUnknown[sizeof(fnvnx_unit_IUnknown)',
       ': public fnvnx_unit_IUnknown\n', '// IUnknown in a comment'], ['struct IUnknown', 'public IUnknown'])
check('COM1 leaves a unit without its own IUnknown alone',
      run(s1.r_own_iunknown, 'struct IDirect3D9;\nvoid f(IUnknown* p);\n'), ['void f(IUnknown* p);'], ['fnvnx_unit'])
check('COM1 leaves an IUnknown nested in a namespace alone',
      run(s1.r_own_iunknown, 'namespace N {\nstruct IUnknown\n{\n};\n}\n'), ['struct IUnknown'], ['fnvnx_unit'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _ci = os.path.join(tempfile.mkdtemp(), 'com.cpp')
    open(_ci, 'w').write('#include "fnvnx_win32.h"\n' + _comg +
                         'void* g(ID3DXBuffer* b, IUnknown* u) { b->Release(); u->Release(); return b->GetBufferPointer(); }\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-fsyntax-only', '-Werror',
                          '-I' + os.path.join(HERE, '..', '..', 'shim', 'include'), _ci], capture_output=True, text=True)
    check('COM1 output compiles next to the platform IUnknown', _r.stderr or 'ok', ['ok'])

# ---- S1-refs: RREF1-CLASS, RREF2, ADDR1, LANE1
_rc = ('struct V { int x; };\nstruct R\n{\n    int Read(void* p, V& v);\n    int Use(int n);\n};\n'
       'struct Other\n{\n    int Read(void* p, V* v);\n};\nV Make();\n'
       'int R::Use(int n)\n{\n    return Read(0, Make());\n}\n')
check('RREF1-CLASS an unqualified call in R:: takes R\'s own declarations',
      run(s1.r_temporaries_to_references, _rc), ['Read(0, fnvnx_lvalue(Make()))'])
check('RREF1-CLASS a free function with a pointer overload elsewhere stays',
      run(s1.r_temporaries_to_references, _rc.replace('int R::Use(int n)', 'int Use2(int n)')), [], ['fnvnx_lvalue'])
_r2 = ('struct E\n{\n    E(int v) { m = v; }\n    bool operator!=(E& o) { return m != o.m; }\n    int m;\n};\n'
       'bool f(E* p) { return p[0] != E(0); }\n')
check('RREF2 a temporary for a non-const reference operator parameter',
      run(s1.r_operator_temporaries, _r2), ['p[0] != fnvnx_lvalue(E(0))'])
check('RREF2 a const reference operator: unchanged',
      run(s1.r_operator_temporaries, _r2.replace('operator!=(E& o)', 'operator!=(const E& o)')), [], ['fnvnx_lvalue'])
check('ADDR1 address of an intrinsic result after a pointer cast',
      run(s1.r_address_of_temporaries, 'void f(__m128 a) { g((const V*)&_mm_andnot_ps(a, a)); }\n'),
      ['(const V*)&fnvnx_lvalue(_mm_andnot_ps(a, a))'])
check('ADDR1 binary & with an intrinsic: unchanged',
      run(s1.r_address_of_temporaries, 'int f(__m128 a) { return (x) & _mm_movemask_ps(a); }\n'), [], ['fnvnx_lvalue'])
check('LANE1 address of the lane array',
      run(s1.r_lane_array_address, 'void f(S s) { g(((&((float*)&(s.v)))[0])); h((0)[&((float*)&(s.v))]); }\n'),
      ['((float(*)[sizeof(s.v) / sizeof(float)])&(s.v))'], ['&((float*)&(s.v))'])
check('LANE1 a lane\'s address stays',
      run(s1.r_lane_array_address, 'void f(S s) { g(&((float*)&(s.v))[1]); }\n'), ['&((float*)&(s.v))[1]'], ['(*)['])

# ---- S1-refs: STD2 (a unit's own `std::` definition renamed, with its recipe symbol)
import json as _json
_s2 = tempfile.mkdtemp()
os.makedirs(os.path.join(_s2, 'build')); os.makedirs(os.path.join(_s2, 'src'))
_json.dump({'units': [{'source': 'src/g.cpp', 'functions': [
    {'address': '0x00401000', 'symbol': '?abs@std@@YANM@Z', 'size': 20},
    {'address': '0x00401020', 'symbol': '?f@@YAHH@Z', 'size': 8}], 'symbols': {}},
    {'source': 'src/h.cpp', 'functions': [{'address': '0x00401040', 'symbol': '?g@@YAHH@Z', 'size': 8}],
     'symbols': {'?abs@std@@YANM@Z': '0x00401000'}}]}, open(os.path.join(_s2, 'build', 'units.json'), 'w'))
s1._STD2_FNS = None; s1.STD2_RENAMES.clear()
_g = ('// @fnv 0x00401000\nFNVNX_KEEP double std::abs(float value)\n{\n    return value < 0 ? -value : value;\n}\n'
      '// @fnv 0x00401020\nint f(int x) { return (int)std::abs((float)x); }\n')
_o = run(s1.r_std_definitions, _g, os.path.join(_s2, 'src', 'g.cpp'))
check('STD2 file-scope std:: definition the recipe decorates in std -> fnvnx_std_',
      _o, ['FNVNX_KEEP double fnvnx_std_abs(float value)', 'return (int)std::abs((float)x);'], ['double std::abs'])
check('STD2 a unit whose recipe has no std:: definition is untouched',
      run(s1.r_std_definitions, _g.replace('0x00401000', '0x00401060'), os.path.join(_s2, 'src', 'h.cpp')),
      ['double std::abs(float value)'], ['fnvnx_std_'])
check('STD2 a member definition in a class scope is not at file scope',
      run(s1.r_std_definitions, 'struct S {\n// @fnv 0x00401000\ndouble std::abs(float v) { return v; }\n};\n',
          os.path.join(_s2, 'src', 'g.cpp')), [], ['fnvnx_std_'])
s1.rename_std_definitions(_s2, Counter())
_u = _json.load(open(os.path.join(_s2, 'build', 'units.json')))['units']
check('STD2 POST hook renames the definition\'s recipe symbol only', _json.dumps(_u),
      ['"?fnvnx_std_abs@@YANM@Z"', '"?f@@YAHH@Z"', '"?abs@std@@YANM@Z": "0x00401000"'])
_shutil.rmtree(_s2, ignore_errors=True)

# ---- S1-refs: COND1 (`c ? false : f()` with f returning a class convertible both ways with bool), RSTR1
_G = ('struct Gate { char v; Gate() {} Gate(bool b) : v(b) {} operator bool() const { return v != 0; } };\n'
      'struct Only { char v; operator bool() const { return v != 0; } };\n'
      'struct N { Gate test(float e) const; int other(float e) const; Only probe(float e) const; };\n')
check('COND1 the literal of `? false :` becomes the gate class (MSVC\'s conversion)',
      run(s1.r_conditional_bool_gates, _G + 'void f(N* n) { if (!n ? false : n->test(0.5f)) g(); }\n'),
      ['!n ? Gate(false) : n->test(0.5f)) g();'])
check('COND1 a call returning int stays',
      run(s1.r_conditional_bool_gates, _G + 'void f(N* n) { if (!n ? false : n->other(0.5f)) g(); }\n'),
      [], ['Gate(false)', 'Gate(true)'])
check('COND1 a class without a bool constructor (no ambiguity) stays',
      run(s1.r_conditional_bool_gates, _G + 'void f(N* n) { if (!n ? false : n->probe(0.5f)) g(); }\n'),
      [], ['Gate(false)', 'Gate(true)'])
check('COND1 an operand that does not end in the call stays',
      run(s1.r_conditional_bool_gates, _G + 'void f(N* n) { int x = !n ? false : n->test(0.5f) + 1; }\n'),
      [], ['Gate(false)', 'Gate(true)'])
_R = ('template <class T> struct Arr { void AddBack(const T& v); void Set(int i, T* p); };\n'
      'struct S { int a; };\n')
check('RSTR1 restrict local passed to a const T& parameter',
      run(s1.r_restrict_reference_args, _R + 'void f(Arr<S*>* o) {\n    S* __restrict c = new S();\n'
          '    o->AddBack(c);\n    o->Set(0, c);\n}\n'),
      ['o->AddBack(fnvnx_unrestrict(c));', 'o->Set(0, c);'])
check('RSTR1 a plain local stays',
      run(s1.r_restrict_reference_args, _R + 'void f(Arr<S*>* o) {\n    S* __restrict d = 0;\n    S* c = 0;\n'
          '    o->AddBack(c);\n}\n'), [], ['fnvnx_unrestrict'])
check('RSTR1 a use outside the declaring function stays',
      run(s1.r_restrict_reference_args, _R + 'void f() { S* __restrict c = 0; }\nvoid h(Arr<S*>* o, S* c) '
          '{ o->AddBack(c); }\n'), [], ['fnvnx_unrestrict'])
# ---- S1-refs: NEW1 plain `unsigned`, NEW5 (placement-only operator delete)
check('NEW1 plain unsigned size parameter -> size_t, unsigned long / short kept',
      prepare.r_newdel_size('x.cpp', 'static void operator delete[](void* p, unsigned);\n'
                            'static void* operator new(unsigned n);\nvoid* operator new(unsigned long n, unsigned short k);\n'
                            'void* operator new(unsigned n, unsigned& total);\n', Counter()),
      ['operator delete[](void* p, size_t)', 'operator new(size_t n)', 'operator new(unsigned long n, unsigned short k)',
       'operator new(size_t n, unsigned& total)'])
_n5 = ('struct Pm\n{\n    virtual ~Pm();\n    int m;\n    static void operator delete(void*, void*);\n'
       '    static void* operator new(size_t n) { return ::operator new(n); }\n};\nstruct Pd : Pm\n{\n    int k;\n};\n'
       'class Pc\n{\n    int h;\npublic:\n    virtual ~Pc() {}\n    static void* operator new(size_t, void* p) { return p; }\n'
       '    static void operator delete(void*, void*) {}\n};\n')
_o5 = run(s1.r_placement_only_delete, _n5)
check('NEW5 placement-only delete + virtual dtor: global delete member',
      _o5, ['struct Pm\n{ FNVNX_GLOBAL_DELETE /* fnv-nx NEW5 */\n',
            'class Pc\n{ public: FNVNX_GLOBAL_DELETE private: /* fnv-nx NEW5 */\n    int h;'], ['struct Pd : Pm\n{ F'])
check('NEW5 the class\'s own inline operator new gets the tag (no delete on a throwing constructor, V1 N2)',
      _o5, ['static void* operator new(size_t n FNVNX_NEW5_TAG) { return ::operator new(n); }'])
check('NEW5 a class whose operator new has no body here: unchanged',
      run(s1.r_placement_only_delete, _n5.replace('static void* operator new(size_t n) { return ::operator new(n); }',
                                                  'static void* operator new(size_t n);')),
      ['class Pc\n{ public: FNVNX_GLOBAL_DELETE'], ['struct Pm\n{ FNVNX'])
check('NEW5 keeps the line count', str(_o5.count('\n') == _n5.count('\n')), ['True'])
check('NEW5 a usual delete next to the placement form: unchanged',
      run(s1.r_placement_only_delete, _n5.replace('static void operator delete(void*, void*);',
                                                  'static void operator delete(void*, void*);\n    static void operator delete(void* p);')
          .replace('class Pc', 'class Pc2 : Pm')), [], ['FNVNX_GLOBAL_DELETE'])
check('NEW5 no virtual destructor, or a base class: unchanged',
      run(s1.r_placement_only_delete, 'struct Q\n{\n    ~Q();\n    static void operator delete(void*, void*);\n};\n'
          'struct R : B\n{\n    virtual ~R();\n    static void operator delete(void*, void*);\n};\n'), [], ['FNVNX_GLOBAL_DELETE'])
check('NEW5 a placement form with another second parameter: unchanged',
      run(s1.r_placement_only_delete, 'struct Q\n{\n    virtual ~Q();\n    static void operator delete(void*, int, int);\n};\n'),
      [], ['FNVNX_GLOBAL_DELETE'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _c5 = os.path.join(tempfile.mkdtemp(), 'n5.cpp')
    _m5 = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h'))
           if l.startswith(('#define FNVNX_GLOBAL_DELETE ', '#define FNVNX_NEW5_TAG ', 'struct fnvnx_new_tag'))]  # the shim's own
    open(_c5, 'w').write('#include <stddef.h>\n#include <new>\n' + ''.join(_m5) + _o5 +
                         'Pm::~Pm() {}\nvoid f(Pd* d, Pc* c) { delete d; delete c; }\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-fsyntax-only', '-Werror', _c5],
                         capture_output=True, text=True)
    check('NEW5 output compiles (derived class too)', _r.stderr or 'ok', ['ok'])
# ---- S1-refs agent C: TPL1 out-of-class members of a header's class template, SPEC1 through a typedef name,
# RCAST1 in comments
_c = tempfile.mkdtemp()
os.makedirs(os.path.join(_c, 'src'))
with open(os.path.join(_c, 'src', 'pb.h'), 'w') as _h:
    _h.write('template <class A, class T> class LBase\n{\n  public:\n    T* m_pFirst;\n    unsigned int m_uiCount;\n};\n'
             'template <class A, class T> class PList : public LBase<A, T>\n{\n  public:\n    int m_iOwn;\n'
             '    T Pop();\n};\n')
got = run(s1.r_dependent_base_members, '#include "pb.h"\ntemplate <class A, class T>\n// note\nKEEPX T PList<A, T>::Pop()\n'
          '{\n    T* m_uiCount2 = m_pFirst;\n    m_iOwn = 0;\n    --m_uiCount;\n    return *m_uiCount2;\n}\n',
          os.path.join(_c, 'src', 'pl.h'))
check('TPL1 out-of-class member of a class template from an included header', got,
      ['T* m_uiCount2 = this->m_pFirst;', '    --this->m_uiCount;', '    m_iOwn = 0;'], ['this->m_iOwn'])
check('TPL1 out-of-class explicit specialization of a header\'s template: untouched',
      run(s1.r_dependent_base_members, '#include "pb.h"\ntemplate <>\nint PList<char, int>::Pop()\n{\n    --m_uiCount;\n'
          '    return 0;\n}\n', os.path.join(_c, 'src', 'pm.h')), (), ['this->'])
check('TPL1 out-of-class member, the template\'s own parameter shadows nothing: params untouched',
      run(s1.r_dependent_base_members, '#include "pb.h"\ntemplate <class A, class m_pFirst>\nint PList<A, m_pFirst>::Pop()\n'
          '{\n    return m_uiCount + sizeof(m_pFirst);\n}\n', os.path.join(_c, 'src', 'pn.h')),
      ['return this->m_uiCount + sizeof(m_pFirst);'], ['this->m_pFirst'])
_sp = os.path.join(_c, 'src', 'gamebryo2.2', 'corelibs', 'nimain', 'nitset.inl')
got = run(s1.r_specialization_decls, 'class Item;\ntemplate <class T> class Ref\n{\n};\n'
          'template <class T> class Alloc\n{\n};\ntemplate <class T, class B>\nclass Bag\n{\npublic:\n    void Put(const T& v);\n'
          '    void Grow(unsigned int n);\n};\n\ntypedef Bag<Ref<Item>,\n            Alloc<Ref<Item> > >\n    ItemBag;\n\n'
          'template <>\n// @x\nFNVNX_KEEP void ItemBag::Put(const Ref<Item>& v)\n{\n    Grow(2);\n}\n\n'
          'void ItemBag::Grow(unsigned int n)\n{\n}\n', _sp)
check('SPEC1 a member defined through a typedef name of the specialization', got,
      ['};\ntemplate <> void Bag<Ref<Item>, Alloc<Ref<Item> >>::Put(const Ref<Item>& v) FNVNX_KEEP; /* fnv-nx SPEC1 */ '
       'template <> void Bag<Ref<Item>, Alloc<Ref<Item> >>::Grow(unsigned int n); /* fnv-nx SPEC1 */'])
check('SPEC1 a typedef of a template the file does not define: nothing',
      run(s1.r_specialization_decls, '#include <x.h>\ntypedef Bag<int, int> IB;\nvoid IB::Grow(unsigned int n)\n{\n}\n', _sp),
      (), ['SPEC1'])
check('RCAST1 a reference cast spelled in a comment: untouched',
      run(s1.r_ref_cast_temporaries, 'class P\n{\n    // bool __cdecl P::*(const P&)\n    P();\n    /* (Q&)\n    f(); */\n};\n'),
      ['    P();\n', '    f(); */'], ['fnvnx_lvalue'])
check('RCAST1 `//` inside a string literal does not hide the cast after it',
      run(s1.r_ref_cast_temporaries, 'g("a//b", (const M&)h(n));\n'), ['(const M&)fnvnx_lvalue(h(n))'])
_shutil.rmtree(_c, ignore_errors=True)
# ---- S1-refs: NSCALL1 (a namespace's own prototype of a CRT name, called unqualified) and DUPINT1-RET (units.json)
_s3 = tempfile.mkdtemp()
os.makedirs(os.path.join(_s3, 'build')); os.makedirs(os.path.join(_s3, 'src'))
_json.dump({'units': [
    {'source': 'src/n.cpp', 'symbols': {'?strcpy_s@ns1@@YAHPADIPBD@Z': '0x00401100'}},
    {'source': 'src/g.cpp', 'symbols': {'?strcpy_s@ns1@@YAHPADIPBD@Z': '0x00401100',
                                        '?strcpy_s@@YAHPADIPBD@Z': '0x00401100'}},
    {'source': 'src/d.cpp', 'symbols': {'_Fmt': '0x00401200', '?Fmt@@YAPADPADIPBDZZ': '0x00401200',
                                        '_Pick': '0x00401300', '?Pick@@YAHPAXE@Z': '0x00401300'}}]},
           open(os.path.join(_s3, 'build', 'units.json'), 'w'))
_saved, s1._UNIT_SYMBOLS = s1._UNIT_SYMBOLS, None
_n = ('namespace ns1 {\nint strcpy_s(char* d, unsigned int n, const char* s);\n'
      'struct Name { operator const char*() const; };\n'
      'void f(char* b, const Name& k)\n{\n    strcpy_s(b, 260, k);\n    if (!strcpy_s(b, 4, "abc")) return;\n'
      '    // strcpy_s(b, 1, k) in a comment\n}\n}\n')
check('NSCALL1 unqualified calls of N\'s own CRT prototype -> N::name',
      run(s1.r_namespace_crt_calls, _n, os.path.join(_s3, 'src', 'n.cpp')),
      ['ns1::strcpy_s(b, 260, k);', '!ns1::strcpy_s(b, 4, "abc")', 'int strcpy_s(char* d',
       '// strcpy_s(b, 1, k) in a comment'], ['ns1::ns1::'])
check('NSCALL1 the unit also binds the global spelling: untouched',
      run(s1.r_namespace_crt_calls, _n, os.path.join(_s3, 'src', 'g.cpp')), [], ['ns1::strcpy_s'])
check('NSCALL1 a class member of that name: untouched',
      run(s1.r_namespace_crt_calls, _n.replace('struct Name {', 'struct Name { int strcpy_s(char*, int);'),
          os.path.join(_s3, 'src', 'n.cpp')), [], ['ns1::strcpy_s'])
_d = ('extern "C" int __cdecl Fmt(char* b, int n, const char* f, ...);\n'
      'char* __cdecl Fmt(char* d, unsigned int n, const char* f, ...);\n'
      'void g(char* b, size_t n)\n{\n    Fmt(b, n + 1, "%d", 1);\n    if (n) Fmt(b, n, "x"); else Fmt(b, 2, "y");\n}\n')
check('DUPINT1-RET other return type, results unused: later prototype dropped',
      run(s1.r_duplicate_int_overloads, _d, os.path.join(_s3, 'src', 'd.cpp')),
      ['extern "C" int __cdecl Fmt(char* b, int n', '/* fnv-nx DUPINT1 */'], ['char* __cdecl Fmt'])
check('DUPINT1-RET a call uses the result: untouched',
      run(s1.r_duplicate_int_overloads, _d.replace('    Fmt(b, n + 1', '    char* p = Fmt(b, n + 1'),
          os.path.join(_s3, 'src', 'd.cpp')), ['char* __cdecl Fmt'], ['fnv-nx DUPINT1'])
check('DUPINT1-RET a narrower integer parameter: untouched',
      run(s1.r_duplicate_int_overloads, 'extern "C" void Pick(void* p, int n);\nint Pick(void* p, unsigned char n);\n'
          'void h(void* p) { Pick(p, 1); }\n', os.path.join(_s3, 'src', 'd.cpp')), ['int Pick('], ['fnv-nx DUPINT1'])
s1._UNIT_SYMBOLS = _saved
_shutil.rmtree(_s3, ignore_errors=True)

check('COND1 an explicit G(bool): the conditional is bool, untouched (V1 C2)',
      run(s1.r_conditional_bool_gates, _G.replace('Gate(bool b)', 'explicit Gate(bool b)') +
          'void f(N* n) { if (!n ? false : n->test(0.5f)) g(); }\n'), [], ['Gate(false)'])
check('COND1 `!x.f(a)` is bool already: untouched (V1 C3)',
      run(s1.r_conditional_bool_gates, _G + 'void f(N* n) { bool r = !n ? false : !n->test(0.5f); }\n'),
      [], ['Gate(false)'])

# ---- S1-refs: FINL1 (`__forceinline` on a declaration whose body the unit does not have)
_F = ('struct A {\n    __forceinline ~A();\n    __forceinline int get(int i);\n    __forceinline int get(int i) const;\n'
      '    __forceinline int put(int i);\n    __forceinline int one();\n};\n'
      'struct B { int one() { return 1; } };\n'
      'struct T { template<class X> static __forceinline void d(X* x, int n, Pod); '
      'template<class X> static __forceinline void d(X* x, int n, Cls); };\n'
      'template<class X> __forceinline void T::d(X* x, int n, Pod) {}\n'
      'struct H : A { H() : A() {} };\n'
      'int A::get(int i) const { return i; }\n__forceinline int A::put(int i) { return i; }\n')
_o = run(s1.r_forceinline_declarations, _F)
check('FINL1 body-less declarations of functions the unit does not define lose __forceinline',
      _o, ['/* fnv-nx FINL1: __forceinline */ ~A();', '/* fnv-nx FINL1: __forceinline */ int get(int i);',
           '/* fnv-nx FINL1: __forceinline */ int one();',
           '/* fnv-nx FINL1: __forceinline */ void d(X* x, int n, Cls);'])
check('FINL1 defined ones keep it (const overload, out-of-class body, Pod overload)',
      _o, ['__forceinline int get(int i) const;', '__forceinline int put(int i);',
           'static __forceinline void d(X* x, int n, Pod);'])
check('FINL1 a class template\'s member defined out of class keeps it (the class, not the template parameter)',
      run(s1.r_forceinline_declarations,
          'template <class K, class E>\nclass DU\n{\n\tpublic:\n\t\tstatic __forceinline void __cdecl up(K& k, E& e);\n};\n'
          'template <class K, class E>\nvoid __cdecl DU<K, E>::up(K& k, E& e)\n{\n}\n'),
      ['static __forceinline void __cdecl up(K& k, E& e);'], ['FINL1'])
check('FINL1 a class named after a nested __declspec(align(16)) keeps it on its defined constructor',
      run(s1.r_forceinline_declarations,
          'struct __declspec(align(16)) BS : R {\n    __declspec(nothrow) __forceinline BS() ;\n};\n'
          '    __declspec(nothrow) BS::BS()  {\n    }\n'),
      ['__declspec(nothrow) __forceinline BS() ;'], ['FINL1'])
check('FINL1 headers are left alone',
      run(s1.r_forceinline_declarations, 'struct A { __forceinline ~A(); };\n', '/nonexistent/src/x.h'), [], ['FINL1'])

# ---- S1-refs: EBO2 (an empty base of a class that introduces its own vfptr goes after the vptr, as in MSVC)
_E = ('struct Lst\n{\n    bool IsEmpty();\n    Lst* Next();\n};\n'
      'struct Holder : public Lst\n{\n    virtual ~Holder();\n    int m_iCount;\n};\n'
      'struct Plain : public Lst\n{\n    int x;\n};\n'
      'bool f(Holder* h) { return ((Lst*)h)->IsEmpty() || h->Next() == 0; }\n')
_eo = run(s1.r_vfptr_empty_base, _E)
check('EBO2 FNVNX_EBO2 first in the empty base of a class introducing a vfptr', _eo,
      ['struct Lst\n{ FNVNX_EBO2 /* fnv-nx EBO2 */\n'], ['struct Holder : public Lst\n{ FNVNX'])
check('EBO2 a base used as an object is left alone',
      run(s1.r_vfptr_empty_base, _E + 'Lst g_list;\n'), [], ['FNVNX_EBO2'])
check('EBO2 no class introducing a vfptr: nothing',
      run(s1.r_vfptr_empty_base, _E.replace('virtual ~Holder();', '~Holder();')), [], ['FNVNX_EBO2'])
check('EBO2 headers are left alone',
      run(s1.r_vfptr_empty_base, _E, '/nonexistent/src/x.h'), [], ['FNVNX_EBO2'])
check('EBO2 a base with data: nothing',
      run(s1.r_vfptr_empty_base, _E.replace('bool IsEmpty();', 'int head;\n    bool IsEmpty();')), [], ['FNVNX_EBO2'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _cE = os.path.join(tempfile.mkdtemp(), 'e2.cpp')
    _mE = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h'))
           if l.startswith('#define FNVNX_EBO2 ')]
    open(_cE, 'w').write('#include <stddef.h>\n' + ''.join(_mE) + _eo +
                         'Holder::~Holder() {}\nstatic Holder g_h;\nstatic_assert(sizeof(Holder) == 8, "size");\n'
                         'static_assert(sizeof(Plain) == 4, "plain");\n'
                         'int off() { return (int)((char*)(Lst*)&g_h - (char*)&g_h); }\n'
                         'static_assert(offsetof(Holder, m_iCount) == 4, "count");\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-w', '-o', '-', _cE],
                         capture_output=True, text=True)
    check('EBO2 output: the base after the vptr (+4), members and sizes unchanged (ILP32)',
          _r.stderr or _r.stdout, ['mov\tw0, 4'], ['error'])

# ---- S1-refs: REC1 (D20: reconstructed bodies from a recon directory, applied only when FNVNX_RECON is given)
_rc = tempfile.mkdtemp()
os.makedirs(os.path.join(_rc, 'dec', 'build')); os.makedirs(os.path.join(_rc, 'dec', 'src'))
os.makedirs(os.path.join(_rc, 'recon', 'r1'))
_json.dump({'units': [{'source': 'src/h.cpp', 'functions': [{'address': '0x00401000', 'symbol': '?f@@YAXXZ', 'size': 8}],
                       'symbols': {}}]}, open(os.path.join(_rc, 'dec', 'build', 'units.json'), 'w'))
_json.dump({'format': 'fnvnx-recon/1', 'unit': 'src/h.cpp', 'append': 'body.cpp',
            'functions': [{'address': '0x00401010', 'symbol': '??0H@@QAE@XZ', 'size': 16}],
            'symbols': {'?Init@H@@QAEXXZ': '0x00401100'}}, open(os.path.join(_rc, 'recon', 'r1', 'recon.json'), 'w'))
open(os.path.join(_rc, 'recon', 'r1', 'body.cpp'), 'w').write('struct H { H(); void Init(); };\n// @fnv 0x00401010\nH::H() { Init(); }\n')
_hp = os.path.join(_rc, 'dec', 'src', 'h.cpp')
prepare.__dict__['_fnvnx_recon'] = None
os.environ.pop('FNVNX_RECON', None)
check('REC1 without FNVNX_RECON: nothing', run(s1.r_recon_bodies, '// @fnv 0x00401000\nvoid f() {}\n', _hp), [], ['REC1'])
os.environ['FNVNX_RECON'] = os.path.join(_rc, 'recon')
prepare.__dict__['_fnvnx_recon'] = None
_sta = Counter()
_ro = s1.r_recon_bodies(_hp, '// @fnv 0x00401000\nvoid f() {}\n', _sta)
check('REC1-APPEND counts each appended recon body', str(dict(_sta)), ["'REC1-APPEND': 1"])
check('REC1 appends the unit\'s reconstructed body', _ro, ['void f() {}', 'fnv-nx REC1', 'H::H() { Init(); }'])
check('REC1 other units untouched', run(s1.r_recon_bodies, 'int g;\n', os.path.join(_rc, 'dec', 'src', 'o.cpp')),
      [], ['REC1'])
s1.add_recon_definitions(os.path.join(_rc, 'dec'), Counter())
_ru = _json.load(open(os.path.join(_rc, 'dec', 'build', 'units.json')))['units'][0]
check('REC1 POST hook adds the recipe entry and bindings', _json.dumps(_ru),
      ['"??0H@@QAE@XZ"', '"?Init@H@@QAEXXZ": "0x00401100"', '"?f@@YAXXZ"'])
# a recon of an address a unit already defines (the decomp gained it) is skipped
_json.dump({'units': [{'source': 'src/h.cpp', 'functions': [{'address': '0x00401010', 'symbol': '??0H@@QAE@XZ', 'size': 16}],
                       'symbols': {}}]}, open(os.path.join(_rc, 'dec', 'build', 'units.json'), 'w'))
prepare.__dict__['_fnvnx_recon'] = None
_st = Counter()
_ro2 = s1.r_recon_bodies(_hp, 'void f() {}\n', _st)
check('REC1 an address the recipe already defines: skipped (REC1-DUP)', _ro2 + str(dict(_st)), ['REC1-DUP'], ['H::H()'])
check('REC1-DUP is reported as rejected (count -3, the placed address)',
      repr(prepare.__dict__.get('_fnvnx_recon_rejected')), ["-3, 'functions placed by another unit now: 0x00401010'"])
# a recon with only `symbols` (names bound to the exe's functions, no body): the bindings persist (R1, Havok agents)
os.makedirs(os.path.join(_rc, 'recon', 'r2'))
_json.dump({'format': 'fnvnx-recon/1', 'unit': 'src/h.cpp', 'symbols': {'?Agent1@@YAXXZ': '0x00dcd6a0'}},
           open(os.path.join(_rc, 'recon', 'r2', 'recon.json'), 'w'))
prepare.__dict__['_fnvnx_recon'] = None
_st = Counter()
_ro3 = s1.r_recon_bodies(_hp, 'void f() {}\n', _st)
s1.add_recon_definitions(os.path.join(_rc, 'dec'), _st)
_ru = _json.load(open(os.path.join(_rc, 'dec', 'build', 'units.json')))['units'][0]
check('REC1 symbols-only recon: bindings persist, no body', _json.dumps(_ru) + _ro3 + str(dict(_st)),
      ['"?Agent1@@YAXXZ": "0x00dcd6a0"', "'REC1-SYM': 1"], ['fnv-nx REC1: reconstructed'])
# REC1-REPL: `replace` pairs edit the unit's own text, in order, before the append; each `old` exactly once
_kp = os.path.join(_rc, 'dec', 'src', 'k.cpp')
_ku = {'units': [{'source': 'src/k.cpp', 'functions': [], 'symbols': {}}]}
def _krecon(name, rec, body=None):
    os.makedirs(os.path.join(_rc, 'recon', name))
    rec = dict({'format': 'fnvnx-recon/1', 'unit': 'src/k.cpp'}, **rec)
    _json.dump(rec, open(os.path.join(_rc, 'recon', name, 'recon.json'), 'w'))
    if body is not None:
        open(os.path.join(_rc, 'recon', name, 'body.cpp'), 'w').write(body)
    prepare.__dict__['_fnvnx_recon'] = None
_ktext = 'void g();\nvoid f() { g(); }\n'
_json.dump(_ku, open(os.path.join(_rc, 'dec', 'build', 'units.json'), 'w'))
os.environ['FNVNX_RECON'] = os.path.join(_rc, 'recon', 'k1')
_krecon('k1', {'append': 'body.cpp', 'symbols': {'?h@@YAXXZ': '0x00402000'},
               'replace': [{'old': 'void f() { g(); }', 'new': 'void f() { h(); }'},
                           {'old': 'void g();', 'new': 'void g();\nvoid h();'}]},
        '/* appended */ void f2() { g(); }\n')
_st = Counter()
_ko = s1.r_recon_bodies(_kp, _ktext, _st)
check('REC1-REPL the pairs apply in order, then the body is appended',
      _ko + str(dict(_st)), ['void g();\nvoid h();\nvoid f() { h(); }', 'fnv-nx REC1', '/* appended */ void f2() { g(); }',
                             "'REC1-REPL': 2"], ['void f() { g(); }'])
check('REC1-REPL applied before the append: the appended body is not edited',
      _ko[_ko.index('fnv-nx REC1'):], ['void f2() { g(); }'], ['h()'])
s1.add_recon_definitions(os.path.join(_rc, 'dec'), _st)
_ku2 = _json.load(open(os.path.join(_rc, 'dec', 'build', 'units.json')))['units'][0]
check('REC1-REPL with symbols: REC1-SYM still persists the binding', _json.dumps(_ku2) + str(dict(_st)),
      ['"?h@@YAXXZ": "0x00402000"', "'REC1-SYM': 1"])
def _kfails(name, rec, text):
    """a recon whose replace does not apply: rejected whole for the unit (no edit, no append), the prepare goes on"""
    os.environ['FNVNX_RECON'] = os.path.join(_rc, 'recon', name)
    _krecon(name, rec, '/* appended body */\n')
    prepare.__dict__['_fnvnx_recon_rejected'] = []
    st = Counter()
    try:
        out = s1.r_recon_bodies(_kp, text, st)
    except RuntimeError as e:
        return 'raised: ' + str(e)
    rej = prepare.__dict__['_fnvnx_recon_rejected']
    return ('unchanged ' if out == text else 'CHANGED ') + str(dict(st)) + ' ' + repr(rej)
check('REC1-REPL an old that occurs twice: the recon is rejected, the prepare goes on',
      _kfails('k2', {'replace': [{'old': 'g()', 'new': 'h()'}]}, 'void a() { g(); }\nvoid b() { g(); }\n'),
      ['unchanged', "'REC1-REJECT': 1", ", 0, 2, 'g()')"], ['raised', 'appended body'])
check('REC1-REPL a missing old: rejected',
      _kfails('k3', {'replace': [{'old': 'void q();', 'new': 'void r();'}]}, _ktext), ['unchanged', "'REC1-REJECT': 1", ', 0, 0,'],
      ['raised'])
check('REC1-REPL an old found only in the appended body: rejected (replace runs before the append)',
      _kfails('k4', {'append': 'body.cpp', 'replace': [{'old': 'void f2()', 'new': 'void f3()'}]}, _ktext),
      ['unchanged', "'REC1-REJECT': 1"], ['raised', 'appended body'])
check('REC1-REPL a replace-only recon is a recon (no body, no symbols): rejected the same way',
      _kfails('k5', {'replace': [{'old': 'void q();', 'new': 'x'}]}, _ktext), ['unchanged', "'REC1-REJECT': 1"], ['raised'])
check('REC1-REPL never half-applies: pair 0 matches, pair 1 does not -> nothing applied, no append',
      _kfails('k6', {'append': 'body.cpp', 'replace': [{'old': 'void g();', 'new': 'void G();'},
                                                        {'old': 'nowhere', 'new': 'x'}]}, _ktext),
      ['unchanged', ", 1, 0, 'nowhere')"], ['raised', 'void G();', 'REC1-REPL\': 1'])
check('REC1-REPL a malformed pair: rejected',
      _kfails('k7', {'replace': [{'old': '', 'new': 'x'}]}, _ktext), ['unchanged', ', 0, -1,'], ['raised'])
def _kdup(name, body, text):
    os.environ['FNVNX_RECON'] = os.path.join(_rc, 'recon', name)
    _krecon(name, {'append': 'body.cpp'}, body)
    prepare.__dict__['_fnvnx_recon_rejected'] = []
    st = Counter(); out = s1.r_recon_bodies(_kp, text, st)
    return ('appended ' if 'fnv-nx REC1' in out else 'unchanged ') + str(dict(st)) + ' ' + repr(prepare.__dict__['_fnvnx_recon_rejected'])
_dt = 'struct E { int a; };\n// @fnv 0x00d2cac0\nstatic void __declspec(noinline)\n    Copy(E* d, const E* s, unsigned int n)\n{\n    d[0] = s[0];\n}\n'
check('REC1-DUP-DEF an append that redefines a function the unit defines (split head): rejected, not appended',
      _kdup('d1', '#if !defined(_MSC_VER)\nstatic void Copy(E* apDest, const E* apSource, unsigned int auiByteCount)\n{\n}\n#endif\n', _dt),
      ['unchanged', "'REC1-REJECT': 1", '-2, ', 'Copy(E*,E*,unsignedint)'], ['appended'])
check('REC1-DUP-DEF the same body when a rule left only the declaration: appended',
      _kdup('d2', 'static void Copy(E* d, const E* s, unsigned int n)\n{\n}\n',
            'struct E { int a; };\nstatic void Copy(E* d, const E* s, unsigned int n); /* fnv-nx STAND1 */\n'),
      ['appended'], ['REC1-REJECT'])
check('REC1-DUP-DEF an overload (other parameters) and a member of another class are not duplicates',
      _kdup('d3', 'void Copy(E* d, int n)\n{\n}\nvoid F::Copy(E* d, const E* s, unsigned int n)\n{\n}\n', _dt),
      ['appended'], ['REC1-REJECT'])
check('REC1-DUP-DEF a definition inside the same namespace counts; another namespace does not',
      _kdup('d4', 'namespace q {\nvoid g()\n{\n}\n}\n', 'namespace q {\nvoid g() { }\n}\nnamespace r {\nvoid h() { }\n}\n'),
      ['unchanged', 'q::g()'], ['appended'])
check('REC1-DUP-DEF ... and the same name in another namespace is appended',
      _kdup('d5', 'namespace p {\nvoid g()\n{\n}\n}\n', 'namespace q {\nvoid g() { }\n}\n'), ['appended'], ['REC1-REJECT'])
_kfails('k8', {'replace': [{'old': '', 'new': 'x'}]}, _ktext)   # one rejection for the report below
os.makedirs(os.path.join(_rc, 'out', 'build'), exist_ok=True)
_st = Counter()
s1.add_recon_definitions(os.path.join(_rc, 'out'), _st)
_rt = open(os.path.join(_rc, 'out', 'build', 'recon_rejected.tsv')).read()
check('REC1-REPL the POST hook lists the rejected recons in build/recon_rejected.tsv',
      _rt + str(dict(_st)), ['#recon dir', 'src/k.cpp', "'REC1-REJECTED-RECONS': 1"])
prepare.__dict__['_fnvnx_recon_rejected'] = []
os.environ.pop('FNVNX_RECON', None)
prepare.__dict__['_fnvnx_recon'] = None
_shutil.rmtree(_rc, ignore_errors=True)

# ---- S1-refs: HUNIT1 (explicit instantiations of a header unit's listed template members)
_hu = run(s1.r_header_unit_instantiations, '#include "nitarray.inl"\nvoid NiTArray_UnitReference() {}\n',
          '/nonexistent/src/gamebryo2.2/corelibs/nimain/nitarray_h_unit.cpp')
check('HUNIT1 the listed members are explicitly instantiated', _hu,
      ['template void NiTArray<ActorCombatGroup*, NiTMallocInterface<ActorCombatGroup*> >::SetAt(unsigned int, ActorCombatGroup*&);',
       'fnv-nx HUNIT1'])
check('HUNIT1 once only', str(run(s1.r_header_unit_instantiations, _hu,
      '/nonexistent/src/gamebryo2.2/corelibs/nimain/nitarray_h_unit.cpp').count('fnv-nx HUNIT1')), ['1'])
check('HUNIT1 other units untouched', run(s1.r_header_unit_instantiations, 'int x;\n', '/nonexistent/src/x_h_unit.cpp'),
      [], ['HUNIT1'])

# ---- S1-layout: EBO3 (an empty base after a base with data goes after it, as in MSVC)
_E3 = ('struct NodeBase0\n{\n    int a, b, c, d;\n};\nstruct NodeBase16\n{\n    NodeBase16* Next(NodeBase16* p);\n};\n'
       'struct Node : NodeBase0, NodeBase16\n{\n};\nstruct Node2 : NodeBase0, NodeBase16\n{\n    int x;\n};\n'
       'void f(Node* n) { static_cast<NodeBase16*>(n)->Next(0); }\n')
_e3o = run(s1.r_later_empty_base, _E3)
check('EBO3 FNVNX_EBO2 first in an empty base that follows a base with data', _e3o,
      ['struct NodeBase16\n{ FNVNX_EBO2 /* fnv-nx EBO3 */\n'], ['struct NodeBase0\n{ FNVNX'])
check('EBO3 an empty base used as an object is left alone',
      run(s1.r_later_empty_base, _E3 + 'NodeBase16 g_e;\n'), [], ['FNVNX_EBO2'])
check('EBO3 the empty base first (or after a base of another file): nothing',
      run(s1.r_later_empty_base, _E3.replace('NodeBase0, NodeBase16', 'NodeBase16, NodeBase0')), [], ['FNVNX_EBO2'])
check('EBO3 a virtual base: nothing',
      run(s1.r_later_empty_base, _E3.replace('Node : NodeBase0, NodeBase16', 'Node : NodeBase0, virtual NodeBase16')),
      [], ['FNVNX_EBO2'])
check('EBO3 headers are left alone', run(s1.r_later_empty_base, _E3, '/nonexistent/src/x.h'), [], ['FNVNX_EBO2'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _c3 = os.path.join(tempfile.mkdtemp(), 'e3.cpp')
    _m3 = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h'))
           if l.startswith('#define FNVNX_EBO2 ')]
    # MSVC 2008 (/d1reportSingleClassLayout): Node size 16 with NodeBase16 at 16; Node2 size 20, x at 16
    open(_c3, 'w').write('#include <stddef.h>\n' + ''.join(_m3) + _e3o +
                         'static Node g_n;\nstatic_assert(sizeof(Node) == 16, "size");\n'
                         'static_assert(sizeof(Node2) == 20 && offsetof(Node2, x) == 16, "node2");\n'
                         'int off() { return (int)((char*)(NodeBase16*)&g_n - (char*)&g_n); }\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-w', '-o', '-', _c3],
                         capture_output=True, text=True)
    check('EBO3 output: the empty base at +16, after the base with data; sizes as MSVC (ILP32)',
          _r.stderr or _r.stdout, ['mov\tw0, 16'], ['error'])

# ---- S1-layout: IDTOR1 (an implicit destructor the recipe binds is declared, so GCC calls it by name)
_i1 = tempfile.mkdtemp()
os.makedirs(os.path.join(_i1, 'src')); os.makedirs(os.path.join(_i1, 'build'))
_json.dump({'units': [
    {'source': 'src/u.cpp', 'flags': ['/MT', '/Od'], 'symbols': {
        '??1List@ns@@QAE@XZ': '0x0046ffb0', '??1Own@@QAE@XZ': '0x0046ffb0', '??1Virt@@UAE@XZ': '0x0046ffb0',
        '??1Far@@QAE@XZ': '0x00999999', '??1Inner@Outer@@QAE@XZ': '0x0046ffb0'},
     'functions': [{'address': '0x0046ffb0', 'symbol': '??1Lst@@QAE@XZ', 'size': 4}]}]},
           open(os.path.join(_i1, 'build', 'units.json'), 'w'))
_I1 = ('struct ListX\n{\n    ~ListX();\n};\nnamespace ns {\nstruct List\n{\n    int dummy;\n    void RemoveAll();\n'
       '    ListX m_uiX;\n};\nstruct Holder\n{\n    List m_kNode;\n    ~Holder();\n};\n'
       'Holder::~Holder() { m_kNode.RemoveAll(); }\n}\n'
       'struct Own\n{\n    ListX x;\n    ~Own();\n};\n'
       'struct Far\n{\n    ListX x;\n};\n'
       'class Outer\n{\n    class Inner\n    {\n        ListX x;\n    };\n};\n'
       'template <class T> struct List2\n{\n    ListX x;\n};\n')
_saved = (s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS)
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
_i1o = run(s1.r_implicit_bound_destructors, _I1, os.path.join(_i1, 'src', 'u.cpp'))
_i1h = run(s1.r_implicit_bound_destructors, _I1, os.path.join(_i1, 'src', 'u.h'))
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = _saved
check('IDTOR1 a class whose implicit destructor the recipe binds (in a namespace, nested) declares it', _i1o,
      ['    ListX m_uiX;\n FNVNX_IDTOR1(List) /* fnv-nx IDTOR1 0x0046ffb0 */\n};',
       '        ListX x;\n     FNVNX_IDTOR1(Inner) /* fnv-nx IDTOR1 0x0046ffb0 */\n};'])
check('IDTOR1 a declared destructor, an undecompiled address, a template, the holder: nothing', _i1o, [],
      ['FNVNX_IDTOR1(Own)', 'FNVNX_IDTOR1(Far)', 'FNVNX_IDTOR1(List2)', 'FNVNX_IDTOR1(Holder)', 'FNVNX_IDTOR1(ListX)'])
check('IDTOR1 headers are left alone', _i1h, [], ['FNVNX_IDTOR1'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _cI = os.path.join(tempfile.mkdtemp(), 'i1.cpp')
    _mI = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h'))
           if l.startswith('#define FNVNX_IDTOR1')]
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-w', '-o', '-', '-x',
                          'c++', '-'], input=''.join(_mI) + _i1o, capture_output=True, text=True)
    check('IDTOR1 output: ~Holder calls ns::List::~List by name, not ListX::~ListX at +4 (ILP32)',
          _r.stderr or _r.stdout, ['\t_ZN2ns4ListD1Ev\n'], ['error', '_ZN5ListXD1Ev'])

# ---- S1-refs: D22 (a listed class's slot-0 placeholder virtual becomes its virtual destructor under GCC)
_d2 = tempfile.mkdtemp()
_json.dump({'format': 'fnvnx-d22/1', 'classes': [{'class': 'N::Map', 'members': ['v0']}, {'class': 'Tbl<>', 'members': ['Unk_00']}]},
           open(os.path.join(_d2, 'd22.json'), 'w'))
os.environ['FNVNX_D22'] = os.path.join(_d2, 'd22.json')
prepare.__dict__['_fnvnx_d22'] = None
_D = ('namespace N {\nstruct Map\n{\n    virtual void v0();\n    virtual int Find(int k);\n    int m_count;\n};\n'
      'struct Derived : public Map\n{\n    virtual void v0();\n    virtual int Find(int k);\n    virtual void Extra();\n};\n}\n'
      'template <class T> class Tbl\n{\npublic:\n    FNVNX_KEEP virtual void Unk_00() {}\n    virtual T Get();\n};\n')
_d22 = run(s1.r_vtable_dtor_slot, _D)
check('D22 placeholder -> FNVNX_D22(C, original)', _d22,
      ['FNVNX_D22(Map, virtual void v0();) /* fnv-nx D22 */', 'FNVNX_D22(Tbl, FNVNX_KEEP virtual void Unk_00() {})'])
check('D22 a derived re-declaration is dropped under GCC', _d22,
      ['FNVNX_D22_DROP(virtual void v0();) /* fnv-nx D22 */', '    virtual void Extra();'])
check('D22 a called placeholder is left alone',
      run(s1.r_vtable_dtor_slot, _D + 'void f(N::Map* m) { m->v0(); }\n'), [], ['FNVNX_D22(Map'])
check('D22 a placeholder defined outside the class is left alone',
      run(s1.r_vtable_dtor_slot, _D + 'void N::Map::v0() { m_count = 0; }\n'), [], ['FNVNX_D22(Map'])
check('D22 a class that declares a destructor is left alone',
      run(s1.r_vtable_dtor_slot, _D.replace('    int m_count;', '    ~Map();\n    int m_count;')), [], ['FNVNX_D22(Map'])
check('D22 an unlisted class or member is left alone',
      run(s1.r_vtable_dtor_slot, _D.replace('namespace N', 'namespace M').replace('Unk_00', 'Unk_01') +
          'int M::Map::Find(int k) { return k; }\n'), [], ['FNVNX_D22'])    # (defines a virtual: not call-only, D26 b)
if _shutil.which('aarch64-linux-gnu-g++'):
    _cD = os.path.join(_d2, 'd22.cpp')
    _mD = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h'))
           if l.startswith('#define FNVNX_D22')]
    open(_cD, 'w').write('#define FNVNX_KEEP\n' + ''.join(_mD) + _d22 +
                         'int slot(N::Derived* d) { return d->Find(3); }\nvoid kill(N::Map* m) { delete m; }\n')
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-w', '-o', '-', _cD],
                         capture_output=True, text=True)
    # Find: exe slot 1 -> GCC slot 2 (D1, D0 first): ILP32 vtable entries are 4 bytes, so offset 8
    check('D22 output: Find at GCC slot 2 (D1/D0 at 0/1), delete through slot 1', _r.stderr or _r.stdout,
          [', 8]', ', 4]'], ['error'])
os.environ.pop('FNVNX_D22', None)
prepare.__dict__['_fnvnx_d22'] = None
_shutil.rmtree(_d2, ignore_errors=True)


# ---- S1-rules: VLIT2 (a vptr literal by the exe's vftables: slot 0 at the store's offset, the function's class)
_vl = tempfile.mkdtemp(); os.makedirs(os.path.join(_vl, 'src')); os.makedirs(os.path.join(_vl, 'build'))
_json.dump({'units': [{'source': 'src/a.cpp', 'symbols': {}, 'functions': [{'address': '0x00487220', 'symbol': '?f@@YAXXZ', 'size': 4}]}]},
           open(os.path.join(_vl, 'build', 'units.json'), 'w'))
_json.dump({'format': 'fnvnx-vlit/1', 'slot0': {'00487220': [['0101cb94', 0], ['0102a28c', 104], ['0102a59c', 104]]},
            'class': {'0101cb94': '.?AVTESHealthForm@@', '0102a28c': '.?AVTESObjectARMA@@', '0102a59c': '.?AVTESObjectARMO@@',
                      '0102a31c': '.?AVTESObjectARMA@@'}}, open(os.path.join(_vl, 'vlit.json'), 'w'))
os.environ['FNVNX_VLIT'] = os.path.join(_vl, 'vlit.json')
prepare.__dict__['_fnvnx_vlit'] = None
_saved_vlit = s1._VLIT; s1._VLIT = None
_VL = ('struct C { C(); };\nC::C()\n{\n    *(int*)((char*)this + 0x0) = (int)&DAT_0102a31c;\n'
       '    *(const char**)((char*)this + 0x68) = " rH";\n    *(void**)((char*)this + 0x68) = (void*)" rH";\n}\n'
       'struct H { void f(); };\nvoid H::f()\n{\n    *reinterpret_cast<const char**>(this) = " rH";\n}\n'
       'struct Z { void g(); };\nvoid Z::g()\n{\n    *(const char**)((char*)this + 0x10) = " rH";\n}\n')
_vlo = run(s1.r_vptr_literals_exe, _VL, os.path.join(_vl, 'src', 'a.cpp'))
check('VLIT2 the store at +0x68 takes the vftable of the function\'s class (ARMA, not ARMO); the cast is kept', _vlo,
      ['*(const char**)((char*)this + 0x68) = (char*)&fnvnx_vlit_0102a28c;', '= (void*)(char*)&fnvnx_vlit_0102a28c;',
       'extern "C" char fnvnx_vlit_0102a28c __asm__("DAT_0102a28c"); /* fnv-nx VLIT2 */ C::C()'])
check('VLIT2 a store through this at offset 0: the one vftable with that slot 0 there', _vlo,
      ['*reinterpret_cast<const char**>(this) = (char*)&fnvnx_vlit_0101cb94;'])
check('VLIT2 no vftable with that slot 0 at the offset: left as it is', _vlo,
      ['*(const char**)((char*)this + 0x10) = " rH";'])
os.environ.pop('FNVNX_VLIT', None); prepare.__dict__['_fnvnx_vlit'] = None; s1._VLIT = _saved_vlit

# ---- S1-rules: STAND7 (an out-of-class inline template member stand-in bound elsewhere -> MSVC only)
_s7 = tempfile.mkdtemp(); os.makedirs(os.path.join(_s7, 'src')); os.makedirs(os.path.join(_s7, 'build'))
_json.dump({'units': [{'source': 'src/u.cpp', 'flags': ['/MT', '/Od', '/Ob1'],
                       'symbols': {'?assign@?$basic_string@DU?$char_traits@D@std@@U?$allocator@D@2@@std@@QAEAAV12@ABV12@II@Z': '0x0044a580'},
                       'functions': [{'address': '0x00b2b910', 'symbol': '?f@@YAXXZ', 'size': 8}]},
                      {'source': 'src/v.cpp', 'symbols': {}, 'functions': [{'address': '0x0044a580', 'symbol': '?assign@x', 'size': 8}]}]},
           open(os.path.join(_s7, 'build', 'units.json'), 'w'))
_S7 = ('template <class C, class T, class A>\ninline basic_string<C, T, A>& basic_string<C, T, A>::assign(const basic_string<C, T, A>& r,'
       ' unsigned int o, unsigned int n)\n{\n    if (o == 0x7f3f3f3f) { _Tidy(true); }\n    return *this;\n}\n'
       '// @fnv 0x00b2b910\nvoid f() {}\n')
_saved = (s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS)
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
_s7o = run(s1.r_template_member_stand_ins, _S7, os.path.join(_s7, 'src', 'u.cpp'))
_json.dump({'units': [{'source': 'src/u.cpp', 'flags': ['/MT', '/O2'], 'symbols': {'?assign@?$basic_string@DU?$char_traits@D@std@@U?$allocator@D@2@@std@@QAEAAV12@ABV12@II@Z': '0x0044a580'},
                       'functions': []}, {'source': 'src/v.cpp', 'symbols': {}, 'functions': [{'address': '0x0044a580', 'symbol': '?assign@x', 'size': 8}]}]},
           open(os.path.join(_s7, 'build', 'units.json'), 'w'))
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
_s7b = run(s1.r_template_member_stand_ins, _S7, os.path.join(_s7, 'src', 'u.cpp'))
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = _saved
check('STAND7 an inline out-of-class template member bound to another unit\'s address: MSVC only', _s7o,
      ['#ifdef _MSC_VER /* fnv-nx STAND7 */\ntemplate <class C', '    return *this;\n}\n#endif\n'])
check('STAND7 not in an /Od /Ob1 unit: nothing', _s7b, [], ['STAND7'])

# ---- S1-rules: DTORB1 (a destructor stand-in calls the address its ??1 is bound to, under GCC)
_db = tempfile.mkdtemp(); os.makedirs(os.path.join(_db, 'src')); os.makedirs(os.path.join(_db, 'build'))
_json.dump({'units': [{'source': 'src/u.cpp', 'symbols': {'??1S@@UAE@XZ': '0x004aa280', '??1T@@UAE@XZ': '0x004aa280'},
                       'functions': [{'address': '0x004aa1e0', 'symbol': '??_GS@@UAEPAXI@Z', 'size': 44},
                                     {'address': '0x004aa300', 'symbol': '??_GT@@UAEPAXI@Z', 'size': 44},
                                     {'address': '0x004aa280', 'symbol': '??1Y@@UAE@XZ', 'size': 92}]}]},
           open(os.path.join(_db, 'build', 'units.json'), 'w'))
_DB = ('struct S\n{\n    virtual ~S();\n    int x;\n};\n// @fnv 0x004aa1e0\nS::~S()\n{}\n'
       'struct M { ~M(); };\nstruct T\n{\n    virtual ~T();\n    M m;\n};\n// @fnv 0x004aa300\nT::~T()\n{}\n')
_saved = (s1._UNIT_SYMBOLS, prepare.__dict__.get('_fnvnx_dtorb_funcs'))
s1._UNIT_SYMBOLS = None; prepare.__dict__['_fnvnx_dtorb_funcs'] = None
_dbo = run(s1.r_bound_dtor_stand_ins, _DB, os.path.join(_db, 'src', 'u.cpp'))
s1._UNIT_SYMBOLS = _saved[0]; prepare.__dict__['_fnvnx_dtorb_funcs'] = _saved[1]
check('DTORB1 the stand-in body calls the bound address under GCC', _dbo,
      ['S::~S()\n{ FNVNX_DTORB_BEGIN(004aa280) /* fnv-nx DTORB1 */ FNVNX_DTORB_END }'])
check('DTORB1 a class with a member that has a destructor: nothing', _dbo, [], ['T::~T()\n{ FNVNX_DTORB'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _mB = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h'))
           if l.startswith('#define FNVNX_DTORB')]
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-w', '-o', '-', '-x', 'c++',
                          '-'], input=''.join(_mB) + _dbo.split('struct M')[0], capture_output=True, text=True)
    check('DTORB1 output: S::~S calls __fnv_004aa280 (ILP32)', _r.stderr or _r.stdout, ['__fnv_004aa280'], ['error'])

# ---- S1-rules: D26 (b) a call-only placeholder declaration takes FNVNX_D22 without a list
_save_d22 = os.environ.pop('FNVNX_D22', None); _save_rc = os.environ.pop('FNVNX_RECON', None)
prepare.__dict__['_fnvnx_d22'] = None
_CO = ('struct HeapStandIn\n{\n    virtual void v0();\n    virtual void v1();\n    virtual void* Allocate(unsigned n);\n};\n'
       'void* f(HeapStandIn* h) { return h->Allocate(4); }\n')
_coo = run(s1.r_vtable_dtor_slot, _CO)
check('D26 call-only: the slot-0 placeholder becomes FNVNX_D22 (no list)', _coo,
      ['FNVNX_D22(HeapStandIn, virtual void v0();) /* fnv-nx D22 */', 'virtual void v1();'])
check('D26 call-only: one virtual only, a defined virtual, a base, a called placeholder, a derived class: nothing',
      run(s1.r_vtable_dtor_slot, 'struct A\n{\n    virtual void v0();\n};\n') +
      run(s1.r_vtable_dtor_slot, _CO + 'int g_v1;\nvoid HeapStandIn::v1() { g_v1 = 1; }\n') +
      run(s1.r_vtable_dtor_slot, _CO.replace('struct HeapStandIn\n', 'struct HeapStandIn : B\n')) +
      run(s1.r_vtable_dtor_slot, _CO + 'void g(HeapStandIn* h) { h->v0(); }\n') +
      run(s1.r_vtable_dtor_slot, _CO + 'struct D : HeapStandIn\n{\n    void v0() {}\n};\n'),
      [], ['FNVNX_D22'])
check('D22-INLINE: a derived class that defines other members -> the base takes FNVNX_D22I (inline empty ~C)',
      run(s1.r_vtable_dtor_slot, _CO + 'struct D : HeapStandIn\n{\n    void v1() {}\n};\n'),
      ['FNVNX_D22I(HeapStandIn, virtual void v0();)'])
check('D26 call-only: a derived class that defines none of its virtuals does not block it',
      run(s1.r_vtable_dtor_slot, _CO + 'struct D : HeapStandIn\n{\n    virtual void v1();\n};\n'),
      ['FNVNX_D22(HeapStandIn, virtual void v0();)'])
_cc = run(s1.r_vtable_dtor_slot, _CO.replace('virtual void v0();', 'virtual void* v0(unsigned int);') +
          'void g(HeapStandIn* h) { h->v0(1); }\nstruct Other\n{\n    void* Get();\n};\n')
check('D26 call-only: a statement call of the slot-0 placeholder becomes FNVNX_D22_CALL', _cc,
      ['FNVNX_D22(HeapStandIn, virtual void* v0(unsigned int);)', 'FNVNX_D22_CALL(h, v0, 1); /* fnv-nx D22 */'])
check('D26 call-only: another class with a virtual of that name not converted: neither is rewritten',
      run(s1.r_vtable_dtor_slot, _CO.replace('virtual void v0();', 'virtual void* v0(unsigned int);') +
          'void g(HeapStandIn* h) { h->v0(1); }\nstruct Other\n{\n    virtual void* v0(unsigned int);\n    int x;\n};\n'
          'void* Other::v0(unsigned int) { return 0; }\n'), [], ['FNVNX_D22'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _mD2 = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h'))
            if l.startswith(('#define FNVNX_D22', 'template <class T> inline void fnvnx_d22_vdtor'))]
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-w', '-o', '-', '-x', 'c++',
                          '-'], input=''.join(_mD2) + _cc, capture_output=True, text=True)
    check('D26 call-only output: h->v0(1) deletes through slot 1 (D0, +4)', _r.stderr or _r.stdout, [', 4]'], ['error'])
# a header's declaration whose member another file defines or calls is left alone (try1of1 4a15e91c)
_th = tempfile.mkdtemp(); os.makedirs(os.path.join(_th, 'src')); os.makedirs(os.path.join(_th, 'build'))
_json.dump({'units': []}, open(os.path.join(_th, 'build', 'units.json'), 'w'))
open(os.path.join(_th, 'src', 'user.cpp'), 'w').write('void g(HeapStandIn* h) { h->v1(); }\n')
prepare.__dict__.pop('_fnvnx_d22_refs', None)
check('D26 a header declaration: another file calling a later member does not block it (same layout everywhere)',
      run(s1.r_vtable_dtor_slot, _CO, os.path.join(_th, 'src', 'x.h')), ['FNVNX_D22(HeapStandIn'])
open(os.path.join(_th, 'src', 'user.cpp'), 'w').write('#include "sub/X.h"\nvoid g(HeapStandIn* h) { h->v0(); }\n')
prepare.__dict__.pop('_fnvnx_d22_refs', None)
check('D26 a header declaration whose placeholder an including file calls or defines: nothing (D22-EXTERN)',
      run(s1.r_vtable_dtor_slot, _CO, os.path.join(_th, 'src', 'x.h')), [], ['FNVNX_D22'])
open(os.path.join(_th, 'src', 'user.cpp'), 'w').write('#include "other.h"\nvoid g(HeapStandIn* h) { h->v0(); }\n')
prepare.__dict__.pop('_fnvnx_d22_refs', None)
check('D26 ... a file that does not include the header does not block it',
      run(s1.r_vtable_dtor_slot, _CO, os.path.join(_th, 'src', 'x.h')), ['FNVNX_D22(HeapStandIn'])
open(os.path.join(_th, 'src', 'mid.h'), 'w').write('#include "x.h"\n')
open(os.path.join(_th, 'src', 'user.cpp'), 'w').write('#include "mid.h"\nvoid g(HeapStandIn* h) { h->v0(); }\n')
prepare.__dict__.pop('_fnvnx_d22_refs', None)
check('D26 ... an indirect includer blocks it',
      run(s1.r_vtable_dtor_slot, _CO, os.path.join(_th, 'src', 'x.h')), [], ['FNVNX_D22'])
prepare.__dict__.pop('_fnvnx_d22_refs', None)
prepare.__dict__.pop('_fnvnx_d22_refs', None)
check('D26 a placeholder call with a non-literal argument is not a destructor call',
      run(s1.r_vtable_dtor_slot, _CO.replace('virtual void v0();', 'virtual void* v0(void*);') +
          'void g(HeapStandIn* h, void* r) { h->v0(r); }\n'), [], ['FNVNX_D22'])
_CE = ('struct Q\n{\n    virtual void f00()\n    {}\n    virtual void f01()\n    {}\n    virtual int f02(unsigned v)\n    {\n        return 0;\n    }\n};\n'
       'int g(Q* q) { return q->f02(1); }\n')
check('D26 call-only: empty or return-a-constant inline placeholders count as declarations (.cpp)',
      run(s1.r_vtable_dtor_slot, _CE), ['FNVNX_D22(Q, virtual void f00()'])
check('D26 call-only: ... not when the file uses the class as an object, nor in a header, nor with a real body',
      run(s1.r_vtable_dtor_slot, _CE + 'Q g_q;\n') + run(s1.r_vtable_dtor_slot, _CE, '/nonexistent/src/q.h') +
      run(s1.r_vtable_dtor_slot, _CE.replace('return 0;', 'return v + 1;')), [], ['FNVNX_D22(Q'])
check('D26 call-only: a named first virtual is not a placeholder',
      run(s1.r_vtable_dtor_slot, _CO.replace('v0()', 'GetName()')), [], ['FNVNX_D22'])
# S5-boot (link 3f7a6c35e923, 0x00e7bc90): a COM interface has no destructor slot; D22 / D26 leave it alone
_COM = ('struct D3DShell\n{\n    virtual void v0();\n    virtual void v1();\n'
        '    virtual unsigned int __stdcall GetAdapterModeCount(unsigned int a, int f);\n};\n'
        'unsigned f(D3DShell* d) { return d->GetAdapterModeCount(0, 22); }\n')
import collections as _collections
_comst = _collections.Counter()
check('D26 COM: a class with a __stdcall virtual is not converted', s1.r_vtable_dtor_slot('/x/src/a.cpp', _COM, _comst) +
      run(s1.r_vtable_dtor_slot, _COM.replace('unsigned int __stdcall', 'HRESULT')) +
      run(s1.r_vtable_dtor_slot, _CO.replace('v1()', 'AddRef()')) +
      run(s1.r_vtable_dtor_slot, _CO.replace('HeapStandIn', 'IDirect3D9')), [], ['FNVNX_D22'])
check('D26 COM: counted D22-COM', str(dict(_comst)), ["'D22-COM': 1"])
import tempfile as _tempfile
prepare.__dict__['_fnvnx_d22_applied'] = []
run(s1.r_vtable_dtor_slot, _CO, '/t/src/a/heap.cpp')
_td = _tempfile.mkdtemp(); s1.d22_write_applied(_td, _collections.Counter(), _collections.Counter())
check('D26 build/d22_applied.tsv lists what prepare converted', open(os.path.join(_td, 'build', 'd22_applied.tsv')).read(),
      ['src/a/heap.cpp\tHeapStandIn\tv0\tcallonly\t\n'])
check('D26 call-only: a numbered sequence P_00, P_01 is a placeholder; P_00 with another second name is not',
      run(s1.r_vtable_dtor_slot, _CO.replace('v0()', 'Impl_00()').replace('v1()', 'Impl_01()')) + '|' +
      run(s1.r_vtable_dtor_slot, _CO.replace('v0()', 'Impl_00()')), ['FNVNX_D22(HeapStandIn, virtual void Impl_00();)'],
      ['FNVNX_D22(HeapStandIn, virtual void Impl_00();) /* fnv-nx D22 */\n    virtual void v1();'])
# S5-boot (extradatalist.cpp AX): a qualified call whose receiver resolves to another class does not block it
_RQ = _CO + ('struct Sub\n{\n    virtual int* v0();\n    virtual void s1();\n};\n'
             'struct Ref\n{\n    virtual void r0();\n    Sub sub;\n};\n'
             'int* g(void* r) { return ((Ref*)r)->sub.v0(); }\n')
check('D26 call-only: `((T*)x)->m.ph()` on another class does not block it (Sub is held: its own call)',
      run(s1.r_vtable_dtor_slot, _RQ), ['FNVNX_D22(HeapStandIn, virtual void v0();)'], ['FNVNX_D22(Sub'])
check('D26 call-only: ... a cast to the class itself, or an unresolved receiver, does',
      run(s1.r_vtable_dtor_slot, _RQ.replace('((Ref*)r)->sub.v0()', '(int*)0; ((HeapStandIn*)r)->v0()')) +
      run(s1.r_vtable_dtor_slot, _RQ.replace('((Ref*)r)->sub.v0()', '((Sub*)r)->v0(), ((Ref*)r)->q.v0()')), [],
      ['FNVNX_D22(HeapStandIn'])
# ---- S1-rules: RAWVT1 (a raw exe slot index on `this` -> FNVNX_D26_SLOT(k): k + 1 under GCC, D26 minus-one)
_RV = ('struct Coll\n{\n    void Load(const char* f);\n    void Open(const char* f, bool w);\n};\n'
       'void Coll::Open(const char* f, bool w)\n{\n    Load(f);\n'
       '    ((void(__thiscall*)(void*, bool))(*reinterpret_cast<void***>(this))[6])(this, w);\n}\n')
check('RAWVT1 a raw slot index on this in a member -> FNVNX_D26_SLOT(k)', run(s1.r_raw_vtable_slots, _RV),
      ['(*reinterpret_cast<void***>(this))[FNVNX_D26_SLOT(6)])(this, w);'])
check('RAWVT1 not slot 0, not in a free function, not in a COM-shaped class',
      run(s1.r_raw_vtable_slots, _RV.replace('[6]', '[0]')) +
      run(s1.r_raw_vtable_slots, 'void f(void* p) { ((void(*)(void*))(*(void***)this)[3])(p); }\n') +
      run(s1.r_raw_vtable_slots, _RV.replace('    void Load(const char* f);', '    virtual long __stdcall Q(int);\n    virtual long __stdcall R(int);\n    void Load(const char* f);')),
      [], ['FNVNX_D26_SLOT'])
_eo = tempfile.mkdtemp(); open(os.path.join(_eo, 'exe_order_classes.tsv'), 'w').write('# class\tvftable\treason\nColl\t010a2528\tD22-USED\n')
os.environ['FNVNX_EXE_ORDER'] = os.path.join(_eo, 'exe_order_classes.tsv'); prepare.__dict__['_fnvnx_exe_order'] = None
check('RAWVT1 not in a class whose table the link keeps in exe order (exe_order_classes.tsv)',
      run(s1.r_raw_vtable_slots, _RV), [], ['FNVNX_D26_SLOT'])
del os.environ['FNVNX_EXE_ORDER']; prepare.__dict__['_fnvnx_exe_order'] = None
# S2-bodies (vtmetric 3f7a6c35): the TESForm family's exe deleting destructor is slot 4 (slots 0 / 1 trivial)
_sk = tempfile.mkdtemp(); os.makedirs(os.path.join(_sk, 'slots'))
_json.dump({'format': 'fnvnx-d22/1', 'classes': [], 'dtor_slots': [{'class': 'TESWorldSpace', 'slot': 4}]},
           open(os.path.join(_sk, 'slots', 'd22.json'), 'w'))
os.environ['FNVNX_RECON'] = _sk; prepare.__dict__['_fnvnx_d22'] = None; prepare.__dict__['_fnvnx_d22_slots'] = None
_WS = 'struct TESWorldSpace\n{\n' + ''.join('    virtual void v%d();\n' % i for i in range(8)) + '};\n' \
      'void f(TESWorldSpace* w) { w->v6(); }\n'
_wso = run(s1.r_vtable_dtor_slot, _WS)
check('D22-SLOTK: the exe dtor slot k (4), not slot 0, becomes FNVNX_D22', _wso,
      ['    virtual void v0();', 'FNVNX_D22(TESWorldSpace, virtual void v4();)'], ['FNVNX_D22(TESWorldSpace, virtual void v0();)'])
check('D22-SLOTK: held when the class has k virtuals or fewer, or calls slot k by name',
      run(s1.r_vtable_dtor_slot, 'struct TESWorldSpace\n{\n    virtual void v0();\n    virtual void v1();\n};\n') +
      run(s1.r_vtable_dtor_slot, _WS.replace('w->v6()', 'w->v4()')), [], ['FNVNX_D22'])
_RW = _RV.replace('Coll', 'TESWorldSpace')
check('RAWVT1 with a dtor slot K = 4: k < 4 stays, k > 4 shifts, k = 4 held',
      run(s1.r_raw_vtable_slots, _RW.replace('[6]', '[2]')) + '|' + run(s1.r_raw_vtable_slots, _RW) + '|' +
      run(s1.r_raw_vtable_slots, _RW.replace('[6]', '[4]')),
      ['(this))[2] /* fnv-nx RAWVT1-KEEP belowk */)', '(this))[FNVNX_D26_SLOT(6)])', '(this))[4] /* fnv-nx RAWVT1-KEEP slot0 */)'])
_json.dump({'format': 'fnvnx-d22/1', 'classes': [], 'dtor_slots': [{'class': 'Shell', 'slot': 4, 'file': 'src/ai/actor.cpp'}]},
           open(os.path.join(_sk, 'slots', 'd22.json'), 'w')); prepare.__dict__['_fnvnx_d22_slots'] = None
_SH = _WS.replace('TESWorldSpace', 'Shell')
check('D22-SLOTK: a per-file dtor_slots row applies in that file only',
      run(s1.r_vtable_dtor_slot, _SH, '/t/src/ai/actor.cpp') + '|' + run(s1.r_vtable_dtor_slot, _SH, '/t/src/ai/other.cpp'),
      ['FNVNX_D22(Shell, virtual void v4();) /* fnv-nx D22 */', '|struct Shell\n{\n    FNVNX_D22(Shell, virtual void v0();)'])
del os.environ['FNVNX_RECON']; prepare.__dict__['_fnvnx_d22'] = None; prepare.__dict__['_fnvnx_d22_slots'] = None
# ---- S1-rules: EMPTY1 (R2 trap rank 32: treeengine's declared-only TagEmpty is the temporary TagText(""))
_EM = ('struct TagText\n{\n    int w;\n    TagText(const char*);\n};\nextern const TagText TagEmpty;\n'
       'TagText g(bool s) { return s ? TagText("x") : TagEmpty; }\n')
check('EMPTY1 a declared-only empty-string object -> FNVNX_EMPTY1(T, name)', run(s1.r_empty_string_objects, _EM),
      [': FNVNX_EMPTY1(TagText, TagEmpty); }', 'extern const TagText TagEmpty;'])
check('EMPTY1 not when the file defines it, or T has no const char* constructor',
      run(s1.r_empty_string_objects, _EM + 'const TagText TagEmpty("");\n') +
      run(s1.r_empty_string_objects, _EM.replace('TagText(const char*);', 'TagText(int);')), [], ['FNVNX_EMPTY1'])
_eo2 = tempfile.mkdtemp(); open(os.path.join(_eo2, 'exe_order_classes.tsv'), 'w').write(
    'class\tvftable\treason\tsymbol\nHeapStandIn\t0101cd04\tdefined-slot0\t_ZTV1X\nOther\t01\tmulti-vtable\t_ZTV1Y\n')
os.environ['FNVNX_EXE_ORDER'] = os.path.join(_eo2, 'exe_order_classes.tsv'); prepare.__dict__['_fnvnx_exe_order'] = None
check('D22-EXEORDER: a class the link keeps in exe order (defined-slot0) keeps its callers\' numbering',
      run(s1.r_vtable_dtor_slot, _CO), [], ['FNVNX_D22'])
check('D22-EXEORDER: ... another reason (multi-vtable) does not hold a class',
      run(s1.r_vtable_dtor_slot, _CO.replace('HeapStandIn', 'Other')), ['FNVNX_D22(Other'])
del os.environ['FNVNX_EXE_ORDER']; prepare.__dict__['_fnvnx_exe_order'] = None
check('D26 call-only: numbered sequences _v01/_v02, d000/d001, v000/v004/v008 are placeholders; v000/v002 is not (EQ G1)',
      run(s1.r_vtable_dtor_slot, _CO.replace('v0()', '_v01()').replace('v1()', '_v02()')) +
      run(s1.r_vtable_dtor_slot, _CO.replace('v0()', 'd000()').replace('v1()', 'd001()')) +
      run(s1.r_vtable_dtor_slot, _CO.replace('v0()', 'w000()').replace('v1()', 'w004()').replace('void* Allocate', 'void* w008')),
      ['FNVNX_D22(HeapStandIn, virtual void _v01();)', 'FNVNX_D22(HeapStandIn, virtual void d000();)',
       'FNVNX_D22(HeapStandIn, virtual void w000();)'])
check('D26 call-only: ... a step-2 sequence is not',
      run(s1.r_vtable_dtor_slot, _CO.replace('v0()', 'w000()').replace('v1()', 'w002()')), [], ['FNVNX_D22'])
# ---- S1-rules: D22-CL (the exe decides: S2's classlayout.tsv, orchestrator 2026-11-08)
_cl = tempfile.mkdtemp(); open(os.path.join(_cl, 'classlayout.tsv'), 'w').write(
    '# fnvnx-classlayout/1\nclass\tvftable\tkind\tdtor_slot\treason\tfile\tqualified\n'
    'Named\t01000000\tminus-one\t0\trtti\t\t\nHeapStandIn\t01000004\texe-order\t-1\tdefined-slot0\t\t\n'
    'TESWorldSpace\t0103195c\tminus-one\t4\tvflag\t\t\nComX\t01000008\tcom\t-1\trtti-com\t\t\n'
    'Named\t0100000c\texe-order\t-1\tsecondary off 8\t\t\t\t\n')
os.environ['FNVNX_CLASSLAYOUT'] = os.path.join(_cl, 'classlayout.tsv'); prepare.__dict__['_fnvnx_classlayout'] = None
check('D22-CL: minus-one, slot 0: a named first virtual converts (no placeholder name needed)',
      run(s1.r_vtable_dtor_slot, _CO.replace('HeapStandIn', 'Named').replace('v0()', 'Reset()')),
      ['FNVNX_D22(Named, virtual void Reset();)'])
check('D22-CL: exe-order and com keep the declaration, even a placeholder one',
      run(s1.r_vtable_dtor_slot, _CO) + run(s1.r_vtable_dtor_slot, _CO.replace('HeapStandIn', 'ComX')), [], ['FNVNX_D22'])
check('D22-CL: minus-one, slot 4: the fifth virtual', run(s1.r_vtable_dtor_slot, _WS),
      ['FNVNX_D22(TESWorldSpace, virtual void v4();)'], ['virtual void v0();) /* fnv-nx D22'])
check('D22-CL: RAWVT1 leaves an exe-order class in exe order', run(s1.r_raw_vtable_slots, _RV.replace('Coll', 'HeapStandIn')),
      [], ['FNVNX_D26_SLOT'])
del os.environ['FNVNX_CLASSLAYOUT']; prepare.__dict__['_fnvnx_classlayout'] = None
# ---- S1-rules: HKRO1 (Havok hkReferencedObject's two base virtuals after its destructor)
_HK = 'class hkReferencedObject\n{\npublic:\nvirtual ~hkReferencedObject(){}\nvoid removeReference()const;\n};\n'
check('HKRO1 a destructor-only hkReferencedObject gets FNVNX_HKRO1 after its destructor',
      run(s1.r_hk_referenced_object_slots, _HK), ['virtual ~hkReferencedObject(){} FNVNX_HKRO1 /* fnv-nx HKRO1 */'])
check('HKRO1 not a full one, nor another class',
      run(s1.r_hk_referenced_object_slots, _HK.replace('void removeReference', 'virtual const void* GetClassType() const;\nvoid removeReference')) +
      run(s1.r_hk_referenced_object_slots, _HK.replace('hkReferencedObject', 'hkOther')), [], ['FNVNX_HKRO1'])
# ---- S1-rules: TAIL1-PACK (the base filler under #pragma pack(N): MSVC's min(alignof(base), N); LAYOUT gate pack rows)
_PK = ('struct B { virtual void f(); };\n#pragma pack(push, 1)\nstruct P : B { short a; };\n#pragma pack(pop)\n'
       'struct Q : B { short a; };\n#pragma pack(push, 4)\n#pragma pack(push, 2)\nstruct R : B { int a; };\n#pragma pack(pop)\n'
       'struct S : B { int a; };\n#pragma pack(pop)\n')
_pko = run(s1.r_msvc_base_layout, _PK)
check('TAIL1-PACK under pack(1) / nested push 2: FNVNX_MSVC_BASE_END_PACK(N, base); after the pops the plain filler',
      _pko, ['struct P : B { FNVNX_MSVC_BASE_END_PACK(1, B)', 'struct Q : B { FNVNX_MSVC_BASE_END(B)',
             'struct R : B { FNVNX_MSVC_BASE_END_PACK(2, B)', 'struct S : B { FNVNX_MSVC_BASE_END_PACK(4, B)'])
# S5-boot (actor.cpp NiTMap_S): an unqualified `v0(p)` in a member of an unrelated class is that class's own call
_UQ = _CO + ('struct CVB\n{\n    virtual int v0(int p);\n    virtual int g(int p);\n};\n'
             'struct CVS : CVB\n{\n    int h(int p);\n};\nint CVS::h(int p) { return v0(p); }\n')
check('D26 call-only: an unqualified call in an unrelated class member does not block it',
      run(s1.r_vtable_dtor_slot, _UQ), ['FNVNX_D22(HeapStandIn, virtual void v0();)'])
check('D26 call-only: ... an unqualified call in a member of a derived class does',
      run(s1.r_vtable_dtor_slot, _UQ.replace('struct CVS : CVB', 'struct CVS : HeapStandIn')), [],
      ['FNVNX_D22(HeapStandIn'])
# S5-boot (modelloader_activator_declarations.h BGSOpenCloseForm): a body-less non-virtual `~C();` gives way
_DT = _CO.replace('    virtual void* Allocate(unsigned n);\n', '    virtual void* Allocate(unsigned n);\n    HeapStandIn();\n    ~HeapStandIn();\n')
check('D26 call-only: a body-less `~C();` is dropped under GCC (D22-DTOR)', run(s1.r_vtable_dtor_slot, _DT),
      ['FNVNX_D22(HeapStandIn, virtual void v0();)', 'FNVNX_D22_DROP(~HeapStandIn();)'])
check('D26 call-only: ... not when the file defines it, nor a virtual or inline one',
      run(s1.r_vtable_dtor_slot, _DT + 'HeapStandIn::~HeapStandIn() {}\n') +
      run(s1.r_vtable_dtor_slot, _DT.replace('    ~HeapStandIn();', '    virtual ~HeapStandIn();')) +
      run(s1.r_vtable_dtor_slot, _DT.replace('    ~HeapStandIn();', '    ~HeapStandIn() {}')), [], ['FNVNX_D22(HeapStandIn'])
if _shutil.which('aarch64-linux-gnu-g++'):
    _mD = [l for l in open(os.path.join(HERE, '..', '..', 'shim', 'include', 'fnvnx_compat.h')) if l.startswith('#define FNVNX_D22')]
    _r = _subprocess.run(['aarch64-linux-gnu-g++', '-mabi=ilp32', '-std=gnu++17', '-O2', '-S', '-w', '-o', '-', '-x', 'c++',
                          '-'], input=''.join(_mD) + _coo, capture_output=True, text=True)
    # D1/D0 at slots 0/1 (4 bytes each, ILP32), v1 at 2, Allocate at exe slot 2 -> native slot 3: offset 12
    check('D26 call-only output: Allocate (exe slot 2) is called at native slot 3, +12 (ILP32)', _r.stderr or _r.stdout,
          ['[x2, 12]'], ['error'])
if _save_d22 is not None: os.environ['FNVNX_D22'] = _save_d22
if _save_rc is not None: os.environ['FNVNX_RECON'] = _save_rc
prepare.__dict__['_fnvnx_d22'] = None

# ---- determinism: every rule, run under two PYTHONHASHSEED values, gives the same text (set / dict order must not
# reach the output: tree hashes and incremental compiles depend on it)
_DET = r'''
import hashlib, os, sys
from collections import Counter
sys.path.insert(0, sys.argv[1])
import prepare
rules = prepare.RULES + prepare.load_stream_rules()
src = open(sys.argv[2]).read()
h = hashlib.sha256()
for rule in rules:
    st = Counter()
    out = rule('/nonexistent/src/staging/blk_00aa0000_o2.cpp', src, st)
    h.update(rule.__name__.encode() + b'\0' + out.encode('utf-8', 'surrogateescape') + repr(sorted(st.items())).encode())
print(h.hexdigest())
'''
_dd = tempfile.mkdtemp()
_snip = os.path.join(_dd, 'snippet.cpp')
open(_snip, 'w').write(
    'namespace N {\nclass A { friend class W; friend class V; W* w; V* v; };\nclass W {}; class V {};\n}\n'
    'typedef union ::__m128 Q;\nextern "C" ::__m128(_mm_load_ss)(const float* p);\nunion __declspec(intrin_type) __m128 '
    '{ struct { float x, y, z, w; } lanes; };\nfloat f(__m128 v) { return v.lanes.x + v.m128_f32[1]; }\n'
    'struct S { int* vtbl; S() { vtbl = 0; } virtual ~S(); };\nint g(int a) { if (a) return 1; }\n'
    'enum E { E_A = 0x80000000 };\nvoid h(va_list ap) { int i = va_arg(ap, int); }\n')
_hashes = set()
for _seed in ('1', '2', '3'):
    _r = _subprocess.run([sys.executable, '-I', '-c', _DET, TOOLS, _snip], capture_output=True, text=True,
                         env=dict(os.environ, PYTHONHASHSEED=_seed))
    _hashes.add(_r.stdout.strip() or 'error: ' + _r.stderr[-300:])
check('determinism: all rules give the same output under PYTHONHASHSEED 1, 2, 3', ' | '.join(sorted(_hashes)),
      [], ['error', ' | '])
_shutil.rmtree(_dd, ignore_errors=True)

# ---- PHSLOT1
_ph = ('struct Shell\n{\n    virtual void v000()\n    {}\n    virtual void v00c()\n    {}\n'
       '    virtual void v058(Shell*);\n    virtual void Named()\n    {}\n    virtual int v003()\n    {}\n'
       '    virtual void v004()\n    { g(); }\n};\n')
_ph_out = run(s1.r_placeholder_slots, _ph)
check('PHSLOT1 empty placeholder bodies become declarations',
      _ph_out, ['virtual void v000();', 'virtual void v00c();', 'virtual void v058(Shell*);'],
      ['virtual void v000()\n', 'virtual void v00c()\n'])
check('PHSLOT1 a named virtual, a non-void placeholder and a placeholder with a body stay',
      _ph_out, ['virtual void Named()\n    {}', 'virtual int v003()\n    {}', 'virtual void v004()\n    { g(); }'])
_st = Counter()
s1.r_placeholder_slots('/nonexistent/src/x.cpp', _ph, _st)
check('PHSLOT1 stats', str(_st['PHSLOT1']), ['2'])
check('PHSLOT1 idempotent', run(s1.r_placeholder_slots, _ph_out), [_ph_out])

# ---- VFTBL1: a table the decomp defines for a name bound to an exe vftable becomes a declaration
_VP = os.path.normpath(os.path.abspath('/nonexistent/src/x.cpp'))
_saved_v = s1._VFT_NAMES
_vt = ('struct VTable { void* entries[4]; };\nvoid __cdecl AgentRelease(void*);\nnamespace fnv_00d2eae0 {\n'
       'static const VTable g_vtblAgent = {\n    {(void*)&AgentRelease, (void*)&AgentRelease, 0, 0}};\n'
       'static const VTable g_vtblBase = {{(void*)&AgentRelease}};\nstatic const VTable g_vtblPlain = {{0}};\n'
       'void f(void** o) { *o = (void*)&g_vtblAgent; *o = (void*)&g_vtblBase; *o = (void*)g_vtblPlain.entries[1]; }\n}\n'
       'static const VTable g_vtblGlobal = {{(void*)&AgentRelease}};\nvoid g(void** o) { *o = (void*)&g_vtblGlobal; }\n')
s1._VFT_NAMES = {_VP: {'g_vtblAgent': True, 'g_vtblBase': False, 'g_vtblPlain': True, 'g_vtblGlobal': False}}
_vo = run(s1.r_vftable_standins, _vt)
check('VFTBL1 a namespace table bound by its C name becomes an extern "C" declaration', _vo,
      ['extern "C" { extern const VTable g_vtblAgent; }'], ['g_vtblAgent = {'])
check('VFTBL1 a namespace table not bound by its plain name stays', _vo, ['static const VTable g_vtblBase = {{('])
check('VFTBL1 a table read through entries[k] stays (x86 slot k is native slot k+1)', _vo,
      ['static const VTable g_vtblPlain = {{0}};'])
check('VFTBL1 a global table becomes a plain declaration', _vo, ['extern const VTable g_vtblGlobal;'],
      ['g_vtblGlobal = {'])
check('VFTBL1 idempotent', run(s1.r_vftable_standins, _vo), [_vo])
_st = Counter()
s1.r_vftable_standins('/nonexistent/src/x.cpp', _vt, _st)
check('VFTBL1 stats', str(_st['VFTBL1']), ['2'])
s1._VFT_NAMES = {}
check('VFTBL1 a name no unit binds to an exe vftable stays', run(s1.r_vftable_standins, _vt), [_vt])
s1._VFT_NAMES = _saved_v
check('VFTBL1 the committed list has the exe vftables 0x010ce838 and 0x0102e388',
      str({0x010ce838, 0x0102e388} <= s1._exe_vftable_addrs()), ['True'])

# ---- REC1 x STAND4 (W6 widened STAND4 to lone static noinline stand-ins; STAND4 runs before REC1, so a recon whose
# `replace` spans such a stand-in saw only the extern declaration and was rejected, 7 recons): the recon applies.
# Our own snippet; the units.json is the STAND4 case's above.
_r4 = tempfile.mkdtemp()
os.makedirs(os.path.join(_r4, 'recon', 'x'))
_json.dump({'format': 'fnvnx-recon/1', 'unit': 'src/u.cpp',
            'replace': [{'old': 'static __declspec(noinline) float Zero()\n{\n    return 0.0f;\n}\nint Use() { return Zero(); }\n',
                         'new': 'static __declspec(noinline) float Zero()\n{\n    return 1.0f;\n}\nint Use() { return Zero() + 1; }\n'},
                        {'old': 'int Last() { return 9; }', 'new': 'int Last() { return 10; }'}]},
           open(os.path.join(_r4, 'recon', 'x', 'recon.json'), 'w'))
_u4 = os.path.join(_s4, 'src', 'u.cpp')
_src4 = ('// @fnv 0x00401230\nint Real() { return 1; }\nstatic __declspec(noinline) float Zero()\n{\n    return 0.0f;\n}\nint Use() { return Zero(); }\n'
         'int Last() { return 9; }\n')
_saved = (s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS)
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
_saved_env = os.environ.get('FNVNX_RECON')
os.environ['FNVNX_RECON'] = os.path.join(_r4, 'recon')
prepare.__dict__['_fnvnx_recon'] = None
prepare.__dict__['_fnvnx_recon_rejected'] = []
prepare.__dict__['_fnvnx_stand4_spans'] = {}
_st = Counter()
_t4 = s1.r_static_stand_ins(_u4, _src4, _st)          # as the rule order runs it: STAND4, then (later) REC1
_n4 = _st['STAND4']
_t4 = s1.r_recon_bodies(_u4, _t4, _st)
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = _saved
if _saved_env is None:
    os.environ.pop('FNVNX_RECON', None)
else:
    os.environ['FNVNX_RECON'] = _saved_env
check('REC1 x STAND4: the stand-in is rewritten before REC1 runs', str(_n4), ['1'])
check('REC1 x STAND4: a recon whose replace spans the stand-in is applied, not rejected',
      _t4 + str(dict(_st)) + repr(prepare.__dict__['_fnvnx_recon_rejected']),
      ['return Zero() + 1;', 'int Last() { return 10; }', "'REC1-REPL': 2", '[]'],
      ["'REC1-REJECT'", 'return Zero(); }'])
check('REC1 x STAND4: STAND4 converts what the replace left (no static body, one declaration)',
      _t4, ['__declspec(noinline) float Zero(); /* fnv-nx STAND4 0x00d571d0 */'],
      ['return 1.0f', 'return 0.0f', 'static __declspec'])

# ---- S1-rules: VFPAD1 (MSVC pads a class's own vfptr to the class's alignment; LAYOUT gate vfpad rows)
_VF = ('struct V16 { virtual ~V16(); unsigned int m10; hkVector4 m20; };\n'
       'struct VD { FNVNX_D22(VD, virtual void v0() ;) /* fnv-nx D22 */\n    double m08; int* p; };\n'
       'struct VS { virtual void f(); int a; char b[4]; };\n'
       'struct VB : B { virtual void f(); hkVector4 m; };\n'
       'struct VN { struct In { __m128 q; }; virtual void f(); In m; };\n'
       'struct hkpSphereMotionD1E4 { virtual void f(); short m_s; hkVector4 unk10; };\n'
       'class NF\n{\n  public:\n    typedef unsigned(__cdecl* RFN)(NF* p);\n    virtual ~NF();\n    RFN m_r; double d;\n};\n'
       '#pragma pack(push, 4)\nstruct VP4 { virtual void f(); double d; };\n#pragma pack(pop)\n'
       '#pragma pack(push, 8)\nstruct VP8 { virtual void f(); hkVector4 v; };\n#pragma pack(pop)\n')
_vfo = run(s1.r_vfptr_pad, _VF)
check('VFPAD1 puts FNVNX_VFPAD(member types) first in a no-base class with a vfptr (not D22\'s macro text, not pointers)',
      _vfo, ['struct V16 { FNVNX_VFPAD(hkVector4) virtual', 'struct VD { FNVNX_VFPAD(double) FNVNX_D22',
             'struct VP8 { FNVNX_VFPAD_PACK(8, hkVector4) virtual', 'class NF\n{ FNVNX_VFPAD(double)\n'],
      ['struct VS { FNVNX', 'struct VB : B { FNVNX', 'struct VN { FNVNX', 'struct hkpSphereMotionD1E4 { FNVNX',
       'struct VP4 { FNVNX', 'int*)'])

# ---- S5-boot: RAWVT1-MEM (the vptr as a data member; BSDiscBootSystemUtility::Initialize recursed, link af44165ab2d8)
_RM = ('class BSDisc\n{\n  public:\n    void** m_ppVirtualMethodTable;\n    int n;\n    void Init();\n    void Count();\n};\n'
       'typedef void(__thiscall* DestroyFn)(BSDisc*);\n'
       'void BSDisc::Init()\n{\n    ((DestroyFn)m_ppVirtualMethodTable[2])(this);\n}\n'
       'void BSDisc::Count()\n{\n    int c = (int)m_ppVirtualMethodTable[7];\n    n = c;\n}\n'
       'struct Ren { void f(); int pad; };\nvoid Ren::f()\n{\n    void** vtbl = *(void***)this;\n'
       '    ((void (__thiscall*)(void*, int))vtbl[0x1a])(this, 1);\n}\n'
       'struct Vv { virtual void g(); void** vtable; void h(); };\nvoid Vv::h()\n{\n    ((DestroyFn)vtable[3])(0);\n}\n')
_rmo = run(s1.r_raw_vtable_slots, _RM)
check('RAWVT1-MEM: a called slot of the vptr member / a local *(void***)this gets FNVNX_D26_SLOT; a data read and a '
      'member of a class with virtuals do not',
      _rmo, ['m_ppVirtualMethodTable[FNVNX_D26_SLOT(2)]', 'vtbl[FNVNX_D26_SLOT(0x1a)]'],
      ['m_ppVirtualMethodTable[FNVNX_D26_SLOT(7)]', 'vtable[FNVNX_D26_SLOT(3)]'])

# ---- S5-boot: D22 call-only with empty out-of-class definitions (FUN_00aa1420's S_00aa1420_Object, NiAlloc corruption)
_OC = ('struct S_00aa1420_Object\n{\n    virtual void S_00aa1420_v0();\n    virtual void S_00aa1420_v1(int* a, int b);\n};\n\n'
       'inline void S_00aa1420_Object::S_00aa1420_v0()\n{}\n\ninline void S_00aa1420_Object::S_00aa1420_v1(int* a, int b)\n{}\n\n'
       'struct H { S_00aa1420_Object* m_00; };\nvoid f(H* h) { int v; h->m_00->S_00aa1420_v1(&v, 1); }\n')
check('D22 call-only: empty out-of-class definitions are placeholders; slot 0\'s definition goes under FNVNX_D22_DROP',
      run(s1.r_vtable_dtor_slot, _OC),
      ['FNVNX_D22(S_00aa1420_Object, virtual void S_00aa1420_v0();)',
       'FNVNX_D22_DROP(inline void S_00aa1420_Object::S_00aa1420_v0()\n{})',
       'inline void S_00aa1420_Object::S_00aa1420_v1(int* a, int b)\n{}'])

# ---- S5-boot: RAWVT1-RECV (another receiver whose class has a minus-one classlayout row)
import tempfile as _tf
_clf = _tf.NamedTemporaryFile('w', suffix='.tsv', delete=False)
_clf.write('# fnvnx-classlayout/1\nclass\tvftable\tkind\tdtor_slot\treason\tfile\tqualified\tevidence\tslots\n'
           'BSExtraData\t0x01\tminus-one\t0\t-\t\tBSExtraData\t-\t-\nIDevX\t0x02\tcom\t-1\t-\t\tIDevX\t-\t-\n'); _clf.close()
_save_cl = os.environ.get('FNVNX_CLASSLAYOUT'); os.environ['FNVNX_CLASSLAYOUT'] = _clf.name
prepare.__dict__.pop('_fnvnx_classlayout', None)
_RR = ('typedef bool(__thiscall* CmpFn)(BSExtraData*, BSExtraData*);\n'
       'bool f(BSExtraData* apExtra, BSExtraData* w, void* raw)\n{\n'
       '    if (((CmpFn)apExtra->m_ppVirtualMethodTable[1])(apExtra, w)) return 1;\n'
       '    return ((CmpFn)((void**)raw)[1])(0, 0);\n}\n'
       'void g(Shell* s)\n{\n    ((CmpFn)s->vtable[2])(0, 0);\n}\n')
check('RAWVT1-RECV: a receiver with a minus-one classlayout row gets FNVNX_D26_SLOT; one without a row does not',
      run(s1.r_raw_vtable_slots, _RR), ['apExtra->m_ppVirtualMethodTable[FNVNX_D26_SLOT(1)]'], ['s->vtable[FNVNX'])
if _save_cl is None: os.environ.pop('FNVNX_CLASSLAYOUT', None)
else: os.environ['FNVNX_CLASSLAYOUT'] = _save_cl
prepare.__dict__.pop('_fnvnx_classlayout', None)

# ---- S5-boot: RAWVT1-DYN (an undecided receiver: the slot is chosen at run time, fnvnx_rawvt.h)
_DY = ('typedef int(__thiscall* F)(void*);\nint f(void* raw, IDirect3DDevice9* dev)\n{\n'
       '    int a = ((F)(*(void***)raw)[7])(raw);\n    return a + ((F)(*(void***)dev)[3])(dev);\n}\n')
_dyo = run(s1.r_raw_vtable_slots, _DY)
check('RAWVT1-DYN: an undecided receiver -> FNVNX_RAWVT_DYN(vptr, k) and the header; a COM name stays',
      _dyo, ['FNVNX_RAWVT_DYN((*(void***)raw), 7)', '#include "fnvnx_rawvt.h" /* fnv-nx RAWVT1-DYN */'],
      ['FNVNX_RAWVT_DYN((*(void***)dev)'])

# ---- S5-boot: VTM1 (a struct modelling an exe vftable gets one pointer after the destructor slot under GCC)
_VM = ('namespace {\nstruct VTable\n{\n    void* f0;\n    void* f1;\n    void(__thiscall* f2)(void*);\n};\n'
       'struct Obj\n{\n    VTable* vt;\n};\n}\n'
       'struct ComVtbl { long (__stdcall* QueryInterface)(void*); void* f1; };\n'
       'struct ShaderEntry { const char* f00; const char* f01; };\n'
       'struct InitVt { void* f0; void* f1; };\nInitVt g_t = {0, 0};\n')
_vmo = run(s1.r_vtable_model_pad, _VM)
check('VTM1: the vftable model gets FNVNX_VTM1_PAD after f0 and the header; COM, a data struct, an initialised table do not',
      _vmo, ['void* f0; FNVNX_VTM1_PAD /* fnv-nx VTM1 */', '#include "fnvnx_vtm.h" /* fnv-nx VTM1 */'],
      ['QueryInterface)(void*); FNVNX', 'f00; FNVNX', 'struct InitVt { void* f0; FNVNX'])

# ---- S1-rules: RAWVT1-SSA (rawvcall's raw sites from the SSA dump, by line text, rewritten by meaning)
_SS = ('struct VT { void* p0[3]; void (__thiscall* f3)(void*, int); };\nstruct R { VT* vt; };\n'
       'void a(void* p) {\n    ((void(__thiscall*)(void*, int))(*((void***)p))[7])(p, 1);\n}\n'
       'void b(void* p) {\n    void** vt = (void**)*p;\n    reinterpret_cast<V0>(vt[0])(p, 0);\n}\n'
       'void c(void* cv, void* buf) {\n    ((void(__thiscall*)(void*, void*))(*((int**)cv))[0])(cv, buf);\n}\n'
       'void d(R* r) {\n    r->vt->f3(r, 2);\n}\n'
       'void e(char* t) {\n    ((void(__thiscall*)(void*, float))(*(void**)(*(int*)((char*)t + 0xe0) + 12)))((char*)t + 0xe0, 1.f);\n}\n'
       'void f(void* o) {\n    ((void(__stdcall*)(int*, int))vt[0x38 / 4])(o, 0);\n}\n')
_sst = os.path.join(tempfile.mkdtemp(), 'rawvt_ssa.tsv')
with open(_sst, 'w') as _fh:
    _fh.write('# fnvnx-rawvt-ssa/1\n')
    for _l, _o in (('((void(__thiscall*)(void*, int))(*((void***)p))[7])(p, 1);', 28), ('reinterpret_cast<V0>(vt[0])(p, 0);', 0),
                   ('((void(__thiscall*)(void*, void*))(*((int**)cv))[0])(cv, buf);', 0), ('r->vt->f3(r, 2);', 'field'),
                   ('((void(__thiscall*)(void*, float))(*(void**)(*(int*)((char*)t + 0xe0) + 12)))((char*)t + 0xe0, 1.f);', 12),
                   ('((void(__stdcall*)(int*, int))vt[0x38 / 4])(o, 0);', 56)):
        _fh.write('src/x.cpp\t%s\t%s\n' % (_l, _o))
os.environ['FNVNX_RAWVT_SSA'] = _sst
prepare.__dict__.pop('_fnvnx_rawvt_ssa', None)
check('RAWVT1-SSA: index -> DYNK on the first argument, slot 0 with an int flag -> DEL0, other slot 0 kept, a model field '
      '-> FNVNX_RAWVT_FIELD, a byte offset -> DYNOFF, a stdcall kept (COM)', run(s1.r_rawvt_ssa, _SS),
      ['[FNVNX_RAWVT_DYNK(*(void **)(p), 7)])(p, 1);', '    FNVNX_RAWVT_DEL0(p, 0);',
       '(cv, buf); /* fnv-nx RAWVT1-KEEP slot0-not-dtor */', 'FNVNX_RAWVT_FIELD(r->vt, f3)(r, 2);',
       '+ FNVNX_RAWVT_DYNOFF(*(void **)((char*)t + 0xe0), 12)', '(o, 0); /* fnv-nx RAWVT1-KEEP com-stdcall */',
       '#include "fnvnx_rawvt.h"'], ['0xe0, 12)', 'DYNOFF(*(void **)((char*)t + 0xe0), 0xe0)'])
os.environ.pop('FNVNX_RAWVT_SSA'); prepare.__dict__.pop('_fnvnx_rawvt_ssa', None)

print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
