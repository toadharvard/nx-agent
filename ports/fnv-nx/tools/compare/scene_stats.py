#!/usr/bin/env python3
"""scene_stats.py - one capture, one JSON (fnvnx-scenestats/1): statistics of the frame, parity of our renderer
against the reference, the first divergent draw and its class, and GPU ms on the handheld model.

    scene_stats.py <capture.nxd9> --exe <nxd9play.exe> --prefix <wine prefix> --nx-render <nx_render>
                   --shaders <dir: sdp2dksh -o D --glsl over every package (.glsl, .dksh, .json)> --sdp-dir <packages>
                   --out <json> [--work <dir>] [--frame F] [--perm <permreport.json>] [--uam <uam>] [--no-bisect] [--ref-cache <dir>]

Parts of the JSON:
  stats     per capture (all frames) and for the stats frame F (default: the last): draws, triangles, vertices,
            unique VS / PS programs (by hash, and by package name), render-target switches, passes (a run of draws
            on one colour target), render- and sampler-state records, texture binds, texture bytes uploaded, draws
            and passes by family (the pixel program's name without digits)
  ref       nxd9play under Wine: frames rendered, frame ms (frames.tsv: a record, not a metric), per-draw coverage of
            frame F (--draw-stats: pixels each draw changes on render target 0)
  paths     "translated" (nx_render's default) and "families" (--families all): exit, skipped draws, draws per path
            (--draw-log: translated or the hand-written family that took it), per-frame parity (capcompare's
            bounds), and for frame F, when it fails, the first divergent draw (a multi-section search over per-draw
            targets: nxd9play --per-draw-at on the reference, nx_render --per-draw pruned as it writes) with its
            drawgate class (shader / format / state / constants), program names and metrics
  gpu       handheld 720p model (render/native/perf/gpu_cost.py constants: Tegra X1, 384 MHz, 256 lanes, 16 TMU,
            16 ROP): per draw of frame F, ms = (pixels x max(ALU, TEX, ROP) + vertices x VS ALU) / clock, with
            pixels = the reference's coverage of that draw (a lower bound: depth-rejected or unchanged pixels are not
            counted), ALU = Maxwell instructions / lanes (uam's DKSH of the translated program), TEX = texture
            instructions x 1.5 / 16 (uam's TGSI, with --uam), ROP 1 / 16 (1 / 8 for 64-bit targets); summed per pass
            and per family. The hand-written families are costed with the translated program's numbers (a stand-in).
Exit 0 with any verdict; 1 when the capture cannot be read or the reference does not render.
"""
import argparse, collections, json, os, re, shutil, struct, subprocess, sys, tempfile, threading, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nxd9io, metrics
from capcompare import THRESHOLDS, judge
from drawgate import compare_small, family, VALUE_RS, VALUE_SS

FORMAT = 'fnvnx-scenestats/1'
REF_FORMAT = 'ref/1'   # the reference replay call (run_ref's arguments, cov.tsv): bump when it changes; part of
                       # the capture cache key (sweep_job.sh), so cached --ref-cache dirs are not reused across it
PRIM_TRIS = {1: lambda n: 0, 2: lambda n: 0, 3: lambda n: 0, 4: lambda n: n, 5: lambda n: n, 6: lambda n: n}
FMT_BPP64 = {113, 116, 36, 115}
GPU = {'clock_mhz': 384.0, 'lanes': 256, 'tmu': 16, 'rop': 16, 'tex_cost': 1.5}


