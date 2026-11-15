#!/usr/bin/env python3
"""Synthetic check of the two recipe rules of the facts root (contract/facts): `objects_of` selection by declaration (target, origin) with the
derived exclusion of the consumers of the producing root, and generator inputs that are {stage, path} refs of earlier generators.  No build, no
private input: admit entries and recipes are written into a temp directory."""
import json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import recipe

fails = []


def check(what, got, want=True):
    if got != want:
        fails.append("%s: got %r want %r" % (what, got, want))


P = "ports/fnv-nx/"
LIFTED = {"lifted_by": P + "lift/x.py", "pack": "pk"}
ROOTS = {
    P + "facts": {"class": "tool", "recipe": P + "facts/build.json", "target": "host"},
    P + "nat": {"class": "product", "target": "guest", "cxx_origin": "native", "recipe": P + "nat/build.json"},
    P + "nat_undeclared": {"class": "product", "target": "guest", "recipe": P + "nat_undeclared/build.json"},
    P + "lifted": {"class": "product", "target": "guest", "cxx_origin": LIFTED, "recipe": P + "lifted/build.json"},
    P + "hosted": {"class": "product", "target": "host", "recipe": P + "hosted/build.json"},
    P + "docs": {"class": "doc"},
    P + "consumer": {"class": "product", "target": "guest", "cxx_origin": "native", "recipe": P + "consumer/build.json"},
    P + "viaconsumer": {"class": "product", "target": "guest", "recipe": P + "viaconsumer/build.json"},
    P + "linker": {"class": "product", "target": "guest", "recipe": P + "linker/build.json"},
    P + "descs": {"class": "tool", "target": "host", "recipe": P + "descs/build.json"},          # reads the objects of every product root (objects_of), consumer included
    P + "bridges": {"class": "product", "target": "guest", "recipe": P + "bridges/build.json"},   # uses descs: reaches facts only through it
}
RECS = {
    "consumer": {"uses": [{"root": P + "facts", "output": "native"}]},
    "viaconsumer": {"uses": [{"root": P + "consumer", "output": "o"}]},          # reaches facts through the consumer, transitively
    "linker": {},                     # depends on nothing: stays in
    "descs": {"generated": [{"tool": P + "t.py", "args": [], "inputs": [], "outputs": ["d"], "objects_of": {"except": [P + "bridges"]}}]},
    "bridges": {"uses": [{"root": P + "descs", "output": "descs"}]},
}


def gen(**oo):
    return {"generated": [{"tool": P + "t.py", "args": [], "inputs": [], "outputs": ["o"], "objects_of": oo}]}


with tempfile.TemporaryDirectory() as repo:
    for r, e in ROOTS.items():
        if e.get("recipe"):
            os.makedirs(os.path.join(repo, os.path.dirname(e["recipe"])), exist_ok=True)
            json.dump(RECS.get(r[len(P):], {}), open(os.path.join(repo, e["recipe"]), "w"))
    F = P + "facts"
    allp = sorted(r for r, e in ROOTS.items() if e.get("class") == "product" and r != F)
    check("descs -> consumer -> facts: bridges depends on facts through descs' objects_of (the cycle lift/rt -> facts -> bridges -> descs -> lift/rt)",
          recipe.depends_on(P + "bridges", F, ROOTS, repo))
    univ = [r for r in allp if not recipe.depends_on(r, F, ROOTS, repo)]
    check("no selector: the universe is every product root with a recipe minus the consumers of the producer (derived from the uses graph)",
          recipe.objects_of_roots(gen(), F, ROOTS, repo), univ)
    check("the consumers of the producer are outside its universe", all(P + r not in univ for r in ("consumer", "viaconsumer", "bridges")) and P + "nat" in univ, True)
    want = [P + "linker", P + "nat", P + "nat_undeclared"]
    check("target guest + origin native: declared native or undeclared origin, not lifted, not host, not a consumer of the producer (directly or through a chain)",
          recipe.objects_of_roots(gen(target="guest", origin="native"), F, ROOTS, repo), want)
    check("origin lifted selects the lifted declaration only", recipe.objects_of_roots(gen(origin="lifted"), F, ROOTS, repo), [P + "lifted"])
    check("target host", recipe.objects_of_roots(gen(target="host"), F, ROOTS, repo), [P + "hosted"])
    check("a root's own origin class is read from the declaration, never from its name",
          [recipe.origin_class(ROOTS[r].get("cxx_origin")) for r in (P + "nat", P + "nat_undeclared", P + "lifted")], ["native", "native", "lifted"])
    check("depends_on is transitive over uses", [recipe.depends_on(P + r, F, ROOTS, repo) for r in ("consumer", "viaconsumer", "linker", "nat")],
          [True, True, False, False])
    check("without a repo the selecting stage cannot read recipes: no consumer exclusion (the build always passes the repo)",
          recipe.objects_of_roots(gen(target="guest", origin="native"), F, ROOTS), sorted(want + [P + "consumer", P + "viaconsumer", P + "bridges"]))
    check("dependents_of: only the roots that depend on the producer (the checker's set), the complement of the producer's own selection",
          recipe.objects_of_roots(gen(target="guest", origin="native", dependents_of=F), P + "check", ROOTS, repo),
          [P + "bridges", P + "consumer", P + "viaconsumer"])
    check("root_edges carries the selected roots as edges (cycle check)", set(recipe.root_edges(gen(target="guest", origin="native"), F, ROOTS, repo)), set(want))

    # check(): objects_of keys and stage refs of generator inputs, on the real recipe of contract/facts
    real = os.path.join(HERE, "..", "..", "..", "..")
    rec = json.load(open(os.path.join(real, P + "contract/facts/build.json")))
    roots, files = recipe.load_admit(real), recipe.tracked(real)
    R = P + "contract/facts"
    errs = lambda r: recipe.check(r, R, files, roots, None, real)
    check("the facts recipe is valid", errs(rec), [])
    bad = json.loads(json.dumps(rec))
    bad["generated"][2]["objects_of"]["origin"] = "hand"
    check("an unknown origin is red", any("objects_of must be" in e for e in errs(bad)))
    bad = json.loads(json.dumps(rec))
    bad["generated"][3]["inputs"][1] = {"stage": R + ":gen:4", "path": "bind.json"}      # a LATER generator
    check("a stage ref of a later generator is red", any("gen:4" in e for e in errs(bad)))
    bad = json.loads(json.dumps(rec))
    bad["generated"][3]["inputs"][1] = {"stage": R + ":gen:0", "path": "n1.json"}        # not an output of that generator
    check("a stage ref of a path the generator does not output is red", any("n1.json" in e for e in errs(bad)))
    bad = json.loads(json.dumps(rec))
    bad["generated"][3]["inputs"][1] = {"stage": P + "lift/descs:gen:0", "path": "descs.tsv"}   # another root's stage: that is `uses`
    check("a stage ref of another root is red", any("lift/descs" in e for e in errs(bad)))
if fails:
    print("\n".join("FAIL " + f for f in fails))
    sys.exit(1)
print("objects_select: ok")
