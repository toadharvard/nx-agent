#!/usr/bin/env python3
"""Gate "machine" (fnvnx-gate/1, class G of VERDICT.md): the product's registry hive holds exactly the keys that the exe and the modules read
(rules: contract/hive.py).
    python3 -I gate_machine.py     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/machine.json and machine.ids
Roots come only from verify/admit.json (class product). A root DECLARES its hive in `<root>/registry.in.json` ("fnvnx-registry-in/1"):
  {"format", "external": NAME of an entry of the root's recipe `external` (private: fnv-nx-build commit + tree_sha256),
   "sites": path of regsites.tsv inside it (the registry-API call sites of the exe and the modules),
   "declared": path inside the root of declared_registry.tsv, "seed": path inside the root of the tracked seed include the product is built from,
   "seed_unit": the recipe unit that applies the seed (the only unit allowed to reference a hive-writing API)}
Universe, classes and certification: contract/hive.py. Writers are read from the objects of the build (recipe.symbols per object: undefined
names). A dynamic site (root, key or value not statically resolved) is class `dynamic`: the gate is red and its detail says MEASUREMENT, as the reach
gate treats a dynamic-name GetProcAddress site; there is no allow-list. Universe: gate_build.quantify (the admitted product roots; a root without registry.in.json is one red item, unless it is header-only (gate_build.unitless): 0 items). No product root: universe 0, green. Bad input, a red build, an invalid recipe or an external that fails its hash is red. Exit 0 iff green."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "contract"))
import cas  # noqa: E402
import gate_build  # noqa: E402
import gate_imports  # noqa: E402
import hive  # noqa: E402
import recipe  # noqa: E402

IN_KEYS = {"format", "external", "sites", "declared", "seed", "seed_unit"}


def refs_of(res):
    """{unit: set of undefined symbol names} of the objects of a build (objects.json `objects`)"""
    out = {}
    for o in res["objects"]:
        out.setdefault(o["unit"], set()).update(recipe.symbols(o["object"], o["unit"])[1])
    return out


def root_items(root, rec, res, decl, ext, repo, cache):
    if set(decl) != IN_KEYS or decl["format"] != "fnvnx-registry-in/1":
        raise ValueError("%s/registry.in.json: need exactly the keys %s and format fnvnx-registry-in/1" % (root, sorted(IN_KEYS)))
    sites = gate_imports.read(os.path.join(ext, decl["sites"]))
    declared = gate_imports.read(os.path.join(repo, root, decl["declared"]))
    seed = gate_imports.read(os.path.join(repo, root, decl["seed"]))
    return hive.judge(sites, declared, seed, refs_of(res), decl["seed_unit"])


def judge_root(root, repo, cache, admit, bad):
    """One admitted product ROOT -> (universe, certified, ids, detail) (gate_build.quantify); input errors are red with the reason, never raised.
    BAD collects (id, class) of the uncertified items."""
    p = os.path.join(repo, root, "registry.in.json")
    try:
        if not os.path.isfile(p):
            if gate_build.unitless(root, cache, repo):
                return 0, 0, [], "header-only root: no code, so no hive (by construction)"
            return 1, 0, [root + ":registry.in.json"], "declares no registry.in.json (the root's hive is undeclared)"
        with open(p) as f:
            decl = json.load(f)
        built = gate_build.ensure(root, cache, repo)[0]
        if built["status"] == "red":
            raise ValueError("build red at stage %s" % built["failure"]["stage"])
        rec = recipe.load_recipe(repo, admit, root)
        items = root_items(root, rec, built, decl, gate_imports.ext_tree(rec, decl["external"], cache), repo, cache)
    except recipe.RecipeError as e:
        return 1, 0, [root + ":recipe"], "recipe invalid: %s" % "; ".join(e.errors)[:200]
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError, cas.CasError) as e:
        return 1, 0, [root + ":inputs"], "input error: %s" % (str(e)[:300] or type(e).__name__)
    mine = sorted((rid, c) for rid, c in items if c)
    bad += mine
    classes = {}
    for _, c in mine:
        k = c.split(":")[0]
        classes[k] = classes.get(k, 0) + 1
    detail = ", ".join("%s %d" % kv for kv in sorted(classes.items())) or "ok"
    if classes.get("dynamic"):
        detail = "MEASUREMENT build, %d registry-API sites with an unresolved key or value: %s" % (classes["dynamic"], detail)
    return len(items), len(items) - len(mine), [rid for rid, _ in mine], detail


def main():
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-machine-")
    cache, repo, bad = os.environ.get("GATE_CACHE") or tmp, ".", []
    try:
        admit = recipe.load_admit(repo)
        u, c, ids, detail = gate_build.quantify(repo, lambda root: judge_root(root, repo, cache, admit, bad))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    status = "green" if u == c else "red"
    doc = {"format": "fnvnx-gate/1", "name": "machine", "status": status, "universe": u, "certified": c, "uncertified": ids[:50],
           "detail": detail}
    out = os.environ["GATE_OUT"]
    with open(os.path.join(out, "machine.json"), "w") as fh:
        json.dump(doc, fh, sort_keys=True)
    with open(os.path.join(out, "machine.ids"), "w") as fh:
        for rid, cl in sorted(bad):
            fh.write("%s\t%s\n" % (rid, cl))
    print("machine: %s %d/%d %s" % (status, c, u, detail[:200]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
