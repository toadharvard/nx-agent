#!/usr/bin/env python3
"""measure.py - static cost of the fnvnx_order plugin and the TSO barriers (S12-ordering, docs/ORDERING.md).

    toolchain/order/measure.py <name>=<obj dir> [<name>=<obj dir> ...] [--hot <units.list>] [--json out.json]

Each obj dir is a tools/link/compile.sh output of the same unit list in another configuration (ORDER_MODE /
ORDER_ARGS). Counts, over the objects every configuration compiled: instructions in executable sections, ordered
loads (ldar*), ordered stores (stlr*), barriers (dmb ish / ishld / ishst), exclusives, and the change against the
first configuration. --hot restricts a second table to the listed units (sources as in objects.tsv).
"""
import collections
import json
import os
import re
import subprocess
import sys

OBJDUMP = os.environ.get('OBJDUMP', 'aarch64-linux-gnu-objdump')
CLASSES = [('ldar', r'^ldar[bh]?$'), ('stlr', r'^stlr[bh]?$'), ('dmb ish', None), ('dmb ishld', None),
           ('dmb ishst', None), ('excl', r'^(ld[a]?xr|st[l]?xr)[bh]?$')]


def census(obj):
    out = subprocess.run([OBJDUMP, '-d', '--no-show-raw-insn', obj], capture_output=True, text=True).stdout
    c = collections.Counter()
    for line in out.splitlines():
        m = re.match(r'^\s+[0-9a-f]+:\s+(\S+)\s*(\S*)', line)
        if not m:
            continue
        op, arg = m.group(1), m.group(2)
        c['insns'] += 1
        if op == 'dmb':
            c['dmb ' + arg.rstrip(',')] += 1
            continue
        for name, rx in CLASSES:
            if rx and re.match(rx, op):
                c[name] += 1
    return c


def objects(d):
    rows = {}
    for line in open(os.path.join(d, 'objects.tsv')):
        src, obj, st = line.rstrip('\n').split('\t')
        if st == 'ok':
            rows[src] = obj
    return rows


def table(title, names, totals):
    keys = ['insns'] + [n for n, _ in CLASSES]
    lines = ['### ' + title, '', '| config | ' + ' | '.join(keys) + ' | insns vs %s |' % names[0],
             '|---|' + '---:|' * (len(keys) + 1)]
    b = totals[names[0]]['insns'] or 1
    for n in names:
        t = totals[n]
        lines.append('| %s | %s | %+.2f %% |' % (n, ' | '.join('{:,}'.format(t[k]) for k in keys),
                                                100.0 * (t['insns'] - b) / b))
    return '\n'.join(lines) + '\n'


def main(argv):
    cfgs, hot, js = [], None, None
    it = iter(argv)
    for a in it:
        if a == '--hot':
            hot = set(l.strip() for l in open(next(it)) if l.strip())
        elif a == '--json':
            js = next(it)
        else:
            n, d = a.split('=', 1)
            cfgs.append((n, d))
    objs = {n: objects(d) for n, d in cfgs}
    common = sorted(set.intersection(*[set(o) for o in objs.values()]))
    names = [n for n, _ in cfgs]
    per = {n: {src: census(objs[n][src]) for src in common} for n in names}
    res = {'units': len(common), 'all': {}, 'hot': {}}
    totals = {n: sum(per[n].values(), collections.Counter()) for n in names}
    md = ['%d units compiled in every configuration' % len(common), '', table('all units', names, totals)]
    res['all'] = {n: dict(totals[n]) for n in names}
    if hot:
        hs = [s for s in common if s in hot]
        ht = {n: sum((per[n][s] for s in hs), collections.Counter()) for n in names}
        md.append(table('hot units (%d)' % len(hs), names, ht))
        res['hot'] = {n: dict(ht[n]) for n in names}
    # the units whose size changes most between the first and the last configuration
    first, last = names[0], names[-1]
    delta = sorted(common, key=lambda s: per[last][s]['insns'] - per[first][s]['insns'], reverse=True)[:10]
    md += ['### largest growth %s -> %s' % (first, last), '', '| unit | insns %s | insns %s | ldar | stlr | dmb |' %
           (first, last), '|---|---:|---:|---:|---:|---:|']
    for s in delta:
        p, q = per[first][s], per[last][s]
        md.append('| %s | %d | %d | %d | %d | %d |' % (s, p['insns'], q['insns'], q['ldar'], q['stlr'],
                                                     q['dmb ish'] + q['dmb ishld'] + q['dmb ishst']))
    print('\n'.join(md))
    if js:
        json.dump(res, open(js, 'w'), indent=1)


if __name__ == '__main__':
    main(sys.argv[1:])
