#!/usr/bin/env python3
"""s23_units - the unit list of the S23 layout proof (recipe `generated` stage of verify/roots/s23-layout).

    s23_units.py SELECTED TREE OUT [--limit N]
        SELECTED = selected.tsv of the S23 selection (external `s23`: fnv-nx-build l2/s23-codeview, toolchain/msvcmodel/datamodel/s23_codeview.py),
        TREE     = the prepared decomp tree (external `decomp`: fnv-nx-build t/8f324ec, subdirectory tree/),
        OUT      = the stage's /out.  --limit N (exploration builds only) keeps the first N units in name order.

RULE of the unit set: the selection's column `first_object` names, for every selected class variant, the cl /Z7 object that holds it; the units are the
distinct such objects.  An object is `<source path with / replaced by _>.obj`; its source is the one file of TREE whose path gives that name (every `.c`,
`.cc`, `.cpp` file of TREE is a candidate; no two give one name, which is checked).  The tool is red when an object has no source or two.
Writes OUT/units/<object name without .obj>.cpp, one wrapper per unit: `#include "fnvnx_compat.h"` (the shim prelude the Switch build force-includes into every decomp
unit; the recipe has no force-include key) and `#include "<TREE>/<source>"` (quoted includes of the source resolve in the source's own directory), and OUT/pairs.tsv
(`<cl object name><TAB><unit file><TAB><source>` per unit: the pairing of a GCC object with its cl object for gate_r7)."""
import os
import sys


def main(argv):
    args = [a for a in argv if not a.startswith("--limit")]
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    if "--limit" in argv:
        args.remove(argv[argv.index("--limit") + 1])
    selected, tree, out = args
    first = set()
    with open(selected, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        col = head.index("first_object")
        for line in f:
            first.add(line.rstrip("\n").split("\t")[col])
    by_name = {}
    for root, dirs, files in os.walk(tree):
        dirs.sort()
        for n in sorted(files):
            if n.endswith((".c", ".cc", ".cpp")):
                rel = os.path.relpath(os.path.join(root, n), tree).replace(os.sep, "/")
                by_name.setdefault(rel.replace("/", "_") + ".obj", []).append(rel)
    bad = sorted(o for o in first if len(by_name.get(o, [])) != 1)
    if bad:
        print("s23_units: %d objects without exactly one source in the tree: %s" % (len(bad), bad[:5]), file=sys.stderr)
        return 1
    names = sorted(first)[:limit]
    os.makedirs(os.path.join(out, "units"), exist_ok=True)
    for o in names:   # a unit is the decomp source behind the platform's force-included prelude (the Switch build's `-include fnvnx_compat.h`)
        with open(os.path.join(out, "units", o[:-4]), "w") as f:
            f.write('#include "fnvnx_compat.h"\n#include "%s/%s"\n' % (tree, by_name[o][0]))
    with open(os.path.join(out, "pairs.tsv"), "w") as f:
        f.write("".join("%s\t%s\t%s\n" % (o, o[:-4], by_name[o][0]) for o in names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
