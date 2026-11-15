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


def report(name, unit, findings=(), sites=None, ranged=(), rewritten=0, helper=0, extra=None):
    p = os.path.join(TMP, name)
    findings, ranged = list(findings), list(ranged)
    d = {"format": "fnvnx-f2i/2", "unit": unit, "helpers": [], "findings": findings, "ranged": ranged, "rewritten": rewritten, "helper": helper,
         "sites": rewritten + len(ranged) + helper + len(findings) if sites is None else sites}
    d.update(extra or {})
    json.dump(d, open(p, "w"))
    return p


def site(kind, **kw):
    d = {"function": "g", "file": "a.c", "line": 3, "kind": kind}
    d.update(kw)
    return d


def fact(lo, hi, itype="i32", conv="trunc", nonan=True, **kw):
    d = {"source": "gcc-range", "itype": itype, "conv": conv, "lo": lo, "hi": hi, "nonan": nonan}
    d.update(kw)
    return d


def rng(f):
    return codes(report("rg_%d.json" % len(os.listdir(TMP)), recipe.sandbox_src("a.c"), ranged=[site("conv-scalar", fact=f)]))


def codes(f2i_path, unit="a.c"):
    return sorted(c for c, _ in G.judge_object_coded("r/" + unit, obj, False, unit, f2i_path)["f2i:r/" + unit])


good = report("good.json", recipe.sandbox_src("a.c"))
check("correct row: the report of the unit, no finding: green", codes(good) == [], codes(good))
check("row without f2i: red f2i-report-missing", codes(None) == ["f2i-report-missing"], codes(None))
check("row naming a missing file: red f2i-report-missing", codes(os.path.join(TMP, "nope.json")) == ["f2i-report-missing"])
check("a report next to the object is not looked at (the old path obj + .f2i.json)", (open(obj + ".f2i.json", "w").write(open(good).read()) or True) and codes(None) == ["f2i-report-missing"])
check("unit mismatch (another unit's report): red f2i-report-unit", codes(report("other.json", recipe.sandbox_src("b.c"))) == ["f2i-report-unit"])
check("the bare unit name is a mismatch (the plugin reports the sandbox path, nothing is stripped)", codes(report("bare.json", "a.c")) == ["f2i-report-unit"])
check("a finding of the plugin: red f2i-conv-scalar", codes(report("fnd.json", recipe.sandbox_src("a.c"), [site("conv-scalar")])) == ["f2i-conv-scalar"])
SRC = recipe.sandbox_src("a.c")
for kind in ("conv-scalar", "conv-vector", "builtin", "mdbuiltin", "asm"):
    check("an unrewritten site of kind %s is red f2i-%s (no kind is exempt)" % (kind, kind), codes(report("k_%s.json" % kind, SRC, [site(kind)])) == ["f2i-" + kind])
