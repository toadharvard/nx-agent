#!/usr/bin/env python3
"""Gate "d3d9total" (fnvnx-gate/1, VERDICT class 7): the D3D9 -> backend translation is total by construction.
    GATE_OUT=<dir> GATE_CACHE=<dir> python3 -I ports/fnv-nx/render/total/gate_d3d9total.py
Run in the checkout root (verify/verdict.py does). Writes GATE_OUT/d3d9total.json; exit 0 iff green, 2 red.
This gate computes nothing itself. The rule (the enum rule and the SM rule, universe = every (enumeration, enumerator) the translation consumes by
switch plus every row of the generated sm_table.json) is render/total/d3d9total/stage.py, run as the hermetic stage `run:gate` of the product root
render/total/d3d9total by verify/recipe.py in GATE_CACHE: the compilers are the declared debs of that stage, the translator's MojoShader and the Wine mirror
are the externals of the roots that pin them (`external_of`), the SM table and rows header are the stage outputs of render/shaders/total, and the
tracked trees it reads are declared inputs. The result is read through recipe.resolve_input
{"stage": "ports/fnv-nx/render/total/d3d9total:run:gate", "path": "d3d9total.json"}, the one reader of a stage output; a stage that did not run
or whose result is not a fnvnx-gate/1 record of this gate is red with the reason. Green iff the stage's own result is green."""
import json, os, sys

REPO = os.getcwd()
import types
_pkg = sys.modules.setdefault("fnvnx", types.ModuleType("fnvnx")); _pkg.__path__ = [os.path.join(REPO, "ports", "fnv-nx")]   # a host-run driver: `fnvnx` is ports/fnv-nx (the stage mounts it at /imp/fnvnx)
from fnvnx.verify import recipe  # noqa: E402

ROOT = "ports/fnv-nx/render/total/d3d9total"


def result(cache):
    """-> the stage's gate record, or a red record naming why the stage's output could not be read"""
    try:
        path, _ = recipe.resolve_input(ROOT, {"stage": ROOT + ":run:gate", "path": "d3d9total.json"}, REPO, cache)
        res = json.load(open(path))
        if res.get("format") != "fnvnx-gate/1" or res.get("name") != "d3d9total":
            raise ValueError("the stage's result is not a d3d9total gate record")
        return res
    except (ValueError, OSError, KeyError, recipe.RecipeError) as x:
        return {"format": "fnvnx-gate/1", "name": "d3d9total", "status": "red", "universe": 0, "certified": 0, "uncertified": [],
                "detail": "stage %s:run:gate: %s" % (ROOT, str(x)[:400])}


def main():
    res = result(os.environ["GATE_CACHE"])
    with open(os.path.join(os.environ["GATE_OUT"], "d3d9total.json"), "w") as f:
        f.write(json.dumps(res, sort_keys=True, indent=1) + "\n")
    return 0 if res["status"] == "green" else 2


if __name__ == "__main__":
    sys.exit(main())
