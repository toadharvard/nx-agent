#!/usr/bin/env python3
"""Parity of two nx_render output directories (same capture, translated vs native shaders).

    python3 -I parity_png.py <reference dir> <candidate dir> [--draws] [--tol 2]

For every f<frame>.png (and with --draws every f<frame>_d<draw>.png) present in both: the max abs difference over
RGBA in 8-bit steps, and the number of pixels whose difference exceeds --tol (default 2, the 2/255 bound of
RENDER_NATIVE.md §4 point 1). Prints one line per image that differs and a summary line:
    parity: frames N, max M, over-tol pixels P (images over tol K)
"""
import os, re, sys
import numpy as np
from PIL import Image


def load(p):
    return np.asarray(Image.open(p).convert('RGBA'), dtype=np.int16)


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    ref, cand = argv[0], argv[1]
    tol = int(argv[argv.index('--tol') + 1]) if '--tol' in argv else 2
    pat = re.compile(r'^f\d{5}(_d\d{5})?\.png$' if '--draws' in argv else r'^f\d{5}\.png$')
    worst, over, bad, n, frames_max = 0, 0, 0, 0, 0
    for fn in sorted(os.listdir(ref)):
        if not pat.match(fn) or not os.path.exists(os.path.join(cand, fn)):
            continue
        a, b = load(os.path.join(ref, fn)), load(os.path.join(cand, fn))
        n += 1
        if a.shape != b.shape:
            print('  %s: size %s vs %s' % (fn, a.shape, b.shape)); bad += 1; continue
        d = np.abs(a - b).max(axis=2)
        m, o = int(d.max()), int((d > tol).sum())
        worst = max(worst, m)
        if '_d' not in fn:
            frames_max = max(frames_max, m)
        over += o
        bad += o > 0
        if m:
            print('  %s: max %d, over tol %d px' % (fn, m, o))
    print('parity: images %d, max %d (frames %d), over-tol pixels %d (images over tol %d)' % (n, worst, frames_max, over, bad))


if __name__ == '__main__':
    main(sys.argv[1:])
