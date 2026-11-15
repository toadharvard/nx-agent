"""vendormap / sdmfeat vendor rule: (1) synthetic cells (the rule on fixed values); (2) over the PINNED AMD APM (fnv-nx-build tools/amd-apm, commit and sha256 in pins.json `external`) the parsed Appendix E CPUID bit rows, map cells and subsets equal the committed
reference, proof/tests/vendor_cells.tsv, the cells of the declared-CPU-cleared feature (3DNow) are 0F 0E (Vol. 5) and 0F 0F (Vol. 3), no bit carries two features, and every row sits under its own function table."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import sdmfeat as S, vendormap as V  # noqa: E402

PINS = [e for e in json.load(open(os.path.join(HERE, '..', 'pins.json')))['external'] if e['name'].startswith('amd-apm')]     # every pinned AMD volume (Vol. 3, Vol. 5)
PIN = PINS[0]


class Synthetic(unittest.TestCase):
    def test_vendor_cell_prefix_rule(self):
        cells = {(0x0f, 'n/a'): ('3DNow!', 'c'), (0x58, 'F3'): ('SSE', 'c'), (0x58, 'none'): ('SSE', 'c')}
        self.assertEqual(S.vendor_cell(cells, None, b'\x0f\x0f\x00')[0], '3DNow!')
        self.assertEqual(S.vendor_cell(cells, 'F3', b'\x0f\x58\xc1')[0], 'SSE')
        self.assertIsNone(S.vendor_cell(cells, '66', b'\x0f\x58\xc1'))        # no 66 cell: not a vendor form
        self.assertIsNone(S.vendor_cell(cells, None, b'\x90'))                  # one-byte opcodes are not in the two-byte map

    def test_table_union(self):
        t = V.table({'SSE2': [(1, 0, 'EDX', 26)]}, [('3DNow!', 0x80000001, 0, 'EDX', 31, 'c'), ('SSE2', 1, 0, 'EDX', 26, 'c')])
        self.assertEqual(t['3DNow!'], [(0x80000001, 0, 'EDX', 31)])
        cpu = {(0, 'EAX'): 4, (0x80000000, 'EAX'): 0x80000004, (0x80000001, 'EDX'): 0}
        self.assertFalse(S.present('3DNow!', t, cpu))                          # the declared answer clears the bit


SAMPLE = """Table of contents

Appendix E Obtaining Processor Information Via
the CPUID Instruction
\f
CPUID Fn0000_0001_EDX Feature Identifiers
 Bits Field Name        Description
31:29 \u2014                 Reserved
                        SSE instruction support. See Appendix D.
 25    SSE
 24    FXSR             FXSAVE and FXRSTOR instructions.
\f
 Bits Field Name        Description
  8:4  Family           a field, not a bit
  0    FPU              x87
CPUID Fn0000_0007_EBX_x1 Extended
  3    BMI9             sub-leaf 1 flag
CPUID Fn8000_001D_EAX_x[N:0] Cache Properties
  1    NotAFeature      the sub-leaf is a variable, so the row has no address
CPUID Fn8000_001D_EDX Next function
 25    MOVBE:           the colon is not part of the name
\f
Appendix F Something
 25    Stray            after the appendix
