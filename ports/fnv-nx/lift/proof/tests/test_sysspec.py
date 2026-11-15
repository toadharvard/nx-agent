"""sysspec: class (a)/(b) shapes prove against the SDM spec under a record; an undecided row gives `uncertified: <why>`; a conditional fault on a privileged instruction fails.
The record here is a synthetic fixture of the structure (fnvnx-x86sys/2); no value in it is a claim about the declared machine."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import shape, sysspec, x86sys


def key(h):
    return shape.shape_key(next(iter(shape.decoder().disasm(bytes.fromhex(h), 0))))


def row(v): return {'value': v, 'source': 'fixture'}


REC = {'format': 'fnvnx-x86sys/2', 'selectors': {'cs': row(0x1b), 'tr': row(0x28)}, 'eflags': {'iopl': row(0)}, 'cr4': {'umip': row(0), 'pce': {'status': 'undecided', 'why': 'pce blocker'}},
       'int_ring3': {'vectors': [3], 'other_vectors': {'status': 'undecided', 'why': 'other vectors blocker'}}, 'mov_to_sreg': {'undecided': {'selectors': [27], 'why': 'sreg blocker'}},
       'sgdt_base': {'status': 'undecided', 'why': 'gdt blocker'}}


class T(unittest.TestCase):
    def setUp(self):
        self.f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False); json.dump(REC, self.f); self.f.close()
        self.old = os.environ.get('FNVNX_X86SYS'); os.environ['FNVNX_X86SYS'] = self.f.name

    def tearDown(self):
        os.unlink(self.f.name)
        if self.old is None: del os.environ['FNVNX_X86SYS']
        else: os.environ['FNVNX_X86SYS'] = self.old

    def test_classes(self):
        for h in ('fa', 'f4', 'cc', 'ce', 'f1'):
            self.assertEqual(sysspec.check(key(h + '00000000'))[0], 'proved', h)

    def test_undecided_rows_are_uncertified_with_the_blocker(self):
        for h, why in (('cd2e', 'other vectors blocker'), ('8ed8', 'sreg blocker'), ('0f0100', 'gdt blocker'), ('0f33', 'pce blocker'), ('ec', 'no row tss_io_bitmap')):
            r, n = sysspec.check(key(h + '00000000'))
            self.assertEqual(r, 'uncertified', h); self.assertIn(why, n, h)

    def test_absent_record_is_uncertified(self):
        os.environ['FNVNX_X86SYS'] = self.f.name + '.absent'
        r, n = sysspec.check(key('fa'))
        self.assertEqual((r, n.startswith('uncertified: record absent')), ('uncertified', True))

    def test_uncovered_is_not_proved(self):
        self.assertEqual(sysspec.check(key('0fa2'))[0], 'not-covered')

    def test_reader(self):
        self.assertEqual(x86sys.cpl(REC), 3)
        with self.assertRaises(x86sys.Undecided): x86sys.get(REC, 'cr4', 'pce')
        with self.assertRaises(x86sys.Undecided): x86sys.get(REC, 'nope')
        self.assertEqual(x86sys.get(REC, 'int_ring3', 'vectors'), [3])


if __name__ == '__main__':
    unittest.main()
