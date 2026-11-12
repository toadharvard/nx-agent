#!/usr/bin/env python3
"""Tests for build.json "fixtures" (recipe.py, RECIPE.md "Fixtures") and gate_fixtures.py, on the toy product root of test_recipe.py with the
real gate objstub as the named gate (no doubles). Plain script: PASS/FAIL lines, exit 0 on pass."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import recipe  # noqa: E402
import verdict  # noqa: E402

T0 = time.time()
FAILED = TR.FAILED
check = TR.check
GATES = json.dumps({"format": "fnvnx-gates/1", "gates": [{"name": "objstub", "argv": [sys.executable, "-I", VERIFY + "/gate_objstub.py"], "result": "objstub.json"},
                                                          {"name": "fixtures", "argv": [sys.executable, "-I", VERIFY + "/gate_fixtures.py"], "result": "fixtures.json"}]})
VAC = "int fx(int x) { return x; }\n"   # objstub: exactly one finding, V (a vacuous body)
TRAP = "#include <stdlib.h>\nvoid fx(void) { abort(); }\n"   # objstub: two findings on it, T2 and T2-noreturn-tail
CLEAN = "void ext(void);\nvoid fx(void) { ext(); }\n"
FX = {"gate": "objstub", "expect": "red", "finding": "V"}
GC = TR.BASE + "/gcache"


def repo(name, src, fixtures=None, **rec):
    r = TR.mut(link=None, **rec)
    r["fixtures"] = fixtures if fixtures is not None else {"fx.c": FX}
    return TR.make_repo(name, rec=r, files={"toy/fx.c": src, "toy/constants.tsv": "b.c\tf_b\tu32\t14\tref b\n", "verify/gates.json": GATES})


def run(r, out):
    rc, g, _, txt = TR.gate(r, "fixtures", TR.BASE + "/" + out, GC)
    return rc, g, txt, verdict._gate_result({"name": "fixtures", "result": "fixtures.json"}, TR.BASE + "/" + out, rc)


def main():
    r1 = repo("fx-ok", VAC)
    rc, g, txt, st = run(r1, "o1")
    check("fixture: a vacuous unit, declared objstub/V: the gate is green 1/1 and accepted by the verdict",
          rc == 0 and g["universe"] == 1 == g["certified"] and st[0] == "green", (g, txt))
    res = recipe.build(r1, TR.ROOT, TR.BASE + "/fx-store", TR.BASE + "/fx-out1", ext=TR.BASE + "/ec")
    check("fixture: built like a unit (objects.json fixtures), but in no unit, object, symbol list or pp_keys of the product",
          res["status"] == "green" and [f["unit"] for f in res["fixtures"]] == ["fx.c"] and "fx.c" not in res["units"]
          and "fx.c" not in [o["unit"] for o in res["objects"]] and all(d["symbol"] != "fx" for d in res["defined"]) and "fx.c" not in res["pp_keys"], res.get("failure"))
    rb, gb, _, _ = TR.gate(r1, "build", TR.BASE + "/ob1", GC)
    check("fixture: the build gate's universe is the product's units alone (fixture excluded)", rb == 0 and gb["universe"] == len(res["units"]) + 0 and "fx.c" not in " ".join(gb["uncertified"]), gb)
    rc, g, txt, st = run(repo("fx-green", CLEAN), "o2")
    check("fixture that comes out green: the gate is red, naming the unit", rc == 2 and g["uncertified"] == [TR.ROOT + "/fx.c"] and "green" in g["detail"] and st[0] == "red", (g, txt))
    rc, g, txt, st = run(repo("fx-other", VAC, {"fx.c": dict(FX, finding="T2")}), "o3")
    check("fixture red with a different finding than declared: the gate is red", rc == 2 and g["uncertified"] == [TR.ROOT + "/fx.c"] and "got ['V']" in g["detail"], (g, txt))
    rc, g, txt, st = run(repo("fx-two", TRAP, {"fx.c": dict(FX, finding="T2")}), "o5")
    check("fixture with two findings, one declared: red (exactly the declared finding, nothing else)", rc == 2 and "T2-noreturn-tail" in g["detail"] and st[0] == "red", (g, txt))
    rc, g, txt, st = run(repo("fx-none", VAC, {}), "o4")
    check("no fixture: universe 0, green", rc == 0 and g["universe"] == 0 and st[0] == "green", (g, txt))
    # recipe validation: a fixture never enters the product
    for what, kw, fx in (("os_layer", {"os_layer": ["fx.c"]}, None), ("a link flag", {"link": {"script": "link.ld", "output": "t.elf", "flags": ["fx.c"]}}, None),
                         ("a generated input", {"generated": [{"tool": TR.T + "gen/gen.py", "args": [], "inputs": [TR.ROOT + "/fx.c"], "outputs": ["gen/gen.h"]}]}, None)):
        r = TR.make_repo("fx-ref-" + what.replace(" ", "-"), rec=dict(TR.mut(**kw), fixtures={"fx.c": FX}), files={"toy/fx.c": VAC, "verify/gates.json": GATES})
        rec = recipe.load_recipe(r, recipe.load_admit(r), TR.ROOT)
        errs = recipe.check(rec, TR.ROOT, recipe.tracked(r), recipe.load_admit(r), None, r)
        check("fixture named in %s is refused" % what, any("fixture fx.c is named in" in e or "is not a unit" in e for e in errs), errs)
    r = repo("fx-bad", VAC)
    rec = recipe.load_recipe(r, recipe.load_admit(r), TR.ROOT)
    for what, fx, want in (("a unit that is no tracked file", {"nope.c": FX}, "not a tracked source file"), ("an unknown gate", {"fx.c": dict(FX, gate="nogate")}, "is not a gate"),
                           ("expect other than red", {"fx.c": dict(FX, expect="green")}, "needs exactly"), ("a generated output", {"gen/gen.h": FX}, "not a tracked source file")):
        errs = recipe.check(dict(rec, fixtures=fx), TR.ROOT, recipe.tracked(r), recipe.load_admit(r), None, r)
        check("fixture refused: " + what, any(want in e for e in errs), errs)
    # --- whole-root fixtures: admit class `fixture` {recipe, target, expect: [{gate, code}]}; each expected gate runs on that root alone
    import gate_admit
    import gate_build
    F2 = TR.T + "fx2"

    def rootfx(name, src, expect):
        rec = json.dumps(TR.mut(link=None, os_layer=[], constants=None, generated=[], include=[], fixtures={}))
        ent = {"path": F2, "class": "fixture", "target": "host", "gates": [], "recipe": F2 + "/build.json", "expect": expect}
        return TR.make_repo(name, rec=TR.mut(link=None), files={"toy/fx.c": VAC, "fx2/a.c": src, "fx2/build.json": rec, "verify/gates.json": GATES}, roots=[ent])
    rv = rootfx("fr-ok", VAC, [{"gate": "objstub", "code": "V"}])
    old = os.environ.pop("GATE_ONLY_ROOT", None)
    cwd = os.getcwd()
    os.chdir(rv)
    try:
        pr, os.environ["GATE_ONLY_ROOT"] = gate_build.product_roots("."), F2
        one = gate_build.product_roots(".")
    finally:
        os.environ.pop("GATE_ONLY_ROOT", None)
        os.chdir(cwd)
    check("fixture root: not a product root, but the one root of a run with GATE_ONLY_ROOT", F2 not in pr and one == [F2], (pr, one))
    rc, g, txt, st = run(rv, "r1")
    check("whole-root fixture: objstub on that root alone reports exactly V: green, counted in the universe", rc == 0 and g["certified"] == g["universe"] and g["universe"] >= 1
          and "%s (root)" % F2 not in g["uncertified"], (g, txt))
    rc, g, txt, st = run(rootfx("fr-clean", CLEAN, [{"gate": "objstub", "code": "V"}]), "r2")
    check("whole-root fixture that comes out green: red, naming the root", rc == 2 and "%s (root)" % F2 in g["uncertified"] and "green" in g["detail"], (g, txt))
    rc, g, txt, st = run(rootfx("fr-code", VAC, [{"gate": "objstub", "code": "T2"}]), "r3")
    check("whole-root fixture with another code than expected: red", rc == 2 and "got ['V']" in g["detail"], (g, txt))
    bad, errs_ = gate_admit.judge([F2 + "/a.c"], {"format": "fnvnx-admit/1", "roots": [{"path": F2, "class": "fixture", "gates": [], "recipe": F2 + "/build.json"}]}, ["objstub"], lambda p: True)
    check("admit: a fixture root without expect is refused", any("expect" in x for x in errs_), (bad, errs_))
    bad, errs_ = gate_admit.judge([F2 + "/a.c"], {"format": "fnvnx-admit/1", "roots": [{"path": F2, "class": "fixture", "gates": [], "recipe": F2 + "/build.json",
                                                                                          "expect": [{"gate": "nogate", "code": "V"}]}]}, ["objstub"], lambda p: True)
    check("admit: a fixture root whose expect names an unknown gate is refused", any("nogate" in x for x in errs_), (bad, errs_))
    if old:
        os.environ["GATE_ONLY_ROOT"] = old
    print("%d failed, wall %.1f s" % (len(FAILED), time.time() - T0))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
