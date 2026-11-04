#!/usr/bin/env python3
"""drawgate.py - S16's render coverage gate (owner rule D21: coverage first, then optimisation). One metric per
render head: skipped draws, and passing draws out of all draws across every capture, broken down by family and by
the class of each frame's first divergence.

    drawgate.py --set <name>=<captures dir>:<ref per-draw run>:<cand per-draw run> [--set ...]
                [--perm <permreport.json>] [--names <hash name tsv>] [--sdp <pkg.sdp>]... --out <dir>

A per-draw run is replay.py's run directory made with `-- --per-draw`: <capture>/f<frame>_d<draw>.png on both sides
(nxd9play: render target 0 after each draw; nx_render: the colour target of that draw's pass), plus nx_render's
summary.txt for skipped draws. A perm.py set needs no per-draw run: each of its frames is one draw after a clear,
so the frame verdict of --perm is the draw verdict.

Per draw:
  pass      its image is within capcompare's frame bounds (THRESHOLDS['frame']) against the reference
  fail      over the bounds; the first failing draw of a frame is that frame's first divergence, and later
            failures in the frame may be its consequences (counted as fail, never as pass)
  skipped   nx_render skipped it (summary.txt). Skipped draws are not dumped, so per-draw images after the first
            skip of a frame no longer line up: those draws count as unaligned (fail)
Family: the pixel program's name without digits and extension (SLS2034.pso -> SLS), from --names (sdp2dksh --list
of the packages: hash -> name) or the perm manifest; our own synthetic programs are family "synthetic:<capture>".

Class of a first divergence (decided in this order, from evidence the gate itself holds):
  shader     its VS or PS fails in isolation in the permutation set (--perm: class over or vacuous-free fail)
  format     its colour target's format, or a bound texture's format, never occurs in a passing draw
  state      one of its render or sampler states (value-like ones excluded: fog/alpha/stencil refs, colours,
             biases) has a value that never occurs in a passing draw
  constants  otherwise: programs, formats and states all pass elsewhere, so the draw's own inputs (constants,
             vertices, texture contents) trigger it
Writes <out>/drawgate.json (fnvnx-drawgate/1) and drawgate.md. Program names are the game's: keep both private;
the counts are public. Exit 0.
"""
import argparse, collections, json, os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nxd9io, metrics
from capcompare import THRESHOLDS, judge

FORMAT = 'fnvnx-drawgate/1'
VALUE_RS = {24, 34, 36, 37, 38, 57, 58, 59, 60, 154, 155, 156, 157, 158, 159, 175, 193, 195, 16}   # refs, colours, biases
VALUE_SS = {4, 8}   # BORDERCOLOR, MIPMAPLODBIAS


def fmt_name(f):
    return struct.pack('<I', f).decode('latin-1') if f > 255 else str(f)


def family(name):
    if not name:
        return 'unknown'
    return re.sub(r'\d+$', '', name.rsplit('.', 1)[0]) or name


def draws_of(path):
    """per frame, per draw: programs (hashes), target format, texture formats, state snapshot"""
    sh, surf, tex, rs, ss, stex = {}, {}, {}, {}, {}, {}
    vs = ps = rt = 0
    dev_fmt, out, cur = 21, [], []
    for t, p in nxd9io.records(path):
        if t == 'DEV ':
            dev_fmt = struct.unpack_from('<3I', p)[2]
        elif t in ('VSH ', 'PSH '):
            sid, lo, hi = struct.unpack_from('<3I', p)
            sh[(t, sid)] = '%016x' % (lo | hi << 32)
        elif t == 'SVS ':
            vs = struct.unpack_from('<I', p)[0]
        elif t == 'SPS ':
            ps = struct.unpack_from('<I', p)[0]
        elif t == 'TEX ':
            tid, f = struct.unpack_from('<2I', p)
            tex[tid] = f
        elif t == 'SURF':
            sid, kind, f = struct.unpack_from('<3I', p)
            surf[sid] = f
        elif t == 'SRT ':
            i, s = struct.unpack_from('<2I', p)
            if i == 0:
                rt = s
        elif t == 'RS  ':
            k, v = struct.unpack_from('<2I', p)
            rs[k] = v
        elif t == 'SS  ':
            s, k, v = struct.unpack_from('<3I', p)
            ss[(s, k)] = v
        elif t == 'STEX':
            s, tid = struct.unpack_from('<2I', p)
            stex[s] = tid
        elif t in ('DRWI', 'DRAW'):
            bound = {s: tex.get(tid) for s, tid in stex.items() if tid}
            cur.append({'vs': sh.get(('VSH ', vs)), 'ps': sh.get(('PSH ', ps)),
                        'rt_fmt': surf.get(rt, dev_fmt) if rt else dev_fmt,
                        'tex_fmts': sorted({f for f in bound.values() if f is not None}),
                        'rs': {k: v for k, v in rs.items() if k not in VALUE_RS},
                        'ss': {(s, k): v for (s, k), v in ss.items() if s in bound and k not in VALUE_SS}})
        elif t == 'PRES':
            out.append(cur)
            cur = []
    return out


