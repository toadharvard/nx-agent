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
import cas  # noqa: E402
import gate_build  # noqa: E402
import recipe  # noqa: E402
import runtime  # noqa: E402
import sandbox  # noqa: E402
import unitstage  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def shas(res):
    d = {o["unit"]: o["sha256"] for o in res["objects"]}
    if res["elf"]:
        d["<elf>"] = res["elf"]["sha256"]
    return d


def judge_root(repo, root, rec, roots, files, ext, cache, warm):
    """-> {"items", "good", "why", "secs"} of one root: two cold builds in fresh stores (and WARM, the published build of gate_build.ensure) compared by sha256."""
    items, good, why, secs, ran = [], [], [], [], []
    pre = [root + "/" + u for u in recipe.units_of(rec, recipe.split_spec(root)[0], files)] if isinstance(rec, dict) else []
    runs = []
    try:
        if warm is not None and cache:
            # Incremental: the second execution lives in a store of its own (GATE_CACHE/detstore, never the store of the build under judgement), whose entries were
            # all made by this gate. A stage whose key is in it was executed here once and its object compared with the build's: it is not executed again; a stage
            # with a new key (new source, flags, compiler pin) is executed exactly once. The final objects are compared on every run.
            tmp, t0 = tempfile.mkdtemp(prefix="gate-det-"), time.time()
            try:
                os.makedirs(os.path.join(cache, "detstore"), exist_ok=True)
                second = recipe.build(repo, root, os.path.join(cache, "detstore"), tmp + "/out", ext=ext)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            secs.append(round(time.time() - t0, 1))
            runs = [second, warm]
            ran.append((second.get("stages") or {}).get("run", 0))
        else:
            for n in range(2):
                tmp, t0 = tempfile.mkdtemp(prefix="gate-det-"), time.time()
                try:
                    runs.append(recipe.build(repo, root, tmp + "/store", tmp + "/out", ext=ext))
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                secs.append(round(time.time() - t0, 1))
            if warm is not None:
                runs.append(warm)
    except recipe.RecipeError as e:
        return {"items": pre, "good": [], "why": ["%s: invalid recipe: %s" % (root, "; ".join(e.errors)[:300])], "secs": secs, "ran": ran}
    a = runs[0]
    names = ["det", "warm"] if len(runs) == 2 else ["cold 1", "cold 2", "warm"]
    items += [root + "/" + u for u in a["units"]] + ([root + "/" + rec["link"]["output"]] if rec.get("link") else [])
    if any(r["status"] == "red" for r in runs):
        return {"items": items, "good": [], "why": ["%s: build red in %s" % (root, ",".join(n for n, r in zip(names, runs) if r["status"] == "red"))], "secs": secs, "ran": ran}
    ref, elf = shas(a), root + "/" + (rec["link"]["output"] if rec.get("link") else "")
    for k in sorted(ref):
        if all(shas(r).get(k) == ref[k] for r in runs):
            good.append(elf if k == "<elf>" else root + "/" + k)
        else:
            differ = [n for n, r in zip(names, runs) if shas(r).get(k) != ref[k]]
            why.append("%s differs in %s" % (k, "/".join(differ)))
    return {"items": items, "good": good, "why": why, "secs": secs, "ran": ran}


def main():
    items, good, why, repo, secs, ran = [], [], [], ".", [], []
    roots, files = recipe.load_admit(repo), recipe.tracked(repo)
    cache = os.environ.get("GATE_CACHE")
    ext = os.path.join(cache, "external") if cache else tempfile.mkdtemp(prefix="gate-det-ext-")   # the bare fetch cache is shared, stores are not
    for root in gate_build.buildable_roots(repo):
        rec = recipe.load_recipe(repo, roots, root)
        warm = None
        try:
            if cache:
                warm = gate_build.ensure(root, cache, repo)[0]
        except recipe.RecipeError as e:
            r = {"items": [root + "/" + u for u in recipe.units_of(rec, recipe.split_spec(root)[0], files)] if isinstance(rec, dict) else [], "good": [],
                 "why": ["%s: invalid recipe: %s" % (root, "; ".join(e.errors)[:300])], "secs": []}
            items += r["items"]
            why += r["why"]
            continue
        if warm is not None and warm["status"] == "green":
            # The claim about a root is that two cold builds equal the warm one. A stage key covers every input of its stage (the content it reads, its tool
            # runtime, flags and environment), so the claim is a function of the root's stage keys and of the code that executes and stores stages: that code's
            # digest is in the key, and so is env.json. A hit is the claim already proven for exactly these stages.
            key = {"gate": "determinism", "root": root, "stage_keys": sorted(warm["stage_keys"]), "pp_keys": sorted(warm.get("pp_keys", {}).items()), "du_keys": sorted(warm.get("du_keys", {}).items()),
                   "objects": [[o["unit"], o["sha256"]] for o in warm["objects"]], "elf": warm["elf"] and warm["elf"]["sha256"],
                   "tools": gate_build.tool_digest(__file__, recipe, sandbox, unitstage, runtime, cas), "env": gate_build.file_sha(os.path.join(HERE, "env.json"))}
            r, hit = gate_build.memo(cache, "determinism", key, lambda: judge_root(repo, root, rec, roots, files, ext, cache, warm), keep=lambda v: not v["why"])
        else:
            r = judge_root(repo, root, rec, roots, files, ext, cache, warm)
        if warm is not None and warm["status"] == "green" and hit:
            r = dict(r, ran=[])   # a memo hit executes nothing
        items += r["items"]
        good += r["good"]
        why += r["why"]
        secs += r["secs"]
        ran += r.get("ran", [])
    if not cache:
        shutil.rmtree(ext, ignore_errors=True)
    bad = sorted(set(items) - set(good))
    detail = "; ".join(why)[:800] or ("%d items equal in %s (cold build seconds %s)" % (len(items), "the build and its second execution (detstore: %d stages executed)" % sum(ran) if cache else
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
