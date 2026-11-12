#!/usr/bin/env python3
"""The `generated` stage of the product root lift/pack (build.json): copy the units of the published pack tree into the root's view.
    packcopy.py PACK OUT        PACK/u/000.cpp .. PACK/u/fff.cpp, PACK/disp.S, PACK/imp.cpp -> OUT/<same names>
The names are fixed (tools/liftgen.py unit_names); a file the pack lacks makes the stage fail, an extra one is not copied."""
import os
import shutil
import sys


def names():
    return ['u/%03x.cpp' % i for i in range(4096)] + ['disp.S', 'imp.cpp']


def main(pack, out):
    os.makedirs(os.path.join(out, 'u'), exist_ok=True)
    for n in names():
        shutil.copyfile(os.path.join(pack, n), os.path.join(out, n))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
