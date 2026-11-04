#!/usr/bin/env python3
"""v1_metric.py - V1's convergence metric (D14), from tests/v1/ledger.tsv and the integration branch's history.

    python3 -I tools/verify/v1_metric.py [--ref origin/claude/port-nsw-un6t9s] [--list]

metric = unverified + open
  unverified  integration commits after BASELINE that make a claim and have no ledger row (by merged hash), plus
              ledger rows with verdict `unverified`
  open        ledger rows with verdict refuted or partial (each row is one refutation case with a failing test)
A commit makes no claim when it only touches the journal (JOURNAL.md, journal/inbox/) or V1's own files
(tests/v1/, tools/verify/): V1 cannot verify itself. The ledger is checked too: every refuted/partial/fixed row
names a test that exists, and a `fixed` row names its fix commit. A broken ledger is an error (exit 2), not a
lower number.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..'))
LEDGER = os.path.join(PORT, 'tests', 'v1', 'ledger.tsv')
BASELINE = 'b6585b8'          # the integration head of V1's round 2 (v1-001); every later merge is counted
EXEMPT = ('ports/fnv-nx/JOURNAL.md', 'ports/fnv-nx/journal/', 'ports/fnv-nx/tests/v1/', 'ports/fnv-nx/tools/verify/')
VERDICTS = {'confirmed', 'refuted', 'partial', 'fixed', 'unverified'}


def git(*a):
    return subprocess.run(['git', '-C', PORT] + list(a), capture_output=True, text=True, check=True).stdout


def ledger():
    rows, errs = [], []
    with open(LEDGER) as f:
        lines = [l.rstrip('\n') for l in f if l.strip() and not l.startswith('#')]
    head = lines[0].split('\t')
    for n, l in enumerate(lines[1:], 2):
        c = l.split('\t')
        if len(c) != len(head):
            errs.append('row %d: %d columns, want %d' % (n, len(c), len(head)))
            continue
        r = dict(zip(head, c))
        if r['verdict'] not in VERDICTS:
            errs.append('%s: verdict %r' % (r['id'], r['verdict']))
        if r['verdict'] != 'unverified' and not os.path.isfile(os.path.join(PORT, 'tests', 'v1', r['test'])):
            errs.append('%s: test %s does not exist' % (r['id'], r['test']))
        if r['verdict'] == 'fixed' and r['fix'] == '-':
            errs.append('%s: fixed without a fix commit' % r['id'])
        rows.append(r)
    ids = [r['id'] for r in rows]
    errs += ['%s: duplicate id' % i for i in sorted({i for i in ids if ids.count(i) > 1})]
    return rows, errs


def claims(ref):
    out = []
    for line in git('log', '--format=%h %s', '--no-merges', '%s..%s' % (BASELINE, ref)).splitlines():
        h, subj = line.split(' ', 1)
        files = [f for f in git('show', '--format=', '--name-only', h).splitlines() if f]
        if files and all(f.startswith(EXEMPT) for f in files):
            continue
        out.append((h, subj))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default='origin/claude/port-nsw-un6t9s')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    rows, errs = ledger()
    if errs:
        print('ledger errors:\n  ' + '\n  '.join(errs))
        return 2
    seen = set()
    for r in rows:
        if r['merged'] != '-':
            seen.add(git('rev-parse', '--short=7', r['merged'] + '^{commit}').strip())
    missing = [(h, s) for h, s in claims(a.ref) if h[:7] not in seen]
    unver = [r for r in rows if r['verdict'] == 'unverified']
    opened = [r for r in rows if r['verdict'] in ('refuted', 'partial')]
    total = len(missing) + len(unver) + len(opened)
    print('v1 metric: %d (unverified merges %d, unverified rows %d, open refutations %d)'
          % (total, len(missing), len(unver), len(opened)))
    if a.list:
        for h, s in missing:
            print('  unverified %s %s' % (h, s[:100]))
        for r in unver + opened:
            print('  %-10s %-12s %s' % (r['verdict'], r['id'], r['claim'][:90]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
