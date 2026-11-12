#!/usr/bin/env python3
"""test_rules_text.py - regression cases of the compile rules (core.py, s1_compile.py): each case runs one rule on a small
synthetic snippet written here and checks the output. Rules that read build/units.json run against a synthetic checkout in a
temporary directory.

    python3 -I rules/tests/test_rules_text.py
"""
import os
import re
import shutil as _shutil
import sys
import tempfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
from support import check  # noqa: E402
import core as prepare  # noqa: E402
import s1_compile as s1  # noqa: E402

core = prepare
core.CTX['win32_header'] = support.WIN32_HEADER
core.CTX['crt_symbols'] = '00401040\tcrt_inline\tfloor(float) -> floorf\n'
support.legacy_units_lookup()


def run(rule, text, path='/nonexistent/src/x.cpp'):
    return rule(path, text, Counter())


TRAP = '__builtin_trap()'


# ---- ALLOC1
check('ALLOC1 untagged file-scope operator new[] dropped',
      run(s1.r_global_allocators, 'inline void* operator new[](unsigned int n)\n{\n    return A(n);\n}\n'),
      ['ALLOC1'], ['return A(n)'])
check('ALLOC1 @fnv-tagged operator new kept',
      run(s1.r_global_allocators, '// @fnv 0x00401080\nvoid* operator new(unsigned int n)\n{\n    return A(n);\n}\n'),
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

# ---- TPL2
got = run(s1.r_namespace_template_decls, 'namespace N {\ntemplate <class T> class A\n{\n  public:\n    void d(T* p)\n'
          '    {\n        D(p);\n    }\n};\ntemplate <class T> inline void D(T* p)\n{\n    p->~T();\n}\n}\n')
check('TPL2 forward-declares a namespace function template used earlier', got,
      ['template <class T> inline void D(T* p); /* fnv-nx TPL2 */'])
got = run(s1.r_namespace_template_decls, 'namespace N {\ntemplate <class T> class A\n{\n  public:\n    void d(T* p)\n'
          '    {\n        D(p);\n    }\n};\ntemplate <class T> inline void D(T* p)\n{\n    p->~T();\n}\n'
          'template <> inline void D(char*)\n{\n}\n}\n')
check('TPL2 never forward-declares an explicit specialization (template <>) ahead of its primary', got,
      ['template <class T> inline void D(T* p); /* fnv-nx TPL2 */'], ['template <> inline void D(char*);'])

# ---- S1-STL (blk_00401340): TPL2 specializations, TPL3, TYPN1, INST1, COPY1, DECL3
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
      run(s1.r_msvc_tokens, 'struct S { unsigned int (__cdecl* m_01)(void*); };\nD<unsigned char(unsigned int)> d;\n'
          'x = reinterpret_cast<unsigned int (T::*)()>(p);\nvoid f(int a, unsigned long (b));\n'),
      ['unsigned int (__cdecl* m_01)(void*);', 'D<unsigned char(unsigned int)> d;',
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
check('STATIC1 an unsized array at file scope becomes a declaration, whatever its name; a sized one stays',
      run(s1.r_image_statics, 'const void* DAT_010d0fc4[];\nconst void* local_table[];\nconst void* sized[4];\n'),
      ['extern const void* DAT_010d0fc4[];', 'extern const void* local_table[];', '\nconst void* sized[4];'])

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
      run(s1.r_keep_definitions, 'bool f()\n{\n// @fnv 0x00401280\n    if (x)\n        g();\n    return true;\n}\n'),
      (), ['FNVNX_KEEP'])

check('KEEP1 tag above an access label: FNVNX_KEEP after the label',
      run(s1.r_keep_definitions, 'class C {\n// @fnv 0x00401400\npublic:\n    C() {}\n};\n'), ['public:\n    FNVNX_KEEP C() {}'])

check('KEEP1 tag in front of a member initializer list: no FNVNX_KEEP',
      run(s1.r_keep_definitions, 'A::A(int n)\n// @fnv 0x004013c0\n    : Base(n)\n{}\n'), (), ['FNVNX_KEEP'])

# ---- NEW1 keeps reference parameters
check('NEW1 size parameter -> size_t, reference parameter kept',
      prepare.r_newdel_size('x.cpp', 'static void* operator new[](unsigned int size, unsigned int& total);\n', Counter()),
      ['operator new[](size_t size, unsigned int& total)'])

# ---- TPL1
import tempfile
_d = tempfile.mkdtemp()
os.makedirs(os.path.join(_d, 'src'))
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

# ---- SAL1 class attributes
check('SAL1 drops the attributes of the annotation classes',
      run(s1.r_sal_attributes, 'enum A { N };\n[repeatable][source_annotation_attribute(Parameter)] struct PreAttribute\n{\n};\n'
          '[repeatable]\n[source_annotation_attribute(Parameter)]\nstruct PostAttribute;\n'),
      ['enum A { N };\nstruct PreAttribute\n', '};\n\n\nstruct PostAttribute;'], ['[repeatable]', 'source_annotation'])

# ---- LINK1 declarator lists
check('LINK1 C-linkage declarator list over two lines',
      run(s1.r_c_data_linkage, 'extern "C" char str_a[], str_b[],\n    str_c[];\nextern "C" int f(int);\n'),
      ['extern char str_a[], str_b[],\n    str_c[];', 'extern "C" int f(int);'])

check('LINK1 a C-linkage function-pointer variable at file scope, any name; a function returning one stays',
      run(s1.r_c_data_linkage, 'extern "C" void (__cdecl *pfn)(int);\nextern "C" void (*get(int))(int);\n'),
      ['extern void (__cdecl *pfn)(int);', 'extern "C" void (*get(int))(int);'])

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
_json.dump({'units': [{'source': 'src/u.cpp', 'symbols': {'_floor': '0x00401040'}},
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

# ---- DECL1 with a trailing comment
check('DECL1 drops a CRT prototype that ends in a // comment',
      prepare.r_platform_protos('/x/src/a.h', 'extern "C" char* __cdecl strchr(const char*, int);  // CRT strchr (x)\n',
                                Counter()), ['#include <string.h> /* fnv-nx DECL1 */'], ['__cdecl strchr'])

check('SSE1 elaborated __m128 references',
      prepare.r_sse_local_types('x.cpp', 'typedef union __m128 unit02;\nvoid f(struct __m128* p);\n', Counter()),
      ['typedef __m128 unit02;', 'void f(__m128* p);'])
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
check('SSE1 qualified elaborated __m128 references (`union ::__m128`)',
      prepare.r_sse_local_types('x.cpp', 'typedef union ::__m128 unit02;\nunion :: __m128;\nvoid f(struct N::__m128i* p);\n'
                                'union __m128_like { int a; };\n', Counter()),
      ['typedef __m128 unit02;\n\nvoid f(__m128i* p);', 'union __m128_like { int a; };'], ['::'])

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

got = run(s1.r_goto_scalar_inits, 'typedef float Real;\ntypedef __m128 Quad;\nstruct Hk;\nint f(int e)\n{\n    if (e) goto out;\n    {\n'
          '        const Real r = 4;\n        Quad q = g();\n        Hk h = 3;\n        return (int)r;\n    }\nout:\n    return 0;\n}\n')
check('GOTO1 takes the scalar types from the unit\'s own typedefs, not from names (Hk is no typedef)', got,
      ['Real r; r = 4;', 'Quad q; q = g();', 'Hk h = 3;'])
got = run(s1.r_goto_scalar_inits, 'typedef int Real;\nint f(int e)\n{\n    const Real k = 2;\n    int a[k];\n    if (e) goto out;\n    a[0] = 1;\nout:\n    return k;\n}\n')
check('GOTO1 a typedef of an integer type is an integer scalar (const literal kept)', got, ['    const Real k = 2;'])

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
got = run(s1.r_goto_crossed_scopes, 'typedef float Real;\nstatic Real dot(V a, V b) { return a.x * b.x; }\n'
          'bool f(V a, V b, float r)\n{\n    if (r < 0) goto miss;\n    const Real& d = dot(a, b);\n'
          '    if (d > r) { miss: return false; }\n    return d < 2 * r;\n}\n')
check('GOTO2 (a) the scalar is a typedef of the unit (typedef float Real)', got, ['Real d; d = dot(a, b);'], ['const Real&'])
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
      ['        FNVNX_ANON3_BEGIN(0) union\n', '        }; FNVNX_ANON3_END(0) FNVNX_ANON3_REF(0, a) FNVNX_ANON3_REF(0, b) '
       'FNVNX_ANON3_REF(0, w) FNVNX_ANON3_REF(0, st)\n        st.read(e);',
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
      run(s1.r_msvc_base_layout, 'class D : public Ent03\n{\n  public:\n    int x;\n};\n'
          'struct E : B, public T<int, char>\n{\n    int y;\n};\n'),
      ['{ FNVNX_MSVC_BASE_END(Ent03)', '{ FNVNX_MSVC_BASE_END(T<int, char>)'], ['(public'])

# ---- ANON2
got = run(s1.r_anonymous_aggregate_members,
          'struct String\n{\n    char* text;\n    ~String();\n    unsigned int Len();\n};\nstruct Extra\n{\n    union\n    {\n'
          '        struct\n        {\n            int value;\n            String unit04;\n        };\n        int other;\n'
          '    };\n};\nvoid f(Extra* slot) { if (slot->unit04.Len()) slot->value = 0; }\n')
check('ANON2 raw storage for a member with a destructor in an anonymous struct of a union', got,
      ['alignas(String) unsigned char unit04[sizeof(String)]; /* fnv-nx ANON2 */',
       'if ((*(String*)(slot->unit04)).Len())', 'int value;'])

# the member declared in a header, used in the unit; another class of the unit has its own member of that name
_a = tempfile.mkdtemp()
os.makedirs(os.path.join(_a, 'src', 'anim'))
open(os.path.join(_a, 'src', 'anim', 'interp_unit.h'), 'w').write(
    'class Ent05\n{\n  public:\n    float x;\n    Ent05& operator=(const Ent05& o);\n};\n'
    'class Ent06\n{\n  public:\n    union\n    {\n        struct\n        {\n            Ent05 m_07;\n'
    '            int m_uiLast;\n        };\n        int state;\n    };\n    bool Update(Ent05& value);\n};\n')
_unit = os.path.join(_a, 'src', 'anim', 'interp.cpp')
_saved, s1._ANON2_NAMES = s1._ANON2_NAMES, None
got = run(s1.r_anonymous_aggregate_members,
          '#include "interp_unit.h"\n// fills m_07\nbool Ent06::Update(Ent05& value)\n{\n'
          '    value = m_07;\n    return m_07.x != 0;\n}\nstruct Shell : public Ent06\n{\n'
          '    bool Eq(Ent06* other) { return &m_07 == &other->m_07; }\n};\n'
          'class Other\n{\n  public:\n    Ent05 m_07;\n    void CloneTo(Other* pkDest);\n};\n'
          'void Other::CloneTo(Other* pkDest)\n{\n    pkDest->m_07 = m_07;\n}\n', _unit)
s1._ANON2_NAMES = _saved
check('ANON2 converts the uses in a file that includes the member, only where they are that member', got,
      ['// fills m_07\n', 'value = (*(Ent05*)(m_07));', 'return (*(Ent05*)(m_07)).x != 0;',
       'return &(*(Ent05*)(m_07)) == &(*(Ent05*)(other->m_07));', '    Ent05 m_07;\n',
       'pkDest->m_07 = m_07;'])

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
_u = {'source': 'src/x.cpp', 'functions': [{'address': '0x00401300', 'symbol': '?F@S@@QAEMXZ', 'size': 31}],
      'symbols': {'?F@S@@QAEMXZ': '0x00401300'}}

# ---- STR1 uses the declaration in scope
got = run(s1.r_string_array_address, 'extern char str_01082ab8[];\nvoid f() { g(&str_01082ab8); }\nnamespace N {\n'
          'extern char str_01082ab8;\nvoid h() { g(&str_01082ab8); }\n}\nextern "C" char str_a0000000[], str_b0000000[],\n'
          '    str_c0000000[];\nvoid k() { const char* p = str_c0000000; g(&str_c0000000); }\n')
check('STR1 rewrites only where the array is the declaration in scope', got,
      ['void f() { g(str_01082ab8); }', 'void h() { g(&str_01082ab8); }', 'g(str_c0000000); }'])

got = run(s1.r_string_array_address, 'extern char tbl[];\nextern char sized[8];\nextern int ints[];\nextern const char* ptrs[];\n'
          'void f() { g(&tbl); g(&sized); g(&ints); g(&ptrs); }\n')
check('STR1 only char arrays of unknown bound, whatever their names', got,
      ['g(tbl); g(&sized); g(&ints); g(&ptrs);'])

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

# ---- ALIGN1
got = run(s1.r_class_alignment, '__declspec(align(16)) struct S_V4 { float x, y, z, w; };\n'
          '__declspec(align(16))class H:public C\n{\n};\n__declspec(align(16))struct TAU32;\n'
          'void f() {\n    __declspec(align(16)) struct Buf\n    {\n        F4 a;\n    } buf;\n}\n'
          'struct A { __declspec(align(16)) struct Atoms m_08; __declspec(align(8)) volatile int t; };\n'
          'template <class T> __declspec(align(16)) __declspec(novtable) struct X { T a; };\n')
check('ALIGN1 moves align behind the class key of definitions and forward declarations', got,
      ['struct __declspec(align(16)) S_V4 { float', 'class __declspec(align(16)) H:public C\n{',
       'struct __declspec(align(16)) TAU32;', '    struct __declspec(align(16)) Buf\n    {',
       '__declspec(align(16)) struct Atoms m_08;', '__declspec(align(8)) volatile int t;',
       'template <class T> __declspec(novtable) struct __declspec(align(16)) X { T a; };'])
check('ALIGN1 keeps the line count', str(got.count('\n')), ['13'])

# ---- ENUM2
got = run(s1.r_enum_int_values, 'enum\n{\n    CHANGE_SCRIPT_VARIABLES = 0x80000000, // = 0x90000000\n    B = 4\n};\n'
          'enum Ent09 { SP_NONE = 0xffffffff, SP_X = 0x80000000u, SP_Y = -1 };\n'
          'enum Small { S0 = 1 };\nenum Expr { E0 = 0x80000000 | 1 };\nenum class Sc { Q = 0x80000000 };\n'
          'enum Huge { H = 0x100000000 };\nenum Based : unsigned { U = 0x80000000 };\n')
check('ENUM2 gives enums with values >= 2^31 MSVC\'s int', got,
      ['enum : int\n{\n    CHANGE_SCRIPT_VARIABLES = (int)0x80000000, // = 0x90000000\n    B = 4\n};',
       'enum Ent09 : int { SP_NONE = (int)0xffffffff, SP_X = (int)0x80000000u, SP_Y = -1 };',
       'enum Small { S0 = 1 };', 'enum Expr { E0 = 0x80000000 | 1 };', 'enum class Sc { Q = 0x80000000 };',
       'enum Huge { H = 0x100000000 };', 'enum Based : unsigned { U = 0x80000000 };'])

# ---- STATIC1 namespace statics (S13: bound by their plain name; unit10, unit11)
_n = tempfile.mkdtemp()
os.makedirs(os.path.join(_n, 'src')); os.makedirs(os.path.join(_n, 'build'))
_json.dump({'units': [{'source': 'src/r.cpp', 'symbols': {
    '_s_12': '0x011da904', '_s_iLastState': '0x011a081c', '_s_szName': '0x01080000', '_g_pInst': '0x011d96c0',
    '?g_pInst@@3PAUH@@A': '0x011d96c0', '?g_pInst@fnv_1@@3PAUH@@A': '0x011d96c0', '_s_kList': '0x011d0000'}}]},
           open(os.path.join(_n, 'build', 'units.json'), 'w'))
_src = ('struct H;\nstruct L { L(); };\nextern H* g_pInst;\nstatic H* g_pInst;\nnamespace fnv_1 {\nextern H* g_pInst;\n}\n'
        'namespace fnv_004012c0 {\nstatic float s_12;\nstatic int s_iLastState = 4;\n'
        'static const char s_szName[] = "x";\nstatic H* g_pInst;\nstatic L s_kList;\nvoid f() { s_12 = 1; }\n}\n')
open(os.path.join(_n, 'src', 'r.cpp'), 'w').write(_src)
_saved = (s1._IMAGE_NAMES, dict(s1._IMAGE_CNAMES), dict(s1._IMAGE_ADDRS))
s1._IMAGE_NAMES = None; s1._IMAGE_CNAMES.clear(); s1._IMAGE_ADDRS.clear()
got = run(s1.r_image_statics, _src, os.path.join(_n, 'src', 'r.cpp'))
s1._IMAGE_NAMES = _saved[0]; s1._IMAGE_CNAMES.clear(); s1._IMAGE_CNAMES.update(_saved[1])
s1._IMAGE_ADDRS.clear(); s1._IMAGE_ADDRS.update(_saved[2])
check('STATIC1 gives namespace statics bound by their plain name C linkage (image variables declared only)', got,
      ['extern "C" { extern float s_12; }', 'extern "C" { int s_iLastState = 4; }',
       'extern "C" { extern const char s_szName[] = "x"; }', 'extern "C" { extern H* g_pInst; }',
       'extern "C" { L s_kList; }', 'H* g_pInst;\nnamespace fnv_1', 'void f() { s_12 = 1; }'])

# ---- VA1 hand-built va_list: a macro both compilers define (the decomp's text on cl), only the parenthesised `(&f + 1)`
got = run(s1.r_va_list, 'void f(const char* fmt, ...) {\n    char* args = (char*)(&fmt + 1);\n    char* b = (char*)&fmt + 1;\n}\n')
check('VA1 hand-built list becomes FNVNX_VA_HAND, the unparenthesised byte offset stays', got,
      ['FNVNX_VA_HAND(args, fmt);', 'char* b = (char*)&fmt + 1;'], ['va_start('])

# ---- VA2
got = run(s1.r_va_enum_args, 'enum Index : int { A };\nvoid f(va_list args) {\n    Index d = va_arg(args, Index);\n'
          '    const char* n = va_arg(args, const char*);\n    int i = va_arg(args, int);\n}\n')
check('VA2 reads an enum through ... as the int it was passed as', got,
      ['Index d = (Index)va_arg(args, int);', 'va_arg(args, const char*)', 'int i = va_arg(args, int);'])

# ---- SSE2 with the decomp's own name for __m128
got = prepare.r_sse_local_protos('/x/src/a.cpp', 'typedef __m128 ClosestPointQuad;\nClosestPointQuad _mm_shuffle_ps(ClosestPointQuad, ClosestPointQuad, unsigned int);\n'
                                 'extern "C" ::unit02 __cdecl _mm_load_ps(const float*);\n'
                                 'ClosestPointQuad f(ClosestPointQuad a) {\n    return _mm_add_ps(a, a);\n}\n', Counter())
check('SSE2 drops intrinsic prototypes with a typedef return type, keeps statements', got,
      ['typedef __m128 ClosestPointQuad;\n', '    return _mm_add_ps(a, a);'], ['_mm_shuffle_ps', '_mm_load_ps'])

# ---- SSE2 parenthesized prototype names, SSE4 unqualified using-declarations
got = prepare.r_sse_local_protos('/x/src/a.cpp', 'extern "C" ::unit02 (_mm_load_ps)(const float*);\nint k;\n', Counter())
got2 = prepare.r_sse_qualified('/x/src/a.cpp', 'namespace N {\nusing ::_mm_add_ps;\nusing _mm_mul_ps;\nusing ::unit13;\n}\n', Counter())
check('SSE2 drops (_mm_x)(...) prototypes, SSE4 unqualified using-declarations of intrinsics', got + got2,
      ['int k;', 'using ::unit13;'], ['_mm_load_ps', '_mm_add_ps', '_mm_mul_ps'])

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

# ---- DECL2 the decomp's merged SDK record `struct _RTL_CRITICAL_SECTION_DEBUG {...} RTL_CRITICAL_SECTION` (S1-crt)
_cs = ('extern "C"{\ntypedef struct _RTL_CRITICAL_SECTION_DEBUG{\nWORD Type;\nstruct _RTL_CRITICAL_SECTION*CriticalSection;\n'
       'DWORD Flags;\n}RTL_CRITICAL_SECTION,*PRTL_CRITICAL_SECTION;\ntypedef RTL_CRITICAL_SECTION CRITICAL_SECTION;}\n'
       'class Lock { CRITICAL_SECTION m_kSection; };\n')
_csg = run(s1.r_sdk_types, _cs)
check('DECL2 drops a _RTL_CRITICAL_SECTION_DEBUG copy named RTL_CRITICAL_SECTION, same lines', _csg,
      ['#include "fnvnx_win32.h" /* fnv-nx DECL2 */\nextern "C"{\n/* fnv-nx DECL2 */\n\n\n\n\n'
       'typedef RTL_CRITICAL_SECTION CRITICAL_SECTION;}'], ['WORD Type'])

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
    {'address': '0x00401080', 'symbol': '?abs@std@@YANM@Z', 'size': 20},
    {'address': '0x004010c0', 'symbol': '?f@@YAHH@Z', 'size': 8}], 'symbols': {}},
    {'source': 'src/h.cpp', 'functions': [{'address': '0x00401100', 'symbol': '?g@@YAHH@Z', 'size': 8}],
     'symbols': {'?abs@std@@YANM@Z': '0x00401080'}}]}, open(os.path.join(_s2, 'build', 'units.json'), 'w'))
