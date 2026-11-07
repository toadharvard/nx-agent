#!/usr/bin/env python3
"""Race and wait-graph reports for emulator runs (D25 #7, tools/threads/README.md).

    python3 -I tools/threads/report.py <log>... --elf <guest.elf> [--slide 0x...] --out <report.md>

Reads the run logs of a guest built with the race checker (tests/s15/mtqemu/hb.c: "hb-race" pair lines and its
"hb: summary") and/or with the wait-graph watchdog on (platform/win32/waitgraph.cpp: "wg: deadlock", "wg: long-wait",
"wg: summary"). Every code address is mapped to the start address of the function that contains it, from the
image's symbol table (nm); the report names functions by that start address only, never by symbol name:
  - races per unordered pair of functions: kinds, races, distinct access pairs, a sample data address;
  - deadlock cycles: the threads' waits (object kind, object address, caller function) in cycle order;
  - long waits: per caller function and object kind, the count and the longest.
Writes <report.md> and <report>.json (fnvnx-threads/1: races, race_pairs, function_pairs, cycles, long_waits,
untracked, evictions; the lane's headline). Exit 0; 3 when no log has either kind of line (the lane did not run).
--slide: added to the image's addresses when the run loaded it elsewhere (the guest's fixed images need none).
"""
import argparse
import bisect
import json
import os
import re
import shutil
import subprocess
import sys

RACE = re.compile(r'^hb-race (\S+) prev 0x([0-9a-f]+) cur 0x([0-9a-f]+) addr 0x([0-9a-f]+) n (\d+) threads (\d+) (\d+)')
HB_SUM = re.compile(r'^hb: summary (.*)$')
WG_CYC = re.compile(r'^wg: deadlock cycle (\d+) threads:(.*)$')
WG_EDGE = re.compile(r'tid (0x[0-9a-f]+) (\w+) 0x([0-9a-f]+) pc 0x([0-9a-f]+) owner (0x[0-9a-f]+)')
WG_LONG = re.compile(r'^wg: long-wait tid (0x[0-9a-f]+) (.*?) pc 0x([0-9a-f]+) waited (\d+) ms timeout (.*)$')
WG_OBJ = re.compile(r'(\w+) 0x([0-9a-f]+) owner (\S+)')
WG_SUM = re.compile(r'^wg: summary (.*)$')


def kv(text):
    toks = text.split()
    return {toks[i]: toks[i + 1] for i in range(0, len(toks) - 1, 2) if re.fullmatch(r'-?\d+', toks[i + 1])}


class Symbols:
    """Function start addresses of an image (nm: T/t/W/w text symbols)."""

    def __init__(self, elf, slide=0):
        self.starts = []
        if not elf:
            return
        nm = next((t for t in ('aarch64-linux-gnu-nm', 'aarch64-none-elf-nm', 'llvm-nm', 'nm') if shutil.which(t)), None)
        if not nm:
            raise SystemExit('report.py: no nm found')
        out = subprocess.run([nm, '-n', '--defined-only', elf], capture_output=True, text=True).stdout
        seen = set()
        for line in out.splitlines():
            p = line.split()
            if len(p) >= 3 and p[1] in 'TtWw' and not p[2].startswith('$'):
                a = int(p[0], 16) + slide
                if a not in seen:
                    seen.add(a)
                    self.starts.append(a)
        self.starts.sort()

    def func(self, pc):
        """The start of the function holding pc; None when unknown (pc 0) or below every symbol."""
        if not pc or not self.starts:
            return None if not pc else pc
        i = bisect.bisect_right(self.starts, pc) - 1
        return self.starts[i] if i >= 0 else None


def fa(a):
    return '0x%08x' % a if a is not None else '?'


