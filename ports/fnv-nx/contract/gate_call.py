"""gate_call - direct call/tail targets of the exe function versus each native copy (L1 CONTRACT).

Native targets are `calls` plus any 8-hex-digit address inside an `other` symbol name that the exe function also
calls (bridges).  Several native copies of one address: each is judged, the worst verdict stands.
"""
import collections, re

NAME = 'call'
ORDER = ('none', 'equal', 'subset', 'superset', 'different')   # best .. worst
AN = re.compile(r'([0-9a-f]{8})')


def run(facts, obj, link):
    copies = collections.defaultdict(list)
    for (_, a), v in obj.items():
        if a != 'ztv' and a in facts['fn']:
            copies[a].append(v)
    counts, rows = collections.Counter(), []
    for a, vs in sorted(copies.items()):
        f = facts['fn'][a]
        end = a + f['size']
        x = {t for _, t in f['calls'] + f['tails'] if not (a <= t < end)}
        worst = (-1, None, None)
        for v in vs:
            n = {t for t in v[2] if not (a <= t < end)}
            for s in v[3]:
                n |= {int(m, 16) for m in AN.findall(s)} & x
            if not x and not n and not v[3]:
                k = 0
            elif x == n:
                k = 1
            elif n < x:
                k = 2
            else:
                k = 3 if n > x else 4
            if k > worst[0]:
                worst = (k, x, n)
        k, _, n = worst
        counts[ORDER[k]] += 1
        if ORDER[k] == 'different':
            rows.append((a, 'different', 'exe_only=%d native_only=%d first=0x%08x' % (len(x - n), len(n - x), min(x ^ n))))
    return {'counts': dict(counts), 'rows': rows, 'fail': ['different'], 'judged': len(copies)}
