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

check('MATH1 in a namespace that takes the C fabs: the copy goes',
      run(s1.r_math_overloads, 'namespace N {\nusing ::fabs; /* fnv-nx DECL1 */\ninline float fabs(float f)\n{\n'
          '    return (float)fabs((double)f);\n}\nfloat g(float x) { return fabs(x); }\n}\n'),
      ['/* fnv-nx MATH1 */', 'float g(float x)'], ['(float)fabs((double)f)', '#include <math.h>'])
check('MATH1 in a namespace of its own: the overload stays',
      run(s1.r_math_overloads, 'namespace N {\ninline float fabs(float f)\n{\n    return f < 0 ? -f : f;\n}\n}\n'),
      ['return f < 0 ? -f : f;'], ['MATH1'])

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
check('DTOR1 needs code after the includes', run(s1.r_incomplete_pointee_dtors, '#include "b.h"\n', _unit), (), ['DTOR1'])
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
      ['enum DIALOGUE_TYPE : int;', 'enum DIALOGUE_TYPE : int\n{', 'enum WIDE;', 'enum Other { B };'])

# ---- DECL1 with a trailing comment
check('DECL1 drops a CRT prototype that ends in a // comment',
      prepare.r_platform_protos('/x/src/a.h', 'extern "C" char* __cdecl strchr(const char*, int);  // CRT strchr (x)\n',
                                Counter()), ['#include <string.h> /* fnv-nx DECL1 */'], ['__cdecl strchr'])

check('SSE1 elaborated __m128 references',
      prepare.r_sse_local_types('x.cpp', 'typedef union __m128 hkQuadReal;\nvoid f(struct __m128* p);\n', Counter()),
      ['typedef __m128 hkQuadReal;', 'void f(__m128* p);'])

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

# ---- GOTO1
got = run(s1.r_goto_scalar_inits, 'int f(V& v, int e)\n{\n    if (e) goto out;\n    {\n        float dw = v.d(3), di = v.d(e);\n'
          '        int n = 4;\n        char *p = 0, *q = p;\n        return (int)(dw + di) + n + (p == q);\n    }\nout:\n    return 0;\n}\n')
check('GOTO1 splits declarator lists declarator by declarator', got,
      ['float dw, di; dw = v.d(3); di = v.d(e);', 'int n; n = 4;', 'char *p, *q; p = 0; q = p;'])

# ---- TAIL1
check('TAIL1 names the last base without its access specifier',
      run(s1.r_msvc_base_layout, 'class D : public NiMemObject\n{\n  public:\n    int x;\n};\n'
          'struct E : B, public T<int, char>\n{\n    int y;\n};\n'),
      ['{ FNVNX_MSVC_BASE_END(NiMemObject)', '{ FNVNX_MSVC_BASE_END(T<int, char>)'], ['(public'])

# ---- scope heads
spans = prepare.scope_spans('// ' + 'y' * 295 + ' (a) b\n// c\n\nstruct T\n{\n    int f();\n};\n')
check('scope head after a cut comment is a class', str([k for _, _, k, _ in spans]), ['class'], ['function'])

print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
