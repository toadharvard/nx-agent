#!/usr/bin/env python3
"""capcompare.py - S16-compare's frame verdict for captures: the candidate's frames (nx_render) against the
reference's (nxd9play under Wine), with the reference's own repeat as the noise floor.

    capcompare.py --ref <run> --ref2 <run> --cand <run> --out <dir> [--diffs] [--json-only]

A run is replay.py's directory: <capture>/f<frame %05u>.png. Per frame: PSNR, SSIM, FLIP, max/mean absolute
difference and the share of pixels off by more than 8 (metrics.py). Writes <out>/capgate.json (fnvnx-capgate/1)
and <out>/capreport.md, numbers only. Exit 0 pass, 1 fail, 2 incomplete (a capture or frame missing on a side, or the
reference does not repeat itself), 3 usage.

The metric and its thresholds are THRESHOLDS below (decision recorded in journal/inbox/S16-compare-*.md):
* noise floor: ref vs ref2 must be identical on every frame (WineD3D on llvmpipe is deterministic for one build);
  a floor that is not zero is reported as "unstable" and the frame is judged against the floor instead (below);
* a candidate frame passes when PSNR >= 40 dB, SSIM >= 0.98, FLIP <= 0.05 (when FLIP is installed) and at most
  0.5 % of the pixels differ by more than 8 of 255 in any channel. With a non-zero floor, each bound moves by the
  floor (PSNR floor - 3 dB, SSIM floor - 0.01, FLIP floor + 0.02, share floor + 0.5 %), never past the hard limits.
"""
import argparse, datetime, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metrics

FORMAT = 'fnvnx-capgate/1'
THRESHOLDS = {
    'frame': {'psnr_min': 40.0, 'ssim_min': 0.98, 'flip_max': 0.05, 'frac_over_8_max': 0.005},
    'floor_margin': {'psnr': 3.0, 'ssim': 0.01, 'flip': 0.02, 'frac_over_8': 0.005},
    'hard': {'psnr_min': 30.0, 'ssim_min': 0.90, 'flip_max': 0.15, 'frac_over_8_max': 0.05},
}


def frames(run, cap):
    d = os.path.join(run, cap)
    if not os.path.isdir(d): return []
    return sorted(f for f in os.listdir(d) if f.startswith('f') and f.endswith('.png') and '_d' not in f)


def captures(run):
    return sorted(d for d in os.listdir(run) if os.path.isdir(os.path.join(run, d))) if os.path.isdir(run) else []


def pv(m):  # identical: PSNR inf, counted as 100 dB
    return 100.0 if m.get('identical') else m['psnr']


def pair(a, b, cap, f):
    try:
        return metrics.compare(metrics.load(os.path.join(a, cap, f)), metrics.load(os.path.join(b, cap, f)))
    except (ValueError, OSError) as e:
        return {'error': str(e)}


def bounds(floor):
    t, mg, hd = THRESHOLDS['frame'], THRESHOLDS['floor_margin'], THRESHOLDS['hard']
    b = dict(t)
    if floor and not floor.get('identical') and 'error' not in floor:
        b['psnr_min'] = max(hd['psnr_min'], min(t['psnr_min'], pv(floor) - mg['psnr']))
        b['ssim_min'] = max(hd['ssim_min'], min(t['ssim_min'], floor['ssim'] - mg['ssim']))
        if floor.get('flip') is not None:
            b['flip_max'] = min(hd['flip_max'], max(t['flip_max'], floor['flip'] + mg['flip']))
        b['frac_over_8_max'] = min(hd['frac_over_8_max'], max(t['frac_over_8_max'], floor['frac_over_8'] + mg['frac_over_8']))
    return b


def judge(m, b):
    if 'error' in m: return [m['error']]
    r = []
    if pv(m) < b['psnr_min']: r.append(f"psnr {pv(m):.2f} < {b['psnr_min']:.2f}")
    if m['ssim'] < b['ssim_min']: r.append(f"ssim {m['ssim']:.4f} < {b['ssim_min']:.4f}")
    if m.get('flip') is not None and m['flip'] > b['flip_max']: r.append(f"flip {m['flip']:.4f} > {b['flip_max']:.4f}")
    if m['frac_over_8'] > b['frac_over_8_max']: r.append(f"over8 {m['frac_over_8']:.4f} > {b['frac_over_8_max']:.4f}")
    return r


