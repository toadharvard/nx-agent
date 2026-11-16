#!/usr/bin/env python3
"""The run-step input {"pp": ROOT, "unit": PATH} (RECIPE.md "The preprocessed text of a unit"): the unit.ii of the unit's PP stage in the guest root ROOT, mounted as one file at /in/N.
One rule, recipe.pp_errors, called by check and by Job.pp_tree (the mount). Red: ROOT not in this recipe's `uses`; ROOT not an admitted root; ROOT not a guest root; PATH not a unit
of ROOT (a header, a missing file, a .s unit); any other shape. Green: a unit of a used guest root. Stage case (needs the declared environment, a farm): a header edit changes the
step's key and its input.
Run: python3 -I <this file>."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import recipe  # noqa: E402
import test_recipe as TR  # noqa: E402

T, ROOT, CONS = TR.T, TR.ROOT, TR.T + "cons"
FAILS = []


def check(name, ok, info=""):
    TR.check(name, ok, info)
    if not ok:
        FAILS.append(name)


def consumer(pp, uses=True):
    return {"format": "fnvnx-recipe/1", "uses": [{"root": ROOT, "output": "gen"}] if uses else [], "outputs": {"o": "o.txt"},
            "run": [{"name": "s", "exec": {"python": True}, "args": ["/in/1"], "inputs": [CONS + "/p.py", pp], "debs": [], "outputs": ["o.txt"], "scratch": []}]}


SCRIPT = "open('/out/o.txt','w').write(open('/in/2').read())\n"
REC = dict(TR.RECIPE, outputs={"gen": "gen/gen.h"})


def make(name, toy_h="#define TOY 7\n", pp=None):
    extra = [{"path": CONS, "class": "product", "target": "host", "recipe": CONS + "/build.json"},
             {"path": T + "hostp", "class": "product", "target": "host", "recipe": T + "hostp/build.json"}]
    files = {"cons/build.json": json.dumps(consumer(pp or {"pp": ROOT, "unit": "a.c"})), "cons/p.py": SCRIPT, "toy/inc/toy.h": toy_h, "toy/s.s": ".text\n",
             "hostp/build.json": json.dumps({"format": "fnvnx-recipe/1", "outputs": {}}), "hostp/h.c": "int h;\n"}
    return TR.make_guest_repo(name, rec=REC, files=files, extra_roots=extra)


def errs(repo, ref, uses=True, rec=None):
    roots, files = recipe.load_admit(repo), recipe.tracked(repo)
    return recipe.pp_errors("t", ref, CONS, rec if rec is not None else consumer(ref, uses), roots, files, repo)


def main():
    repo = make("pp-a")
    check("a unit of a used guest root is green", errs(repo, {"pp": ROOT, "unit": "a.c"}) == [], errs(repo, {"pp": ROOT, "unit": "a.c"}))
    check("a generated source is a unit too (the unit list of the root)", errs(repo, {"pp": ROOT, "unit": "os.S"}) == [])
    check("a root this recipe does not `uses` is red", any("uses" in e for e in errs(repo, {"pp": ROOT, "unit": "a.c"}, uses=False)))
    check("a root that is not admitted is red", any("not an admitted" in e for e in errs(repo, {"pp": T + "nope", "unit": "a.c"}, rec={"uses": [{"root": T + "nope", "output": "x"}]})))
    check("a host root is red", any("not a guest root" in e for e in errs(repo, {"pp": T + "hostp", "unit": "h.c"}, rec={"uses": [{"root": T + "hostp", "output": "x"}]})))
    check("a header is no unit: red", any("not a unit" in e for e in errs(repo, {"pp": ROOT, "unit": "inc/toy.h"})))
    check("a file the root does not have is red", any("not a unit" in e for e in errs(repo, {"pp": ROOT, "unit": "zzz.c"})))
    check("a .s unit has no PP stage: red", any(".s unit" in e for e in errs(repo, {"pp": ROOT, "unit": "s.s"})))
    for bad in ({"pp": ROOT}, {"pp": ROOT, "unit": 1}, {"pp": ROOT, "unit": "a.c", "x": 1}, {"unit": "a.c"}, [ROOT, "a.c"], "a.c"):
        check("another shape is red: %r" % (bad,), len(errs(repo, bad, rec={"uses": [{"root": ROOT, "output": "gen"}]})) == 1 and "is not {pp: ROOT, unit: PATH}" in errs(repo, bad, rec={"uses": [{"root": ROOT, "output": "gen"}]})[0])
    # check() and the mount are one function
    roots, files = recipe.load_admit(repo), recipe.tracked(repo)
    bad = {"pp": ROOT, "unit": "zzz.c"}
    ce = [e for e in recipe.check(consumer(bad), CONS, files, roots, recipe.load_pin(repo), repo) if "zzz.c" in e]
    me = None
    try:
        recipe.Job(repo, CONS, tempfile.mkdtemp(prefix="pp-st-"), 2, None).pp_tree(tempfile.mkdtemp(prefix="pp-ws-"), bad)
    except recipe.BuildRed as r:
        me = r.args[0]["stderr"]
    check("check reds the ref with the rule's message, and the mount raises the same message", ce and me and ce[0].split(": ", 1)[1] == me[0].split("input: ", 1)[1], (ce, me))
    # stage case: a header edit changes the step's key and its input
    try:
        recipe.env_blocked(repo)
    except recipe.EnvBlocked as e:
        check("the stage case is blocked by the declared environment (run on a farm)", bool(e.args[0]), e.args)
        return 1 if FAILS else 0
    store = tempfile.mkdtemp(prefix="pp-store-")
    res = {}
    for tag, h in (("a", "#define TOY 7\n"), ("b", "#define TOY 7\n"), ("c", "#define TOY 8\n")):
        r = make("pp-" + tag + "2", toy_h=h)
        out = tempfile.mkdtemp(prefix="pp-out-")
        o = recipe.build(r, CONS, store, out)
        res[tag] = (o["status"], sorted(o["stage_keys"]), open(o["outputs"]["o"]["path"]).read() if o.get("outputs") else "")
    check("the step is green and its input is the preprocessed text of a.c (TOY expanded, no directive left)", res["a"][0] == "green" and "#define" not in res["a"][2] and " 7" in res["a"][2], res["a"][:1] + (res["a"][2][-200:],))
    check("the same header gives the same keys", res["a"][1] == res["b"][1])
    check("a header edit changes the input and the step's key", res["a"][2] != res["c"][2] and res["a"][1] != res["c"][1] and " 8" in res["c"][2])
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
