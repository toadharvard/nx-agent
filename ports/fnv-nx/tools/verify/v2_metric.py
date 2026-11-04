#!/usr/bin/env python3
"""v2_metric.py - V2's convergence metric (D14), from tests/v2/ledger.tsv and the integration branch's history.

    python3 -I tools/verify/v2_metric.py [--ref origin/claude/port-nsw-un6t9s] [--list]

V2 is the second verifier. Its half of the merged claims is the compile, rules and ABI streams (S1-STL, S1-refs,
S1-fuzzA, S1-fuzzB, S1b, S11, S12) and the link streams (S2c-vt, S2c-abi, S2d, S4). V1 keeps the rest.

metric = unverified + open
  unverified  integration commits after BASELINE in V2's half (stream_of() below) that make a claim and have no
              row in V2's ledger or V1's (the working tree's and origin/claude/fnv-v1's; by merged hash), plus V2 rows with verdict `unverified`
  open        V2 rows with verdict refuted or partial (each row is one refutation case with a failing test)
The ledger format, the exemptions and the ledger checks are V1's (tools/verify/v1_metric.py), reused here. A commit
whose subject names no stream is assigned by the paths it touches (PATHS); a commit no rule assigns is V1's.
A broken ledger is an error (exit 2), not a lower number.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import v1_metric as v1  # noqa: E402  (git, VERDICTS; the same ledger format)

PORT = v1.PORT
LEDGER = os.path.join(PORT, 'tests', 'v2', 'ledger.tsv')
BASELINE = v1.BASELINE
EXEMPT = v1.EXEMPT + ('ports/fnv-nx/tests/v2/',)

# subject token -> V2 stream. Subjects are "fnv-nx <Stream>: ..." or "fnv-nx fnv-<branch>: adopt the stream work".
STREAMS = {
    'S1-STL': 'S1-STL', 'fnv-s1-stl': 'S1-STL',
    'S1-refs': 'S1-refs', 'fnv-s1-refs': 'S1-refs',
    'S1-fuzzA': 'S1-fuzzA', 'fnv-s1-fuzz-a': 'S1-fuzzA',
    'S1-fuzzB': 'S1-fuzzB', 'fnv-s1-fuzz-b': 'S1-fuzzB',
    'S1b': 'S1b', 'fnv-s1b': 'S1b',
    'S11': 'S11', 'fnv-s11': 'S11',
    'S12': 'S12', 'fnv-s12-ordering': 'S12', 'fnv-s12': 'S12',
    'S2c-vt': 'S2c-vt', 'S2c': 'S2c-vt', 'fnv-s2c': 'S2c-vt',
    'S2c-abi': 'S2c-abi', 'fnv-s2c-abi': 'S2c-abi',
    'S2d': 'S2d', 'fnv-s2d-bodies': 'S2d', 'fnv-s2d': 'S2d',
    'S4': 'S4', 'S4-runtime': 'S4', 'fnv-s4': 'S4',
}
# fallback for subjects without a stream token: path prefix -> stream
PATHS = (
    ('ports/fnv-nx/tests/s1b/', 'S1b'), ('ports/fnv-nx/tests/s11/', 'S11'), ('ports/fnv-nx/tests/s12/', 'S12'),
    ('ports/fnv-nx/tests/s2d/', 'S2d'), ('ports/fnv-nx/tests/s4/', 'S4'),
)
TOKEN = re.compile(r'^(?:fnv-nx\s+)?([A-Za-z0-9-]+)(?:\s+revert)?:')


def stream_of(subject, files):
    m = TOKEN.match(subject)
    if m and m.group(1) in STREAMS:
        return STREAMS[m.group(1)]
    if m:                          # a named stream that is not V2's
        return None
    for prefix, s in PATHS:
        if files and all(f.startswith(prefix) for f in files):
            return s
    return None


def read_ledger(path, testdir, check=True):
    rows, errs = [], []
    if not os.path.isfile(path):
        return rows, ['%s missing' % path] if check else []
    with open(path) as f:
        lines = [l.rstrip('\n') for l in f if l.strip() and not l.startswith('#')]
    head = lines[0].split('\t')
    for n, l in enumerate(lines[1:], 2):
        c = l.split('\t')
        if len(c) != len(head):
            errs.append('row %d: %d columns, want %d' % (n, len(c), len(head)))
            continue
        r = dict(zip(head, c))
        if r['verdict'] not in v1.VERDICTS:
            errs.append('%s: verdict %r' % (r['id'], r['verdict']))
        if r['verdict'] != 'unverified' and not os.path.isfile(os.path.join(testdir, r['test'])):
            errs.append('%s: test %s does not exist' % (r['id'], r['test']))
        if r['verdict'] == 'fixed' and r['fix'] == '-':
            errs.append('%s: fixed without a fix commit' % r['id'])
        rows.append(r)
    ids = [r['id'] for r in rows]
    errs += ['%s: duplicate id' % i for i in sorted({i for i in ids if ids.count(i) > 1})]
    return rows, (errs if check else [])


def claims(ref):
    out = []
    for line in v1.git('log', '--format=%h %s', '--no-merges', '%s..%s' % (BASELINE, ref)).splitlines():
        h, subj = line.split(' ', 1)
        files = [f for f in v1.git('show', '--format=', '--name-only', h).splitlines() if f]
        if files and all(f.startswith(EXEMPT) for f in files):
            continue
        s = stream_of(subj, files)
        if s:
            out.append((h, s, subj))
    return out


def v1_branch_rows(ref):
    """V1's ledger on its own branch (rows V1 wrote before the integration branch merged them)."""
    try:
        text = v1.git('show', '%s:ports/fnv-nx/tests/v1/ledger.tsv' % ref)
    except Exception:
        return []
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith('#')]
    head = lines[0].split('\t')
    return [dict(zip(head, l.split('\t'))) for l in lines[1:] if len(l.split('\t')) == len(head)]


def merged_set(rows):
    seen = set()
    for r in rows:
        for h in r['merged'].split(','):
            if h != '-':
                try:
                    seen.add(v1.git('rev-parse', '--short=7', h + '^{commit}').strip())
                except Exception:
                    seen.add(h[:7])
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default='origin/claude/port-nsw-un6t9s')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--v1-ref', default='origin/claude/fnv-v1', help="V1's branch, whose ledger rows count as verified")
    a = ap.parse_args()
    rows, errs = read_ledger(LEDGER, os.path.join(PORT, 'tests', 'v2'))
    if errs:
        print('ledger errors:\n  ' + '\n  '.join(errs))
        return 2
    v1rows, _ = read_ledger(v1.LEDGER, os.path.join(PORT, 'tests', 'v1'), check=False)
    v1rows += v1_branch_rows(a.v1_ref)
    seen = merged_set(rows) | merged_set(v1rows)
    missing = [(h, s, subj) for h, s, subj in claims(a.ref) if h[:7] not in seen]
    unver = [r for r in rows if r['verdict'] == 'unverified']
    opened = [r for r in rows if r['verdict'] in ('refuted', 'partial')]
    total = len(missing) + len(unver) + len(opened)
    print('v2 metric: %d (unverified merges %d, unverified rows %d, open refutations %d)'
          % (total, len(missing), len(unver), len(opened)))
    if a.list:
        for h, s, subj in missing:
            print('  unverified %s %-8s %s' % (h, s, subj[:100]))
        for r in unver + opened:
            print('  %-10s %-14s %s' % (r['verdict'], r['id'], r['claim'][:90]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
