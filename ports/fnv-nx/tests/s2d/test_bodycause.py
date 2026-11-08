#!/usr/bin/env python3
"""tests/s2d - cause classes of `missing` names (tools/link/bodycause.py) on a hand-written tree and objects (no
decomp content).

    python3 -I tests/s2d/test_bodycause.py       # needs c++filt, nm and aarch64-linux-gnu-g++ or g++

Checks one name per cause: crt-import (std::, _Xran), data, name-mismatch (an object defines the member with
another parameter spelling; a GCC clone only; never another instantiation of the class template), not-compiled (a body in a source that is not a unit; a body under #if 0; an in-class
body), no-body (template-special, msvc-direct, other).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import bodycause  # noqa: E402

GXX = shutil.which('aarch64-linux-gnu-g++') or shutil.which('g++')

ROWS = [  # Itanium name, note
    ('_ZNSt6vectorIiSaIiEE5_TidyEv', 'no definition, address or body'),
    ('_ZN10StringBase5_XranEv', 'no definition, address or body'),
    ('_ZN3Foo6kCountE', 'not a function (data)'),
    ('_ZN3Foo3SetEj', 'no definition, address or body'),          # Foo::Set(unsigned) - objects have Set(int)
    ('_ZN3Bar4LoadEv', 'no definition, address or body'),         # body in src/extra.cpp, not a unit
    ('_ZN3Baz4DropEv', 'no definition, address or body'),         # body under #if 0
    ('_ZN3Qux3TopEv', 'no definition, address or body'),          # in-class body in a header copy
    ('_ZN4ListIiED1Ev', 'special member of a template or unnamed class'),
    ('_ZN4Zork4PokeEv', 'MSVC calls it directly (pristine reference); was missing: no definition, address or body'),
    ('_Z8LonelyFnv', 'no definition, address or body'),
    ('_ZN4BoxyIlE4SizeEv', 'no definition, address or body'),     # Boxy<long>::Size - objects have Boxy<int>
    ('_ZN2Cl4MakeEPKc', 'no definition, address or body'),        # only Cl::Make(char const*).isra.0 exists
    ('_ZN3ErrD1Ev', 'no definition, address or body'),            # Err::~Err - objects have Other::Err::~Err
    ('_ZN12fnv_000000013std3Str5eraseEjj', 'no definition, address or body'),   # unit copy; tree body in a header
    ('_ZN12fnv_000000023std3Str6assignEj', 'no definition, address or body'),   # unit copy; objects have assign(int)
    ('_ZN12fnv_000000033std3Str4zorkEv', 'no definition, address or body'),     # unit copy; no body anywhere
]
EXPECT = {
    '_ZNSt6vectorIiSaIiEE5_TidyEv': ('crt-import', 'std scope'),
    '_ZN10StringBase5_XranEv': ('crt-import', 'MSVC STL thrower _Xran'),
    '_ZN3Foo6kCountE': ('data', None),
    '_ZN3Foo3SetEj': ('name-mismatch', None),
    '_ZN3Bar4LoadEv': ('not-compiled', 'in a source that is not a unit'),
    '_ZN3Baz4DropEv': ('not-compiled', 'under #if 0'),
    '_ZN3Qux3TopEv': ('not-compiled', 'in a class copy or overload the referencing unit does not emit'),
    '_ZN4ListIiED1Ev': ('no-body', 'template-special'),
    '_ZN4Zork4PokeEv': ('no-body', 'msvc-direct'),
    '_Z8LonelyFnv': ('no-body', 'other'),
    '_ZN4BoxyIlE4SizeEv': ('no-body', 'other'),
    '_ZN12fnv_000000013std3Str5eraseEjj': ('not-compiled', 'in a class copy or overload the referencing unit '
                                                           'does not emit'),
    '_ZN12fnv_000000023std3Str6assignEj': ('name-mismatch', 'defined as another decoration'),
    '_ZN12fnv_000000033std3Str4zorkEv': ('crt-import', 'std scope (unit copy, no body in the tree)'),
    '_ZN3ErrD1Ev': ('no-body', 'other'),                          # another scope's class is no decoration of it
    '_ZN2Cl4MakeEPKc': ('name-mismatch', 'defined only as a GCC clone (.isra/.constprop: its own signature, no '
                                          'alias target)'),
}


@unittest.skipUnless(GXX and shutil.which('c++filt') and shutil.which('nm'), 'needs g++, c++filt and nm')
class Causes(unittest.TestCase):
    def test_classes(self):
        with tempfile.TemporaryDirectory() as d:
            tree, out, objs = (os.path.join(d, x) for x in ('tree', 'out', 'objs'))
            for p in ('tree/src', 'tree/include', 'tree/build', 'out/bodies', 'objs'):
                os.makedirs(os.path.join(d, p))
            open(os.path.join(tree, 'build', 'units.json'), 'w').write('{"units": [{"source": "src/a.cpp"}]}')
            open(os.path.join(tree, 'src', 'a.cpp'), 'w').write(
                'struct Foo { void Set(int); };\nvoid Foo::Set(int) {}\n'
                '#if 0\nstruct Baz { void Drop(); };\nvoid Baz::Drop() { }\n#endif\n')
            open(os.path.join(tree, 'src', 'extra.cpp'), 'w').write('void Bar::Load()\n{\n}\n')
            open(os.path.join(tree, 'include', 'q.h'), 'w').write(
                'class Qux\n{\n  public:\n    int Top() { return 1; }\n    void Down();\n};\n'
                'namespace std { struct Str { void erase(unsigned a, unsigned b) { } }; }\n')
            subprocess.run([GXX, '-c', '-x', 'c++', '-', '-o', os.path.join(objs, 'a.o')], check=True,
                           input='struct Foo { void Set(int); };\nvoid Foo::Set(int) {}\n'
                           'template <class T> struct Boxy { int Size(); };\n'
                           'template <> int Boxy<int>::Size() { return 0; }\n'
                           'extern "C" void make_clone() __asm__("_ZN2Cl4MakeEPKc.isra.0");\n'
                           'extern "C" void make_clone() {}\n'
                           'namespace std { struct Str { void assign(int); }; void Str::assign(int) {} }\n'
                           'namespace Other { struct Err { ~Err(); }; Err::~Err() {} }\n', text=True)
            with open(os.path.join(out, 'bodies', 'bodies.tsv'), 'w') as f:
                f.write('#name\tclass\taddress\ttarget\tunits\tnote\thost\n')
                for n, note in ROWS:
                    f.write('%s\tmissing\t\t\tsrc/b.cpp\t%s\t\n' % (n, note))
                f.write('_ZN3Foo3GetEv\tvcall\t\t\tsrc/b.cpp\tslot\t\n')
            traps = os.path.join(d, 'traps.txt')
            open(traps, 'w').write('_ZN3Foo3SetEj\n_Z8LonelyFnv\n')
            dst = os.path.join(d, 'causes.tsv')
            bodycause.main([out, tree, objs, '--traps', traps, '-o', dst])
            got = {}
            for line in open(dst):
                if not line.startswith('#'):
                    f = line.rstrip('\n').split('\t')
                    got[f[0]] = (f[2], f[3], f[5])
        self.assertEqual(set(got), set(EXPECT))
        for n, (cause, detail) in EXPECT.items():
            self.assertEqual(got[n][0], cause, n)
            if detail is not None:
                self.assertEqual(got[n][1], detail, n)
        self.assertEqual(got['_ZN3Foo3SetEj'][2], '1')
        self.assertEqual(got['_ZN3Bar4LoadEv'][2], '0')


A64 = shutil.which('aarch64-linux-gnu-g++')


@unittest.skipUnless(A64 and shutil.which('readelf') and shutil.which('nm'), 'needs aarch64-linux-gnu-g++, readelf, nm')
class Placeholders(unittest.TestCase):
    def test_reach(self):
        """`unresolved` C placeholders: called -> function, else variable; live when reachable from a placed
        `__fnv_` definition, a data table or another object's reference, harness-only when only an unreferenced
        convention caller uses it (a synthesized zz_ object's bare undefined name is no reference)."""
        a = ('extern "C" int stub_a(void); extern int g_h, g_live, g_tab, g_b; extern "C" int f_live(int);\n'
             'int stub_caller() { return stub_a() + g_h; }\n'
             'extern "C" int __fnv_00401000(int x) { return f_live(x) + g_live; }\n'
             'static int __attribute__((noinline)) viatab() { return g_tab; }\nint (*tab[])() = { viatab };\n'
             'int from_b() { return g_b; }\n')
        with tempfile.TemporaryDirectory() as d:
            for name, src in (('src_a.cpp.o', a), ('src_b.cpp.o', 'int from_b();\nint user() { return from_b(); }\n')):
                subprocess.run([A64, '-mabi=ilp32', '-O1', '-c', '-x', 'c++', '-', '-o', os.path.join(d, name)],
                               check=True, input=src, text=True)
            subprocess.run([A64, '-mabi=ilp32', '-c', '-x', 'assembler', '-', '-o',
                            os.path.join(d, 'zz_bd_src_a.cpp.o')], check=True, input='.globl _Z11stub_callerv\n',
                           text=True)
            rows = [{'name': n, 'units': 'src/a.cpp', 'cause': 'unresolved', 'detail': 'unresolved'}
                    for n in ('stub_a', 'g_h', 'f_live', 'g_live', 'g_tab', 'g_b')]
            rows.append({'name': '_ZN1A1fEv', 'units': 'src/a.cpp', 'cause': 'unresolved', 'detail': 'unresolved'})
            bodycause.placeholder_details(rows, d)
        self.assertEqual({r['name']: r['detail'] for r in rows},
                         {'stub_a': 'c function harness-only', 'g_h': 'c variable harness-only',
                          'f_live': 'c function live', 'g_live': 'c variable live', 'g_tab': 'c variable live',
                          'g_b': 'c variable live', '_ZN1A1fEv': 'unresolved'})


if __name__ == '__main__':
    unittest.main()
