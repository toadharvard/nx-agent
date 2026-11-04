#!/usr/bin/env python3
"""test_s13_metric.py - V1 (D14, queue item 3: audit the stream metrics) against S13's tools/difffuzz/metric.py
(7af7f55, claude/fnv-s13): the metric must not drop because evidence stopped being counted.

    python3 -I tests/v1/test_s13_metric.py [--metric <path/to/metric.py>]   -> one line per case, exit 1 on a defect
    (default: tools/difffuzz/metric.py of this checkout; skips when it does not exist)

Each case builds two worlds of synthetic shard reports (counts and made-up cause names only) in throwaway git
repositories: a baseline, and the same shard after something that is not a fix. The metric of the second world
must not be lower than the baseline's.
  M1  the shard's branch disappears (reports/fz<k> not published, or deleted)
  M2  the shard's only report is FAILED (no good report to fall back on)
  M3  the newest head's run hit its deadline: every fuzzable function pending, so the causes block (verdicts valid
      for this head) is empty
  M4  note: causes.POLICY reclassifies an artifact cause (here oracle-vcall -> by-design); reports unchanged
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.normpath(os.path.join(HERE, '..', '..', 'tools', 'difffuzz', 'metric.py'))


def git(d, *a, env=None):
    subprocess.run(['git', '-C', d] + list(a), check=True, capture_output=True, env=env)


def report(h, total, nonat, nm, fz, pending, causes):
    s = ['# fuzz %s shard 1/1' % h, '',
         '- functions catalogued 100; in this shard %d: no native object %d, control flow not modelled '
         '(indirect calls/jumps, unresolved relocations) %d, fuzzable %d (no native definition in the link: 0)'
         % (total, nonat, nm, fz),
         '- this head: fuzzed %d, verdict reused from an earlier head (key unchanged) 0, pending %d (key changed %d, '
         'never fuzzed 0)' % (fz - pending, pending, pending), '',
         '## Verdicts, cumulative', '', '| stream | functions | executions | hard | soft | oracle gap | not set up | '
         'causes |', '|---|---:|---:|---:|---:|---:|---:|---|',
         '| ALL | %d | 9 | 1 | 1 | 1 | 0 | %s |' % (fz - pending, ', '.join('%s %d' % kv for kv in causes.items())), '',
         '```json fnvnx-causes', json.dumps({'format': 'fnvnx-causes/1',
                                            'causes': {c: {'divergences': n} for c, n in causes.items()}}), '```']
    return '\n'.join(s) + '\n'


def world(tmp, name, shard_files):
    """shard_files: {k: [(head_index, text)] or None (branch missing)} -> (art, repo, heads)"""
    base = os.path.join(tmp, name)
    repo, art = os.path.join(base, 'repo'), os.path.join(base, 'art')
    os.makedirs(repo)
    os.makedirs(art)
    for d in (repo, art):
        git(d, 'init', '-q')
        git(d, 'config', 'user.email', 't@invalid')
        git(d, 'config', 'user.name', 't')
    heads = []
    for i in range(2):
        env = dict(os.environ, GIT_COMMITTER_DATE='2026-11-04T1%d:00:00Z' % i, GIT_AUTHOR_DATE='2026-11-04T10:00:00Z')
        git(repo, 'commit', '-q', '--allow-empty', '-m', 'h%d' % i, env=env)
        heads.append(subprocess.run(['git', '-C', repo, 'rev-parse', 'HEAD'], capture_output=True,
                                    text=True).stdout.strip()[:12])
    for k, files in shard_files.items():
        if files is None:
            continue
        git(art, 'checkout', '-q', '--orphan', 'reports/fz%d' % k)
        subprocess.run(['git', '-C', art, 'rm', '-rfq', '.'], capture_output=True)
        for hi, text in files:
            with open(os.path.join(art, heads[hi] + '-fuzz.md'), 'w') as f:
                f.write(text(heads[hi]))
        git(art, 'add', '.')
        git(art, 'commit', '-q', '-m', 'r')
        git(art, 'update-ref', 'refs/remotes/origin/reports/fz%d' % k, 'HEAD')
    return art, repo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--metric', default=DEFAULT)
    a = ap.parse_args()
    if not os.path.isfile(a.metric):
        print('skip: no %s' % a.metric)
        return 0
    sys.path.insert(0, os.path.dirname(a.metric))
    spec = importlib.util.spec_from_file_location('s13_metric', a.metric)
    metric = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metric)
    causes = sys.modules['causes']
    art_cause = next(c for c in ('oracle-vcall', 'new-null', 'local-static') if causes.policy(c)[0] == 'artifact')

    try:
        tmp = tempfile.mkdtemp(prefix='v1-s13metric-')
    except OSError as e:
        print('mktemp failed: %s' % e)
        return 2
    if not tmp or not os.path.isdir(tmp):
        print('mktemp failed')
        return 2
    bad = []

    def case(cid, ok, what, note=False):
        print('%s: %s - %s' % (cid, 'ok' if ok else ('note' if note else 'DEFECT'), what))
        if not ok and not note:
            bad.append(cid)

    def m(name, files, shards=2):
        art, repo = world(tmp, name, files)
        return metric.measure(art, repo, shards)['totals']['metric']

    try:
        good = lambda h: report(h, 600, 0, 100, 500, 0, {art_cause: 300})        # noqa: E731
        other = lambda h: report(h, 10, 0, 0, 10, 0, {})                          # noqa: E731
        base = m('base', {1: [(0, good)], 2: [(0, other)]})
        gone = m('gone', {1: None, 2: [(0, other)]})
        case('M1', gone >= base, 'shard 1 branch missing: metric %d -> %d (its %d artifacts and 100 not fuzzable '
             'replaced by 1 unpublished)' % (base, gone, 300))
        failed = lambda h: '# fuzz %s FAILED (rc 3)\n\n```\nfuzz_job: no prepared tree\n```\n' % h  # noqa: E731
        onlyf = m('onlyfailed', {1: [(0, failed)], 2: [(0, other)]})
        case('M2', onlyf >= base, 'shard 1 only FAILED: metric %d -> %d' % (base, onlyf))
        timeout = lambda h: report(h, 600, 0, 100, 500, 500, {})                  # noqa: E731
        late = m('deadline', {1: [(0, good), (1, timeout)], 2: [(0, other)]})
        case('M3', late >= base, 'shard 1 newest head: 500 of 500 fuzzable pending, causes block empty: metric '
             '%d -> %d (pending functions are counted in no term)' % (base, late))
        saved = dict(causes.POLICY) if hasattr(causes, 'POLICY') else None
        if saved is not None and art_cause in causes.POLICY:
            v = causes.POLICY[art_cause]
            causes.POLICY[art_cause] = ('by-design',) + tuple(v[1:]) if isinstance(v, tuple) else 'by-design'
            try:
                rec = m('reclass', {1: [(0, good)], 2: [(0, other)]})
            except Exception as ex:  # noqa: BLE001
                rec = None
                print('M4: skip - POLICY shape not understood (%s)' % ex)
            finally:
                causes.POLICY.clear()
                causes.POLICY.update(saved)
            if rec is not None:
                case('M4', rec >= base, '%s reclassified by-design, reports unchanged: metric %d -> %d (a POLICY '
                     'edit lowers the metric with no harness change; state it in the commit)' % (art_cause, base, rec),
                     note=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
