#!/usr/bin/env python3
"""Facts stage: the callee-popped byte count of every entry (RETN), the `jmp [iat]` thunks (THUNK) and the stack-allocating entries (ALLOCA) of an image.
    retnfacts.py --exe EXE --universe UNIVERSE.tsv --table MODULES_TSV --image NAME --out retn.tsv [--jobs N]
A pure function of the image bytes and the universe rows (x86flow.compute); the text is canonical (x86flow.retn_text), so the output is the same for every --jobs. It is the
declared output of the product root lift/retn (recipe stage, GATE_CACHE), and fpmodes / fpstate take it as a required input (--retn)."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import argparse
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fnvnx.lift.tools.libarg as libarg; libarg.take()      # --lib DIR: the package fnvlift (a declared input of the stage)
import fnvnx.lift.tools.x86flow as W


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True)
    ap.add_argument('--universe', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--table', required=True, help='the loader module table (modules.tsv)')
    ap.add_argument('--image', required=True, help='exe, or a row of the module table')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    a = ap.parse_args()
    W.configure(os.path.dirname(os.path.abspath(a.universe)))
    if os.path.basename(a.universe) != 'universe.tsv':
        sys.exit('--universe: the file is named universe.tsv (x86flow.configure)')
    raw, rows = W.load_universe(a.exe, W.image_base(a.exe, a.table, a.image))
    with Pool(a.jobs) as p:
        ret, thunk, alloca = W.compute(p, rows)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, 'w').write(W.retn_text(ret, thunk, alloca))
    print('retnfacts: entries %d, RETN %d, THUNK %d, ALLOCA %d' % (len(rows), len(ret), len(thunk), len(alloca)))


if __name__ == '__main__':
    main()
