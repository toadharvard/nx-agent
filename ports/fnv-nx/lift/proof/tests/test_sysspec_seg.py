"""sysspec_seg: MOV Sreg, POP Sreg, PUSH Sreg Forms against the cited SDM spec under the declared record; the segment-register write effect (dsl kind sel) and its C emission."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
declared_x86sys.configure()      # fnvlift.x86sys reads the record at import
import capstone
import z3
from fnvnx.lift.fnvlift import core, dsl, inputs
from fnvnx.lift.proof import lift_dsl, sysspec, x86sys

A0 = 0x400000


def key(h): return h + '/0.0.0.0'


def emit(hx):
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md.detail = True
    i = next(md.disasm(bytes.fromhex(hx), A0))
    cx = core.Ctx(A0, i.size, {A0: i}, {}, [])
    cx.start, cx.a, cx.nxt, cx.m, cx.pfx, cx.ops, cx.term, cx.xm, cx.xdp = A0, A0, A0 + i.size, i.mnemonic, '', i.operands, False, None, 0
    cx.uent, cx.b0, cx.modrm, cx.s, cx.macc = set(), 1, i.modrm, 32, 'M'
    lift_dsl.emit_form(cx, i)
    return '\n'.join(cx.L)


class Seg(unittest.TestCase):
    def setUp(self): declared_x86sys.configure()
    def tearDown(self): inputs.reset()

    def test_every_segment_register_is_proved(self):
        for h in ('8ed0', '8e5000', '17', '8ec8', '8e08', '16', '0e', '6616', '06', '1e', '8cd8', '668c0424', '8ec0', '8ed8', '8ee0', '8ee8', '07', '1f', '8e00'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'proved', (h, n))

    def test_an_undecided_selector_row_has_no_form(self):
        from fnvnx.lift.fnvlift import x86sys as lx
        u = lx.REC['mov_to_sreg']['undecided']; old = list(u['selectors']); u['selectors'][:] = [27]
        rec = copy.deepcopy(x86sys.load()); rec['mov_to_sreg']['undecided']['selectors'] = [27]
        try:
            import sysspec_seg
            for h in ('8ec0', '8ed8', '07', '1f', '8e00'):
                r, n = sysspec_seg.check(key(h), rec); self.assertEqual(r, 'uncertified', h)
            self.assertRaises(core.Unsupported, emit, '8ec0')
            self.assertEqual(sysspec_seg.check(key('8ed0'), rec)[0], 'proved')       # ss does not depend on it
        finally: u['selectors'][:] = old

    def test_prefixes_outside_the_spec_are_uncertified(self):
        for h in ('671f', '6707'): self.assertEqual(sysspec.check(key(h))[0], 'uncertified', h)

    def test_a_record_that_differs_from_the_form_is_a_difference(self):
        import sysspec_seg
        rec = x86sys.load(); rec = copy.deepcopy(rec); rec['mov_to_sreg']['ss']['selectors'].append(0x50)
        r, n = sysspec_seg.check(key('8ed0'), rec); self.assertEqual(r, 'FAILED(dsl)'); self.assertIn('fault', n)

    def test_push_is_a_16_bit_move_and_pop_adds_the_operand_size(self):
        t = emit('16'); self.assertIn('M16(', t); self.assertNotIn('M32(', t); self.assertIn('0x4u', t)
        self.assertIn('0x2u', emit('6616'))
        self.assertIn('(in_esp + 0x4u)', emit('17'))

    def test_the_emitted_load_checks_then_sets(self):
        t = emit('8ed0'); self.assertLess(t.index('FNVL_RAISE_AV'), t.index('fnvl_seg_set(c, 2,'))
        self.assertIn('FNVL_UD', emit('8ec8')); self.assertNotIn('fnvl_seg_set', emit('8ec8'))
        self.assertIn('fnvl_seg_get(c,3)', emit('8cd8'))

    def test_crdr_moves_are_privileged(self):
        for h in ('0f20c0', '0f22c0', '0f21c0', '0f23c0'):
            self.assertEqual(sysspec.check(key(h))[0], 'proved', h); self.assertIn('FNVL_PRIV', emit(h))

    def test_fs_write_rebases_by_the_model_of_fnvl_seg_set(self):
        f = dsl.Form('k', A0, 1); f.sel('fs', dsl.V('sel_gs', 16))
        p = f.post(); teb = z3.BitVec('teb', 32); s = z3.Solver()
        flat = declared_x86sys_fs()
        s.add(z3.ZeroExt(16, z3.BitVec('sel_gs', 16)) & 0xfffc == flat, p['fs_base'] != teb); self.assertEqual(s.check(), z3.unsat)
        s = z3.Solver(); s.add(z3.ZeroExt(16, z3.BitVec('sel_gs', 16)) & 0xfffc != flat, p['fs_base'] != 0); self.assertEqual(s.check(), z3.unsat)
        self.assertIn('fsb = c->fs;', '\n'.join(dsl.xc_lines('sel', 4, dsl.V('sel_gs', 16), lambda n: 'x')))


def declared_x86sys_fs():
    from fnvnx.lift.fnvlift import x86sys
    return x86sys.SEL['fs'] & 0xfffc


class Cites(unittest.TestCase):
    def test_cites_and_pages(self):
        sdm = os.environ.get('FNVNX_SDM_TXT', '/tmp/sdmdl/sdm.txt')
        if not os.path.exists(sdm): self.skipTest('pinned SDM text not present')
        import subprocess
        py = os.path.join(HERE, '..', 'sysspec_seg.py'); tb = os.path.join(HERE, '..', 'sysspec_seg_pages.tsv')
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'sdm_cite_check.py'), sdm, py, tb], capture_output=True, text=True); self.assertEqual(r.returncode, 0, r.stdout)
        r = subprocess.run([sys.executable, '-I', os.path.join(HERE, '..', 'sdm_pages.py'), sdm, py], capture_output=True, text=True)
        with open(tb) as fh: self.assertEqual(r.stdout, fh.read())


class Prefix(unittest.TestCase):
    def setUp(self): declared_x86sys.configure()
    def tearDown(self): inputs.reset()

    def test_segment_override_on_stack_access_is_claimed_by_scope(self):
        for h in ('2607', '261e', '3606', '3607', '2e1e', '6407', '651f'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'proved', (h, n))

    def test_address_size_prefix_without_memory_operand_stays_unclaimed(self):
        for h in ('6707', '671e', '671f'):
            r, n = sysspec.check(key(h)); self.assertEqual(r, 'uncertified', h); self.assertIn('reserved', n)

    def test_forms_record_their_address_kinds(self):
        import symform
        self.assertEqual(symform.symbuild(key('1e')).form.access, frozenset({'stack'}))
        self.assertIn('explicit', symform.symbuild('8b00/0.0.0.0').form.access)

    def test_a_segment_override_on_an_explicit_memory_operand_is_not_claimed(self):
        r, n = sysspec.check(key('268c00')); self.assertNotEqual(r, 'proved')


class Far(unittest.TestCase):
    def setUp(self): declared_x86sys.configure()
    def tearDown(self): inputs.reset()

    def test_far_classes_of_the_record_are_proved_over_the_domain(self):
        import sysspec_far
        rec = x86sys.load()
        for h, call in (('ea000000000000/1.6.0.0', False), ('9a000000000000/1.6.0.0', True)):
            cases = sysspec_far.check(h, call, rec, True); self.assertTrue(all(r == 'proved' for _, r in cases), cases)
            self.assertEqual({c for c, _ in cases} >= {'valid', 'null', 'ti1', 'beyond', 'gp', 'complete', 'cs-selector', 'eip'}, True)
        r, n = sysspec.check('ea000000000000/1.6.0.0'); self.assertEqual(r, 'uncertified'); self.assertIn('OutsideDomain', n); self.assertIn('115 GDT indexes', n)

    def test_a_record_that_differs_from_the_form_is_a_difference(self):
        import sysspec_far
        rec = copy.deepcopy(x86sys.load()); rec['jmp_far']['jumps']['selectors'].append(0x20)       # a selector the Form faults on is declared valid
        cases = dict(sysspec_far.check('ea000000000000/1.6.0.0', False, rec, True)); self.assertEqual(cases['valid'], 'differs')
        rec = copy.deepcopy(x86sys.load()); del rec['jmp_far']['data_descriptors']['0x20']          # a selector of no class: the partition is not complete
        cases = dict(sysspec_far.check('ea000000000000/1.6.0.0', False, rec, True)); self.assertEqual(cases['complete'], 'differs')


if __name__ == '__main__':
    unittest.main()
