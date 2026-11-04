"""pick_bridges.py - campaign targets: the callers of S2's ABI-checked call references (bridges and the rest).

    pick_bridges.py [--abi out/abi.tsv] [--cap N] [--verdicts bridge,manual,x86-inconsistent]

For every row of the link's abi.tsv (object, undefined name, address, verdict, classes) the oracle functions of that
unit whose direct calls include the address are targets: when they run, the call goes through the generated bridge
(or the raw call for manual / x86-inconsistent pairs) and difffuzz compares the callee's logical arguments decoded
per the definition's signature on both machines. Round robin over (verdict, first class) up to --cap.
"""
import argparse
import collections
import json
import os
import sys

import config
import pick


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--abi', default=str(config.LINK / 'out/abi.tsv'))
    ap.add_argument('--catalog', default=str(config.WORK / 'catalog.jsonl'))
    ap.add_argument('--cap', type=int, default=400)
    ap.add_argument('--verdicts', default='bridge,manual,x86-inconsistent')
    ap.add_argument('--max-size', type=int, default=1500)
    a = ap.parse_args()
    want = set(a.verdicts.split(','))
    pairs = collections.defaultdict(set)          # (source, addr) -> {(verdict, class)}
    for line in open(a.abi, encoding='utf-8', errors='replace'):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 7 or f[5] not in want or not f[2]:
            continue
        pairs[(f[0], f[2])].add((f[5], f[6].split(',')[0]))
    by_unit = collections.defaultdict(dict)
    for (src, ad), v in pairs.items():
        by_unit[src][ad] = v
    fixed = config.LINK / 'fixed'
    groups = collections.defaultdict(list)
    for r in pick.load_catalog(a.catalog):
        calls = by_unit.get(r['source'])
        if not calls or r['unresolved'] or r['size'] > a.max_size:
            continue
        if not os.path.exists(fixed / config.obj_name(r['source'])):
            continue
        hit = [c for c in r['calls'] if c in calls]
        if not hit:
            continue
        verdict, cls = sorted(calls[hit[0]])[0]
        groups[(verdict, cls)].append(dict(unit=r['unit'], source=r['source'], address=r['address'],
                                           symbol=r['symbol'], size=r['size'], calls=r['calls'],
                                           kind='calls%d' % len(r['calls']), tag='%s:%s' % (verdict, cls),
                                           callee=hit[0]))
    out, seen = [], set()
    while len(out) < a.cap and any(groups.values()):
        for g in sorted(groups):
            if groups[g] and len(out) < a.cap:
                r = groups[g].pop(0)
                if r['address'] not in seen:
                    seen.add(r['address']); out.append(r)
    for r in out:
        print(json.dumps(r))
    print('%d targets; groups: %s' % (len(out), dict(collections.Counter(r['tag'] for r in out))), file=sys.stderr)


if __name__ == '__main__':
    main()
