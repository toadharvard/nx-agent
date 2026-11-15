"""sdmpriv: the class of verbatim SDM #GP(0) sentences, and that the committed rows are the privileged set that forms_sys and f_sys read (no hand copy of it)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import privset, sdmpriv, forms_sys


class T(unittest.TestCase):
    def test_sentences(self):
        n = 0
        for ln in open(os.path.join(HERE, 'sdm_priv_sentences.tsv'), encoding='utf-8'):
            if ln[0] == '#' or not ln.strip():
                continue
            s, c, _ = ln.rstrip('\n').split('\t')
            self.assertEqual(sdmpriv.classify(s) or '-', c, s); n += 1
        self.assertEqual(n, 8)

    def test_one_source(self):
        self.assertEqual(set(forms_sys._PRIV), set(privset.mnemonics()))
        self.assertFalse(hasattr(forms_sys, 'PRIV_MN'))
        for m in ('cli', 'hlt', 'in', 'lgdt', 'wrmsr', 'sysexit'):
            self.assertIn(m, privset.mnemonics())
        for m in ('rdpmc', 'rdtsc', 'mov', 'sgdt'):          # conditional (SDM: PCE, TSD, UMIP) or operand-qualified: not in the set
            self.assertNotIn(m, privset.mnemonics())


if __name__ == '__main__':
    unittest.main()
