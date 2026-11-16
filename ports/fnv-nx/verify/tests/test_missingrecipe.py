#!/usr/bin/env python3
"""An admit row whose recipe file is not tracked in the tree is red in `recipe check` (recipe.missing_recipes), for the `recipe` string, a {target: path} map and variants.
Pure: a toy admit table and file set, and recipe.check on a toy git repo with a row that names a recipe that is not in the tree."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_recipe as TR  # noqa: E402
import test_link as TL  # noqa: E402

recipe, T, ROOT, check = TR.recipe, TR.T, TR.ROOT, TR.check
files = {"a/build.json", "b/build.json"}
roots = {"a": {"path": "a", "recipe": "a/build.json"}, "b": {"path": "b", "recipe": {"guest": "b/build.json", "host": "b/build.host.json"}},
         "c": {"path": "c", "recipe": "c/build.json"}, "d": {"path": "d", "variants": {"v": {"recipe": "d/v.json"}}}, "e": {"path": "e"}}
e = recipe.missing_recipes(roots, files)
check("missing recipes found: c (string), b host (map), d variant; none for tracked or for a row without a recipe", [x.split()[2] + ":" + x.split()[5].rstrip(",") for x in e] == ["b:b/build.host.json", "c:c/build.json", "d:d/v.json"], e)
check("a table whose recipes are all tracked is green", recipe.missing_recipes({"a": roots["a"]}, files) == [], "")
r = TL.main_rec()
repo = TL.mk("mr1", r, dict(TL.BASE_FILES, **{"toy/b.c": "int f_b(void) { return 1; }\n"}))
adm = recipe.load_admit(repo)
adm["ports/fnv-nx/ghost"] = {"path": "ports/fnv-nx/ghost", "class": "product", "target": "host", "recipe": "ports/fnv-nx/ghost/build.json"}
er = [x for x in recipe.check(r, ROOT, recipe.tracked(repo), adm, None, repo) if "not a tracked file of the tree" in x]
check("recipe check is red for a row whose build.json is not in the tree (fixture tree)", len(er) == 1 and "ghost/build.json" in er[0], er)
er = [x for x in recipe.check(r, ROOT, recipe.tracked(repo), recipe.load_admit(repo), None, repo) if "not a tracked file of the tree" in x]
check("and green without that row", er == [], er)
sys.exit(1 if TR.FAILED else 0)
