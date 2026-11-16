#!/usr/bin/env python3
"""The one expansion point of a recipe (recipe.expand_recipe; RECIPE.md "foreach", "Profiles"): expansion is a projection, expand(expand(r)) == expand(r), over a build-time foreach
(the marker step of foreach_dynamic is passed through unchanged) and over every admitted recipe of the tree; and no function but expand_recipe calls expand_profile or expand_foreach
(call sites found by ast over every .py of the tree, tests excluded). Plain script: one PASS/FAIL line per case."""
# reads: ports/fnv-nx
import ast
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import recipe  # noqa: E402

REPO = os.path.realpath(os.path.join(VERIFY, "..", "..", ".."))
TREE = os.path.join(REPO, "ports", "fnv-nx")
fails = []

def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + repr(info)[:300]))
    if not ok:
        fails.append(name)

# the repro: a build-time foreach expanded twice
dyn = {"format": "fnvnx-recipe/1", "run": [
    {"name": "mk", "exec": {"python": True}, "args": [], "inputs": [], "debs": [], "outputs": ["mk.tsv"]},
    {"name": "prove", "foreach": {"table": {"stage": "r:run:mk", "path": "mk.tsv"}, "key": "shape", "into": "proof"}, "exec": {"python": True},
     "args": ["{shape}"], "inputs": [], "debs": [], "outputs": ["proof/{shape}.txt"]}]}
a = recipe.expand_foreach(copy.deepcopy(dyn), REPO)
try:
    b = recipe.expand_foreach(copy.deepcopy(a), REPO)
except ValueError as e:
    b = e
check("a build-time foreach: the marker step carries row, dirs [into], no outputs", a["run"][1]["foreach"].get("row") and a["run"][1]["dirs"] == ["proof"] and a["run"][1]["outputs"] == [], a)
check("a build-time foreach: expanding the expansion again changes nothing", b == a, b)

# every admitted recipe of the tree
pin = recipe.load_pin(REPO)
roots = recipe.load_admit(REPO)
paths = sorted({p for r in roots.values() if isinstance(r, dict) for p in ([r["recipe"]] if isinstance(r.get("recipe"), str) else list((r.get("recipe") or {}).values()))})
bad = []
for p in paths:
    with open(os.path.join(REPO, p)) as fh:
        raw = json.load(fh)
    try:
        e1, _, f1 = recipe.expand_recipe(raw, pin, REPO)
        e2, _, f2 = recipe.expand_recipe(copy.deepcopy(e1), pin, REPO)
    except Exception as x:   # an expansion that raises is a failure of the rule, not a skip
        bad.append((p, repr(x)))
        continue
    if e1 != e2 or f2:
        bad.append((p, "not a projection" if e1 != e2 else f2))
check("every admitted recipe (%d files): expand(expand(r)) == expand(r)" % len(paths), not bad and len(paths) > 0, bad)

# call sites, by ast
sites = []
for d, ds, fs in os.walk(TREE):
    ds[:] = [x for x in ds if x not in ("__pycache__", ".git", "tests")]
    for n in fs:
        if not n.endswith(".py"):
            continue
        p = os.path.join(d, n)
        try:
            tree = ast.parse(open(p).read())
        except (SyntaxError, ValueError):
            continue
        def walk(node, fn):
            for c in ast.iter_child_nodes(node):
                f = c.name if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(c, ast.Call):
                    nm = c.func.attr if isinstance(c.func, ast.Attribute) else (c.func.id if isinstance(c.func, ast.Name) else None)
                    if nm in ("expand_foreach", "expand_profile"):
                        sites.append((os.path.relpath(p, REPO), fn, nm))
                walk(c, f)
        walk(tree, "<module>")
stray = [s for s in sites if s[1] != "expand_recipe"]
check("call sites: expand_foreach and expand_profile are called only from expand_recipe (%d sites)" % len(sites), sites and not stray, stray)

print("FAILED: %d" % len(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
