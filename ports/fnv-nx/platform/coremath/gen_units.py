#!/usr/bin/env python3
"""gen_units - the unit list of the CORE-MATH root (recipe `generated` stage of ports/fnv-nx/platform/coremath).

    gen_units.py EXT OUT FUNCTION...   EXT = the pinned CORE-MATH external (/ext/core-math); OUT = the stage's /out (writes OUT/units.list)

One `core-math:<path>` line per function: the binary64 source src/binary64/<f>/<f>.c of the external, the form the recipe's generated `sources` takes. The function set is the
argument list, which build.json holds once. Red: a function whose source is no file of the external."""
import os
import sys


def main(argv):
    if len(argv) < 3:
        sys.exit("usage: gen_units.py EXT OUT FUNCTION...")
    ext, out, funcs = argv[0], argv[1], argv[2:]
    lines = []
    for f in funcs:
        rel = "src/binary64/%s/%s.c" % (f, f)
        if not os.path.isfile(os.path.join(ext, rel)):
            sys.exit("gen_units: %s is no file of the external" % rel)
        lines.append("core-math:%s" % rel)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "units.list"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
