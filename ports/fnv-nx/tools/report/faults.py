#!/usr/bin/env python3
"""faults.py - the fault classifier (D25 #1): faults of boot and scenario runs grouped by (kind, function, caller chain
of 3), split into new / known / fixed against the previous head's groups. zero_targets.py's #1 row reads the result.

    python3 -I tools/report/faults.py <log>... [--elf guest.elf] [--prev prev-faults.json] --out faults.md

Logs (any mix):
  tests/s5/emu_guest.py   "== trace (<why>), thread N" with "   pc  <sym>" and "   #k <sym>" frames (the fault that ended
                          the run); "   stubbed fault: <name> <how> (<err> at <where>)" (a fault the stand-in table
                          absorbed); "skip-summary\\t<n>\\t<fault>\\t<site>\\t<first initializer>" (initializers skipped
                          after a fault); "== hang: no progress ..." with "   thread ..." lines (tools/perf/emu_progress.py)
  linhost                 "linhost: fault: signal S thread T pc 0x.. lr 0x.. addr 0x.." + "linhost: frames 0x.. ..."
                          (+ "linhost: trap: <text>"); "linhost: skipped initializer 0x..: signal S at pc 0x..";
                          "linhost: hang: no progress ..." + "linhost: hang thread <tid> pc .. lr .. frames .." (the main
                          thread's, or else the first, stack is the hang's)
Functions: an emulator symbol is keyed by the exe address it carries ("[orig XXXXXXXX]": stable across relinks), else
by its name without the offset; a linhost address is mapped with nm of --elf to the function containing it, and from
there to its __fnv_<orig> alias when it has one, else to the native name, else to the bare address (unstable: such
groups say so).
Kinds: fatal:<why> (emu, the error without addresses), fatal:signal-<S> (linhost), stubbed:<err>, skipped-init:<err>,
hang. Status against --prev (an earlier fnvnx-faults/1): new (not there), known (there), fixed (there, not here).
Writes <out> and <out>.json (fnvnx-faults/1: groups with kind, func, chain, count, status; totals). Exit 0; 3 when no
log has a fault line or a run summary.
"""
import argparse
import bisect
import json
import os
import re
import shutil
import subprocess
import sys
from collections import OrderedDict

ORIG = re.compile(r'\[orig ([0-9a-fA-F]{8})\]')


def norm_err(s):
    s = re.sub(r'\(UC_ERR_\w+\)', '', s)
    s = re.sub(r'0x[0-9a-fA-F]+|\b\d+\b', '#', s)
    return re.sub(r'\s+', ' ', s).strip()[:60] or '?'


def emu_func(sym):
    """'0x2001234 Foo::bar [orig 00401230]+0x10' -> 'orig:00401230'; '0x.. name+0x..' -> 'name'; '0x..' -> '?'"""
    m = ORIG.search(sym)
    if m:
        return 'orig:' + m.group(1).lower()
    s = re.sub(r'^0x[0-9a-fA-F]+\s*', '', sym.strip())
    s = re.sub(r'\+0x[0-9a-fA-F]+$', '', s)
    return s or '?'


class Elf:
    def __init__(self, path):
        self.starts, self.names = [], {}
        if not path:
            return
        nm = next((t for t in ('aarch64-none-elf-nm', 'aarch64-linux-gnu-nm', 'llvm-nm', 'nm') if shutil.which(t)), None)
        if not nm:
            return
        by = {}
        for line in subprocess.run([nm, '--defined-only', path], capture_output=True, text=True).stdout.splitlines():
            f = line.split()
            if len(f) >= 3 and f[1] in 'TtWw':
                by.setdefault(int(f[0], 16), []).append(f[2])
        self.starts = sorted(by)
        for a, ns in by.items():
            orig = [n for n in ns if re.fullmatch(r'__fnv_[0-9a-f]{8}', n)]
            nat = [n for n in ns if not n.startswith('__fnv_') and not n.startswith('$')]
            self.names[a] = 'orig:' + orig[0][6:] if orig else (nat[0] if nat else ns[0])

    def func(self, a):
        """the function holding a; '0x..' without a symbol table; None outside the image (linhost's own frames)"""
        if not self.starts:
            return '%#x' % a
        i = bisect.bisect_right(self.starts, a) - 1
        if i < 0 or a - self.starts[i] > 0x100000:
            return None
        return self.names[self.starts[i]]

    def chain(self, addrs):
        return [f for f in (self.func(x) for x in addrs) if f is not None]


