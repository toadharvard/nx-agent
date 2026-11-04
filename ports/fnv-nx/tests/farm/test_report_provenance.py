#!/usr/bin/env python3
"""tools/farm/full_report.py's provenance verdict: a full report says PASS only when every tree-derived stage was
prepared from the report's commit and, given --since, written or confirmed by this job (the 097cdd1504c8 report
read 06857911's tree, objects and link as "cached" and said ok)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(PORT, 'tools', 'farm'))
import full_report  # noqa: E402

HEAD, OLD = '097cdd1504c8' + 'a' * 28, '06857911f5ab' + 'b' * 28
bad = 0


def ok(cond, what):
    global bad
    print(('ok   ' if cond else 'FAIL ') + what)
    bad += not cond


def recs(commit, run_id, cached_at=None, stale=None):
    pf = {'port_commit': commit, 'decomp_commit': 'd' * 40, 'mode': 'prepare.py'}
    R = {}
    for s in ('prepare', 'census', 'compile', 'link', 'guest', 'boot', 'package'):
        R[s] = {'status': 'ok', 'run_id': run_id, 'prepared_from': pf}
        if cached_at:
            R[s]['cached_at'] = cached_at
    R['host'] = {'status': 'ok', 'run_id': run_id}      # not tree-derived: no provenance record needed
    if cached_at:
        R['host']['cached_at'] = cached_at
    if stale:
        R['link']['stale'] = stale
    return R


f = full_report.provenance_failures
ok(f(recs(HEAD, '20261104-160000'), HEAD, '20261104-155900') == [], 'this commit, this job: PASS')
ok(f(recs(HEAD, '20261104-120000', cached_at='20261104-160000'), HEAD, '20261104-155900') == [],
   'cached in this job (cached_at): PASS')
r = f(recs(OLD, '20261104-120000'), HEAD, '20261104-155900')
ok(any('prepared from 06857911f5ab' in x for x in r) and any('earlier run' in x for x in r),
   'the 097cdd1504c8 case (06857911 records, make not run): FAIL with both reasons')
ok(any('prepared from' in x for x in f(recs(OLD, '20261104-160000'), HEAD, '20261104-155900')),
   'another commit\'s tree in this job: FAIL')
R = recs(HEAD, '20261104-160000')
del R['compile']['prepared_from']
R['prepare'].pop('prepared_from')
ok(any('prepare: no provenance record' in x for x in f(R, HEAD, None)), 'prepare without a record: FAIL')
ok(any('stale' in x for x in f(recs(HEAD, '20261104-160000', stale='x'), HEAD, None)), 'a stale stage: FAIL')
ok(f(recs(HEAD[:12], '20261104-160000'), HEAD, None) == [], 'a 12-digit commit matches the full one')
ok(f(recs(HEAD, '1'), '?', None) == ['no --commit given'], 'no commit: FAIL')

with tempfile.TemporaryDirectory() as t:
    for s, r in recs(OLD, '20261104-120000').items():
        os.makedirs(os.path.join(t, 'b', s))
        json.dump(r, open(os.path.join(t, 'b', s, 'stage.json'), 'w'))
    out = os.path.join(t, 'r.md')
    p = subprocess.run([sys.executable, '-I', os.path.join(PORT, 'tools', 'farm', 'full_report.py'),
                        os.path.join(t, 'b'), out, '--commit', HEAD, '--make-rc', '0', '--since', '20261104-155900'],
                       capture_output=True, text=True)
    txt = open(out).read() if os.path.isfile(out) else p.stderr
    ok('- provenance: FAIL' in txt, 'the report says provenance: FAIL (%s)' % next(
        (l for l in txt.splitlines() if 'provenance' in l), p.stderr.strip()[-200:]))
print('test_report_provenance: %d failed' % bad)
sys.exit(1 if bad else 0)
