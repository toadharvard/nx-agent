"""census.py: the codemap rule (a range whose first byte the codemap accounts as data is not code) and the family check."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from proof import census

class Codemap(unittest.TestCase):
    def test_class_by_byte(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'codemap.tsv')
            open(p, 'w').write('00401000\t21\tentry-decomp\tentry:00401000\n00401015\t11\tpadding\t-\n00401020\t218\tdata-reloc\t-\n')
            cm = census.read_codemap(p)
            c = lambda va: census.codemap_class(cm, va)
            self.assertEqual([c(0x401000), c(0x401014), c(0x401015), c(0x40101f), c(0x401020), c(0x4010f9), c(0x4010fa), c(0x400fff)],
                             ['entry-decomp', 'entry-decomp', 'padding', 'padding', 'data-reloc', 'data-reloc', '', ''])
            self.assertIn('data-reloc', census.DATA_CLASSES); self.assertNotIn('padding', census.DATA_CLASSES)

if __name__ == '__main__': unittest.main()