def parse(logs, sym):
    """The guest flushes periodically (fnvnx_threads_flush): a dump's counts are cumulative, so per log the last
    count of an access pair and the last summary hold; logs (separate runs) add up."""
    races, summary, cycles, longs, wg_summary = {}, {}, [], {}, {}
    seen_any = False
    acc = {}
    for path in logs:
        last_sum, last_wg, mine = {}, {}, {}
        with open(path, errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')
                # the guest's debug lines may carry a prefix ("[fnvnx] ", a timestamp): match from "hb"/"wg"
                k = min([i for i in (line.find('hb-race '), line.find('hb: summary'), line.find('wg: ')) if i >= 0],
                        default=-1)
                if k < 0:
                    continue
                line = line[k:]
                m = RACE.match(line)
                if m:
                    seen_any = True
                    k = (m.group(1), int(m.group(2), 16), int(m.group(3), 16))
                    mine[k] = (max(int(m.group(5)), mine.get(k, (0, 0))[0]), int(m.group(4), 16))
                    continue
                m = HB_SUM.match(line)
                if m:
                    seen_any = True
                    last_sum = {a: int(b) for a, b in kv(m.group(1)).items()}
                    continue
                m = WG_CYC.match(line)
                if m:
                    seen_any = True
                    edges = [{'tid': e[0], 'kind': e[1], 'obj': int(e[2], 16), 'func': sym.func(int(e[3], 16)),
                              'owner': e[4]} for e in WG_EDGE.findall(m.group(2))]
                    cycles.append(edges)
                    continue
                m = WG_LONG.match(line)
                if m:
                    seen_any = True
                    objs = WG_OBJ.findall(m.group(2))
                    kind = '+'.join(sorted({o[0] for o in objs})) or '?'
                    fn = sym.func(int(m.group(3), 16))
                    ms = int(m.group(4))
                    r = longs.setdefault((fn or 0, kind), {'n': 0, 'max_ms': 0, 'infinite': 0, 'obj': None})
                    r['n'] += 1
                    r['infinite'] += m.group(5) == 'INFINITE'
                    if ms >= r['max_ms']:
                        r['max_ms'], r['obj'] = ms, int(objs[0][1], 16) if objs else None
                    continue
                m = WG_SUM.match(line)
                if m:
                    seen_any = True
                    last_wg = {a: int(b) for a, b in kv(m.group(1)).items()}
        for k, (n, addr) in mine.items():
            n0, a0 = acc.get(k, (0, addr))
            acc[k] = (n0 + n, a0)
        for a, b in last_sum.items():
            summary[a] = summary.get(a, 0) + b
        for a, b in last_wg.items():
            wg_summary[a] = b if a == 'limit' else wg_summary.get(a, 0) + b
    for (kind, prev, cur), (n, addr) in acc.items():
        fp, fc = sym.func(prev), sym.func(cur)
        key = tuple(sorted((fp or 0, fc or 0)))
        r = races.setdefault(key, {'kinds': set(), 'races': 0, 'pcs': set(), 'addr': addr})
        r['kinds'].add(kind)
        r['races'] += n
        r['pcs'].add((prev, cur))
    return seen_any, races, summary, cycles, longs, wg_summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--elf')
    ap.add_argument('--slide', default='0')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    sym = Symbols(a.elf, int(a.slide, 0))
    seen, races, hsum, cycles, longs, wsum = parse(a.logs, sym)
    nraces = sum(r['races'] for r in races.values())
    npairs = sum(len(r['pcs']) for r in races.values())
    head = {'schema': 'fnvnx-threads/1', 'races': nraces, 'race_pairs': npairs, 'function_pairs': len(races),
            'cycles': len(cycles), 'long_waits': sum(r['n'] for r in longs.values()),
            'untracked': hsum.get('untracked', 0), 'evictions': hsum.get('evictions', 0),
            'read_evictions': hsum.get('read-evictions', 0), 'pairs_full': hsum.get('pairs-full', 0),
            'symbols': len(sym.starts), 'logs': [os.path.basename(p) for p in a.logs]}
    o = open(a.out, 'w')
    o.write('# threads report (races, deadlocks, hangs)\n\n')
    o.write('- races %d in %d function pairs (%d access pairs); deadlock cycles %d; long waits %d\n' %
            (nraces, len(races), npairs, len(cycles), head['long_waits']))
    if not seen:
        o.write('- no race-checker or watchdog lines in the logs: the lane did not run\n')
    if hsum:
        o.write('- race checker: %s\n' % ', '.join('%s %d' % kv_ for kv_ in sorted(hsum.items())))
    if wsum:
        o.write('- watchdog: %s\n' % ', '.join('%s %d' % kv_ for kv_ in sorted(wsum.items())))
    if not sym.starts:
        o.write('- no symbol table: pcs are listed as they are, not as function starts\n')
    if races:
        o.write('\n## races per function pair\n\n| function A | function B | kinds | races | access pairs | sample address |\n'
                '|---|---|---|---:|---:|---|\n')
        for key, r in sorted(races.items(), key=lambda kv_: -kv_[1]['races']):
            o.write('| %s | %s | %s | %d | %d | 0x%08x |\n' % (fa(key[0] or None), fa(key[1] or None),
                                                              ', '.join(sorted(r['kinds'])), r['races'],
                                                              len(r['pcs']), r['addr']))
    if cycles:
        o.write('\n## deadlock cycles\n\n')
        for i, c in enumerate(cycles, 1):
            o.write('%d. %s\n' % (i, ' -> '.join('tid %s waits on %s 0x%08x in %s (owner %s)' %
                                                  (e['tid'], e['kind'], e['obj'], fa(e['func']), e['owner'])
                                                  for e in c)))
    if longs:
        o.write('\n## long waits per caller\n\n| caller function | objects | waits | without timeout | longest ms | '
                'object of the longest |\n|---|---|---:|---:|---:|---|\n')
        for (fn, kind), r in sorted(longs.items(), key=lambda kv_: -kv_[1]['max_ms']):
            o.write('| %s | %s | %d | %d | %d | %s |\n' % (fa(fn or None), kind, r['n'], r['infinite'], r['max_ms'],
                                                          fa(r['obj'])))
    o.close()
    head['function_pair_list'] = [{'a': fa(k[0] or None), 'b': fa(k[1] or None), 'kinds': sorted(r['kinds']),
                                   'races': r['races'], 'access_pairs': len(r['pcs'])} for k, r in races.items()]
    head['cycle_list'] = [[{**e, 'func': fa(e['func']), 'obj': '0x%08x' % e['obj']} for e in c] for c in cycles]
    json.dump(head, open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('threads: races %d (%d function pairs), cycles %d, long waits %d' %
          (nraces, len(races), len(cycles), head['long_waits']))
    return 0 if seen else 3


if __name__ == '__main__':
    sys.exit(main())