def compare(ref, ref2, cand, out, diffs=False):
    caps = sorted(set(captures(ref)) | set(captures(cand)))
    res = []
    for c in caps:
        e = {'capture': c, 'frames': [], 'reasons': []}
        fr, fc = frames(ref, c), frames(cand, c)
        if not fr or not fc:
            e['status'] = 'incomplete'; e['reasons'].append('no reference frames' if not fr else 'no candidate frames')
            # a reference frame the candidate did not render counts as a frame over the threshold
            e['frames'] = [{'frame': f[1:-4], 'metrics': {'error': 'missing in the candidate'}, 'floor': None,
                            'pass': False, 'reasons': ['missing in the candidate']} for f in fr]
            res.append(e); continue
        for f in fr:
            if f not in fc:
                e['reasons'].append(f'{f}: missing in the candidate'); e['missing'] = True
                e['frames'].append({'frame': f[1:-4], 'metrics': {'error': 'missing in the candidate'}, 'floor': None,
                                    'pass': False, 'reasons': ['missing in the candidate']})
                continue
            floor = pair(ref, ref2, c, f) if ref2 and f in frames(ref2, c) else None
            m = pair(ref, cand, c, f)
            b = bounds(floor)
            why = judge(m, b)
            fe = {'frame': f[1:-4], 'metrics': m, 'floor': floor, 'bounds': b, 'pass': not why, 'reasons': why}
            if floor is None: e['no_floor'] = True
            elif not floor.get('identical'): e['unstable'] = True
            if diffs and 'error' not in m:
                os.makedirs(os.path.join(out, 'diffs'), exist_ok=True)
                metrics.diff_image(metrics.load(os.path.join(ref, c, f)), metrics.load(os.path.join(cand, c, f)),
                                   os.path.join(out, 'diffs', f'{c}_{f}'))
            e['frames'].append(fe)
            e['reasons'] += [f"{f}: {w}" for w in why]
        extra = [f for f in fc if f not in fr]
        if extra: e['reasons'].append(f'{len(extra)} frames only in the candidate'); e['missing'] = True
        failed = any(not x['pass'] for x in e['frames'])
        e['status'] = 'fail' if failed else ('incomplete' if e.get('missing') or not e['frames'] else
                                            ('unstable' if e.get('unstable') else 'pass'))
        ms = [x['metrics'] for x in e['frames'] if 'error' not in x['metrics']]
        if ms:
            fl = [m['flip'] for m in ms if m.get('flip') is not None]
            e['worst'] = {'psnr': min(pv(m) for m in ms), 'ssim': min(m['ssim'] for m in ms),
                          'flip': max(fl) if fl else None, 'frac_over_8': max(m['frac_over_8'] for m in ms),
                          'max_abs': max(m['max_abs'] for m in ms)}
        fls = [x['floor'] for x in e['frames'] if x['floor'] and 'error' not in x['floor']]
        if fls:
            e['floor_worst'] = {'psnr': min(pv(m) for m in fls), 'ssim': min(m['ssim'] for m in fls),
                                'max_abs': max(m['max_abs'] for m in fls), 'identical': all(m['identical'] for m in fls)}
        res.append(e)
    cnt = lambda k: sum(1 for r in res if r['status'] == k)
    summ = {'captures': len(res), 'frames': sum(len(r['frames']) for r in res), 'pass': cnt('pass'),
            'fail': cnt('fail'), 'incomplete': cnt('incomplete'), 'unstable': cnt('unstable'),
            'frames_pass': sum(1 for r in res for x in r['frames'] if x['pass'])}
    summ['metric'] = metric(res)
    status = 'fail' if summ['fail'] else ('incomplete' if summ['incomplete'] or summ['unstable'] or not res else 'pass')
    g = {'format': FORMAT, 'gate': 'S16-compare-captures', 'status': status,
         'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
         'thresholds': THRESHOLDS, 'summary': summ, 'captures': res,
         'runs': {k: meta(v) for k, v in (('ref', ref), ('ref2', ref2), ('cand', cand)) if v}}
    return g


