"""metric.py - the S13 convergence metric from the fuzz farms' published reports.

    python3 metric.py [--artifacts <fnv-nx-build checkout>] [--repo <nx-agent checkout>] [--shards N] [--json]

Reads branch reports/fz<k> (k = 1..N) of the artifacts repository (fetched as refs/remotes/origin/reports/fz<k>;
`--fetch` fetches them first, in one call). For each shard it takes the newest report, by the commit time of its
head in this repository (heads unknown here sort first), and the newest report that is not FAILED. Three terms:

  artifacts      divergences whose cause POLICY classes as `artifact` (this checkout's causes.py), from the
                 cumulative ```json fnvnx-causes``` block of each shard's newest good report; an older report
                 without that block contributes its cumulative per-cause table (ALL row), causes counted per
                 divergence as well
  not_fuzzable   functions of the shard with a native object that the harness does not fuzz (control flow not
                 modelled, unresolved relocations); coverage = fuzzable / functions with a native object
  unpublished    shards whose newest report is FAILED (the run published no verdicts before the deadline)
  pending        fuzzable functions of each shard's newest good report without a verdict for its head (key changed
                 or never fuzzed when the deadline came)
A shard with no good report at all (branch missing, only FAILED runs) has no verdict for any of its functions: they
count as not fuzzable, catalogued / N of them (V1 M1, M2), beside the unpublished shard. A POLICY reclassification
(artifact -> by-design) lowers the artifacts term with no harness change: say so in the commit (V1 M4).

metric = artifacts + not_fuzzable + unpublished + pending (the owner's rule: each commit states
`metric: <before> -> <after>`).
Only counts are read; reports hold addresses and cause names, never decomp text.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import causes  # noqa: E402

_SHARD = re.compile(r'in this shard (\d+): no native object (\d+), control flow not modelled \([^)]*\) (\d+), '
                    r'fuzzable (\d+)')
_PENDING = re.compile(r'pending (\d+)')


def git(repo, *args, check=True):
    p = subprocess.run(['git', '-C', repo] + list(args), capture_output=True, text=True)
    if check and p.returncode:
        return None
    return p.stdout


def head_time(repo, h, _memo={}):
    if h not in _memo:
        out = git(repo, 'log', '-1', '--format=%ct', h, check=True)
        _memo[h] = int(out.strip()) if out and out.strip() else 0
    return _memo[h]


def first_section(text):
    """A report file can hold an older run appended after a FAILED header: keep the first run only."""
    m = list(re.finditer(r'^# fuzz ', text, re.M))
    return text[:m[1].start()] if len(m) > 1 else text


def parse(text):
    """-> dict(status, n (shard count, None if unknown), total, no_native, not_modelled, fuzzable, pending,
    causes: {cause: divergences})."""
    t = first_section(text)
    r = {'status': 'failed' if re.match(r'# fuzz [0-9a-f]{12} FAILED', t) or '- status: not run' in t else 'ok'}
    m = re.match(r'# fuzz [0-9a-f]{12} shard \d+/(\d+)', t) or re.search(r'start fuzz \d+ (\d+) at', t)
    r['n'] = int(m.group(1)) if m else None
    m = re.search(r'functions catalogued (\d+)', t)
    r['catalogued'] = int(m.group(1)) if m else None
    m = _SHARD.search(t)
    if m:
        r.update(total=int(m.group(1)), no_native=int(m.group(2)), not_modelled=int(m.group(3)),
                 fuzzable=int(m.group(4)))
    elif r['status'] == 'ok':
        r['status'] = 'failed'
    m = _PENDING.search(t)
    r['pending'] = int(m.group(1)) if m else 0
    m = re.search(r'```json fnvnx-causes\n(.*?)\n```', t, re.S)
    if m:
        r['causes'] = {c: v.get('divergences', 0) for c, v in json.loads(m.group(1)).get('causes', {}).items()}
    else:
        # the cumulative ALL row: "| ALL | f | e | hard | soft | gap | unset | cause n, cause n |"
        sec = t.split('## Verdicts, cumulative', 1)
        row = re.search(r'^\| ALL \|(.*)$', sec[1] if len(sec) > 1 else '', re.M)
        cells = row.group(1).split('|') if row else []
        r['causes'] = {}
        if len(cells) >= 7:
            for part in cells[6].split(','):
                part = part.strip()
                if part:
                    name, _, n = part.rpartition(' ')
                    r['causes'][name] = int(n)
    return r


def shard_reports(art, k):
    ref = 'refs/remotes/origin/reports/fz%d' % k
    names = git(art, 'ls-tree', '--name-only', ref)
    if names is None:
        return ref, []
    return ref, [n for n in names.split() if n.endswith('-fuzz.md')]


def measure(art, repo, shards=None):
    """shards None: the shard count of the newest report that names one (after a re-shard, fz1..N run with the
    new N and the older layout's reports are ignored)."""
    allreps = {}
    for k in range(1, (shards or 64) + 1):
        ref, names = shard_reports(art, k)
        if not names and shards is None:
            continue
        reps = []
        for n in names:
            h = n[:-len('-fuzz.md')]
            reps.append((head_time(repo, h), h, parse(git(art, 'show', '%s:%s' % (ref, n)) or '')))
        allreps[k] = reps
    if shards is None:
        named = sorted((t, r['n']) for reps in allreps.values() for t, _, r in reps if r['n'])
        shards = named[-1][1] if named else 26
    rows = []
    for k in range(1, shards + 1):
        reps = [x for x in allreps.get(k, []) if x[2]['n'] in (None, shards)]
        reps.sort(key=lambda x: x[0])
        if not reps:
            rows.append({'shard': k, 'newest': None, 'newest_status': 'missing', 'good': None})
            continue
        good = [x for x in reps if x[2]['status'] == 'ok']
        g = good[-1] if good else None
        rows.append({'shard': k, 'newest': reps[-1][1], 'newest_status': reps[-1][2]['status'],
                     'good': g[1] if g else None, 'r': g[2] if g else None})
    tot = dict(artifacts=0, not_fuzzable=0, unpublished=0, pending=0, fuzzable=0, with_native=0, no_evidence=0)
    by_cause = {}
    cats = [x[2]['catalogued'] for reps in allreps.values() for x in reps if x[2].get('catalogued')]
    per_shard = (max(cats) + shards - 1) // shards if cats else 0
    for row in rows:
        if row['newest_status'] != 'ok':
            tot['unpublished'] += 1
        r = row.get('r')
        if not r:
            # no verdict at all for this shard: all its functions are uncovered (V1 M1, M2)
            tot['not_fuzzable'] += per_shard
            tot['with_native'] += per_shard
            tot['no_evidence'] += 1
            continue
        tot['pending'] += r.get('pending', 0)
        native = r['total'] - r['no_native']
        tot['with_native'] += native
        tot['fuzzable'] += r['fuzzable']
        tot['not_fuzzable'] += native - r['fuzzable']
        for c, n in r['causes'].items():
            if causes.policy(c)[0] == 'artifact':
                tot['artifacts'] += n
                by_cause[c] = by_cause.get(c, 0) + n
    tot['coverage'] = round(100.0 * tot['fuzzable'] / tot['with_native'], 2) if tot['with_native'] else 0.0
    tot['metric'] = tot['artifacts'] + tot['not_fuzzable'] + tot['unpublished'] + tot['pending']
    tot['shards'] = shards
    return dict(format='fnvnx-s13-metric/1', totals=tot, artifact_causes=dict(sorted(by_cause.items(),
                key=lambda x: -x[1])), shards=[{k: v for k, v in row.items() if k != 'r'} for row in rows])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--artifacts', default=os.environ.get('FNVNX_ARTIFACTS', '/home/user/fnv-nx-build'))
    ap.add_argument('--repo', default=os.path.abspath(os.path.join(HERE, '../../../..')))
    ap.add_argument('--shards', type=int, default=None, help='shard count (default: the newest report\'s)')
    ap.add_argument('--fetch', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if a.fetch:
        specs = ['+refs/heads/reports/fz%d:refs/remotes/origin/reports/fz%d' % (k, k)
                 for k in range(1, (a.shards or 26) + 1)]
        subprocess.run(['git', '-C', a.artifacts, 'fetch', '-q', '--depth', '1', 'origin'] + specs, check=True)
    m = measure(a.artifacts, a.repo, a.shards)
    if a.json:
        print(json.dumps(m, indent=1))
        return 0
    t = m['totals']
    print('metric %d = artifacts %d + not fuzzable %d (coverage %.2f %%; %d shards without any verdict) + '
          'unpublished shards %d (of %d) + pending %d'
          % (t['metric'], t['artifacts'], t['not_fuzzable'], t['coverage'], t['no_evidence'], t['unpublished'],
             t['shards'], t['pending']))
    print('artifact causes: ' + ', '.join('%s %d' % kv for kv in m['artifact_causes'].items()))
    for s in m['shards']:
        print('  fz%-2d newest %s %-7s good %s' % (s['shard'], s['newest'], s['newest_status'], s['good']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
