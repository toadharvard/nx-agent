#!/usr/bin/env python3
"""tests/s2d - W12's call-shape fixes (EQ fuzz link 0540aff0d154), on hand-written units (no decomp content).

    python3 -I tests/s2d/test_w12_callshape.py

* The three patch files (dbug1_w12_*, ret1_w12_*) parse, carry evidence, and their renames are in units_renames.tsv.
* The by-value-member shape: GCC calls `o->member.vf()` directly (never reading the vptr); the patched shape (a local
  pointer to the member) reads the vptr and calls the slot. A search function whose index is dropped compiles to a
  bare return; returning it keeps the loop. Both need the aarch64 cross compiler (skipped without it).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..', '..')
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.join(ROOT, 'tools', 'rules'))

CXX = shutil.which('aarch64-linux-gnu-g++') or shutil.which('g++')

BYVALUE = '''struct N;
struct E { virtual void a() {} virtual N* vf14() { return 0; } };
struct O { int u[6]; E embedded; };
extern void L(const char*);
void direct(O* o) { L((const char*)o->embedded.vf14()); }
void viaptr(O* o) { E* e = &o->embedded; L((const char*)e->vf14()); }
'''

SEARCH = '''struct S { int a, b, c; int n_at_0x10; const unsigned char* buf;
  void drop(int v); int keep(int v); };
void S::drop(int v) { int c = n_at_0x10 - 1; while (c >= 0) { if (buf[c] == v) break; --c; } }
int S::keep(int v) { int c = n_at_0x10 - 1; while (c >= 0) { if (buf[c] == v) break; --c; } return c; }
'''


def asm(src):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'x.cpp')
        open(p, 'w').write(src)
        r = subprocess.run([CXX, '-O2', '-S', '-o', '-', p], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout


def body(text, sym_part):
    m = re.search(r'^(_Z\w*%s\w*):\n(.*?)^\.LFE' % sym_part, text, re.S | re.M)
    return m.group(2)


class W12(unittest.TestCase):
    def test_patch_files(self):
        pd = os.path.join(ROOT, 'patches')
        ren = open(os.path.join(pd, 'units_renames.tsv')).read()
        for name, addrs in (('dbug1_w12_byvalue_vcall.s1b', ['0x0081e440']), ('ret1_w12_lastindex.s1b', ['0x00cbc8f0']),
                            ('dbug1_w12_handle_sret.s1b', ['0x00dbf1a0'])):
            t = open(os.path.join(pd, name)).read()
            for a in addrs:
                m = re.search(r'^@@ (DBUG1|RET1) (\S+) %s\n((?:#.*\n)+)<<<' % a, t, re.M)
                self.assertIsNotNone(m, name)
                self.assertIn('x86', m.group(3))
            self.assertEqual(t.count('<<<'), t.count('>>>'))
        self.assertIn('?FUN_00cbc8f0@FUN_00cbc8f0_cls@@QAEHH@Z', ren)
        self.assertIn('?SetShiftedValue@S_dbf1a0_Handle@@QBEPAXPAX@Z', ren)

    @unittest.skipUnless(CXX, 'no g++')
    def test_byvalue_member_vcall(self):
        t = asm(BYVALUE)
        self.assertNotRegex(body(t, '6direct'), r'\bblr\b|\bcall\b')            # devirtualized: the bug
        self.assertRegex(body(t, '6viaptr'), r'\bblr\b|\bcall\b')               # the patched shape reads the vptr

    @unittest.skipUnless(CXX, 'no g++')
    def test_search_result_kept(self):
        t = asm(SEARCH)
        self.assertEqual(len([l for l in body(t, '4drop').splitlines() if l.strip() and not l.startswith(('.', '\t.'))
                              and not l.strip().startswith('.')]), 1)           # a bare ret
        self.assertGreater(len(body(t, '4keep').splitlines()), 4)


if __name__ == '__main__':
    unittest.main()
