#!/usr/bin/env python3
"""Gate "admit" (fnvnx-gate/1): nothing is in the trunk without the gates that close its classes of failure.
    python3 -I gate_admit.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/admit.json

Rule. verify/admit.json ("fnvnx-admit/1") lists the roots of the port tree: {"path", "class", "gates", ["recipe"], ["imports"], ["trees"], ["entry"]}.
`entry` (class tool: required, may be empty): the stage entry points of the root, .py files inside its trees. gate_pure's universe is the
import closure of these and of the `generated` tools of the recipes; a script only a gate driver runs is not an entry point.
`trees` (optional): the list of the directories (or exact files) of one root, at least two, the first equal to "path"; the root's
universe is their union, and its python is judged in one puregate run, so trees that import each other form ONE root with no
file moved. Two roots never share or nest a tree. The root is named by "path" in `imports`.
`imports` (a root's python cross-root imports): a list of paths of other admitted roots; "pure" (gate_pure.py) judges the root
with its own directory and the parent directory of each imported root as import bases, so a root's python may import another
root's modules only when it declares it. Each entry must be an admitted root other than the root itself, without repeats; the
declared imports form no cycle (an error of the table: every file is uncertified).
Universe: every file git tracks below ports/fnv-nx (`git ls-files -z`), an item is its repo-relative path.
A file is certified iff ALL hold:
  1. exactly one root contains it (a root is a directory prefix or one exact file path; roots do not nest);
  2. the root's class is one of CLASSES and the root names every gate REQUIRED for that class;
  3. every gate the root names is a gate of verify/gates.json of this same tree;
  4. a product root names a "recipe": a file inside the root that the build gates read (how its code is compiled in the
     declared environment); the file exists.
Classes:
  doc      text for people: no gate required. Nothing in a doc root is compiled or run by a build.
  tool     stage and verification tools: "selftests" required (their tests pass in the declared environment).
  product  code that is compiled into the shipped binary: REQUIRED_PRODUCT gates, the static closure of the run-time
           failure classes of verify/VERDICT.md. A product root cannot be admitted before those gates exist in
           gates.json, so code enters the trunk only together with the gates that judge it.
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
CLASSES = ("doc", "tool", "product")
# A product root names a gate for every run-time failure class of VERDICT.md. Wired on the trunk: objstub .. oracle,
# the contract and link gates. Owned by other leads: imports, forms, d3d9total, r7, namegate, argordergate, abi.
# Classes A-G of the owner's rule: datamodel, order, fpexact, eh, exepin, addr32, machine.
REQUIRED_PRODUCT = ("objstub", "linkstrict", "determinism", "oracle",
                    "n1", "n2_data", "n2_call", "n2_vcall", "xc", "xunit", "rtti", "pcrel", "bodysel",
                    "abs", "disp", "reach", "reachlink", "gpa",
                    "imports", "forms", "d3d9total", "r7", "namegate", "argordergate", "abi",
                    "datamodel", "order", "fpexact", "eh", "exepin", "addr32", "machine", "hostapi")
REQUIRED = {"doc": (), "tool": ("selftests",), "product": REQUIRED_PRODUCT}


def trees(r):
    """The trees of an admit root: its `trees` list, else [path]."""
    t = r.get("trees")
    return list(t) if isinstance(t, list) else [r.get("path", "")]


def imports_errors(roots):
    """Errors of the declared imports of ROOTS (list of admit root dicts): an unknown root, itself, a repeat, a cycle."""
    by = {r.get("path"): r for r in roots}
    errs, edges = [], {}
    for r in roots:
        imp = r.get("imports", [])
        p = r.get("path")
        if not isinstance(imp, list) or any(not isinstance(i, str) for i in imp):
            errs.append("root %s: imports is not a list of paths" % p)
            continue
        if len(set(imp)) != len(imp):
            errs.append("root %s: imports repeats a root" % p)
        for i in imp:
            if i == p:
                errs.append("root %s imports itself" % p)
            elif i not in by:
                errs.append("root %s: import %s is not an admitted root" % (p, i))
        edges[p] = [i for i in imp if i in by and i != p]
    state = {}

    def visit(n, stack):
        state[n] = 1
        for m in sorted(edges.get(n, [])):
            if state.get(m) == 1:
                errs.append("imports cycle: " + " -> ".join(stack[stack.index(m):] + [n, m] if m in stack else [n, m]))
            elif m not in state:
                visit(m, stack + [n])
        state[n] = 2
    for n in sorted(edges):
        if n not in state:
            visit(n, [])
    return errs


def judge(files, admit, gate_names, exists):
    """-> (uncertified {path: reason}, root errors [str]); pure function of its arguments"""
    roots, errors = [], []
    if admit.get("format") != "fnvnx-admit/1":
        errors.append("admit.json: format is not fnvnx-admit/1")
    for r in admit.get("roots", []):
        path, cls, gates = r.get("path", ""), r.get("class"), r.get("gates", [])
        why = []
        if "trees" in r and not (isinstance(r["trees"], list) and len(r["trees"]) >= 2 and r["trees"][0] == path
                                 and len(set(r["trees"])) == len(r["trees"]) and all(isinstance(t, str) for t in r["trees"])):
            why.append("trees is not a list of at least two distinct paths starting with path")
        if cls == "tool":
            en = r.get("entry")
            if not isinstance(en, list) or any(not isinstance(e, str) or not e.endswith(".py") or not exists(e)
                                               or not any(e.startswith(t + "/") for t in trees(r)) for e in en):
                why.append("entry is not a list of existing .py files inside the root's trees")
        for t in (trees(r) if not why else [path]):
            if not t or t != t.strip("/") or not (t == TREE or t.startswith(TREE + "/")):
                why.append("%s is not below %s" % (t, TREE))
        if cls not in CLASSES:
            why.append("class %r is not one of %s" % (cls, ", ".join(CLASSES)))
        else:
            why += ["required gate %s not named" % g for g in REQUIRED[cls] if g not in gates]
            if cls == "product":
                rec = r.get("recipe", "")
                if not any(rec.startswith(t + "/") for t in trees(r)) or not exists(rec):
                    why.append("recipe %r is not an existing file inside the root" % rec)
        why += ["gate %s is not in gates.json" % g for g in gates if g not in gate_names]
        roots.append((trees(r) if not why else [path], why))
    errors += imports_errors(admit.get("roots", []))
    paths = [(t, i) for i, (ts, _) in enumerate(roots) for t in ts]
    for n, (a, i) in enumerate(paths):
        for b, j in paths[n + 1:]:
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                errors.append("trees %s and %s overlap" % (a, b))
    bad = {}
    for f in files:
        hit = [(ts[0], why) for ts, why in roots if any(f == p or f.startswith(p + "/") for p in ts)]
        if len(hit) != 1:
            bad[f] = "in %d roots" % len(hit)
        elif hit[0][1]:
            bad[f] = "root %s: %s" % (hit[0][0], "; ".join(hit[0][1]))
    errors += ["root %s: %s" % (ts[0], "; ".join(why)) for ts, why in roots if why]
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