def compare_small(a, b):
    """metrics.compare, but targets below SSIM's 11x11 window (luminance chains: 4x4, 1x1) are judged on PSNR and
    the share of pixels over 8 only (ssim 1, no FLIP)"""
    if min(a.shape[:2]) >= 11 and a.shape == b.shape:
        return metrics.compare(a, b)
    import numpy as np, math
    if a.shape != b.shape:
        return {'error': f'size {a.shape} != {b.shape}'}
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    p = metrics.psnr(a, b)
    return {'identical': bool(d.max() == 0), 'psnr': None if math.isinf(p) else round(p, 4), 'ssim': 1.0,
            'flip': None, 'max_abs': int(d.max()), 'mean_abs': round(float(d.mean()), 5),
            'frac_over_8': round(float((d.max(axis=2) > 8).mean()), 6)}


def skipped_of(run, cap):
    p = os.path.join(run, cap, 'summary.txt')
    s = open(p).read() if os.path.isfile(p) else ''
    m = re.search(r'skipped (\d+)', s)
    first = re.search(r'first skip: frame (\d+) draw (\d+)', s)
    return (int(m.group(1)) if m else 0), ((int(first.group(1)), int(first.group(2))) if first else None)


def gate(sets, perm, names):
    bounds = THRESHOLDS['frame']
    rows = []
    perm_fail = set()
    if perm:
        for r in perm['rows']:
            if r['class'] in ('over', 'ill'):
                perm_fail.add(r['hash'])
        for r in perm['rows']:
            names.setdefault(r['hash'], r['name'])
    for sname, (caps, ref, cand) in sets.items():
        if sname == 'perm' and perm:
            for r in perm['rows']:
                if r['class'] == 'vacuous':
                    continue
                rows.append({'set': sname, 'capture': r['capture'], 'frame': int(r['frame']), 'draw': 0,
                             'verdict': 'pass' if r['class'] == 'pass' else 'fail', 'family': family(r['name']),
                             'vs': r['hash'] if r['stage'] == 'vertex' else None,
                             'ps': r['hash'] if r['stage'] == 'pixel' else None, 'rt_fmt': 21, 'tex_fmts': [],
                             'rs': {}, 'ss': {}, 'metrics': {'max_abs': r['max_abs'], 'mean_abs': r['mean_abs']}})
            continue
        for f in sorted(os.listdir(caps)):
            if not f.endswith('.nxd9'):
                continue
            cap = f[:-5]
            frames = draws_of(os.path.join(caps, f))
            nskip, first_skip = skipped_of(cand, cap)
            for fi, dl in enumerate(frames):
                for di, d in enumerate(dl):
                    name = names.get(d['ps'] or '', '')
                    fam = family(name) if name else ('synthetic:' + cap if sname == 'synthetic' else 'unknown')
                    rp = os.path.join(ref, cap, f'f{fi:05d}_d{di:05d}.png')
                    cp = os.path.join(cand, cap, f'f{fi:05d}_d{di:05d}.png')
                    row = dict(d, set=sname, capture=cap, frame=fi, draw=di, family=fam)
                    if first_skip and (fi, di) >= first_skip:
                        row['verdict'], row['metrics'] = ('skipped' if (fi, di) == first_skip else 'unaligned'), {}
                    elif not (os.path.isfile(rp) and os.path.isfile(cp)):
                        row['verdict'], row['metrics'] = 'fail', {'error': 'image missing'}
                    else:
                        m = compare_small(metrics.load(rp), metrics.load(cp))
                        row['verdict'] = 'fail' if judge(m, bounds) else 'pass'
                        row['metrics'] = {k: m.get(k) for k in ('max_abs', 'mean_abs', 'frac_over_8', 'psnr', 'ssim')}
                    rows.append(row)
            if nskip and not first_skip:
                rows.append({'set': sname, 'capture': cap, 'frame': -1, 'draw': -1, 'verdict': 'skipped',
                             'family': 'unknown', 'metrics': {}, 'rs': {}, 'ss': {}, 'tex_fmts': [], 'rt_fmt': 0})
    # evidence from passing draws
    ok = [r for r in rows if r['verdict'] == 'pass']
    ok_fmts = {r['rt_fmt'] for r in ok} | {f for r in ok for f in r['tex_fmts']}
    ok_rs = {(k, v) for r in ok for k, v in r['rs'].items()}
    ok_ss = {(k[1], v) for r in ok for k, v in r['ss'].items()}
    firsts = []
    seen = set()
    for r in rows:
        key = (r['set'], r['capture'], r['frame'])
        if r['verdict'] == 'pass' or key in seen or r['set'] == 'perm':
            continue
        seen.add(key)
        if r['verdict'] in ('skipped', 'unaligned'):
            cls, why = 'skipped', 'nx_render skipped the draw'
        elif (r['vs'] in perm_fail) or (r['ps'] in perm_fail):
            cls, why = 'shader', 'its program fails in isolation (permutation set)'
        elif r['rt_fmt'] not in ok_fmts or any(f not in ok_fmts for f in r['tex_fmts']):
            bad = sorted({f for f in [r['rt_fmt']] + r['tex_fmts'] if f not in ok_fmts})
            cls, why = 'format', 'never in a passing draw: ' + ', '.join(fmt_name(f) for f in bad)
        else:
            nrs = sorted(f'RS{k}={v}' for k, v in r['rs'].items() if (k, v) not in ok_rs)
            nss = sorted(f'SS{k[1]}={v}' for k, v in r['ss'].items() if (k[1], v) not in ok_ss)
            if nrs or nss:
                cls, why = 'state', 'never in a passing draw: ' + ', '.join((nrs + nss)[:6])
            else:
                cls, why = 'constants', 'programs, formats and states all pass elsewhere'
        firsts.append({'set': r['set'], 'capture': r['capture'], 'frame': r['frame'], 'draw': r['draw'],
                       'family': r['family'], 'class': cls, 'why': why, 'vs': r.get('vs'), 'ps': r.get('ps'),
                       'vs_name': names.get(r.get('vs') or ''), 'ps_name': names.get(r.get('ps') or ''),
                       'metrics': r['metrics']})
    total = len(rows)
    cnt = collections.Counter(r['verdict'] for r in rows)
    fam = collections.defaultdict(collections.Counter)
    for r in rows:
        fam[r['family']][r['verdict']] += 1
    per_set = collections.defaultdict(collections.Counter)
    for r in rows:
        per_set[r['set']][r['verdict']] += 1
    return {'format': FORMAT, 'draws': total, 'pass': cnt['pass'], 'fail': cnt['fail'],
            'skipped': cnt['skipped'] + cnt['unaligned'],
            'pass_fraction': round(cnt['pass'] / total, 4) if total else None,
            'by_set': {k: dict(v) for k, v in sorted(per_set.items())},
            'by_family': {k: dict(v) for k, v in sorted(fam.items())},
            'first_divergence_by_class': dict(collections.Counter(f['class'] for f in firsts)),
            'first_divergences': firsts, 'thresholds': bounds}


