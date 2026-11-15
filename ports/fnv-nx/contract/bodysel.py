#!/usr/bin/env python3
"""bodysel - the static body selector (MASTER_PLAN4 R1): NATIVE(f) iff N1 and N2 and N3 and N4, else LIFTED.

    bodysel.py --universe universe.tsv --n1 n1.json --gates <dir with bodysel.tsv of registry.py> [--n3 n3.tsv] --out bodysel.tsv

Inputs are static facts only: the universe of code entries (L3: every entry of the exe, including funclets,
catch blocks, tail targets and the PE entry point), the N1 certificate, the object gates of N2 and L2's compiler
plugin verdict for N3 (address <tab> ok|ok+E|reason <tab> inlined callee addresses...; `ok+E`: the body is a
`__sdd` that implements the vector branch, flags & 2, of `??_E`).  No fuzz, boot or scenario
output is read: the argument list is the whole key of this stage (R13).
  N1  byte identity with the exe under one symbol map            (n1.py)
  N2  object gates on every site: data, call, vcall               (registry.py rows; a row = not N2)
  N3  GIMPLE inside the portable subset                           (L2 plugin; no verdict = not N3)
  N4  closure: everything inlined into f is NATIVE                (greatest fixed point over N3's inline lists)
A native body that references data or a symbol with no exe address cannot pass N1/N2 (its relocations are not
the exe's), so it is LIFTED here; a lifted body refers to exe addresses only by construction.  The link
therefore has no "unresolved" decision of its own: an unresolved reference there is an assert,
"N1/certificate violated: tool defect", expected 0 times.
The selector is total: every code entry of the universe gets NATIVE with its certificate, or LIFTED with
the first reason.  Incompleteness costs speed, never correctness.
Gate `bodysel` ($GATE_OUT/bodysel.json, gatefile.py): universe = the code addresses of the fact base, certified = those
with a verdict (totality).  The result is a function of the argument files only; rows are written sorted.
"""
import argparse, collections, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
import gatefile


def decide(universe, n1, gate_rows, n3):
    """universe: {code entry address: kind} (L3's universe.tsv: every code entry of the module)."""
    out = {}
    for a, kind in universe.items():
        N = n1['fn'].get(a)
        if N is None:
            out[a] = ['LIFTED', 'not a decomp function (%s)' % kind]
        elif not N['n1']:
            out[a] = ['LIFTED', 'N1: ' + N['why']]
        elif a in gate_rows:
            out[a] = ['LIFTED', 'N2: gate ' + gate_rows[a]]
        elif a not in n3:
            out[a] = ['LIFTED', 'N3: no verdict']
        elif n3[a][0] not in ('ok', 'ok+E'):
            out[a] = ['LIFTED', 'N3: ' + n3[a][0]]
        elif N['sym'].startswith('??_E') and n3[a][0] != 'ok+E':
            # the exe function is a vector deleting destructor (flags & 2); the native __sdd must implement it
            out[a] = ['LIFTED', 'N3: ??_E without the vector branch of __sdd']
        else:
            out[a] = ['NATIVE', 'N1 N2 N3']
    chg = True                      # N4: greatest fixed point
    while chg:
        chg = False
        for a, v in out.items():
            if v[0] == 'NATIVE' and any(out.get(c, ['LIFTED'])[0] != 'NATIVE' for c in n3[a][1]):
                out[a] = ['LIFTED', 'N4: an inlined callee is not NATIVE']
                chg = True
    for v in out.values():
        if v[0] == 'NATIVE':
            v[1] = 'N1 N2 N3 N4'
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--universe', required=True, help="L3's universe.tsv: address, kind, extent, flags")
    ap.add_argument('--n1', required=True)
    ap.add_argument('--gates', required=True)
    ap.add_argument('--n3')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    rows = {}
    for ln in open(os.path.join(a.gates, 'bodysel.tsv')):
        p = ln.rstrip('\n').split('\t')
        rows.setdefault(int(p[0], 16), p[1] + ' ' + p[2])
    n3 = {}
    if a.n3:
        for ln in open(a.n3):
            p = ln.rstrip('\n').split('\t')
            n3[int(p[0], 16)] = (p[1], [int(x, 16) for x in p[2:] if x])
    universe = {}
    for ln in open(a.universe):
        p = ln.rstrip('\n').split('\t')
        if len(p) >= 2 and p[0][:1] != '#':
            universe[int(p[0], 16)] = p[1]
    n1 = store.load(a.n1, 'n1')
    missing = sorted(x for x, F in n1['fn'].items() if x not in universe)
    if missing:           # invariant: the universe holds every decomp function
        sys.exit('universe/certificate mismatch: %d N1 addresses are not code entries, first %08x: tool defect' % (len(missing), missing[0]))
    out = decide(universe, n1, rows, n3)
    with open(a.out, 'w') as fh:
        for addr, (v, why) in sorted(out.items()):
            fh.write('%08x\t%s\t%s\n' % (addr, v.lower(), why))
    # gate `bodysel` (L4): totality - every code address of the fact base has NATIVE (certificate) or LIFTED (reason)
    bad = ['%08x' % x for x, (v, why) in sorted(out.items()) if not (v in ('NATIVE', 'LIFTED') and why)]
    gatefile.write('bodysel', len(out), len(out) - len(bad), bad,
                   'native %d, lifted %d' % (sum(1 for v, _ in out.values() if v == 'NATIVE'), sum(1 for v, _ in out.values() if v == 'LIFTED')))
    c = collections.Counter((v, why.split(':')[0]) for v, why in out.values())
    for k, n in sorted(c.items(), key=lambda x: -x[1]):
        print('%7d  %s  %s' % (n, k[0], k[1]))


if __name__ == '__main__':
    main()
