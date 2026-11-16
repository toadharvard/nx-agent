#!/usr/bin/env python3
"""The cleared-feature class (proof/forms_cpufeat.py, sysspec_cpufeat.py): an instruction whose CPUID feature the declared CPU clears is the Form fault(UD) with no other effect; the MPX hint-NOP encodings (0F 1A/1B) are the Form NOP
with no memory access (SDM Vol. 1 E.4.1).  Needs the mounted SDMROWS input (FNVNX_INPUT_SDMROWS) and the declared CPU model (verify/cpuid_model.json) and the pinned SDM text (FNVNX_SDM_TXT) for the page table check.
python3 -I tests/test_sysspec_cpufeat.py"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import declared_x86sys
from fnvlift import inputs
from proof import forms, shape, symform
import sysspec_cpufeat as S

SDM = os.environ.get('FNVNX_SDM_TXT')
PROOF = os.path.join(HERE, '..')


def key(h):
    return shape.shape_key(next(iter(shape.decoder().disasm(bytes.fromhex(h), 0))))


def _udsail():
    u = os.environ.get('FNVNX_INPUT_UDSAIL')      # the produced input UDSAIL (the Sail #UD verdicts of the forms with no SDM row: 3DNow 0F 0F, 0F 0E); mounted when the gate has built it
    if u: return os.path.join(u, 'udsail.tsv') if os.path.isdir(u) else u
    import tempfile
    p = os.path.join(tempfile.mkdtemp(prefix='udsail-'), 'udsail.tsv'); open(p, 'w').write('# fnvnx-udsail/2\n')      # no refused or undefined sequence: the shapes of this test all have SDM rows (as fnvlift/tests/_inputs.py for a buffer test)
    return p


class Cleared(unittest.TestCase):
    def setUp(self): declared_x86sys.configure(); inputs.configure(cpumodel=os.path.join(HERE, '..', '..', '..', 'verify', 'cpuid_model.json'), udsail=_udsail())
    def tearDown(self): inputs.reset()

    def test_3dnow_avx_sse3_are_ud(self):
        for h in ('0f0fc10d', '0f0f0094', '0f0e', 'c5f1e3c2', 'db08', 'dd08', 'df08'):
            r, n = S.check(key(h)); self.assertEqual(r, 'proved', (h, n))
            self.assertEqual([k for k, _ in symform.symbuild(key(h)).form.faults], [3], h)

    def test_mpx_is_nop_without_memory_access(self):
        for h in ('0f1a00', '0f1a05aabbccdd', '0f1a0424', '0f1a8000000000', '0f1b00', '660f1a00', 'f20f1a00', 'f30f1b00'):
            r, n = S.check(key(h)); self.assertEqual(r, 'proved', (h, n))
            f = symform.symbuild(key(h)).form
            self.assertFalse(f.faults or f.stores or f.regs or f.flags, h)
            self.assertEqual(f.access, frozenset(), h)

    def test_a_present_feature_is_not_claimed(self):
        for h in ('90', '0fef02', '660ffcc1', '0f2800', '01c1'): self.assertEqual(S.check(key(h))[0], 'not-covered', h)

    def test_mpx_class_is_every_cell_of_the_opcode_pair(self):
        # evidence of the record's exec_as_nop: every cell of the SDM opcode map at 0F 1A / 0F 1B is an MPX instruction, and the map has no cell at 0F 0E / 0F 0F (3DNow)
        rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(HERE, 'opmap_cells.tsv')) if l.strip() and not l.startswith('#')]
        two = [r for r in rows if r[0] == 'two']
        for op in ('1A', '1B'):
            cells = [r for r in two if r[1] == op]
            self.assertTrue(cells and all(r[4].startswith('bnd') for r in cells), op)
        self.assertEqual([r for r in two if r[1] in ('0E', '0F')], [])

    def test_the_decision_is_the_declared_cpus(self):
        from proof import cpufeat
        for h, ud in (('c5f1e3c2', True), ('0fef02', False), ('660ffcc1', False)):
            real = next(iter(shape.decoder().disasm(bytes.fromhex(h), 0)))
            self.assertEqual(bool(cpufeat.missing(real)), ud, h)
        self.assertEqual(cpufeat.nop(bytes.fromhex('0f1a00'), 0), 'MPX')      # MPX: NOP on the declared CPU

    def test_unconfigured_input_gives_no_decision(self):
        inputs.reset()
        self.assertIsNone(S.kind(S.G.decode(key('0f0fc10d'))))


@unittest.skipUnless(SDM and os.path.exists(SDM), 'pinned SDM text not available: FNVNX_SDM_TXT')
class Pages(unittest.TestCase):
    def test_page_table_is_the_generated_one_and_every_quote_is_in_the_sdm(self):
        gen = subprocess.run([sys.executable, '-I', 'sdm_pages.py', SDM, 'sysspec_cpufeat.py'], cwd=PROOF, capture_output=True, text=True, check=True).stdout
        self.assertEqual(gen, open(os.path.join(PROOF, 'sysspec_cpufeat_pages.tsv')).read())
        r = subprocess.run([sys.executable, '-I', 'sdm_cite_check.py', SDM, 'sysspec_cpufeat.py', 'sysspec_cpufeat_pages.tsv'], cwd=PROOF, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('MISS', r.stdout)


if __name__ == '__main__': unittest.main()
