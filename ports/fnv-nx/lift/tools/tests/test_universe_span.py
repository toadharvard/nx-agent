#!/usr/bin/env python3
"""universe.tsv extent is the SPAN: a row [entry, kind, extent, flags] covers [entry, entry + extent), and every byte that the entry reaches (its ranges.tsv ranges) lies inside it.
    test_universe_span.py IMAGE_FACTS_DIR [...]       (a directory that holds universe.tsv and ranges.tsv, as modlift writes it; exit 0 = every row of every directory holds)"""
import sys


def rows(path):
    return [l.rstrip('\n').split('\t') for l in open(path) if l.strip() and l[0] != '#']


bad = []
for d in sys.argv[1:]:
    uni = {int(r[0], 16): int(r[2]) for r in rows(d + '/universe.tsv')}
    rng = {int(r[0], 16): [tuple(int(x, 16) for x in p.split('-')) for p in r[1].split(',')] for r in rows(d + '/ranges.tsv')}
    for e, x in uni.items():
        if x <= 0: bad.append('%s: entry %08x has extent %d' % (d, e, x))
        for a, b in rng.get(e, ()):
            if not (e <= a < b <= e + x): bad.append('%s: entry %08x span [%08x, %08x) does not hold the reached range %08x-%08x' % (d, e, e, e + x, a, b))
    for e in rng:
        if e not in uni: bad.append('%s: ranges.tsv entry %08x has no universe.tsv row' % (d, e))
    print('%s: %d rows, %d ranges rows' % (d, len(uni), len(rng)))
print('\n'.join(bad[:40])); print('test_universe_span: %s (%d violations)' % ('FAIL' if bad else 'ok', len(bad)))
sys.exit(1 if bad else 0)
