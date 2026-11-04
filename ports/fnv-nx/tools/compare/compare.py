#!/usr/bin/env python3
"""compare.py - S16-compare's verdict: per-frame metrics and scene-state diffs of a candidate run against the
reference run, written as a report (Markdown) and a gate file (JSON, format fnvnx-gate/1, docs/COMPARE.md 4).

    compare.py --ref <run dir> --cand <run dir> [--ref2 <second reference run>] [--scenes scenes.json]
               --out <dir> [--candidate-kind nine-replay] [--candidate-commit <sha>] [--diffs]

A run directory holds <scene>/frame_<i>.png, optionally <scene>/state.json (fnvnx-scenestate/1) and
<scene>/run.json, plus run.json / versions.txt at the top. --ref2 is a second, independent reference run: the
reference's own run-to-run spread, which is the baseline of the stochastic scenes and a sanity check of the
deterministic ones (a deterministic scene whose reference does not repeat itself is reported "unstable").
Exit status: 0 gate pass, 1 fail, 2 incomplete (a scene missing on either side), 3 usage.
"""
import argparse, datetime, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metrics, scenediff

GATE_FORMAT = 'fnvnx-gate/1'
THRESHOLDS = {
    # a candidate frame passes when all of these hold (FLIP only when it was computed)
    'deterministic': {'psnr_min': 38.0, 'ssim_min': 0.97, 'flip_max': 0.05},
    # stochastic scenes: no worse than the reference's own spread minus a margin, and never below the floor
    'stochastic': {'psnr_margin': 3.0, 'ssim_margin': 0.03, 'flip_margin': 0.03,
                   'psnr_floor': 18.0, 'ssim_floor': 0.60, 'flip_ceiling': 0.40},
    'state': {'pos_tol': 1.0, 'rot_tol': 0.01, 'actor_pos_tol': 64.0, 'stochastic_actor_pos_tol': 2048.0},
    # the reference must repeat itself on a deterministic scene, or the scene definition is not deterministic
    'ref_repeat': {'psnr_min': 40.0, 'ssim_min': 0.99},
}

def frames(run, scene):
    d = os.path.join(run, scene)
    if not os.path.isdir(d): return []
    fs = [f for f in os.listdir(d) if f.startswith('frame_') and f.endswith('.png')]
    return sorted(fs, key=lambda f: int(f[6:-4]))

def best_pair(run_a, run_b, scene):
    """metrics of frame i of a against frame i of b, for every i both have"""
    out = []
    fa, fb = frames(run_a, scene), set(frames(run_b, scene))
    for f in fa:
        if f not in fb: continue
        a, b = metrics.load(os.path.join(run_a, scene, f)), metrics.load(os.path.join(run_b, scene, f))
        try: m = metrics.compare(a, b)
        except ValueError as e: m = {'error': str(e)}
        m['frame'] = int(f[6:-4]); out.append(m)
    return out

def psnr_v(m):  # identical frames have psnr None (infinite): treat as 100 dB for thresholds
    return 100.0 if m.get('identical') else m.get('psnr')

def judge_frame(m, cls, base):
    """(pass, reasons) of one candidate frame; base: the reference's own worst frame metrics (stochastic)"""
    if 'error' in m: return False, [m['error']]
    r = []
    if cls == 'deterministic':
        t = THRESHOLDS['deterministic']
        if psnr_v(m) < t['psnr_min']: r.append(f"psnr {psnr_v(m):.2f} < {t['psnr_min']}")
        if m['ssim'] < t['ssim_min']: r.append(f"ssim {m['ssim']:.4f} < {t['ssim_min']}")
        if m.get('flip') is not None and m['flip'] > t['flip_max']: r.append(f"flip {m['flip']:.4f} > {t['flip_max']}")
    else:
        t = THRESHOLDS['stochastic']
        pmin = max(t['psnr_floor'], (base['psnr'] - t['psnr_margin']) if base else t['psnr_floor'])
        smin = max(t['ssim_floor'], (base['ssim'] - t['ssim_margin']) if base else t['ssim_floor'])
        if psnr_v(m) < pmin: r.append(f"psnr {psnr_v(m):.2f} < {pmin:.2f}")
        if m['ssim'] < smin: r.append(f"ssim {m['ssim']:.4f} < {smin:.4f}")
        if m.get('flip') is not None:
            fmax = min(t['flip_ceiling'], (base['flip'] + t['flip_margin']) if base and base.get('flip') is not None
                       else t['flip_ceiling'])
            if m['flip'] > fmax: r.append(f"flip {m['flip']:.4f} > {fmax:.4f}")
    return not r, r

