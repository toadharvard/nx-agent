#!/usr/bin/env python3
"""Selftest of the path rules of recipe.check (no build): an include into a generated tree of another root is a check error (`uses` is the way to a
producer's declared output). Runs on the real admit table and recipes of the checkout: lib/sha256 (a guest root) plus one include entry."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import recipe

REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
os.chdir(REPO)
fails = []


def check(what, ok, detail=None):
    if not ok:
        fails.append("%s: %r" % (what, detail))


roots, files, pin = recipe.load_admit("."), recipe.tracked("."), recipe.load_pin(".")
R = "ports/fnv-nx/lib/sha256"
rec = json.load(open(roots[R]["recipe"]))
OWNER = "ports/fnv-nx/platform"
trees = recipe.generated_trees(roots, ".")
GEN = [t for t in trees.get(OWNER, []) if t.startswith(OWNER + "/freetype/gen")]
check("the platform root has generated trees that hold no tracked file (derived from its recipe's outputs)", GEN, trees.get(OWNER))
check("a directory that also holds tracked files is no generated tree", OWNER + "/audio" not in trees.get(OWNER, []), trees.get(OWNER))


def gen_errs(**kw):
    return [e for e in recipe.check(dict(rec, **kw), R, files, roots, pin, ".") if "generated tree" in e]


check("the root as it is: no generated-tree error", gen_errs() == [], gen_errs())
for inc in (GEN[0], GEN[0] + "/include"):
    e = gen_errs(include=list(rec.get("include") or []) + [inc])
    check("include %s (inside a generated tree of another root) is a check error naming the owner and `uses`" % inc, len(e) == 1 and OWNER in e[0] and "uses" in e[0], e)
own = trees.get(OWNER, [])
rp = json.load(open(roots[OWNER]["recipe"]))
pe = [e for e in recipe.check(dict(rp, include=list(rp.get("include") or []) + [GEN[0]]), OWNER, files, roots, pin, ".") if "generated tree" in e]
check("a root's own generated tree is no violation (the rule is about another root)", pe == [], pe)
if fails:
    print("\n".join("FAIL " + f for f in fails))
    sys.exit(1)
print("pathrules: ok")
