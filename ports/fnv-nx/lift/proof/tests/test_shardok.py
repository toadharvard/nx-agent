import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from fnvnx.lift.proof import shardok

class T(unittest.TestCase):
    def f(self, recs):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'x.jsonl')
        open(p, 'w').write(''.join(json.dumps(r) + '\n' for r in recs)); return p
    def test_empty_not_published(self): self.assertFalse(shardok.ok(self.f([]), 2))
    def test_short_not_published(self): self.assertFalse(shardok.ok(self.f([{'result': 'proved'}]), 2))
    def test_error_not_published(self): self.assertFalse(shardok.ok(self.f([{'result': 'proved'}, {'result': 'error'}]), 2))
    def test_complete(self): self.assertTrue(shardok.ok(self.f([{'result': 'proved'}, {'result': 'FAILED'}]), 2))
    def test_missing_file(self): self.assertFalse(shardok.ok('/nonexistent', 0))
if __name__ == '__main__': unittest.main()
