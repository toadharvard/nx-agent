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

# ---- CTOR1
check('CTOR1 explicit constructor call of a template class',
      run(s1.r_ctor_calls, 'void f() { ((std::vector<char>*)this)->std::vector<char>::vector(); }\n'),
      ['::new ((void*)(std::vector<char>*)(((std::vector<char>*)this))) std::vector<char>()'])

check('CTOR1 a constructor definition after a comment ending in "." is not a call',
      run(s1.r_ctor_calls, '// see the matcher checks.\nA::A(int x)\n    : m(x)\n{}\n'), ['A::A(int x)'], ['::new'])

# ---- scope heads
spans = prepare.scope_spans('// ' + 'y' * 295 + ' (a) b\n// c\n\nstruct T\n{\n    int f();\n};\n')
check('scope head after a cut comment is a class', str([k for _, _, k, _ in spans]), ['class'], ['function'])

print('%d failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