s1._STD2_FNS = None; s1.STD2_RENAMES.clear()
_g = ('// @fnv 0x00401080\nFNVNX_KEEP double std::abs(float value)\n{\n    return value < 0 ? -value : value;\n}\n'
      '// @fnv 0x004010c0\nint f(int x) { return (int)std::abs((float)x); }\n')
_o = run(s1.r_std_definitions, _g, os.path.join(_s2, 'src', 'g.cpp'))
check('STD2 file-scope std:: definition the recipe decorates in std -> fnvnx_std_',
      _o, ['FNVNX_KEEP double fnvnx_std_abs(float value)', 'return (int)std::abs((float)x);'], ['double std::abs'])
check('STD2 a unit whose recipe has no std:: definition is untouched',
      run(s1.r_std_definitions, _g.replace('0x00401080', '0x00401140'), os.path.join(_s2, 'src', 'h.cpp')),
      ['double std::abs(float value)'], ['fnvnx_std_'])
check('STD2 a member definition in a class scope is not at file scope',
      run(s1.r_std_definitions, 'struct S {\n// @fnv 0x00401080\ndouble std::abs(float v) { return v; }\n};\n',
          os.path.join(_s2, 'src', 'g.cpp')), [], ['fnvnx_std_'])
s1.rename_std_definitions(_s2, Counter())
_u = _json.load(open(os.path.join(_s2, 'build', 'units.json')))['units']
check('STD2 POST hook renames the definition\'s recipe symbol only', _json.dumps(_u),
      ['"?fnvnx_std_abs@@YANM@Z"', '"?f@@YAHH@Z"', '"?abs@std@@YANM@Z": "0x00401080"'])
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
# ---- S1-refs agent C: TPL1 out-of-class members of a header's class template,
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
    {'source': 'src/n.cpp', 'symbols': {'?strcpy_s@ns1@@YAHPADIPBD@Z': '0x00401180'}},
    {'source': 'src/g.cpp', 'symbols': {'?strcpy_s@ns1@@YAHPADIPBD@Z': '0x00401180',
                                        '?strcpy_s@@YAHPADIPBD@Z': '0x00401180'}},
    {'source': 'src/d.cpp', 'symbols': {'_Fmt': '0x004011c0', '?Fmt@@YAPADPADIPBDZZ': '0x004011c0',
                                        '_Pick': '0x00401200', '?Pick@@YAHPAXE@Z': '0x00401200'}}]},
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
_sta = Counter()
_st = Counter()
_st = Counter()
_st = Counter()
_dt = 'struct E { int a; };\n// @fnv 0x00401440\nstatic void __declspec(noinline)\n    Copy(E* d, const E* s, unsigned int n)\n{\n    d[0] = s[0];\n}\n'
_st = Counter()

