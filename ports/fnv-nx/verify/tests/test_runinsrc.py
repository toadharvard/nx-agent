#!/usr/bin/env python3
"""A C/C++ source that is a declared input of a `run` step of its root is that step's input, not a unit (RECIPE.md "Run stages"); every other source is a unit,
and in a generated-only root it is red. Pure (recipe.check on toy git repos; no stage runs). The step's key covers its input contents (the existing run key,
test_run in test_recipe.py)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import test_link as TL  # noqa: E402

recipe, T, ROOT, check = TR.recipe, TR.T, TR.ROOT, TR.check
files = [ROOT + "/" + p for p in ("a.cc", "b.c", "gen/in.cc", "tool/x.cc", "h.h")]


def rec(inputs):
    return {"run": [{"name": "r", "artifact": "x:y", "args": [], "inputs": inputs, "debs": [], "outputs": ["o.txt"]}]}


def main():
    ris = lambda inputs: sorted(recipe.run_input_sources(rec(inputs), ROOT, files))  # noqa: E731
    check("a file declared as a run input is the step's input", ris([ROOT + "/a.cc"]) == ["a.cc"], ris([ROOT + "/a.cc"]))
    check("a directory input holds its sources as inputs", ris([ROOT + "/gen"]) == ["gen/in.cc"], ris([ROOT + "/gen"]))
    check("a header or a file nobody declares is not a run-input source", ris([ROOT + "/h.h"]) == [] and ris([]) == [], "")
    check("{external, path} and {stage, path} inputs name no source of the root", ris([{"external": "e", "path": "a.cc"}, {"stage": "r:gen:0", "path": "a.cc"}]) == [], "")
    au = recipe.all_units_of(rec([ROOT + "/a.cc"]), ROOT, files)
    check("all_units_of leaves the run input out and keeps every other source: green", au == ["b.c", "gen/in.cc", "tool/x.cc"], au)
    check("without run steps every source is a unit (unchanged)", recipe.all_units_of({}, ROOT, files) == ["a.cc", "b.c", "gen/in.cc", "tool/x.cc"], "")
    check("units_of follows all_units_of", recipe.units_of(rec([ROOT + "/a.cc"]), ROOT, files) == au, "")
    # generated-only roots: a run-only recipe compiles nothing; an undeclared source is red, a declared run input is green
    def tu(inputs, extra):
        r = rec(inputs)
        repo = TL.mk("ri" + str(abs(hash(str(inputs) + str(sorted(extra))))), r, dict(extra))
        return [e for e in recipe.check(r, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo) if "translation unit" in e and ("in.cc" in e or "loose.cc" in e)]
    e = tu([ROOT + "/in.cc"], {"toy/in.cc": "int f;\n"})
    check("generated-only root: a .cc that is a declared run input is green", e == [], e)
    e = tu([], {"toy/loose.cc": "int f;\n"})
    check("generated-only root: an undeclared .cc is red", len(e) == 1 and "loose.cc" in e[0], e)
    g = rec([ROOT + "/in.cc"])
    check("generated-only: run steps and no unit keys", recipe.generated_only(g), "")
    check("generated-only: `compiler` and `plugins` may be written (the compiler stage)", recipe.generated_only(dict(g, compiler={"name": "s", "root": "r", "output": "O", "at": "/ext/s"}, plugins=[{"name": "p"}])), "")
    check("not generated-only: any unit key (flags, include, units, link, profile, variants)", not any(recipe.generated_only(dict(g, **{k: 1})) for k in ("flags", "include", "units", "link", "profile", "variants")), "")
    check("fill keeps the root's own compiler", recipe.fill_generated_only(dict(g, compiler="X"))["compiler"] == "X", "")
    # run inputs may lie in a tree of an admitted root (link/ is a tree of the contract root), not only under a root path
    rts = {"ports/fnv-nx/contract": {"path": "ports/fnv-nx/contract", "trees": ["ports/fnv-nx/contract", "ports/fnv-nx/link"], "class": "tool"}}
    ee = recipe.run_errors(rec(["ports/fnv-nx/link/guest.ld"]), ROOT, {"ports/fnv-nx/link/guest.ld"}, rts, set(), set(), ".")
    check("a run input in a tree of an admitted root (link of contract) is accepted", not [x for x in ee if "inputs:" in x], ee)
    ee = recipe.run_errors(rec(["ports/fnv-nx/elsewhere/x"]), ROOT, {"ports/fnv-nx/elsewhere/x"}, rts, set(), set(), ".")
    check("a run input in no admitted tree is red", any("not a tracked file or directory of an admitted root" in x for x in ee), ee)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
