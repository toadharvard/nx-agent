#!/usr/bin/env python3
"""Gate "fixtures" (fnvnx-gate/1): every fixture of every product root's recipe (build.json "fixtures", RECIPE.md) is judged by the gate it names.
    python3 -I gate_fixtures.py     cwd = checkout root, env GATE_OUT, optional GATE_CACHE and GATE_COMMIT; writes GATE_OUT/fixtures.json
A fixture {gate G, expect red, finding CODE} on unit U passes iff G, run in fixture mode, reports exactly the finding CODE on U and no other
finding on U, and nothing on any other item. Fixture mode: G's own argv from verify/gates.d with env GATE_FIXTURES=1 and a private GATE_OUT;
gate_build.ensure() then returns each root with the fixture units as its only objects (gate_build.fixture_view), and G's result file must
carry "findings": [{"id": "<root>/<unit>[:<symbol>]", "code": CODE}]. A fixture that comes out green, or red with another finding, or a gate
that writes no "findings", is red. Whole-root fixtures: an admit entry of class `fixture` with "expect": [{gate, code}]; each expected gate is run on that root alone (env GATE_ONLY_ROOT, normal
mode) and its findings under the root must be exactly the expected codes of that gate. Universe: the fixtures of all roots, one item per fixture root. Certified: those that pass. uncertified: `<root>/<unit>` (<= 50).
No fixture: universe 0, green. The fixture objects are in no other gate's universe (units_of and objects.json exclude them)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_build  # noqa: E402
import gatesdir  # noqa: E402
import recipe  # noqa: E402


def run_gate(g, cache, only=None):
    """-> (findings list or None, reason). One run of gate spec G in fixture mode; with ONLY (a fixture root) the gate runs on that root alone, in normal mode."""
    out = tempfile.mkdtemp(prefix="gate-fixture-")
    try:
        env = dict(os.environ, GATE_OUT=out, GATE_CACHE=cache)
        env.pop("GATE_KEEP", None)
        env.pop("GATE_ONLY_ROOT", None)
        env.pop("GATE_FIXTURES", None)
        if only:
            env["GATE_ONLY_ROOT"] = only
        else:
            env["GATE_FIXTURES"] = "1"
        p = subprocess.run(g["argv"], env=env, capture_output=True, text=True)
        try:
            r = json.load(open(os.path.join(out, g["result"])))
        except (OSError, ValueError):
            return None, "no result file (exit %d)" % p.returncode
        f = r.get("findings") if isinstance(r, dict) else None
        if not (isinstance(f, list) and all(isinstance(x, dict) and isinstance(x.get("id"), str) and isinstance(x.get("code"), str) for x in f)):
            return None, 'result has no "findings" [{id, code}] (a gate must support fixture mode)'
        return f, ""
    finally:
        shutil.rmtree(out, ignore_errors=True)


def under_id(i, p):
    return i == p or i.startswith(p + ":")


def judge(fixtures, found):
    """FIXTURES [(id, gate, finding)], FOUND {gate: (findings, reason)} -> (failed fixture ids, reasons, gates with findings outside the fixtures)."""
    bad, why, stray = [], [], []
    mine = {}
    for i, g, _ in fixtures:
        mine.setdefault(g, []).append(i)
    for i, g, want in fixtures:
        fs, reason = found[g]
        if fs is None:
            bad.append(i)
            why.append("%s: gate %s: %s" % (i, g, reason))
            continue
        codes = sorted({f["code"] for f in fs if under_id(f["id"], i)})
        if codes != [want]:
            bad.append(i)
            why.append("%s: gate %s expected exactly %s, got %s" % (i, g, want, codes or "green (no finding)"))
    for g, ids_ in mine.items():
        fs = found[g][0] or []
        extra = sorted({f["id"] for f in fs if not any(under_id(f["id"], i) for i in ids_)})
        if extra:
            stray.append(g)
            why.append("gate %s: findings outside the fixtures: %s" % (g, extra[:5]))
    return sorted(set(bad)), why, stray


def judge_root(root, expect, found):
    """EXPECT [{gate, code}], FOUND {gate: (findings, reason)} of runs on ROOT alone -> [reasons]. Per gate: exactly the expected codes, no other."""
    why = []
    for g in sorted({x["gate"] for x in expect}):
        fs, reason = found[g]
        if fs is None:
            why.append("%s: gate %s: %s" % (root, g, reason))
            continue
        want = sorted({x["code"] for x in expect if x["gate"] == g})
        got = sorted({f["code"] for f in fs if f["id"].startswith(root + "/")})
        stray = sorted({f["id"] for f in fs if not f["id"].startswith(root + "/")})
        if got != want or stray:
            why.append("%s: gate %s expected exactly %s, got %s%s" % (root, g, want, got or "green (no finding)", "; outside the root: %s" % stray[:3] if stray else ""))
    return why


def main():
    repo = "."
    tmp = None if os.environ.get("GATE_CACHE") else tempfile.mkdtemp(prefix="gate-fixtures-")
    cache = os.environ.get("GATE_CACHE") or tmp
    roots, fixtures, why, bad, stray = recipe.load_admit(repo), [], [], [], []
    try:
        gates = {g["name"]: g for g in gatesdir.load_dir(os.path.join(repo, gatesdir.DIR))["gates"]}
        for root in gate_build.product_roots(repo):
            rec = recipe.load_recipe(repo, roots, root)
            fixtures += [("%s/%s" % (root, u), rec["fixtures"][u]["gate"], rec["fixtures"][u]["finding"]) for u in sorted(recipe.fixtures_of(rec))]
        found = {g: run_gate(gates[g], cache) for g in sorted({g for _, g, _ in fixtures}) if g in gates}
        for _, g, _ in fixtures:
            found.setdefault(g, (None, "not a gate of gates.d"))
        bad, why, stray = judge(fixtures, found)
        for root, ent in sorted((p, r) for p, r in roots.items() if r["class"] == "fixture"):   # whole-root fixtures: each expected gate runs on that root alone
            ids_ = "%s (root)" % root
            fixtures.append((ids_, "", ""))
            rf = {x["gate"]: run_gate(gates[x["gate"]], cache, root) if x["gate"] in gates else (None, "not a gate of gates.d") for x in ent["expect"]}
            w = judge_root(root, ent["expect"], rf)
            if w:
                bad.append(ids_)
                why += w
    finally:
        if tmp and not os.environ.get("GATE_KEEP"):
            shutil.rmtree(tmp, ignore_errors=True)
    detail = "; ".join(why)[:900] or ("%d fixtures, each red with exactly its declared finding" % len(fixtures) if fixtures else "no fixture")
    res = {"format": "fnvnx-gate/1", "name": "fixtures", "status": "red" if bad or stray else "green", "universe": len(fixtures),
           "certified": len(fixtures) - len(bad), "uncertified": bad[:50], "detail": detail}
    with open(os.path.join(os.environ["GATE_OUT"], "fixtures.json"), "w") as f:
        json.dump(res, f, sort_keys=True)
    print("fixtures: %s %d/%d %s" % (res["status"], res["certified"], res["universe"], detail[:200]))
    return 2 if bad or stray else 0


if __name__ == "__main__":
    sys.exit(main())
