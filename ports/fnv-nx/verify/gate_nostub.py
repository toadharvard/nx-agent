#!/usr/bin/env python3
"""Gate "nostub" (fnvnx-gate/1): every file below the DIRs has zero findings under the nostub lint (nostub.py). Absolute rule.
    python3 -I gate_nostub.py [--terminator NAME]... DIR...      run in the checkout root, env GATE_OUT (result directory)
Universe: the files nostub checks below the existing DIRs (a missing DIR contributes nothing). Certified: files with zero
findings (an unlexable file has the finding `unlexable`). No base tree, no allow-lists. uncertified: the first 50 uncertified
paths, sorted. Result keys besides the gate format: per_rule {rule: findings}, per_dir {dir: findings}, terminators
{name: tokens covered} (see nostub.py, R1). Exit 0 iff green."""
import importlib.util
import json
import os
import sys

_spec = importlib.util.spec_from_file_location("nostub", os.path.join(os.path.dirname(os.path.abspath(__file__)), "nostub.py"))
ns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ns)

def main(argv):
    terms, dirs, it = [], [], iter(argv)
    for a in it:
        if a == "--terminator":
            terms.append(next(it, ""))
        else:
            dirs.append(os.path.normpath(a))
    dirs = [d for d in dirs if os.path.isdir(d)]
    res = ns.check(dirs, sorted(set(terms))) if dirs else {"files": [], "findings": [], "counts": {}, "terminators": {}}
    bad = sorted({x["file"] for x in res["findings"]})
    per_dir = {d: sum(1 for x in res["findings"] if x["file"] == d or x["file"].startswith(d + os.sep)) for d in dirs}
    n = len(res["findings"])
    out = {"format": "fnvnx-gate/1", "name": "nostub", "status": "red" if bad else "green", "universe": len(res["files"]),
           "certified": len(res["files"]) - len(bad), "uncertified": bad[:50], "per_rule": res["counts"], "per_dir": per_dir,
           "terminators": res["terminators"],
           "detail": "%d findings in %d of %d files" % (n, len(bad), len(res["files"])) if bad else
           "%d files, zero findings" % len(res["files"])}
    with open(os.path.join(os.environ["GATE_OUT"], "nostub.json"), "w") as fh:
        json.dump(out, fh, sort_keys=True)
    return 2 if bad else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
