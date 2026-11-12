#!/usr/bin/env python3
"""tests/s2d - POP2 (tools/rules/s2_bodies.py, a prepare POST hook) on hand-written units (no decomp content).

    python3 -I rules/tests/test_pop2.py

POP2: a getter whose reference pushes a word its definition does not pop, followed by a call that pops one word
more than its reference pushes, gets the word moved: `b->Flags(2)` used as a bool -> `b->Flags().FUN_<a>(2)`,
`Use(Get(a, f))` -> `Use(Get(a), f)`, `D(x)->R()` -> `D()->R(x)`; the overloads are declared in the class bodies
MSVC's names place (a header, a per-function namespace) and the new decorated names go to units.json. Side effects
in the moved argument, an overload clash, a result that is not read as a bool and a chained receiver with
arguments of its own leave the call alone. Line numbers stay.
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
import core as prepare  # noqa: E402
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
// @fnv 0x00401100
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
namespace fnv_00401140 {
struct FormMask
{
    int m;
    operator bool() const;
};
struct Buf
{
    FormMask Flags(int);
};
// @fnv 0x00401140
void G(Buf* b)
{
    if (((Buf*)b)->Flags(0x80000000))
        g();
}
}  // namespace fnv_00401140
struct H
{
    int Get(int, float);
    void Use(int);
    void Run(int a, float f);
};
// @fnv 0x00401180
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
// @fnv 0x004011c0
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
// @fnv 0x00401200
void Clash::Run(int a, float f)
{
    Use(Get(a, f));
}
'''

UNITS = {'units': [
    {'source': 'src/a.cpp', 'flags': ['/Od'], 'functions': [
        {'symbol': '?F@@YAXPAUBuf@@H@Z', 'address': '0x00401100'}],
     'symbols': {
         '?Flags@Buf@@QAE?AUFormMask@@I@Z': '0x00401040', '??BFormMask@@QBE_NXZ': '0x004010c0',
         '?Flags@Buf@fnv_00401140@@QAE?AUFormMask@2@H@Z': '0x00401040',
         '??BFormMask@fnv_00401140@@QBE_NXZ': '0x004010c0',
         '?Get@H@@QAEHHM@Z': '0x00401340', '?Use@H@@QAEXH@Z': '0x00401400',
         '?Active@Tes@@QAEPAUCell@@PAX@Z': '0x00401380', '?Activate@Cell@@QAEXXZ': '0x00401440',
         '?Pair@Tes@@QAEPAUCell@@PAX0@Z': '0x004013c0', '?Two@Cell@@QAEXXZ': '0x00401480',
         '?Get@Clash@@QAEHHM@Z': '0x00401340', '?Use@Clash@@QAEXH@Z': '0x00401400'}},
    {'source': 'src/b.cpp', 'flags': ['/Od'], 'symbols': {}, 'functions': [
        {'symbol': '?FUN_00401040@Owner@@QAE?AUColor@@XZ', 'address': '0x00401040'},      # pops its buffer only
        {'symbol': '?FUN_004010c0@T@@QBE_NH@Z', 'address': '0x004010c0'},                 # pops the flags
        {'symbol': '?FUN_00401340@X@@QAEHH@Z', 'address': '0x00401340'},                  # pops one word
        {'symbol': '?FUN_00401400@Y@@QAEXHM@Z', 'address': '0x00401400'},                 # pops two
        {'symbol': '?FUN_00401380@X@@QAEPAXXZ', 'address': '0x00401380'},
        {'symbol': '?FUN_00401440@Y@@QAEXPAX@Z', 'address': '0x00401440'},
        {'symbol': '?FUN_004013c0@X@@QAEPAXXZ', 'address': '0x004013c0'},
        {'symbol': '?FUN_00401480@Y@@QAEXPAXPAX@Z', 'address': '0x00401480'}]},
]}

SRC_INLINE = '''#define __forceinline inline
void g();
namespace fnv_00401240 {
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
// @fnv 0x00401240
void F(Buf* b)
{
    if (b->Flags(0x80000000))
        g();
}
}  // namespace fnv_00401240
struct Cell
{
    void Activate() {}
};
struct Tes
{
    Cell* Active(void* p);
};
// @fnv 0x00401280
void K(Tes* t, void* p)
{
    t->Active(p)->Activate();
}
namespace fnv_004012c0 {
struct FormMask
{
    unsigned int m;
    // @fnv 0x004010c0
    operator bool() const
    {
        return m != 0;
    }
};
struct Buf
{
    FormMask Flags(unsigned int) const;
};
// @fnv 0x004012c0
void F(Buf* b)
{
    if (b->Flags(8))
        g();
}
}  // namespace fnv_004012c0
struct Ent01
{
    int Flags();
};
namespace fnv_00401300 {
namespace N {
struct FormMask
{
    unsigned int m;
    operator bool() const;
};
struct Buf
{
    FormMask Flags(unsigned int);
};
}
// @fnv 0x00401300
void F(N::Buf* b)
{
    if (((N::Buf*)b)->Flags(0x8000020))
        g();
    if (reinterpret_cast<N::Buf*>(b)->Flags(3))
        g();
}
}  // namespace fnv_00401300
'''

UNITS_INLINE = {'units': [
    {'source': 'src/c.cpp', 'flags': ['/Od'], 'functions': [], 'symbols': {
        '?Flags@Buf@fnv_00401240@@QBE?AUFormMask@2@I@Z': '0x00401040',
        '??BFormMask@fnv_00401240@@QBE_NXZ': '0x004010c0',
        '?Flags@Buf@fnv_004012c0@@QBE?AUFormMask@2@I@Z': '0x00401040',
        '??BFormMask@fnv_004012c0@@QBE_NXZ': '0x004010c0',
        '?Flags@Ent01@@QAEHXZ': '0x00401080',
        '?Flags@Buf@N@fnv_00401300@@QAE?AUFormMask@23@I@Z': '0x00401040',
        '??BFormMask@N@fnv_00401300@@QBE_NXZ': '0x004010c0',
        '?Active@Tes@@QAEPAUCell@@PAX@Z': '0x00401380', '?Activate@Cell@@QAEXXZ': '0x00401440'}},
    UNITS['units'][1],
]}


class MsvcSig(unittest.TestCase):
    def test_words(self):
        f = _msvcsig.parse('?Ent02@Ent03@@QAE?AUFormChangeMask@@I@Z')
        self.assertEqual((f.ident, f.scopes, f.ret_class, _msvcsig.stack_words(f)),
                         ('Ent02', ['Ent03'], ['FormChangeMask'], 2))   # buffer + flags
        f = _msvcsig.parse('?FUN_004014c0@Owner@s428110@@QAE?AUColor@2@XZ')    # name back-reference
        self.assertEqual((f.ret_class, _msvcsig.stack_words(f)), (['Color', 's428110'], 1))
        f = _msvcsig.parse('??BFormChangeMask@fnv_00401500@@QBE_NXZ')
        self.assertEqual((f.special, f.const, _msvcsig.stack_words(f)), ('operator bool', True, 0))
        f = _msvcsig.parse('?FUN_00401540@@YIHPAVTESDataHandler@@@Z')           # fastcall: the pointer in ecx
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
            self.assertIn('if (b->Flags().FUN_004010c0(2))', out)
            self.assertIn('if (!b->Flags(n++))', out)                           # side effect: stays
            self.assertIn('if (((Buf*)b)->Flags().FUN_004010c0(0x80000000))', out)
            self.assertIn('Use(Get(a), f);', out)                               # nested: the float moves
            self.assertIn('t->Active()->Activate(p);', out)                     # chain
            self.assertIn('t->Pair()->Two(p, p);', out)                         # a type back-reference moves
            self.assertIn('if (b->Flags().FUN_004010c0(4))\n        b->Flags(n);', out)   # not a chain to Flags(n)
            self.assertIn('if (b->Flags().FUN_004010c0(8))', out)               # explicit operator bool
            self.assertIn('void Clash::Run(int a, float f)\n{\n    Use(Get(a, f));', out)   # overload clash
            self.assertIn('bool FUN_004010c0(unsigned int) const;', hdr)
            self.assertIn('FormMask Flags();', hdr)
            self.assertIn('bool FUN_004010c0(int) const;', out)                 # the namespace's own class
            self.assertIn('FormMask Flags();', out)
            self.assertIn('int Get(int);', out)
            self.assertIn('void Use(int, float);', out)
            self.assertIn('Cell* Active();', out)
            self.assertIn('void Activate(void* p);', out)
            self.assertEqual(stats['POP2'], 7)
            self.assertGreaterEqual(stats['POP2-SKIP-moved-argument-with-side-effects'], 1)
            self.assertGreaterEqual(stats['POP2-SKIP-overload-clash'], 1)
            syms = json.load(open(os.path.join(d, 'build', 'units.json')))['units'][0]['symbols']
            new = {'?Flags@Buf@@QAE?AUFormMask@@XZ': '0x00401040',
                   '?FUN_004010c0@FormMask@@QBE_NI@Z': '0x004010c0',
                   '?Flags@Buf@fnv_00401140@@QAE?AUFormMask@2@XZ': '0x00401040',
                   '?FUN_004010c0@FormMask@fnv_00401140@@QBE_NH@Z': '0x004010c0',
                   '?Get@H@@QAEHH@Z': '0x00401340', '?Use@H@@QAEXHM@Z': '0x00401400',
                   '?Active@Tes@@QAEPAUCell@@XZ': '0x00401380', '?Activate@Cell@@QAEXPAX@Z': '0x00401440',
                   '?Pair@Tes@@QAEPAUCell@@XZ': '0x004013c0', '?Two@Cell@@QAEXPAXPAX@Z': '0x00401480'}
            for k, v in new.items():
                self.assertEqual(syms.get(k), v, k)
            self.assertNotIn('?Use@Clash@@QAEXHM@Z', syms)
            rows = open(os.path.join(d, 'build', 'pop2_sites.tsv')).read()
            self.assertIn('src/a.cpp\t00401180\tGet\tUse\t00401340\t00401400\tnested\t1', rows)

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
            self.assertIn('if (b->Flags().FUN_004010c0(0x80000000))', out)
            self.assertIn('bool FUN_004010c0(unsigned int) const;', out)
            self.assertIn('return m != 0;', out)                                 # the stand-in stays
            self.assertNotIn('inline bool FUN_', out)
            self.assertIn('t->Active()->Activate(p);', out)                      # a named receiver, inline body
            self.assertIn('void Activate(void* p);', out)
            self.assertIn('if (b->Flags(8))', out)                               # tagged body: skipped
            self.assertIn('if (((N::Buf*)b)->Flags().FUN_004010c0(0x8000020))', out)    # a cast names the class
            self.assertIn('if (reinterpret_cast<N::Buf*>(b)->Flags().FUN_004010c0(3))', out)
            self.assertEqual(stats['POP2'], 4)
            self.assertEqual(stats['POP2-SKIP-R-declaration-inline-body-no'], 1)
            syms = json.load(open(os.path.join(d, 'build', 'units.json')))['units'][0]['symbols']
            self.assertEqual(syms.get('?FUN_004010c0@FormMask@fnv_00401240@@QBE_NI@Z'), '0x004010c0')

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
