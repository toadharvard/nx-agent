#!/usr/bin/env python3
"""fuzztriage.py HANDOFFS ABI POP OUT [--patches DIR] - S1b's rows of S13b's fuzz hand-off list, classified by what the
link run and popscan say about the function and its direct callees.

S13b (tools/difffuzz/causes.py) gives S1b three causes: `uninit-arm` (the native result depends on an argument slot
nobody wrote, or an uninitialised local), `native-garbage-arg` (the native passes a garbage word as an argument) and
`callee-saved`. Each row names one fuzzed function F. Classes (column `class`, first that applies):

  patched        F is a site of patches/*.s1b (fixed in the source; the next fuzz run of a later link re-checks it)
  pop-caller     F calls a callee that takes more words than F's call passes (popscan `fewer-donor`/`fewer-other`)
  pop-callee     F itself is a pop-mismatch callee that some caller calls with fewer words
  bridge-callee  F calls a callee whose ABI row is `manual` with a bridge-gap class (varargs, by-value aggregates,
                 struct results, arity): the link has no automatic bridge for that shape -> S2c
  bridge-self    F's own definition has such a row (the harness calls F through the same missing bridge) -> S2c
  xinc-callee    F calls a callee whose row is x86-inconsistent with an ecx / this / result-pointer class (XINC1)
  other          none of these: an uninitialised local or a cause not visible in the ABI tables; read by hand

Environment: DECOMP (fnv_decomp with build/units.json), S11 (pristine objects in $S11/obj/pristine). The output is
decomp-derived: scratch only, never committed.
"""
import argparse
import collections
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import popscan  # noqa: E402
from abiclass import BRIDGE, ECX, classes  # noqa: E402

PORT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
S1B = ('uninit-arm', 'native-garbage-arg', 'callee-saved', 'native-stale-result')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('handoffs')
    ap.add_argument('abi')
    ap.add_argument('pop')
    ap.add_argument('out')
    ap.add_argument('--patches', default=os.path.join(PORT, 'patches'))
    a = ap.parse_args()
    sites = set()
    for p in glob.glob(os.path.join(a.patches, '*.s1b')):
        for m in re.finditer(r'^@@ \w+ (\S+) 0x([0-9a-fA-F]+)', open(p, encoding='utf-8').read(), re.M):
            sites.add(int(m.group(2), 16))
    bridge, xinc = set(), set()
    for line in open(a.abi):
        if line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if len(f) < 7:
            continue
        try:
            ad = int(f[2], 16)
        except ValueError:
            continue
        cl = classes(f)
        if f[5] == 'manual' and cl & BRIDGE:
            bridge.add(ad)
        elif f[5] == 'x86-inconsistent' and cl & ECX:
            xinc.add(ad)
    pop_caller, pop_callee = set(), set()
    for line in open(a.pop):
        if line.startswith('#'):
            continue
        r = line.rstrip('\n').split('\t')
        if r[10] in ('fewer-donor', 'fewer-other') and r[5] != '-':
            pop_caller.add(int(r[5], 16))
            pop_callee.add(int(r[1], 16))
    w = popscan.World(os.environ['DECOMP'], os.environ['S11'])

    def callees(addr):
        out = set()
        for i, sym in w.defs.get(addr, []):
            f = w.func(i, sym)
            if f is None:
                continue
            syms = w.units[i]['symbols']
            for nm, _ in f[1].values():
                t = syms.get(nm)
                if t is not None:
                    out.add(int(t, 16))
            break
        return out

    rows, summ = [], collections.Counter()
    for line in open(a.handoffs):
        f = line.rstrip('\n').split('\t')
        if len(f) < 4 or f[0] != 'S1b' or f[1] not in S1B:
            continue
        addr = int(f[2], 16)
        cs = callees(addr)
        if addr in sites:
            c, why = 'patched', ''
        elif addr in pop_caller:
            c, why = 'pop-caller', ''
        elif addr in pop_callee:
            c, why = 'pop-callee', ''
        elif cs & bridge:
            c, why = 'bridge-callee', ','.join('%08x' % x for x in sorted(cs & bridge))
        elif addr in bridge:
            c, why = 'bridge-self', ''
        elif cs & xinc:
            c, why = 'xinc-callee', ','.join('%08x' % x for x in sorted(cs & xinc))
        else:
            c, why = 'other', ''
        rows.append((f[1], c, f[2], f[3], why, f[4] if len(f) > 4 else ''))
        summ[(f[1], c)] += 1
    with open(a.out, 'w') as o:
        o.write('#cause\tclass\taddress\tsource\tcallees\treproducer\n')
        for r in sorted(rows):
            o.write('\t'.join(r) + '\n')
    for (cause, c), n in sorted(summ.items()):
        print('%-20s %-14s %4d' % (cause, c, n))


if __name__ == '__main__':
    main()