def parse(path):
    """per frame: draws (programs, target, primitive counts, state snapshot) and counters"""
    sh, surf, tex_fmt = {}, {}, {}
    rs, ss, stex = {}, {}, {}
    vs = ps = rt = 0
    frames, cur = [], None
    dev = (0, 0, 21)

    def new_frame():
        return {'draws': [], 'rt_switches': 0, 'rs_records': 0, 'ss_records': 0, 'tex_binds': 0, 'tex_bytes': 0,
                'clears': 0}
    cur = new_frame()
    for t, p in nxd9io.records(path):
        if t == 'DEV ':
            w, h, f = struct.unpack_from('<3I', p)
            dev = (w, h, f)
        elif t in ('VSH ', 'PSH '):
            sid, lo, hi = struct.unpack_from('<3I', p)
            sh[(t, sid)] = '%016x' % (lo | hi << 32)
        elif t == 'SVS ':
            vs = struct.unpack_from('<I', p)[0]
        elif t == 'SPS ':
            ps = struct.unpack_from('<I', p)[0]
        elif t == 'TEX ':
            tid, f, w, h = struct.unpack_from('<4I', p)
            tex_fmt[tid] = f
            cur['tex_bytes'] += max(0, len(p) - 24)
        elif t in ('TEXU', 'TEXC'):
            cur['tex_bytes'] += len(p)
        elif t == 'SURF':
            sid, kind, f, w, h = struct.unpack_from('<5I', p)
            surf[sid] = (f, w, h)
        elif t == 'SRT ':
            i, s = struct.unpack_from('<2I', p)
            if i == 0 and s != rt:
                rt = s
                cur['rt_switches'] += 1
        elif t == 'RS  ':
            k, v = struct.unpack_from('<2I', p)
            rs[k] = v
            cur['rs_records'] += 1
        elif t == 'SS  ':
            s_, k, v = struct.unpack_from('<3I', p)
            ss[(s_, k)] = v
            cur['ss_records'] += 1
        elif t == 'STEX':
            s_, tid = struct.unpack_from('<2I', p)
            stex[s_] = tid
            cur['tex_binds'] += 1 if tid else 0
        elif t == 'CLR ':
            cur['clears'] += 1
        elif t in ('DRWI', 'DRAW'):
            if t == 'DRWI':
                ty, base, mn, nv, start, pc = struct.unpack_from('<IiIIII', p)
            else:
                ty, start, pc = struct.unpack_from('<3I', p)
                nv = {4: 3 * pc, 5: pc + 2, 6: pc + 2}.get(ty, pc)
            f, w, h = surf.get(rt, (dev[2], dev[0], dev[1])) if rt else (dev[2], dev[0], dev[1])
            bound = {s_: tex_fmt.get(tid) for s_, tid in stex.items() if tid}
            cur['draws'].append({'vs': sh.get(('VSH ', vs)), 'ps': sh.get(('PSH ', ps)), 'rt': rt, 'rt_fmt': f,
                                 'rt_size': [w, h], 'prim': ty, 'tris': PRIM_TRIS.get(ty, lambda n: 0)(pc),
                                 'verts': nv, 'tex_fmts': sorted({x for x in bound.values() if x is not None}),
                                 'rs': {k: v for k, v in rs.items() if k not in VALUE_RS},
                                 'ss': {f'{k[0]}:{k[1]}': v for k, v in ss.items() if k[0] in bound and k[1] not in VALUE_SS}})
        elif t == 'PRES':
            frames.append(cur)
            cur = new_frame()
    return frames, dev


def sdp_names(d):
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'verify', 'shaders'))
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'verify'))
    from sdpfuzz import extract
    names = collections.defaultdict(set)
    for fn in sorted(os.listdir(d)):
        if re.match(r'shaderpackage\d+\.sdp$', fn, re.I):
            for n, bc in extract(os.path.join(d, fn)).items():
                names['%016x' % nxd9io.fnv1a64(bc)].add(n)
    return names


def stats_of(frames, names, sel):
    def one(fl):
        ds = [d for f in fl for d in f['draws']]
        passes, fam_d, fam_p = 0, collections.Counter(), collections.Counter()
        for f in fl:
            last = None
            for d in f['draws']:
                fam = family(min(names.get(d['ps'] or '', {''}))) if names.get(d['ps'] or '') else 'unknown'
                fam_d[fam] += 1
                if d['rt'] != last:
                    passes += 1
                    fam_p[fam] += 1
                    last = d['rt']
        vs_h = {d['vs'] for d in ds if d['vs']}
        ps_h = {d['ps'] for d in ds if d['ps']}
        nm = lambda hs: sorted({n for h in hs for n in names.get(h, ())})
        return {'frames': len(fl), 'draws': len(ds), 'triangles': sum(d['tris'] for d in ds),
                'vertices': sum(d['verts'] for d in ds), 'unique_vs': len(vs_h), 'unique_ps': len(ps_h),
                'unique_vs_names': len(nm(vs_h)), 'unique_ps_names': len(nm(ps_h)),
                'rt_switches': sum(f['rt_switches'] for f in fl), 'passes': passes,
                'rs_records': sum(f['rs_records'] for f in fl), 'ss_records': sum(f['ss_records'] for f in fl),
                'tex_binds': sum(f['tex_binds'] for f in fl), 'tex_bytes': sum(f['tex_bytes'] for f in fl),
                'clears': sum(f['clears'] for f in fl), 'draws_by_family': dict(fam_d), 'passes_by_family': dict(fam_p),
                'programs': {'vs': sorted(vs_h), 'ps': sorted(ps_h)}}
    return {'capture': one(frames), 'frame': one([frames[sel]]) if frames else None}