def worst(ms):
    ms = [m for m in ms if 'error' not in m]
    if not ms: return None
    fl = [m['flip'] for m in ms if m.get('flip') is not None]
    return {'psnr': min(psnr_v(m) for m in ms), 'ssim': min(m['ssim'] for m in ms), 'flip': max(fl) if fl else None}

def state_of(run, scene):
    p = os.path.join(run, scene, 'state.json')
    return scenediff.load(p) if os.path.isfile(p) else None

def run_scene(s, ref, cand, ref2, out, diffs):
    sid, cls = s['id'], s.get('class', 'deterministic')
    res = {'id': sid, 'title': s.get('title', sid), 'class': cls, 'reasons': []}
    if not frames(ref, sid): res.update(status='incomplete', reasons=['no reference frames']); return res
    if not frames(cand, sid): res.update(status='incomplete', reasons=['no candidate frames']); return res
    base = None
    if ref2 and frames(ref2, sid):
        rr = best_pair(ref, ref2, sid)
        base = worst(rr)
        res['ref_repeat'] = {'frames': rr, 'worst': base}
        t = THRESHOLDS['ref_repeat']
        if cls == 'deterministic' and base and (base['psnr'] < t['psnr_min'] or base['ssim'] < t['ssim_min']):
            res['reasons'].append('reference does not repeat itself (scene not deterministic)')
            res['unstable'] = True
    fm = best_pair(ref, cand, sid)
    for m in fm:
        m['pass'], m['reasons'] = judge_frame(m, cls, base)
        if diffs and 'error' not in m:
            os.makedirs(os.path.join(out, 'diffs'), exist_ok=True)
            f = f"frame_{m['frame']}.png"
            metrics.diff_image(metrics.load(os.path.join(ref, sid, f)), metrics.load(os.path.join(cand, sid, f)),
                               os.path.join(out, 'diffs', f'{sid}_{m["frame"]}.png'))
    res['frames'], res['worst'] = fm, worst(fm)
    if not fm: res['reasons'].append('no frame pairs')
    for m in fm:
        if not m['pass']: res['reasons'].append(f"frame {m['frame']}: " + ', '.join(m['reasons']))
    sa, sb = state_of(ref, sid), state_of(cand, sid)
    if sa and sb:
        t = THRESHOLDS['state']
        d = scenediff.diff(sa, sb, pos_tol=t['pos_tol'], rot_tol=t['rot_tol'],
                           actor_tol=t['stochastic_actor_pos_tol'] if cls == 'stochastic' else t['actor_pos_tol'])
        res['state'] = {'ok': d['ok'], 'problems': d['problems'], 'camera': d.get('camera'),
                        'refs': None if d['refs'] is None else
                        {k: (v if k == 'matched' else len(v)) for k, v in d['refs'].items()}}
        if not d['ok']: res['reasons'].append('state: ' + '; '.join(d['problems']))
    else:
        res['state'] = None
    res['status'] = 'pass' if fm and not any(not m['pass'] for m in fm) and (not res['state'] or res['state']['ok']) \
        else 'fail'
    if res.get('unstable') and res['status'] == 'pass': res['status'] = 'unstable'
    return res

def read_meta(run):
    m = {}
    for f in ('run.json', 'versions.txt'):
        p = os.path.join(run, f)
        if not os.path.isfile(p): continue
        if f.endswith('.json'): m.update(json.load(open(p)))
        else: m['versions'] = dict(l.split(': ', 1) for l in open(p).read().splitlines() if ': ' in l)
    return m

