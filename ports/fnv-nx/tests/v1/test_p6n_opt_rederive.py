#!/usr/bin/env python3
"""test_p6n_opt_rederive.py - V1 (D14) against P6n 4463ec3 / c4ebb04 / 91710d3: optimizations[] (render/native/perf/
optimizations.json, carried by report.json) records "each S6n perf commit measured against its parent".
  D1  internal arithmetic: every entry's cpu_instr_delta_main / _render and gpu_ms pairs equal its own before/after
      snapshots (opt_delta.py entry())
  D2  chain: an entry whose before_commit is the previous entry's commit starts from that entry's after snapshot
  D3  traceability: every entry's commit / before_commit is a commit of this repository (git cat-file), so the
      measured trees can be rebuilt
  D4  re-derivation, two entries, independently: the merged commit and its first parent are exported (git archive),
      the current perf tools' x86-64 host driver (perf/native_drv.cpp, -DPERF_CALLGRIND, build.sh's flags) is built
      against each tree, mix_hi (gen_stream.py) runs under callgrind real and dry, and the main-thread instructions
      of the measured frame (mark 4k+1 -> 4k+2, real - dry) are compared with the commit message's x86-64 delta
      (within 10 %; the absolute level depends on the stream generator's version):
        c94cc7d (epilogue lookup table)   message: main 8.16 M -> 7.64 M
        831830a (epilogue / dyn reuse)    message: main 7.42 M -> 7.03 M
  D5  "measured" vs model: entries whose only change is gpu_ms (cpu deltas 0) come from gpu_cost.py, a model
      (uam instruction counts x efficiency); the doc must not present them as measured
  python3 -I tests/v1/test_p6n_opt_rederive.py [--skip-d4]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
PERF = os.path.join(ROOT, 'render', 'native', 'perf')
DOC = os.path.join(ROOT, 'docs', 'RENDER_PERF.md')
REDERIVE = [('c94cc7d', 8.16e6, 7.64e6), ('831830a', 7.42e6, 7.03e6)]


def git(*a):
    return subprocess.run(['git', '-C', ROOT] + list(a), capture_output=True, text=True)


def build_and_measure(commit, work):
    tree = os.path.join(work, commit)
    os.makedirs(tree)
    top = git('rev-parse', '--show-toplevel').stdout.strip()
    tar = os.path.join(work, commit + '.tar')
    arch = subprocess.run(['git', '-C', top, 'archive', '-o', tar, commit, 'ports/fnv-nx/render/native'], capture_output=True)
    if arch.returncode:
        raise SystemExit('git archive %s failed: %s' % (commit, arch.stderr.decode()[-300:]))
    subprocess.run(['tar', '-x', '-C', tree, '-f', tar], check=True)
    n = os.path.join(tree, 'ports', 'fnv-nx', 'render', 'native')
    for f in ('native_drv.cpp', 'nxd3d.h'):   # the current perf tools, as opt_delta.py does
        shutil.copy(os.path.join(PERF, f), os.path.join(n, 'perf', f))
    b = os.path.join(tree, 'b')
    os.makedirs(b)
    subprocess.run([sys.executable, os.path.join(n, 'shaders', 'build.py'), os.path.join(b, 'shaders')], check=True, capture_output=True)
    hf = ['-O2', '-g', '-I' + os.path.join(n, 'include'), '-I' + os.path.join(n, 'front'), '-I' + os.path.join(n, 'n1'),
          '-I' + os.path.join(n, 'capture'), '-I' + os.path.join(b, 'shaders'), '-I' + os.path.join(n, 'perf')]
    hxx = hf + ['-std=gnu++17', '-fno-exceptions', '-fno-rtti']
    objs = []
    for s in ('front/front.cpp', 'n1/renderer.cpp', 'capture/capture.cpp'):
        o = os.path.join(b, os.path.basename(s)[:-4] + '.o')
        subprocess.run(['g++'] + hxx + ['-c', os.path.join(n, s), '-o', o], check=True)
        objs.append(o)
    for s in (os.path.join(n, 'front', 'd3d9_state.c'), os.path.join(b, 'shaders', 'nx_shaders.c'), os.path.join(n, 'front', 'gpu_dispatch.c')):
        o = os.path.join(b, os.path.basename(s)[:-2] + '.o')
        subprocess.run(['gcc'] + hf + ['-std=gnu11', '-c', s, '-o', o], check=True)
        objs.append(o)
    exe = os.path.join(b, 'native_drv_host')
    subprocess.run(['g++'] + hxx + ['-DPERF_CALLGRIND', os.path.join(n, 'perf', 'native_drv.cpp')] + objs + ['-lm', '-o', exe], check=True)
    return exe


def callgrind(exe, stream, dry, cg, frames):
    sys.path.insert(0, PERF)
    import cg_split
    base = os.path.join(cg, os.path.basename(os.path.dirname(os.path.dirname(exe))) + ('.dry' if dry else '.real'))
    subprocess.run(['valgrind', '--tool=callgrind', '--callgrind-out-file=' + base, exe, stream, '1' if dry else '0', '-', '-', '3'],
                   check=True, capture_output=True)
    parts = {}
    for f in os.listdir(cg):
        if not f.startswith(os.path.basename(base) + '.'):
            continue
        p = os.path.join(cg, f)
        trig = [ln for ln in open(p) if ln.startswith('desc: Trigger')]
        if trig and 'mark' in trig[0]:
            parts[int(trig[0].split('mark')[1])] = cg_split.split(p, 12)
    k = frames - 1
    return parts[4 * k + 2]['total'], parts[4 * k + 3]['total']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-d4', action='store_true')
    o = ap.parse_args()
    ents = json.load(open(os.path.join(PERF, 'optimizations.json')))
    rep = json.load(open(os.path.join(PERF, 'report.json')))
    fails = 0
    # D1
    bad = []
    for e in ents:
        b, a = e['before'], e['after']
        g = lambda s, c: s['gpu_ms'].get(c + '_eff_0.6')
        want = (a['main'] - b['main'], a['render_front'] + a['render_backend'] - b['render_front'] - b['render_backend'],
                [g(b, 'docked_720p'), g(a, 'docked_720p')], [g(b, 'handheld_720p'), g(a, 'handheld_720p')])
        got = (e['cpu_instr_delta_main'], e['cpu_instr_delta_render'], e['gpu_ms_docked_720p'], e['gpu_ms_handheld_720p'])
        if want != got:
            bad.append('%s: %s != %s' % (e['commit'][:8], got, want))
    fails += bool(bad)
    print('D1: %s - %d entries, deltas equal their snapshots%s' % ('DEFECT' if bad else 'ok', len(ents), ('; ' + '; '.join(bad)) if bad else ''))
    # D2
    bad = []
    for p, e in zip(ents, ents[1:]):
        if e['before_commit'] == p['commit'] and e['before'] != p['after']:
            bad.append('%s starts from %s but its before snapshot differs (main %d vs %d)' % (e['commit'][:8], p['commit'][:8], e['before']['main'], p['after']['main']))
    fails += bool(bad)
    print('D2: %s - chained entries start from the previous after snapshot%s' % ('DEFECT' if bad else 'ok', ('; ' + '; '.join(bad)) if bad else ''))
    # D3
    missing = [c[:8] for e in ents for c in (e['commit'], e['before_commit']) if git('cat-file', '-e', c + '^{commit}').returncode]
    missing = sorted(set(missing), key=missing.index)
    fails += bool(missing)
    print('D3: %s - %d of %d recorded commits are not in this repository: %s' % ('DEFECT' if missing else 'ok', len(missing),
          len({c for e in ents for c in (e['commit'], e['before_commit'])}), ' '.join(missing)))
    # D4
    if not o.skip_d4:
        work = tempfile.mkdtemp(prefix='v1-optd-')   # raises when it cannot
        try:
            sys.path.insert(0, PERF)
            import gen_stream
            streams = os.path.join(work, 'streams')
            subprocess.run([sys.executable, '-I', os.path.join(PERF, 'gen_stream.py'), streams], check=True, capture_output=True)
            cg = os.path.join(work, 'cg')
            os.makedirs(cg)
            for commit, mb, ma in REDERIVE:
                parent = git('rev-parse', commit + '^').stdout.strip()
                res = {}
                for c in (parent, commit):
                    exe = build_and_measure(c[:12], work)
                    rm, rf = callgrind(exe, os.path.join(streams, 'mix_hi.nxd3d'), False, cg, gen_stream.FRAMES)
                    dm, df = callgrind(exe, os.path.join(streams, 'mix_hi.nxd3d'), True, cg, gen_stream.FRAMES)
                    res[c] = (rm - dm, rf - df, rm)
                (pb, fb, rpb), (pa, fa, rpa) = res[parent], res[commit]
                want = ma - mb
                ok = (pa - pb) < 0 and abs((pa - pb) - want) <= 0.1 * abs(want)
                fails += not ok
                print('D4 %s: %s - x86-64 callgrind main (real - dry) %.2f M -> %.2f M = %+.2f M; message %.2f -> %.2f = %+.2f M '
                      '(real without the dry subtraction %.2f -> %.2f); render front %.2f -> %.2f M'
                      % (commit, 'ok' if ok else 'DEFECT', pb / 1e6, pa / 1e6, (pa - pb) / 1e6, mb / 1e6, ma / 1e6, want / 1e6,
                         rpb / 1e6, rpa / 1e6, fb / 1e6, fa / 1e6))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    # D5
    model = [e for e in ents if e['cpu_instr_delta_main'] == 0 and e['cpu_instr_delta_render'] == 0 and e['gpu_ms_handheld_720p'][0] != e['gpu_ms_handheld_720p'][1]]
    doc = open(DOC).read()
    gm = rep.get('gpu_model') or rep.get('gpu') or {}
    labelled = bool(re.search(r'model', doc, re.I))
    print('D5: %s - %d entries change only the GPU model (%s); RENDER_PERF.md names them a model: %s' %
          ('ok' if labelled else 'DEFECT', len(model), ', '.join('%s %s' % (e['commit'][:8], e['gpu_ms_handheld_720p']) for e in model), labelled))
    fails += not labelled
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
