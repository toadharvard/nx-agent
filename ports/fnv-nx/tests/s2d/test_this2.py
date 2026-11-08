#!/usr/bin/env python3
"""tests/s2d - THIS2 (tools/rules/s2_bodies.py, a prepare POST hook) on a hand-written unit (no decomp content).

    python3 -I tests/s2d/test_this2.py

A free stdcall caller that hands its ecx to a thiscall callee the unit declares free becomes a member of a shell
declared on its head line (its units.json definition renamed, the callee's member name added to `symbols`); a member
caller calls through `this`; a caller that calls the callee twice stays. Line numbers stay; the result compiles
(host g++, -fsyntax-only) when it is there. The sites are the test's own (THIS2_SITES replaced for the run).
"""
import collections
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools'))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'rules'))
import prepare      # noqa: E402
sys.modules.setdefault('prepare', prepare)
import s2_bodies    # noqa: E402
sys.path.insert(0, HERE)
from test_pop2 import syntax_ok   # noqa: E402

THIS_SRC = '''struct StrArr { int n; };
void __stdcall FUN_00406000(StrArr*);
float* __stdcall FUN_00406100(float a, float b);
// @fnv 0x00403500
void __stdcall FUN_00403500(StrArr* p)
{
    FUN_00406000(p);
    p->n = 1;
}
struct Obj
{
    float* Make(float a, float b);
};
// @fnv 0x00403600
float* Obj::Make(float a, float b)
{
    float* r = FUN_00406100(a, b);
    return r;
}
// @fnv 0x00403700
void __stdcall FUN_00403700(StrArr* p)
{
    FUN_00406000(p);
    FUN_00406000(p);
}
'''

THIS_UNITS = [
    {'source': 'src/c.cpp', 'flags': ['/O2'], 'functions': [
        {'symbol': '?FUN_00403500@@YGXPAUStrArr@@@Z', 'address': '0x00403500'},
        {'symbol': '?Make@Obj@@QAEPAMMM@Z', 'address': '0x00403600'},
        {'symbol': '?FUN_00403700@@YGXPAUStrArr@@@Z', 'address': '0x00403700'}],
     'symbols': {'?FUN_00406000@@YGXPAUStrArr@@@Z': '0x00406000', '?FUN_00406100@@YGPAMMM@Z': '0x00406100'}},
    {'source': 'src/d.cpp', 'flags': ['/O2'], 'symbols': {}, 'functions': [
        {'symbol': '?FUN_00406000@Base@@QAEXPAUStrArr@@@Z', 'address': '0x00406000'},
        {'symbol': '?FUN_00406100@Base@@QAEPAXHH@Z', 'address': '0x00406100'}]},
]



class This2(unittest.TestCase):
    def test_members(self):
        saved = dict(s2_bodies.THIS2_SITES)
        s2_bodies.THIS2_SITES.clear()
        s2_bodies.THIS2_SITES.update({0x00403500: 0x00406000, 0x00403600: 0x00406100, 0x00403700: 0x00406000})
        try:
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, 'build'))
                os.makedirs(os.path.join(d, 'src'))
                json.dump({'units': THIS_UNITS}, open(os.path.join(d, 'build', 'units.json'), 'w'))
                open(os.path.join(d, 'src', 'c.cpp'), 'w').write(THIS_SRC)
                stats = collections.Counter()
                s2_bodies.this_members(d, stats, collections.Counter())
                out = open(os.path.join(d, 'src', 'c.cpp')).read()
                self.assertEqual(out.count('\n'), THIS_SRC.count('\n'))
                self.assertIn('struct FUN_00403500_this2 { void FUN_00403500(StrArr* p); void FUN_00406000(StrArr*); '
                              '}; void FUN_00403500_this2::FUN_00403500(StrArr* p)', out)
                self.assertIn('float* r = ((FUN_00403600_this2*)this)->FUN_00406100(a, b);', out)
                self.assertIn('struct FUN_00403600_this2 { float* FUN_00406100(float a, float b); }; ', out)
                self.assertIn('void __stdcall FUN_00403700(StrArr* p)', out)        # called twice: stays
                self.assertEqual(stats['THIS2'], 2)
                self.assertEqual(stats['THIS2-SKIP-callee-called-2-times'], 1)
                u = json.load(open(os.path.join(d, 'build', 'units.json')))['units'][0]
                self.assertEqual(u['functions'][0]['symbol'], '?FUN_00403500@FUN_00403500_this2@@QAEXPAUStrArr@@@Z')
                self.assertEqual(u['functions'][2]['symbol'], '?FUN_00403700@@YGXPAUStrArr@@@Z')
                self.assertEqual(u['symbols'].get('?FUN_00406000@FUN_00403500_this2@@QAEXPAUStrArr@@@Z'),
                                 '0x00406000')
                self.assertEqual(u['symbols'].get('?FUN_00406100@FUN_00403600_this2@@QAEPAMMM@Z'), '0x00406100')
                r = syntax_ok(os.path.join(d, 'src', 'c.cpp'), os.path.join(d, 'src'))
                if r is not None:
                    self.assertTrue(r[0], r[1])
        finally:
            s2_bodies.THIS2_SITES.clear()
            s2_bodies.THIS2_SITES.update(saved)



class TestThis2NamespaceNames(unittest.TestCase):
    """THIS2 in a namespace: the shell enters MSVC's back-reference table after the identifier (0x00c2a960)."""

    def test_backref_shifts(self):
        import _msvcsig
        n = '?FUN_00406000@fnv_00403500@@YGXPAUStrArr@1@@Z'
        got = s2_bodies._this2_member_name(n, _msvcsig.parse(n), 'FUN_00403500_this2')
        self.assertEqual(got, '?FUN_00406000@FUN_00403500_this2@fnv_00403500@@QAEXPAUStrArr@2@@Z')
        g = _msvcsig.parse(got)
        self.assertEqual(g.params, _msvcsig.parse(n).params)

    def test_scope_without_backref(self):
        import _msvcsig
        n = '?FUN_00406000@fnv_00403500@@YGXH@Z'
        self.assertEqual(s2_bodies._this2_member_name(n, _msvcsig.parse(n), 'S'),
                         '?FUN_00406000@S@fnv_00403500@@QAEXH@Z')

    def test_plain_unchanged(self):
        import _msvcsig
        n = '?FUN_00406000@@YGXPAUStrArr@@@Z'
        self.assertEqual(s2_bodies._this2_member_name(n, _msvcsig.parse(n), 'S'), '?FUN_00406000@S@@QAEXPAUStrArr@@@Z')

    def test_member_refused(self):
        import _msvcsig
        n = '?f@C@@QAEXH@Z'
        self.assertIsNone(s2_bodies._this2_member_name(n, _msvcsig.parse(n), 'S'))


if __name__ == '__main__':
    unittest.main()
