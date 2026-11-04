#!/usr/bin/env python3
# Native d9 families against the translated programs on real captures (stream render-lit, RENDER_NATIVE.md §4.5).
#
#   python3 -I render/verify/shaders/d9_parity.py --bin <nx_render> --shaders <sdp2dksh --glsl dir> \
#       --index <that dir>/index.tsv [--frames 0,1,2] [--work DIR] [--json OUT] capture.nxd9 ...
#
# For each capture and frame, nx_render renders twice: --families none (every program translated: the reference)
# and --families all (the hand-written d9 families where they recognise a program). Both write the float colour
# target after every draw (--per-draw --raw). For every draw a family drew, the two targets are compared:
#   max_abs  largest |native - translated| over all channels and pixels
#   bad_px   pixels with a channel off by more than 2/255 and by more than 1e-3 relative (RENDER_NATIVE.md §4)
# --force-visible renders both runs with depth, stencil, blending and culling off, so every draw writes its colour.
#   written  pixels the draw changed in the reference (0 for every draw of a family = the check says nothing)
# The errors are cumulative within a frame (a target holds every earlier draw), so they bound the family's error.
# METRIC: lit draws on the native path / all lit draws. A draw is lit when its pixel program is one of the lighting
# classes by its package name (index.tsv from sdp2dksh: SLS*, SM3*, SKIN*, HAIR*); names only come from the local
# index, never from git. Captures, packages, images and the JSON stay local (they derive from the player's game).
import argparse, array, json, os, shutil, struct, subprocess, sys

LIT = ('SLS', 'SM3', 'SKIN', 'HAIR')


def read_f32(path):
    d = open(path, 'rb').read()
    w, h = struct.unpack_from('<II', d)
    a = array.array('f')
    a.frombytes(d[8:8 + 16 * w * h])
    return w, h, a


def compare(pa, pb):
    wa, ha, a = read_f32(pa)
    wb, hb, b = read_f32(pb)
    if (wa, ha) != (wb, hb):
        return {'max_abs': float('inf'), 'bad_px': wa * ha, 'px': wa * ha}
    m, bad = 0.0, 0
    for i in range(0, len(a), 4):
        worst = False
        for c in range(4):
            x, y = a[i + c], b[i + c]
            d = abs(x - y)
            if d != d:                               # NaN on one side only
                d = 0.0 if (x != x) and (y != y) else float('inf')
            if d > m:
                m = d
            if d > 2.0 / 255 and d > 1e-3 * abs(y):
                worst = True
        bad += worst
    return {'max_abs': m, 'bad_px': bad, 'px': wa * ha}


def changed(pa, pb):
    _, _, a = read_f32(pa)
    _, _, b = read_f32(pb)
    if len(a) != len(b):
        return len(a) // 4
    return sum(1 for i in range(0, len(a), 4) if a[i:i + 4] != b[i:i + 4])


def run(binary, cap, shaders, out, frame, families, visible=False):
    shutil.rmtree(out, ignore_errors=True)
    log = out + '.log'
    r = subprocess.run([binary, 'd3d9', cap, '-o', out, '--shaders', shaders, '--per-draw', '--raw', '--frame', str(frame),
                        '--families', families, '--draw-log', log] + (['--force-visible'] if visible else []), capture_output=True, text=True)
    if r.returncode not in (0, 3):
        raise SystemExit('nx_render failed (%d): %s' % (r.returncode, r.stderr.strip()))
    rows = []
    for line in open(log):
        f, d, bd, vs, ps, fam, name = line.rstrip('\n').split('\t')
        rows.append({'frame': int(f), 'draw': int(d), 'backend_draw': int(bd), 'vs': vs, 'ps': ps, 'family': fam, 'shader': name})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('captures', nargs='+')
    ap.add_argument('--bin', required=True)
    ap.add_argument('--shaders', required=True)
    ap.add_argument('--index', required=True)
    ap.add_argument('--frames', default='0,1,2')
    ap.add_argument('--work', default='build/d9_parity')
    ap.add_argument('--json')
    ap.add_argument('--force-visible', action='store_true', help='depth/stencil/blend/cull off in both runs (exercises draws the depth test rejects)')
    a = ap.parse_args()
    names = {}
    for line in open(a.index):
        p = line.rstrip('\n').split('\t')
        if len(p) > 2:
            names.setdefault(p[0], p[2])
    os.makedirs(a.work, exist_ok=True)
    total_lit = native_lit = 0
    fam = {}
    per_capture = {}
    for cap in a.captures:
        cn = os.path.basename(cap).split('.')[0]
        cl = cn_native = 0
        for frame in [int(x) for x in a.frames.split(',')]:
            ref = run(a.bin, cap, a.shaders, os.path.join(a.work, cn + '_ref'), frame, 'none', a.force_visible)
            nat = run(a.bin, cap, a.shaders, os.path.join(a.work, cn + '_nat'), frame, 'all', a.force_visible)
            for r in [x for x in nat if x['frame'] == frame]:
                lit = names.get(r['ps'], '').startswith(LIT)
                cl += lit
                if r['family'] == 'translated':
                    continue
                cn_native += lit
                n = 'f%05d_d%05d.f32' % (frame, r['backend_draw'])
                e = compare(os.path.join(a.work, cn + '_nat', n), os.path.join(a.work, cn + '_ref', n))
                prev = os.path.join(a.work, cn + '_ref', 'f%05d_d%05d.f32' % (frame, r['backend_draw'] - 1))
                w = changed(os.path.join(a.work, cn + '_ref', n), prev) if r['backend_draw'] > 0 else -1
                s = fam.setdefault(r['family'], {'draws': 0, 'max_abs': 0.0, 'bad_px': 0, 'written_px': 0, 'shaders': {}})
                s['written_px'] += max(w, 0)
                s['draws'] += 1
                s['max_abs'] = max(s['max_abs'], e['max_abs'])
                s['bad_px'] = max(s['bad_px'], e['bad_px'])
                s['shaders'][r['shader']] = s['shaders'].get(r['shader'], 0) + 1
            shutil.rmtree(os.path.join(a.work, cn + '_ref'), ignore_errors=True)
            shutil.rmtree(os.path.join(a.work, cn + '_nat'), ignore_errors=True)
        per_capture[cn] = {'lit_draws': cl, 'native_lit_draws': cn_native}
        total_lit += cl
        native_lit += cn_native
    res = {'schema': 'd9-parity/1', 'force_visible': a.force_visible, 'lit_draws': total_lit, 'native_lit_draws': native_lit,
           'native_share': native_lit / total_lit if total_lit else None, 'captures': per_capture, 'families': fam}
    for k, v in per_capture.items():
        print('%-20s lit %3d native %3d' % (k, v['lit_draws'], v['native_lit_draws']))
    for k, v in sorted(fam.items()):
        print('family %-10s draws %3d written_px %8d max_abs %.3g bad_px %d  %s' % (k, v['draws'], v['written_px'], v['max_abs'], v['bad_px'],
              ' '.join('%s:%d' % kv for kv in sorted(v['shaders'].items()))))
    print('metric: native lit draws %d / %d' % (native_lit, total_lit))
    if a.json:
        json.dump(res, open(a.json, 'w'), indent=1)
    worst = max([v['bad_px'] for v in fam.values()] or [0])
    return 1 if worst else 0


if __name__ == '__main__':
    sys.exit(main())
