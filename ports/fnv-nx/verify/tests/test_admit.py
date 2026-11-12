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


tool = {"path": T + "/verify", "class": "tool", "gates": ["selftests"], "entry": []}
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
# the gates of a product root are DERIVED: every gate of gates.d whose `covers` include a class of the root
COV = {"objstub": ["product-guest", "lifted", "host-product", "test"], "datamodel": ["product-guest", "lifted"], "n1": ["linked"],
       "argordergate": ["product-guest"], "selftests": ["tool"], "headers": ["headers"], "t_only": ["test"]}
GN = GATES + list(COV)
prod = {"path": T + "/platform", "class": "product", "recipe": T + "/platform/build.json"}
need = ga.required_gates(prod, COV, False)
check("required gates of a guest product root: the gates covering product-guest, none covering only another class", need == ["argordergate", "datamodel", "objstub"])
check("a product root with a link also needs the `linked` gates", ga.required_gates(prod, COV, True) == ["argordergate", "datamodel", "n1", "objstub"])
check("a lifted root: lifted gates, not the product-guest-only ones", ga.required_gates(dict(prod, cxx_origin={"lifted_by": "x", "pack": "p"}), COV, False) == ["datamodel", "objstub"])
check("a host product root: no guest gate", ga.required_gates(dict(prod, target="host"), COV, False) == ["objstub"])
check("a test root: the gates covering test", ga.required_gates({"path": "t", "class": "test"}, COV, False) == ["objstub", "t_only"])
J = lambda e, names=GN, ex=yes, **kw: ga.judge([T + "/platform/w.c"], adm(e), names, ex, covers=COV, **kw)
bad, err = J(dict(prod, gates=need), GATES)
check("a product root is refused while its gates do not exist in gates.d", bad and err)
bad, err = J(dict(prod, gates=need))
check("a product root with every derived gate and a recipe is admitted", not bad and not err)
bad, err = J(dict(prod, gates=need), GN, no)
check("a product root whose recipe file is absent is refused", bad and err)
for g in need:
    bad, err = J(dict(prod, gates=[x for x in need if x != g]))
    check("a product root lacking %s is refused" % g, bad and any(g in e for e in err))
bad, err = ga.judge([T + "/platform/w.c"], adm(dict(prod, gates=need)), GN + ["late"], yes, covers=dict(COV, late=["product-guest"]))
check("a gate added to gates.d with covers becomes required of the class at once (no list to edit)", bad and any("late" in e for e in err))
bad, err = ga.judge([T + "/platform/w.c"], adm(dict(prod, gates=need)), GN, yes, covers=COV, linked=lambda p: True)
check("a root that declares a link is required to name the linked gates", bad and any("n1" in e for e in err))
bad, err = ga.judge(files, adm(tool, doc, {"path": T + "/verify/tests", "class": "doc", "gates": []}), GATES, yes)
check("nested roots are an error", any("overlap" in e for e in err))
bad, err = ga.judge(files, adm(dict(tool, **{"class": "misc"}), doc), GATES, yes)
check("an unknown class is refused", bad and err)
bad, err = ga.judge(files, adm(dict(tool, path="other/verify"), doc), GATES, yes)
check("a root outside the port tree is refused", err and T + "/verify/a.py" in bad)
bad, err = ga.judge([], adm(tool, doc), GATES, yes)
check("an empty tree is certified", not bad and not err)
# class test: the gates whose covers include `test` (derived like a product root's), a recipe and a target
TG = ga.required_gates({"class": "test"}, COV, False)
troot = {"path": T + "/tests", "class": "test", "target": "guest", "gates": TG, "recipe": T + "/tests/build.json"}
tf = [T + "/tests/t.cc"]
JT = lambda e, names=GN + TG, ex=yes: ga.judge(tf, adm(tool, doc, e), names, ex, covers=COV)
bad, err = JT(troot)
check("a test root with the gates that cover class test, a recipe and a target is certified (the product-only gates are not asked of it)", not bad and not err)
check("the test set is the gates covering `test`", TG == ["objstub", "t_only"])
for g in TG:
    bad, err = JT(dict(troot, gates=[x for x in TG if x != g]))
    check("a test root without gate %s is refused" % g, list(bad) == tf and any(g in e for e in err))
bad, err = JT({k: v for k, v in troot.items() if k != "target"})
check("a test root without a target is refused", list(bad) == tf and any("target" in e for e in err))
bad, err = JT(dict(troot, target="elsewhere"))
check("a test root with an unknown target is refused", list(bad) == tf)
bad, err = JT(troot, ex=no)
check("a test root whose recipe file does not exist is refused", list(bad) == tf and any("recipe" in e for e in err))
bad, err = JT(dict(troot, **{"class": "product"}))
check("the same root as a product root is refused: it lacks the gates covering product-guest", list(bad) == tf)

import json  # noqa: E402
import tempfile  # noqa: E402
import gate_build as gbd  # noqa: E402
rp = tempfile.mkdtemp(prefix="admit-roots-")
os.makedirs(rp + "/ports/fnv-nx/verify")
json.dump({"roots": [{"path": T + "/p", "class": "product"}, {"path": T + "/t", "class": "test"}, {"path": T + "/v", "class": "tool"}]},
          open(rp + "/ports/fnv-nx/verify/admit.json", "w"))
check("the build family judges the product and the test roots; the other gates keep the product roots",
      gbd.buildable_roots(rp) == [T + "/p", T + "/t"] and gbd.product_roots(rp) == [T + "/p"])
print("%d failed" % fails)
sys.exit(1 if fails else 0)
