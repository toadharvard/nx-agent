#!/usr/bin/env python3
"""LAR / LSL (0F 02, 0F 03, register and memory source, 16- and 32-bit operand) and IRETD at CPL 3: the Forms of the declared record's lar_lsl and iret rows, proved over their domain against the SDM Operation (sysspec_legacy);
the record's own rows are compared with the SDM rule row by row (record_vs_spec, iret_record_diffs), so a wrong row is a difference, never a silent value.  Needs the CPUID table only for the cleared-feature class (not used here).
python3 -I tests/test_sysspec_lar_iret.py"""
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
from fnvlift import dsl, inputs
from proof import shape, symform, sysspec, x86sys
import sysspec_legacy as L


def key(h):
    return shape.shape_key(next(iter(shape.decoder().disasm(bytes.fromhex(h), 0))))


class LarLsl(unittest.TestCase):
    def setUp(self):
        declared_x86sys.configure(); self.rec = x86sys.load()
        global fx
        from fnvlift import x86sys as fx
        self.saved = fx.REC; r = copy.deepcopy(self.rec)      # a record that carries descriptor.accessed (the field L5 has still to add): a test input, one value per descriptor
        for i, d in enumerate(r['lar_lsl']['descriptors'].values()): d['accessed'] = i & 1
        fx.REC = r; self.load = x86sys.load; x86sys.load = lambda p=None: r
    def tearDown(self): fx.REC = self.saved; x86sys.load = self.load; inputs.reset()

    def test_every_shape_is_proved_over_its_domain_and_outside_domain_is_named(self):
        for h in ('0f02c1', '0f0300', '660f02c1', '660f0300', '0f020424', '0f02808000000000', '0f0305aabbccdd', '0f02c6', '660f03d3'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'uncertified', (h, n)); self.assertIn('OutsideDomain', n); self.assertIn('115 GDT indexes', n)

    def test_the_accessed_bit_is_undecided_until_the_record_has_it(self):
        fx.REC = self.saved
        with self.assertRaises(dsl.UndecidedRow) as e: symform.symbuild(key('0f02c1'))
        self.assertIn('descriptor.accessed', str(e.exception))
        symform.symbuild(key('0f03c1'))      # LSL does not read it

    def test_zf_is_the_only_flag_and_the_destination_is_conditional(self):
        for h in ('0f02c1', '0f0300', '660f02c1'):
            f = symform.symbuild(key(h)).form
            self.assertEqual([x[0] for x in f.flags], ['zf']); self.assertIsNone(f.flags[0][2]); self.assertEqual(len(f.regs), 1); self.assertIsNotNone(f.regs[0][2]); self.assertFalse(f.faults or f.stores)

    def test_the_record_agrees_with_the_sdm_rule(self):
        self.assertEqual(L.record_vs_spec(self.rec), [])

    def test_a_wrong_row_is_a_difference_with_its_row(self):
        for mut, want in ((lambda r: r['descriptors']['0x18']['lar'].__setitem__('zf', 0), 'lar 0x18 RPL 0: ZF record 0, SDM 1'),
                          (lambda r: r['descriptors']['0x08']['lsl'].__setitem__('zf', 1), 'lsl 0x08 RPL 0: ZF record 1, SDM 0'),
                          (lambda r: r['descriptors']['0x18']['lsl']['dest'].__setitem__('limit', 0x12345678), 'lsl 0x18 limit 12345678 with G = 1'),
                          (lambda r: r['descriptors']['0x38']['lar']['dest'].__setitem__('value_with_accessed_clear', 0x40f300), 'lar 0x38 value 40f300: type/S/DPL differ from the descriptor (1, 2, 3)'),
                          (lambda r: r['descriptors']['0x20']['lar']['dest'].__setitem__('accessed_mask', 0x200), 'lar 0x20 masks ffff00 f0000 200')):
            rec = copy.deepcopy(self.rec); mut(rec['lar_lsl']); d = L.record_vs_spec(rec)
            self.assertTrue(any(x.startswith(want.split(' value')[0]) for x in d), (want, d))
        rec = copy.deepcopy(self.rec); rec['lar_lsl']['zf1_descriptors'].append('0x70'); self.assertIn("zf1_descriptors: record ['0x18', '0x20', '0x38', '0x40', '0x70'], SDM ['0x18', '0x20', '0x38', '0x40']", L.record_vs_spec(rec))

    def test_a_wrong_form_is_a_difference(self):
        k = key('0f02c1'); f = symform.symbuild(k).form; ins = L.G.decode(k)
        good = L.check_larlsl(ins, f, symform.parse_key(k)[1], fx.REC); self.assertEqual(good[0], 'uncertified')
        g = copy.copy(f); g.flags = []
        self.assertEqual(L.check_larlsl(ins, g, symform.parse_key(k)[1], fx.REC)[0], 'FAILED(dsl)')


class Iret(unittest.TestCase):
    def setUp(self): declared_x86sys.configure(); self.rec = x86sys.load()
    def tearDown(self): inputs.reset()

    def test_the_rows_of_the_record_against_the_sdm(self):
        # the record's loaded_mask has bit 1 (the always-1 bit, row bit1) besides the bits the Operation names: the one difference, reported to the record's owner
        self.assertEqual(L.iret_record_diffs(self.rec), [L.TEXT['m_mask'] % (self.rec['iret']['eflags_restored']['loaded_mask'], 0x254dd5, self.rec['iret']['eflags_restored']['loaded_mask'] ^ 0x254dd5)])
        self.assertEqual(self.rec['iret']['eflags_restored']['loaded_mask'] ^ 0x254dd5, 2)

    def test_the_form_is_proved_over_its_domain_under_the_sdm_bits(self):
        rec = copy.deepcopy(self.rec); rec['iret']['eflags_restored']['loaded_mask'] = 0x254dd5
        for h in ('cf', '2ecf'):
            k = key(h); r, n = L.check(k, rec); self.assertEqual(r, 'uncertified', (h, n)); self.assertIn('NT = 1', n); self.assertIn('OutsideDomain', n)
        self.assertEqual(sysspec.check(key('cf'))[0], 'FAILED(dsl)')      # under the record as shipped: the mask difference

    def test_the_sixteen_bit_iret_is_an_undecided_row(self):
        r, n = sysspec.check(key('66cf')); self.assertEqual(r, 'uncertified'); self.assertIn('operand_size 32 only', n)

    def test_a_wrong_row_is_a_difference(self):
        rec = copy.deepcopy(self.rec); rec['iret']['same_privilege_return']['cs_selector_checks']['returns_to'] = [27, 24]
        self.assertTrue(any(x.startswith('returns_to: record [27, 24]') for x in L.iret_record_diffs(rec)))
        rec = copy.deepcopy(self.rec); rec['iret']['eflags_restored']['loaded_bits'].remove('RF')
        self.assertTrue(any(x.startswith('loaded_bits') for x in L.iret_record_diffs(rec)))

    def test_cs_classes_are_the_sdm_rule_over_the_descriptors(self):
        self.assertEqual(L.iret_cs_valid(self.rec), [0x1b])      # the one code segment of DPL 3 returned to with RPL 3; the kernel code segment (DPL 0, 0x08) has RPL < CPL for every RPL the process can name
        k = key('cf'); f = symform.symbuild(k).form; self.assertEqual([c for c, _ in f.faults], [7]); self.assertEqual(f.stores, [])


if __name__ == '__main__': unittest.main()
