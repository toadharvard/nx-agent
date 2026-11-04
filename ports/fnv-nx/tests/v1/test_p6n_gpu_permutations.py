#!/usr/bin/env python3
"""test_p6n_gpu_permutations.py - V1 (D14) against render 5f8952b (merged in 2f5880c7): "the GPU model binds the
renderer's static light and epilogue permutations" (handheld 720p, mix_hi, efficiency 0.6: 23.3 -> 10.25 ms).

    python3 -I tests/v1/test_p6n_gpu_permutations.py     -> one line per check, exit 1 on a defect

Read from render/native/perf/report.json (the model's own shader table and pass rows), gpu_cost.py and
n1/renderer.cpp; no uam needed.
  G1  the light loop is compiled out of the static permutations: light_loops 0, tgsi_dyn == tgsi_static, and the
      static count grows with the point-light count (1 < 2 < 4 < 8), so uam unrolled it
  G2  the permutation the model costs is the one Renderer::Render binds for its light assumption: the renderer pads
      points to 1/2/4/8 (kPad) and binds lit[_spec]_d<dirs>p<pad>; LIGHTS = 3 (sun + 2 points) -> d1p2 =
      gpu_cost.LIT_PERMUTATION; the epilogue choice (__e0 / __ea / __ef / runtime for both) is the renderer's
  G3  (sensitivity, reported, not a defect) the frame with 4 and 8 point lights per lit draw instead of 2, and with
      every tree and grass draw alpha-tested (FNV's leaves and grass are), each from the model's own formula
      (ALU-bound passes scale with the bound shaders' alu_slots)
10.25 ms rests on two unmeasured inputs: the light count per draw and gen_stream's alpha-test / fog mix. No real
capture exists on the private repo (no gamedata/captures branch), so G3 gives the range and the number stays
model-only.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.join(HERE, '..', '..', 'render', 'native')
bad = 0


def case(cid, ok, text, note=False):
    global bad
    print('%s: %s - %s' % (cid, 'ok' if ok else ('note' if note else 'DEFECT'), text))
    bad |= not ok and not note


def main():
    rep = os.path.join(NATIVE, 'perf', 'report.json')
    if not os.path.isfile(rep):
        print('skip: no report.json')
        return 0
    r = json.load(open(rep))
    g = r['gpu']
    t = g['shaders']
    if not g['assumptions'].get('permutations'):
        print('skip: report.json was built without permutations')
        return 0
    # G1
    ok, rows = True, []
    for b in ('lit', 'lit_spec'):
        for d in (0, 1):
            seq = []
            for p in (1, 2, 4, 8):
                s = t.get('%s_d%dp%d__e0.frag' % (b, d, p))
                if s is None:
                    ok = False
                    rows.append('%s_d%dp%d missing' % (b, d, p))
                    continue
                ok &= s['light_loops'] == 0 and s['tgsi_dyn'] == s['tgsi_static']
                seq.append(s['tgsi_static'])
            ok &= seq == sorted(seq) and len(set(seq)) == len(seq)
            rows.append('%s d%d: %s' % (b, d, seq))
    case('G1', ok, 'static permutations unrolled (tgsi_static per 1/2/4/8 points): ' + '; '.join(rows))
    # G2
    src = open(os.path.join(NATIVE, 'n1', 'renderer.cpp')).read()
    gc = open(os.path.join(NATIVE, 'perf', 'gpu_cost.py')).read()
    pad = re.search(r'kPad\[5\]\s*=\s*\{([^}]*)\}', src)
    pads = [int(x) for x in pad.group(1).split(',')] if pad else None
    lights = int(re.search(r'^LIGHTS\s*=\s*(\d+)', gc, re.M).group(1))
    pts = lights - 1
    want = 'd1p%d' % next(p for p in (pads or [])[1:] if p >= pts) if pads else None
    lp = g['assumptions'].get('lit_permutation') or {}
    epi = all(('"%s__%s"' % ('%s', e)) in src for e in ('e0', 'ea', 'ef'))
    sel = re.search(r'!at && !fog \? es\.e0 : at && !fog \? es\.ea : !at && fog \? es\.ef : 0', src) is not None
    ok = (pads == [0, 1, 2, 4, 8] and want == 'd1p2' and lp.get('lit_spec.frag') == 'lit_spec_d1p2.frag'
          and lp.get('lit.frag') == 'lit_d1p2.frag' and epi and sel)
    case('G2', ok, 'renderer pads points to %s; LIGHTS = %d -> %s; model binds %s; epilogue selection e0/ea/ef/runtime '
         'in Render: %s' % (pads, lights, want, sorted(lp.values()), sel))
    # G3
    mix = g['assumptions']['epilogue_mix_hi']
    passes = {p['name']: p for p in r['hotspots']['gpu_by_pass/mix_hi/handheld_720p']}
    frame = r['hotspots']['gpu_frame_ms/mix_hi/handheld_720p']

    def bound(base, m):
        stem, tot, acc = base[:-5], sum(m.values()), 0.0
        for k, n in m.items():
            at, fog = int(k[2]), int(k[7])
            tag = '__e0' if not at and not fog else '__ea' if at and not fog else '__ef' if fog and not at else ''
            name = stem + tag + '.frag'
            acc += t[name if name in t else base]['alu_slots'] * n / tot
        return acc

    def scaled(fam, b0, b1, m0, m1):
        return passes[fam]['frag_ms'] * (bound(b1, m1) / bound(b0, m0) - 1)

    fams = g['assumptions']['families']
    rows = ['as reported %.2f ms' % frame]
    for p in (4, 8):
        d = (scaled('lit', fams['lit']['ps'].replace('.frag', '_d1p2.frag'), fams['lit']['ps'].replace('.frag', '_d1p%d.frag' % p),
                    mix['lit'], mix['lit'])
             + scaled('trees', fams['trees']['ps'].replace('.frag', '_d1p2.frag'),
                      fams['trees']['ps'].replace('.frag', '_d1p%d.frag' % p), mix['trees'], mix['trees']))
        rows.append('1+%d lights %.2f ms' % (p, frame + d))
    at = {f: {'at1_fog0': sum(mix[f].values())} for f in ('trees', 'grass')}
    d = (scaled('trees', fams['trees']['ps'].replace('.frag', '_d1p2.frag'), fams['trees']['ps'].replace('.frag', '_d1p2.frag'),
                mix['trees'], at['trees'])
         + scaled('grass', fams['grass']['ps'], fams['grass']['ps'], mix['grass'], at['grass']))
    share = {f: sum(v for k, v in mix[f].items() if k.startswith('at1')) / sum(mix[f].values()) for f in ('trees', 'grass')}
    rows.append('all trees/grass alpha-tested (gen_stream: %.0f %% / %.0f %%) %.2f ms (overdraw and early-z unchanged in '
                'the model)' % (100 * share['trees'], 100 * share['grass'], frame + d))
    case('G3', False, 'handheld 720p mix_hi eff 0.6 sensitivity: ' + '; '.join(rows), note=True)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
