#!/usr/bin/env python3
"""tests/s13/test_metric.py - tools/difffuzz/metric.py on synthetic shard reports in a throwaway git repository
(counts and cause names only, no decomp content)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', 'tools', 'difffuzz'))
import metric  # noqa: E402

fails = 0


def check(name, cond, info=''):
    global fails
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  ' + str(info)))
    fails += 0 if cond else 1


def report(h, k, total, nonat, nm, fz, causes_block=None, all_row=''):
    s = ['# fuzz %s shard %d/2' % (h, k), '',
         '- functions catalogued 100; in this shard %d: no native object %d, control flow not modelled '
         '(unresolved relocations, indirect calls without the player\'s exe) %d, fuzzable %d (no native definition '
         'in the link: 0)' % (total, nonat, nm, fz),
         '- this head: fuzzed 1, verdict reused from an earlier head (key unchanged) 0, pending 3 (key changed 0)', '',
         '## Verdicts, cumulative', '', '| stream | functions | executions | hard | soft | oracle gap | not set up | '
         'causes |', '|---|---:|---:|---:|---:|---:|---:|---|',
         '| ALL | 9 | 9 | 1 | 1 | 1 | 0 | %s |' % all_row, '']
    if causes_block is not None:
        s += ['```json fnvnx-causes', json.dumps({'format': 'fnvnx-causes/1', 'causes': causes_block}), '```']
    return '\n'.join(s) + '\n'


def git(d, *a, env=None):
    subprocess.run(['git', '-C', d] + list(a), check=True, capture_output=True, env=env)


with tempfile.TemporaryDirectory() as tmp:
    repo, art = os.path.join(tmp, 'repo'), os.path.join(tmp, 'art')
    os.makedirs(repo)
    os.makedirs(art)
    for d in (repo, art):
        git(d, 'init', '-q')
        git(d, 'config', 'user.email', 't@invalid')
        git(d, 'config', 'user.name', 't')
    heads = []
    for i in range(3):     # three heads, increasing commit time
        env = dict(os.environ, GIT_COMMITTER_DATE='2026-11-04T1%d:00:00Z' % i, GIT_AUTHOR_DATE='2026-11-04T10:00:00Z')
        git(repo, 'commit', '-q', '--allow-empty', '-m', 'h%d' % i, env=env)
        heads.append(subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], capture_output=True,
                                    text=True).stdout.strip()[:12])
    h0, h1, h2 = heads
    files = {
        1: {h0: report(h0, 1, 10, 1, 1, 8, all_row='oracle-vcall 5, falloff? 2'),
            h1: report(h1, 1, 10, 1, 0, 9, causes_block={'oracle-vcall': {'divergences': 3},
                                                          'native-trap': {'divergences': 4},
                                                          'new-null': {'divergences': 2}})},
        2: {h1: report(h1, 2, 20, 0, 2, 18, all_row='uninit-both 1, bool-repr 7'),
            h2: '# fuzz %s FAILED (rc 3)\n\n```\nfuzz_job: no prepared tree\n```\n' % h2},
    }
    for k, fs in files.items():
        git(art, 'checkout', '-q', '--orphan', 'reports/fz%d' % k)
        subprocess.run(['git', '-C', art, 'rm', '-rfq', '.'], capture_output=True)
        for h, text in fs.items():
            open(os.path.join(art, h + '-fuzz.md'), 'w').write(text)
        git(art, 'add', '.')
        git(art, 'commit', '-q', '-m', 'r')
        git(art, 'update-ref', 'refs/remotes/origin/reports/fz%d' % k, 'HEAD')
    m = metric.measure(art, repo, 2)
    t = m['totals']
    s1, s2 = m['shards']
    check('newest report by head commit time', s1['newest'] == h1 and s2['newest'] == h2, m['shards'])
    check('a FAILED newest report counts as unpublished', t['unpublished'] == 1 and s2['good'] == h1, t)
    check('artifacts from the causes block (oracle-vcall 3 + new-null 2) and the ALL row (uninit-both 1)',
          t['artifacts'] == 6 and m['artifact_causes'] == {'oracle-vcall': 3, 'new-null': 2, 'uninit-both': 1}, m)
    check('not fuzzable = with a native object - fuzzable (0 + 2)', t['not_fuzzable'] == 2, t)
    check('coverage over functions with a native object (27/29)', abs(t['coverage'] - 93.1) < 0.01, t)
    check('metric is the sum', t['metric'] == 6 + 2 + 1, t)
    check('a missing shard branch is unpublished', metric.measure(art, repo, 3)['totals']['unpublished'] == 2)
    r = metric.parse('# fuzz %s FAILED (rc 3)\n\n# fuzz %s shard 1/2\n' % (h0, h0))
    check('a FAILED header with an older run appended is failed', r['status'] == 'failed', r)

print('%d failures' % fails)
sys.exit(1 if fails else 0)
