"""vendormap / sdmfeat vendor rule: (1) synthetic cells (the rule on fixed values); (2) over the PINNED AMD APM (fnv-nx-build tools/amd-apm, commit and sha256 in pins.json `external`) the parsed features and map cells equal the committed
reference, proof/tests/vendor_cells.tsv, and the cells of the declared-CPU-cleared feature (3DNow!) are 0F 0E and 0F 0F."""
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

PIN = [e for e in json.load(open(os.path.join(HERE, '..', 'pins.json')))['external'] if e['name'] == 'amd-apm'][0]


class Synthetic(unittest.TestCase):
    def test_vendor_cell_prefix_rule(self):
        cells = {(0x0f, 'n/a'): ('3DNow!', 'c'), (0x58, 'F3'): ('SSE', 'c'), (0x58, 'none'): ('SSE', 'c')}
        self.assertEqual(S.vendor_cell(cells, None, b'\x0f\x0f\x00')[0], '3DNow!')
        self.assertEqual(S.vendor_cell(cells, 'F3', b'\x0f\x58\xc1')[0], 'SSE')
        self.assertIsNone(S.vendor_cell(cells, '66', b'\x0f\x58\xc1'))        # no 66 cell: not a vendor form
        self.assertIsNone(S.vendor_cell(cells, None, b'\x90'))                  # one-byte opcodes are not in the two-byte map

    def test_table_union(self):
        t = V.table({'SSE2': [(1, 0, 'EDX', 26)]}, [('3DNow!', 0x80000001, 'EDX', 31, 'c'), ('SSE2', 1, 'EDX', 26, 'c')])
        self.assertEqual(t['3DNow!'], [(0x80000001, 0, 'EDX', 31)])
        cpu = {(0, 'EAX'): 4, (0x80000000, 'EAX'): 0x80000004, (0x80000001, 'EDX'): 0}
        self.assertFalse(S.present('3DNow!', t, cpu))                          # the declared answer clears the bit


class Pinned(unittest.TestCase):
    def test_cells_equal_the_reference(self):
        B = os.environ.get('FNVNX_BUILD')
        if not B:
            self.skipTest('FNVNX_BUILD (a clone of fnv-nx-build, the declared input of this test) is not set')
        import pdfplumber
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(['git', '-C', B, 'fetch', '-q', 'origin', PIN['ref']], check=True)
            self.assertEqual(subprocess.run(['git', '-C', B, 'rev-parse', 'FETCH_HEAD'], check=True, capture_output=True, text=True).stdout.strip(), PIN['commit'], 'the pinned branch moved')
            pdf = os.path.join(d, 'a.pdf')
            open(pdf, 'wb').write(subprocess.run(['git', '-C', B, 'show', 'FETCH_HEAD:' + PIN['file']], check=True, capture_output=True).stdout)
            self.assertEqual(hashlib.sha256(open(pdf, 'rb').read()).hexdigest(), PIN['sha256'])
            txt = os.path.join(d, 'a.txt'); subprocess.run(['pdftotext', '-layout', pdf, txt], check=True)
            p = pdfplumber.open(pdf); f = V.features(p, txt); fn = {x[0] for x in f}; sub = V.subsets(p, txt, fn)
            cells = V.mapcells(p, txt, dict({x: x for x in fn}, **{m: v[0] for m, v in sub.items()}))
            got = V.dump(f) + ''.join('cell\t0F%02X\t%s\t%s\n' % (k[0], k[1], v[0]) for k, v in sorted(cells.items())) + ''.join('subset\t%s\t%s\n' % (m, v[0]) for m, v in sorted(sub.items()))
        self.assertEqual(got, open(os.path.join(HERE, 'vendor_cells.tsv')).read())
        self.assertEqual({k[0] for k, v in cells.items() if v[0] == '3DNow!'}, {0x0e, 0x0f})
        self.assertIn(('3DNow!', 0x80000001, 'EDX', 31), {x[:4] for x in f})


if __name__ == '__main__':
    unittest.main()
