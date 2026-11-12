#!/usr/bin/env python3
"""Gate "datamodel" (fnvnx-gate/1, VERDICT.md class A: data model): every unit of a guest product root is compiled by the pinned game
compiler in the exe's data model.
    python3 -I gate_datamodel.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/datamodel.json
Universe (through gate_build.quantify): the admitted product roots with "target": "guest" (a host root has none); per root one item
`<root>:compiler` plus one item `<root>/<unit>` per unit of the build. No guest root: universe 0, green.
Certificate, read from the build's own outputs (objects.json of the root, the CC stage documents of its units in GATE_CACHE/store/stage-docs,
found by the keys objects.json lists, the document whose "stage" is `cc:<unit>`); nothing is decided by a name in a file:
  compiler item: the build is green, the root's recipe compiler is the external of verify/guest_compiler.json (recipe.guest_errors: name,
    repo, ref, commit, tree_sha256, at, driver paths), and every CC document mounts at the pin's `at` the pin's tree_sha256 and starts
    with the pin's driver (the object was built by that tree, not by a name).
  unit item, all of: a CC stage document exists for the unit; its argv holds -mabi=ilp32; for a C++ unit of a native root also every flag of
    recipe.SEMANTICS_FLAGS and, for every plugin of recipe.GUEST_CXX_PLUGINS, the declared input /plug/<name> (a tree holding <name>.so) and the
    argv flag -fplugin=/plug/<name>/<name>.so. A C++ unit of a lifted root needs no plugin (RECIPE.md: the lifter emits the order itself).
    The documents are the stage keys, so the object is the one these flags and inputs built.
Does not cover (stated): the pin file declares no data-model flags of its own, so the flag set is -mabi=ilp32 for every unit plus
recipe.SEMANTICS_FLAGS for native C++ (the constant of the recipe rule); C and assembler units are checked for -mabi=ilp32 only.
Relies on the owner-accepted item "the compiler is correct": the pinned compiler under -mabi=ilp32 has the exe's data model."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402

ILP32 = "-mabi=ilp32"


def cc_docs(store, res):
    """{unit: CC stage document} of a build result: the stage documents of its stage keys whose stage is `cc:<unit>`."""
    out = {}
    for k in res.get("stage_keys", []):
        p = os.path.join(store, "stage-docs", k[:2], k + ".json")
        if os.path.isfile(p):
            d = json.load(open(p))
            if str(d.get("stage", "")).startswith("cc:"):
                out[d["stage"][3:]] = d
    return out


def unit_why(u, doc, native, pin):
    """Reasons a unit's CC stage document does not certify it ([] = certified)."""
    if doc is None:
        return ["no CC stage document"]
    argv, ins = doc.get("argv", []), {i["at"]: i["tree"] for i in doc.get("inputs", [])}
    why = [] if ILP32 in argv else ["argv lacks " + ILP32]
    if u.endswith(recipe.CXX_EXT) and native:
        why += ["argv lacks " + f for f in recipe.SEMANTICS_FLAGS if f not in argv and f != ILP32]
        for n in recipe.GUEST_CXX_PLUGINS:
            if "/plug/" + n not in ins:
                why.append("no declared input /plug/" + n)
            if "-fplugin=/plug/%s/%s.so" % (n, n) not in argv:
                why.append("argv lacks -fplugin for " + n)
    ex = pin["external"]
    if ins.get(ex["at"]) != ex["tree_sha256"]:
        why.append("compiler tree at %s is not the pin's" % ex["at"])
    if not argv or not argv[0].startswith(ex["at"] + "/"):
        why.append("driver is not inside the pinned compiler")
    return why


def judge_root(root, repo, cache):
    """-> (universe, certified, ids, detail) of one root (gate_build.quantify convention)."""
    roots = recipe.load_admit(repo)
    if recipe.entry_of(roots, root).get("target") != "guest":
        return 0, 0, [], "host root"
    pin, files = recipe.load_pin(repo), recipe.tracked(repo)
    rec = recipe.load_recipe(repo, roots, root)
    item = root + ":compiler"
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        us = [root + "/" + u for u in recipe.units_of(rec, root, files)] if isinstance(rec, dict) else []
        return len(us) + 1, 0, [item] + us, "invalid recipe: %s" % "; ".join(e.errors)[:300]
    units = [recipe.split_spec(root)[0] + "/" + u for u in res.get("units", [])]
    if res["status"] != "green":
        return len(units) + 1, 0, [item] + units, "build %s" % res["status"]
    native = recipe.entry_of(roots, root).get("cxx_origin") == "native"
    docs = cc_docs(os.path.join(cache, "store"), res)
    bad, why = [], []
    g = recipe.guest_errors(rec, root, roots, pin)
    if g:
        bad.append(item)
        why.append("; ".join(g)[:300])
    for u in res["units"]:
        w = unit_why(u, docs.get(u), native, pin)
        if w or g:
            bad.append(root + "/" + u)
            if w:
                why.append("%s: %s" % (u, ", ".join(w)))
    n = len(units) + 1
    return n, n - len(bad), bad, "; ".join(why)[:300] or "%d units" % len(units)


def main():
    import shutil
    import tempfile
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-datamodel-")
    cache = os.environ.get("GATE_CACHE") or tmp
    try:
        u, c, ids, detail = gate_build.quantify(repo, lambda r: judge_root(r, repo, cache))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    if u == 0 and detail != gate_build.NO_ROOT:
        detail = "no guest root (universe 0)"
    res = {"format": "fnvnx-gate/1", "name": "datamodel", "status": "red" if c != u else "green", "universe": u, "certified": c,
           "uncertified": ids if c != u else [], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "datamodel.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("datamodel: %s %d/%d %s" % (res["status"], c, u, detail[:200]))
    return 2 if c != u else 0


if __name__ == "__main__":
    sys.exit(main())
