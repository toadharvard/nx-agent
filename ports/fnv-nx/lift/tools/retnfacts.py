#!/usr/bin/env python3
"""Facts stage: the callee-popped byte count of every entry (RETN), the `jmp [iat]` thunks (THUNK) and the stack-allocating entries (ALLOCA) of an image.
    retnfacts.py --exe EXE --universe UNIVERSE.tsv --out retn.tsv [--jobs N]
A pure function of the image bytes and the universe rows (x86flow.compute); the text is canonical (x86flow.retn_text), so the output is the same for every --jobs. It is the
declared output of the product root lift/retn (recipe stage, GATE_CACHE), and fpmodes / fpstate take it as a required input (--retn)."""
import argparse
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x86flow as W


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--universe', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()
    W.configure(os.path.dirname(os.path.abspath(a.universe)))
    if os.path.basename(a.universe) != 'universe.tsv':
        sys.exit('--universe: the file is named universe.tsv (x86flow.configure)')
    raw, rows = W.load_universe(a.exe)
    with Pool(a.jobs) as p:
        ret, thunk, alloca = W.compute(p, rows)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, 'w').write(W.retn_text(ret, thunk, alloca))
    print('retnfacts: entries %d, RETN %d, THUNK %d, ALLOCA %d' % (len(rows), len(ret), len(thunk), len(alloca)))


if __name__ == '__main__':
    main()
