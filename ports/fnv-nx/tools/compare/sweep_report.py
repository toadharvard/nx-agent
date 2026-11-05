#!/usr/bin/env python3
"""sweep_report.py - the sweep's tables and summaries (tools/farm/sweep_job.sh).

    sweep_report.py programs  --shard-dir <S> --shaders <sdp2dksh dir> [--uam <uam>] --out <programs.tsv>
    sweep_report.py shard     --shard-dir <S> --head <commit> --k K --n N [--cand C] [--notes TEXT] --out <json> --md <md>
    sweep_report.py aggregate --dirs <dir of <k>of<N> results>... [--all-programs <sdp2dksh dir>] --out <json> --md <md>

programs   one row per program of the shard: name, hash, stage, model, per parameter set its class on both paths
           (translated, families: pass / over / ill / vacuous), the worst class, and uam's numbers: Maxwell
           instructions, GPRs (DKSH), texture instructions (TGSI, with --uam), and ns per covered pixel on the
           handheld model (render/native/perf/gpu_cost.py: Maxwell / 256 lanes, texture x 1.5 / 16 TMU, at 384 MHz)
shard      the shard's results (programs.tsv, scenes/*.json, done.tsv) as one JSON and a summary.md
aggregate  every shard of a head (reports/sweep/<head12>/*of*): programs checked out of all (1,809), failure
           classes ranked by programs and by draws, the top programs by GPU ms across the scenes (the candidates for
           hand-written GLSL), the scenes with the worst frame ms, programs never seen in any scene
Every output names the game's programs and scenes: private repository only.
"""
import argparse, collections, glob, json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', '..', 'render', 'native', 'perf'))
CLASS_ORDER = ['over', 'ill', 'vacuous', 'pass']   # worst first
CLOCK_HZ, LANES, TMU, TEX_COST = 384e6, 256, 16, 1.5


def worst(cs):
    cs = [c for c in cs if c]
    return min(cs, key=CLASS_ORDER.index) if cs else ''


def programs(a):
    import gpu_cost
    rows = {}
    for v in sorted(os.listdir(os.path.join(a.shard_dir, 'perm'))):
        for path, col in (('rep/permreport.json', 'translated'), ('rep_fam/permreport.json', 'families')):
            p = os.path.join(a.shard_dir, 'perm', v, path)
            if not os.path.isfile(p):
                continue
            for r in json.load(open(p))['rows']:
                e = rows.setdefault(r['hash'], {'name': r['name'], 'hash': r['hash'], 'stage': r['stage'],
                                                'model': r['model'], 'classes': {}})
                e['classes'][f'{v}:{col}'] = r['class']
    variants = sorted({k.split(':')[0] for e in rows.values() for k in e['classes']})
    with open(a.out, 'w') as f:
        f.write('\t'.join(['name', 'hash', 'stage', 'model'] + [f'{v}:{c}' for v in variants for c in ('translated', 'families')]
                          + ['worst_translated', 'worst_families', 'maxwell', 'gprs', 'tex', 'ns_per_pixel']) + '\n')
        for h, e in sorted(rows.items(), key=lambda x: x[1]['name']):
            mx = gp = tx = ns = ''
            dk = os.path.join(a.shaders, h + '.dksh')
            if os.path.isfile(dk):
                try:
                    st = gpu_cost.dksh_stats(dk); mx, gp = st['maxwell_static'], st['gprs']
                    gl = os.path.join(a.shaders, h + '.glsl')
                    if a.uam and os.path.isfile(gl):
                        with tempfile.TemporaryDirectory() as t:
                            tx = gpu_cost.tgsi_stats(a.uam, gl, 'frag' if e['stage'] == 'pixel' else 'vert', t, 0)['tex']
                    if e['stage'] == 'pixel':
                        ns = round(max(mx / LANES, (tx or 0) * TEX_COST / TMU) / CLOCK_HZ * 1e9, 4)
                except Exception:
                    pass
            cols = [e['classes'].get(f'{v}:{c}', '') for v in variants for c in ('translated', 'families')]
            wt = worst([e['classes'].get(f'{v}:translated') for v in variants])
            wf = worst([e['classes'].get(f'{v}:families') for v in variants])
            f.write('\t'.join(map(str, [e['name'], h, e['stage'], e['model']] + cols + [wt, wf, mx, gp, tx, ns])) + '\n')
    print(f'programs: {len(rows)} rows, variants {variants}')
    return 0


