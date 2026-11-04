#!/usr/bin/env python3
"""tests/s9 - the pipeline's provenance rule (J-0054) and stage cache on toy stages (no decomp content, no tools).

    python3 -I tests/s9/test_provenance.py

A toy `prep` stage stands in for prepare (it records prepared_from = a commit the test chooses, output id = the
tree content it is told to produce); `use` consumes it (prov), `use2` consumes `use`. Checks: inputs from another
commit are refused (status fail, downstream skipped) unless FORCE_STALE=1 (then warn + stale); inputs from the head
are consumed; a rerun with nothing changed caches every stage; a re-prepare at a new head that gives the same tree
keeps the consumers cached and moves their provenance to the new head; a record without provenance is refused.
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'pipeline'))
import build  # noqa: E402

HEAD = 'a' * 40
OLD = 'b' * 40


class Prep(build.Stage):
    name = 'prep'
    commit, content = HEAD, 'tree-1'

    def fingerprint(self, b):
        return ['prep', Prep.commit, Prep.content]

    def run(self, b, r):
        r['prepared_from'] = {'port_commit': Prep.commit, 'mode': 'toy'}
        r['output_id'] = Prep.content


class Use(build.Stage):
    name = 'use'
    deps = ('prep',)
    prov = ('prep',)
    runs = 0

    def fingerprint(self, b):
        return ['use', build.up_id(b, 'prep')]

    def run(self, b, r):
        Use.runs += 1
        r['output_id'] = 'objs-of-' + build.up_id(b, 'prep')


class Use2(build.Stage):
    name = 'use2'
    deps = ('use',)
    prov = ('use',)

    def fingerprint(self, b):
        return ['use2', build.up_id(b, 'use')]

    def run(self, b, r):
        r['output_id'] = 'link-of-' + build.up_id(b, 'use')


class Provenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fnvnx-s9-')
        self.saved = build.STAGES, build.BYNAME
        build.STAGES = [Prep(), Use(), Use2()]
        build.BYNAME = {s.name: s for s in build.STAGES}
        Prep.commit, Prep.content, Use.runs = HEAD, 'tree-1', 0

    def tearDown(self):
        build.STAGES, build.BYNAME = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_pipeline(self, **cfg):
        c = {k: d for k, d, _ in build.CONFIG}
        c.update(BUILD_DIR=self.tmp, JOBS='1', PORT_COMMIT='')
        c.update(cfg)
        b = build.Build(c)
        b.head = HEAD                      # the "current head" of this test, whatever the checkout's is
        failed = build.execute(b, ['use2'])
        b.plog.close()
        recs = {}
        for s in ('prep', 'use', 'use2'):
            with open(os.path.join(self.tmp, s, 'stage.json')) as f:
                recs[s] = build.json.load(f)
        return failed, recs

    def test_head_consumed_and_cached(self):
        failed, r = self.run_pipeline()
        self.assertFalse(failed)
        self.assertEqual([r[s]['status'] for s in ('prep', 'use', 'use2')], ['ok'] * 3)
        self.assertEqual(r['use2']['prepared_from']['port_commit'], HEAD)
        failed, r = self.run_pipeline()
        self.assertFalse(failed)
        self.assertEqual([r[s]['ran'] for s in ('prep', 'use', 'use2')], [False] * 3)
        self.assertEqual(Use.runs, 1)

    def test_stale_refused(self):
        Prep.commit = OLD
        failed, r = self.run_pipeline()
        self.assertTrue(failed)
        self.assertEqual(r['use']['status'], 'fail')
        self.assertIn('stale inputs', r['use']['error'])
        self.assertEqual(r['use2']['status'], 'skipped')
        self.assertEqual(Use.runs, 0)

    def test_stale_forced(self):
        Prep.commit = OLD
        failed, r = self.run_pipeline(FORCE_STALE='1')
        self.assertFalse(failed)
        self.assertEqual((r['use']['status'], r['use2']['status']), ('warn', 'warn'))
        self.assertTrue(r['use2'].get('stale'))
        self.assertEqual(r['use2']['prepared_from']['port_commit'], OLD)
        # cached outputs of a stale tree are refused too, once FORCE_STALE is gone
        failed, r = self.run_pipeline()
        self.assertTrue(failed)
        self.assertEqual(r['use']['status'], 'fail')

    def test_reprepare_same_tree_moves_provenance(self):
        Prep.commit = OLD
        self.run_pipeline(FORCE_STALE='1')
        Prep.commit = HEAD                 # prepared again at the head, identical content
        failed, r = self.run_pipeline()
        self.assertFalse(failed)
        self.assertTrue(r['prep']['ran'])
        self.assertEqual((r['use']['ran'], r['use2']['ran']), (False, False))
        self.assertEqual((r['use']['status'], r['use2']['status']), ('ok', 'ok'))
        self.assertEqual(r['use2']['prepared_from']['port_commit'], HEAD)
        self.assertNotIn('stale', r['use2'])

    def test_reprepare_new_tree_reruns(self):
        self.run_pipeline()
        Prep.content = 'tree-2'
        failed, r = self.run_pipeline()
        self.assertFalse(failed)
        self.assertTrue(r['use']['ran'])
        self.assertEqual(Use.runs, 2)

    def test_missing_record_refused(self):
        self.run_pipeline()
        p = os.path.join(self.tmp, 'prep', 'stage.json')
        with open(p) as f:
            rec = build.json.load(f)
        del rec['prepared_from']
        with open(p, 'w') as f:
            build.json.dump(rec, f)
        failed, r = self.run_pipeline()
        self.assertTrue(failed)
        self.assertIn('no provenance record', r['use']['error'])

    def test_prepared_from_txt(self):
        d = os.path.join(self.tmp, 'run')
        os.makedirs(os.path.join(d, 'tree'))
        with open(os.path.join(d, 'prepared_from.txt'), 'w') as f:
            f.write(OLD + '\n')            # S2c's driver: the commit alone, next to the tree
        pf = build.read_prepared_from(os.path.join(d, 'tree'))
        self.assertEqual((pf['port_commit'], pf['decomp_commit']), (OLD, None))
        build.write_prepared_from(d, {'port_commit': HEAD, 'decomp_commit': OLD, 'mode': 'prepare.py',
                                      'tree': os.path.join(d, 'tree')})
        pf = build.read_prepared_from(os.path.join(d, 'tree'))   # the pipeline's .json wins
        self.assertEqual((pf['port_commit'], pf['decomp_commit']), (HEAD, OLD))
        with open(os.path.join(d, 'prepared_from.txt')) as f:
            self.assertEqual(f.readline().strip(), HEAD)


if __name__ == '__main__':
    unittest.main(verbosity=1)
