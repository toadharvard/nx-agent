#!/usr/bin/env python3
"""registry - run every contract gate on one (facts, objfacts) pair and write the verdicts (L1 CONTRACT).

    registry.py --facts facts.json --obj obj.json --n1 n1.json --link <link dir> --out <dir> [--only data,call]

A gate is a module `gate_<name>.py` in this directory with

    NAME = '<name>'
    def run(facts, obj, link) -> {'counts': {verdict: n}, 'rows': [(exe_address, verdict, detail)], 'fail': [verdict, ...],
                                  'judged': n}

`counts` covers every function the gate looked at, `judged` is their number; `rows` lists only the functions whose
verdict is in `fail` (these are the BODYSEL rows: such a function gets the lifted body unless the row is closed).  A
gate compares sets: what the exe function references against what the native function references.  It never runs code.
The gate modules are imported by name in the table below (no dynamic import).

Outputs in --out:
  gates.json    {gate: {'counts': ..., 'fail_rows': n}}   counts only, no timing
  bodysel.tsv   address <tab> gate <tab> verdict <tab> detail           exe-derived: fnv-nx-build only, sorted, LF

These are NOT failure gates.  A function with a row is not N2; bodysel.py turns it into LIFTED, which is a
legal outcome.  The counts are a metric (share of native bodies) and an input of BODYSEL: this tool writes no
gate file.  The verdict is red only on a violated invariant (bodysel totality, xunit, rtti, pcrel, link).
"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_data, gate_call, gate_vcall, gate_xc, store

ORDER = ('data', 'call', 'vcall', 'xc')
GATES = {'data': (gate_data, 'n2_data'), 'call': (gate_call, 'n2_call'), 'vcall': (gate_vcall, 'n2_vcall'),
         'xc': (gate_xc, 'xc')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--facts', required=True)
    ap.add_argument('--obj', required=True)
    ap.add_argument('--link', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--n1', required=True, help='N1 certificate: gives the address of ___security_cookie')
    ap.add_argument('--only', default='')
    a = ap.parse_args()
    facts = store.load(a.facts, 'facts')
    n1 = store.load(a.n1, 'n1')
    obj = store.load(a.obj, 'obj')
    ck = {v for k, v in n1['sym'].items() if len(k) == 2 and k[1] == '___security_cookie'}
    if len(ck) != 1:
        sys.exit('___security_cookie is not one address in the N1 map: %r' % sorted(ck))
    facts['cookie'] = ck.pop()
    facts['pristine'] = gate_call.pristine_names(n1)
    os.makedirs(a.out, exist_ok=True)
    summary = {}
    rows = []
    for g in (a.only.split(',') if a.only else ORDER):
        m, fname = GATES[g]
        r = m.run(facts, obj, a.link)
        summary[g] = dict(counts=r['counts'], fail_rows=len(r['rows']))
        rows += [(addr, g, v, d) for addr, v, d in r['rows']]
        print('%-6s %s fail_rows=%d' % (g, json.dumps(r['counts'], sort_keys=True), len(r['rows'])))
    with open(os.path.join(a.out, 'gates.json'), 'w', newline='\n') as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
        fh.write('\n')
    with open(os.path.join(a.out, 'bodysel.tsv'), 'w', newline='\n') as fh:
        for addr, g, v, d in sorted(rows):
            fh.write('%08x\t%s\t%s\t%s\n' % (addr, g, v, d))


if __name__ == '__main__':
    main()