# ---- S1-layout: EBO3 (an empty base after a base with data goes after it, as in MSVC)
_E3 = ('struct Ent14\n{\n    int a, b, c, d;\n};\nstruct Ent15\n{\n    Ent15* Next(Ent15* p);\n};\n'
       'struct Node : Ent14, Ent15\n{\n};\nstruct Node2 : Ent14, Ent15\n{\n    int x;\n};\n'
       'void f(Node* n) { static_cast<Ent15*>(n)->Next(0); }\n')
_e3o = run(s1.r_later_empty_base, _E3)
check('EBO3 FNVNX_EBO2 first in an empty base that follows a base with data', _e3o,
      ['struct Ent15\n{ FNVNX_EBO2 /* fnv-nx EBO3 */\n'], ['struct Ent14\n{ FNVNX'])
check('EBO3 an empty base used as an object is left alone',
      run(s1.r_later_empty_base, _E3 + 'Ent15 g_e;\n'), [], ['FNVNX_EBO2'])
check('EBO3 the empty base first (or after a base of another file): nothing',
      run(s1.r_later_empty_base, _E3.replace('Ent14, Ent15', 'Ent15, Ent14')), [], ['FNVNX_EBO2'])
check('EBO3 a virtual base: nothing',
      run(s1.r_later_empty_base, _E3.replace('Node : Ent14, Ent15', 'Node : Ent14, virtual Ent15')),
      [], ['FNVNX_EBO2'])
