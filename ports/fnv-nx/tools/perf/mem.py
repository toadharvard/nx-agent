#!/usr/bin/env python3
"""mem.py - the memory lane's report (D25 #8): the peak against the Switch budget, the split by kind, the timeline,
failed allocations and the top allocation sites, from the "mem: ..." lines of platform/win32/memtrack.cpp.
tools/perf/MEM.md.

    python3 -I tools/perf/mem.py <log>... [--elf guest.elf] [--budget-gib 3.2] [--top 20] --out report.md

Budget: the application's memory on the Switch, default 3.2 GiB (--budget-gib). GPU resources live in the same
memory there, so they count inside it: total = heap + virtual + gpu. The peak is the recorder's own (the largest sum
at any allocation), not the timeline's maximum (the timeline only samples every period).
The guest flushes periodically, so a log's counts are cumulative: per log the last peak, site and summary lines hold;
several logs (runs) are reported side by side and the worst peak decides.
Sites: (kind, caller) -> live, peak live, allocations, bytes, failures; callers mapped to function start addresses
with nm of --elf (tools/threads/report.py's Symbols), by address only.
Writes <report.md> and <report>.json (fnvnx-mem/1). Exit 0; 3 when no log has memory lines.
Verdict: FAIL when the peak is over the budget or any allocation failed, else PASS.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'threads'))
from report import Symbols  # noqa: E402

KINDS = ('heap', 'virtual', 'gpu')
T = re.compile(r'mem: t (\d+) heap (\d+) virtual (\d+) gpu (\d+)')
FAIL = re.compile(r'mem: fail (\w+) (\d+) caller 0x([0-9a-f]+) t (\d+)')
PEAK = re.compile(r'mem: peak (\d+) heap (\d+) virtual (\d+) gpu (\d+) t (\d+)')
SITE = re.compile(r'mem: site (\w+) 0x([0-9a-f]+) live (\d+) peak (\d+) allocs (\d+) bytes (\d+) fails (\d+)')
SUM = re.compile(r'mem: summary heap (\d+) virtual (\d+) gpu (\d+) peak (\d+) fails (\d+) untracked (\d+) sites (\d+)')


def parse(path):
    r = {'log': os.path.basename(path), 'timeline': [], 'fails': [], 'peak': None, 'sites': {}, 'summary': None}
    sites = None
    for line in open(path, errors='replace'):
        k = line.find('mem: ')
        if k < 0:
            continue
        line = line[k:].rstrip('\n')
        m = T.match(line)
        if m:
            r['timeline'].append(tuple(int(x) for x in m.groups()))
            continue
        m = FAIL.match(line)
        if m:
            r['fails'].append({'kind': m.group(1), 'bytes': int(m.group(2)), 'caller': int(m.group(3), 16),
                               't': int(m.group(4))})
            continue
        m = PEAK.match(line)
        if m:
            v = [int(x) for x in m.groups()]
            r['peak'] = {'total': v[0], 'heap': v[1], 'virtual': v[2], 'gpu': v[3], 't': v[4]}
            sites = {}   # a flush starts: its site lines replace the previous flush's
            r['sites'] = sites
            continue
        m = SITE.match(line)
        if m:
            if sites is None:
                sites = r['sites']
            v = [int(x) for x in m.groups()[2:]]
            sites[(m.group(1), int(m.group(2), 16))] = dict(zip(('live', 'peak', 'allocs', 'bytes', 'fails'), v))
            continue
        m = SUM.match(line)
        if m:
            v = [int(x) for x in m.groups()]
            r['summary'] = dict(zip(('heap', 'virtual', 'gpu', 'peak', 'fails', 'untracked', 'sites'), v))
    return r


def gib(b):
    return b / float(1 << 30)


def fmt(b):
    return '%.1f MiB' % (b / float(1 << 20)) if b < (1 << 30) else '%.2f GiB' % gib(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--elf')
    ap.add_argument('--budget-gib', type=float, default=3.2)
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    sym = Symbols(a.elf)
    budget = int(a.budget_gib * (1 << 30))
    runs = [parse(p) for p in a.logs]
    runs = [r for r in runs if r['peak'] or r['timeline'] or r['summary'] or r['fails']]
    worst = max(runs, key=lambda r: (r['peak'] or {}).get('total', max([sum(t[1:]) for t in r['timeline']] or [0])),
                default=None)
    head = {'schema': 'fnvnx-mem/1', 'budget_bytes': budget, 'budget_gib': a.budget_gib, 'runs': []}
    nfail = 0
    for r in runs:
        pk = r['peak'] or {}
        tl_max = max([sum(t[1:]) for t in r['timeline']] or [0])
        total = pk.get('total', tl_max)
        fails = (r['summary'] or {}).get('fails', len(r['fails']))
        nfail += fails
        head['runs'].append({'log': r['log'], 'peak': pk or None, 'peak_total': total, 'timeline_max': tl_max,
                             'over_budget': total > budget, 'headroom_bytes': budget - total, 'fails': fails,
                             'untracked': (r['summary'] or {}).get('untracked', 0), 'end': r['summary']})
    peak = max((x['peak_total'] for x in head['runs']), default=0)
    head.update({'peak_total': peak, 'over_budget': peak > budget, 'fails': nfail,
                 'verdict': 'no data' if not runs else ('FAIL' if peak > budget or nfail else 'PASS')})
    o = open(a.out, 'w')
    o.write('# memory (live bytes, peak against the Switch budget)\n\n')
    if not runs:
        o.write('- no memory lines: the guest ran without the recorder (tools/threads/variant.sh MEM=1)\n')
    else:
        o.write('- verdict: %s; peak %s of %s budget (%.0f%%), headroom %s; failed allocations %d\n' %
                (head['verdict'], fmt(peak), fmt(budget), 100.0 * peak / budget, fmt(budget - peak), nfail))
        o.write('- GPU resources count inside the budget (total = heap + virtual + gpu)\n')
        o.write('\n## runs\n\n| log | peak | heap | virtual | gpu | at s | timeline max | end live | fails | untracked |\n'
                '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n')
        for x in head['runs']:
            pk, end = x['peak'] or {}, x['end'] or {}
            o.write('| %s | %s | %s | %s | %s | %s | %s | %s | %d | %d |\n' % (
                x['log'], fmt(x['peak_total']), fmt(pk.get('heap', 0)), fmt(pk.get('virtual', 0)),
                fmt(pk.get('gpu', 0)), '%.1f' % (pk['t'] / 1000.0) if pk else '-', fmt(x['timeline_max']),
                fmt(end.get('heap', 0) + end.get('virtual', 0) + end.get('gpu', 0)) if end else '-', x['fails'],
                x['untracked']))
    if worst and worst['fails']:
        o.write('\n## failed allocations (%s, first %d)\n\n| kind | bytes | caller function | at s |\n|---|---:|---|---:|\n'
                % (worst['log'], len(worst['fails'])))
        for f in worst['fails'][:40]:
            fn = sym.func(f['caller'])
            o.write('| %s | %d | %s | %.1f |\n' % (f['kind'], f['bytes'], '0x%08x' % fn if fn else '?', f['t'] / 1000.0))
    site_rows = []
    if worst:
        agg = {}
        for (kind, caller), v in worst['sites'].items():   # sites of one function together
            fn = sym.func(caller) or caller
            e = agg.setdefault((kind, fn), {'live': 0, 'peak': 0, 'allocs': 0, 'bytes': 0, 'fails': 0, 'sites': 0})
            for k2 in ('live', 'peak', 'allocs', 'bytes', 'fails'):
                e[k2] += v[k2]
            e['sites'] += 1
        for kind in KINDS:
            rows = sorted(((fn, v) for (k, fn), v in agg.items() if k == kind), key=lambda kv: -kv[1]['peak'])
            if not rows:
                continue
            o.write('\n## top %s sites (%s)\n\n| caller function | peak live | live at end | allocations | bytes | fails |\n'
                    '|---|---:|---:|---:|---:|---:|\n' % (kind, worst['log']))
            for fn, v in rows[:a.top]:
                o.write('| 0x%08x | %s | %s | %d | %s | %d |\n' % (fn, fmt(v['peak']), fmt(v['live']), v['allocs'],
                                                                   fmt(v['bytes']), v['fails']))
                site_rows.append({'kind': kind, 'func': '0x%08x' % fn, **v})
        if worst['timeline']:
            tl = worst['timeline']
            o.write('\n## timeline (%s, %d points)\n\n| s | heap | virtual | gpu | total |\n|---:|---:|---:|---:|---:|\n'
                    % (worst['log'], len(tl)))
            step = max(1, len(tl) // 20)
            for t in tl[::step]:
                o.write('| %.0f | %s | %s | %s | %s |\n' % (t[0] / 1000.0, fmt(t[1]), fmt(t[2]), fmt(t[3]), fmt(sum(t[1:]))))
    o.close()
    head['top_sites'] = site_rows
    json.dump(head, open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('mem: %s, peak %s of %s, %d failed' % (head['verdict'], fmt(peak), fmt(budget), nfail))
    return 0 if runs else 3


if __name__ == '__main__':
    sys.exit(main())
