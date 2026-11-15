"""Tests of proof/formgate.py on the real declared inputs (no doubles): formgate_inputs.json, the real certs external of fnv-nx-build fetched by
recipe.fetch_externals (exact commit, tree_sha256 checked), the real admit.json of this tree. Needs the network (ARTIFACTS_URL or the default
fnv-nx-build). Cases that need a lifted root's pack, or a broken pack, wait for a lifted product root and for root-level fixtures (L1)."""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, '..', '..'))
REPO = os.path.normpath(os.path.join(LIFT, '..', '..', '..'))
sys.path.insert(0, LIFT)
sys.path.insert(0, os.path.join(REPO, 'ports', 'fnv-nx', 'contract'))
sys.path.insert(0, os.path.join(REPO, 'ports', 'fnv-nx', 'verify'))
from proof import formgate as F


class Real(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.mkdtemp(prefix='formgate-real-')
        cls.doc = F.load_inputs()
        cname, cls.crel = cls.doc['certs'].split('/', 1)
        cls.ext = F.fetch([e for e in cls.doc['external'] if e['name'] == cname], cls.cache)
        cls.cname = cname

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.cache, ignore_errors=True)

    def test_inputs_are_valid_and_the_model_pin_matches(self):
        self.assertEqual(self.doc['model_sha256'], F.model_pin())

    def test_real_certs_parse(self):          # at 16ca5eb9 the file holds the header only: 0 certificates yet
        certs = F.load_certs(F.path_in(self.ext, self.cname, self.crel, 'certs'), self.doc['model_sha256'])
        self.assertIsInstance(certs, dict)

    def test_no_lifted_root_is_universe_zero_green(self):
        u, c, ids, _ = F.judge(REPO, os.path.join(self.cache, 'judge'))
        self.assertEqual((u, c, ids), (0, 0, [])) if not F.lifted_roots(REPO) else self.assertEqual(u, c + len(ids))


if __name__ == '__main__':
    unittest.main()
