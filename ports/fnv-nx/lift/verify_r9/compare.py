#!/usr/bin/env python3
"""Compare verdicts.tsv (mine) with memclass entries.tsv (theirs: private = whole==0 and events_escaping==0)."""
import csv, collections, sys
V = '/home/user/l3work/verify_r9/verdicts.tsv'
T = '/home/user/l3facts/memclass/entries.tsv'
mine = {r['entry']: r for r in csv.DictReader(open(V), delimiter='\t')}
rows = list(csv.DictReader(open(T), delimiter='\t'))
strict = '--strict' in sys.argv
def theirs(r):
    ok = r['whole'] == '0' and r['events_escaping'] == '0'
    if strict:
        ok = ok and r['stack-escaped.L'] == '0' and r['stack-escaped.S'] == '0'
    return ok
both = mp = tp = 0
mine_only = collections.Counter()
their_only = []
cond = collections.Counter()
for r in rows:
    m = mine.get(r['entry'])
    if not m:
        continue
    a, b = m['priv'] == '1', theirs(r)
    mp += a
    tp += b
    both += a and b
    if a and not b:
        mine_only[(r['whole'], r['events_escaping'] != '0', r['awhy'])] += 1
    if b and not a:
        their_only.append((r, m))
        for c in m['conds']:
            cond[c] += 1
print('entries', len(rows), 'mine', mp, 'theirs', tp, 'both', both, 'mine_only', sum(mine_only.values()), 'theirs_only', len(their_only))
print('mine-only reasons (whole, events>0, awhy):', mine_only.most_common(5))
first = collections.Counter(m['conds'][0] for r, m in their_only)
print('theirs-only by lowest cond', first, 'any cond', cond)
tags = collections.Counter(m['first_events'].split(';')[0].split('/')[1] for r, m in their_only)
print(tags.most_common(30))
with open('/home/user/l3work/verify_r9/theirs_only.tsv', 'w') as f:
    for r, m in their_only:
        f.write('\t'.join([r['entry'], r['kind'], m['root'], m['conds'], m['first_events']]) + '\n')
