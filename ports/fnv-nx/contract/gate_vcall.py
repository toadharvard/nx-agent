"""gate_vcall - virtual-call displacements of the exe function versus each native copy (L1 CONTRACT).

Exe side: icalls of kind 'mem' (disp) and kind 'reg' with a known displacement; functions without any are skipped.
Both sides are sorted; equal lengths are compared pairwise.  Several native copies: worst verdict stands.
"""
import collections

import store

NAME = 'vcall'
ORDER = ('equal', 'delta_0_or_4', 'count_differs', 'other')   # best .. worst


def run(facts, obj, link):
    copies = collections.defaultdict(list)
    for k, v in obj.items():
        a = k[1]
        if store.is_function_key(k) and a in facts['fn']:
            copies[a].append(sorted(v[4]))
    counts, rows, delta = collections.Counter(), [], collections.Counter()
    for a, vs in sorted(copies.items()):
        x = sorted(d for _, k, _, d in facts['fn'][a]['icalls'] if k in ('mem', 'reg') and d is not None)
        if not x:
            continue
        worst = (-1, '')
        for n in vs:
            if len(x) != len(n):
                k, det = 2, 'exe=%d native=%d' % (len(x), len(n))
            else:
                ds = [b - c for c, b in zip(x, n)]
                delta.update(ds)
                k = 0 if not any(ds) else 1 if all(q in (0, 4) for q in ds) else 3
                bad = [q for q in ds if q not in (0, 4)]
                det = 'sites=%d bad=%d first=%d' % (len(ds), len(bad), bad[0] if bad else 0)
            if k > worst[0]:
                worst = (k, det)
        counts[ORDER[worst[0]]] += 1
        if ORDER[worst[0]] == 'other':
            rows.append((a, 'other', worst[1]))
    judged = sum(counts.values())
    counts = dict(counts)
    for d, n in delta.most_common(10):
        counts['delta:%d' % d] = n
    return {'counts': counts, 'rows': rows, 'fail': ['other'], 'judged': judged}
