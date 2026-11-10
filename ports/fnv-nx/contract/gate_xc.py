"""gate_xc - does every C++ static initializer (facts['xc']) have an address-bound native function? (L1 CONTRACT)"""
import collections

NAME = 'xc'


def run(facts, obj, link):
    bound = {a for (_, a) in obj if a != 'ztv'}
    counts, rows = collections.Counter(), []
    for a in facts['xc']:
        if a in bound:
            counts['native'] += 1
            continue
        v = 'missing_decomp' if facts['fn'].get(a, {}).get('decomp') else 'missing_other'
        counts[v] += 1
        rows.append((a, v, 'no native body'))
    return {'counts': dict(counts), 'rows': rows, 'fail': ['missing_decomp', 'missing_other']}
