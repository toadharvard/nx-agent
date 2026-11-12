#!/usr/bin/env python3
"""Gate "admit" (fnvnx-gate/1): nothing is in the trunk without the gates that close its classes of failure.
    python3 -I gate_admit.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/admit.json

Rule. verify/admit.json ("fnvnx-admit/1") lists the roots of the port tree: {"path", "class", "gates", ["recipe"]}.
Universe: every file git tracks below ports/fnv-nx (`git ls-files -z`), an item is its repo-relative path.
A file is certified iff ALL hold:
  1. exactly one root contains it (a root is a directory prefix or one exact file path; roots do not nest);
  2. the root's class is one of CLASSES and the root names every gate REQUIRED for that class;
  3. every gate the root names is a gate of verify/gates.json of this same tree;
  4. a product or test root names a "recipe": a file inside the root that the build gates read (how its code is compiled in the
     declared environment); the file exists.
Classes:
  doc      text for people: no gate required. Nothing in a doc root is compiled or run by a build.
  tool     stage and verification tools: "selftests" required (their tests pass in the declared environment).
  product  code that is compiled into the shipped binary: REQUIRED_PRODUCT gates, the static closure of the run-time
           failure classes of verify/VERDICT.md. A product root cannot be admitted before those gates exist in
           gates.json, so code enters the trunk only together with the gates that judge it.
  test     tests that are BUILT by the recipe from the real product files (owner order "no doubles in tests"): "doubles" required; a
           "recipe" like a product root. A test root's objects are judged by gate doubles against the symbols of the product roots.
Argument: the verdict is green only if certified == universe, so a tracked file outside every root, a root with a
missing gate, or a product root without its gates makes every merge red. The table maps directories to gates; it
holds no exception for a file, a function or a finding.
Assumption: `git ls-files` lists the tree under test (the verdict runs gates in a clean worktree of the commit)."""
import json
import os
import subprocess
import sys

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
GATES = TREE + "/verify/gates.json"
CLASSES = ("doc", "tool", "product", "test")
# A product root names a gate for every run-time failure class of VERDICT.md. Wired on the trunk: objstub .. oracle,
# the contract and link gates. Owned by other leads: imports, forms, d3d9total, r7, namegate, argordergate, abi.
# Classes A-G of the owner's rule: datamodel, order, fpexact, eh, exepin, addr32, machine.
REQUIRED_PRODUCT = ("objstub", "linkstrict", "determinism", "oracle",
                    "n1", "n2_data", "n2_call", "n2_vcall", "xc", "xunit", "rtti", "pcrel", "bodysel",
                    "abs", "disp", "reach", "reachlink", "gpa",
                    "imports", "forms", "d3d9total", "r7", "namegate", "argordergate", "abi",
                    "datamodel", "order", "fpexact", "eh", "exepin", "addr32", "machine", "hostapi")
REQUIRED = {"doc": (), "tool": ("selftests",), "product": REQUIRED_PRODUCT, "test": ("doubles",)}


def judge(files, admit, gate_names, exists):
    """-> (uncertified {path: reason}, root errors [str]); pure function of its arguments"""
    roots, errors = [], []
    if admit.get("format") != "fnvnx-admit/1":
        errors.append("admit.json: format is not fnvnx-admit/1")
    for r in admit.get("roots", []):
        path, cls, gates = r.get("path", ""), r.get("class"), r.get("gates", [])
        why = []
        if not path or path != path.strip("/") or not (path == TREE or path.startswith(TREE + "/")):
            why.append("path is not below " + TREE)
        if cls not in CLASSES:
            why.append("class %r is not one of %s" % (cls, ", ".join(CLASSES)))
        else:
            why += ["required gate %s not named" % g for g in REQUIRED[cls] if g not in gates]
            if cls in ("product", "test"):
                rec = r.get("recipe", "")
                if not rec.startswith(path + "/") or not exists(rec):
                    why.append("recipe %r is not an existing file inside the root" % rec)
        why += ["gate %s is not in gates.json" % g for g in gates if g not in gate_names]
        roots.append((path, why))
    paths = [p for p, _ in roots]
    for i, a in enumerate(paths):
        for b in paths[i + 1:]:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                errors.append("roots %s and %s overlap" % (a, b))
    bad = {}
    for f in files:
        hit = [(p, why) for p, why in roots if f == p or f.startswith(p + "/")]
        if len(hit) != 1:
            bad[f] = "in %d roots" % len(hit)
        elif hit[0][1]:
            bad[f] = "root %s: %s" % (hit[0][0], "; ".join(hit[0][1]))
    errors += ["root %s: %s" % (p, "; ".join(why)) for p, why in roots if why]
    return bad, errors


def main():
    out = os.environ["GATE_OUT"]
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    files = sorted(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    with open(ADMIT) as f:
        admit = json.load(f)
    with open(GATES) as f:
        gate_names = [g["name"] for g in json.load(f)["gates"]]
    bad, errors = judge(files, admit, gate_names, os.path.isfile)
    red = bool(bad or errors)
    certified = 0 if errors else len(files) - len(bad)
    detail = "%d files, %d roots; %s" % (len(files), len(admit.get("roots", [])),
                                         "; ".join(sorted(errors))[:400] if errors else "every file under a gated root")
    res = {"format": "fnvnx-gate/1", "name": "admit", "status": "red" if red else "green", "universe": len(files),
           "certified": certified, "uncertified": sorted(bad)[:50], "detail": detail}
    with open(os.path.join(out, "admit.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    print("admit: %s %d/%d %s" % (res["status"], certified, len(files), detail))
    return 2 if red else 0


if __name__ == "__main__":
    sys.exit(main())
