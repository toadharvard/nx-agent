#!/usr/bin/env python3
"""metrics.py - per-frame image metrics of S16-compare: PSNR, SSIM, and NVIDIA FLIP when its package is installed.

    metrics.py <reference.png> <candidate.png> [--diff out.png] [--json]

* PSNR over RGB, 8-bit peak 255; identical images give inf (reported as null in JSON with "identical": true).
* SSIM: Wang, Bovik, Sheikh, Simoncelli 2004, on BT.601 luma, 11x11 Gaussian window with sigma 1.5, K1 0.01, K2 0.03,
  L 255, "valid" region only (no padding), mean over the map. Matches skimage's structural_similarity with
  gaussian_weights=True, use_sample_covariance=False, data_range=255 on the same luma.
* FLIP: LDR-FLIP mean error from the `flip_evaluator` package (pip install flip-evaluator), at 67 pixels per degree
  (a 0.7 m viewing distance of a 24" 1080p monitor, FLIP's default). None when the package is missing.
* Also: max and mean absolute channel difference, and the share of pixels with any channel off by more than 8.

Images of different sizes are not comparable: compare() raises ValueError.
"""
import argparse, json, math, sys
import numpy as np

def load(path):
    from PIL import Image
    im = Image.open(path)
    if im.mode != 'RGB': im = im.convert('RGB')
    return np.asarray(im, dtype=np.uint8)

def psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return math.inf if mse == 0 else 10.0 * math.log10(255.0 ** 2 / mse)

def luma(a):
    a = a.astype(np.float64)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]

def _gauss(n=11, sigma=1.5):
    x = np.arange(n) - (n - 1) / 2.0
    g = np.exp(-x * x / (2 * sigma * sigma))
    return g / g.sum()

def _filt(x, g):
    """separable 'valid' correlation with the 1-D kernel g along both axes"""
    n = len(g)
    # rows
    y = sum(g[i] * x[:, i:x.shape[1] - n + 1 + i] for i in range(n))
    return sum(g[i] * y[i:y.shape[0] - n + 1 + i, :] for i in range(n))

def ssim(a, b, L=255.0, K1=0.01, K2=0.03):
    x, y = (luma(a), luma(b)) if a.ndim == 3 else (a.astype(np.float64), b.astype(np.float64))
    if min(x.shape) < 11: raise ValueError('image smaller than the 11x11 SSIM window')
    g = _gauss()
    mx, my = _filt(x, g), _filt(y, g)
    sxx = _filt(x * x, g) - mx * mx
    syy = _filt(y * y, g) - my * my
    sxy = _filt(x * y, g) - mx * my
    C1, C2 = (K1 * L) ** 2, (K2 * L) ** 2
    m = ((2 * mx * my + C1) * (2 * sxy + C2)) / ((mx * mx + my * my + C1) * (sxx + syy + C2))
    return float(m.mean())

def flip(a, b):
    try:
        import flip_evaluator as fe
    except Exception:
        return None
    r = fe.evaluate(a.astype(np.float32) / 255.0, b.astype(np.float32) / 255.0, 'LDR')
    return float(r[1])

def compare(a, b):
    if a.shape != b.shape: raise ValueError(f'size differs: {a.shape} vs {b.shape}')
    d = np.abs(a.astype(np.int16) - b.astype(np.int16))
    p = psnr(a, b)
    return {'size': [int(a.shape[1]), int(a.shape[0])], 'identical': bool(d.max() == 0),
            'psnr': None if math.isinf(p) else round(p, 4), 'ssim': round(ssim(a, b), 6), 'flip': flip(a, b),
            'max_abs': int(d.max()), 'mean_abs': round(float(d.mean()), 5),
            'frac_over_8': round(float((d.max(axis=2) > 8).mean()), 6)}

def diff_image(a, b, path, gain=4):
    """|a-b| per channel times gain, as an RGB PNG (black = equal)"""
    from PIL import Image
    d = np.clip(np.abs(a.astype(np.int16) - b.astype(np.int16)) * gain, 0, 255).astype(np.uint8)
    Image.fromarray(d, 'RGB').save(path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ref'); ap.add_argument('cand'); ap.add_argument('--diff'); ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    r, c = load(a.ref), load(a.cand)
    m = compare(r, c)
    if a.diff: diff_image(r, c, a.diff)
    print(json.dumps(m) if a.json else '  '.join(f'{k}={v}' for k, v in m.items()))

if __name__ == '__main__':
    sys.exit(main())
