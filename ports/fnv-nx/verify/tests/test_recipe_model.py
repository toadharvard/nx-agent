#!/usr/bin/env python3
"""Tests for verify/recipe_model.py: the pure model of the build. It imports no stage machinery (no cas, sandbox, runtime, compiler), recipe.py and
gate_build.py use its definitions (one definition each), and the build manifest "fnvnx-built/1" is written whole and read back; a missing or wrong manifest is a RecipeError.
Plain script: exit 0 on pass."""
import ast
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import recipe_model as rm  # noqa: E402
import recipe  # noqa: E402
import gate_build  # noqa: E402

FAILED = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  %r" % (info,)))
    if not ok:
        FAILED.append(name)


tree = ast.parse(open(os.path.join(VERIFY, "recipe_model.py")).read())
imps = sorted({n.names[0].name.split(".")[0] for n in tree.body if isinstance(n, ast.Import)} | {n.module for n in tree.body if isinstance(n, ast.ImportFrom)})
check("recipe_model imports only the standard library, no stage machinery", imps == ["json", "os", "posixpath", "subprocess"], imps)
check("recipe.py takes the model's definitions", all(getattr(recipe, n) is getattr(rm, n) for n in
      ("RecipeError", "BuildRed", "load_admit", "load_recipe", "units_of", "load_pin", "roots_target", "EXT_KEYS", "ASM_EXT", "CC_EXT", "CXX_EXT", "tracked", "is_unit")))
check("gate_build takes the model's quantify, product_roots, tree_of", gate_build.quantify is rm.quantify and gate_build.product_roots is rm.product_roots
      and gate_build.tree_of is rm.tree_of)
check("units_of: tracked sources and generated outputs with a source name", rm.units_of({"generated": [{"outputs": ["g.cc", "g.h"]}]}, "r", ["r/a.c", "r/b.h", "r/c.S", "x/d.c"]) == ["a.c", "c.S", "g.cc"])
check("is_unit by extension, roots_target", rm.is_unit("a.cc") and not rm.is_unit("a.h") and rm.roots_target({"r": {"target": "guest"}}, "r") == "guest" and rm.roots_target({}, "r") is None)

with tempfile.TemporaryDirectory() as t:
    tree_id = "a" * 40
    od = rm.objects_dir(t, tree_id, "ports/fnv-nx/x")
    check("objects_dir is a function of cache, tree and root", od == os.path.join(t, "objects", tree_id, "ports__fnv-nx__x"))
    try:
        rm.read_built(t, tree_id)
        check("no manifest: RecipeError", False)
    except rm.RecipeError as e:
        check("no manifest: RecipeError naming gate build", "gate build must run first" in str(e), str(e))
    os.makedirs(od)
    oj = os.path.join(od, "objects.json")
    json.dump({"format": "fnvnx-objects/1", "root": "ports/fnv-nx/x"}, open(oj, "w"))
    p = rm.write_built(t, tree_id, {"ports/fnv-nx/x": oj}, {"gcc": os.path.join(t, "external", "c" * 40)})
    m = rm.read_built(t, tree_id)
    check("manifest round trip: roots and externals", p == rm.built_path(t, tree_id) and m["roots"] == {"ports/fnv-nx/x": {"objects": oj}}
          and m["externals"] == {"gcc": os.path.join(t, "external", "c" * 40)} and m["format"] == rm.BUILT_FORMAT)
    check("objects_of reads the root's objects.json", rm.objects_of(m, "ports/fnv-nx/x")["root"] == "ports/fnv-nx/x")
    try:
        rm.objects_of(m, "ports/fnv-nx/other")
        check("unbuilt root: RecipeError", False)
    except rm.RecipeError as e:
        check("unbuilt root: RecipeError", "not in the build manifest" in str(e), str(e))
    try:
        rm.read_built(t, "b" * 40)
        check("manifest of another tree is not used", False)
    except rm.RecipeError:
        check("manifest of another tree is not used", True)
    json.dump({"format": "x"}, open(rm.built_path(t, tree_id), "w"))
    try:
        rm.read_built(t, tree_id)
        check("wrong format: RecipeError", False)
    except rm.RecipeError as e:
        check("wrong format: RecipeError", "fnvnx-built/1" in str(e), str(e))

REPO = os.path.abspath(os.path.join(VERIFY, "..", "..", ".."))
r = rm.quantify(REPO, lambda root: (1, 1, [], ""))
check("quantify sums the per-root results", r[0] == r[1])
print("FAILED: %s" % FAILED if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