def wine_path(p):
    return 'Z:' + os.path.abspath(p).replace('/', '\\')


def run_ref(cap, a, out, extra):
    r = subprocess.run([sys.executable, '-I', os.path.join(HERE, 'replay.py'), 'ref', '--exe', a.exe, '--prefix',
                        a.prefix, '--out', out, '--timeout', str(a.timeout), cap, '--'] + extra,
                       capture_output=True, text=True)
    return r.returncode


def run_cand(cap, a, out, extra, prune=None):
    os.makedirs(out, exist_ok=True)
    cmd = [a.nx_render, 'd3d9', cap, '-o', out, '--shaders', a.shaders] + extra
    stop = threading.Event()

    def pruner():   # nx_render --per-draw writes every draw of the frame: keep only the probes, bound the disk
        while not stop.is_set():
            for f in os.listdir(out):
                m = re.match(r'f\d+_d(\d+)\.png$', f)
                if m and int(m.group(1)) not in prune:
                    try: os.remove(os.path.join(out, f))
                    except OSError: pass
            time.sleep(0.1)
    th = threading.Thread(target=pruner, daemon=True) if prune is not None else None
    if th: th.start()
    run_cand.last_err = ''
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout)
        rc = r.returncode
        if rc not in (0, 3):   # nx_render's own error line ("nx_render: ..."), kept for the scene's class
            run_cand.last_err = ((r.stderr or '').strip().splitlines() or [''])[-1][:200]
    except subprocess.TimeoutExpired:
        rc = -1
        run_cand.last_err = f'timeout ({a.timeout} s)'
    if th:
        stop.set(); th.join()
        for f in os.listdir(out):
            m = re.match(r'f\d+_d(\d+)\.png$', f)
            if m and int(m.group(1)) not in prune:
                os.remove(os.path.join(out, f))
    return rc


def frame_verdicts(refdir, canddir, n):
    out = []
    for i in range(n):
        rp, cp = os.path.join(refdir, f'f{i:05d}.png'), os.path.join(canddir, f'f{i:05d}.png')
        if not (os.path.isfile(rp) and os.path.isfile(cp)):
            out.append({'frame': i, 'pass': False, 'error': 'missing'}); continue
        m = compare_small(metrics.load(rp), metrics.load(cp))
        why = judge(m, THRESHOLDS['frame']) if 'error' not in m else [m['error']]
        out.append({'frame': i, 'pass': not why, 'psnr': m.get('psnr'), 'max_abs': m.get('max_abs'),
                    'mean_abs': m.get('mean_abs'), 'frac_over_8': m.get('frac_over_8'), 'reasons': why})
    return out


