"""sysspec_gap (BSWAP, XLAT, MOV from Sreg, BTS) and sysspec_int (INT n): claimed by decoding, proved against the SDM spec under the declared record (the tree's x86sys.json); an undecided row is `uncertified: <why>`; a wrong effect is a difference."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
import z3
from fnvlift import inputs
from proof import shape, sysspec, symform, x86sys
import sysspec_gap


def key(h):
    return shape.shape_key(next(iter(shape.decoder().disasm(bytes.fromhex(h), 0))))


class Declared(unittest.TestCase):
    def setUp(self):
        declared_x86sys.configure()
        global sysspec_int
        import sysspec_int           # reads fnvlift.x86sys at import: the record is configured first
    def tearDown(self): inputs.reset()

    def test_bswap_every_register_proved(self):
        for h in ('0fc8', '0fc9', '0fca', '0fcb', '0fcc', '0fcd', '0fce', '0fcf'):
            self.assertEqual(sysspec.check(key(h))[0], 'proved', h)

    def test_bswap_16_bit_is_uncertified(self):
        r, n = sysspec.check(key('660fc8')); self.assertEqual(r, 'uncertified'); self.assertIn('undefined', n)

    def test_xlat_proved_and_prefixed_forms_are_not(self):
        self.assertEqual(sysspec.check(key('d7'))[0], 'proved')
        for h in ('67d7', '2ed7'): self.assertEqual(sysspec.check(key(h))[0], 'uncertified', h)

    def test_bts_register_and_memory_forms(self):
        for h in ('0fab0424', '0fabc8', '0fba2c2405', '0fbae805'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'proved', (h, n))

    def test_mov_from_sreg_to_register_and_memory(self):
        for h in ('8cc0', '8c0424', '668c0424', '8c6500', '8cd8'):
            r, n = sysspec.check(key(h)); self.assertIn(r, ('proved', 'uncertified'), (h, n))
        self.assertEqual(sysspec.check(key('8c0424'))[0], 'proved')

    def test_a_wrong_effect_is_a_difference(self):
        ins = sysspec_gap.decode(key('0fc8')); f = symform.symbuild(key('0fc8')).form
        d = sysspec_gap.compare(f, ins, {'eax': z3.BitVec('eax', 32)}, {}, None)
        self.assertIn('eax', d)

    def test_int_n_the_record_vectors_the_other_vectors_and_the_excluded_one(self):
        self.assertEqual(sysspec_int.row(3)[0], 'gate'); self.assertEqual(sysspec_int.row(0x80)[0], 'other'); self.assertEqual(sysspec_int.row(0x29)[0], 'excluded')
        self.assertTrue(sysspec_int.claims('cd00/1.1.0.0')); self.assertFalse(sysspec_int.claims('cc/0.0.0.0'))
        r, n = sysspec.check('cd00/1.1.0.0')
        self.assertEqual(r, 'uncertified'); self.assertIn('int_ring3.excluded.41', n); self.assertIn('255 of 256', n)

    def test_int_n_concrete_vectors_are_proved(self):
        for v in (3, 4, 45, 0, 0x80, 0xff): self.assertEqual(sysspec_int.check('cd%02x/0.0.0.0' % v)[0], 'proved', v)
        self.assertEqual(sysspec_int.check('cd29/0.0.0.0')[0], 'uncertified')

    def test_cites_check_against_the_pinned_sdm(self):
        sdm = os.environ.get('FNVNX_SDM_TXT', '/tmp/sdmdl/sdm.txt')
        if not os.path.exists(sdm): self.skipTest('pinned SDM text not present')
        import subprocess
        for n in ('gap', 'int'):
            r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'sdm_cite_check.py'), sdm, os.path.join(HERE, '..', 'sysspec_%s.py' % n), os.path.join(HERE, '..', 'sysspec_%s_pages.tsv' % n)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)

    def test_page_tables_are_the_generated_ones(self):
        sdm = os.environ.get('FNVNX_SDM_TXT', '/tmp/sdmdl/sdm.txt')
        if not os.path.exists(sdm): self.skipTest('pinned SDM text not present')
        import subprocess
        for n in ('gap', 'int'):
            r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'sdm_pages.py'), sdm, os.path.join(HERE, '..', 'sysspec_%s.py' % n)], capture_output=True, text=True)
            self.assertEqual(r.stdout, open(os.path.join(HERE, '..', 'sysspec_%s_pages.tsv' % n)).read())


class Undecided(unittest.TestCase):
    def test_undecided_selector_row_is_uncertified_with_its_why(self):
        with open(declared_x86sys.PATH) as fh: rec = json.load(fh)
        rec['selectors']['es'] = {'status': 'undecided', 'why': 'es blocker'}
        f = tempfile.NamedTemporaryFile('w', suffix='.json', delete=False); json.dump(rec, f); f.close()
        try:
            inputs.reset(); inputs.configure(x86sys=f.name)
            r, n = sysspec.check(key('8c0424')); self.assertEqual(r, 'uncertified'); self.assertIn('es blocker', n)
        finally: os.unlink(f.name); inputs.reset()


if __name__ == '__main__':
    unittest.main()
