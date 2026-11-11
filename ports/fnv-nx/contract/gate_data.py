"""gate_data - .data references of the exe function versus each native copy (L1 CONTRACT).

Addresses are canonicalised to the start of the containing variable (sorted __fnv_XXXXXXXX of out/aliases.ld).
The /GS cookie (`___security_cookie`, its address from the N1 symbol map: facts['cookie']) is the compiler's own
check, not a reference of the source; it is dropped from both sides.
Several native copies of one address: each is judged, the worst verdict stands.
"""
import bisect, collections, os, re

import store

NAME = 'data'
ORDER = ('none', 'equal', 'subset', 'superset', 'different')   # best .. worst
AL = re.compile(r'__fnv_([0-9a-f]{8}) =')


def verdict(x, n):
    if not x and not n:
        return 'none'
    if x == n:
        return 'equal'
    if n < x:
        return 'subset'
    return 'superset' if n > x else 'different'


def run(facts, obj, link):
    al = []
    with open(os.path.join(link, 'out', 'aliases.ld')) as fh:
        for ln in fh:
            m = AL.match(ln)
            if m:
                al.append(int(m[1], 16))
    al.sort()
    va, vs = facts['secs']['.data'][:2]

    def canon(t):
        i = bisect.bisect_right(al, t) - 1
        return al[i] if i >= 0 else t

    def pick(addrs):
        return {canon(t) for t in addrs if va <= t < va + vs}

    copies = collections.defaultdict(list)
    for k, v in obj.items():
        a = k[1]
        if store.is_function_key(k) and a in facts['fn']:
            copies[a].append(pick(v[1]))
    exe = {a: pick(t for _, t in facts['fn'][a]['data']) for a in copies}
    miss = collections.Counter()
    for a, ns in copies.items():
        for n in ns:
            miss.update(exe[a] - n)
    cookie = canon(facts['cookie'])
    counts, rows = collections.Counter(), []
    for a, ns in sorted(copies.items()):
        x = exe[a] - {cookie}
        vs_ = [(ORDER.index(verdict(x, n - {cookie})), n - {cookie}) for n in ns]
        i, n = max(vs_, key=lambda p: p[0])
        v = ORDER[i]
        counts[v] += 1
        if v in ('superset', 'different'):
            first = min(x ^ n)
            rows.append((a, v, 'exe_only=%d native_only=%d first=0x%08x' % (len(x - n), len(n - x), first)))
    counts = dict(counts)
    counts['cookie_refs_ignored'] = miss[cookie]
    return {'counts': counts, 'rows': rows, 'fail': ['superset', 'different'], 'judged': len(copies)}
