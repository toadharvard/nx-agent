#!/usr/bin/env python3
"""abiclass.py ABI_TSV POP_TSV OUT_TSV [--patches DIR] - S1b verdicts for a link run's open ABI pairs, ranked by
how close the calling function is to the boot (docs/PATCHES.md, "ABI verdicts").

Rows read from fnvlink's abi.tsv: every `x86-inconsistent` row whose classes are not only `pop-mismatch` (what
tools/gates/ready.py counts as unexplained) and every `manual` row. popscan.py's table (POP_TSV, same link) gives
each pop-mismatch call site its family. Verdicts (column `verdict`):

  patched            the calling function is an S1b site (patches/*.s1b): fixed in the source, the next link
                     should drop the row
  benign-more        the reference pushes more words than the callee takes: extra arguments, ignored natively
  benign-unread      the callee takes words nobody passes but never reads them
  benign-nocall      no call of the reference in the pristine object (address taken or declaration only)
  benign-conv        only who pops differs (the link's moves cover the positions)
  fix-donor          the missing words were pushed for an earlier call of the same caller (popgen.py's family,
                     not generated: see its manual.tsv)
  fix-other          the callee reads words nobody passes; read by hand
  ecx                this / ecx / result-pointer classes (XINC1 rounds 1-4 domain: xinc1/analyze.py)
  bridge-gap         the link has no automatic bridge for the shape (manual verdict): varargs, aggregates by value,
                     struct results; an S2c item
  other              anything else

`boot` is the shortest x86 call distance from the boot roots (WinMain 0x0086a850 and every dynamic initializer
`??__E`) to the calling function, following direct calls and the function tables (vftables) a function refers to,
in the pristine MSVC objects; `-` = not reached that way (virtual dispatch through objects made elsewhere, callbacks).

Environment: DECOMP (fnv_decomp with build/units.json), S11 (pristine objects in $S11/obj/pristine). Output is
decomp-derived (addresses, symbols): scratch only, never committed.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, os.path.join(PORT, 'tools', 'verify', 'v1_link'))
import coff_x86  # noqa: E402

WINMAIN = 0x0086a850
ECX = {'this-not-passed', 'ecx-not-passed', 'ecx-unused', 'result-pointer-not-passed', 'result-moves',
       'result-buffer-not-written', 'cast-conflict', 'edx-dummy'}
BRIDGE = {'varargs', 'aggregate-param', 'ret-aggregate', 'arity', 'sret-explicit', 'reg-shift'}


def classes(r):
    return set(re.sub(r'\([^)]*\)', '', r[6]).split(','))


def call_graph(decomp, s11):
    """{address: set(callee addresses)} over the pristine objects, and the root set."""
    units = json.load(open(os.path.join(decomp, 'build', 'units.json')))['units']
    funcs = {int(f['address'], 16) for u in units for f in u['functions']}
    g = collections.defaultdict(set)
    roots = {WINMAIN}
    for i, u in enumerate(units):
        p = '%s/obj/pristine/u%d.obj' % (s11, i)
        if not os.path.exists(p):
            continue
        try:
            o = coff_x86.Coff(p)
        except ValueError:
            continue
        syms = u['symbols']
        data_targets = {}

        def table(name):
            """Function addresses a data symbol of this object (a vftable, a callback table) refers to."""
            if name in data_targets:
                return data_targets[name]
            out = set()
            k = o.by_name.get(name)
            if k is not None:
                secno = o.syms[k][2]
                if secno > 0 and not (o.secs[secno - 1][5] & 0x20):
                    for nm, _ in o.relocs(secno).values():
                        a = syms.get(nm)
                        if a is not None and int(a, 16) in funcs:
                            out.add(int(a, 16))
            data_targets[name] = out
            return out

        for f in u['functions']:
            a = int(f['address'], 16)
            if f['symbol'].startswith('??__E'):
                roots.add(a)
            r = o.func(f['symbol'])
            if r is None:
                continue
            for nm, _ in r[1].values():
                t = syms.get(nm)
                if t is not None and int(t, 16) in funcs:
                    g[a].add(int(t, 16))
                else:
                    g[a] |= table(nm)
    return g, roots


def distances(g, roots):
    d = {r: 0 for r in roots}
    q = collections.deque(roots)
    while q:
        a = q.popleft()
        for b in g.get(a, ()):
            if b not in d:
                d[b] = d[a] + 1
                q.append(b)
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('abi')
    ap.add_argument('pop')
    ap.add_argument('out')
    ap.add_argument('--patches', default=os.path.join(PORT, 'patches'))
    a = ap.parse_args()
    sites = set()
    for p in glob.glob(os.path.join(a.patches, '*.s1b')):
        for m in re.finditer(r'^@@ \w+ (\S+) (0x[0-9a-fA-F]+)', open(p, encoding='utf-8', errors='replace').read(),
                             re.M):
            sites.add((m.group(1), int(m.group(2), 16)))
    pop = collections.defaultdict(list)          # (src, callee, ref) -> [(caller, family)]
    for l in open(a.pop):
        if l.startswith('#'):
            continue
        f = l.rstrip('\n').split('\t')
        pop[(f[0], f[1], f[2])].append((int(f[5], 16) if f[5] != '-' else None, f[10]))
    g, roots = call_graph(os.environ['DECOMP'], os.environ['S11'])
    dist = distances(g, roots)
    rows = []
    for l in open(a.abi):
        if l.startswith('#'):
            continue
        f = l.rstrip('\n').split('\t')
        if len(f) < 7:
            continue
        cl = classes(f)
        if not ((f[5] == 'x86-inconsistent' and cl - {'pop-mismatch'}) or f[5] == 'manual'):
            continue
        src = f[0].replace('#vtables', '')
        calls = pop.get((src, f[2], f[3]), [])
        if f[5] == 'manual':
            v = 'bridge-gap' if cl & BRIDGE else 'other'
            calls = calls or [(None, None)]
            for c, fam in calls:
                rows.append((f, src, c, v))
            continue
        if cl & ECX:
            for c, fam in calls or [(None, None)]:
                rows.append((f, src, c, 'patched' if c is not None and (src, c) in sites else 'ecx'))
            continue
        if not calls:
            rows.append((f, src, None, 'other'))
            continue
        for c, fam in calls:
            if c is not None and (src, c) in sites:
                v = 'patched'
            else:
                v = {'more': 'benign-more', 'fewer-unread': 'benign-unread', 'nocaller': 'benign-nocall',
                     'conv-only': 'benign-conv', 'fewer-donor': 'fix-donor', 'fewer-other': 'fix-other'}.get(fam,
                                                                                                         'other')
            rows.append((f, src, c, v))
    with open(a.out, 'w') as out:
        out.write('#verdict\tboot\tsrc\tcaller\tcallee\tref_msvc\tabi_verdict\tclasses\n')
        for f, src, c, v in sorted(rows, key=lambda x: (x[3], dist.get(x[2], 1 << 20), x[1])):
            b = dist.get(c)
            out.write('\t'.join([v, '-' if b is None else str(b), src, '%08x' % c if c else '-', f[2], f[3], f[5],
                                 f[6]]) + '\n')
    # summary: pairs (abi rows) by verdict of their worst call, and calling functions by boot distance
    by_pair = collections.defaultdict(set)
    for f, src, c, v in rows:
        by_pair[(f[5], src, f[2], f[3])].add(v)
    rank = ['fix-other', 'fix-donor', 'ecx', 'bridge-gap', 'other', 'patched', 'benign-more', 'benign-unread',
            'benign-nocall', 'benign-conv']
    summ = collections.Counter()
    for (kind, *_), vs in by_pair.items():
        summ[(kind, min(vs, key=rank.index))] += 1
    print('pairs (abi rows) by verdict of their worst call:')
    for (kind, v), n in sorted(summ.items()):
        print('  %-16s %-14s %5d' % (kind, v, n))
    print('boot roots %d, functions reached %d' % (len(roots), len(dist)))
    callers = collections.defaultdict(set)
    for f, src, c, v in rows:
        if c is not None:
            callers[v].add(c)
    for v in rank:
        if v not in callers:
            continue
        ds = [dist.get(c) for c in callers[v]]
        near = sum(1 for d in ds if d is not None and d <= 4)
        reach = sum(1 for d in ds if d is not None)
        print('  %-14s callers %4d  boot<=4 %4d  reached %4d' % (v, len(ds), near, reach))


if __name__ == '__main__':
    main()
