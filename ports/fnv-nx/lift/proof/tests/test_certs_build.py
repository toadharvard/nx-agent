"""Tests of proof/certs_build.py on synthetic shard files (shapes of three plain encodings; the records are written here, nothing is exe-derived)."""
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..'))
from proof import certs_build, shape, symform

WORK = os.environ.get('FNVNX_TEST_WORK', '/home/user/l3work/proof/test_work')
MD = shape.decoder()


def key_of(h):
    ins = list(MD.disasm(bytes.fromhex(h), 0))
    return shape.shape_key(ins[0])


KEYS = sorted(key_of(h) for h in ('01c8', '31c0', '8b4510'))
MODEL = certs_build.pinned_model()


def rec(key, result='proved', model=MODEL, dsl=None):
    return {'shape': key, 'result': result, 'model_sha': model, 'dsl_sha': dsl or symform.shape_sha(key)}


class Aggregate(unittest.TestCase):
    def setUp(self):
        self.d = os.path.join(WORK, 'certs_build')
        shutil.rmtree(self.d, ignore_errors=True)
        os.makedirs(self.d)

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, name, recs):
        p = os.path.join(self.d, name)
        with open(p, 'w') as fh:
            fh.writelines(json.dumps(r, sort_keys=True) + '\n' for r in recs)
        return p

    def run_main(self, paths, out):
        return certs_build.main(['--shards'] + paths + ['--out', out])

    def test_rows_sorted_and_independent_of_shard_order(self):
        a = self.write('shapeprove_1_of_2.jsonl', [rec(KEYS[2]), rec(KEYS[0])])
        b = self.write('shapeprove_2_of_2.jsonl', [rec(KEYS[1]), rec(KEYS[1], 'timeout')])
        o1, o2 = os.path.join(self.d, 'o1'), os.path.join(self.d, 'o2')
        self.assertEqual(self.run_main([a, b], o1), 0)
        self.assertEqual(self.run_main([b, a], o2), 0)
        t1 = open(o1).read()
        self.assertEqual(t1, open(o2).read())
        rows = [ln.split('\t') for ln in t1.splitlines()[1:]]
        self.assertEqual([r[0] for r in rows], KEYS)
        self.assertTrue(all(r[3] == MODEL and r[4] == 'sail' and r[5] == 'proved' and r[2] == symform.shape_sha(r[0]) for r in rows))

    def test_only_proved_rows(self):
        keys = KEYS
        a = self.write('shapeprove_1_of_1.jsonl', [rec(KEYS[0]), rec(KEYS[1], 'timeout'), {'shape': KEYS[2], 'result': 'no-form'}])
        o = os.path.join(self.d, 'o')
        self.assertEqual(self.run_main([a], o), 0)
        self.assertEqual(len(open(o).read().splitlines()), 2)

    def test_stale_records_are_ignored_not_refused(self):
        o = os.path.join(self.d, 'o')
        for bad in (rec(KEYS[0], model='0' * 64), rec(KEYS[0], dsl='1' * 64)):
            a = self.write('shapeprove_1_of_1.jsonl', [bad, rec(KEYS[1])])
            self.assertEqual(self.run_main([a], o), 0)
            self.assertEqual([ln.split('\t')[0] for ln in open(o).read().splitlines()[1:]], [KEYS[1]])

    def test_refusals(self):
        o = os.path.join(self.d, 'o')
        a = self.write('shapeprove_1_of_1.jsonl', [rec(KEYS[0]), rec(KEYS[0], 'FAILED(dsl)')])
        self.assertEqual(self.run_main([a], o), 2)
        self.assertFalse(os.path.exists(o))

    def shapes_file(self, keys=KEYS):
        p = os.path.join(self.d, 'shapes.tsv')
        with open(p, 'w') as fh:
            fh.write('# fixture\n' + ''.join('%s\tint\t1\n' % k for k in keys))
        return p

    def queue(self, paths, keys=KEYS):
        o = os.path.join(self.d, 'q')
        self.assertEqual(certs_build.main(['--queue', self.shapes_file(keys), '--shards'] + paths + ['--out', o]), 0)
        return open(o).read().split()

    def test_dsl_edit_requeues_exactly_the_changed_forms(self):
        from proof import forms
        keys = KEYS + [key_of('01d8'), key_of('31db')]          # add eax,ebx / xor ebx,ebx: 5 shapes, 2 of them xor
        keys = sorted(set(keys))
        a = self.write('shapeprove_1_of_1.jsonl', [rec(k) for k in keys])
        self.assertEqual(self.queue([a], keys), [])                   # everything proved under the current DSL
        xor_keys = [k for k in keys if k.startswith('31')]
        orig = forms.H['xor']

        def edited(c):
            orig(c)
            c.F.undefined('df', 'edited')
        forms.H['xor'] = edited
        certs_build._SHA.clear()
        try:
            self.assertEqual(self.queue([a], keys), xor_keys)         # the old records are stale for xor, valid for the rest
            o = os.path.join(self.d, 'o')
            self.assertEqual(self.run_main([a], o), 0)
            self.assertEqual([ln.split('\t')[0] for ln in open(o).read().splitlines()[1:]], [k for k in keys if k not in xor_keys])
        finally:
            forms.H['xor'] = orig
            certs_build._SHA.clear()
        self.assertEqual(self.queue([a], keys), [])

    def test_failed_and_timeout_stay_queued(self):
        keys = KEYS
        a = self.write('shapeprove_1_of_1.jsonl', [rec(KEYS[0]), rec(KEYS[1], 'timeout'), rec(KEYS[2], 'FAILED(dsl)')])
        self.assertEqual(self.queue([a], keys), KEYS[1:])

    def test_directory_argument(self):
        self.write('shapeprove_1_of_1.jsonl', [rec(KEYS[0])])
        o = os.path.join(self.d, 'o')
        self.assertEqual(self.run_main([self.d], o), 0)
        self.assertEqual(len(open(o).read().splitlines()), 2)


if __name__ == '__main__':
    unittest.main()
