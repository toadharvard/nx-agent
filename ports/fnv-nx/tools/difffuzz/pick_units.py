"""pick_units.py - campaign targets: every eligible function of the listed units (plus forced addresses).

    pick_units.py <units.list> [--force sites.txt] [--max-size N] [--max-calls N] [--link DIR]

units.list: one source path per line. sites.txt: `<source> <0xaddr>` lines (e.g. S1b patch sites) that are taken
even when they have indirect calls. Only units whose rewritten object exists in <link>/fixed are used.
"""
import argparse
import json
import os
import sys

import config
import pick


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('units')
    ap.add_argument('--force', default='')
    ap.add_argument('--catalog', default=str(config.WORK / 'catalog.jsonl'))
    ap.add_argument('--max-size', type=int, default=800)
    ap.add_argument('--max-calls', type=int, default=12)
    ap.add_argument('--link', default=str(config.LINK))
    ap.add_argument('--tag', default='')
    a = ap.parse_args()
    units = {l.strip() for l in open(a.units) if l.strip()}
    units = {u for u in units if os.path.exists(os.path.join(a.link, 'fixed', config.obj_name(u)))}
    forced = set()
    if a.force:
        for line in open(a.force):
            f = line.split()
            if len(f) >= 2 and f[1].startswith('0x'):
                forced.add('%08x' % int(f[1], 16))
    n = 0
    for r in pick.load_catalog(a.catalog):
        if r['source'] not in units or r['unresolved']:
            continue
        must = r['address'] in forced
        if not must and (r['indirect'] or r['tails'] or r['size'] > a.max_size or len(r['calls']) > a.max_calls):
            continue
        print(json.dumps(dict(unit=r['unit'], source=r['source'], address=r['address'], symbol=r['symbol'],
                              size=r['size'], calls=r['calls'], kind='leaf' if not r['calls'] else
                              'calls%d' % len(r['calls']), site=must, tag=a.tag)))
        n += 1
    print('%d functions in %d units' % (n, len(units)), file=sys.stderr)


if __name__ == '__main__':
    main()
