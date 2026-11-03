"""pick.py - choose functions for a campaign from catalog.jsonl, limited to units we have both objects for.

Filters: leaf (no outgoing calls, no indirect calls/jumps, no unresolved relocations), size bounds, and the unit has
a finished AArch64 object. Prints JSON lines {unit, source, address, symbol, size, kind}.
"""
import argparse
import json
import sys

import config


def have_a64(sources):
    ok = set()
    for s in sources:
        if config.a64_object(s) is not None:
            ok.add(s)
    return ok


def load_catalog(path):
    for line in open(path):
        r = json.loads(line)
        if 'error' not in r:
            yield r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalog', default=str(config.WORK / 'catalog.jsonl'))
    ap.add_argument('--leaf', action='store_true', help='only functions with no outgoing/indirect calls')
    ap.add_argument('--max-size', type=int, default=400)
    ap.add_argument('--min-size', type=int, default=2)
    ap.add_argument('--max-calls', type=int, default=10 ** 9)
    ap.add_argument('--need-a64', action='store_true', default=True)
    ap.add_argument('--no-float', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()
    cat = list(load_catalog(a.catalog))
    sources = {r['source'] for r in cat}
    a64 = have_a64(sources)
    out = []
    for r in cat:
        if a.need_a64 and r['source'] not in a64:
            continue
        if not (a.min_size <= r['size'] <= a.max_size):
            continue
        if r['unresolved']:
            continue
        ncalls = len(r['calls']) + r['indirect'] + len(r['tails'])
        if a.leaf and ncalls:
            continue
        if ncalls > a.max_calls:
            continue
        if r['indirect'] or r['tails']:
            continue   # indirect control flow: target cannot be recovered, skip for now
        if a.no_float and (r['x87'] or r['sse']):
            continue
        kind = 'leaf' if ncalls == 0 else 'calls%d' % len(r['calls'])
        out.append(dict(unit=r['unit'], source=r['source'], address=r['address'], symbol=r['symbol'],
                        size=r['size'], calls=r['calls'], x87=r['x87'], sse=r['sse'], fs=r['fs'], kind=kind))
    out.sort(key=lambda r: (len(r['calls']), r['size']))
    if a.limit:
        out = out[:a.limit]
    for r in out:
        print(json.dumps(r))
    print('%d functions' % len(out), file=sys.stderr)


if __name__ == '__main__':
    main()
