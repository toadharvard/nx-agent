#!/usr/bin/env python3
"""Gate "build" (fnvnx-gate/1): every product root's recipe (build.json, see RECIPE.md) builds, all in the sandbox.
    python3 -I gate_build.py        cwd = checkout root (a git worktree), env GATE_OUT, optional GATE_CACHE, optional GATE_COMMIT
Universe: every unit of every product root of verify/admit.json (tracked sources + generated sources, recipe.units_of) and the ELF
of a root with a link. Certified: units whose object was built (+ the ELF). uncertified: repo-relative unit paths (<= 50, sorted).
No product root: universe 0, green. A recipe that fails validation certifies nothing (units = tracked source files).
Stages are cached by key in GATE_CACHE/store (a temp dir without GATE_CACHE); the result must not depend on the cache (determinism.py
compares a warm and a cold build). Keep going (RECIPE.md): a red root lists every red unit and stage and every blocked unit; all that was buildable is built and kept.
Hand-over to later gates: verdict.py gives EVERY gate its own GATE_OUT, so a later gate cannot
read this gate's GATE_OUT. objects.json of a root is therefore also kept at GATE_CACHE/objects/<tree>/<root name>/objects.json (with
obj/, the ELF and constants.tsv beside it; <tree> = git tree of the commit under test, <root name> = path with / as __), and later
gates call ensure() (build or cache hits, same result). Without GATE_CACHE nothing outlives the gate except GATE_OUT/objects/<root name>.json."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recipe  # noqa: E402


def product_roots(repo="."):
    """Every (root, variant) pair of the admitted product roots, spelled `root` (the implicit variant) or `root@NAME` (recipe.split_spec)."""
    adm = recipe.load_admit(repo)
    return sorted(s for p, r in adm.items() if r["class"] == "product" for s in recipe.pairs_of(_full(repo, r), p, r))


def _full(repo, ent):
    try:
        with open(os.path.join(repo, ent["recipe"])) as f:
            return json.load(f)
    except (OSError, ValueError, KeyError):
        return {}


def buildable_roots(repo="."):
    """The roots the build family (build, determinism, linkstrict, objstub) judges: the product roots and the test roots (admit class test)."""
    return sorted(p for p, r in recipe.load_admit(repo).items() if r["class"] in ("product", "test"))


NO_ROOT = "no product root (nothing to build yet)"


def quantify(repo, judge_root):
    """The universe convention of every per-root gate: the admitted product roots of admit.json. JUDGE_ROOT(root) -> (universe, certified,
    ids, detail); the items are summed (ids <= 50), a root's red detail is kept. No product root: (0, 0, [], NO_ROOT), green."""
    u = c = 0
    ids, why = [], []
    roots = product_roots(repo)
    for root in roots:
        ru, rc, rids, rd = judge_root(root)
        u, c, ids = u + ru, c + rc, ids + rids
        if rc != ru:
            why.append("%s: %s" % (root, rd))
    if not roots:
        return 0, 0, [], NO_ROOT
    return u, c, sorted(ids)[:50], ("; ".join(why) or "%d roots" % len(roots))[:900]


def tree_of(repo="."):
    return subprocess.run(["git", "-C", repo, "rev-parse", os.environ.get("GATE_COMMIT", "HEAD") + "^{tree}"], capture_output=True,
                          text=True, check=True).stdout.strip()


def ensure(root, cache, repo="."):
    """Build ROOT with the stage cache GATE_CACHE (CACHE, or a fresh temp dir) -> (objects dict, out dir); raises RecipeError."""
    out = os.path.join(cache, "objects", tree_of(repo), root.replace("/", "__"))
    return recipe.build(repo, root, os.path.join(cache, "store"), out, ext=os.path.join(cache, "external")), out


def ids(root, res, rec):
    """Items of a root: repo-relative unit paths and its ELF."""
    return [root + "/" + u for u in res.get("units", [])] + ([root + "/" + rec["link"]["output"]] if rec.get("link") else [])


def main():
    items, done, why, repo = [], [], [], "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-build-")
    cache = os.environ.get("GATE_CACHE") or tmp
    roots = recipe.load_admit(repo)
    files = recipe.tracked(repo)
    try:
        for root in buildable_roots(repo):
            rec = recipe.load_recipe(repo, roots, root)
            try:
                res, out = ensure(root, cache, repo)
            except recipe.RecipeError as e:
                items += [root + "/" + u for u in recipe.units_of(rec, recipe.split_spec(root)[0], files) if isinstance(rec, dict)]
                why.append("%s: invalid recipe: %s" % (root, "; ".join(e.errors)[:300]))
                continue
            items += ids(root, res, rec)
            if res["status"] == "red":
                f = res["failure"]
                why.append("%s: stage %s exit %s: %s (%d red units, %d red stages, %d blocked)" % (
                    root, f["stage"], f["exit"], " | ".join(f["stderr"][-3:])[:300], len(res.get("failed_units", [])), len(res.get("red_stages", [])),
                    len(res.get("blocked", []))))
                ok = res["objects"] and [o["unit"] for o in res["objects"]] or f.get("done", [])
                done += [root + "/" + u for u in ok] + ([root + "/" + rec["link"]["output"]] if res["elf"] else [])
            else:
                done += ids(root, res, rec)
            os.makedirs(os.path.join(os.environ["GATE_OUT"], "objects"), exist_ok=True)
            shutil.copyfile(os.path.join(out, "objects.json"), os.path.join(os.environ["GATE_OUT"], "objects", root.replace("/", "__") + ".json"))
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    bad = sorted(set(items) - set(done))
    detail = "; ".join(why) or ("%d units/ELF built from %d product and test roots" % (len(items), len(buildable_roots(repo))) if items else
                                NO_ROOT)
    res = {"format": "fnvnx-gate/1", "name": "build", "status": "red" if why else "green", "universe": len(items),
           "certified": len(set(done) & set(items)), "uncertified": bad[:50], "detail": detail[:900]}
    with open(os.path.join(os.environ["GATE_OUT"], "build.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("build: %s %d/%d %s" % (res["status"], res["certified"], res["universe"], detail[:200]))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main())
