#!/usr/bin/env python3
"""sdmrows_stage: the liftsrc generator stage of the lifter's `sdmrows` input.

    sdmrows_stage.py --sdm SDM.txt --out OUTDIR --lift LIFTDIR      -> OUTDIR/sdmrows/sdmrows.json

LIFTDIR: the lift/ directory (its proof/ package) as the stage mounts it, a declared input; always an argument, never derived from this file's place (the tool is mounted apart from the tree).

SDM.txt: `pdftotext -layout` of the pinned Intel SDM (the recipe external `sdm`; the text that proof/sdmfeat.py dumprows reads). The output is proof/sdmfeat.py `dumprows`: every opcode-table row, the SDM's flag
table and the NOP flags; the sha256 of the text is in it.  The lifter's CPUID gate (proof/cpufeat.py) reads only this file."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--sdm', required=True); A('--out', required=True); A('--lift', required=True)
    a = ap.parse_args()
    sys.path.insert(0, a.lift)
    os.makedirs(os.path.join(a.out, 'sdmrows'), exist_ok=True)
    from fnvnx.lift.proof import sdmfeat
    return sdmfeat.cmd_dumprows(a.sdm, os.path.join(a.out, 'sdmrows', 'sdmrows.json'))


if __name__ == '__main__':
    sys.exit(main())
