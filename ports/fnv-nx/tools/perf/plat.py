#!/usr/bin/env python3
"""plat.py - the platform lane's report (D25 #3 unimplemented platform calls, #4 failed file opens, #6 audio decode
and input mapping failures), from the "plat: ..." lines of platform/win32/platstat.cpp and the boot's import lines.
tools/perf/PLAT.md.

    python3 -I tools/perf/plat.py <log>... [--elf guest.elf] [--original original_opens.json] --out report.md

Sources:
  plat: <class> <name> caller 0x<pc>[ <detail>]               first occurrence (n = 1 until a count line)
  plat: count <class> <name> caller 0x<pc> n <n>[ <detail>]   a flush (cumulative: the last flush of a log holds)
  plat: summary ...                                            the recorder's totals
  fnvnx boot: import <dll>!<name>: no export                   an import the platform does not provide
  fnvnx boot: imports: <s> slots, <r> resolved, <u> unresolved
Metric (the lane's, lower is better): distinct unimplemented entries (unimpl names + imports with no export)
+ distinct failed opens (by path hash) + distinct decode failures + distinct unmapped inputs (by detail). Distinct
by name / path / detail, not by caller, so it does not move when a relink moves code. No-op entries (documented
Switch no-ops) are listed and not counted.
--original: the original's failed opens on the same scenario (fnvnx-plat-original/1, tools/perf/plat_wine.py from
a Wine run traced at kernel32!CreateFileA). A failed open whose path class and path hash the original's run also
shows failing is listed separately as `also fails in the original` and left out of the metric; the metric without
the baseline is reported next to it (metric_all), so nothing drops out silently.
--elf maps callers to function start addresses (nm; tools/threads/report.py's Symbols). Writes <report.md> and
<report>.json (fnvnx-plat/1). Exit 0; 3 when no log has platform or import lines.
"""
import argparse
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'threads'))
from report import Symbols  # noqa: E402

CLASSES = ('unimpl', 'noop', 'open', 'decode', 'input')
FIRST = re.compile(r'plat: (unimpl|noop|open|decode|input) (\S+) caller 0x([0-9a-f]+)(?: (.*))?$')
COUNT = re.compile(r'plat: count (unimpl|noop|open|decode|input) (\S+) caller 0x([0-9a-f]+) n (\d+)(?: (.*))?$')
SUMMARY = re.compile(r'plat: summary (.*)$')
NOEXP = re.compile(r'fnvnx boot: import (\S+?)!(\S+): no export')
IMPORTS = re.compile(r'fnvnx boot: imports: (\d+) slots, (\d+) resolved, (\d+) unresolved')
OPEN_D = re.compile(r'(\w+) err (\d+) ci (\w+) path ([0-9a-f]+)')


