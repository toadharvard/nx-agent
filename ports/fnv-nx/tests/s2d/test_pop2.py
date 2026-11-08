#!/usr/bin/env python3
"""tests/s2d - POP2 (tools/rules/s2_bodies.py, a prepare POST hook) on hand-written units (no decomp content).

    python3 -I tests/s2d/test_pop2.py

POP2: a getter whose reference pushes a word its definition does not pop, followed by a call that pops one word
more than its reference pushes, gets the word moved: `b->Flags(2)` used as a bool -> `b->Flags().FUN_<a>(2)`,
`Use(Get(a, f))` -> `Use(Get(a), f)`, `D(x)->R()` -> `D()->R(x)`; the overloads are declared in the class bodies
MSVC's names place (a header, a per-function namespace) and the new decorated names go to units.json. Side effects
in the moved argument, an overload clash, a result that is not read as a bool and a chained receiver with
arguments of its own leave the call alone. Line numbers stay; the results compile (host g++, -fsyntax-only) and the
new names demangle (llvm-undname) when the tools are there.
"""
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'rules'))
import prepare      # noqa: E402
sys.modules.setdefault('prepare', prepare)
import s2_bodies    # noqa: E402
import _msvcsig     # noqa: E402

HDR = '''#pragma once
struct FormMask
{
    unsigned int m;
    operator bool() const;
};
struct Buf
{
    FormMask Flags(unsigned int);
};
'''

SRC = '''#include "a_decls.h"
void g();
// @fnv 0x00403000
void F(Buf* b, int n)
{
    if (b->Flags(2))
        g();
    if (!b->Flags(n++))
        g();
    if (b->Flags(4))
        b->Flags(n);
    if (b->Flags(8).operator bool())
        g();
}
namespace fnv_00403100 {
struct FormMask
{
    int m;
    operator bool() const;
};
struct Buf
{
    FormMask Flags(int);
};
// @fnv 0x00403100
void G(Buf* b)
{
    if (((Buf*)b)->Flags(0x80000000))
        g();
}
}  // namespace fnv_00403100
struct H
{
    int Get(int, float);
    void Use(int);
    void Run(int a, float f);
};
// @fnv 0x00403200
void H::Run(int a, float f)
{
    Use(Get(a, f));
}
struct Cell
{
    void Activate();
    void Two();
};
struct Tes
{
    Cell* Active(void* p);
    Cell* Pair(void* p, void* q);
};
// @fnv 0x00403300
void K(Tes* t, void* p)
{
    t->Active(p)->Activate();
    t->Pair(p, p)->Two();
}
struct Clash
{
    int Get(int, float);
    void Use(int);
    void Use(int, int*);
    void Run(int a, float f);
};
// @fnv 0x00403400
void Clash::Run(int a, float f)
{
    Use(Get(a, f));
}
'''

UNITS = {'units': [
    {'source': 'src/a.cpp', 'flags': ['/Od'], 'functions': [
        {'symbol': '?F@@YAXPAUBuf@@H@Z', 'address': '0x00403000'}],
     'symbols': {
         '?Flags@Buf@@QAE?AUFormMask@@I@Z': '0x00401000', '??BFormMask@@QBE_NXZ': '0x00402000',
         '?Flags@Buf@fnv_00403100@@QAE?AUFormMask@2@H@Z': '0x00401000',
         '??BFormMask@fnv_00403100@@QBE_NXZ': '0x00402000',
         '?Get@H@@QAEHHM@Z': '0x00404000', '?Use@H@@QAEXH@Z': '0x00405000',
         '?Active@Tes@@QAEPAUCell@@PAX@Z': '0x00404100', '?Activate@Cell@@QAEXXZ': '0x00405100',
         '?Pair@Tes@@QAEPAUCell@@PAX0@Z': '0x00404200', '?Two@Cell@@QAEXXZ': '0x00405200',
         '?Get@Clash@@QAEHHM@Z': '0x00404000', '?Use@Clash@@QAEXH@Z': '0x00405000'}},
    {'source': 'src/b.cpp', 'flags': ['/Od'], 'symbols': {}, 'functions': [
        {'symbol': '?FUN_00401000@Owner@@QAE?AUColor@@XZ', 'address': '0x00401000'},      # pops its buffer only
        {'symbol': '?FUN_00402000@T@@QBE_NH@Z', 'address': '0x00402000'},                 # pops the flags
        {'symbol': '?FUN_00404000@X@@QAEHH@Z', 'address': '0x00404000'},                  # pops one word
        {'symbol': '?FUN_00405000@Y@@QAEXHM@Z', 'address': '0x00405000'},                 # pops two
        {'symbol': '?FUN_00404100@X@@QAEPAXXZ', 'address': '0x00404100'},
        {'symbol': '?FUN_00405100@Y@@QAEXPAX@Z', 'address': '0x00405100'},
        {'symbol': '?FUN_00404200@X@@QAEPAXXZ', 'address': '0x00404200'},
        {'symbol': '?FUN_00405200@Y@@QAEXPAXPAX@Z', 'address': '0x00405200'}]},
]}

