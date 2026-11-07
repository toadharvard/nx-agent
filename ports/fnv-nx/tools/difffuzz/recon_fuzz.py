#!/usr/bin/env python3
"""recon_fuzz.py - D23 (b) acceptance fuzzing of reconstructed functions against the exe itself (FNVNX_ORACLE=exe).

    recon_fuzz.py targets <recon dir> <catalog.jsonl> <targets.jsonl> <plan.json>
    recon_fuzz.py report <plan.json> <campaign dir> <report.md> <summary.json>

<recon dir>: one subdirectory per recon branch (its files: recon.json, ...), as tools/farm/recon.sh fetches them.
`targets` maps each recon's functions (recon.json `functions`: address, symbol, size) to the prepared tree's unit
(FNVNX_TREE build/units.json, where REC1 appended them) and the exe-oracle catalog row (catalog.py with
FNVNX_ORACLE=exe), and writes the campaign's targets plus a plan: per recon, per function, either the target or the
reason it cannot run (not in this head's tree: the prepare predates the recon; no native object; not modelled).
`report` gives each function a verdict: clean (every execution agrees, no divergence), hard, soft, or not run, and
the recon a verdict: `fuzz-clean` only when all its functions are clean. The summary is fnvnx-recon-fuzz/1, the
record recon.json's `"accept": "fuzz"` cites. Nothing here contains exe or decomp bytes: addresses, counts, causes.
"""
import json
import sys
from pathlib import Path

import config


def _recons(d):
    for sub in sorted(Path(d).iterdir()) if Path(d).is_dir() else []:
        for rj in sorted(sub.rglob('recon.json')):
            try:
                yield sub.name, rj, json.loads(rj.read_text())
            except ValueError as e:
                yield sub.name, rj, {'error': str(e)}


def cmd_targets(recon_dir, cat, out, plan_out):
    units = json.load(open(config.TREE / 'build/units.json'))['units']
    by_addr = {}
    for i, u in enumerate(units):
        for f in u['functions']:
            by_addr.setdefault(int(f['address'], 16), (i, u['source']))
    rows = {}
    for line in open(cat):
        r = json.loads(line)
        rows[int(r['address'], 16)] = r
    plan, targets = [], []
    for branch, rj, rec in _recons(recon_dir):
        entry = dict(branch='recon/' + branch, file=str(rj.relative_to(recon_dir)), unit=rec.get('unit'),
                     functions=[])
        if 'error' in rec:
            entry['error'] = 'recon.json: ' + rec['error']
        for f in rec.get('functions', []):
            a = int(f['address'], 16)
            fe = dict(address='%08x' % a, symbol=f.get('symbol'), size=f.get('size'))
            r = rows.get(a)
            if a not in by_addr:
                fe['not_run'] = 'not in this head\'s prepared tree (its prepare predates the recon, or REC1 skipped it)'
            elif r is None:
                fe['not_run'] = 'no exe-oracle catalog row'
            elif 'error' in r:
                fe['not_run'] = 'exe oracle: ' + r['error']
            elif config.a64_object(r['source']) is None:
                fe['not_run'] = 'no native object for ' + r['source']
            elif r['unresolved']:
                fe['not_run'] = 'not modelled (unresolved)'
            else:
                ncalls = len(r['calls']) + len(r['tails']) + r['indirect']
                targets.append(dict(unit=r['unit'], source=r['source'], address=r['address'], symbol=r['symbol'],
                                    size=r['size'], calls=r['calls'], x87=r['x87'], sse=r['sse'], fs=r['fs'],
                                    kind='leaf' if ncalls == 0 else 'calls%d' % ncalls))
            entry['functions'].append(fe)
        plan.append(entry)
    with open(out, 'w') as h:
        for t in targets:
            h.write(json.dumps(t) + '\n')
    json.dump(plan, open(plan_out, 'w'), indent=1)
    print('%d recons, %d functions, %d targets' % (len(plan), sum(len(e['functions']) for e in plan), len(targets)))


def cmd_report(plan_path, camp, out, summary):
    plan = json.load(open(plan_path))
    res = {}
    p = Path(camp) / 'results.jsonl'
    for line in open(p) if p.exists() else []:
        r = json.loads(line)
        res[r['address']] = r
    lines, recs, tot = [], [], dict(clean=0, hard=0, soft=0, not_run=0)
    for e in plan:
        fv = []
        for f in e['functions']:
            r = res.get(f['address'])
            if 'not_run' in f:
                v, why = 'not run', f['not_run']
            elif r is None:
                v, why = 'not run', 'no result (stopped or crashed before it)'
            elif not r.get('status', '').startswith('ok'):
                v, why = 'not run', 'harness: ' + r.get('status', '?')
            else:
                divs = r.get('divergences') or []
                hard = [d for d in divs if not d.get('soft')]
                v = 'hard' if hard else 'soft' if divs else 'clean'
                why = ', '.join(sorted({'%s/%s' % (d.get('kind'), d.get('cause')) for d in divs})) or \
                    '%d executions' % r.get('execs', 0)
            tot[v.replace(' ', '_')] += 1
            fv.append(dict(address=f['address'], verdict=v, detail=why, execs=(r or {}).get('execs', 0)))
            lines.append('| %s | %s | 0x%s | %s | %s |' % (e['branch'], e.get('unit') or '', f['address'], v, why))
        ok = bool(fv) and all(x['verdict'] == 'clean' for x in fv) and 'error' not in e
        recs.append(dict(branch=e['branch'], unit=e.get('unit'), verdict='fuzz-clean' if ok else 'not-accepted',
                         functions=fv, error=e.get('error')))
    s = dict(format='fnvnx-recon-fuzz/1', oracle='exe', recons=len(recs),
             accepted=sum(r['verdict'] == 'fuzz-clean' for r in recs), functions=tot, records=recs)
    json.dump(s, open(summary, 'w'), indent=1)
    md = ['- recons: %d, fuzz-clean %d' % (s['recons'], s['accepted']),
          '- functions: clean %(clean)d, hard %(hard)d, soft %(soft)d, not run %(not_run)d' % tot, '',
          '| branch | unit | address | verdict | detail |', '|---|---|---|---|---|'] + lines
    md += ['', '```json fnvnx-recon-fuzz', json.dumps(dict((k, v) for k, v in s.items() if k != 'records')), '```']
    Path(out).write_text('\n'.join(md) + '\n')
    print('recons %d, fuzz-clean %d, functions %s' % (s['recons'], s['accepted'], tot))


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == 'targets':
        cmd_targets(*sys.argv[2:])
    elif len(sys.argv) == 6 and sys.argv[1] == 'report':
        cmd_report(*sys.argv[2:])
    else:
        print(__doc__)
        sys.exit(2)