def first_divergence(cap, a, work, frame, ndraws, cand_extra, points=32):
    """the smallest draw index of `frame` whose render target 0 differs (per-draw images on both sides). Rounds of
    `points` probes narrow [lo, hi]; assumes a divergence persists in the target once it appears."""
    lo, hi, rnd = 0, ndraws - 1, 0
    first_bad = None
    while lo <= hi:
        step = max(1, (hi - lo + 1) // points)
        probes = sorted(set(list(range(lo, hi + 1, step)) + [hi]))
        rd, cd = os.path.join(work, f'bis{rnd}_ref'), os.path.join(work, f'bis{rnd}_cand')
        shutil.rmtree(rd, ignore_errors=True); shutil.rmtree(cd, ignore_errors=True)
        run_ref(cap, a, rd, ['--per-draw-at', ','.join(map(str, probes)), '--frame', str(frame), '--frames',
                             str(frame)])
        run_cand(cap, a, cd, cand_extra + ['--per-draw', '--frame', str(frame)], prune=set(probes))
        name = os.path.splitext(os.path.basename(cap))[0]
        bad = None
        for k in probes:
            rp = os.path.join(rd, name, f'f{frame:05d}_d{k:05d}.png')
            cp = os.path.join(cd, f'f{frame:05d}_d{k:05d}.png')
            if not (os.path.isfile(rp) and os.path.isfile(cp)):
                continue
            m = compare_small(metrics.load(rp), metrics.load(cp))
            if 'error' in m or judge(m, THRESHOLDS['frame']):
                bad = (k, m); break
        shutil.rmtree(rd, ignore_errors=True); shutil.rmtree(cd, ignore_errors=True)
        rnd += 1
        if bad is None:
            return first_bad
        first_bad = bad
        prev = max([p for p in probes if p < bad[0]], default=lo - 1)
        if bad[0] - prev <= 1 or step == 1:
            return first_bad
        lo, hi = prev + 1, bad[0] - 1
        if lo > hi:
            return first_bad
    return first_bad


def classify(d, ok_draws, perm_fail):
    ok_fmts = {x['rt_fmt'] for x in ok_draws} | {f for x in ok_draws for f in x['tex_fmts']}
    ok_rs = {(k, v) for x in ok_draws for k, v in x['rs'].items()}
    ok_ss = {(k.split(':')[1], v) for x in ok_draws for k, v in x['ss'].items()}
    if d['vs'] in perm_fail or d['ps'] in perm_fail:
        return 'shader', 'its program fails in isolation (permutation set)'
    bad = sorted({f for f in [d['rt_fmt']] + d['tex_fmts'] if f not in ok_fmts})
    if bad:
        return 'format', 'not in an earlier passing draw: ' + ', '.join(map(str, bad))
    nrs = sorted(f'RS{k}={v}' for k, v in d['rs'].items() if (k, v) not in ok_rs)
    nss = sorted(f'SS{k.split(":")[1]}={v}' for k, v in d['ss'].items() if (k.split(':')[1], v) not in ok_ss)
    if nrs or nss:
        return 'state', 'not in an earlier passing draw: ' + ', '.join((nrs + nss)[:6])
    return 'constants', 'programs, formats and states all pass in earlier draws'


def program_cost(h, a, cache):
    if h in cache:
        return cache[h]
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'native', 'perf'))
    import gpu_cost
    c = None
    dk, gl, js = (os.path.join(a.shaders, f'{h}.{x}') for x in ('dksh', 'glsl', 'json'))
    if os.path.isfile(dk):
        try:
            st = gpu_cost.dksh_stats(dk)
            c = {'maxwell': st['maxwell_static'], 'gprs': st['gprs'], 'tex': 0}
            if a.uam and os.path.isfile(gl) and os.path.isfile(js):
                stage = 'frag' if json.load(open(js)).get('stage') == 'pixel' else 'vert'
                with tempfile.TemporaryDirectory() as t:
                    try:
                        c['tex'] = gpu_cost.tgsi_stats(a.uam, gl, stage, t, 0)['tex']
                    except Exception:
                        pass
        except Exception:
            c = None
    cache[h] = c
    return c


