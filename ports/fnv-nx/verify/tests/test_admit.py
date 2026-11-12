#!/usr/bin/env python3
"""gate_admit.judge: every tracked file under exactly one root whose class has its required gates in gates.d."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gate_admit as ga  # noqa: E402

T = "ports/fnv-nx"
GATES = ["pure", "selftests", "admit"]
fails = 0


def check(name, cond):
    global fails
    print(("ok   " if cond else "FAIL ") + name)
    fails += not cond


def adm(*roots):
    return {"format": "fnvnx-admit/1", "roots": [dict(r) for r in roots]}


tool = {"path": T + "/verify", "class": "tool", "gates": ["selftests"]}
doc = {"path": T + "/docs", "class": "doc", "gates": []}
files = [T + "/verify/a.py", T + "/docs/x.md"]
yes = lambda p: True  # noqa: E731
no = lambda p: False  # noqa: E731

ref = {"path": T + "/refs", "class": "ref", "gates": []}
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
check("a ref root that names a gate is refused", err and bad)
bad, err = ga.judge(files, adm(tool, doc), GATES, yes)
check("two roots cover two files", not bad and not err)
bad, err = ga.judge(files + [T + "/platform/w.c"], adm(tool, doc), GATES, yes)
check("a file outside every root is uncertified", list(bad) == [T + "/platform/w.c"] and not err)
bad, err = ga.judge(files, adm(dict(tool, gates=[]), doc), GATES, yes)
check("a tool root without selftests is refused", list(bad) == [T + "/verify/a.py"] and err)
bad, err = ga.judge(files, adm(dict(tool, gates=["selftests", "ghost"]), doc), GATES, yes)
check("a named gate missing from gates.d is refused", bad and "ghost" in err[0])
prod = {"path": T + "/platform", "class": "product", "recipe": T + "/platform/build.json",
        "gates": list(ga.REQUIRED["product"])}
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), GATES, yes)
check("a product root is refused while its gates do not exist", bad and err)
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), GATES + list(ga.REQUIRED["product"]), yes)
check("a product root with every required gate and a recipe is admitted", not bad and not err)
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), GATES + list(ga.REQUIRED["product"]), no)
check("a product root whose recipe file is absent is refused", bad and err)
bad, err = ga.judge([T + "/platform/w.c"], adm(dict(prod, gates=["objstub"])), GATES + list(ga.REQUIRED["product"]), yes)
check("a product root naming only some required gates is refused", bad and err)
CLASS_GATES = ("datamodel", "order", "fpexact", "eh", "exepin", "addr32", "machine")
check("classes A-G are required of a product root", all(g in ga.REQUIRED["product"] for g in CLASS_GATES))
every = GATES + list(ga.REQUIRED["product"])
for g in ga.REQUIRED["product"]:
    bad, err = ga.judge([T + "/platform/w.c"], adm(dict(prod, gates=[x for x in prod["gates"] if x != g])), every, yes)
    check("a product root lacking required gate %s is refused" % g, bad and any(g in e for e in err))
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), GATES + [g for g in ga.REQUIRED["product"] if g != "eh"], yes)
check("a product root naming a gate absent from gates.d is refused", bad and any("eh" in e for e in err))
bad, err = ga.judge(files, adm(tool, doc, {"path": T + "/verify/tests", "class": "doc", "gates": []}), GATES, yes)
check("nested roots are an error", any("overlap" in e for e in err))
bad, err = ga.judge(files, adm(dict(tool, **{"class": "misc"}), doc), GATES, yes)
check("an unknown class is refused", bad and err)
bad, err = ga.judge(files, adm(dict(tool, path="other/verify"), doc), GATES, yes)
check("a root outside the port tree is refused", err and T + "/verify/a.py" in bad)
bad, err = ga.judge([], adm(tool, doc), GATES, yes)
check("an empty tree is certified", not bad and not err)
print("%d failed" % fails)
sys.exit(1 if fails else 0)