check("rewritten sites (counted) are green", codes(report("rw.json", SRC, rewritten=3)) == [])
check("rewritten sites listed per site, count matching: green", codes(report("rw2.json", SRC, rewritten=2, extra={"rewrites": [site("conv-scalar"), site("builtin")]})) == [])
check("extra keys in the entries of rewrites (how, code) are accepted", codes(report("rw4.json", SRC, rewritten=2, extra={"rewrites": [site("conv-scalar", how="msvc/ftol", code=0), site("conv-vector", how="md/trunc", code=1234)]})) == [])
check("rewrites listed with a count that differs: red (malformed)", codes(report("rw3.json", SRC, rewritten=2, extra={"rewrites": [site("conv-scalar")]})) == ["f2i-report-missing"])
check("a helper site is green", codes(report("hp.json", SRC, helper=2)) == [])
check("a ranged site with a missing fact is red (malformed)", codes(report("rg5.json", SRC, ranged=[site("conv-scalar")])) == ["f2i-report-missing"])
# range facts: the gate computes the specified set from itype and conv; the report carries no bound
M31, M32, M63, M64 = 2 ** 31, 2 ** 32, 2 ** 63, 2 ** 64
cases = [
    ("trunc i32 inside", fact(-2147483648.5, 2147483647.5), []), ("trunc i32 at -2^31-1 (excluded)", fact(-M31 - 1, 5), ["f2i-range"]),
    ("trunc i32 hi = 2^31 (excluded)", fact(0, M31), ["f2i-range"]), ("trunc i32 hi just under 2^31", fact(0, 2147483647.9999998), []),
    ("trunc u32 lo -0.5 inside", fact(-0.5, 4294967295.5, "u32"), []), ("trunc u32 lo -1 excluded", fact(-1, 5, "u32"), ["f2i-range"]),
    ("trunc u32 hi 2^32 excluded", fact(0, M32, "u32"), ["f2i-range"]), ("floor i32 lo = -2^31 included", fact(-M31, 5, conv="floor"), []),
    ("floor i32 lo below -2^31", fact(-M31 - 0.5, 5, conv="floor"), ["f2i-range"]), ("floor i32 hi 2^31 excluded", fact(0, M31, conv="floor"), ["f2i-range"]),
    ("floor i32 hi 2^31-0.5 included", fact(0, M31 - 0.5, conv="floor"), []), ("ceil i32 hi = 2^31-1 included", fact(0, M31 - 1, conv="ceil"), []),
    ("ceil i32 hi 2^31-0.5 excluded", fact(0, M31 - 0.5, conv="ceil"), ["f2i-range"]), ("ceil i32 lo -2^31 - 0.5 included", fact(-M31 - 0.5, 0, conv="ceil"), []),
    ("ceil i32 lo -2^31-1 excluded", fact(-M31 - 1, 0, conv="ceil"), ["f2i-range"]), ("round i32 hi 2^31-0.5 excluded", fact(0, M31 - 0.5, conv="round"), ["f2i-range"]),
    ("round i32 hi 2^31-0.75 included", fact(0, M31 - 0.75, conv="round"), []), ("round i32 lo -2^31-0.5 excluded", fact(-M31 - 0.5, 0, conv="round"), ["f2i-range"]),
    ("round i32 lo -2^31-0.25 included", fact(-M31 - 0.25, 0, conv="round"), []), ("rint i32 inside [mn, mx]", fact(-M31, M31 - 1, conv="rint"), []),
    ("rint i32 hi 2^31-0.5 excluded (rounding up gives 2^31)", fact(0, M31 - 0.5, conv="rint"), ["f2i-range"]),
    ("rint i32 lo -2^31-0.5 excluded (rounding down gives -2^31-1)", fact(-M31 - 0.5, 0, conv="rint"), ["f2i-range"]),
    ("trunc i64 inside", fact(-1e18, 1e18, "i64"), []), ("trunc i64 hi 2^63 excluded", fact(0, M63, "i64"), ["f2i-range"]),
    ("trunc u64 hi 2^64 excluded", fact(0, M64, "u64"), ["f2i-range"]), ("trunc i8 hi 128 excluded", fact(0, 128, "i8"), ["f2i-range"]),
    ("trunc i8 hi 127.5 included", fact(0, 127.5, "i8"), []), ("trunc u8 lo -1 excluded", fact(-1, 0, "u8"), ["f2i-range"]),
    ("trunc i16 inside", fact(-32768, 32767, "i16"), []), ("trunc u16 hi 65536 excluded", fact(0, 65536, "u16"), ["f2i-range"]),
    ("nonan false is red", fact(0, 1, nonan=False), ["f2i-range"]), ("lo > hi is red", fact(5, 1), ["f2i-range"]),
]
for name, f, want in cases:
    check("range fact: %s -> %s" % (name, want or "green"), rng(f) == want, rng(f))
for name, f in (("nonan missing", {k: v for k, v in fact(0, 1).items() if k != "nonan"}), ("nonan not a boolean", fact(0, 1, nonan="true")),
                ("a bound in the report", fact(0, 1, bound_lo=-5, bound_hi=5)), ("source flag", fact(0, 1, source="flag")),
                ("itype outside the table", fact(0, 1, itype="i128")), ("conv outside the table", fact(0, 1, conv="nearest")),
                ("non-real lo", fact("0", 1)), ("bool lo", fact(True, 1))):
    check("range fact: %s is malformed, red" % name, rng(f) == ["f2i-report-missing"], rng(f))
check("range fact: a ranged site with no fact is malformed, red", codes(report("nofact.json", recipe.sandbox_src("a.c"), ranged=[site("conv-scalar")])) == ["f2i-report-missing"])
check("specified_set trunc i32 is the open interval (-2^31-1, 2^31)", G.specified_set("i32", "trunc") == (-M31 - 1, True, M31, True))
check("counts that do not add up are red (malformed)", codes(report("cnt.json", SRC, sites=4, rewritten=3)) == ["f2i-report-missing"])
check("a /1 report is red (malformed)", codes(report("v1.json", SRC, extra={"format": "fnvnx-f2i/1"})) == ["f2i-report-missing"])
check("a kind that is not one of the five is red (malformed)", codes(report("kk.json", SRC, [site("conv")])) == ["f2i-report-missing"])
check("a range-proven site with a finding beside it: only the finding is red", codes(report("mix.json", SRC, [site("mdbuiltin")], ranged=[site("conv-scalar", fact=fact(0, 1))])) == ["f2i-mdbuiltin"])
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
