#!/usr/bin/env python3
"""The stage of the gate "d3d9exports" (render/total/d3d9exports/build.json, run step `gate`): the exports of our d3d9.dll are exactly the exports of the interface spec.
    python3 -I -B stage.py --spec D9_SPEC.json --exports exports.tsv --out OUT.json
Universe = every export of the spec (bridgefacts/d3d9/d3d9_spec.py, the `exports` table of the D9_SPEC output) that carries a signature (not `undocumented`).
Rule: exports.tsv (contract/exports, the one reader of the `.fnvnx.export` records of the built objects of every guest root) holds, for the module d3d9.dll, one row
per universe name and no other row. An export of the spec with no row, a row with no export of the spec, or a name twice is uncertified."""
import argparse, json, sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--exports", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.spec) as f:
        spec = sorted(e["name"] for e in json.load(f)["exports"] if not e.get("undocumented"))
    rows = []
    with open(a.exports) as f:
        for ln in f.read().split("\n"):
            c = ln.split("\t")
            if len(c) > 1 and c[0] == "d3d9.dll":
                rows.append(c[1])
    have = sorted(set(rows))
    bad = sorted(["missing:" + n for n in spec if n not in have] + ["extra:" + n for n in have if n not in spec]
                 + ["twice:" + n for n in have if rows.count(n) > 1])
    res = {"format": "fnvnx-gate/1", "name": "d3d9exports", "status": "red" if bad else "green", "universe": len(spec),
           "certified": len(spec) - len([b for b in bad if b.startswith(("missing:", "twice:"))]), "uncertified": bad,
           "detail": "%d d3d9.dll records, %d spec exports" % (len(rows), len(spec))}
    with open(a.out, "w") as f:
        f.write(json.dumps(res, sort_keys=True, indent=1) + "\n")
    return 0


sys.exit(main())
