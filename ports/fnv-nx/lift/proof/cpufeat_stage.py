#!/usr/bin/env python3
"""cpufeat_stage: the recipe stage that runs sdmfeat.py over the pinned documents (lift/cpufeat/build.json).  sdmfeat reads its sources by NAME beside each other (SDM.txt next to SDM.pdf, VOL.txt next to VOL.pdf) and
imports the `proof` package; a recipe stage mounts each declared input at /in/N.  This one function lays the mounts out under a scratch directory (symbolic links, no copy, no content of its own) and runs the sdmfeat command:

    python3 -I cpufeat_stage.py --work DIR --src proof/NAME.py=PATH [--src ...] --sdm-pdf P --sdm-txt P --apm PDF TXT [--apm PDF TXT ...] features --out OUT

The command is sdmfeat's own (`features SDM.txt OUT --apm VOL.pdf ...`); nothing is computed here."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import argparse
import os
import sys


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', required=True); ap.add_argument('--src', action='append', default=[], metavar='DEST=PATH')
    ap.add_argument('--sdm-pdf', required=True); ap.add_argument('--sdm-txt', required=True)
    ap.add_argument('--apm', nargs=2, action='append', required=True, metavar=('PDF', 'TXT'))
    ap.add_argument('command', choices=['features']); ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    for s in a.src:
        dest, _, path = s.partition('=')
        os.makedirs(os.path.dirname(os.path.join(a.work, dest)), exist_ok=True); os.symlink(path, os.path.join(a.work, dest))
    docs = os.path.join(a.work, 'docs'); os.makedirs(docs)
    names = [('sdm.pdf', a.sdm_pdf), ('sdm.txt', a.sdm_txt)]
    apm = []
    for i, (pdf, txt) in enumerate(a.apm):
        names += [('apm%d.pdf' % i, pdf), ('apm%d.txt' % i, txt)]; apm += ['--apm', os.path.join(docs, 'apm%d.pdf' % i)]
    for n, p in names: os.symlink(p, os.path.join(docs, n))
    sys.path.insert(0, a.work)
    from fnvnx.lift.proof import sdmfeat
    return sdmfeat.main([a.command, os.path.join(docs, 'sdm.txt'), a.out] + apm)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
