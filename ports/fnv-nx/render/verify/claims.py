#!/usr/bin/env python3
# V6n analytic checks: S6n claims (and V6n's knowledge of D3D9) where Nine-on-softpipe is NOT the reference,
# because Nine there deviates from D3D9 on purpose or by accident. The target's read-back is checked against a
# value computed from the D3D9 documentation; Nine's value is printed for the record.
#
#   python3 -I render/verify/claims.py [--bin DIR] [--work DIR] [--mutant NAME] [NAME...]
# Checks:
#   c40_depthbias_d24   C40/D1: DEPTHBIAS is in depth-buffer units: z' = z + DEPTHBIAS (D24: +-2 LSB)
#   c40_depthbias_d16   same on D16
#   o1_x8_dst_alpha     D3D9: DESTALPHA on X8R8G8B8 reads 1.0 (softpipe reads the stored X byte)
import os, sys, subprocess, tempfile
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import cmp as C
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))

def quad(z):
    v = ['v %g %g %g 1 0xff808080 0 0 0' % p for p in [(0, 0, z), (16, 0, z), (0, 16, z), (16, 0, z), (16, 16, z), (0, 16, z)]]
    return 'mode rhw\n' + '\n'.join(v) + '\ndraw TRIANGLELIST 2\n'
BASE = 'size 16 16\nrt {rt}\nds {ds}\nclear 7 {clear} 1.0 0\nrs CULLMODE CULL_NONE\n' \
       'tss 0 COLOROP TOP_SELECTARG1\ntss 0 COLORARG1 TA_DIFFUSE\ntss 0 ALPHAOP TOP_SELECTARG1\ntss 0 ALPHAARG1 TA_DIFFUSE\n'
CHECKS = {
    'c40_depthbias_d24': (BASE.format(rt='A8R8G8B8', ds='D24S8', clear='0xff000000') +
                          'rs ZENABLE 1\nrs ZFUNC CMP_ALWAYS\nrs DEPTHBIAS f:0.0625\n' + quad(0.5),
                          lambda d: ('depth', (0.5 + 0.0625) * (2 ** 24 - 1), 2)),
    'c40_depthbias_d16': (BASE.format(rt='A8R8G8B8', ds='D16', clear='0xff000000') +
                          'rs ZENABLE 1\nrs ZFUNC CMP_ALWAYS\nrs DEPTHBIAS f:0.0625\n' + quad(0.5),
                          lambda d: ('depth', (0.5 + 0.0625) * (2 ** 16 - 1), 1)),
    'o1_x8_dst_alpha': (BASE.format(rt='X8R8G8B8', ds='D24S8', clear='0x00ffffff') +
                        'rs ALPHABLENDENABLE 1\nrs SRCBLEND BLEND_DESTALPHA\nrs DESTBLEND BLEND_ZERO\n' + quad(0.5),
                        lambda d: ('colour', 128.0, 2)),
}

def run_one(bindir, work, name, exe, env):
    sc = os.path.join(work, name + '.scene')
    open(sc, 'w').write('# analytic claim check %s (claims.py)\n' % name + CHECKS[name][0])
    od = os.path.join(work, exe)
    os.makedirs(od, exist_ok=True)
    e = dict(os.environ); e.update(env)
    r = subprocess.run([os.path.join(bindir, exe), '-q', '-o', od, sc], capture_output=True, text=True, env=e)
    p = os.path.join(od, name + '.v6nd')
    if r.returncode or not os.path.exists(p):
        return None
    d = C.load(p)
    kind, exp, tol = CHECKS[name][1](d)
    v = float(d['d'][8, 8]) if kind == 'depth' else float(d['c'][8, 8, 0])
    return v, exp, tol

def main():
    a = sys.argv[1:]
    bindir = os.path.join(ROOT, 'build/v6n/bin'); work = os.path.join(ROOT, 'build/v6n/claims'); env = {}
    names = []
    i = 0
    while i < len(a):
        if a[i] == '--bin': bindir = a[i + 1]; i += 2
        elif a[i] == '--work': work = a[i + 1]; i += 2
        elif a[i] == '--mutant': env['V6N_MUTANT'] = a[i + 1]; i += 2
        else: names.append(a[i]); i += 1
    os.makedirs(work, exist_ok=True)
    bad = 0
    for n in names or list(CHECKS):
        t = run_one(bindir, work, n, 'v6n_gl', env)
        o = run_one(bindir, work, n, 'v6n_nine', {})
        if t is None:
            print('%-20s ERROR (target did not run)' % n); bad += 1; continue
        v, exp, tol = t
        ok = abs(v - exp) <= tol
        bad += not ok
        print('%-20s %s  target %.1f expected %.1f (+-%g)  [nine-softpipe %s]' % (n, 'PASS' if ok else 'FAIL', v, exp, tol,
              '%.1f' % o[0] if o else 'n/a'))
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