"""


class AppendixE(unittest.TestCase):
    def test_rule_on_synthetic_text(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False, encoding='utf-8') as fh:
            fh.write(SAMPLE)
        try:
            rows = V.cpuid_rows(fh.name)
        finally:
            os.unlink(fh.name)
        self.assertEqual([r[:5] for r in rows], [('SSE', 1, 0, 'EDX', 25), ('FXSR', 1, 0, 'EDX', 24), ('FPU', 1, 0, 'EDX', 0), ('BMI9', 7, 1, 'EBX', 3), ('MOVBE', 0x8000001D, 0, 'EDX', 25)])


class Pinned(unittest.TestCase):
    def test_cells_equal_the_reference(self):
        B = os.environ.get('FNVNX_BUILD')
        if not B:
            self.skipTest('FNVNX_BUILD (a clone of fnv-nx-build, the declared input of this test) is not set')
        import pdfplumber
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(['git', '-C', B, 'fetch', '-q', 'origin', PIN['ref']], check=True)
            self.assertEqual(subprocess.run(['git', '-C', B, 'rev-parse', 'FETCH_HEAD'], check=True, capture_output=True, text=True).stdout.strip(), PIN['commit'], 'the pinned branch moved')
            vols = []
            for i, pin in enumerate(PINS):
                self.assertEqual(pin['commit'], PIN['commit'])
                pdf = os.path.join(d, 'v%d.pdf' % i)
                open(pdf, 'wb').write(subprocess.run(['git', '-C', B, 'show', 'FETCH_HEAD:' + pin['file']], check=True, capture_output=True).stdout)
                self.assertEqual(hashlib.sha256(open(pdf, 'rb').read()).hexdigest(), pin['sha256'])
                subprocess.run(['pdftotext', '-layout', pdf, pdf[:-4] + '.txt'], check=True)
                vols.append((pdf, pdf[:-4] + '.txt'))
            f, cells = V.layer(vols)
            txt = vols[0][1]; p = pdfplumber.open(vols[0][0]); t31 = V.table31(txt); fn = V.gating(txt, f); sub = V.subsets(p, txt, fn)
            got = V.dump(f) + ''.join('cell\t0F%02X\t%s\t%s\n' % (k[0], k[1], v[0]) for k, v in sorted(cells.items())) + ''.join('subset\t%s\t%s\n' % (m, v[0]) for m, v in sorted(sub.items()))
        self.assertEqual(got, open(os.path.join(HERE, 'vendor_cells.tsv')).read())
        self.assertEqual({k[0] for k, v in cells.items() if v[0] == '3DNow'}, {0x0e, 0x0f})            # 0F 0F from Vol. 3 Appendix A (map cell), 0F 0E FEMMS from Vol. 5 (instruction reference); no hand entry
        self.assertIn(('3DNow', 0x80000001, 0, 'EDX', 31), {x[:5] for x in f})
        # one bit, one feature; every row's leaf and register are those of the table that the row was read under (the citation names the section)
        seen = {}
        for name, leaf, sub_, reg, bit, cite in f:
            self.assertEqual(seen.setdefault((leaf, sub_, reg, bit), name), name, 'two features at one CPUID bit')
            self.assertIn('"CPUID Fn%04X_%04X_%s' % (leaf >> 16, leaf & 0xffff, reg), cite)
        # the four that the Table 3-1 reader missed, and FXSR at its own bit (EDX[24] of both functions; EDX[25] is SSE)
        for r in (('3DNowExt', 0x80000001, 0, 'EDX', 30), ('SSE4A', 0x80000001, 0, 'ECX', 6), ('XOP', 0x80000001, 0, 'ECX', 11), ('FMA4', 0x80000001, 0, 'ECX', 16), ('FXSR', 1, 0, 'EDX', 24), ('FXSR', 0x80000001, 0, 'EDX', 24), ('SSE', 1, 0, 'EDX', 25)):
            self.assertIn(r, {x[:5] for x in f})
        self.assertEqual({k for k, v in seen.items() if v == 'FXSR'}, {(1, 0, 'EDX', 24), (0x80000001, 0, 'EDX', 24)})
        # Table 3-1 against Appendix E: a flag that Appendix E defines by the same name sits at the register and bit that Table 3-1 prints
        for flag, regbits in t31.items():
            have = {(x[3], x[4]) for x in f if x[0] == flag}
            if have:
                self.assertTrue(regbits <= have, '%s: Table 3-1 %s, Appendix E %s' % (flag, sorted(regbits), sorted(have)))


if __name__ == '__main__':
    unittest.main()
