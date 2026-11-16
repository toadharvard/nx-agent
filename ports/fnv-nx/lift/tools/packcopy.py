#!/usr/bin/env python3
"""The `generated` stage of the product root lift/pack (build.json): copy the units of the published pack tree into the root's view.
    packcopy.py PACK OUT        PACK/u/000.src .. PACK/u/fff.src -> OUT/u/000.cpp .. OUT/u/fff.cpp
The names are fixed (tools/liftgen.py unit_names); a file the pack lacks makes the stage fail, an extra one is not copied. An external with an inc/ directory is rejected: the runtime's headers are generated, not carried."""
import os as _q_os, sys as _q_sys
_q_sys.path.insert(0, _q_os.path.join(_q_os.path.dirname(_q_os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): qualified imports of another root's modules (fnvnx.contract.X, fnvnx.verify.X)
import os
import shutil
import sys


def names():
    return ['u/%03x.cpp' % i for i in range(4096)]


def main(pack, out):
    if os.path.exists(os.path.join(pack, 'inc')):
        sys.exit('packcopy: %s/inc exists: the pack external holds no header of the runtime (inc/fnvl_prelude.h and inc/imp.h are the pack\'s own generated stage, packstage.py inc, from the runtime\'s current headers)' % pack)
    os.makedirs(os.path.join(out, 'u'), exist_ok=True)
    for n in names():
        shutil.copyfile(os.path.join(pack, n[:-4] + '.src'), os.path.join(out, n))


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fnvnx.lift.tools.cli as cli
    (a, b), _ = cli.parse('usage: packcopy.py PACK OUT\n  copies the lifted pack PACK to OUT', sys.argv[1:], (2, 2))
    main(a, b)
