#!/usr/bin/env python3
"""export_kinds - the sympair kind table of the names plugin as a declared output (the recipe stage of toolchain/names).

    export_kinds.py --inc <sympair_kinds.inc> --out <file>

The table is defined once, in toolchain/names/sympair_kinds.inc (the plugin includes it); this stage publishes that file byte for byte, so a consumer
(contract/facts, which reads the L2 sections of objects) takes it through `uses` and holds no copy of the kinds. Exit 1 when the file defines no kind."""
import argparse
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inc", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = open(a.inc, "rb").read()
    if not re.search(rb"X\(([^,()\s]+),", data):
        print("export_kinds: %s defines no kind" % a.inc, file=sys.stderr)
        return 1
    with open(a.out, "wb") as f:
        f.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
