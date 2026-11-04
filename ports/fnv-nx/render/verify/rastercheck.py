#!/usr/bin/env python3
# V6n coverage check against the exact D3D9 rule (raster_ref.py): N deterministic random triangles per vertex grid
# (integer, half, 1/16, 1/256 px), one triangle per scene (RHW, CULL_NONE, white on black), rendered by each
# executable; coverage = pixels with red > 128.
#   python3 -I render/verify/rastercheck.py [--bin DIR] [--work DIR] [-n N] [--keep] EXE...   (EXE: v6n_nine, v6n_gl, ...)
# Prints per executable and grid: wrong pixels / triangles with any wrong pixel. Exit 1 if any EXE has a wrong
# pixel beyond --allow (default 0).
import os, sys, random, subprocess, collections
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import cmp as C, raster_ref as R
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
GRIDS = (('int', 1), ('half', 2), ('g16', 16), ('g256', 256))

def scenes(work, n, seed=5):
    rnd = random.Random(seed); out = []
    os.makedirs(work, exist_ok=True)
    for g, d in GRIDS:
        for i in range(n):
            pts = [(rnd.randint(0, 32 * d) / d, rnd.randint(0, 32 * d) / d) for _ in range(3)]
            p = os.path.join(work, '%s_%03d.scene' % (g, i))
            open(p, 'w').write('size 32 32\nrt A8R8G8B8\nds NONE\nclear 1 0xff000000 1.0 0\nrs CULLMODE CULL_NONE\n'
                               'tss 0 COLOROP TOP_SELECTARG1\ntss 0 COLORARG1 TA_DIFFUSE\nmode rhw\n' +
                               ''.join('v %r %r 0.5 1 0xffffffff 0 0 0\n' % q for q in pts) + 'draw TRIANGLELIST 1\n'
                               '# tri %r\n' % (pts,))
            out.append((p, g, pts))
    return out

def main():
    a = sys.argv[1:]; bindir = os.path.join(ROOT, 'build/v6n/bin'); work = os.path.join(ROOT, 'build/v6n/raster')
    n = 60; allow = 0; exes = []
    i = 0
    while i < len(a):
        if a[i] == '--bin': bindir = a[i + 1]; i += 2
        elif a[i] == '--work': work = a[i + 1]; i += 2
        elif a[i] == '-n': n = int(a[i + 1]); i += 2
        elif a[i] == '--allow': allow = int(a[i + 1]); i += 2
        else: exes.append(a[i]); i += 1
    sc = scenes(os.path.join(work, 'scenes'), n)
    refs = {p: R.coverage(pts, 32, 32) for p, g, pts in sc}
    bad_exe = 0
    for exe in exes:
        od = os.path.join(work, exe); os.makedirs(od, exist_ok=True)
        subprocess.run([os.path.join(bindir, exe), '-q', '-o', od] + [p for p, _, _ in sc], capture_output=True)
        res = collections.defaultdict(lambda: [0, 0]); worst = None
        for p, g, pts in sc:
            d = C.load(os.path.join(od, os.path.basename(p)[:-6] + '.v6nd'))
            cov = {(x, y) for y in range(32) for x in range(32) if d['c'][y, x, 0] > 128}
            wrong = len(cov ^ refs[p])
            res[g][0] += wrong; res[g][1] += wrong > 0
            if wrong and (worst is None or wrong > worst[0]):
                worst = (wrong, os.path.relpath(p, ROOT), sorted(cov - refs[p])[:3], sorted(refs[p] - cov)[:3])
        total = sum(v[0] for v in res.values())
        print('%-14s %s  total wrong px %d%s' % (exe, '  '.join('%s %d px/%d tri' % (g, res[g][0], res[g][1]) for g, _ in GRIDS),
              total, ('  worst %d px %s extra %s missing %s' % worst) if worst else ''))
        bad_exe += total > allow
    return 1 if bad_exe else 0

if __name__ == '__main__':
    sys.exit(main())
