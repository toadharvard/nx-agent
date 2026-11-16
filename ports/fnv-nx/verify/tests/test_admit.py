#!/usr/bin/env python3
# reads: ports/fnv-nx/contract ports/fnv-nx/link ports/fnv-nx/toolchain/msvcmodel/DATAMODEL.json ports/fnv-nx/verify
"""gate_admit.judge: every tracked file under a root (the nearest one), the class of the root one of CLASSES, no row naming gates."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import gate_admit as ga  # noqa: E402
import gatesdir  # noqa: E402

T = "ports/fnv-nx"
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
bad, err = ga.judge(files + rf, adm(tool, doc, ref), yes, set(rf))
check("a ref root whose files are all references is certified", not bad and not err)
bad, err = ga.judge(files + rf, adm(tool, doc, ref), yes, {rf[0]})
check("a ref root file that no oracle record points at is refused", list(bad) == [rf[1]] and not err)
bad, err = ga.judge(files + [T + "/refs/test_x.py", T + "/refs/build.json"], adm(tool, doc, ref), yes, {T + "/refs/test_x.py", T + "/refs/build.json"})
check("a test or a build.json in a ref root is refused even when cited", set(bad) == {T + "/refs/test_x.py", T + "/refs/build.json"})
bad, err = ga.judge(files + rf, adm(tool, doc, dict(ref, recipe=T + "/refs/build.json")), yes, set(rf))
check("a ref root with a recipe is refused (it is not a product root)", err and bad)
bad, err = ga.judge(files + rf, adm(tool, doc, dict(ref, gates=["selftests"])), yes, set(rf))
check('a row that lists "gates" is refused (the set is derived from gates.d covers)', err and bad and any("lists" in e for e in err))
bad, err = ga.judge(files, adm(dict(tool, gates=[]), doc), yes)
check('an empty "gates" list is a listing too', err and bad)
bad, err = ga.judge(files, adm(tool, doc), yes)
check("two roots cover two files", not bad and not err)
bad, err = ga.judge(files + [T + "/platform/w.c"], adm(tool, doc), yes)
check("a file outside every root is uncertified", list(bad) == [T + "/platform/w.c"] and not err)
prod = {"path": T + "/platform", "class": "product", "recipe": T + "/platform/build.json"}
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), yes)
check("a product root with a recipe is admitted", not bad and not err)
bad, err = ga.judge([T + "/platform/w.c"], adm(prod), no)
check("a product root whose recipe file is absent is refused", bad and err)
bad, err = ga.judge([T + "/platform/w.c"], adm({"path": T + "/platform", "class": "product"}), yes)
check("a product root with no recipe is refused", bad and err)

# nearest-root ownership: a root inside another owns its files; the parent owns the rest
hroot = {"path": T + "/platform/include", "class": "header"}
inner = [T + "/platform/include/a.h"]
bad, err = ga.judge([T + "/platform/w.c"] + inner, adm(prod, hroot), yes)
check("a header root inside a product root is admitted: its files belong to it, the others to the product root", not bad and not err)
bad, err = ga.judge(inner, adm(prod, dict(hroot, **{"class": "ref"})), yes, set())
check("the nested root's own class judges its files (a ref root file that is no reference is refused)", list(bad) == inner)
bad, err = ga.judge(files, adm(tool, doc, dict(doc, **{"class": "doc"})), yes)
check("the same tree named by two rows is an error", any("twice" in e for e in err))
bad, err = ga.judge(files, adm(tool, doc, {"path": T + "/verify/tests", "class": "doc"}), yes)
check("a doc root inside a tool root is allowed (nearest root)", not bad and not err)
bad, err = ga.judge(files, adm(dict(tool, **{"class": "misc"}), doc), yes)
check("an unknown class is refused", bad and err)
bad, err = ga.judge(files, adm(dict(tool, path="other/verify"), doc), yes)
check("a root outside the port tree is refused", err and T + "/verify/a.py" in bad)
bad, err = ga.judge([], adm(tool, doc), yes)
check("an empty tree is certified", not bad and not err)
# class test
troot = {"path": T + "/tests", "class": "test", "target": "guest", "recipe": T + "/tests/build.json"}
tf = [T + "/tests/t.cc"]
bad, err = ga.judge(tf, adm(tool, doc, troot), yes)
check("a test root with a recipe and a target is certified", not bad and not err)
bad, err = ga.judge(tf, adm(tool, doc, {k: v for k, v in troot.items() if k != "target"}), yes)
check("a test root without a target is refused", list(bad) == tf and any("target" in e for e in err))
bad, err = ga.judge(tf, adm(tool, doc, dict(troot, target="elsewhere")), yes)
check("a test root with an unknown target is refused", list(bad) == tf)
bad, err = ga.judge(tf, adm(tool, doc, troot), no)
check("a test root whose recipe file does not exist is refused", list(bad) == tf and any("recipe" in e for e in err))

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
# class header: headers only, no recipe, no target (both derived from the recipes that include it), no translation unit
hroot = {"path": T + "/inc", "class": "header"}
hf = [T + "/inc/x/a.h", T + "/inc/x/b.inc"]
bad, err = ga.judge(hf, adm(tool, doc, hroot), yes)
check("a header root of header files, with no recipe and no target, is certified", not bad and not err)
for k, v in (("target", "host"), ("recipe", T + "/inc/build.json"), ("variants", {"m": {}}), ("cxx_origin", "native")):
    bad, err = ga.judge(hf, adm(tool, doc, dict(hroot, **{k: v})), yes)
    check("a header root naming %s is refused (a root with no units has none)" % k, set(bad) == set(hf) and any(k in e for e in err))
for u in ("/inc/x/c.c", "/inc/x/d.cc", "/inc/x/e.S", "/inc/build.json"):
    bad, err = ga.judge(hf + [T + u], adm(tool, doc, hroot), yes)
    check("a header root with %s is refused (it has a unit: a product or test root)" % u, bad and any("unit" in e for e in err))
# the gate set of a root is derived from gates.d covers, never from a table of the gate
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))   # gatesdir reads the tree from the checkout root
covers = {g["name"]: set(g.get("covers", [])) for g in gatesdir.load_dir().get("gates", [])}
check("every gate of gates.d declares the classes it covers", all(covers.values()))
check("gate pure covers the classes that hold source (tool, ref, header, test, every product class)",
      {"tool", "ref", "header", "test", "product-guest", "lifted", "host-product"} <= covers["pure"])
check("gate_admit holds no table of gate names", not any(hasattr(ga, n) for n in ("REQUIRED", "REQUIRED_PRODUCT", "REQUIRED_TEST", "REQUIRED_HEADER", "gates_of")))
print("%d failed" % fails)
sys.exit(1 if fails else 0)
