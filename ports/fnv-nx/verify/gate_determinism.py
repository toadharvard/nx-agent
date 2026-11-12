#!/usr/bin/env python3
"""Gate "determinism" (fnvnx-gate/1, class 9): the build of every product root is a function of the tracked tree.
    python3 -I gate_determinism.py   cwd = checkout root, env GATE_OUT, optional GATE_CACHE, optional GATE_COMMIT
Rule: build the recipe TWICE COLD (two fresh stores, no cache: every stage runs in the sandbox, tool runtimes included) and compare
every object and the ELF by sha256. Also the warm build of gate_build (GATE_CACHE/objects/<tree>/<root name>, made by gate_build.ensure,
stages served from the cache) must equal them: R12 "warm == cold". Without GATE_CACHE there is no warm build and only the cold pair
is compared (said in detail). Universe: every unit and ELF of every product root; certified: items equal in all builds; uncertified:
repo-relative unit paths. A build that fails (red) certifies nothing of what it did not build. No product root: universe 0, green.
ABIs: the rule is independent of the ABI; the toy root of tests/test_recipe.py is built and judged in LP64 and in ILP32 (`-mabi=ilp32`, ELF32 objects, `-m aarch64linux32`).
What two cold builds can and cannot expose is stated in tests/test_recipe.py (sandbox.OPEN_CHANNELS: only channels that differ
between two runs on one host are exposed: wall_clock, entropy by AT_RANDOM; host constants such as cpu count, kernel release, memory
size are equal in both builds and stay open). EXTERNALS (recipe key "external"): fetched once into a bare cache shared by the two cold builds (GATE_CACHE/external or a temp dir), but each
cold build has its own store and re-checks out, re-ingests and re-verifies tree_sha256. A shared fetch does not weaken "two cold builds":
an external is a content-pinned INPUT (commit + tree hash verified on every build), not a build output, so nothing a build computes is shared.
COST: this gate doubles the compile time of the verdict (two cold builds of every root, each also ingesting the tool runtimes into its
fresh store: measured ~4 s for the python runtime of a root with generators, ~1.5 s for the cross gcc, ~3 s more for an external compiler tree of
110 MB; a stage itself costs ~0.03 s of sandbox set-up, trees are placed once per store). A 100-unit root: ~12 s per cold build before the real
compile time, ~0.5 s cached; see the measured times in the test output."""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import recipe  # noqa: E402


def shas(res):
    d = {o["unit"]: o["sha256"] for o in res["objects"]}
    if res["elf"]:
        d["<elf>"] = res["elf"]["sha256"]
    return d


def main():
    items, good, why, repo, secs = [], [], [], ".", []
    roots, files = recipe.load_admit(repo), recipe.tracked(repo)
    cache = os.environ.get("GATE_CACHE")
    ext = os.path.join(cache, "external") if cache else tempfile.mkdtemp(prefix="gate-det-ext-")   # the bare fetch cache is shared, stores are not
    for root in gate_build.buildable_roots(repo):
        rec = recipe.load_recipe(repo, roots, root)
        pre = [root + "/" + u for u in recipe.units_of(rec, root, files)] if isinstance(rec, dict) else []
        runs = []
        try:
            for n in range(2):
                tmp, t0 = tempfile.mkdtemp(prefix="gate-det-"), time.time()
                try:
                    runs.append(recipe.build(repo, root, tmp + "/store", tmp + "/out", ext=ext))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                secs.append(round(time.time() - t0, 1))
            if cache:
                runs.append(gate_build.ensure(root, cache, repo)[0])
        except recipe.RecipeError as e:
            items += pre
            why.append("%s: invalid recipe: %s" % (root, "; ".join(e.errors)[:300]))
            continue
        a = runs[0]
        names = ["cold 1", "cold 2", "warm"]
        items += [root + "/" + u for u in a["units"]] + ([root + "/" + rec["link"]["output"]] if rec.get("link") else [])
        if any(r["status"] == "red" for r in runs):
            why.append("%s: build red in %s" % (root, ",".join(n for n, r in zip(names, runs) if r["status"] == "red")))
            continue
        ref, elf = shas(a), root + "/" + (rec["link"]["output"] if rec.get("link") else "")
        for k in sorted(ref):
            if all(shas(r).get(k) == ref[k] for r in runs):
                good.append(elf if k == "<elf>" else root + "/" + k)
            else:
                differ = [n for n, r in zip(names, runs) if shas(r).get(k) != ref[k]]
                why.append("%s differs in %s" % (k, "/".join(differ)))
    if not cache:
        shutil.rmtree(ext, ignore_errors=True)
    bad = sorted(set(items) - set(good))
    detail = "; ".join(why)[:800] or ("%d items equal in %s (cold build seconds %s)" % (len(items), "2 cold + 1 warm build" if cache else
                                                                                     "2 cold builds, no GATE_CACHE so no warm build", secs)
                                      if items else "no product root (nothing to build yet)")
    res = {"format": "fnvnx-gate/1", "name": "determinism", "status": "red" if why else "green", "universe": len(items),
           "certified": len(good), "uncertified": bad[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "determinism.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("determinism: %s %d/%d %s" % (res["status"], res["certified"], res["universe"], detail[:200]))
    return 2 if why else 0


if __name__ == "__main__":
    sys.exit(main())
