"""summary.py - counts of a campaign: status, executions, divergences by kind and cause."""
import collections
import json
import sys

st, kinds, causes, execs, n = collections.Counter(), collections.Counter(), collections.Counter(), 0, 0
for line in open(sys.argv[1]):
    r = json.loads(line)
    n += 1
    s = r.get('status', '?')
    st[s.split(':')[0] if s != 'ok' else 'ok'] += 1
    execs += r.get('execs', 0)
    for d in r.get('divergences', []):
        kinds[d['kind']] += 1
        causes[d.get('cause')] += 1
print('functions %d  execs %d' % (n, execs))
print('status', dict(st))
print('kinds', dict(kinds))
print('causes', dict(causes))