def read_tsv(p):
    if not os.path.isfile(p):
        return []
    lines = open(p).read().splitlines()
    if not lines:
        return []
    hdr = lines[0].split('\t')
    return [dict(zip(hdr, l.split('\t'))) for l in lines[1:]]


def load_shard(d):
    progs = read_tsv(os.path.join(d, 'programs.tsv'))
    scenes = []
    for p in sorted(glob.glob(os.path.join(d, 'scenes', '*.json'))):
        try:
            scenes.append(json.load(open(p)))
        except ValueError:
            pass
    done = [l.split('\t') for l in open(os.path.join(d, 'done.tsv')).read().splitlines()] if os.path.isfile(os.path.join(d, 'done.tsv')) else []
    return progs, scenes, done


def scene_row(j):
    tr = j.get('paths', {}).get('translated', {})
    fm = j.get('paths', {}).get('families', {})
    fd = tr.get('first_divergence') or {}
    st = (j.get('stats') or {}).get('frame') or {}
    return {'scene': j['capture'], 'draws': st.get('draws'), 'triangles': st.get('triangles'),
            'unique_ps': st.get('unique_ps'), 'passes': st.get('passes'),
            'frames_over': tr.get('frames_over'), 'families_frames_over': fm.get('frames_over'),
            'skipped': tr.get('skipped'), 'draws_pass': tr.get('draws_pass_frame'),
            'first_class': fd.get('class'), 'first_ps': ','.join(fd.get('ps_names', [])) or fd.get('ps'),
            'first_draw': fd.get('draw'), 'gpu_ms': (j.get('gpu') or {}).get('frame_ms'),
            'ref_frame_ms': ((j.get('ref') or {}).get('frame_ms') or [None])[-1]}


def summarize(progs, scenes, done, all_hashes=None, title=''):
    n_prog = len(progs)
    cls_prog = collections.Counter(p.get('worst_translated') or 'none' for p in progs)
    cls_prog_f = collections.Counter(p.get('worst_families') or 'none' for p in progs)
    sr = [scene_row(j) for j in scenes]
    draws_total = sum(r['draws'] or 0 for r in sr)
    draws_pass = sum((r['draws_pass'] if r['draws_pass'] is not None else 0) for r in sr)
    cls_scene = collections.Counter(r['first_class'] or ('pass' if r['frames_over'] == 0 else 'unknown') for r in sr)
    cls_draws = collections.Counter()
    for r in sr:
        if r['first_class'] and r['draws'] is not None and r['first_draw'] is not None:
            cls_draws[r['first_class']] += r['draws'] - r['first_draw']
    # GPU ms by program across the scenes (top candidates for hand-written GLSL)
    gpu_prog = collections.Counter()
    seen = set()
    for j in scenes:
        g = j.get('gpu') or {}
        for d in g.get('top_draws', []):
            pass
        for h in ((j.get('stats') or {}).get('capture') or {}).get('programs', {}).get('ps', []) + \
                 ((j.get('stats') or {}).get('capture') or {}).get('programs', {}).get('vs', []):
            seen.add(h)
        for fam, ms in (g.get('by_family_ms') or {}).items():
            gpu_prog[fam] += ms
    worst_scenes = sorted([r for r in sr if r['gpu_ms'] is not None], key=lambda r: -r['gpu_ms'])[:15]
    never = sorted(h for h in (all_hashes or set()) if h not in seen) if all_hashes else None
    by_name = {p['hash']: p['name'] for p in progs}
    top_progs = sorted([p for p in progs if p.get('ns_per_pixel') not in ('', None)],
                       key=lambda p: -float(p['ns_per_pixel']))[:20]
    out = {'title': title, 'programs_checked': n_prog, 'programs_total': len(all_hashes) if all_hashes else None,
           'program_classes_translated': dict(cls_prog), 'program_classes_families': dict(cls_prog_f),
           'scenes_done': len(done), 'scenes_measured': len(sr),
           'scene_status': dict(collections.Counter(d[1] for d in done if len(d) > 1)),
           'draws': draws_total, 'draws_pass': draws_pass, 'first_divergence_by_scenes': dict(cls_scene),
           'first_divergence_by_draws': dict(cls_draws), 'gpu_ms_by_family': dict(gpu_prog.most_common()),
           'worst_scenes_gpu_ms': worst_scenes, 'top_programs_ns_per_pixel': [
               {k: p[k] for k in ('name', 'hash', 'stage', 'maxwell', 'gprs', 'tex', 'ns_per_pixel')} for p in top_progs],
           'failing_programs': [{k: p[k] for k in ('name', 'hash', 'worst_translated', 'worst_families')}
                                for p in progs if p.get('worst_translated') in ('over', 'ill')],
           'programs_never_seen_in_scenes': len(never) if never is not None else None,
           'scenes': sr}
    return out, never