SRC_INLINE = '''#define __forceinline inline
void g();
namespace fnv_00403500 {
struct FormMask
{
    unsigned int m;
    __forceinline operator bool() const
    {
        return m != 0;
    }
};
struct Buf
{
    FormMask Flags(unsigned int) const;
};
// @fnv 0x00403500
void F(Buf* b)
{
    if (b->Flags(0x80000000))
        g();
}
}  // namespace fnv_00403500
struct Cell
{
    void Activate() {}
};
struct Tes
{
    Cell* Active(void* p);
};
// @fnv 0x00403600
void K(Tes* t, void* p)
{
    t->Active(p)->Activate();
}
namespace fnv_00403700 {
struct FormMask
{
    unsigned int m;
    // @fnv 0x00402000
    operator bool() const
    {
        return m != 0;
    }
};
struct Buf
{
    FormMask Flags(unsigned int) const;
};
// @fnv 0x00403700
void F(Buf* b)
{
    if (b->Flags(8))
        g();
}
}  // namespace fnv_00403700
'''

UNITS_INLINE = {'units': [
    {'source': 'src/c.cpp', 'flags': ['/Od'], 'functions': [], 'symbols': {
        '?Flags@Buf@fnv_00403500@@QBE?AUFormMask@2@I@Z': '0x00401000',
        '??BFormMask@fnv_00403500@@QBE_NXZ': '0x00402000',
        '?Flags@Buf@fnv_00403700@@QBE?AUFormMask@2@I@Z': '0x00401000',
        '??BFormMask@fnv_00403700@@QBE_NXZ': '0x00402000',
        '?Active@Tes@@QAEPAUCell@@PAX@Z': '0x00404100', '?Activate@Cell@@QAEXXZ': '0x00405100'}},
    UNITS['units'][1],
]}


