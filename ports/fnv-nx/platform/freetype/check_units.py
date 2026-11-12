#!/usr/bin/env python3
"""Class check of the FreeType sources entry of platform/build.json: the units of the patched tree are exactly FreeType's own unit set.

    check_units.py TREE [BUILD_JSON]     TREE = the pinned mirror (fnv-nx-build mirror/freetype-src); default BUILD_JSON = ../build.json

Recomputes, from the pinned tree, the set U that units.py derives (modules.cfg and the rules.mk of its modules, read after the patches), applies the
patches of the `sources` entry of build.json for the external `freetype` in list order (the recipe's own `patch -p1 -F0`), and requires:
  (1) the patches listed in build.json are exactly the `.patch` files of platform/freetype/patches, in file-name order (none unlisted, none missing);
  (2) the unit-named files (*.c *.cc *.cpp *.cxx *.s *.S) of the patched tree are exactly U;
  (3) U is units.txt (the committed derivation);
  (4) a deleted file is never one that U needs (units.py stops when a unit of U is missing).
Equal is green (exit 0), any difference is red (exit 1) and lists it. The deletion patch is therefore never a reviewed list: it can not drift from modules.cfg."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_delpatch
import units


def main(a):
    tree = a[0]
    bj = a[1] if len(a) > 1 else os.path.join(HERE, "..", "build.json")
    rec = json.load(open(bj))
    ent = [s for s in rec.get("sources", []) if s["external"] == "freetype"]
    if len(ent) != 1:
        print("check_units: build.json has %d sources entries for freetype" % len(ent))
        return 1
    root = os.path.dirname(os.path.abspath(bj))
    listed = ent[0]["patches"]
    on_disk = sorted("patches/" + p for p in os.listdir(os.path.join(HERE, "patches")) if p.endswith(".patch"))
    red = []
    if [p.split("freetype/", 1)[-1] for p in listed] != on_disk:
        red.append("patches of build.json %s differ from the files of platform/freetype/patches %s" % (listed, on_disk))
    with tempfile.TemporaryDirectory() as tmp:
        work = units.patched(tree, [os.path.join(root, p) for p in listed], tmp)
        want = units.derive_tree(work)
        have = gen_delpatch.unit_files(work)
    for p in sorted(set(have) - set(want)):
        red.append("built but not in the FreeType unit set: " + p)
    for p in sorted(set(want) - set(have)):
        red.append("in the FreeType unit set but deleted: " + p)
    if want != open(os.path.join(HERE, "units.txt")).read().split():
        red.append("units.txt differs from the derived set")
    print("\n".join(red) if red else "freetype units: %d, patched tree == derived set" % len(want))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
