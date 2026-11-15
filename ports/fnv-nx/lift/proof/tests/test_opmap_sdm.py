"""opmap over the PINNED SDM PDF (fnv-nx-build tools/intel-sdm, fetched and checked by sha256) gives exactly the committed reference cells, proof/tests/opmap_cells.tsv."""
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import opmap

B = os.environ.get('FNVNX_BUILD') or sys.exit('FNVNX_BUILD (a clone of fnv-nx-build, the declared input of this test) is not set')
PIN = 'a4a62e6a7ba11a76c7753a195b825087306812aac39b930973f9168ee599f321'      # tools/intel-sdm MANIFEST.json sha256


def pinned_sdm(d):
    subprocess.run(['git', '-C', B, 'fetch', '-q', 'origin', 'tools/intel-sdm'], check=True)
    pdf = os.path.join(d, 'sdm.pdf')
    with open(pdf, 'wb') as fh:
        fh.write(subprocess.run(['git', '-C', B, 'show', 'FETCH_HEAD:intel-64-ia-32-sdm-combined.pdf'], check=True, capture_output=True).stdout)
    assert hashlib.sha256(open(pdf, 'rb').read()).hexdigest() == PIN, 'the fetched SDM is not the pinned one'
    subprocess.run(['pdftotext', '-layout', pdf, os.path.join(d, 'sdm.txt')], check=True)
    return pdf, os.path.join(d, 'sdm.txt')


class T(unittest.TestCase):
    def test_cells_equal_the_reference(self):
        with tempfile.TemporaryDirectory() as d:
            pdf, txt = pinned_sdm(d)
            got = opmap.dump(opmap.load(pdf, txt))
        self.assertEqual(got, open(os.path.join(HERE, 'opmap_cells.tsv')).read())

    def test_x87_cells_of_the_sdm(self):
        """cells read off the SDM's Tables A-7..A-22 (the instruction in the cell, as printed there); blanks are reserved (A.5 NOTES)"""
        with tempfile.TemporaryDirectory() as d:
            pdf, txt = pinned_sdm(d)
            m = opmap.load(pdf, txt)
        want = [(b'\xdf\x56\x00', 'A-21 FIST'), (b'\xdf\x88\x00', 'A-21 FISTTP'), (b'\xd8\xc3', 'A-8 FADD'), (b'\xd9\xd0', 'A-10 FNOP'), (b'\xdb\xe3', 'A-14 FINIT'),
                (b'\xdf\xe0', 'A-22 FSTSW'), (b'\xdd\x20', 'A-17 FRSTOR'), (b'\xdb\xe8', 'A-14 FUCOMI'), (b'\xdf\xf0', 'A-22 FCOMIP')]
        for body, w in want:
            r = opmap.lookup(m, '', body)
            self.assertIsNotNone(r, body.hex())
            self.assertIn(w.split()[0].replace('A-', 'Table A-'), r[0]); self.assertIn(w.split()[1], r[1])
        for body in (b'\xd9\xd1', b'\xdb\xe4', b'\xdb\x30', b'\xd9\x08'):             # blank cells: D9 D1, DB E4, DB /6, D9 /1
            self.assertIsNone(opmap.lookup(m, '', body), body.hex())
        for mrm in range(0xc0, 0x100):                                                      # D8 C0-FF: A-8, every column of rows C-F is an instruction
            self.assertIsNotNone(opmap.lookup(m, '', bytes([0xd8, mrm])), hex(mrm))


if __name__ == '__main__':
    unittest.main()
