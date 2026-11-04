#!/usr/bin/env python3
"""permreport.py - S16's per-program verdict for perm.py captures: joins the manifest, the candidate gate and the
conditioning gate (capcompare.py runs), and checks that each frame actually drew something.

    permreport.py --manifest <perm dir>/manifest.json --gate <capgate.json> [--cond <capgate.json>]
                  --ref <reference run dir> --out <dir> [--clear 0xff404040]

Classes per program (one frame each):
  pass          candidate within the bounds
  over          candidate over the bounds, reference well-conditioned: a defect candidate (one side is wrong)
  ill           candidate over, but the reference moves under the 1e-5 input perturbation: no evidence
  vacuous       the reference frame is the clear colour only: the program drew nothing, so the comparison proves
                nothing (counted separately, never as a pass of coverage)
Writes <out>/permreport.json (fnvnx-permreport/1: counts, per-class lists with names and hashes) and
<out>/permreport.md. Both name the game's programs, so they stay private like the captures; the counts are public.
Exit 0.
"""
import argparse, collections, json, os, sys
import numpy as np
from PIL import Image

FORMAT = 'fnvnx-permreport/1'


def frames_of(g):
    return {(c['capture'], f['frame']): f for c in g['captures'] for f in c['frames']}


def drew(path, clear):
    a = np.asarray(Image.open(path).convert('RGB'), dtype=np.int16).reshape(-1, 3)
    return float((np.abs(a - np.array(clear)).max(axis=1) > 0).mean())


def report(man, gate, cond, ref, clear):
    fg, fc = frames_of(gate), frames_of(cond) if cond else {}
    rows = []
    for cap, lst in man['captures'].items():
        for e in lst:
            k = (cap, e['frame'])
            f, c = fg.get(k), fc.get(k)
            p = os.path.join(ref, cap, f"f{e['frame']}.png")
            cov = drew(p, clear) if os.path.isfile(p) else 0.0
            if f is None:
                cls = 'over'
            elif cov == 0.0:
                cls = 'vacuous'
            elif f['pass']:
                cls = 'pass'
            elif c is not None and not c['pass']:
                cls = 'ill'
            else:
                cls = 'over'
            m = (f or {}).get('metrics', {})
            rows.append({'capture': cap, 'frame': e['frame'], 'name': e['name'], 'hash': e['hash'],
                         'stage': e['stage'], 'model': e['model'], 'class': cls, 'coverage': round(cov, 4),
                         'max_abs': m.get('max_abs'), 'mean_abs': m.get('mean_abs'),
                         'frac_over_8': m.get('frac_over_8'),
                         'cond_max_abs': (c or {}).get('metrics', {}).get('max_abs')})
    cnt = collections.Counter(r['class'] for r in rows)
    by_model = collections.defaultdict(collections.Counter)
    for r in rows:
        by_model[r['model']][r['class']] += 1
    return {'format': FORMAT, 'package': man.get('package'), 'programs': len(rows), 'counts': dict(cnt),
            'by_model': {k: dict(v) for k, v in sorted(by_model.items())},
            'skipped': man.get('skipped', []), 'rows': rows}


def md(r):
    L = [f"# S16 permutations: {r['package']}", '',
         f"{r['programs']} programs: " + ', '.join(f'{k} {v}' for k, v in sorted(r['counts'].items())), '',
         '| model | ' + ' | '.join(('pass', 'over', 'ill', 'vacuous')) + ' |', '|---|---|---|---|---|']
    for m, c in r['by_model'].items():
        L.append(f'| {m} | ' + ' | '.join(str(c.get(k, 0)) for k in ('pass', 'over', 'ill', 'vacuous')) + ' |')
    for cls in ('over', 'ill', 'vacuous'):
        L += ['', f'## {cls}', '']
        L += [f"* {x['name']} `{x['hash']}` ({x['model']}, {x['capture']} f{x['frame']}): max {x['max_abs']}, mean "
              f"{x['mean_abs']}, over8 {x['frac_over_8']}, cond max {x['cond_max_abs']}, drew {x['coverage']}"
              for x in r['rows'] if x['class'] == cls] or ['* none']
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifest', required=True); ap.add_argument('--gate', required=True); ap.add_argument('--cond')
    ap.add_argument('--ref', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--clear', default='0xff404040')
    a = ap.parse_args()
    v = int(a.clear, 16)
    clear = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    r = report(json.load(open(a.manifest)), json.load(open(a.gate)), json.load(open(a.cond)) if a.cond else None,
               a.ref, clear)
    os.makedirs(a.out, exist_ok=True)
    json.dump(r, open(os.path.join(a.out, 'permreport.json'), 'w'), indent=1)
    open(os.path.join(a.out, 'permreport.md'), 'w').write(md(r))
    print(f"permreport: {r['programs']} programs, " + ', '.join(f'{k} {v}' for k, v in sorted(r['counts'].items())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
