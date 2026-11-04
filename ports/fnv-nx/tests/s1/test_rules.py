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
check('MATH1 next to the namespace\'s own extern "C" prototype: the copy stays (MSVC found it)',
      run(s1.r_math_overloads, 'namespace N {\nextern "C" double cos(double);\ninline float __cdecl cos(float _X)\n{\n'
          '    return cosf(_X);\n}\n}\n'), ['return cosf(_X);'], ['MATH1'])
check('MATH1 in a namespace of its own: the overload stays',
      run(s1.r_math_overloads, 'namespace N {\ninline float fabs(float f)\n{\n    return f < 0 ? -f : f;\n}\n}\n'),
      ['return f < 0 ? -f : f;'], ['MATH1'])

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
      ['enum DIALOGUE_TYPE : int;', 'enum DIALOGUE_TYPE : int\n{', 'enum WIDE : int;', 'enum Other { B };'])
s1._OPAQUE_NAMES = None
check('ENUM1 a definition int cannot hold keeps its form',
      run(s1.r_opaque_enums, 'enum WIDE { W = 0x80000000 };\n', os.path.join(_e, 'src', 'c.cpp')), ['enum WIDE { W'])
s1._OPAQUE_NAMES = None

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
       "'symbols': {}}]} 2", "'?x@@3HA': '0x1'"])

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
       '    static void* operator new(size_t n);\n};\nstruct Pd : Pm\n{\n    int k;\n};\n'
       'class Pc\n{\n    int h;\npublic:\n    virtual ~Pc() {}\n    static void* operator new(size_t, void* p) { return p; }\n'
       '    static void operator delete(void*, void*) {}\n};\n')
_o5 = run(s1.r_placement_only_delete, _n5)
check('NEW5 placement-only delete + virtual dtor: global delete member',
      _o5, ['struct Pm\n{ FNVNX_GLOBAL_DELETE /* fnv-nx NEW5 */\n',
            'class Pc\n{ public: FNVNX_GLOBAL_DELETE private: /* fnv-nx NEW5 */\n    int h;'], ['struct Pd : Pm\n{ F'])
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
           if l.startswith('#define FNVNX_GLOBAL_DELETE ')]          # the shim's own definition of the macro
    open(_c5, 'w').write('#include <stddef.h>\n' + ''.join(_m5) + _o5 +
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

print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
