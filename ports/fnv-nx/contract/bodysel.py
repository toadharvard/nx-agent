#!/usr/bin/env python3
"""bodysel - the static body selector (MASTER_PLAN4 R1): NATIVE(f) iff N1 and N2 and N3 and N4, else LIFTED.

    bodysel.py --facts facts.pkl --n1 n1.pkl --gates <dir with bodysel.tsv of registry.py> [--n3 n3.tsv] --out bodysel.tsv

Inputs are static facts only: the exe fact base, the N1 certificate, the object gates of N2 and L2's compiler
plugin verdict for N3 (address <tab> ok|reason <tab> inlined callee addresses...).  No fuzz, boot or scenario
output is read: the argument list is the whole key of this stage (R13).
  N1  byte identity with the exe under one symbol map            (n1.py)
  N2  object gates on every site: data, call, vcall               (registry.py rows; a row = not N2)
  N3  GIMPLE inside the portable subset                           (L2 plugin; no verdict = not N3)
  N4  closure: everything inlined into f is NATIVE                (greatest fixed point over N3's inline lists)
A native body that references data or a symbol with no exe address cannot pass N1/N2 (its relocations are not
the exe's), so it is LIFTED here; a lifted body refers to exe addresses only by construction.  The link
therefore has no "unresolved" decision of its own: an unresolved reference there is an assert,
"N1/certificate violated: tool defect", expected 0 times.
The selector is total: every code address of the fact base gets NATIVE with its certificate, or LIFTED with
the first reason.  Incompleteness costs speed, never correctness.
"""
import argparse, collections, os, pickle


def decide(facts, n1, gate_rows, n3):
    out = {}
    for a, F in facts['fn'].items():
        N = n1['fn'].get(a)
        if N is None:
            out[a] = ['LIFTED', 'not a decomp function']
        elif not N['n1']:
            out[a] = ['LIFTED', 'N1: ' + N['why']]
        elif a in gate_rows:
            out[a] = ['LIFTED', 'N2: gate ' + gate_rows[a]]
        elif a not in n3:
            out[a] = ['LIFTED', 'N3: no verdict']
        elif n3[a][0] != 'ok':
            out[a] = ['LIFTED', 'N3: ' + n3[a][0]]
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
    ap.add_argument('--facts', required=True)
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
    out = decide(pickle.load(open(a.facts, 'rb')), pickle.load(open(a.n1, 'rb')), rows, n3)
    with open(a.out, 'w') as fh:
        for addr, (v, why) in sorted(out.items()):
            fh.write('%08x\t%s\t%s\n' % (addr, v.lower(), why))
    c = collections.Counter((v, why.split(':')[0]) for v, why in out.values())
    for k, n in sorted(c.items(), key=lambda x: -x[1]):
        print('%7d  %s  %s' % (n, k[0], k[1]))


if __name__ == '__main__':
    main()
