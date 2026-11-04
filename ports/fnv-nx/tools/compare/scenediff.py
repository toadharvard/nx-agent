#!/usr/bin/env python3
"""scenediff.py - diff two scene-state files (format fnvnx-scenestate/1, docs/COMPARE.md section 3).

    scenediff.py <reference state.json> <candidate state.json> [--pos-tol 1.0] [--rot-tol 0.01]
                 [--actor-pos-tol 64] [--ignore-disabled] [--json out.json]

References are matched by form id. Runtime-created references (mod index 0xFF) get new ids every run, so they are
matched by base form and nearest position instead (greedy, within --actor-pos-tol). Actors (ACHR, ACRE) use
--actor-pos-tol for "moved", everything else --pos-tol. Reported: missing (in the reference, not the candidate),
extra, moved (position delta over the tolerance), rotated, base (same ref id, other base form), disabled flag,
and the scene-level fields: cell, camera, time, weather, shader set. Exit 0 when the states agree, 1 otherwise.
"""
import argparse, json, math, sys

ACTORS = {'ACHR', 'ACRE'}
FORMAT = 'fnvnx-scenestate/1'

def load(p):
    d = json.load(open(p))
    if d.get('format') != FORMAT: raise ValueError(f'{p}: format {d.get("format")!r}, want {FORMAT}')
    return d

def dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

def angdiff(a, b):
    """largest per-axis difference of two rotations in radians, modulo 2 pi"""
    return max(abs((x - y + math.pi) % (2 * math.pi) - math.pi) for x, y in zip(a, b))

def dynamic(r):
    return (r['ref'] >> 24) == 0xFF

def diff_refs(ra, rb, pos_tol=1.0, rot_tol=0.01, actor_tol=64.0, ignore_disabled=False):
    if ignore_disabled:
        ra = [r for r in ra if not r.get('disabled')]; rb = [r for r in rb if not r.get('disabled')]
    A = {r['ref']: r for r in ra if not dynamic(r)}
    B = {r['ref']: r for r in rb if not dynamic(r)}
    out = {'missing': [], 'extra': [], 'moved': [], 'rotated': [], 'base': [], 'disabled': [], 'matched': 0}
    pairs = []
    for k in sorted(set(A) | set(B)):
        if k not in B: out['missing'].append(A[k]); continue
        if k not in A: out['extra'].append(B[k]); continue
        pairs.append((A[k], B[k]))
    # runtime references: same base form, nearest position first
    da = [r for r in ra if dynamic(r)]; db = [r for r in rb if dynamic(r)]
    cand = sorted(((dist(a['pos'], b['pos']), i, j) for i, a in enumerate(da) for j, b in enumerate(db)
                   if a['base'] == b['base']), key=lambda t: t[0])
    ua, ub = set(), set()
    for d, i, j in cand:
        if i in ua or j in ub or d > actor_tol: continue
        ua.add(i); ub.add(j); pairs.append((da[i], db[j]))
    out['missing'] += [r for i, r in enumerate(da) if i not in ua]
    out['extra'] += [r for j, r in enumerate(db) if j not in ub]
    for a, b in pairs:
        out['matched'] += 1
        if a['base'] != b['base']: out['base'].append({'ref': a['ref'], 'ref_base': a['base'], 'cand_base': b['base']})
        tol = actor_tol if a.get('kind') in ACTORS else pos_tol
        d = dist(a['pos'], b['pos'])
        if d > tol: out['moved'].append({'ref': a['ref'], 'base': a['base'], 'kind': a.get('kind'), 'delta': round(d, 3),
                                         'ref_pos': a['pos'], 'cand_pos': b['pos']})
        if 'rot' in a and 'rot' in b and a.get('kind') not in ACTORS and angdiff(a['rot'], b['rot']) > rot_tol:
            out['rotated'].append({'ref': a['ref'], 'delta': round(angdiff(a['rot'], b['rot']), 5)})
        if bool(a.get('disabled')) != bool(b.get('disabled')):
            out['disabled'].append({'ref': a['ref'], 'ref_disabled': bool(a.get('disabled')),
                                    'cand_disabled': bool(b.get('disabled'))})
    return out

