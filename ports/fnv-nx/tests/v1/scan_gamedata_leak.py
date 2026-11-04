#!/usr/bin/env python3
"""scan_gamedata_leak.py - V1 (D14) against J-0128: does a published tree carry bytes of the player's exe?

    python3 -I tests/v1/scan_gamedata_leak.py <FalloutNV.exe> <dir> [--window 64] [--json out.json]

Indexes every window-aligned, non-trivial window of the exe (at least 16 distinct byte values, so zero runs,
padding and int3 fill are not counted) and slides over every file under <dir> at every offset. Prints, per file,
the number of exe windows found and the exe ranges they come from (offsets and counts only, never bytes).
Exit 1 when any file carries an exe window. A file the same size as the exe is reported as well.
"""
import argparse
import json
import os
import sys


def index(exe, w):
    idx = {}
    for o in range(0, len(exe) - w + 1, w):
        k = exe[o:o + w]
        if len(set(k)) >= 16:
            idx.setdefault(k, o)
    return idx


def scan(data, idx, w):
    hits = []
    i, n = 0, len(data) - w + 1
    while i < n:
        o = idx.get(data[i:i + w])
        if o is not None:
            hits.append((i, o))
            i += w
        else:
            i += 1
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exe')
    ap.add_argument('dir')
    ap.add_argument('--window', type=int, default=64)
    ap.add_argument('--json')
    a = ap.parse_args()
    with open(a.exe, 'rb') as f:
        exe = f.read()
    idx = index(exe, a.window)
    found = {}
    nfiles = 0
    for dp, dn, fn in os.walk(a.dir):
        dn[:] = [d for d in dn if d != '.git']
        for name in sorted(fn):
            p = os.path.join(dp, name)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            nfiles += 1
            with open(p, 'rb') as f:
                data = f.read()
            hits = scan(data, idx, a.window)
            rel = os.path.relpath(p, a.dir)
            if hits or len(data) == len(exe):
                lo, hi = (min(o for _, o in hits), max(o for _, o in hits)) if hits else (0, 0)
                found[rel] = {'windows': len(hits), 'bytes': len(hits) * a.window, 'exe_lo': lo, 'exe_hi': hi,
                              'same_size': len(data) == len(exe)}
    print('scanned %d files, %d exe windows indexed (%d bytes each)' % (nfiles, len(idx), a.window))
    for rel, v in sorted(found.items(), key=lambda kv: -kv[1]['windows']):
        print('  %-50s %7d windows (%d bytes) from exe 0x%x..0x%x%s' % (
            rel, v['windows'], v['bytes'], v['exe_lo'], v['exe_hi'], ' SAME SIZE AS THE EXE' if v['same_size'] else ''))
    if a.json:
        with open(a.json, 'w') as f:
            json.dump(found, f, indent=1, sort_keys=True)
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
