#!/usr/bin/env python3
"""nxd9trim.py - keep the last K complete frames of a d9cap journal (S5-data's rule, rewritten here).

    nxd9trim.py <journal.nxd9> <out.nxd9> [--frames 3]

Every record is kept except, before the first kept frame, the pixel records (DRAW, DRWI, CLR, STRC, CFIL) and the
PRES records, so the output's frames are 0..K-1 and carry every resource and state of the frames before them. An
incomplete last frame (records after the final PRES, e.g. a journal cut at its size cap) is dropped. Cut by frames,
never by time: in-cell frame rates under llvmpipe are far below the menu's. Exit 1 when the journal has fewer than
K complete frames.
"""
import argparse, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nxd9io

PIXEL = {'DRAW', 'DRWI', 'CLR ', 'STRC', 'CFIL'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('dst'); ap.add_argument('--frames', type=int, default=3)
    a = ap.parse_args()
    total = sum(1 for t, _ in nxd9io.records(a.src) if t == 'PRES')
    if total < a.frames:
        print(f'nxd9trim: {a.src} has {total} complete frames, fewer than {a.frames}', file=sys.stderr)
        return 1
    first = total - a.frames
    w, f, kept, dropped = nxd9io.Writer(a.dst), 0, 0, 0
    for t, p in nxd9io.records(a.src):
        if f >= total:
            break                       # after the last complete frame
        if f < first and (t in PIXEL or t == 'PRES'):
            if t == 'PRES':
                f += 1
            dropped += 1
            continue
        w.rec(t, p)
        kept += 1
        if t == 'PRES':
            f += 1
    w.close()
    print(f'nxd9trim: frames {first}..{total - 1} of {total} kept as 0..{a.frames - 1}; {kept} records kept, {dropped} dropped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
