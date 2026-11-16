"""treeid.resume: only the complete, non-error records of the running tree are resumed; everything else is dropped and the shape is done again."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from fnvnx.lift.proof import treeid  # noqa: E402


class T(unittest.TestCase):
    def test_resume_keeps_only_this_trees_good_records(self):
        p = os.path.join(tempfile.mkdtemp(), 's.jsonl')
        rows = [{'shape': 'a', 'result': 'proved', 'tree': 'T1'}, {'shape': 'b', 'result': 'error', 'tree': 'T1'}, {'shape': 'c', 'result': 'proved', 'tree': 'T0'},
                {'shape': 'd', 'result': 'proved'}, {'shape': 'a', 'result': 'proved', 'tree': 'T1'}]
        open(p, 'w').write(''.join(json.dumps(r) + '\n' for r in rows) + '{"shape": "e", "res')       # a partial last line
        self.assertEqual(treeid.resume(p, 'T1'), {'a'})
        self.assertEqual([json.loads(l)['shape'] for l in open(p)], ['a'])

    def test_a_missing_file_resumes_nothing(self):
        self.assertEqual(treeid.resume(os.path.join(tempfile.mkdtemp(), 'none.jsonl'), 'T1'), set())

    def test_current_is_a_commit(self):
        self.assertRegex(treeid.current(), r'^[0-9a-f]{40}(\+dirty)?$')


if __name__ == '__main__':
    unittest.main()
