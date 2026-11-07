#!/usr/bin/env python3
"""tests of T1-fastbench's overhead-audit cache (tools/perf/overhead_audit.py --catalog/--cache/--cache-out): a function
whose audit key is cached is not measured again and takes no time from the deadline; a changed key, a cache from any
shard, an uncached failure. The harness (dyncount, engine, funcmodel, fnkeys) is stubbed: no unicorn, no link.

    python3 -I tests/t1/test_audit_cache.py
"""
import io, json, os, shutil, sys, tempfile, types, unittest
from contextlib import redirect_stdout
HERE = os.path.dirname(os.path.abspath(__file__))
PERF = os.path.join(HERE, '..', '..', 'tools', 'perf')

RUNS = []          # (source, addr) measured
KEYS = {}          # fnkeys.compute's result: {'%08x': key}


def stub_modules(tree):
    dc = types.ModuleType('dyncount')
    dc.COUNT = {}
    dc.CountingA64 = type('CountingA64', (), {'_count_block': lambda *a: None})

    def run_target(uc, source, addr, seeds, steps):
        RUNS.append((source, addr))
        if addr == 0x400:
            raise SystemExit('no function')
        return [{'x86': 20, 'a64': 30, 'a64_bridge': 2}] * seeds
    dc.run_target = run_target
    cfg = types.ModuleType('config'); cfg.TREE = tree; cfg.LINK = tree; cfg.A64WORK = tree
    eng = types.ModuleType('engine')
    eng.UnitCache = lambda *a: types.SimpleNamespace(arm_image=lambda s: types.SimpleNamespace(syms={}))
    cost = types.ModuleType('cost'); cost.MCA = lambda *a, **k: None
    fm = types.ModuleType('funcmodel'); fm.price_funcs = lambda *a: [{}]
    fk = types.ModuleType('fnkeys')
    fk.compute = lambda targets, catalog: (dict(KEYS), {}, 1)
    for m in (dc, cfg, eng, cost, fm, fk):
        sys.modules[m.__name__] = m


class AuditCache(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp(prefix='t1audit.')
        os.makedirs(os.path.join(self.t, 'build'))
        json.dump({'units': [{'source': 'a.cpp', 'functions': [{'address': '0x100'}, {'address': '0x200'}]},
                             {'source': 'b.cpp', 'functions': [{'address': '0x300'}, {'address': '0x400'}]}]},
                  open(os.path.join(self.t, 'build', 'units.json'), 'w'))
        stub_modules(self.t)
        sys.path.insert(0, PERF)
        sys.modules.pop('overhead_audit', None)
        import overhead_audit
        self.oa = overhead_audit
        self.cache = os.path.join(self.t, 'cache'); os.makedirs(self.cache)
        open(os.path.join(self.t, 'cat.jsonl'), 'w').close()

    def tearDown(self):
        shutil.rmtree(self.t, ignore_errors=True)

    def run_audit(self, out, *extra):
        RUNS.clear()
        sys.argv = ['overhead_audit.py', '--units', '.', '--seeds', '2', '--catalog', os.path.join(self.t, 'cat.jsonl'),
                    '--cache', self.cache, '--cache-out', out, '--summary', out + '.json'] + list(extra)
        with redirect_stdout(io.StringIO()):
            self.oa.main()
        return json.load(open(out + '.json'))

    def test_incremental(self):
        KEYS.clear(); KEYS.update({'00000100': 'k1', '00000200': 'k2', '00000400': 'k4'})   # 0x300: no native key
        s = self.run_audit(os.path.join(self.cache, '1of2.jsonl'))
        self.assertEqual(len(RUNS), 4); self.assertEqual(s['cached'], 0); self.assertEqual(s['keyed'], 3)
        rows = [json.loads(l) for l in open(os.path.join(self.cache, '1of2.jsonl'))]
        self.assertEqual(sorted(r['addr'] for r in rows), [0x100, 0x200])   # setup failure (0x400) not cached
        # unchanged keys: only the unkeyed and the failed function run again; a changed key runs again
        KEYS['00000200'] = 'k2b'
        s = self.run_audit(os.path.join(self.t, 'new.jsonl'))
        self.assertEqual(sorted(a for _, a in RUNS), [0x200, 0x300, 0x400])
        self.assertEqual(s['cached'], 1); self.assertEqual(s['clean'], 3)
        self.assertEqual(s['x86_insns'], 60)                                # cached row counted in the totals

    def test_cached_rows_ignore_deadline(self):
        KEYS.clear(); KEYS.update({'00000100': 'k1', '00000200': 'k2', '00000300': 'k3'})
        self.run_audit(os.path.join(self.cache, '2of2.jsonl'))
        s = self.run_audit(os.path.join(self.t, 'n.jsonl'), '--deadline', '1e-9')
        self.assertEqual(s['cached'], 3)
        self.assertEqual(s['pending'], 1)                                  # only the uncached one hits the deadline
        self.assertEqual(RUNS, [])


if __name__ == '__main__':
    unittest.main()