def report_md(g):
    L = [f"# S16-compare: {g['candidate']['kind']} against {g['reference']['kind']}", '',
         f"status **{g['status']}**: {g['summary']['pass']} pass, {g['summary']['fail']} fail, "
         f"{g['summary']['incomplete']} incomplete, {g['summary']['unstable']} unstable, of {g['summary']['scenes']} "
         f"scenes. Generated {g['generated']}.", '',
         '| scene | class | status | frames | worst PSNR | worst SSIM | worst FLIP | ref repeat PSNR/SSIM | state |',
         '|---|---|---|---|---|---|---|---|---|']
    f2 = lambda v, n: '-' if v is None else (f'{v:.{n}f}' if v < 99 else 'inf')
    for s in g['scenes']:
        w, rr = s.get('worst') or {}, (s.get('ref_repeat') or {}).get('worst') or {}
        st = s.get('state')
        sts = '-' if not st else ('ok' if st['ok'] else '; '.join(st['problems']))
        L.append(f"| {s['id']} | {s['class']} | {s['status']} | {len(s.get('frames') or [])} | {f2(w.get('psnr'), 2)} | "
                 f"{f2(w.get('ssim'), 4)} | {f2(w.get('flip'), 4)} | {f2(rr.get('psnr'), 2)} / {f2(rr.get('ssim'), 4)} | "
                 f"{sts} |")
    L += ['', '## Reasons', '']
    for s in g['scenes']:
        for r in s['reasons']: L.append(f"* {s['id']}: {r}")
    L += ['', '## Thresholds', '', '```', json.dumps(g['thresholds'], indent=1), '```', '',
          '## Runs', '', f"* reference: `{json.dumps(g['reference'])}`", f"* candidate: `{json.dumps(g['candidate'])}`"]
    return '\n'.join(L) + '\n'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', required=True); ap.add_argument('--cand', required=True); ap.add_argument('--ref2')
    ap.add_argument('--scenes', default=os.path.join(HERE, 'scenes.json')); ap.add_argument('--out', required=True)
    ap.add_argument('--reference-kind', default='wine-wined3d-llvmpipe')
    ap.add_argument('--candidate-kind', default='unknown'); ap.add_argument('--candidate-commit')
    ap.add_argument('--only', help='comma-separated scene ids'); ap.add_argument('--diffs', action='store_true')
    a = ap.parse_args()
    sc = json.load(open(a.scenes))
    scenes = [s for s in sc['scenes'] if not a.only or s['id'] in a.only.split(',')]
    os.makedirs(a.out, exist_ok=True)
    res = [run_scene(s, a.ref, a.cand, a.ref2, a.out, a.diffs) for s in scenes]
    cnt = lambda k: sum(1 for r in res if r['status'] == k)
    summ = {'scenes': len(res), 'pass': cnt('pass'), 'fail': cnt('fail'), 'incomplete': cnt('incomplete'),
            'unstable': cnt('unstable')}
    status = 'fail' if summ['fail'] else ('incomplete' if summ['incomplete'] or summ['unstable'] else 'pass')
    g = {'format': GATE_FORMAT, 'gate': 'S16-compare', 'status': status,
         'generated': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
         'reference': {'kind': a.reference_kind, 'dir': os.path.basename(os.path.normpath(a.ref)), **read_meta(a.ref)},
         'candidate': {'kind': a.candidate_kind, 'commit': a.candidate_commit,
                       'dir': os.path.basename(os.path.normpath(a.cand)), **read_meta(a.cand)},
         'reference_repeat': os.path.basename(os.path.normpath(a.ref2)) if a.ref2 else None,
         'scene_set': sc.get('format'), 'thresholds': THRESHOLDS, 'summary': summ, 'scenes': res}
    json.dump(g, open(os.path.join(a.out, 'gate.json'), 'w'), indent=1)
    open(os.path.join(a.out, 'report.md'), 'w').write(report_md(g))
    print(f"{status}: {summ}")
    return {'pass': 0, 'fail': 1, 'incomplete': 2}[status]

if __name__ == '__main__':
    sys.exit(main())