check('EBO3 headers are left alone', run(s1.r_later_empty_base, _E3, '/nonexistent/src/x.h'), [], ['FNVNX_EBO2'])

# ---- S1-rules: STAND7 (an out-of-class inline template member stand-in bound elsewhere -> MSVC only)
_s7 = tempfile.mkdtemp(); os.makedirs(os.path.join(_s7, 'src')); os.makedirs(os.path.join(_s7, 'build'))
_json.dump({'units': [{'source': 'src/u.cpp', 'flags': ['/MT', '/Od', '/Ob1'],
                       'symbols': {'?assign@?$basic_string@DU?$char_traits@D@std@@U?$allocator@D@2@@std@@QAEAAV12@ABV12@II@Z': '0x00401240'},
                       'functions': [{'address': '0x00401380', 'symbol': '?f@@YAXXZ', 'size': 8}]},
                      {'source': 'src/v.cpp', 'symbols': {}, 'functions': [{'address': '0x00401240', 'symbol': '?assign@x', 'size': 8}]}]},
           open(os.path.join(_s7, 'build', 'units.json'), 'w'))
_S7 = ('template <class C, class T, class A>\ninline basic_string<C, T, A>& basic_string<C, T, A>::assign(const basic_string<C, T, A>& r,'
       ' unsigned int o, unsigned int n)\n{\n    if (o == 0x7f3f3f3f) { _Tidy(true); }\n    return *this;\n}\n'
       '// @fnv 0x00401380\nvoid f() {}\n')
