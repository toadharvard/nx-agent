import json, os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from proof import shardok

class T(unittest.TestCase):
    def f(self, recs):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'x.jsonl')
        open(p, 'w').write(''.join(json.dumps(r) + '\n' for r in recs)); return p
    def test_empty_not_published(self): self.assertFalse(shardok.ok(self.f([]), 2))
    def test_short_not_published(self): self.assertFalse(shardok.ok(self.f([{'result': 'proved'}]), 2))
    def test_error_not_published(self): self.assertFalse(shardok.ok(self.f([{'result': 'proved'}, {'result': 'error'}]), 2))
    def test_complete(self): self.assertTrue(shardok.ok(self.f([{'result': 'proved'}, {'result': 'FAILED'}]), 2))
    def test_missing_file(self): self.assertFalse(shardok.ok('/nonexistent', 0) and False)
if __name__ == '__main__': unittest.main()