def md(o):
    L = [f"# {o['title']}", '',
         f"metric: programs checked {o['programs_checked']}" + (f"/{o['programs_total']}" if o['programs_total'] else '')
         + f"; classes (translated) {o['program_classes_translated']}; scenes {o['scenes_measured']} measured "
         f"({o['scene_status']}); draws {o['draws_pass']}/{o['draws']} pass", '',
         '## Failure classes', '',
         f"- programs (translated, worst over the parameter sets): {o['program_classes_translated']}",
         f"- programs (--families all): {o['program_classes_families']}",
         f"- scenes by the class of the first divergence: {o['first_divergence_by_scenes']}",
         f"- draws from the first divergence on, by its class: {o['first_divergence_by_draws']}", '',
         '## Failing programs', ''] + [f"- {p['name']} `{p['hash']}`: translated {p['worst_translated']}, families {p['worst_families']}"
                                       for p in o['failing_programs'][:50]] + ['',
         '## Top programs by GPU cost per pixel (handheld, ns)', ''] + [
             f"- {p['name']} `{p['hash']}`: {p['ns_per_pixel']} ns/px (Maxwell {p['maxwell']}, GPRs {p['gprs']}, tex {p['tex']})"
             for p in o['top_programs_ns_per_pixel']] + ['',
         '## GPU ms by family over all scenes (handheld, reference coverage)', ''] + [
             f"- {k}: {round(v, 3)}" for k, v in list(o['gpu_ms_by_family'].items())[:20]] + ['',
         '## Worst scenes by GPU ms (handheld model)', '',
         '| scene | draws | passes | gpu ms | frames over | first divergence | ref frame ms (Wine, record) |', '|---|---|---|---|---|---|---|'] + [
             f"| {r['scene']} | {r['draws']} | {r['passes']} | {r['gpu_ms']} | {r['frames_over']} | {r['first_class'] or '-'} {r['first_ps'] or ''} | {r['ref_frame_ms']} |"
             for r in o['worst_scenes_gpu_ms']] + ['',
         f"programs never seen in any measured scene: {o['programs_never_seen_in_scenes']}", '']
    if o.get('notes'):
        L += ['## Notes', ''] + [f'- {n}' for n in o['notes']]
    return '\n'.join(L) + '\n'


def all_program_hashes(d):
    return {f[:-5] for f in os.listdir(d) if f.endswith('.json') and len(f) == 21} if d and os.path.isdir(d) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['programs', 'shard', 'aggregate'])
    ap.add_argument('--shard-dir'); ap.add_argument('--shaders'); ap.add_argument('--uam'); ap.add_argument('--out')
    ap.add_argument('--md'); ap.add_argument('--head', default=''); ap.add_argument('--k'); ap.add_argument('--n')
    ap.add_argument('--cand', default=''); ap.add_argument('--notes', default='')
    ap.add_argument('--dirs', nargs='*', default=[]); ap.add_argument('--all-programs')
    a = ap.parse_args()
    if a.cmd == 'programs':
        return programs(a)
    if a.cmd == 'shard':
        progs, scenes, done = load_shard(a.shard_dir)
        o, _ = summarize(progs, scenes, done, title=f'sweep {a.head[:12]} shard {a.k}/{a.n} (candidate nx_render {a.cand[:12]})')
        o.update(head=a.head, k=a.k, n=a.n, candidate=a.cand, notes=[n for n in a.notes.splitlines() if n])
    else:
        progs, scenes, done = [], [], []
        for d in a.dirs:
            p, s, dn = load_shard(d)
            progs += p; scenes += s; done += dn
        allh = all_program_hashes(a.all_programs)
        o, never = summarize(progs, scenes, done, allh, title=f'sweep summary over {len(a.dirs)} shards')
        if never is not None:
            o['programs_never_seen'] = never
    json.dump(o, open(a.out, 'w'), indent=1)
    open(a.md, 'w').write(md(o))
    print(md(o).splitlines()[2])
    return 0


if __name__ == '__main__':
    sys.exit(main())
