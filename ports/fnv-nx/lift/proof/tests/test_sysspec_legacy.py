"""sysspec_legacy (DAA DAS AAA AAS AAM AAD, SALC, SLDT, LES / LDS, far RET) and the rows the declared record does not decide (SGDT, LAR, IRETD): claimed by decoding, proved against the SDM Operation under the declared record;
an undecided row is `uncertified: <why>`; a wrong effect is a difference; the cleared-#UD encodings (intud.CLEARED_UD) are the encodings the SDM opcode map gives no cell, found by rule from the pinned cells."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
from fnvnx.lift.fnvlift import inputs
from fnvnx.lift.proof import forms, intud, shape, sysspec, symform


def key(h):
    return shape.shape_key(next(iter(shape.decoder().disasm(bytes.fromhex(h), 0))))


class Legacy(unittest.TestCase):
    def setUp(self): declared_x86sys.configure()
    def tearDown(self): inputs.reset()

    def test_bcd_adjusts_are_proved_with_every_flag_of_the_operation(self):
        for h in ('27', '2f', '37', '3f', 'd40a', 'd400', 'd50a', 'd5ff', 'd4ff', '2e27', '3e3f'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'proved', (h, n))

    def test_aam_by_zero_is_the_divide_fault(self):
        f = symform.symbuild(key('d400')).form
        self.assertEqual([k for k, _ in f.faults], [1])      # #DE, the fault of DIV by zero (fnvlift/faults.json DE0)

    def test_operand_size_and_lock_rep_prefixes_are_uncertified(self):
        for h in ('6627', 'f227', 'f3d6'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'uncertified', (h, n))

    def test_salc_sldt_les_lds(self):
        for h in ('d6', '2ed6', '0f0000', '0f000424', '0f00c0', 'c417', 'c51b', 'c44000', 'c59800000000'):
            r, n = sysspec.check(key(h)); self.assertIn(r, ('proved', 'uncertified'), (h, n))
        for h in ('d6', '0f0000', 'c417', 'c51b'): self.assertEqual(sysspec.check(key(h))[0], 'proved', h)

    def test_far_return_is_proved_over_its_domain_and_uncertified_outside_it(self):
        for h in ('cb', 'ca0000', '66cb'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'uncertified', (h, n)); self.assertIn('proved over its domain', n); self.assertIn('rpl', n)

    def test_rows_the_record_does_not_decide_are_uncertified_with_the_row(self):
        r, n = sysspec.check(key('0f0100')); self.assertEqual(r, 'uncertified'); self.assertIn('sgdt.base', n)

    def test_a_wrong_effect_is_a_difference(self):
        k = key('3ed6')      # SALC with a segment prefix: not used elsewhere, so the Form cache holds no other
        symform.symbuild(key('d6')); good = forms.H['salc']      # the first build registers the handlers
        def bad(c):
            from fnvnx.lift.fnvlift.dsl import C
            good(c); c.F.reg('eax', C(0, 32))
        forms.H['salc'] = bad
        try:
            symform.symbuild.cache_clear() if hasattr(symform.symbuild, 'cache_clear') else None
            r, n = sysspec.check(k); self.assertEqual(r, 'FAILED(dsl)', n)
        finally: forms.H['salc'] = good

    def test_cleared_ud_encodings_are_a_fault_whatever_the_mnemonic(self):
        for h in ('d0f0', 'd1f0', 'd2f0', 'd3f0', 'c0f001', 'c1f001', 'c1750001', 'f6c801', 'f7c801000000', 'f74c000000000000'):
            f = symform.symbuild(key(h)).form
            self.assertEqual([k for k, _ in f.faults], [3], h); self.assertFalse(f.regs or f.stores or f.flags, h)
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'uncertified', h); self.assertIn('cleared-#UD', n)
        for h in ('d0e0', 'd1e0', 'f6c001', 'f7d0', 'f6d8'):      # /4 SHL/SAL, /0 TEST, /2 NOT, /3 NEG are cells
            self.assertNotIn(3, [k for k, _ in symform.symbuild(key(h)).form.faults], h)


class ClearedUniverse(unittest.TestCase):
    def test_every_decoded_encoding_without_an_sdm_cell_is_in_the_class_and_has_the_evidence(self):
        """by rule from the committed cells of the pinned SDM (opmap_cells.tsv): a (opcode, ModRM.reg) of a one-byte group with an empty cell in every row of that opcode and that capstone decodes is exactly CLEARED_UD"""
        rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(HERE, 'opmap_cells.tsv'), encoding='utf-8') if l.startswith('group\t-\t')]
        cell = {}
        for _, _tb, ops, _g, pfx, _pi, regs in rows:
            if pfx != '-': continue
            for op in ops.split(','):
                if int(op, 16) in (0x66, 0x67, 0x2e, 0x36, 0x3e, 0x26, 0x64, 0x65, 0xf0, 0xf2, 0xf3): continue      # a prefix byte: the VEX group rows that the cells file lists under F3 are no one-byte group
                for reg, c in enumerate(regs.split(' | ')): cell.setdefault((int(op, 16), reg), []).append(bool(c.strip()))
        empty = sorted(k for k, v in cell.items() if not any(v))
        dec = shape.decoder(); found = set()
        for op, reg in empty:
            for mod in (0x00, 0xc0):
                ins = list(dec.disasm(bytes([op, mod | (reg << 3), 0, 0, 0, 0, 0, 0]), 0))
                if ins and ins[0].size > 0: found.add((op, reg))
        self.assertEqual(found, set(intud.CLEARED_UD))
        for k in intud.CLEARED_UD: self.assertIn(k, empty)


class Cites(unittest.TestCase):
    def test_cites_check_against_the_pinned_sdm(self):
        sdm = os.environ.get('FNVNX_SDM_TXT', '/tmp/sdmdl/sdm.txt')
        if not os.path.exists(sdm): self.skipTest('pinned SDM text not present')
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'sdm_cite_check.py'), sdm, os.path.join(HERE, '..', 'sysspec_legacy.py'), os.path.join(HERE, '..', 'sysspec_legacy_pages.tsv')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_page_table_is_the_generated_one(self):
        sdm = os.environ.get('FNVNX_SDM_TXT', '/tmp/sdmdl/sdm.txt')
        if not os.path.exists(sdm): self.skipTest('pinned SDM text not present')
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'sdm_pages.py'), sdm, os.path.join(HERE, '..', 'sysspec_legacy.py')], capture_output=True, text=True)
        self.assertEqual(r.stdout, open(os.path.join(HERE, '..', 'sysspec_legacy_pages.tsv')).read())


if __name__ == '__main__':
    unittest.main()
