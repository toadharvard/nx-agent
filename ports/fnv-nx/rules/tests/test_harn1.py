#!/usr/bin/env python3
"""tests/s2d - HARN1 (tools/rules/s2_bodies.py) on a hand-written unit (no decomp content).

    python3 -I rules/tests/test_harn1.py

An untagged free definition whose name no unit's symbols / functions reference becomes static (with its
namespace-scope prototypes, extern "C" dropped); a tagged one, a member, a referenced one, an inline one, one with a
block-scope declaration before it, one with an undefined overload's prototype and one another unit declares
(and takes the address of) keep their linkage.
"""
import collections
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import support  # noqa: E402
support.legacy_units_lookup()
import s2_bodies   # noqa: E402

SRC = '''extern "C" int stub_a(int);
extern "C" int stub_b(int);
int FUN_00001040(int a, int b);
int stub_caller(void);
void* g_text = (void*)"int in_literal() {";
// @fnv 0x00001040
int FUN_00001040(int a, int b)
{
    return a + b;
}
int stub_caller(void)
{
    return FUN_00001040(stub_a(1), stub_b(2));
}
extern "C" void __cdecl Entry_c(void)
{
    stub_caller();
}
extern "C" __declspec(noinline) void dummy_caller(void)
{
    stub_a(3);
}
namespace ns {
void ns_caller()
{
    stub_b(4);
}
}
struct S
{
    int Member(int k)
    {
        return stub_a(k);
    }
};
int Referenced(int k)
{
    return stub_a(k);
}
inline int Inl(int k)
{
    return stub_a(k);
}
void Late();
void UsesLate()
{
    Late();
}
void Late()
{
    stub_a(5);
}
void Blocky()
{
    void Later2();
    Later2();
}
void Later2()
{
    stub_b(6);
}
int Over(int a);
int Over(float a);
int Over(int a)
{
    return a;
}
int keep_caller(void)
{
    return stub_b(7);
}
int Forwarded(int, ...);
#if defined(_MSC_VER)
int Forwarded(int a, ...)
{
    return stub_a(a);
}
#else
__asm__(".globl _Z9Forwardedizz\\n");
#endif
int CallsForwarded(void)
{
    return Forwarded(1);
}
/*
void in_comment()
{
}
*/
'''


class Harn1(unittest.TestCase):
    def test_rule(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            units = {'units': [
                {'source': 'src/a.cpp', 'flags': ['/O2'],
                 'functions': [{'symbol': '?FUN_00001040@@YAHHH@Z', 'address': '0x00001040'}],
                 'symbols': {'_stub_a': '0x00001080'}},
                {'source': 'src/b.cpp', 'flags': ['/O2'], 'functions': [],
                 'symbols': {'?Referenced@@YAHH@Z': '0x000010c0'}}]}
            with open(os.path.join(d, 'build', 'units.json'), 'w') as fh:
                json.dump(units, fh)
            path = os.path.join(d, 'src', 'a.cpp')
            with open(path, 'w') as fh:
                fh.write(SRC)
            with open(os.path.join(d, 'src', 'b.cpp'), 'w') as fh:   # a variant copy takes its address
                fh.write('int keep_caller(void);\nint (*volatile g_keep)(void) = keep_caller;\n')
            st = collections.Counter()
            s2_bodies._REF_IDENTS = s2_bodies._FOREIGN = None
            out = s2_bodies.r_harness_internal(path, SRC, st)
            s2_bodies._REF_IDENTS = s2_bodies._FOREIGN = None
        for made in ('static int stub_caller(void);\n', 'static int stub_caller(void)\n{',
                     'static void __cdecl Entry_c(void)', 'static __declspec(noinline) void dummy_caller(void)',
                     'static void ns_caller()', 'static void UsesLate()', 'static void Late();',
                     'static void Late()\n', 'static void Blocky()', 'static int CallsForwarded(void)'):
            self.assertIn(made, out)
        for kept in ('\nint FUN_00001040(int a, int b)\n', '    int Member(int k)', '\nint Referenced(int k)',
                     '\ninline int Inl(', '\nvoid Later2()\n', '\nint Over(int a)\n{', '\nint keep_caller(void)', 'int Forwarded(int, ...);\n#if', '\nint Forwarded(int a, ...)\n{',
                     'void in_comment()',
                     'extern "C" int stub_a(int);', '"int in_literal() {"'):
            self.assertIn(kept, out)
        self.assertEqual(st['HARN1'], 10)
        self.assertEqual(out.count('\n'), SRC.count('\n'))   # line numbers stay


if __name__ == '__main__':
    unittest.main()
