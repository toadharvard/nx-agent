#!/usr/bin/env python3
"""content_presets.py - the content lever (stream S15-simd, docs/PERF.md 11.7): Switch INI presets built only from
the game's existing settings, how each one scales the frame model's items, and the worst frame per preset.

    content_presets.py [--model tools/perf/budget_model.json] [--render report.json] [--perf perf.json]
                       [--scenarios placed,plan45b] [--json out.json]

The presets and the scaling rules are budget_model.json's `content` block. Every setting a preset names is a static
setting object of FalloutNV.exe 1.4.0.525 (tools/perf/setting_names.py lists them; the exe's own defaults, read from
its initializers, are the `base` preset). The rules are estimates [E] until frameprof profiles frames with each
preset (they need a scene); each is stated with the reason for its shape:

* visible references: within the fade distance of their class, which is the class multiplier x a per-reference base
  distance; on the ground plane their number grows with the square of that distance, and never past the loaded area
  (uGridsToLoad x uGridsToLoad cells). Per class c: v_c = min((m_c' / m_c)^2, loaded') with loaded' = (N'/N)^2;
* draws: RENDER_NATIVE.md 3.7's sources (statics, land, distant LOD, trees, grass, actors, shadow maps, water
  reflection, particles, image space + UI) at their mid counts, each scaled by the setting that governs it (statics
  and items by their fade, land by the loaded area, grass, actors, shadows) or constant; V, the objects the cull
  tests, is the same over the scene sources;
* grass: instances per area fall with the square of the grass spacing (iMinGrassSize), and the grass area with the
  square of fGrassMaxStartFadeDistance; bShadowsOnGrass adds grass to the shadow passes;
* shadows: each shadowed actor (iActorShadowCountExt) adds a shadow pass over its geometry: a share of the
  accumulation and of the renderer proportional to the count;
* items: cull = traversal (proportional to the loaded cells) + per-object tests (proportional to V); accumulation and
  the renderer = draws (V) + shadow passes; scene-graph update = loaded nodes (cells) + moving visible objects (V);
  animation, AI, Havok: not driven by these settings (actors in high process are set by distance to the player,
  not by fade), unchanged.

Writes nothing into the model; prints a table (preset x scenario: worst frame mid / lo-hi at 1.02 GHz, main thread
M instructions) and the per-item scale of each preset. Uses frame_budget.build with a lever per preset whose `scales`
carry the factors, combined with each scenario's own levers.
"""
import argparse
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import tables  # noqa: E402  (the decomp-derived budget model is a private table: tools/tables.py)
sys.path.insert(0, HERE)
import frame_budget  # noqa: E402


def factors(content, preset):
    base, p = content['presets']['base'], dict(content['presets']['base'], **content['presets'][preset])
    sh = content['shares']
    loaded = (p['uGridsToLoad'] / base['uGridsToLoad']) ** 2
    v = {
        'objects': min((p['fLODFadeOutMultObjects'] / base['fLODFadeOutMultObjects']) ** 2, loaded),
        'items': min((p['fLODFadeOutMultItems'] / base['fLODFadeOutMultItems']) ** 2, loaded),
        'actors': min((p['fLODFadeOutMultActors'] / base['fLODFadeOutMultActors']) ** 2, loaded),
        'grass': min((base['iMinGrassSize'] / p['iMinGrassSize']) ** 2 *
                     (p['fGrassMaxStartFadeDistance'] / base['fGrassMaxStartFadeDistance']) ** 2, loaded),
    }
    shadow = (p['iActorShadowCountExt'] + sh['grass_shadow_actor_equiv'] * p['bShadowsOnGrass']) / max(
        base['iActorShadowCountExt'] + sh['grass_shadow_actor_equiv'] * base['bShadowsOnGrass'], 1e-9)
    k = dict(v, loaded=loaded, shadow=shadow, const=1.0)
    dr = sh['draws']
    draws = sum(n * k[sh['scales_with'][s]] for s, n in dr.items()) / sum(dr.values())
    cs = sh['cull_sources']
    V = sum(dr[s] * k[sh['scales_with'][s]] for s in cs) / sum(dr[s] for s in cs)
    f = {
        'cull': sh['cull_traversal'] * loaded + (1 - sh['cull_traversal']) * V,
        'render_accumulation': draws,
        'renderer_main': draws,
        'renderer_worker': draws,
        'scene_update': sh['scene_loaded'] * loaded + (1 - sh['scene_loaded']) * V,
    }
    return f, dict(v, loaded=loaded, V=V, shadow=shadow, draws=draws)


def run(model, render, perf, scenarios, content):
    out = []
    for preset in content['presets']:
        f, parts = factors(content, preset)
        m = copy.deepcopy(model)
        m['levers'].append({'id': 'content_' + preset, 'name': 'content preset ' + preset, 'basis': 'E',
                            'scales': {k: [v, v] for k, v in f.items()}})
        for sc in m['scenarios']:
            sc['levers'] = list(sc['levers']) + ['content_' + preset]
        b = frame_budget.build(m, None, render, perf)
        res = {s['id']: s for s in b['scenarios']}
        row = {'preset': preset, 'factors': {k: round(v, 3) for k, v in f.items()},
               'parts': {k: round(v, 3) for k, v in parts.items()}, 'scenarios': {}}
        for sid in scenarios:
            r = res[sid]['results']['1020']
            row['scenarios'][sid] = {'worst_frame_ms': r['worst_frame_ms'],
                                     'main_minsn_mid': round(res[sid]['cores']['0'].get('minsn', {}).get('mid', 0)
                                                             if isinstance(res[sid]['cores']['0'].get('minsn'), dict)
                                                             else 0, 2)}
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', default=None)
    ap.add_argument('--render')
    ap.add_argument('--perf')
    ap.add_argument('--scenarios', default='placed,plan45b')
    ap.add_argument('--json')
    a = ap.parse_args()
    a.model = a.model or tables.path('budget_model.json')
    model = json.load(open(a.model))
    render = json.load(open(a.render)) if a.render else None
    perf = json.load(open(a.perf)) if a.perf else None
    scs = a.scenarios.split(',')
    rows = run(model, render, perf, scs, model['content'])
    print('%-10s %s   %s' % ('preset', '   '.join('%-24s' % ('worst ms ' + s) for s in scs),
                             'scale: cull / accumulation+renderer / scene update'))
    for r in rows:
        cells = []
        for s in scs:
            w = r['scenarios'][s]['worst_frame_ms']
            cells.append('%-24s' % ('%.1f (%.1f-%.1f)' % (w['mid'], w['lo'], w['hi'])))
        f = r['factors']
        print('%-10s %s   %.2f / %.2f / %.2f' % (r['preset'], '   '.join(cells), f['cull'],
                                                   f['render_accumulation'], f['scene_update']))
    if a.json:
        json.dump({'schema': 'fnvnx-content-presets/1', 'rows': rows}, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
