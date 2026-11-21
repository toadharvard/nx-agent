#!/usr/bin/env python3
"""One recipe per target (RECIPE.md): a per-target recipe holds the target-dependent fields only. `generated`, `run`, `uses` and `outputs` live once, in the base
recipe; `units` and `include` are inherited and a copy equal to the base's is red. No compiler needed. Run: python3 -I <this file>."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


GEN = [{"tool": "t/g.py", "args": ["x"], "inputs": [], "outputs": ["g.c"]}]
BASE = {"format": recipe.FORMAT, "compiler": {"cc": "gcc", "cxx": "g++"}, "flags": {"c": ["-O1"], "cxx": [], "asm": []}, "include": ["inc"], "units": "all",
        "generated": GEN, "outputs": {"g": "g.c"}, "external": [{"name": "pack", "commit": "1" * 40}]}
HOST = {"format": recipe.FORMAT, "compiler": {"cc": "clang", "cxx": "clang++"}, "flags": {"c": ["-O2"], "cxx": [], "asm": []}, "external": [{"name": "tc", "commit": "2" * 40}]}

check("a target that inherits is green", recipe.per_target_errors(HOST, BASE) == [], recipe.per_target_errors(HOST, BASE))
for k, v in (("generated", GEN), ("run", []), ("uses", []), ("outputs", {})):
    e = recipe.per_target_errors(dict(HOST, **{k: v}), BASE)
    check("a per-target recipe with %s is red" % k, any(repr(k) in x for x in e), e)
e = recipe.per_target_errors(dict(HOST, units="all"), BASE)
check("an identical units copy is red", any("'units'" in x and "redundant" in x for x in e), e)
e = recipe.per_target_errors(dict(HOST, include=["inc"]), BASE)
check("an identical include copy is red", any("'include'" in x for x in e), e)
check("a differing include is green", recipe.per_target_errors(dict(HOST, include=["inc", "inc2"]), BASE) == [])
check("an external named in both with different content is red", bool(recipe.per_target_errors(dict(HOST, external=[{"name": "pack", "commit": "3" * 40}]), BASE)))
eff = recipe.inherit(HOST, BASE)
check("the effective recipe takes the stages, outputs, units, include and base externals from the base; the target fields stay its own",
      eff["generated"] == GEN and eff["outputs"] == BASE["outputs"] and eff["units"] == "all" and eff["include"] == ["inc"] and eff["compiler"]["cc"] == "clang"
      and [x["name"] for x in eff["external"]] == ["tc", "pack"], eff)
check("a differing units/include of the per-target recipe wins", recipe.inherit(dict(HOST, include=["z"]), BASE)["include"] == ["z"])
with tempfile.TemporaryDirectory() as td:
    for n, d in (("base.json", BASE), ("host.json", HOST)):
        with open(os.path.join(td, n), "w") as f:
            json.dump(d, f)
    roots = {"r": {"path": "r", "class": "product", "target": "guest", "recipe": "base.json", "variants": {"host": {"target": "host", "recipe": "host.json"}}}}
    try:
        got = recipe.load_recipe(td, roots, "r@host")
    except Exception as ex:  # noqa: BLE001
        got = repr(ex)
    check("load_recipe of root@host is the inherited effective recipe", isinstance(got, dict) and got.get("generated") == GEN and got["compiler"]["cc"] == "clang", got)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
