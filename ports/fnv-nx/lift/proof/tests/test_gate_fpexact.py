#!/usr/bin/env python3
"""Tests of the f2i reader of lift/proof/gate_fpexact.py (check 3a): the path comes from the `f2i` field of the object's row in objects.json, the
unit the plugin reports is recipe.sandbox_src(unit). Plain script: one PASS/FAIL line per case, exit 0 on pass. The identity case builds the toy
root of verify/tests/test_recipe.py with the real recipe and reads the stage documents and the preprocessed text it produced."""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.normpath(os.path.join(HERE, "..", ".."))
VERIFY = os.path.normpath(os.path.join(LIFT, "..", "verify"))
for p in (os.path.join(LIFT, "proof"), os.path.join(VERIFY, "tests"), VERIFY, os.path.join(LIFT, ".."), os.path.join(LIFT, "..", "contract")):
    sys.path.insert(0, p)
import scratch  # noqa: E402

scratch.install()
import gate_build  # noqa: E402
import gate_fpexact as G  # noqa: E402
import recipe  # noqa: E402
import test_recipe as TR  # noqa: E402
import unitstage  # noqa: E402

TMP = tempfile.mkdtemp(prefix="fpexact-")
FAILED = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + str(extra)[:500]))
    if not cond:
        FAILED.append(name)


obj = os.path.join(TMP, "a.o")
open(os.path.join(TMP, "a.c"), "w").write("int f(int x) { return x + 1; }\n")
subprocess.run(["aarch64-linux-gnu-gcc", "-mabi=ilp32", "-O2", "-c", os.path.join(TMP, "a.c"), "-o", obj], check=True, capture_output=True)


def report(name, unit, findings=(), sites=0):
    p = os.path.join(TMP, name)
    json.dump({"format": "fnvnx-f2i/1", "unit": unit, "sites": sites, "helpers": [], "findings": list(findings)}, open(p, "w"))
    return p


def codes(f2i_path, unit="a.c"):
    return sorted(c for c, _ in G.judge_object_coded("r/" + unit, obj, False, unit, f2i_path)["f2i:r/" + unit])


good = report("good.json", recipe.sandbox_src("a.c"))
check("correct row: the report of the unit, no finding: green", codes(good) == [], codes(good))
check("row without f2i: red f2i-report-missing", codes(None) == ["f2i-report-missing"], codes(None))
check("row naming a missing file: red f2i-report-missing", codes(os.path.join(TMP, "nope.json")) == ["f2i-report-missing"])
check("a report next to the object is not looked at (the old path obj + .f2i.json)", (open(obj + ".f2i.json", "w").write(open(good).read()) or True) and codes(None) == ["f2i-report-missing"])
check("unit mismatch (another unit's report): red f2i-report-unit", codes(report("other.json", recipe.sandbox_src("b.c"))) == ["f2i-report-unit"])
check("the bare unit name is a mismatch (the plugin reports the sandbox path, nothing is stripped)", codes(report("bare.json", "a.c")) == ["f2i-report-unit"])
check("a finding of the plugin: red f2i-conv", codes(report("fnd.json", recipe.sandbox_src("a.c"), [{"function": "g", "file": "a.c", "line": 3, "kind": "conv"}], 1)) == ["f2i-conv"])
bad = os.path.join(TMP, "bad.json")
open(bad, "w").write("{")
check("malformed report: red f2i-report-missing", codes(bad) == ["f2i-report-missing"])
res = {"objects": [{"unit": "a.c", "object": obj, "f2i": good}, {"unit": "b.c", "object": obj}]}
check("read_f2i_paths: the f2i field of each row, None for a row without", G.read_f2i_paths(res) == {"a.c": good, "b.c": None}, G.read_f2i_paths(res))
its = G.judge_objects_coded("r", G.read_inputs(res), None, None, None, G.read_f2i_paths(res))
check("judge_objects_coded takes the path per unit: a.c clean, b.c (no f2i) red", its["f2i:r/a.c"] == [] and [c for c, _ in its["f2i:r/b.c"]] == ["f2i-report-missing"], its)

# identity: the sandbox function is the path the recipe's stages pass and the preprocessed text names
check("sandbox_src is one function: recipe.sandbox_src is unitstage.sandbox_src", recipe.sandbox_src is unitstage.sandbox_src)
src = open(os.path.join(VERIFY, "unitstage.py")).read()
check("unitstage composes no /src/ path but in sandbox_src", len(re.findall(r'"/src/"', src)) == 1, re.findall(r'.*"/src/".*', src))
repo = TR.make_repo("fpexact-identity")
cache = os.path.join(TMP, "cache")
res, out = gate_build.ensure(TR.ROOT, cache, repo)
store = os.path.join(cache, "store")
docs = {}
for k in res["stage_keys"]:
    p = os.path.join(store, "stage-docs", k[:2], k + ".json")
    if os.path.isfile(p):
        d = json.load(open(p))
        docs[d["stage"]] = d
cunits = [o["unit"] for o in res["objects"] if o["unit"].endswith((".c", ".cc", ".cpp"))]
check("the toy root has C units to compare", len(cunits) > 0, res["objects"])
check("the PP stage of every C/C++ unit passes recipe.sandbox_src(unit) as its input", all(docs.get("pp:" + u, {}).get("argv", [""])[-1] == recipe.sandbox_src(u) for u in cunits),
      {u: docs.get("pp:" + u, {}).get("argv", [""])[-1] for u in cunits})
ok = True
for u, k in res["pp_keys"].items():
    if u in cunits:
        first = open(unitstage.result_file(store, k, "unit.ii")).read(400)
        ok = ok and ('"%s"' % recipe.sandbox_src(u)) in first
check("the preprocessed text (what the CC stage compiles, the plugin's input name) names recipe.sandbox_src(unit)", ok)
print("FAILED: %s" % FAILED if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
