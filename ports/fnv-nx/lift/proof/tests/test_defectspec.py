"""defectspec (CERTS.md "Oracle rules" 1): every declared model defect has exactly one certifying spec, and a FAILED(model-defect) record counts only when its spec proves it; it is never a pass by itself.
The counting clause is defectspec.main: a record that names a defect is `proved` only when the spec of EVERY named id returns proved; a spec that does not prove, and an id without a spec (`no-spec`), are
not counted (row status != proved, exit 1).  The tests run the real main over real shard files in a temporary directory (no replaced function)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import declared_x86sys; declared_x86sys.configure()
from proof import defects  # noqa: E402
from proof import defectspec  # noqa: E402

REP_MOVSB = 'f3a4/0.0.0.0'      # rep movsb: the string spec proves it
REP_SEG = '2ef3a4/0.0.0.0'      # a segment prefix: stringspec.check answers `uncertified`, not proved


def run_main(records):
    """defectspec.main over a shards directory holding `records` -> (exit status, {shape: row status})"""
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, 's.jsonl'), 'w') as fh:
            for r in records: fh.write(json.dumps(r) + '\n')
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = defectspec.main([td, '--x86sys', declared_x86sys.PATH])
    return rc, {ln.split('\t')[0]: ln.split('\t')[2] for ln in out.getvalue().splitlines()}


class T(unittest.TestCase):
    def test_every_defect_id_has_a_spec(self):
        self.assertEqual({d['id'] for d in defects.DEFECTS}, set(defectspec.SPECS))

    def test_ids_are_unique_and_one_spec_each(self):
        ids = [d['id'] for d in defects.DEFECTS]
        self.assertEqual(len(ids), len(set(ids)), 'duplicate defect id in defects.DEFECTS')
        for i in ids:
            self.assertTrue(callable(defectspec.SPECS[i]), i)      # SPECS is a dict: one entry per id, by construction a single spec
        self.assertEqual(len(defectspec.SPECS), len(ids))

    def test_a_record_counts_only_when_its_spec_proves_it(self):
        rec = lambda k, d: {'shape': k, 'result': 'FAILED(model-defect)', 'defects': d}
        self.assertEqual(run_main([rec(REP_MOVSB, ['rep-prefix-ignored'])]), (0, {REP_MOVSB: 'proved'}))
        rc, rows = run_main([rec(REP_SEG, ['rep-prefix-ignored'])])                      # the spec does not prove this shape
        self.assertEqual((rc, rows), (1, {REP_SEG: 'uncertified'}))
        rc, rows = run_main([rec(REP_MOVSB, ['no-such-defect'])])                      # an id without a spec
        self.assertEqual((rc, rows), (1, {REP_MOVSB: 'no-spec'}))
        rc, rows = run_main([rec(REP_MOVSB, ['rep-prefix-ignored', 'no-such-defect'])])      # every named id must prove
        self.assertEqual((rc, rows), (1, {REP_MOVSB: 'no-spec'}))
        rc, rows = run_main([{'shape': REP_SEG, 'result': 'proved'}])                    # a record that names no defect is not this gate's
        self.assertEqual((rc, rows), (0, {}))


if __name__ == '__main__':
    unittest.main()
