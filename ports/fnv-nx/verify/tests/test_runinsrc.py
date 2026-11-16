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
    au = recipe.all_units_of(rec([ROOT + "/a.cc"]), ROOT, files, {})
    check("all_units_of leaves the run input out and keeps every other source: green", au == ["b.c", "gen/in.cc", "tool/x.cc"], au)
    check("without run steps every source is a unit (unchanged)", recipe.all_units_of({}, ROOT, files, {}) == ["a.cc", "b.c", "gen/in.cc", "tool/x.cc"], "")
    check("units_of follows all_units_of", recipe.units_of(rec([ROOT + "/a.cc"]), ROOT, files, {}) == au, "")
    # nested admitted roots and plugin sources are not units of the outer root (own_sources)
    nroot = ROOT + "/sub"
    nf = [ROOT + "/a.cc", ROOT + "/sub/n.cc", ROOT + "/sub/m/o.cc"]
    nu = recipe.all_units_of({}, ROOT, nf, {ROOT: {}, nroot: {}})
    check("a file under a root admitted inside the root is not a unit of the outer root (nearest-root ownership)", nu == ["a.cc"], nu)
    check("the nested root keeps its own files", recipe.all_units_of({}, nroot, nf, {ROOT: {}, nroot: {}}) == ["m/o.cc", "n.cc"], "")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ps = recipe.PLUGIN_SOURCES["fnvnx_ftoi"]
        pd = os.path.dirname(ps)
        os.makedirs(os.path.join(td, pd))
        open(os.path.join(td, ps), "w").write('#include "inc.cc"\n')
        open(os.path.join(td, pd, "inc.cc"), "w").write("int x;\n")
        troot = os.path.dirname(pd)
        pf = [ps, pd + "/inc.cc", troot + "/other/u.cc"]
        pu = recipe.all_units_of({}, troot, pf, {troot: {}}, td)
        check("a plugin source and the files it includes are inputs of the plugin stage, not units", pu == ["other/u.cc"], pu)
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
    # unused compiler / plugins of a generated-only root
    def gu(rc, ins):
        r = dict(rec(ins), **rc)
        repo = TL.mk("gu" + str(abs(hash(str(rc) + str(ins)))), r, {"toy/b.c": None})
        return [x for x in recipe.check(r, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo) if x.startswith("gonly:")]
    cmp_ = {"name": "s", "root": "r", "output": "O", "at": "/ext/s"}
    e = gu({"compiler": cmp_, "plugins": [{"name": "p"}]}, [{"plugin": "p"}])
    check("compiler + a plugin that a run step takes: no gonly error", e == [], e)
    e = gu({"compiler": cmp_, "plugins": [{"name": "p"}, {"name": "q"}]}, [{"plugin": "p"}])
    check("a plugin no run step takes is red (gonly:unused-plugin)", len(e) == 1 and "unused-plugin:q" in e[0], e)
    e = gu({"compiler": cmp_}, [])
    check("a compiler with no plugin and no step using it is red (gonly:unused-compiler)", len(e) == 1 and "unused-compiler" in e[0], e)
    e = gu({}, [])
    check("a generated-only root that writes no compiler gets none counted as unused (the filled profile compiler is not a written one)", e == [], e)
    # forms: {"plugins": "root"} and debs {"closure": [...]}
    pl = {"plugins": [{"name": "p"}, {"name": "q"}]}
    st = dict(rec(["x", {"plugins": "root"}])["run"][0])
    check('{"plugins": "root"} expands to one plugin input per declared plugin, in declaration order',
          recipe.step_inputs(pl, st) == ["x", {"plugin": "p"}, {"plugin": "q"}], recipe.step_inputs(pl, st))
    check("a root that declares no plugins expands it to nothing", recipe.step_inputs({}, st) == ["x"], "")
    def re_(r, ins=None, rc=pl):
        r = dict(rc, **r)
        return recipe.run_errors(r, ROOT, set(), {}, set(), set(), ".")
    e = re_(dict(run=[dict(rec([{"plugins": "root"}])["run"][0], exec="/usr/bin/bash")]), rc={})
    check('{"plugins": "root"} with no declared plugins is red', any("needs the root to declare" in x for x in e), e)
    d = dict(rec([])["run"][0], debs={"closure": ["bash", "nosuchdeb"]})
    e = re_(dict(run=[d]))
    check("debs.closure: a top that env.json does not declare is red", any("nosuchdeb is not declared" in x for x in e), e)
    check("debs.closure: the tops are declared, no debs error", not [x for x in re_(dict(run=[dict(d, debs={"closure": ["bash"]})])) if "debs" in x], "")
    for bad in ({"closure": []}, {"closure": "bash"}, {"closure": ["bash"], "x": 1}, "bash"):
        e = re_(dict(run=[dict(d, debs=bad)]))
        check("debs form %r is red" % (bad,), any("debs is a list" in x for x in e), e)
    c = recipe.step_debs({"debs": {"closure": ["bash", "sed"]}})
    check("debs.closure resolves the Depends closure (tops and what they need, sorted)", c == sorted(set(c)) and {"bash", "sed", "libc6"} <= set(c), c)
    try:
        recipe.step_debs({"debs": {"closure": ["no-such-package-zz"]}})
        check("a top that is not installed is refused by the build", False, "")
    except ValueError as ex:
        check("a top that is not installed is refused by the build", "not installed" in str(ex), ex)
    print("%d FAILED" % len(TR.FAILED) if TR.FAILED else "all passed")
    return 1 if TR.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
