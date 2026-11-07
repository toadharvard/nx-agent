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
--suppress <file> (repeatable): known-benign entries, one per line, tab-separated, '#' starts a comment:
    <kind>  <a>  <b>  <build>  <reason>
  kind   write-write | write-read | read-write | race (any of the three) | long-wait
  a, b   race: the two function start addresses, either order; long-wait: a = the caller function, b = the object
         kind (cs, mutex, event, semaphore, thread) or *
  build  the first 12 hex digits of the image's sha256 (the report prints it) or *: function addresses move between
         builds, so an entry for one build does not silently hide a different function in the next
  reason required, free text: why the pair is benign (who decided, where it is recorded)
  Suppressed entries leave the counts and the tables; they are listed with their reason and hits, and an entry that
  matched nothing is listed as stale. Suppression files hold addresses of a game build: they live with the farm's
  data ($FARM_HOME/threads/suppress.tsv, or the private artifacts repository), never in this repository.
"""
import argparse
import bisect
import hashlib
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


RACE_KINDS = ('write-write', 'write-read', 'read-write')


def load_suppress(paths):
    out = []
    for path in paths:
        for n, line in enumerate(open(path, errors='replace'), 1):
            line = line.split('#', 1)[0].rstrip('\n')
            if not line.strip():
                continue
            c = [x.strip() for x in line.split('\t')]
            where = '%s:%d' % (os.path.basename(path), n)
            if len(c) < 5 or not c[4]:
                raise SystemExit('report.py: %s: want <kind> <a> <b> <build> <reason> (tab-separated, reason '
                                 'required)' % where)
            kind, a, b, build, reason = c[0], c[1], c[2], c[3].lower(), '\t'.join(c[4:])
            if kind not in RACE_KINDS + ('race', 'long-wait'):
                raise SystemExit('report.py: %s: unknown kind %r' % (where, kind))
            try:
                fa_ = int(a, 16)
                fb_ = None if kind == 'long-wait' else int(b, 16)
            except ValueError:
                raise SystemExit('report.py: %s: addresses are hex (0x...)' % where)
            out.append({'where': where, 'kind': kind, 'a': fa_, 'b': b if kind == 'long-wait' else fb_,
                        'build': build, 'reason': reason, 'hits': 0})
    return out


def apply_suppress(entries, build, races, longs):
    """Moves suppressed race pairs and long waits out of races / longs; returns the suppressed rows."""
    gone = []
    for e in entries:
        if e['build'] != '*' and not build.startswith(e['build']):
            continue
        if e['kind'] == 'long-wait':
            for key in [k for k in longs if k[0] == e['a'] and (e['b'] == '*' or e['b'] in k[1].split('+'))]:
                r = longs.pop(key)
                e['hits'] += r['n']
                gone.append(('long-wait', fa(key[0]), key[1], r['n'], e))
            continue
        key = tuple(sorted((e['a'], e['b'])))
        r = races.get(key)
        if not r:
            continue
        kinds = set(RACE_KINDS) if e['kind'] == 'race' else {e['kind']}
        if not r['kinds'] <= kinds:
            continue   # a pair with a kind the entry does not name stays reported
        races.pop(key)
        e['hits'] += r['races']
        gone.append(('race', fa(key[0]), fa(key[1]), r['races'], e))
    return gone


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--elf')
    ap.add_argument('--slide', default='0')
    ap.add_argument('--out', required=True)
    ap.add_argument('--suppress', action='append', default=[])
    a = ap.parse_args()
    sym = Symbols(a.elf, int(a.slide, 0))
    supp = load_suppress(a.suppress)
    build = hashlib.sha256(open(a.elf, 'rb').read()).hexdigest()[:12] if a.elf else ''
    seen, races, hsum, cycles, longs, wsum = parse(a.logs, sym)
    gone = apply_suppress(supp, build, races, longs)
    nraces = sum(r['races'] for r in races.values())
    npairs = sum(len(r['pcs']) for r in races.values())
    head = {'schema': 'fnvnx-threads/1', 'races': nraces, 'race_pairs': npairs, 'function_pairs': len(races),
            'cycles': len(cycles), 'long_waits': sum(r['n'] for r in longs.values()),
            'untracked': hsum.get('untracked', 0), 'evictions': hsum.get('evictions', 0),
            'read_evictions': hsum.get('read-evictions', 0), 'pairs_full': hsum.get('pairs-full', 0),
            'symbols': len(sym.starts), 'logs': [os.path.basename(p) for p in a.logs], 'build': build or None,
            'suppressed_races': sum(g[3] for g in gone if g[0] == 'race'),
            'suppressed_long_waits': sum(g[3] for g in gone if g[0] == 'long-wait'),
            'stale_suppressions': [e['where'] for e in supp if not e['hits']]}
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
    if build:
        o.write('- build %s (the suppression files\' build column)\n' % build)
    if supp:
        o.write('- suppressed: %d races, %d long waits by %d entries (%d stale)\n' %
                (head['suppressed_races'], head['suppressed_long_waits'], len(supp), len(head['stale_suppressions'])))
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
    if supp:
        o.write('\n## suppressed (known benign)\n\n| what | a | b | count | entry | reason |\n|---|---|---|---:|---|---|\n')
        for what, x, y, n, e in gone:
            o.write('| %s | %s | %s | %d | %s | %s |\n' % (what, x, y, n, e['where'], e['reason'].replace('|', '/')))
        for e in supp:
            if not e['hits']:
                o.write('| stale (matched nothing) | %s | %s | 0 | %s | %s |\n' % (
                    fa(e['a']), e['b'] if e['kind'] == 'long-wait' else fa(e['b']), e['where'],
                    e['reason'].replace('|', '/')))
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