def parse(path, elf):
    """[(kind, func, chain, count)] of one log; ran: the log shows a run (a summary or a STOPPED line)"""
    out, ran = [], False
    lines = open(path, errors='replace').read().splitlines()
    i = 0
    while i < len(lines):
        l = lines[i]
        if re.match(r'^(STOPPED:|== guest at |linhost: (reached|the guest|fnv-nx guest)|PASS$|FAIL$)', l):
            ran = True
        m = re.match(r'^== trace \((.*)\), thread', l)
        if m:
            why, pc, fr = m.group(1), None, []
            j = i + 1
            while j < len(lines) and lines[j].startswith('   '):
                t = lines[j].strip()
                if t.startswith('pc '):
                    pc = t[3:].strip()
                mm = re.match(r'^#\d+\s+(.*)$', t)
                if mm:
                    fr.append(emu_func(mm.group(1)))
                j += 1
            out.append(('fatal:' + norm_err(why), emu_func(pc) if pc else '?', tuple(fr[:3]), 1))
            i = j
            continue
        m = re.match(r'^\s+stubbed fault: (\S+) .*?\((.*) at (.*)\)$', l)
        if m:
            out.append(('stubbed:' + norm_err(m.group(2)), emu_func(m.group(3).split(' | ')[0]), (m.group(1),), 1))
        m = re.match(r'^skip-summary\t(\d+)\t([^\t]*)\t([^\t]*)\t(.*)$', l)
        if m:
            site = m.group(3).split(' | ')[0]
            out.append(('skipped-init:' + norm_err(m.group(2)), emu_func(site) if site.startswith('0x') else site,
                        (emu_func(m.group(4)),), int(m.group(1))))
        if l.startswith('== hang:'):
            fr, j = [], i + 1
            while j < len(lines) and lines[j].startswith('   thread '):
                mm = re.match(r'^\s+thread \S+ \S+ pc (.*?) frames (.*)$', lines[j])
                if mm and not fr:
                    fr = [emu_func(mm.group(1))] + [emu_func(x) for x in mm.group(2).split(' | ') if x and x != '-']
                j += 1
            out.append(('hang', fr[0] if fr else '?', tuple(fr[1:4]), 1))
            i = j
            continue
        m = re.match(r'^linhost: fault: signal (\d+) thread \d+ pc (0x[0-9a-f]+) lr (0x[0-9a-f]+)', l)
        if m:
            fr = []
            for k in range(i + 1, min(i + 5, len(lines))):
                mm = re.match(r'^linhost: frames (.*)$', lines[k])
                if mm:
                    fr = elf.chain(int(x, 16) for x in mm.group(1).split())
                    break
            out.append(('fatal:signal-' + m.group(1), elf.func(int(m.group(2), 16)) or '%#x' % int(m.group(2), 16),
                        tuple(fr[:3]), 1))
        m = re.match(r'^linhost: skipped initializer (0x[0-9a-f]+): signal (\d+) at pc (0x[0-9a-f]+)', l)
        if m:
            out.append(('skipped-init:signal-' + m.group(2), elf.func(int(m.group(3), 16)) or m.group(3),
                        tuple(elf.chain([int(m.group(1), 16)])), 1))
        if l.startswith('linhost: hang: no progress'):
            fr, j = [], i + 1
            while j < len(lines) and not lines[j].startswith('linhost: hang thread'):
                j += 1
            mm = re.match(r'^linhost: hang thread \d+ pc (0x[0-9a-f]+) lr (0x[0-9a-f]+) frames ?(.*)$',
                          lines[j]) if j < len(lines) else None
            if mm:
                fr = elf.chain([int(mm.group(1), 16)] + [int(x, 16) for x in mm.group(3).split()])
            out.append(('hang', fr[0] if fr else '?', tuple(fr[1:4]), 1))
            ran = True
        i += 1
    return out, ran


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('logs', nargs='+')
    ap.add_argument('--elf')
    ap.add_argument('--prev')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    elf = Elf(a.elf)
    groups, any_ran, seen = OrderedDict(), False, False
    for p in a.logs:
        evs, ran = parse(p, elf)
        any_ran |= ran
        seen |= bool(evs)
        for kind, func, chain, n in evs:
            k = (kind, func, chain)
            g = groups.setdefault(k, {'kind': kind, 'func': func, 'chain': list(chain), 'count': 0, 'logs': []})
            g['count'] += n
            if os.path.basename(p) not in g['logs']:
                g['logs'].append(os.path.basename(p))
    prev = {}
    if a.prev and os.path.isfile(a.prev):
        pj = json.load(open(a.prev))
        prev = {(g['kind'], g['func'], tuple(g['chain'])): g for g in pj.get('groups', []) if g.get('status') != 'fixed'}
    for k, g in groups.items():
        g['status'] = 'known' if k in prev else ('new' if a.prev else 'unclassified')
        g['unstable'] = g['func'].startswith('0x') or any(c.startswith('0x') for c in g['chain'])
    fixed = [dict(g, status='fixed', count=0) for k, g in prev.items() if k not in groups]
    rows = list(groups.values())
    head = {'schema': 'fnvnx-faults/1', 'groups': rows + fixed, 'total_groups': len(rows),
            'total_faults': sum(g['count'] for g in rows), 'new': sum(1 for g in rows if g['status'] == 'new'),
            'known': sum(1 for g in rows if g['status'] == 'known'), 'fixed': len(fixed),
            'fatal': sum(1 for g in rows if g['kind'].startswith('fatal') or g['kind'] == 'hang'),
            'prev': os.path.basename(a.prev) if a.prev else None, 'logs': [os.path.basename(p) for p in a.logs]}
    o = open(a.out, 'w')
    o.write('# faults (D25 #1)\n\n')
    if not seen and not any_ran:
        o.write('- no fault lines and no run summary in the logs\n')
    o.write('- %d fault groups (%d faults); new %d, known %d, fixed %d%s; run-ending (fatal or hang) %d\n' % (
        head['total_groups'], head['total_faults'], head['new'], head['known'], head['fixed'],
        '' if a.prev else ' (no previous head given: unclassified)', head['fatal']))
    if rows or fixed:
        o.write('\n| status | kind | function | callers | count |\n|---|---|---|---|---:|\n')
        order = {'new': 0, 'unclassified': 1, 'known': 2, 'fixed': 3}
        for g in sorted(rows + fixed, key=lambda g: (order[g['status']], -g['count'])):
            o.write('| %s | %s | %s | %s | %d |\n' % (g['status'], g['kind'], g['func'] + (' (address)' if g.get('unstable')
                                                                                             else ''),
                                                    ' < '.join(g['chain']) or '-', g['count']))
    o.close()
    json.dump(head, open(a.out[:-3] + '.json' if a.out.endswith('.md') else a.out + '.json', 'w'), indent=1)
    print('faults: %d groups, new %d, known %d, fixed %d' % (head['total_groups'], head['new'], head['known'],
                                                              head['fixed']))
    return 0 if (seen or any_ran) else 3


if __name__ == '__main__':
    sys.exit(main())