def gpu_ms(draws, coverage, a):
    cache, per = {}, []
    clk = GPU['clock_mhz'] * 1e6
    for i, d in enumerate(draws):
        pc, vc = program_cost(d['ps'], a, cache) if d['ps'] else None, program_cost(d['vs'], a, cache) if d['vs'] else None
        px = max(0, coverage.get(i, 0))
        alu = (pc['maxwell'] / GPU['lanes']) if pc else 0.0
        tex = (pc['tex'] * GPU['tex_cost'] / GPU['tmu']) if pc else 0.0
        rop = 1.0 / (GPU['rop'] / 2 if d['rt_fmt'] in FMT_BPP64 else GPU['rop'])
        vtx = d['verts'] * ((vc['maxwell'] / GPU['lanes']) if vc else 0.0)
        ms = (px * max(alu, tex, rop) + vtx) / clk * 1000.0
        per.append({'draw': i, 'ms': round(ms, 5), 'pixels': px, 'ps_maxwell': pc and pc['maxwell'],
                    'ps_gprs': pc and pc['gprs'], 'vs_maxwell': vc and vc['maxwell'], 'costed': bool(pc)})
    return per, cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('capture'); ap.add_argument('--exe', required=True); ap.add_argument('--prefix', required=True)
    ap.add_argument('--nx-render', required=True); ap.add_argument('--shaders', required=True)
    ap.add_argument('--sdp-dir', required=True); ap.add_argument('--out', required=True)
    ap.add_argument('--work'); ap.add_argument('--frame', type=int); ap.add_argument('--perm')
    ap.add_argument('--uam'); ap.add_argument('--no-bisect', action='store_true')
    ap.add_argument('--timeout', type=int, default=1500)
    ap.add_argument('--ref-cache', help='a directory for the reference replay (frames, frames.tsv, cov.tsv, rc): used '
                    'as is when it holds one, else filled (capstore.py keeps it next to the capture)')
    a = ap.parse_args()
    t0 = time.time()
    cap = os.path.abspath(a.capture)
    name = os.path.splitext(os.path.basename(cap))[0]
    work = a.work or tempfile.mkdtemp(prefix='scene_stats.')
    os.makedirs(work, exist_ok=True)
    try:
        frames, dev = parse(cap)
    except ValueError as e:
        print(f'scene_stats: {e}', file=sys.stderr); return 1
    if not frames:
        print('scene_stats: no frames', file=sys.stderr); return 1
    F = a.frame if a.frame is not None else len(frames) - 1
    names = sdp_names(a.sdp_dir)
    out = {'format': FORMAT, 'capture': name, 'frames': len(frames), 'stats_frame': F, 'size': list(dev[:2]),
           'stats': stats_of(frames, names, F)}
    # reference: final frames, coverage of frame F, frame times
    rbase = os.path.abspath(a.ref_cache) if a.ref_cache else work
    rd, covp, rcp = os.path.join(rbase, 'ref'), os.path.join(rbase, 'cov.tsv'), os.path.join(rbase, 'rc')
    if a.ref_cache and os.path.isfile(rcp) and os.path.isdir(rd):
        rc = int(open(rcp).read().strip() or 1)      # cached: the capture, nxd9play and F are in the cache key
        out['ref_cached'] = True
    else:
        os.makedirs(rbase, exist_ok=True)
        shutil.rmtree(rd, ignore_errors=True)
        rc = run_ref(cap, a, rd, ['--frame', str(F), '--draw-stats', wine_path(covp)])
        if a.ref_cache:
            open(rcp, 'w').write(f'{rc}\n')
    cov = {}
    if os.path.isfile(covp):
        for line in open(covp):
            f = line.split('\t')
            if len(f) >= 6 and int(f[0]) == F:
                cov[int(f[1])] = int(f[5])
    ft = []
    fp = os.path.join(rd, name, 'frames.tsv')
    if os.path.isfile(fp):
        ft = [float(l.split('\t')[1]) for l in open(fp) if '\t' in l]
    out['ref'] = {'rc': rc, 'frames_rendered': len([f for f in os.listdir(os.path.join(rd, name)) if re.match(r'f\d+\.png$', f)]) if os.path.isdir(os.path.join(rd, name)) else 0,
                  'frame_ms': ft, 'coverage_draws': len(cov), 'coverage_pixels': sum(v for v in cov.values() if v > 0)}
    if rc not in (0,) or not out['ref']['frames_rendered']:
        json.dump(out, open(a.out, 'w'), indent=1)
        print(f'scene_stats: {name}: the reference did not render (exit {rc})', file=sys.stderr)
        return 1
    perm_fail = set()
    if a.perm and os.path.isfile(a.perm):
        perm_fail = {r['hash'] for r in json.load(open(a.perm))['rows'] if r['class'] in ('over', 'ill')}
    drawsF = frames[F]['draws']
    out['paths'] = {}
    for pname, extra in (('translated', []), ('families', ['--families', 'all'])):
        cd = os.path.join(work, pname)
        shutil.rmtree(cd, ignore_errors=True)
        dl = os.path.join(work, pname + '.drawlog')
        rc = run_cand(cap, a, cd, extra + ['--draw-log', dl])
        summ = open(os.path.join(cd, 'summary.txt')).read().strip() if os.path.isfile(os.path.join(cd, 'summary.txt')) else ''
        m = re.search(r'skipped (\d+)', summ)
        by_path = collections.Counter()
        if os.path.isfile(dl):
            for line in open(dl):
                f = line.rstrip('\n').split('\t')
                if len(f) >= 7:
                    by_path[f[6] if f[6] != '-' else 'translated'] += 1
        fv = frame_verdicts(os.path.join(rd, name), cd, len(frames))
        p = {'rc': rc, 'skipped': int(m.group(1)) if m else None, 'summary': summ, 'draws_by_path': dict(by_path),
             'frames': fv, 'frames_over': sum(1 for x in fv if not x['pass'])}
        fF = fv[F] if F < len(fv) else None
        cand_err = getattr(run_cand, 'last_err', '')
        if rc not in (0, 3) and not any('error' not in x for x in fv):
            # the candidate rendered no frame at all: no draw to bisect; the class is the failure itself (formerly
            # counted as 'unknown' by sweep_report), its error line the reason
            p['first_divergence'] = {'frame': F, 'draw': 0, 'class': 'candidate_error', 'why': cand_err or f'exit {rc}',
                                     'vs': None, 'ps': None, 'vs_names': [], 'ps_names': [], 'family': 'none',
                                     'metrics': {}}
            p['draws_pass_frame'] = 0
        elif fF and not fF['pass'] and not a.no_bisect and drawsF:
            fd = first_divergence(cap, a, os.path.join(work, 'bis_' + pname), F, len(drawsF), extra)
            if fd:
                k, mm = fd
                d = drawsF[k]
                cls, why = classify(d, drawsF[:k], perm_fail)
                p['first_divergence'] = {'frame': F, 'draw': k, 'class': cls, 'why': why, 'vs': d['vs'], 'ps': d['ps'],
                                         'vs_names': sorted(names.get(d['vs'] or '', ())), 'ps_names': sorted(names.get(d['ps'] or '', ())),
                                         'family': family(min(names[d['ps']])) if names.get(d['ps'] or '') else 'unknown',
                                         'metrics': {x: mm.get(x) for x in ('max_abs', 'mean_abs', 'frac_over_8', 'psnr')}}
                p['draws_pass_frame'] = k
            else:
                p['first_divergence'] = None
                p['draws_pass_frame'] = None
        else:
            p['draws_pass_frame'] = len(drawsF) if fF and fF['pass'] else None
        out['paths'][pname] = p
        shutil.rmtree(cd, ignore_errors=True)
    # GPU ms of frame F on the handheld model
    per, cache = gpu_ms(drawsF, cov, a)
    pass_ms, fam_ms, last, pi = collections.Counter(), collections.Counter(), None, -1
    for d, g in zip(drawsF, per):
        if d['rt'] != last:
            pi += 1; last = d['rt']
        pass_ms[pi] += g['ms']
        fam = family(min(names[d['ps']])) if names.get(d['ps'] or '') else 'unknown'
        fam_ms[fam] += g['ms']
    out['gpu'] = {'model': dict(GPU, pixels='reference coverage (--draw-stats), lower bound'),
                  'frame_ms': round(sum(g['ms'] for g in per), 4), 'costed_draws': sum(1 for g in per if g['costed']),
                  'draws': len(per), 'by_pass_ms': {str(k): round(v, 4) for k, v in pass_ms.items()},
                  'by_family_ms': {k: round(v, 4) for k, v in fam_ms.most_common()},
                  'top_draws': sorted(per, key=lambda g: -g['ms'])[:10],
                  'programs': {h: c for h, c in cache.items() if c}}
    out['seconds'] = round(time.time() - t0, 1)
    if not a.work:
        shutil.rmtree(work, ignore_errors=True)
    json.dump(out, open(a.out, 'w'), indent=1)
    tr = out['paths'].get('translated', {})
    print(f"scene_stats: {name}: {out['stats']['frame']['draws']} draws, translated {tr.get('frames_over')}/{len(frames)} "
          f"frames over, first divergence {(tr.get('first_divergence') or {}).get('class')}, gpu {out['gpu']['frame_ms']} ms "
          f"({out['seconds']} s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
