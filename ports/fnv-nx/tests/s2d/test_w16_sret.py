#!/usr/bin/env python3
"""tests/s2d - W16's by-value-versus-out-pointer fixes for the callee 0x00dba6a0 (hand-written, no decomp content).

    python3 -I tests/s2d/test_w16_sret.py

* dbug1_w16_handle_sret2.s1b has the two sites (0x00dc5440 DCIterator::SetShiftedValue, 0x00dc85a0
  hkDataClassX::getParent), each with x86 evidence, balanced hunks, MSVC text kept under `#ifdef _MSC_VER`; the two
  reference renames are in units_renames.tsv.
* All four users of 0x00dba6a0 are covered (hkdataobject.cpp defines it; the tagfile writer is W12's patch).
* The by-value-member vcall candidates need no patch: the only callee with a body in the tree is the one W12 fixed
  (the scan is a farm/offline job; this test only pins the one patched site).
"""
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PD = os.path.join(HERE, '..', '..', 'patches')


class W16(unittest.TestCase):
    def test_patch_file(self):
        t = open(os.path.join(PD, 'dbug1_w16_handle_sret2.s1b')).read()
        for a in ('0x00dc5440', '0x00dc85a0'):
            m = re.search(r'^@@ DBUG1 (\S+) %s\n((?:#.*\n)+)<<<' % a, t, re.M)
            self.assertIsNotNone(m, a)
            self.assertIn('x86', m.group(2))
            self.assertIn('0x00dba6a0', m.group(2))
        self.assertEqual(t.count('<<<'), 4)
        self.assertEqual(t.count('<<<'), t.count('>>>'))
        self.assertEqual(t.count('#ifdef _MSC_VER'), 4)

    def test_renames(self):
        ren = open(os.path.join(PD, 'units_renames.tsv')).read()
        for old, new in (('?SetShiftedValue@DCIterator@@QAE?AU1@XZ', '?SetShiftedValue@DCIterator@@QAEPAXPAX@Z'),
                         ('?getParent@hkDataClassX@@QBE?AV1@XZ', '?getParent@hkDataClassX@@QBEPAXPAX@Z')):
            self.assertRegex(ren, r'\tref\t0x00dba6a0\t%s\t%s\n' % (re.escape(old), re.escape(new)))


if __name__ == '__main__':
    unittest.main()
