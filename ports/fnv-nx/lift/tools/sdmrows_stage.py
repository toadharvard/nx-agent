#!/usr/bin/env python3
"""sdmrows_stage: the liftsrc generator stage of the lifter's `sdmrows` input.

    sdmrows_stage.py --sdm SDM.txt --out OUTDIR      -> OUTDIR/sdmrows/sdmrows.json

SDM.txt: `pdftotext -layout` of the pinned Intel SDM (the recipe external `sdm`; the same text L3b's proof/sdmfeat.py partition reads). The output is proof/sdmfeat.py `dumprows`: every opcode-table row, the SDM's flag
table and the NOP flags; the sha256 of the text is in it.  The lifter's CPUID gate (fnvlift/f_cpufeat.py) reads only this file."""
import argparse
import os
import sys

LIFT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, LIFT)


def main():
    ap = argparse.ArgumentParser(); A = ap.add_argument
    A('--sdm', required=True); A('--out', required=True)
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, 'sdmrows'), exist_ok=True)
    from proof import sdmfeat
    return sdmfeat.cmd_dumprows(a.sdm, os.path.join(a.out, 'sdmrows', 'sdmrows.json'))


if __name__ == '__main__':
    sys.exit(main())
