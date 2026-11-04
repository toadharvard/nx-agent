#!/usr/bin/env python3
"""test_perf_commits.py - every commit that render/native/perf/optimizations.json (and report.json's copy) names
must be a commit in this repository's history (an ancestor of HEAD), not a hash from an agent's local branch that
was later cherry-picked under another id (V1 D3/D3b).

    python3 -I tests/s6n/test_perf_commits.py      -> one line per bad record, exit 1 when any
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PERF = os.path.join(HERE, '..', '..', 'render', 'native', 'perf')


def git(*a):
    return subprocess.run(['git', '-C', HERE] + list(a), capture_output=True, text=True)


def main():
    bad = 0
    rows = json.load(open(os.path.join(PERF, 'optimizations.json')))
    rep = json.load(open(os.path.join(PERF, 'report.json'))).get('optimizations') or []
    for src, rs in (('optimizations.json', rows), ('report.json', rep)):
        for r in rs:
            c = r.get('commit')
            if not c or c == 'baseline' or len(c) < 7:
                continue
            ok = git('merge-base', '--is-ancestor', c, 'HEAD').returncode == 0
            if not ok:
                bad += 1
                print('%s: %s (%s) is not in HEAD\'s history' % (src, c, r.get('name', '')[:60]))
    n = len(rows) + len(rep)
    print('perf commits: %d records, %d not in history' % (n, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
