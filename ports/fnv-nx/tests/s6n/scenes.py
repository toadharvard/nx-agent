#!/usr/bin/env python3
"""N0 scenes: run them and compare with the golden images (stream S6n).

    tests/s6n/scenes.py --bin <run_gl> --out <dir> [--update] [--tol N] [scene ...]
    tests/s6n/scenes.py --compare <dir with .rgba files> [--tol N]   (e.g. readbacks copied from the Switch SD card)

The runner writes <out>/<scene>.rgba ("NXRGBA w h\\n" + RGBA8, top row first) and evaluates the scenes' own
conformance checks (exit code). This script compares each image with tests/s6n/golden/<scene>.png: exact for the GL
backend (--tol 0, default), --tol N allows a per-channel difference of N (deko3d vs the GL goldens: see
docs/RENDER_NATIVE.md). --update rewrites the goldens from the run.
"""
import argparse, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")


def read_rgba(path):
    data = open(path, "rb").read()
    nl = data.index(b"\n")
    magic, w, h = data[:nl].split()
    assert magic == b"NXRGBA"
    return int(w), int(h), data[nl + 1:]


def compare(out, names, tol, update):
    from PIL import Image
    import numpy as np
    bad = 0
    for name in names:
        p = os.path.join(out, name + ".rgba")
        if not os.path.exists(p):
            print("%-10s missing readback" % name)
            bad += 1
            continue
        w, h, px = read_rgba(p)
        img = np.frombuffer(px, dtype=np.uint8).reshape(h, w, 4)
        g = os.path.join(GOLDEN, name + ".png")
        if update:
            os.makedirs(GOLDEN, exist_ok=True)
            Image.fromarray(img, "RGBA").save(g, optimize=True)
            print("%-10s golden written" % name)
            continue
        if not os.path.exists(g):
            print("%-10s no golden" % name)
            bad += 1
            continue
        ref = np.asarray(Image.open(g).convert("RGBA"))
        if ref.shape != img.shape:
            print("%-10s size %s vs golden %s" % (name, img.shape, ref.shape))
            bad += 1
            continue
        d = np.abs(ref.astype(int) - img.astype(int))
        over = int((d.max(axis=2) > tol).sum())
        print("%-10s max diff %d, %d pixels over tol %d -> %s" % (name, int(d.max()), over, tol,
                                                                  "ok" if over == 0 else "DIFF"))
        if over:
            bad += 1
            Image.fromarray((np.minimum(d[:, :, :3] * 16, 255)).astype(np.uint8), "RGB").save(
                os.path.join(out, name + ".diff.png"))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin")
    ap.add_argument("--out")
    ap.add_argument("--compare")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--tol", type=int, default=0)
    ap.add_argument("scenes", nargs="*")
    a = ap.parse_args()
    rc = 0
    if a.compare:
        out = a.compare
        names = a.scenes or sorted(f[:-5] for f in os.listdir(out) if f.endswith(".rgba"))
    else:
        if not a.bin or not a.out:
            ap.error("--bin and --out, or --compare")
        os.makedirs(a.out, exist_ok=True)
        r = subprocess.run([a.bin, a.out] + a.scenes)
        if r.returncode:
            print("conformance checks FAILED (exit %d)" % r.returncode)
            rc = 1
        out = a.out
        names = a.scenes or sorted(f[:-5] for f in os.listdir(out) if f.endswith(".rgba"))
    bad = compare(out, names, a.tol, a.update)
    if bad:
        print("golden comparison: %d scene(s) differ" % bad)
        rc = 1
    elif not a.update:
        print("golden comparison: %d scenes match" % len(names))
    return rc


if __name__ == "__main__":
    sys.exit(main())