_saved = (s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS)
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
_json.dump({'units': [{'source': 'src/u.cpp', 'flags': ['/MT', '/O2'], 'symbols': {'?assign@?$basic_string@DU?$char_traits@D@std@@U?$allocator@D@2@@std@@QAEAAV12@ABV12@II@Z': '0x00401240'},
                       'functions': []}, {'source': 'src/v.cpp', 'symbols': {}, 'functions': [{'address': '0x00401240', 'symbol': '?assign@x', 'size': 8}]}]},
           open(os.path.join(_s7, 'build', 'units.json'), 'w'))
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = None, None, None
s1._UNIT_SYMBOLS, s1._UNIT_INFO, s1._ALL_FUNCTIONS = _saved

# ---- S1-rules: TAIL1-PACK (the base filler under #pragma pack(N): MSVC's min(alignof(base), N); LAYOUT gate pack rows)
_PK = ('struct B { virtual void f(); };\n#pragma pack(push, 1)\nstruct P : B { short a; };\n#pragma pack(pop)\n'
       'struct Q : B { short a; };\n#pragma pack(push, 4)\n#pragma pack(push, 2)\nstruct R : B { int a; };\n#pragma pack(pop)\n'
       'struct S : B { int a; };\n#pragma pack(pop)\n')
_pko = run(s1.r_msvc_base_layout, _PK)
check('TAIL1-PACK under pack(1) / nested push 2: FNVNX_MSVC_BASE_END_PACK(N, base); after the pops the plain filler',
      _pko, ['struct P : B { FNVNX_MSVC_BASE_END_PACK(1, B)', 'struct Q : B { FNVNX_MSVC_BASE_END(B)',
             'struct R : B { FNVNX_MSVC_BASE_END_PACK(2, B)', 'struct S : B { FNVNX_MSVC_BASE_END_PACK(4, B)'])
