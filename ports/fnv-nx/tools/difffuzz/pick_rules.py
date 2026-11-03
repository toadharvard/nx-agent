"""pick_rules.py - campaign targets among functions a prepare rule changed (attrib.py rules), by rule family.

    pick_rules.py <rules.json> [--families ASM2,ASM4,...] [--exclude XC1] [--max-size N] [--max-calls N]

Same filters as pick.py (unit has an AArch64 object, no unresolved relocation, no indirect control flow); prints
JSON lines with an extra `rules` field.
"""
import argparse
import json
import sys

import config
import pick


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('rules')
    ap.add_argument('--catalog', default=str(config.WORK / 'catalog.jsonl'))
    ap.add_argument('--families', default='')
    ap.add_argument('--exclude', default='XC1,GONE')
    ap.add_argument('--max-size', type=int, default=2000)
    ap.add_argument('--max-calls', type=int, default=12)
    a = ap.parse_args()
    rules = json.load(open(a.rules))['functions']
    fam = set(filter(None, a.families.split(',')))
    excl = set(filter(None, a.exclude.split(',')))
    cat = [r for r in pick.load_catalog(a.catalog) if r['address'] in rules]
    a64 = pick.have_a64({r['source'] for r in cat})
    n = 0
    for r in sorted(cat, key=lambda r: (len(r['calls']), r['size'])):
        fams = set(rules[r['address']]['rules'])
        if fam and not fams & fam or fams <= excl:
            continue
        if r['source'] not in a64 or r['unresolved'] or r['indirect'] or r['tails'] or r['size'] > a.max_size:
            continue
        if len(r['calls']) > a.max_calls:
            continue
        print(json.dumps(dict(unit=r['unit'], source=r['source'], address=r['address'], symbol=r['symbol'],
                              size=r['size'], calls=r['calls'], kind='calls%d' % len(r['calls']),
                              rules=sorted(fams))))
        n += 1
    print('%d functions' % n, file=sys.stderr)


if __name__ == '__main__':
    main()
