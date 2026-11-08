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
With --pref <perturbed reference run> --pmanifest <its manifest> (perm.py --perturb 1e-5 over the programs that
are over, rendered by the reference only), a program that is over is ill when the reference itself moves under the
perturbation where the candidate differs: at least 90 % of the candidate's pixels off by more than 8 lie within one
pixel of a pixel the perturbed reference moved by more than 8. Elsewhere it stays over: an unstable pixel somewhere
else does not excuse a difference. --pref/--pmanifest repeat (pairs in order, perm.py --perturb-seed 1..N): the
moved pixels are the union over the draws. One draw is not enough where the unstable pixels are sparse (isolated
saturated flips of an amplified rounding difference, the large set's constants in [-1000, 1000]): each draw, and
the candidate, flips another sample of the same unstable set, so a single draw covers a quarter of the candidate's
pixels and the union of 8 all of them (S16 large sweep, 27 PS programs).
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


def over8(a, b):
    """mask of the pixels that differ by more than 8 in any channel"""
    x = np.asarray(Image.open(a).convert('RGB'), dtype=np.int16)
    y = np.asarray(Image.open(b).convert('RGB'), dtype=np.int16)
    return np.abs(x - y).max(axis=2) > 8 if x.shape == y.shape else None


def explained(ref_png, cand_png, pref_pngs, share=0.9):
    """the candidate's differing pixels lie where the perturbed references move (union over the draws, dilated by
    one pixel)"""
    d, u = over8(ref_png, cand_png), None
    for pp in pref_pngs:
        m = over8(ref_png, pp)
        if m is not None:
            u = m if u is None else (u | m)
    if d is None or u is None or not u.any():
        return False
    if not d.any():
        return True
    du = u.copy()
    du[1:, :] |= u[:-1, :]; du[:-1, :] |= u[1:, :]
    u2 = du.copy()
    u2[:, 1:] |= du[:, :-1]; u2[:, :-1] |= du[:, 1:]
    return float((d & u2).sum()) / float(d.sum()) >= share


def report(man, gate, cond, ref, clear, cand=None, pref=None, pman=None):
    """pref, pman: one perturbed reference run and its manifest, or lists of them (one pair per draw)"""
    fg, fc = frames_of(gate), frames_of(cond) if cond else {}
    prefs = pref if isinstance(pref, list) else ([pref] if pref else [])
    pmans = pman if isinstance(pman, list) else ([pman] if pman else [])
    pfrs = [{e['hash']: (c, e['frame']) for c, lst in (m or {}).get('captures', {}).items() for e in lst} for m in pmans]
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
            if cls == 'over' and f is not None and prefs and cand:
                pps = [os.path.join(pr, pfr[e['hash']][0], f"f{pfr[e['hash']][1]}.png")
                       for pr, pfr in zip(prefs, pfrs) if e['hash'] in pfr]
                pps = [x for x in pps if os.path.isfile(x)]
                cp = os.path.join(cand, cap, f"f{e['frame']}.png")
                if pps and os.path.isfile(cp) and explained(p, cp, pps):
                    cls = 'ill'
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
    ap.add_argument('--cand', help='the candidate run (with --pref)')
    ap.add_argument('--pref', action='append', help='the reference run of the --perturb captures (repeat: one per draw)')
    ap.add_argument('--pmanifest', action='append', help='their manifest (repeat, in the order of --pref)')
    a = ap.parse_args()
    v = int(a.clear, 16)
    clear = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    r = report(json.load(open(a.manifest)), json.load(open(a.gate)), json.load(open(a.cond)) if a.cond else None,
               a.ref, clear, a.cand, a.pref, [json.load(open(x)) for x in a.pmanifest] if a.pmanifest else None)
    os.makedirs(a.out, exist_ok=True)
    json.dump(r, open(os.path.join(a.out, 'permreport.json'), 'w'), indent=1)
    open(os.path.join(a.out, 'permreport.md'), 'w').write(md(r))
    print(f"permreport: {r['programs']} programs, " + ', '.join(f'{k} {v}' for k, v in sorted(r['counts'].items())))
    return 0


if __name__ == '__main__':
    sys.exit(main())
