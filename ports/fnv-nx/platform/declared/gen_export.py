#!/usr/bin/env python3
"""gen_export - a tracked file of this root becomes a declared output of the root, byte for byte.

    gen_export.py SRC DST

Writes a byte-identical copy of SRC to DST and creates DST's parent directory. It is the one way a root publishes a tracked file to the other roots:
the root's `outputs` (verify/RECIPE.md, "Outputs and uses") name only a path in `generated[].outputs`, never a tracked file, so the file passes through
this stage, and the consumer takes it with `uses`. The tool lives in the publishing root because the tool's root is part of the stage key.
Python 3 standard library only."""
import os
import sys


def main():
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
