#!/usr/bin/env python3
# V6n pixel comparison of two read-backs (.v6nd, written by scene.c).
#
#   python3 -I render/verify/cmp.py REF.v6nd TEST.v6nd [--png OUT.png]
#
# Thresholds (docs/RENDER_VERIFY.md §4):
#   colour, 8-bit targets  : |ref - test| <= 2 (of 255) per channel; alpha only when the target has alpha
#   colour, 5/6-bit (R5G6B5): <= 1 step of the channel
#   colour, float targets  : |ref - test| <= 1e-3 * max(1, |ref|)  (relative, absolute near 0); NaN == NaN
#   stencil                : exact
#   depth                  : D24/D16 integer within DEPTH_TOL = 1 LSB (softpipe truncates z * (2^n - 1) to an integer,
#                            llvmpipe rounds; both are inside D3D9's precision, any larger gap is a real error);
#                            D32F within 2 ulp
# Exit 0 = match, 1 = mismatch (summary on stdout), 2 = unreadable/incompatible.
import sys, struct
import numpy as np

COLOR_TOL_8 = 2.0
FLOAT_REL = 1e-3
DEPTH_TOL = 1

def load(path):
    with open(path, 'rb') as f:
        hdr = f.readline().decode().split()
        if hdr[0] != 'V6ND':
            raise ValueError('not a v6nd file')
        w, h, ch, isf, hd, hs, rt, ds = map(int, hdr[1:9])
        c = np.frombuffer(f.read(w * h * 16), np.float32).reshape(h, w, 4)
        d = np.frombuffer(f.read(w * h * 4), np.uint32).reshape(h, w) if hd else None
        s = np.frombuffer(f.read(w * h), np.uint8).reshape(h, w) if hs else None
    return dict(w=w, h=h, ch=ch, isf=isf, rt=rt, ds=ds, c=c, d=d, s=s)

def compare(a, b):
    """returns (ok, list of finding strings, per-pixel bad mask)"""
    out = []
    if (a['w'], a['h'], a['rt'], a['ds']) != (b['w'], b['h'], b['rt'], b['ds']):
        return False, ['incompatible dumps'], None
    ch = min(a['ch'], b['ch'])
    ca, cb = a['c'][..., :ch].astype(np.float64), b['c'][..., :ch].astype(np.float64)
    if a['isf']:
        both_nan = np.isnan(ca) & np.isnan(cb)
        diff = np.abs(ca - cb)
        tol = FLOAT_REL * np.maximum(1.0, np.abs(ca))
        badc = ~both_nan & ~(diff <= tol)
    else:
        tol = 1.0 if a['rt'] == 23 else COLOR_TOL_8
        diff = np.abs(ca - cb)
        badc = diff > tol
    bad = badc.any(axis=2)
    if bad.any():
        ys, xs = np.nonzero(bad)
        y, x = ys[0], xs[0]
        out.append('colour: %d px over tolerance (max |d| %.4g), first (%d,%d) ref %s test %s'
                   % (bad.sum(), np.nanmax(np.where(badc, diff, 0)), x, y, a['c'][y, x, :ch].tolist(), b['c'][y, x, :ch].tolist()))
    if a['d'] is not None and b['d'] is not None:
        if a['ds'] == 82:
            fa, fb = a['d'].view(np.float32).astype(np.float64), b['d'].view(np.float32).astype(np.float64)
            badd = np.abs(fa - fb) > 2 * np.spacing(np.abs(fa).astype(np.float32)).astype(np.float64)
        else:
            badd = np.abs(a['d'].astype(np.int64) - b['d'].astype(np.int64)) > DEPTH_TOL
        if badd.any():
            ys, xs = np.nonzero(badd)
            out.append('depth: %d px differ, first (%d,%d) ref %d test %d' % (badd.sum(), xs[0], ys[0], a['d'][ys[0], xs[0]], b['d'][ys[0], xs[0]]))
        bad = bad | badd
    if a['s'] is not None and b['s'] is not None:
        bads = a['s'] != b['s']
        if bads.any():
            ys, xs = np.nonzero(bads)
            out.append('stencil: %d px differ, first (%d,%d) ref %d test %d' % (bads.sum(), xs[0], ys[0], a['s'][ys[0], xs[0]], b['s'][ys[0], xs[0]]))
        bad = bad | bads
    return not out, out, bad

def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    ok, msgs, bad = compare(a, b)
    if '--png' in sys.argv and bad is not None:
        try:
            from PIL import Image
            p = sys.argv[sys.argv.index('--png') + 1]
            def img(c, isf):
                v = np.clip(c[..., :3] if isf else c[..., :3] / 255.0, 0, 1)
                return (v * 255).astype(np.uint8)
            m = np.zeros((a['h'], a['w'], 3), np.uint8); m[bad] = (255, 0, 255)
            Image.fromarray(np.concatenate([img(a['c'], a['isf']), img(b['c'], b['isf']), m], axis=1)).resize(
                (a['w'] * 3 * 8, a['h'] * 8), Image.NEAREST).save(p)
        except ImportError:
            pass
    print('MATCH' if ok else 'MISMATCH\n  ' + '\n  '.join(msgs))
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