def metric_line(g):
    return (f"{g['pass']}/{g['draws']} draws pass ({g['pass_fraction']}), {g['skipped']} skipped; first divergences: "
            + (', '.join(f'{k} {v}' for k, v in sorted(g['first_divergence_by_class'].items())) or 'none'))


def md(g):
    L = ['# S16 render coverage gate (D21)', '', f'metric: {metric_line(g)}', '',
         '| set | pass | fail | skipped |', '|---|---|---|---|']
    for s, c in g['by_set'].items():
        L.append(f"| {s} | {c.get('pass', 0)} | {c.get('fail', 0)} | {c.get('skipped', 0) + c.get('unaligned', 0)} |")
    L += ['', '| family | pass | fail | skipped |', '|---|---|---|---|']
    for s, c in sorted(g['by_family'].items(), key=lambda x: -sum(x[1].values())):
        L.append(f"| {s} | {c.get('pass', 0)} | {c.get('fail', 0)} | {c.get('skipped', 0) + c.get('unaligned', 0)} |")
    L += ['', '## First divergences', '']
    L += [f"* {f['set']}/{f['capture']} frame {f['frame']} draw {f['draw']}: **{f['class']}** ({f['why']}); "
          f"{f['vs_name'] or f['vs']} + {f['ps_name'] or f['ps']}, family {f['family']}, max {f['metrics'].get('max_abs')}"
          for f in g['first_divergences']] or ['* none']
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', action='append', default=[], help='name=captures:ref:cand (perm: name=perm::)')
    ap.add_argument('--perm'); ap.add_argument('--names'); ap.add_argument('--out', required=True)
    ap.add_argument('--sdp', action='append', default=[], help='shader package: program names by hash')
    a = ap.parse_args()
    sets = {}
    for s in a.set:
        n, v = s.split('=', 1)
        sets[n] = tuple(v.split(':'))
    names = {}
    if a.names and os.path.isfile(a.names):
        for line in open(a.names):
            p = line.rstrip('\n').split('\t')
            if len(p) >= 2:
                names[p[0]] = p[1]
    if a.sdp:
        sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'verify', 'shaders'))
        sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'verify'))
        from sdpfuzz import extract
        for pk in a.sdp:
            for n, bc in extract(pk).items():
                names.setdefault('%016x' % nxd9io.fnv1a64(bc), n)
    g = gate(sets, json.load(open(a.perm)) if a.perm else None, names)
    os.makedirs(a.out, exist_ok=True)
    json.dump(g, open(os.path.join(a.out, 'drawgate.json'), 'w'), indent=1)
    open(os.path.join(a.out, 'drawgate.md'), 'w').write(md(g))
    print('drawgate:', metric_line(g))
    return 0


if __name__ == '__main__':
    sys.exit(main())
