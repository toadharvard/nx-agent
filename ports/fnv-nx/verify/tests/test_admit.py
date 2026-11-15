#!/usr/bin/env python3
"""gate_admit.judge: every tracked file under exactly one root whose class has its required gates in gates.d."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gate_admit as ga  # noqa: E402

T = "ports/fnv-nx"
GATES = ["pure", "selftests", "admit"]
EVERY = GATES + [g for g in ga.REQUIRED["product"] if g not in GATES]   # the gates.d of a tree that has every gate of every class
fails = 0


def check(name, cond):
    global fails
    print(("ok   " if cond else "FAIL ") + name)
    fails += not cond


def adm(*roots):
    return {"format": "fnvnx-admit/1", "roots": [dict(r) for r in roots]}


tool = {"path": T + "/verify", "class": "tool", "entry": []}
doc = {"path": T + "/docs", "class": "doc"}
files = [T + "/verify/a.py", T + "/docs/x.md"]
yes = lambda p: True  # noqa: E731
no = lambda p: False  # noqa: E731

ref = {"path": T + "/refs", "class": "ref"}
rf = [T + "/refs/spec.md", T + "/refs/t.tsv"]
bad, err = ga.judge(files + rf, adm(tool, doc, ref), GATES, yes, set(rf))
check("a ref root whose files are all references is certified", not bad and not err)
bad, err = ga.judge(files + rf, adm(tool, doc, ref), GATES, yes, {rf[0]})
check("a ref root file that no oracle record points at is refused", list(bad) == [rf[1]] and not err)
bad, err = ga.judge(files + [T + "/refs/test_x.py", T + "/refs/build.json"], adm(tool, doc, ref), GATES, yes, {T + "/refs/test_x.py", T + "/refs/build.json"})
check("a test or a build.json in a ref root is refused even when cited", set(bad) == {T + "/refs/test_x.py", T + "/refs/build.json"})
bad, err = ga.judge(files + rf, adm(tool, doc, dict(ref, recipe=T + "/refs/build.json")), GATES, yes, set(rf))
check("a ref root with a recipe is refused (it is not a product root)", err and bad)
bad, err = ga.judge(files + rf, adm(tool, doc, dict(ref, gates=["selftests"])), GATES, yes, set(rf))
check('a row that lists "gates" is refused (the set is a function of the class)', err and bad and any("lists" in e for e in err))
bad, err = ga.judge(files, adm(dict(tool, gates=["selftests"]), doc), GATES, yes)
check('a tool row naming exactly its class gates is refused all the same', err and bad)
bad, err = ga.judge(files, adm(dict(tool, gates=[]), doc), GATES, yes)
check('an empty "gates" list is a listing too', err and bad)
check("gates_of: doc none, ref none, tool selftests+pure, a host-reader tool root selftests only, product REQUIRED_PRODUCT+pure, test REQUIRED_TEST",
      ga.gates_of(doc) == [] and ga.gates_of(ref) == [] and ga.gates_of({"path": T + "/contract", "class": "tool"}) == ["selftests", "pure"] and ga.gates_of(tool) == ["selftests"]
      and ga.gates_of({"path": T + "/x", "class": "product"}) == list(ga.REQUIRED_PRODUCT) + ["pure"] and ga.gates_of({"path": T + "/x", "class": "test"}) == list(ga.REQUIRED_TEST)
      and ga.gates_of({"class": "misc"}) == [])
bad, err = ga.judge(files, adm(tool, doc), GATES, yes)
check("two roots cover two files", not bad and not err)
bad, err = ga.judge(files + [T + "/platform/w.c"], adm(tool, doc), GATES, yes)
check("a file outside every root is uncertified", list(bad) == [T + "/platform/w.c"] and not err)
bad, err = ga.judge(files, adm(tool, doc), ["pure", "admit"], yes)
check("a tool root whose selftests gate has no file in gates.d is refused", list(bad) == [T + "/verify/a.py"] and any("selftests" in e for e in err))
prod = {"path": T + "/platform", "class": "product", "recipe": T + "/platform/build.json"}
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), GATES, yes)
check("a product root is refused while its gates do not exist", bad and err)
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), GATES + list(ga.REQUIRED["product"]), yes)
check("a product root with every required gate and a recipe is admitted", not bad and not err)
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), GATES + list(ga.REQUIRED["product"]), no)
check("a product root whose recipe file is absent is refused", bad and err)
CLASS_GATES = ("datamodel", "order", "fpexact", "eh", "exepin", "addr32", "machine")
check("classes A-G are required of a product root", all(g in ga.REQUIRED["product"] for g in CLASS_GATES))
every = GATES + list(ga.REQUIRED["product"])
for g in ga.REQUIRED["product"]:
    bad, err = ga.judge([T + "/platform/w.c"], adm(prod), [x for x in every if x != g], yes)
    check("a product root is refused while gate %s has no file in gates.d" % g, bad and any(g in e for e in err))
bad, err = ga.judge(files, adm(tool, doc, {"path": T + "/verify/tests", "class": "doc"}), GATES, yes)
check("nested roots are an error", any("overlap" in e for e in err))
bad, err = ga.judge(files, adm(dict(tool, **{"class": "misc"}), doc), GATES, yes)
check("an unknown class is refused", bad and err)
bad, err = ga.judge(files, adm(dict(tool, path="other/verify"), doc), GATES, yes)
check("a root outside the port tree is refused", err and T + "/verify/a.py" in bad)
bad, err = ga.judge([], adm(tool, doc), GATES, yes)
check("an empty tree is certified", not bad and not err)
# class test
TG = list(ga.REQUIRED_TEST)
troot = {"path": T + "/tests", "class": "test", "target": "guest", "recipe": T + "/tests/build.json"}
tf = [T + "/tests/t.cc"]
bad, err = ga.judge(tf, adm(tool, doc, troot), GATES + TG, yes)
check("a test root with its REQUIRED_TEST gates, a recipe and a target is certified (REQUIRED_PRODUCT is not asked of it)", not bad and not err)
check("REQUIRED_TEST is the build family, objstub, doubles, oracle, assumptions, pure (the stub gate is objstub; legal is a whole-tree gate)",
      set(ga.REQUIRED_TEST) == {"build", "determinism", "linkstrict", "objstub", "doubles", "oracle", "assumptions", "pure"}
      and not set(ga.REQUIRED_TEST) >= set(ga.REQUIRED_PRODUCT))
for g in TG:
    bad, err = ga.judge(tf, adm(tool, doc, troot), [x for x in GATES + TG if x != g], yes)
    check("a test root is refused while gate %s has no file in gates.d" % g, list(bad) == tf and any(g in e for e in err))
bad, err = ga.judge(tf, adm(tool, doc, troot), GATES + [g for g in TG if g != "doubles"], yes)
check("a test root cannot be admitted while the doubles gate has no file in gates.d", list(bad) == tf and any("doubles" in e for e in err))
for g in ("objstub", "doubles"):
    bad, err = ga.judge(tf, adm(tool, doc, dict(troot, gates=[x for x in TG if x != g])), GATES + TG, yes)
    check("a test root with objstub and doubles admits; one lacking %s is red" % g, list(bad) == tf and any(g in e for e in err))
check("a test root naming the old `stub` or `legal` is refused: no such file in gates.d",
      all(list(ga.judge(tf, adm(tool, doc, dict(troot, gates=TG + [g])), GATES + TG, yes)[0]) == tf for g in ("stub", "legal")))
bad, err = ga.judge(tf, adm(tool, doc, {k: v for k, v in troot.items() if k != "target"}), GATES + TG, yes)
check("a test root without a target is refused", list(bad) == tf and any("target" in e for e in err))
bad, err = ga.judge(tf, adm(tool, doc, dict(troot, target="elsewhere")), GATES + TG, yes)
check("a test root with an unknown target is refused", list(bad) == tf)
bad, err = ga.judge(tf, adm(tool, doc, troot), GATES + TG, no)
check("a test root whose recipe file does not exist is refused", list(bad) == tf and any("recipe" in e for e in err))
bad, err = ga.judge(tf, adm(tool, doc, dict(troot, **{"class": "product"})), GATES + TG, yes)
check("the same root as a product root is refused: it lacks REQUIRED_PRODUCT", list(bad) == tf)

import json  # noqa: E402
import tempfile  # noqa: E402
import atexit  # noqa: E402
import shutil  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)
import gate_build as gbd  # noqa: E402
rp = tempfile.mkdtemp(prefix="admit-roots-")
os.makedirs(rp + "/ports/fnv-nx/verify")
json.dump({"roots": [{"path": T + "/p", "class": "product"}, {"path": T + "/t", "class": "test"}, {"path": T + "/v", "class": "tool"}]},
          open(rp + "/ports/fnv-nx/verify/admit.json", "w"))
check("the build family judges the product and the test roots; the other gates keep the product roots",
      gbd.buildable_roots(rp) == [T + "/p", T + "/t"] and gbd.product_roots(rp) == [T + "/p"])
print("%d failed" % fails)
sys.exit(1 if fails else 0)
