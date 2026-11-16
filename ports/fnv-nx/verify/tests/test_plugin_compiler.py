#!/usr/bin/env python3
# reads: ports/fnv-nx/verify
"""Tests for the plugin-compiler rule of verify/recipe.py (RECIPE.md "Plugins"): plugins are bound to the guest compiler.  Pure rule checks, no build:
a root of another compiler that declares plugins is red; a host native C++ root loads none and needs none; a guest root still needs the full set; a root with a unit and no
compiler is red."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
REAL = os.path.dirname(os.path.dirname(os.path.dirname(VERIFY)))
sys.path.insert(0, VERIFY)
import recipe  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (": " + str(detail)[:400]) if detail and not ok else ""), flush=True)
    if not ok:
        fails.append(name)


pin = recipe.load_pin(REAL)
ex = pin["external"]
cc, cxx = recipe.pin_drivers()
ROOT = "ports/fnv-nx/toy"
files = [ROOT + "/a.cc", ROOT + "/build.json"]
guest = {"format": "fnvnx-recipe/1", "compiler": {"external": ex["name"], "cc": cc, "cxx": cxx}, "external": [dict(ex)], "flags": {"c": [], "cxx": [recipe.SDD_DEFINE], "asm": []},
         "include": [], "units": "all", "plugins": [{"name": n} for n in recipe.PLUGIN_SOURCES]}
host = {"format": "fnvnx-recipe/1", "compiler": {"cc": recipe.PLUGIN_CXX.replace("g++", "gcc"), "cxx": recipe.PLUGIN_CXX}, "flags": {"c": [], "cxx": [], "asm": []},
        "include": [], "units": "all", "plugins": []}


def errs(rec, target, origin="native"):
    roots = {ROOT: {"path": ROOT, "class": "product", "target": target, "cxx_origin": origin, "recipe": ROOT + "/build.json"}}
    return recipe.plugin_errors(rec, ROOT, files, roots, [ROOT + "/a.cc"], [e["name"] for e in rec.get("external", [])], "", REAL)


e = errs(dict(host, plugins=[{"name": n} for n in recipe.PLUGIN_SOURCES]), "host")
check("host compiler declaring plugins is red", any("plugins are bound to the guest compiler" in x for x in e), e)
e = errs(host, "host")
check("host native C++ root loads none and needs none", not e, e)
e = errs(guest, "guest")
check("guest root with the full set is valid", not e, e)
e = errs(dict(guest, plugins=[{"name": n} for n in list(recipe.PLUGIN_SOURCES)[:1]]), "guest")
check("guest root without the full set is red", any("does not load plugin" in x for x in e), e)
m = recipe.check({k: v for k, v in host.items() if k != "compiler"}, ROOT, files, {ROOT: {"path": ROOT, "class": "product", "target": "host", "recipe": ROOT + "/build.json"}}, pin, REAL)
check("a root with a unit and no compiler is red", any("missing key 'compiler'" in x for x in m), m)
sys.exit(1 if fails else 0)
