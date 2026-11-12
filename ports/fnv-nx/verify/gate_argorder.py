#!/usr/bin/env python3
"""Gate "argordergate" (fnvnx-gate/1, VERDICT.md class: calling convention / argument order): every native C++ unit of a guest root is compiled with
the fnvnx_order plugin (MSVC evaluation order of arguments, objects, operators, assignments) loaded in the rule's order.
    python3 -I gate_argorder.py        cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/argordergate.json
Universe: the C++ units of every admitted product root with "target": "guest" and "cxx_origin": "native" (admit.json; the lifter emits the order
itself, a lifted root has none). No such root: universe 0, green.
Certificate of a unit, all of: (1) the recipe of its root validates (recipe.check, which holds the plugin rule); (2) the recipe's plugin list is
the rule's list in the rule's load order (recipe.PLUGIN_SOURCES order, the order of compile.sh) and holds fnvnx_order with exactly the rule's arguments (args=rtl, ...)
(recipe.PLUGIN_ARGS); (3) the build is green and its result holds the fnvnx_order .so (the file built by the hermetic plugin stage); (4) the unit's object
was built. The .so is a declared input of every CC stage of a C++ unit (recipe.Job.compile: cc_inputs), so it is in the CC stage key; an
object compiled without it, or with another .so, has another key and is not this object. A red build or invalid recipe certifies no unit of the root."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402

ORDER = "fnvnx_order"


def native_guest(repo):
    roots = recipe.load_admit(repo)
    return [r for r in gate_build.product_roots(repo) if roots[r].get("target") == "guest" and roots[r].get("cxx_origin") == "native"]


def judge_root(root, repo, cache):
    """-> (universe, certified, ids, detail) of one native guest root."""
    roots, files = recipe.load_admit(repo), recipe.tracked(repo)
    rec = recipe.load_recipe(repo, roots, root)
    cxx = sorted(root + "/" + u for u in recipe.units_of(rec, root, files) if u.endswith(recipe.CXX_EXT)) if isinstance(rec, dict) else []
    errs = recipe.check(rec, root, files, roots, recipe.load_pin(repo))
    if errs:
        return len(cxx), 0, cxx, "invalid recipe: %s" % "; ".join(errs)[:300]
    names = [p["name"] for p in rec.get("plugins", [])]
    want = list(recipe.PLUGIN_SOURCES)
    if names != want:
        return len(cxx), 0, cxx, "plugins %s are not the rule's %s in the rule's order" % (names, want)
    od = next(p for p in rec["plugins"] if p["name"] == ORDER)
    if od["args"] != recipe.PLUGIN_ARGS[ORDER]:
        return len(cxx), 0, cxx, "%s arguments %s are not the rule's %s" % (ORDER, od["args"], recipe.PLUGIN_ARGS[ORDER])
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return len(cxx), 0, cxx, "invalid recipe: %s" % "; ".join(e.errors)[:300]
    if res["status"] != "green" or ORDER not in [p["name"] for p in res["plugins"]]:
        return len(cxx), 0, cxx, "build %s, plugins %s" % (res["status"], [p["name"] for p in res["plugins"]])
    built = {root + "/" + o["unit"] for o in res["objects"]}
    bad = [u for u in cxx if u not in built]
    return len(cxx), len(cxx) - len(bad), bad, "%d units" % len(cxx)


def main():
    repo, tmp = ".", None
    cache = os.environ.get("GATE_CACHE")
    if not cache:
        import tempfile
        cache = tmp = tempfile.mkdtemp(prefix="gate-argorder-")
    roots = native_guest(repo)
    try:
        u = c = 0
        ids, why = [], []
        for r in roots:
            ru, rc, rids, rd = judge_root(r, repo, cache)
            u, c, ids = u + ru, c + rc, ids + rids
            if rc != ru:
                why.append("%s: %s" % (r, rd))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    detail = "; ".join(why) or ("%d C++ units of %d native guest roots load fnvnx_order in the rule's order" % (u, len(roots)) if roots else
                                "no native guest root (universe 0)")
    res = {"format": "fnvnx-gate/1", "name": "argordergate", "status": "red" if why else "green", "universe": u, "certified": c,
           "uncertified": sorted(ids)[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "argordergate.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("argordergate: %s %d/%d %s" % (res["status"], c, u, detail[:200]))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main())
