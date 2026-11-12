#!/usr/bin/env python3
"""Gate "objstub" (fnvnx-gate/1, classes 1 and 2 of VERDICT.md): no built function holds a trap/abort path or is vacuous (rules and proofs: objstub.py).
    python3 -I gate_objstub.py [--jobs N]     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/objstub.json
Roots come from verify/admit.json, never from arguments: for every product root the gate calls gate_build.ensure(root, GATE_CACHE) (build or
cache hits) and judges what the recipe says (RECIPE.md): the objects (os_layer ones may hold SVC), the constants table (key (unit, symbol))
and the terminator set DERIVED from the recipe's compiler: objstub.unit_terminators runs the compiler's own plugin over the preprocessed text
of every C/C++ unit (the stage result unit.ii of the build, found by the pp_key in objects.json), so the set is a function of the compiler,
its flags and every header the unit really includes (a function of the compiler and the header set; the finer key is the preprocessor stage
key). The set is cached per unit under GATE_CACHE/objstub. No product root: universe 0, green.
Wine rows: verify/wine_fixme.tsv of the tree under test (objstub.read_wine_table; missing or malformed is a gate error, red); the result
holds "wine_fixme": {id: call sites} so growth of the simplified bodies is visible.
Declared-machine rows: verify/declared_machine.tsv of the tree under test (objstub.read_declared_table; missing or malformed is a gate error, red),
judged by the same rule as the Wine rows through `fnvnx_declared_machine(id)`; the result holds "declared_machine": {id: call sites}.
Reach facts: GATE_CACHE/reach/<git tree>/reach.facts.tsv (rows fact_id<TAB>sha256, written by the gate `reach`, which does not exist yet).
While the file is absent, every call of fnvnx_reach_violation is the finding `reach-uncertified`.
Universe: every function of every object of every product root, every uncovered gap, and a root whose recipe is invalid or whose build is
red (one item `root`, uncertified). Certified: items with no finding. uncertified: the first 50 `unit path:symbol`, sorted. Per object results
are cached by (object sha256, os_layer flag, analysis version, the terminators the object mentions) under GATE_CACHE/objstub/<key>.json;
uncached objects run in --jobs processes (default os.cpu_count()). Exit 0 iff green; unreadable input is red with the reason in detail."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import objstub  # noqa: E402
import recipe  # noqa: E402
import unitstage  # noqa: E402


def root_check(root, repo, cache, jobs):
    """-> (results {unit path: analysis}, rows {(unit path, symbol): row}, os_layer ids, root-level findings [(id, name, kind, detail)],
    the root's failure function (recipe error_abi.fail) or None, the root's handler records (recipe empty_handlers) or None)"""
    rec = recipe.load_recipe(repo, recipe.load_admit(repo), root)
    try:
        res, _ = gate_build.ensure(root, cache, repo)
    except recipe.RecipeError as e:
        return {}, {}, set(), [("-", root, "recipe-invalid", "; ".join(e.errors)[:200])], None, None
    if res["status"] == "red":
        return {}, {}, set(), [("-", root, "build-red", "stage %s" % res["failure"]["stage"])], None, None
    base = root + "/"
    objs = [(base + o["unit"], o["object"]) for o in res["objects"]]
    objs += [("%s%s(%s)" % (base, m["archive"].lstrip("/"), m["member"]), m["object"]) for m in res.get("link_archives", [])]   # pulled archive members
    drv = dict(zip(("c", "c++"), recipe.host_compilers(rec, cache)))
    store, names = os.path.join(cache, "store"), set()
    for lang, ext in (("c", recipe.CC_EXT), ("c++", recipe.CXX_EXT)):
        us = [(k, unitstage.result_file(store, k, "unit.ii"), lang) for u, k in sorted(res["pp_keys"].items()) if u.endswith(ext)]
        if us:
            names |= set(objstub.unit_terminators(drv[lang], us, {"c": rec["flags"]["c"], "c++": rec["flags"]["cxx"]}, cache, jobs)[0])
    os_ids = {base + o["unit"] for o in res["objects"] if o["os_layer"]}
    rows = objstub.read_rows(res["constants"], base) if res["constants"] else {}
    eh = rec.get("empty_handlers")
    records = objstub.read_handler_records(os.path.join(repo, eh["cover"]), os.path.join(repo, eh["syntax"])) if eh else None
    fail = (rec.get("error_abi") or {}).get("fail")
    return objstub.analyse_all(objs, names, os_ids, cache, jobs), rows, os_ids, [], fail, records


def main(argv):
    jobs = int(argv[argv.index("--jobs") + 1]) if "--jobs" in argv else os.cpu_count() or 1
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-objstub-")
    cache, repo = os.environ.get("GATE_CACHE") or tmp, "."
    stats = {}   # counts of the rule-H certifications and of the trap bodies
    items, finds, why, results, rows, roots = [], [], "", {}, {}, gate_build.buildable_roots(repo)
    fails, handlers = {}, {}   # {object id: the root's failure function}, {object id: the root's handler records}
    try:
        wine = objstub.read_wine_table(os.path.join(repo, "ports/fnv-nx/verify/wine_fixme.tsv"))   # the table of the tree under test
        declared = objstub.read_declared_table(os.path.join(repo, "ports/fnv-nx/verify/declared_machine.tsv"))   # the table of the tree under test
        facts = objstub.read_facts(os.path.join(cache, "reach", gate_build.tree_of(repo), "reach.facts.tsv")) if roots else None
        for root in roots:
            r, w, _, fs, fail, records = root_check(root, repo, cache, jobs)
            results.update(r)
            fails.update({o: fail for o in r} if fail else {})
            handlers.update({o: records for o in r} if records else {})
            if fail:
                fs = fs + objstub.error_abi_findings(r, list(r), fail)
            rows.update(w)
            finds += fs
            items += [(i, n) for i, n, _, _ in fs]
        its, fs = objstub.judge(results, rows, facts, wine, declared, fails, handlers, stats)
        items, finds = items + its, finds + fs
    except (ValueError, KeyError, OSError) as e:
        why = "input error: %s" % (str(e)[:300] or type(e).__name__)
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    bad = sorted({"%s:%s" % (o, s) if o != "-" else s for o, s, _, _ in finds})
    func_bad = {(o, s) for o, s, _, _ in finds}
    cert = len([it for it in items if it not in func_bad])
    kinds = {}
    for _, _, k, _ in finds:
        kinds[k] = kinds.get(k, 0) + 1
    if why:
        status, detail = "red", why
    elif not roots:
        status, detail = "green", "no product root (nothing to certify yet)"
    elif finds:
        status, detail = "red", "%d findings: %s" % (len(finds), ", ".join("%s %d" % kv for kv in sorted(kinds.items())))
    else:
        status, detail = "green", "%d functions in %d objects of %d roots, no trap path, no vacuous body" % (len(items), len(results), len(roots))
    res = {"format": "fnvnx-gate/1", "name": "objstub", "status": status, "universe": len(items), "certified": cert,
           "uncertified": bad[:50], "detail": detail, "wine_fixme": {str(k): v for k, v in objstub.wine_counts(results).items()},
           "declared_machine": {str(k): v for k, v in objstub.declared_counts(results).items()},
           "trap_body": stats.get("trap_body", 0), "handler_justified": {k[8:]: v for k, v in sorted(stats.items()) if k.startswith("handler_")}}
    with open(os.path.join(os.environ["GATE_OUT"], "objstub.json"), "w") as fh:
        json.dump(res, fh, sort_keys=True)
    print("objstub: %s %d/%d %s" % (status, cert, len(items), detail[:200]))
    return 0 if status == "green" else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