def diff(a, b, **kw):
    r = {'scene': a.get('scene') or b.get('scene'), 'problems': []}
    ca, cb = a.get('cell') or {}, b.get('cell') or {}
    if ca and cb and ca.get('form') != cb.get('form'):
        r['problems'].append(f"cell: {ca.get('edid') or ca.get('form')} vs {cb.get('edid') or cb.get('form')}")
    if a.get('refs') is not None and b.get('refs') is not None:
        d = diff_refs(a['refs'], b['refs'], kw.get('pos_tol', 1.0), kw.get('rot_tol', 0.01),
                      kw.get('actor_tol', 64.0), kw.get('ignore_disabled', False))
        r['refs'] = d
        for k in ('missing', 'extra', 'moved', 'rotated', 'base', 'disabled'):
            if d[k]: r['problems'].append(f'refs {k}: {len(d[k])}')
    else:
        r['refs'] = None
    cama, camb = a.get('camera'), b.get('camera')
    if cama and camb:
        dp = dist(cama['pos'], camb['pos'])
        ra = [math.radians(x or 0) for x in cama.get('rot_deg', [0, 0, 0])]
        rb = [math.radians(x or 0) for x in camb.get('rot_deg', [0, 0, 0])]
        r['camera'] = {'pos_delta': round(dp, 3), 'rot_delta_deg': round(math.degrees(angdiff(ra, rb)), 4),
                       'fov_delta': round(abs((cama.get('fov') or 0) - (camb.get('fov') or 0)), 4)}
        if dp > kw.get('cam_pos_tol', 1.0): r['problems'].append(f'camera moved {dp:.2f}')
        if r['camera']['rot_delta_deg'] > kw.get('cam_rot_tol_deg', 0.1): r['problems'].append('camera rotated')
        if r['camera']['fov_delta'] > 0.01: r['problems'].append('camera fov')
    ta, tb = a.get('time'), b.get('time')
    if ta and tb and ta.get('gamehour') is not None and tb.get('gamehour') is not None:
        dh = abs(ta['gamehour'] - tb['gamehour'])
        r['time_delta_h'] = round(dh, 4)
        if dh > kw.get('hour_tol', 0.1): r['problems'].append(f'gamehour differs by {dh:.3f}')
    wa, wb = a.get('weather'), b.get('weather')
    if wa and wb and wa.get('form') != wb.get('form'): r['problems'].append('weather')
    sa, sb = a.get('shaders'), b.get('shaders')
    if sa is not None and sb is not None:
        ka = {(s['kind'], s['hash']) for s in sa}; kb = {(s['kind'], s['hash']) for s in sb}
        r['shaders'] = {'missing': len(ka - kb), 'extra': len(kb - ka), 'common': len(ka & kb)}
        if ka != kb: r['problems'].append(f"shaders: {len(ka - kb)} missing, {len(kb - ka)} extra")
    r['ok'] = not r['problems']
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ref'); ap.add_argument('cand')
    ap.add_argument('--pos-tol', type=float, default=1.0); ap.add_argument('--rot-tol', type=float, default=0.01)
    ap.add_argument('--actor-pos-tol', type=float, default=64.0); ap.add_argument('--ignore-disabled', action='store_true')
    ap.add_argument('--json')
    a = ap.parse_args()
    r = diff(load(a.ref), load(a.cand), pos_tol=a.pos_tol, rot_tol=a.rot_tol, actor_tol=a.actor_pos_tol,
             ignore_disabled=a.ignore_disabled)
    if a.json: json.dump(r, open(a.json, 'w'), indent=1)
    d = r.get('refs')
    if d: print(f"refs matched {d['matched']}, " + ', '.join(f'{k} {len(d[k])}' for k in
                                                         ('missing', 'extra', 'moved', 'rotated', 'base', 'disabled')))
    print('ok' if r['ok'] else 'DIFFER: ' + '; '.join(r['problems']))
    return 0 if r['ok'] else 1

if __name__ == '__main__':
    sys.exit(main())
