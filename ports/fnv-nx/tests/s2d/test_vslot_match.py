#!/usr/bin/env python3
"""tests/s2d - EQ native-vslot-differs (S2d-vslot): tools/link/vtmetric.py's slot checker and its census of
exe vftables no V row stands for, on hand-made fixtures (no exe, no game content).

    python3 -I tests/s2d/test_vslot_match.py

Checks: slot_address reads an address from the map's rows (link, native and D1 spellings) or from a bound name
(`__fnv_<a>`, `_D0`, flag / deleting-destructor / constant stubs, the MSVC-entry form) and gives None for a trap or a
stand-in; compare_slots compares native slot k + 1 with exe slot k after the destructor pair (the minus-one layout),
skips exe slots 0 and 1, never counts an unplaced slot, and lets the exe decide the alignment when the pair is a
flag-stub pair that stands for two exe slots (a placeholder destructor) instead of a destructor; the TESForm case
(a vtable slot named by another unit's address, here 0x0046f070 where the exe's slot holds 0x00401190) is exactly
one mismatch; unpaired_reason sorts the census by what classes.tsv says (typeinfo only: no polymorphic view; a
vtable with offset > 0: no secondary base; ...).
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'link'))
import vtmetric  # noqa: E402

MAP = ('# fnvnx-addrmap 1\n'
       '# address\tkind\tlink_symbol\tnative_symbol\tobject\tnote\toffset\td1_symbol\td1_native\n'
       '00401190\tF\t__fnv_00401190\t_ZN18FUN_00401190_shell12FUN_00401190Ev\tsrc_a.cpp.o\t\t\t\t\n'
       '0046f070\tF\t__fnv_0046f070\t_ZNK18FUN_0046f070_shell12FUN_0046f070Ev\tsrc_b.cpp.o\t\t\t\t\n'
       '00500000\tG\t__fnv_00500000\t_ZN3FooD0Ev\tsrc_c.cpp.o\t\t\t__fnv_00500000_D1\t_ZN3FooD1Ev\n'
       '0102a31c\tV\t_ZTV3Foo\t_ZTV3Foo\tsrc_c.cpp.o\tprimary\t8\t\t\n'
       '00000000\tE\t\t\t\tno native definition\t\t\t\n')


class SlotAddress(unittest.TestCase):
    def setUp(self):
        with tempfile.NamedTemporaryFile('w', suffix='.tsv', delete=False) as f:
            f.write(MAP)
        self.path = f.name
        self.sa = vtmetric.read_symaddr(self.path)

    def tearDown(self):
        os.unlink(self.path)

    def test_map_rows(self):
        self.assertEqual(self.sa['__fnv_00401190'], 0x401190)
        self.assertEqual(self.sa['_ZNK18FUN_0046f070_shell12FUN_0046f070Ev'], 0x46f070)
        self.assertEqual(self.sa['_ZN3FooD1Ev'], 0x500000)
        self.assertNotIn('_ZTV3Foo', self.sa)   # a V row is no function
        self.assertNotIn('', self.sa)

    def test_names(self):
        sa = self.sa
        f = vtmetric.slot_address
        self.assertEqual(f('__fnv_00401190', sa), 0x401190)
        self.assertEqual(f('_ZN3FooD1Ev', sa), 0x500000)
        self.assertEqual(f('__fnv_00500000_D0', sa), 0x500000)
        self.assertEqual(f('__fnvnx_vflag_00500000_1', sa), 0x500000)
        self.assertEqual(f('__fnvnx_vgdel_00500000', sa), 0x500000)
        self.assertEqual(f('__fnvnx_vconst_004010d0', sa), 0x4010d0)
        self.assertEqual(f('__fnvnx_msvc_00401190', sa), 0x401190)
        self.assertIsNone(f('__fnvnx_vtrap_12345678', sa))
        self.assertIsNone(f('_ZN10NiAVObject4v003Ev', sa))   # a stand-in nothing places
        self.assertIsNone(f(None, sa))


class Compare(unittest.TestCase):
    SA = {}

    def cmp(self, names, d, exe):
        return vtmetric.compare_slots(names, d, exe, self.SA)

    def test_minus_one_layout(self):
        # GCC: D1, D0, a, b, c; MSVC: ??_G, a, b, c. a is exe slot 1 (not compared), b and c agree
        names = ['__fnv_00500000_D1', '__fnv_00500000_D0', '__fnv_00400010', '__fnv_00400020', '__fnv_00400030']
        agree, bad = self.cmp(names, 0, [0x500000, 0x400010, 0x400020, 0x400030])
        self.assertEqual((agree, bad), (2, []))

    def test_tesform_slot(self):
        # native slot 4 (exe slot 3) is named by another unit's address; the exe holds another function there
        names = ['__fnv_00500000_D1', '__fnv_00500000_D0', '__fnv_00400010', '__fnv_00400020', '__fnv_0046f070']
        agree, bad = self.cmp(names, 0, [0x500000, 0x400010, 0x400020, 0x401190])
        self.assertEqual(agree, 1)
        self.assertEqual(bad, [(4, 3, 0x46f070, 0x401190)])

    def test_unplaced_slots_never_count(self):
        names = ['__fnv_00500000_D1', '__fnv_00500000_D0', '__fnvnx_vtrap_00000001', None, '__fnv_00400030']
        agree, bad = self.cmp(names, 0, [0x500000, 0x400010, 0x400020, 0x400030])
        self.assertEqual((agree, bad), (1, []))

    def test_no_destructor(self):
        names = ['__fnv_00400000', '__fnv_00400010', '__fnv_00400020', '__fnv_00400030']
        agree, bad = self.cmp(names, None, [0x400000, 0x400010, 0x400020, 0x400031])
        self.assertEqual((agree, bad), (1, [(3, 3, 0x400030, 0x400031)]))

    def test_flag_pair_that_is_not_a_destructor(self):
        # a placeholder pair of virtuals bound through flag stubs (_0, _1) stands for two exe slots, so no GCC slot
        # is lost: the alignment with no pair agrees with the exe at every slot, the one with a pair at none
        names = ['__fnvnx_vflag_00400000_0', '__fnvnx_vflag_00400000_1', '__fnv_00400020', '__fnv_00400030']
        exe = [0x400000, 0x400004, 0x400020, 0x400030]
        agree, bad = self.cmp(names, None, exe)
        self.assertEqual((agree, bad), (2, []))

    def test_flag_pair_that_is_a_destructor(self):
        # the same spelling where the exe has one slot for the pair: the pair alignment wins
        names = ['__fnvnx_vflag_00400000_0', '__fnvnx_vflag_00400000_1', '__fnv_00400010', '__fnv_00400020',
                 '__fnv_00400030']
        exe = [0x400000, 0x400010, 0x400020, 0x400030]
        agree, bad = self.cmp(names, None, exe)
        self.assertEqual((agree, bad), (2, []))

    def test_pair_with_a_d0_named_second_slot(self):
        # the link binds D1 to a flag stub and leaves the class's own `D0Ev` as it was (no address behind it): the
        # D0 name marks the pair although exevft.dtor_pos (D1 then D0 by name) does not find it
        names = ['__fnvnx_vflag_00400000_0', '_ZN1KD0Ev', '__fnv_00400010', '__fnv_00400020', '__fnv_00400030']
        exe = [0x400000, 0x400010, 0x400020, 0x400030]
        agree, bad = self.cmp(names, None, exe)
        self.assertEqual((agree, bad), (2, []))
        # the same names where the exe really has a slot per entry: no pair agrees more
        exe2 = [0x400000, 0x400001, 0x400010, 0x400020, 0x400030]
        self.assertEqual(self.cmp(names, None, exe2), (3, []))

    def test_pair_of_one_symbol(self):
        # D1 and D0 bound to the one function the exe's single deleting destructor is
        names = ['__fnv_00400000', '__fnv_00400000', '__fnv_00400010', '__fnv_00400020', '__fnv_00400030']
        exe = [0x400000, 0x400010, 0x400020, 0x400030]
        self.assertEqual(self.cmp(names, None, exe), (2, []))

    def test_pair_alignment_still_reports_a_real_mismatch(self):
        names = ['__fnvnx_vflag_00400000_0', '_ZN1KD0Ev', '__fnv_00400010', '__fnv_00400020', '__fnv_00400031']
        exe = [0x400000, 0x400010, 0x400020, 0x400030]
        agree, bad = self.cmp(names, None, exe)
        self.assertEqual((agree, bad), (1, [(4, 3, 0x400031, 0x400030)]))

    def test_flag3_pair(self):
        sa = {}
        self.assertEqual(vtmetric.slot_address('__fnvnx_vflag3_00404000_0', sa), 0x404000)
        names = ['__fnvnx_vflag3_00404000_0', '__fnvnx_vflag3_00404000_1', '__fnv_00404010', '__fnv_00404020',
                 '__fnv_00404030']
        self.assertEqual(self.cmp(names, None, [0x404000, 0x404010, 0x404020, 0x404030]), (2, []))


class Renames(unittest.TestCase):
    def test_vtables_object_of_a_host(self):
        with tempfile.NamedTemporaryFile('w', suffix='.tsv', delete=False) as f:
            f.write('#object\tundefined\tclass\ttier\taddress\ttarget\tnote\n'
                    'src/a.cpp\t_ZN1K1fEv\tcode\tunit/exact\t00401000\t__fnv_00401000\t\n'
                    'src/a.cpp#vtables\t_ZN1KD1Ev\tvslot-unresolved\t\t\t__fnvnx_vtrap_1\t\n')
        try:
            r = vtmetric.read_renames(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(r['src_a.cpp.o'], {'__fnv_00401000': '_ZN1K1fEv'})
        self.assertEqual(r['zz_vt_src_a.cpp.o'], {'__fnv_00401000': '_ZN1K1fEv', '__fnvnx_vtrap_1': '_ZN1KD1Ev'})


class Unpaired(unittest.TestCase):
    CLASSES = {'QuantityMenu': 'typeinfo', 'TESObjectARMA': 'vtable; typeinfo',
               'ExtraCellMusicType': 'no host unit could name the class'}

    def test_reasons(self):
        r = vtmetric.unpaired_reason
        self.assertEqual(r('.?AVQuantityMenu@@', 0, self.CLASSES), 'no-polymorphic-view')
        self.assertEqual(r('.?AVTESObjectARMA@@', 104, self.CLASSES), 'no-secondary-base')
        self.assertEqual(r('.?AVTESObjectARMA@@', 0, self.CLASSES), 'paired-elsewhere')
        self.assertEqual(r('.?AVExtraCellMusicType@@', 0, self.CLASSES), 'not-nameable')
        self.assertEqual(r('.?AV?$NiTMapBase@H@@', 0, self.CLASSES), 'template-or-no-row')
        self.assertEqual(r('.?AVNoSuchClass@@', 0, self.CLASSES), 'template-or-no-row')

    def test_class_rows(self):
        with tempfile.NamedTemporaryFile('w', suffix='.tsv', delete=False) as f:
            f.write('#class\thost_unit\tencoding\tresult\nQuantityMenu\tsrc/m.cpp\t12QuantityMenu\ttypeinfo\n')
        try:
            self.assertEqual(vtmetric.read_class_rows(f.name), {'QuantityMenu': 'typeinfo'})
        finally:
            os.unlink(f.name)
        self.assertEqual(vtmetric.read_class_rows('/nonexistent/classes.tsv'), {})


if __name__ == '__main__':
    unittest.main()