def syntax_ok(path, inc):
    cxx = shutil.which('g++')
    if not cxx:
        return None
    r = subprocess.run([cxx, '-fsyntax-only', '-fms-extensions', '-fpermissive', '-w', '-D__stdcall=', '-I', inc, path],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def undname(name):
    tool = shutil.which('llvm-undname') or shutil.which('llvm-undname-18')
    if not tool:
        return None
    r = subprocess.run([tool, name], capture_output=True, text=True)
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 else ''


class MsvcSig(unittest.TestCase):
    def test_words(self):
        f = _msvcsig.parse('?GetChangeFlags@BGSLoadFormBuffer@@QAE?AUFormChangeMask@@I@Z')
        self.assertEqual((f.ident, f.scopes, f.ret_class, _msvcsig.stack_words(f)),
                         ('GetChangeFlags', ['BGSLoadFormBuffer'], ['FormChangeMask'], 2))   # buffer + flags
        f = _msvcsig.parse('?FUN_00428110@Owner@s428110@@QAE?AUColor@2@XZ')    # name back-reference
        self.assertEqual((f.ret_class, _msvcsig.stack_words(f)), (['Color', 's428110'], 1))
        f = _msvcsig.parse('??BFormChangeMask@fnv_00526570@@QBE_NXZ')
        self.assertEqual((f.special, f.const, _msvcsig.stack_words(f)), ('operator bool', True, 0))
        f = _msvcsig.parse('?FUN_00560dc0@@YIHPAVTESDataHandler@@@Z')           # fastcall: the pointer in ecx
        self.assertEqual(_msvcsig.stack_words(f), 0)
        self.assertIsNone(_msvcsig.parse('?g_data@@3HA'))                         # a variable


class Pop2(unittest.TestCase):
    def run_hook(self, d):
        stats, files = collections.Counter(), collections.Counter()
        s2_bodies.pop_chains(d, stats, files)
        return stats

    def test_chains(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            json.dump(UNITS, open(os.path.join(d, 'build', 'units.json'), 'w'))
            open(os.path.join(d, 'src', 'a_decls.h'), 'w').write(HDR)
            open(os.path.join(d, 'src', 'a.cpp'), 'w').write(SRC)
            stats = self.run_hook(d)
            out = open(os.path.join(d, 'src', 'a.cpp')).read()
            hdr = open(os.path.join(d, 'src', 'a_decls.h')).read()
            self.assertEqual(out.count('\n'), SRC.count('\n'))
            self.assertEqual(hdr.count('\n'), HDR.count('\n'))
            self.assertIn('if (b->Flags().FUN_00402000(2))', out)
            self.assertIn('if (!b->Flags(n++))', out)                           # side effect: stays
            self.assertIn('if (((Buf*)b)->Flags().FUN_00402000(0x80000000))', out)
            self.assertIn('Use(Get(a), f);', out)                               # nested: the float moves
            self.assertIn('t->Active()->Activate(p);', out)                     # chain
            self.assertIn('t->Pair()->Two(p, p);', out)                         # a type back-reference moves
            self.assertIn('if (b->Flags().FUN_00402000(4))\n        b->Flags(n);', out)   # not a chain to Flags(n)
            self.assertIn('if (b->Flags().FUN_00402000(8))', out)               # explicit operator bool
            self.assertIn('void Clash::Run(int a, float f)\n{\n    Use(Get(a, f));', out)   # overload clash
            self.assertIn('bool FUN_00402000(unsigned int) const;', hdr)
            self.assertIn('FormMask Flags();', hdr)
            self.assertIn('bool FUN_00402000(int) const;', out)                 # the namespace's own class
            self.assertIn('FormMask Flags();', out)
            self.assertIn('int Get(int);', out)
            self.assertIn('void Use(int, float);', out)
            self.assertIn('Cell* Active();', out)
            self.assertIn('void Activate(void* p);', out)
            self.assertEqual(stats['POP2'], 7)
            self.assertGreaterEqual(stats['POP2-SKIP-moved-argument-with-side-effects'], 1)
            self.assertGreaterEqual(stats['POP2-SKIP-overload-clash'], 1)
            syms = json.load(open(os.path.join(d, 'build', 'units.json')))['units'][0]['symbols']
            new = {'?Flags@Buf@@QAE?AUFormMask@@XZ': '0x00401000',
                   '?FUN_00402000@FormMask@@QBE_NI@Z': '0x00402000',
                   '?Flags@Buf@fnv_00403100@@QAE?AUFormMask@2@XZ': '0x00401000',
                   '?FUN_00402000@FormMask@fnv_00403100@@QBE_NH@Z': '0x00402000',
                   '?Get@H@@QAEHH@Z': '0x00404000', '?Use@H@@QAEXHM@Z': '0x00405000',
                   '?Active@Tes@@QAEPAUCell@@XZ': '0x00404100', '?Activate@Cell@@QAEXPAX@Z': '0x00405100',
                   '?Pair@Tes@@QAEPAUCell@@XZ': '0x00404200', '?Two@Cell@@QAEXPAXPAX@Z': '0x00405200'}
            for k, v in new.items():
                self.assertEqual(syms.get(k), v, k)
            self.assertNotIn('?Use@Clash@@QAEXHM@Z', syms)
            for k in new:
                dem = undname(k)
                if dem is not None:
                    self.assertNotIn('invalid', dem.lower(), k)
                    self.assertIn('(', dem, k)
            r = syntax_ok(os.path.join(d, 'src', 'a.cpp'), os.path.join(d, 'src'))
            if r is not None:
                self.assertTrue(r[0], r[1])
            rows = open(os.path.join(d, 'build', 'pop2_sites.tsv')).read()
            self.assertIn('src/a.cpp\t00403200\tGet\tUse\t00404000\t00405000\tnested\t1', rows)

    def test_inline_stand_in_receiver(self):
        """A receiver declared with an inline body (a unit-local copy's stand-in) gets the declaration-only overload
        and the call names it; the stand-in stays. A body with its own @fnv tag stays skipped."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            json.dump(UNITS_INLINE, open(os.path.join(d, 'build', 'units.json'), 'w'))
            open(os.path.join(d, 'src', 'c.cpp'), 'w').write(SRC_INLINE)
            stats = self.run_hook(d)
            out = open(os.path.join(d, 'src', 'c.cpp')).read()
            self.assertEqual(out.count('\n'), SRC_INLINE.count('\n'))
            self.assertIn('if (b->Flags().FUN_00402000(0x80000000))', out)
            self.assertIn('bool FUN_00402000(unsigned int) const;', out)
            self.assertIn('return m != 0;', out)                                 # the stand-in stays
            self.assertNotIn('inline bool FUN_', out)
            self.assertIn('t->Active()->Activate(p);', out)                      # a named receiver, inline body
            self.assertIn('void Activate(void* p);', out)
            self.assertIn('if (b->Flags(8))', out)                               # tagged body: skipped
            self.assertEqual(stats['POP2'], 2)
            self.assertEqual(stats['POP2-SKIP-R-declaration-inline-body-no'], 1)
            syms = json.load(open(os.path.join(d, 'build', 'units.json')))['units'][0]['symbols']
            self.assertEqual(syms.get('?FUN_00402000@FormMask@fnv_00403500@@QBE_NI@Z'), '0x00402000')
            r = syntax_ok(os.path.join(d, 'src', 'c.cpp'), os.path.join(d, 'src'))
            if r is not None:
                self.assertTrue(r[0], r[1])

    def test_idempotent_on_patched_text(self):
        """A second run finds nothing: the moved calls no longer push more than their definitions pop."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'build'))
            os.makedirs(os.path.join(d, 'src'))
            json.dump(UNITS, open(os.path.join(d, 'build', 'units.json'), 'w'))
            open(os.path.join(d, 'src', 'a_decls.h'), 'w').write(HDR)
            open(os.path.join(d, 'src', 'a.cpp'), 'w').write(SRC)
            self.run_hook(d)
            once = open(os.path.join(d, 'src', 'a.cpp')).read()
            stats = self.run_hook(d)
            self.assertEqual(open(os.path.join(d, 'src', 'a.cpp')).read(), once)
            self.assertEqual(stats['POP2'], 0)


if __name__ == '__main__':
    unittest.main()