_dd = tempfile.mkdtemp()
_snip = os.path.join(_dd, 'snippet.cpp')
open(_snip, 'w').write(
    'namespace N {\nclass A { friend class W; friend class V; W* w; V* v; };\nclass W {}; class V {};\n}\n'
    'typedef union ::__m128 Q;\nextern "C" ::__m128(_mm_load_ss)(const float* p);\nunion __declspec(intrin_type) __m128 '
    '{ struct { float x, y, z, w; } lanes; };\nfloat f(__m128 v) { return v.lanes.x + v.m128_f32[1]; }\n'
    'struct S { int* vtbl; S() { vtbl = 0; } virtual ~S(); };\nint g(int a) { if (a) return 1; }\n'
    'enum E { E_A = 0x80000000 };\nvoid h(va_list ap) { int i = va_arg(ap, int); }\n')
_shutil.rmtree(_dd, ignore_errors=True)

# ---- S1-rules: VFPAD1 (MSVC pads a class's own vfptr to the class's alignment; LAYOUT gate vfpad rows)
_VF = ('struct V16 { virtual ~V16(); unsigned int m10; unit16 m20; };\n'
       'struct VD { FNVNX_D22(VD, virtual void v0() ;) /* fnv-nx D22 */\n    double m08; int* p; };\n'
       'struct VS { virtual void f(); int a; char b[4]; };\n'
       'struct VB : B { virtual void f(); unit16 m; };\n'
       'struct VN { struct In { __m128 q; }; virtual void f(); In m; };\n'
       'class NF\n{\n  public:\n    typedef unsigned(__cdecl* RFN)(NF* p);\n    virtual ~NF();\n    RFN m_r; double d;\n};\n'
       '#pragma pack(push, 4)\nstruct VP4 { virtual void f(); double d; };\n#pragma pack(pop)\n'
       '#pragma pack(push, 8)\nstruct VP8 { virtual void f(); unit16 v; };\n#pragma pack(pop)\n')
_vfo = run(s1.r_vfptr_pad, _VF)
check('VFPAD1 puts FNVNX_VFPAD(member types) first in a no-base class with a vfptr (not D22\'s macro text, not pointers)',
      _vfo, ['struct V16 { FNVNX_VFPAD(unit16) virtual', 'struct VD { FNVNX_VFPAD(double) FNVNX_D22',
             'struct VP8 { FNVNX_VFPAD_PACK(8, unit16) virtual', 'class NF\n{ FNVNX_VFPAD(double)\n'],
      ['struct VS { FNVNX', 'struct VB : B { FNVNX', 'struct VN { FNVNX',
       'struct VP4 { FNVNX', 'int*)'])

support.finish()