def parse(path):
    first, last_count, summary, noexp, imports = {}, None, None, set(), None
    cur = None
    for line in open(path, errors='replace'):
        line = line.rstrip('\n')
        m = NOEXP.search(line)
        if m:
            noexp.add('%s!%s' % (m.group(1).lower(), m.group(2)))
            continue
        m = IMPORTS.search(line)
        if m:
            imports = tuple(int(x) for x in m.groups())
            continue
        k = line.find('plat: ')
        if k < 0:
            continue
        line = line[k:]
        m = COUNT.match(line)
        if m:
            if cur is None:
                cur = {}
            cur[(m.group(1), m.group(2), int(m.group(3), 16), m.group(5) or '')] = int(m.group(4))
            continue
        m = SUMMARY.match(line)
        if m:
            summary = m.group(1)
            if cur is not None:
                last_count, cur = cur, None   # a flush ends with its summary
            continue
        m = FIRST.match(line)
        if m:
            first.setdefault((m.group(1), m.group(2), int(m.group(3), 16), m.group(4) or ''), 1)
    if cur is not None:
        last_count = cur   # a flush cut by the end of the log
    entries = dict(first)
    if last_count:
        entries.update(last_count)
    return {'entries': entries, 'summary': summary, 'noexp': noexp, 'imports': imports}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--elf')
    ap.add_argument('--original')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    sym = Symbols(a.elf)
    runs = [parse(p) for p in a.logs]
    seen = any(r['entries'] or r['noexp'] or r['imports'] for r in runs)
    entries = defaultdict(int)
    noexp, imports = set(), None
    for r in runs:
        for k, n in r['entries'].items():
            entries[k] += n
        noexp |= r['noexp']
        imports = r['imports'] or imports

    def fn(c):
        f = sym.func(c)
        return '0x%08x' % f if f else '?'

    by = {c: defaultdict(lambda: {'calls': 0, 'sites': set(), 'details': set()}) for c in CLASSES}
    opens = defaultdict(lambda: {'calls': 0, 'apis': set(), 'cls': '', 'err': '', 'ci': '', 'sites': set()})
    for (cls, name, caller, detail), n in entries.items():
        if cls == 'open':
            m = OPEN_D.match(detail)
            key = m.group(4) if m else detail
            o = opens[key]
            o['calls'] += n
            o['apis'].add(name)
            o['sites'].add(fn(caller))
            if m:
                o['cls'], o['err'], o['ci'] = m.group(1), m.group(2), m.group(3)
            continue
        key = name if cls in ('unimpl', 'noop') else '%s %s' % (name, detail)
        e = by[cls][key]
        e['calls'] += n
        e['sites'].add(fn(caller))
        if detail:
            e['details'].add(detail)
    orig, orig_note = set(), None
    if a.original:
        try:
            oj = json.load(open(a.original))
            if oj.get('schema') == 'fnvnx-plat-original/1':
                orig = {(f['class'], f['hash']) for f in oj.get('failed_opens', [])}
                orig_note = '%s (%d opens traced, %d distinct failed)' % (os.path.basename(a.original),
                                                                        oj.get('opens', 0), len(orig))
        except (OSError, ValueError):
            orig_note = None
    for k, o in opens.items():
        o['in_original'] = (o['cls'], k) in orig
    in_orig = sum(1 for o in opens.values() if o['in_original'])
    metric = {'unimpl': len(by['unimpl']) + len(noexp), 'open': len(opens) - in_orig, 'decode': len(by['decode']),
              'input': len(by['input'])}
    total = sum(metric.values())
    head = {'schema': 'fnvnx-plat/1', 'metric': total, 'parts': metric, 'unimpl_entries': len(by['unimpl']),
            'imports_no_export': sorted(noexp), 'imports': imports, 'noop_entries': len(by['noop']),
            'open_by_class': {}, 'ci_yes': sum(1 for o in opens.values() if o['ci'] == 'yes'),
            'open_in_original': in_orig, 'metric_all': total + in_orig, 'original': orig_note,
            'logs': [os.path.basename(p) for p in a.logs]}
    for o in opens.values():
        head['open_by_class'][o['cls'] or '?'] = head['open_by_class'].get(o['cls'] or '?', 0) + 1
    w = open(a.out, 'w')
    w.write('# platform (unimplemented calls, failed opens, decode and input failures)\n\n')
    if not seen:
        w.write('- no platform or import lines: the guest ran without the recorder (tools/threads/variant.sh PLAT=1)\n')
    w.write('- metric %d = unimplemented %d (%d entries + %d imports with no export) + failed opens %d + decode %d + '
            'input %d; no-op entries %d (not counted)\n' % (total, metric['unimpl'], len(by['unimpl']), len(noexp),
                                                           metric['open'], metric['decode'], metric['input'],
                                                           len(by['noop'])))
    if a.original:
        w.write('- baseline: %s; failed opens that also fail in the original: %d (listed, not counted); metric '
                'without the baseline %d\n' % (orig_note or 'unreadable: nothing subtracted', in_orig, total + in_orig))
    if imports:
        w.write('- imports: %d slots, %d resolved, %d unresolved\n' % imports)
    if opens:
        w.write('- failed opens by class: %s; a case-insensitive scan finds %d of them (the path mapper missed a '
                'spelling)\n' % (', '.join('%s %d' % kv for kv in sorted(head['open_by_class'].items())), head['ci_yes']))
    for r in runs:
        if r['summary']:
            w.write('- recorder: %s\n' % r['summary'])

    def table(cls, title, cols):
        rows = sorted(by[cls].items(), key=lambda kv: -kv[1]['calls'])
        if not rows:
            return
        w.write('\n## %s\n\n| %s | calls | call sites | caller functions |\n|---|---:|---:|---|\n' % (title, cols))
        for k, e in rows:
            w.write('| %s | %d | %d | %s |\n' % (k.replace('|', '/'), e['calls'], len(e['sites']),
                                                 ', '.join(sorted(e['sites'])[:4]) + (' ...' if len(e['sites']) > 4 else '')))
    table('unimpl', 'unimplemented entries called', 'entry')
    if noexp:
        w.write('\n## imports with no export\n\n' + ''.join('- %s\n' % n for n in sorted(noexp)))
    if opens:
        for title, sel in (('failed opens', False), ('failed opens that also fail in the original (not counted)', True)):
            rows_ = sorted(((k, o) for k, o in opens.items() if o['in_original'] == sel),
                           key=lambda kv: (kv[1]['cls'], -kv[1]['calls']))
            if not rows_:
                continue
            w.write('\n## %s\n\n| path hash | class | error | ci | calls | entries | caller functions |\n'
                    '|---|---|---:|---|---:|---|---|\n' % title)
            for k, o in rows_:
                w.write('| %s | %s | %s | %s | %d | %s | %s |\n' % (k, o['cls'], o['err'], o['ci'], o['calls'],
                                                                  ', '.join(sorted(o['apis'])),
                                                                  ', '.join(sorted(o['sites'])[:3])))
    table('decode', 'audio decode failures', 'decoder and detail')
    table('input', 'unmapped inputs', 'mapping and input')
    table('noop', 'documented no-ops (not counted)', 'entry')
    w.close()
    head['unimpl_list'] = sorted(by['unimpl'])
    head['open_list'] = [{'path': k, 'class': o['cls'], 'err': o['err'], 'ci': o['ci'], 'calls': o['calls'],
                          'in_original': o['in_original']} for k, o in opens.items()]
    json.dump(head, open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('plat: metric %d (unimpl %d, open %d, decode %d, input %d)' % (total, metric['unimpl'], metric['open'],
                                                                       metric['decode'], metric['input']))
    return 0 if seen else 3


if __name__ == '__main__':
    sys.exit(main())
