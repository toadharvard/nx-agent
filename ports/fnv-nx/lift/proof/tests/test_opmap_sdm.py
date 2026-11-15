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

B = os.environ.get('FNVNX_BUILD', '/home/user/fnv-nx-build')
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


if __name__ == '__main__':
    unittest.main()
