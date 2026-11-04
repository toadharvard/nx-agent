#!/usr/bin/env python3
# V6n pixel differential runner: runs scenes on the oracle (v6n_nine) and the target (v6n_gl, optionally with a
# trap mutant) and compares the read-backs with cmp.py's thresholds.
#
#   python3 -I render/verify/pixdiff.py [--bin DIR] [--work DIR] [--mutant NAME] [--traps] [-v] SCENE|DIR ...
#     --target gl|s6ngl  the target executable: v6n_gl (V6n's executor of the nx/state.h contract, default) or
#               v6n_s6ngl (S6n's own GL backend render/native/gl/nxgl.c); both use S6n's translator
#     --traps   trap mode: each scene tests/v6n/traps/<m>.scene must MATCH unmutated and MISMATCH with V6N_MUTANT=<m>
# Exit 0 when every expectation holds.
import os, sys, subprocess, glob
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cmp as C
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))

def scenes_of(args):
    out = []
    for a in args:
        out += sorted(glob.glob(os.path.join(a, '*.scene'))) if os.path.isdir(a) else [a]
    return out

def run(exe, scenes, outdir, env=None):
    os.makedirs(outdir, exist_ok=True)
    e = dict(os.environ); e.update(env or {})
    r = subprocess.run([exe, '-q', '-o', outdir] + scenes, capture_output=True, text=True, env=e)
    errs = {}
    for l in r.stderr.splitlines():
        p = l.split(':', 1)[0]
        errs.setdefault(p, l)
    return errs

def dump(outdir, scene):
    b = os.path.basename(scene)
    return os.path.join(outdir, (b[:-6] if b.endswith('.scene') else b) + '.v6nd')

TARGET = 'v6n_gl'

def diff(bindir, work, scenes, mutant=None, tag='gl'):
    nd, gd = os.path.join(work, 'nine'), os.path.join(work, tag)
    for d in (nd, gd):
        for s in scenes:
            p = dump(d, s)
            if os.path.exists(p):
                os.remove(p)
    ne = run(os.path.join(bindir, 'v6n_nine'), scenes, nd)
    ge = run(os.path.join(bindir, TARGET), scenes, gd, {'V6N_MUTANT': mutant} if mutant else None)
    res = {}
    for s in scenes:
        a, b = dump(nd, s), dump(gd, s)
        if not os.path.exists(a):
            res[s] = ('ERROR', ['oracle: ' + ne.get(s, 'no output')]); continue
        if not os.path.exists(b):
            res[s] = ('ERROR', ['target: ' + ge.get(s, 'no output')]); continue
        ok, msgs, _ = C.compare(C.load(a), C.load(b))
        res[s] = ('MATCH' if ok else 'MISMATCH', msgs)
    return res

def main():
    a = sys.argv[1:]
    bindir = os.path.join(ROOT, 'build/v6n/bin'); work = os.path.join(ROOT, 'build/v6n/pix'); mutant = None
    traps = verbose = False; rest = []
    i = 0
    while i < len(a):
        if a[i] == '--bin': bindir = a[i + 1]; i += 2
        elif a[i] == '--work': work = a[i + 1]; i += 2
        elif a[i] == '--mutant': mutant = a[i + 1]; i += 2
        elif a[i] == '--traps': traps = True; i += 1
        elif a[i] == '--target':
            global TARGET
            TARGET = 'v6n_' + a[i + 1]; i += 2
        elif a[i] == '-v': verbose = True; i += 1
        else: rest.append(a[i]); i += 1
    scenes = scenes_of(rest)
    if not traps:
        res = diff(bindir, work, scenes, mutant)
        bad = 0
        for s, (v, msgs) in res.items():
            if v != 'MATCH' or verbose:
                print('%-9s %s%s' % (v, os.path.relpath(s, ROOT), ''.join('\n          ' + m for m in msgs)))
            bad += v != 'MATCH'
        print('pixdiff: %d scenes, %d match, %d differ/error%s' % (len(res), len(res) - bad, bad, ' (mutant %s)' % mutant if mutant else ''))
        return 1 if bad else 0
    base = diff(bindir, work, scenes)
    killed = 0; bad = 0
    for s in scenes:
        m = os.path.basename(s)[:-6]
        v0 = base[s][0]
        v1, msgs = diff(bindir, work, [s], m, tag='mut')[s]
        ok = v0 == 'MATCH' and v1 == 'MISMATCH'
        killed += v1 == 'MISMATCH'; bad += not ok
        print('%-24s unmutated %-8s mutant %-8s %s %s' % (m, v0, v1, 'OK ' if ok else 'BAD', (msgs[0] if msgs else '')[:90]))
        if v0 != 'MATCH' and verbose:
            print('            ' + '\n            '.join(base[s][1]))
    print('traps: %d scenes, %d mutants killed, %d expectations failed' % (len(scenes), killed, bad))
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
