#!/usr/bin/env python3
"""scripterr.py - the script-error lane's report (D25 #10): script and console errors of a run, by (script form id,
error kind, caller), counts and ids only - no game text. zero_targets.py's #10 row reads it.

    python3 -I tools/report/scripterr.py <log>... [--elf guest.elf] [--patterns FILE] [--baseline orig.json]
                                         --out scripterr.md

Sources:
  the call watcher (tools/perf/callwatch.py): "linhost: call <kind> caller 0x<lr> form 0x<id> ..." and
      "emu: call <kind> caller <function> form 0x<id> ..." - the kind is the watched entry point's name, the form the
      `form` value (none: '-'); linhost callers are mapped with nm of --elf to functions (faults.py's keys:
      orig:XXXXXXXX or the native name); the "... calls <kind> n <count>" totals count the calls past the logged ones
  --patterns FILE (private, like the call watcher's config): lines "<kind><TAB><regex>" applied to every other log
      line (the guest's debug output and console as the platform's stand-ins print them); a match counts as an error of
      that kind, its form is the regex's named group `form` (hex), else the first 8-digit hex word of the match, else
      '-'; the caller is '-'. The text itself is never written.
  --baseline: the original's run of the same scenario (an earlier fnvnx-scripterr/1, e.g. from Wine's console log
      through --patterns): groups present there (by form and kind) are listed as `also in the original` and left out
      of the metric.
Metric: distinct (form, kind, caller) groups not in the baseline. Writes <out> and <out>.json (fnvnx-scripterr/1).
Exit 0; 3 when no log shows a watcher ("N functions watched", N > 0), a call-watcher line or a pattern match.
"""
import argparse
import json
import os
import re
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from faults import Elf  # noqa: E402  (function keys: orig:XXXXXXXX / native name)

CALL = re.compile(r'(linhost|emu): call (\S+) caller (\S+)((?: \w+ (?:0x[0-9a-f]+|unreadable))*)\s*$')
CALLS = re.compile(r'(linhost|emu): calls (\S+) n (\d+)')
WATCHED = re.compile(r'(?:callwatch|linhost: --calls): ([1-9]\d*) functions watched')
HEX8 = re.compile(r'\b([0-9A-Fa-f]{8})\b')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--elf')
    ap.add_argument('--patterns')
    ap.add_argument('--baseline')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    elf = Elf(a.elf)
    pats = []
    if a.patterns:
        for line in open(a.patterns):
            line = line.rstrip('\n')
            if not line.strip() or line.startswith('#') or '\t' not in line:
                continue
            k, rx = line.split('\t', 1)
            pats.append((k.strip(), re.compile(rx)))
    groups, logged, totals, seen = OrderedDict(), {}, {}, False
    for p in a.logs:
        for line in open(p, errors='replace'):
            line = line.rstrip('\n')
            m = CALL.search(line)
            if m:
                seen = True
                side, kind, caller, rest = m.groups()
                if side == 'linhost' and caller.startswith('0x'):
                    caller = elf.func(int(caller, 16)) or caller
                vals = dict(zip(rest.split()[::2], rest.split()[1::2]))
                form = vals.get('form', '-')
                form = '%08x' % int(form, 16) if form.startswith('0x') else form
                g = groups.setdefault((form, kind, caller), {'form': form, 'kind': kind, 'caller': caller, 'count': 0,
                                                             'source': 'call'})
                g['count'] += 1
                logged[kind] = logged.get(kind, 0) + 1
                continue
            if WATCHED.search(line):   # a watcher ran: no call lines then means none happened (a measured 0)
                seen = True
                continue
            m = CALLS.search(line)
            if m:
                seen = True
                totals[m.group(2)] = totals.get(m.group(2), 0) + int(m.group(3))
                continue
            for kind, rx in pats:
                mm = rx.search(line)
                if not mm:
                    continue
                seen = True
                form = (mm.groupdict().get('form') if 'form' in rx.groupindex else None) or \
                    (HEX8.search(mm.group(0)).group(1) if HEX8.search(mm.group(0)) else '-')
                form = form.lower()
                g = groups.setdefault((form, kind, '-'), {'form': form, 'kind': kind, 'caller': '-', 'count': 0,
                                                          'source': 'pattern'})
                g['count'] += 1
                break
    base = set()
    if a.baseline and os.path.isfile(a.baseline):
        for g in json.load(open(a.baseline)).get('groups', []):
            base.add((g['form'], g['kind']))
    for g in groups.values():
        g['in_original'] = (g['form'], g['kind']) in base
    rows = list(groups.values())
    counted = [g for g in rows if not g['in_original']]
    unlogged = {k: totals[k] - logged.get(k, 0) for k in totals if totals[k] > logged.get(k, 0)}
    head = {'schema': 'fnvnx-scripterr/1', 'metric': len(counted), 'groups': rows, 'distinct': len(rows),
            'in_original': len(rows) - len(counted), 'calls': sum(g['count'] for g in rows),
            'calls_not_logged': unlogged, 'kinds': sorted({g['kind'] for g in rows}),
            'forms': len({g['form'] for g in counted if g['form'] != '-'}),
            'baseline': os.path.basename(a.baseline) if a.baseline else None,
            'logs': [os.path.basename(p) for p in a.logs]}
    o = open(a.out, 'w')
    o.write('# script errors (D25 #10)\n\n')
    if not seen:
        o.write('- no call-watcher line and no pattern match: the run had no watcher (tools/perf/callwatch.py)\n')
    o.write('- metric %d: distinct (form, kind, caller) groups%s; %d errors, %d forms; also in the original: %d\n' % (
        head['metric'], '' if a.baseline else ' (no baseline: nothing subtracted)', head['calls'], head['forms'],
        head['in_original']))
    if unlogged:
        o.write('- calls past the logged 5000 (counted, not grouped): %s\n' %
                ', '.join('%s %d' % kv for kv in sorted(unlogged.items())))
    if rows:
        o.write('\n| form | kind | caller | count | also in the original |\n|---|---|---|---:|---|\n')
        for g in sorted(rows, key=lambda g: (g['in_original'], -g['count'])):
            o.write('| %s | %s | %s | %d | %s |\n' % (g['form'], g['kind'], g['caller'], g['count'],
                                                     'yes' if g['in_original'] else ''))
    o.close()
    json.dump(head, open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('scripterr: metric %d (%d groups, %d in the original)' % (head['metric'], len(rows), head['in_original']))
    return 0 if seen else 3


if __name__ == '__main__':
    sys.exit(main())
