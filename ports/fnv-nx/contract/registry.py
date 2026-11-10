#!/usr/bin/env python3
"""registry - run every contract gate on one (facts, objfacts) pair and write the verdicts (L1 CONTRACT).

    registry.py --facts facts.pkl --obj objfacts.pkl --n1 n1.pkl --link <link dir> --out <dir> [--only data,call]

A gate is a module `gate_<name>.py` in this directory with

    NAME = '<name>'
    def run(facts, obj, link) -> {'counts': {verdict: n}, 'rows': [(exe_address, verdict, detail)], 'fail': [verdict, ...]}

`counts` covers every function the gate looked at; `rows` lists only the functions whose verdict is in `fail`
(these are the BODYSEL rows: such a function gets the lifted body unless the row is closed).  A gate compares
sets: what the exe function references against what the native function references.  It never runs code.

Outputs in --out:
  gates.json    {gate: {'counts': ..., 'fail_rows': n, 'seconds': s}}   counts only
  bodysel.tsv   address <tab> gate <tab> verdict <tab> detail           exe-derived: fnv-nx-build only
"""
import argparse, importlib, json, os, pickle, sys, time

GATES = ('data', 'call', 'vcall', 'xc')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--obj', required=True)
    ap.add_argument('--link', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--n1', required=True, help='N1 certificate: gives the address of ___security_cookie')
    ap.add_argument('--only', default='')
    a = ap.parse_args()
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    facts = pickle.load(open(a.facts, 'rb'))
    obj = pickle.load(open(a.obj, 'rb'))
    ck = {v for k, v in pickle.load(open(a.n1, 'rb'))['sym'].items() if len(k) == 2 and k[1] == '___security_cookie'}
    if len(ck) != 1:
        sys.exit('___security_cookie is not one address in the N1 map: %r' % sorted(ck))
    facts['cookie'] = ck.pop()
    os.makedirs(a.out, exist_ok=True)
    summary = {}
    rows = []
    for g in (a.only.split(',') if a.only else GATES):
        m = importlib.import_module('gate_' + g)
        t = time.time()
        r = m.run(facts, obj, a.link)
        summary[g] = dict(counts=r['counts'], fail_rows=len(r['rows']), seconds=round(time.time() - t, 1))
        rows += [(addr, g, v, d) for addr, v, d in r['rows']]
        print('%-6s %s fail_rows=%d' % (g, json.dumps(r['counts'], sort_keys=True), len(r['rows'])))
    json.dump(summary, open(os.path.join(a.out, 'gates.json'), 'w'), indent=1, sort_keys=True)
    with open(os.path.join(a.out, 'bodysel.tsv'), 'w') as fh:
        for addr, g, v, d in sorted(rows):
            fh.write('%08x\t%s\t%s\t%s\n' % (addr, g, v, d))


if __name__ == '__main__':
    main()
