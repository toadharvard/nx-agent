#!/usr/bin/env python3
"""recheck_summary.py - which root causes a replay on another object set removed.

    recheck_summary.py <tag> <campaign dir> [...]

For every saved divergence (div/*.json) the cause recorded when it was found (re-classified statically like
report.py) is paired with its replay status in recheck_<tag>.jsonl: still / gone / error (the function cannot be set
up on the new objects: unit not compiled, entry missing). Prints per cause: total, gone, still, error, and the
still-reproducing examples.
"""
import collections
import json
import os
import sys

import report


def main():
    tag, dirs = sys.argv[1], sys.argv[2:]
    tab = collections.defaultdict(collections.Counter)
    still = collections.defaultdict(list)
    errs = collections.Counter()
    for d in dirs:
        rp = os.path.join(d, 'recheck_%s.jsonl' % tag)
        base = os.path.join(d, 'recheck.jsonl')
        prev = {}
        if os.path.exists(base):
            for line in open(base):
                x = json.loads(line)
                prev[x['file']] = x
        for line in open(rp):
            x = json.loads(line)
            if prev.get(x['file'], {}).get('status') == 'gone':
                continue            # was a harness limit on the old objects already
            rec = json.load(open(os.path.join(d, 'div', x['file'])))
            rec['_now'] = prev.get(x['file'], {}).get('divergence')
            cause = report.reclassify(rec)
            if x['status'] == 'still' and (x.get('divergence') or {}).get('soft'):
                x['status'] = 'soft'        # reproduces only as a representation-level verdict now
            tab[cause][x['status']] += 1
            tab[cause]['total'] += 1
            if x['status'] == 'still':
                still[cause].append((rec['address'], rec['symbol'], (x.get('divergence') or {}).get('kind')))
            elif x['status'] == 'error':
                errs[(x.get('divergence') or {}).get('error', '')[:60]] += 1
    print('%-16s %6s %6s %6s %6s %6s' % ('cause', 'total', 'gone', 'soft', 'still', 'error'))
    for c in sorted(tab, key=lambda c: -tab[c]['total']):
        t = tab[c]
        print('%-16s %6d %6d %6d %6d %6d' % (c, t['total'], t['gone'], t['soft'], t['still'], t['error']))
    print('\nstill reproducing:')
    for c, xs in sorted(still.items()):
        print('  %s: %s' % (c, ', '.join('%s %s (%s)' % (a, s[:40], k) for a, s, k in xs[:5])))
    print('\nsetup errors:', dict(errs.most_common(6)))


if __name__ == '__main__':
    main()
