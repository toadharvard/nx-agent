#!/usr/bin/env python3
"""Gate "headers" (fnvnx-gate/1): every header of every admitted headers tree is read by an admitted unit (RECIPE.md "Headers trees").
    python3 -I gate_headers.py     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/headers.json
Universe: every tracked file under a root of class `headers` in verify/admit.json (admit.json refuses a unit file, a recipe and a link there).
Certified: a file that the preprocessor stage of at least one unit of an admitted product root (every variant) read: the stage's own
dependency list (deps.d of the stage result found by the pp_key of objects.json), so a header is certified through the units that include it, and
those units' objects are judged by objstub and the other gates. A header no unit reads is not certified (uncertified lists them, <= 50, and the
detail gives the count). A root whose build is red certifies nothing. No headers tree: universe 0, green."""
import json
import os
import posixpath
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402
import unitstage  # noqa: E402
import unitview  # noqa: E402


def read_set(repo, cache):
    """-> (set of repo paths read by some unit's PP stage, [reasons])"""
    seen, why, store = set(), [], os.path.join(cache, "store")
    for root in gate_build.product_roots(repo):
        try:
            res, _ = gate_build.ensure(root, cache, repo)
        except recipe.RecipeError as e:
            why.append("%s: invalid recipe: %s" % (root, "; ".join(e.errors)[:200]))
            continue
        if res["status"] == "red":
            why.append("%s: build red at %s" % (root, res["failure"]["stage"]))
            continue
        for key in res["pp_keys"].values():
            text = open(unitstage.result_file(store, key, "deps.d")).read()
            for p in unitview.parse_deps(text):
                p = posixpath.normpath(p)
                if p.startswith("/src/"):
                    seen.add(p[len("/src/"):])
    return seen, why


def main():
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-headers-")
    cache = os.environ.get("GATE_CACHE") or tmp
    try:
        roots = [p for p, r in recipe.load_admit(repo).items() if r["class"] == "headers"]
        files = sorted(f for f in recipe.tracked(repo) if any(recipe.under(f, r) for r in roots))
        seen, why = read_set(repo, cache) if files else (set(), [])
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    bad = [f for f in files if f not in seen]
    detail = "; ".join(why)[:600] or ("%d of %d headers read by no unit" % (len(bad), len(files)) if bad else
                                       ("%d headers in %d trees, each read by an admitted unit" % (len(files), len(roots)) if files else "no headers tree"))
    res = {"format": "fnvnx-gate/1", "name": "headers", "status": "red" if bad else "green", "universe": len(files),
           "certified": len(files) - len(bad), "uncertified": bad[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "headers.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("headers: %s %d/%d %s" % (res["status"], res["certified"], res["universe"], detail[:200]))
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
