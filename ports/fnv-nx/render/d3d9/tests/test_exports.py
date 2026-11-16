#!/usr/bin/env python3
"""test_exports.py OBJECTS_DIR: the `.fnvnx.export` records of the built objects of the d3d9 root (OBJECTS_DIR, the recipe's `obj/` tree, an argument) name, for the module
"d3d9.dll", exactly the exports that carry a signature in the interface spec (bridgefacts/d3d9/d3d9_exports.py), each once, all of kind func.
The records are the only place the export fact is written (dev/d3d9_exports.cpp): there is no table to compare."""
import collections, os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); PORT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, PORT); sys.path.insert(0, os.path.join(PORT, "bridgefacts", "d3d9"))
import d3d9_exports as EX                      # noqa: E402
from contract import exportgen                 # noqa: E402

spec = sorted(e["name"] for e in EX.exports() if not e.get("undocumented"))
recs = []
for d, _, fs in os.walk(sys.argv[1]):
    for f in sorted(fs):
        if f.endswith(".o"): recs += exportgen.records(os.path.join(d, f))
recs = [r for r in recs if r[0] == "d3d9.dll"]
names = collections.Counter(r[1] for r in recs)
assert sorted(names) == spec, ("records %s spec %s" % (sorted(names), spec))
assert all(c == 1 for c in names.values()), names
assert all(r[3] == "func" for r in recs)
print("test_exports: %d d3d9.dll records == %d spec exports, each once" % (len(recs), len(spec)))