def metric(res):
    """S16's convergence metric (the number every commit body quotes): frames over the threshold, mean and max
    per-pixel error (absolute 8-bit channel difference) of the candidate against the reference, and the same two
    numbers for the noise floor (reference against its second run)."""
    fr = [x for r in res for x in r['frames']]
    ok = [x['metrics'] for x in fr if 'error' not in x['metrics']]
    fl = [x['floor'] for x in fr if x['floor'] and 'error' not in x['floor']]
    missing = sum(1 for r in res if r['status'] == 'incomplete')
    return {'frames': len(fr), 'frames_over': sum(1 for x in fr if not x['pass']), 'captures_incomplete': missing,
            'mean_abs': round(sum(m['mean_abs'] for m in ok) / len(ok), 5) if ok else None,
            'max_abs': max((m['max_abs'] for m in ok), default=None),
            'floor_mean_abs': round(sum(m['mean_abs'] for m in fl) / len(fl), 5) if fl else None,
            'floor_max_abs': max((m['max_abs'] for m in fl), default=None)}


def metric_line(m):
    return (f"{m['frames_over']}/{m['frames']} over, mean {m['mean_abs']}, max {m['max_abs']} "
            f"(floor mean {m['floor_mean_abs']}, max {m['floor_max_abs']}; incomplete {m['captures_incomplete']})")


def meta(run):
    p = os.path.join(run, 'run.json')
    if not os.path.isfile(p): return {}
    m = json.load(open(p))
    return {'versions': m.get('versions'), 'seconds': {k: v.get('seconds') for k, v in m.get('captures', {}).items()},
            'rc': {k: v.get('rc') for k, v in m.get('captures', {}).items()}}


def report(g):
    f = lambda v, n: '-' if v is None else ('inf' if v >= 99.99 and n == 2 else f'{v:.{n}f}')
    s = g['summary']
    L = [f"# S16-compare captures: nx_render against nxd9play (Wine)", '',
         f"status **{g['status']}**: {s['pass']} pass, {s['fail']} fail, {s['incomplete']} incomplete, "
         f"{s['unstable']} unstable of {s['captures']} captures; {s['frames_pass']}/{s['frames']} frames pass.", '',
         f"metric: {metric_line(s['metric'])}", '',
         '| capture | status | frames | worst PSNR | worst SSIM | worst FLIP | worst over8 | max abs | floor |',
         '|---|---|---|---|---|---|---|---|---|']
    for r in g['captures']:
        w, fl = r.get('worst') or {}, r.get('floor_worst')
        fs = '-' if not fl else ('0 (identical)' if fl['identical'] else f"{f(fl['psnr'], 2)} dB / max {fl['max_abs']}")
        L.append(f"| {r['capture']} | {r['status']} | {len(r['frames'])} | {f(w.get('psnr'), 2)} | {f(w.get('ssim'), 4)} | "
                 f"{f(w.get('flip'), 4)} | {f(w.get('frac_over_8'), 4)} | {w.get('max_abs', '-')} | {fs} |")
    L += ['', '## Reasons', '']
    L += [f"* {r['capture']}: {x}" for r in g['captures'] for x in r['reasons']] or ['* none']
    L += ['', '## Thresholds', '', '```', json.dumps(g['thresholds'], indent=1), '```']
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', required=True); ap.add_argument('--ref2'); ap.add_argument('--cand', required=True)
    ap.add_argument('--out', required=True); ap.add_argument('--diffs', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    g = compare(a.ref, a.ref2, a.cand, a.out, a.diffs)
    json.dump(g, open(os.path.join(a.out, 'capgate.json'), 'w'), indent=1)
    open(os.path.join(a.out, 'capreport.md'), 'w').write(report(g))
    print(f"{g['status']}: {g['summary']['pass']}/{g['summary']['captures']} captures pass; metric: "
          f"{metric_line(g['summary']['metric'])}")
    return {'pass': 0, 'fail': 1, 'incomplete': 2}[g['status']]


if __name__ == '__main__':
    sys.exit(main())
