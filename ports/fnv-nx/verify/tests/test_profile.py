#!/usr/bin/env python3
"""Tests for the recipe profile (recipe.expand_profile, effective deltas, RECIPE.md "Profiles"): the expanded recipe of a root that names its profile
is the recipe that spelled every profile field (so every stage key, a function of the expanded values, is unchanged), a restated profile field is red,
and the variant deltas flags_add / defines_add append. Pure: no sandbox. Plain script, one PASS/FAIL line per case."""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)
        print("   ", detail)


REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
PIN = recipe.load_pin(REPO)
EXT = PIN["external"]
LEGACY = {   # the loader recipe as it was written before profiles: every profile field spelled
    "format": "fnvnx-recipe/1",
    "compiler": {"external": EXT["name"], "cc": recipe.pin_drivers()[0], "cxx": recipe.pin_drivers()[1]},
    "flags": {"c": ["-O2"], "cxx": [], "asm": []},
    "include": ["ports/fnv-nx/loader/include"],
    "units": "all",
    "plugins": [{"name": n} for n in recipe.PLUGIN_SOURCES],
    "external": [dict(EXT)],
}
NEW = {"format": "fnvnx-recipe/1", "profile": "guest", "flags": {"c": ["-O2"], "cxx": [], "asm": []}, "include": ["ports/fnv-nx/loader/include"], "units": "all"}

ex, err = recipe.expand_profile(NEW, PIN)
check("guest profile expands to the recipe that spelled every field", ex == LEGACY and not err, (err, ex))
check("the expansion is idempotent and has no profile key", recipe.expand_profile(ex, PIN) == (ex, []) and "profile" not in ex)

more = {"name": "libnx", "repo": "r", "ref": "x", "commit": "c", "tree_sha256": "t", "at": "/ext/libnx"}
r2 = dict(NEW, external=[more])
ex2, err2 = recipe.expand_profile(r2, PIN)
check("the root's own externals follow the profile's", not err2 and ex2["external"] == [dict(EXT), more], ex2["external"])

for k, v in (("compiler", {"cc": "x"}), ("plugins", [{"name": "fnvnx_ftoi"}])):
    _, e = recipe.expand_profile(dict(NEW, **{k: v}), PIN)
    check("restating %s is red" % k, any("not written in the recipe" in x and k in x for x in e), e)
_, e = recipe.expand_profile(dict(NEW, external=[dict(EXT)]), PIN)
check("restating a profile external is red", any(EXT["name"] in x for x in e), e)
_, e = recipe.expand_profile(dict(NEW, profile="cloud"), PIN)
check("an unknown profile is red", any("unknown" in x for x in e), e)
hx, e = recipe.expand_profile(dict(NEW, profile="host"), PIN)
check("host profile: the env.json compiler, no plugin, no external", not e and hx["plugins"] == [] and hx["external"] == [] and hx["compiler"]["cxx"] == recipe.PLUGIN_CXX, hx)
check("no profile: the recipe is returned as it is", recipe.expand_profile(copy.deepcopy(LEGACY), PIN) == (LEGACY, []))

# variant deltas
base = copy.deepcopy(NEW)
base["variants"] = {"asan": {"flags_add": {"c": ["-fsanitize=address"], "cxx": ["-fsanitize=address"]}, "defines_add": ["FNVNX_ASAN=1"]},
                    "whole": {"flags": {"c": ["-O0"], "cxx": [], "asm": []}}}
b, _ = recipe.expand_profile(base, PIN)
ea = recipe.effective(b, "asan")
check("flags_add and defines_add append to the base flags", ea["flags"] == {"c": ["-O2", "-fsanitize=address", "-DFNVNX_ASAN=1"], "cxx": ["-fsanitize=address", "-DFNVNX_ASAN=1"], "asm": ["-DFNVNX_ASAN=1"]}, ea["flags"])
check("the base recipe is not changed by a delta", recipe.effective(b, "")["flags"] == NEW["flags"] and b["variants"]["asan"]["flags_add"]["c"] == ["-fsanitize=address"])
check("a whole replacement still replaces", recipe.effective(b, "whole")["flags"]["c"] == ["-O0"])
check("a variant keeps the profile's compiler, plugins and externals", ea["plugins"] == LEGACY["plugins"] and ea["compiler"] == LEGACY["compiler"] and ea["external"] == LEGACY["external"])

# the target of the profile is the admit.json target of the root
roots = {"ports/fnv-nx/loader": {"path": "ports/fnv-nx/loader", "class": "product", "target": "host", "recipe": "x"}}
errs = recipe.check(copy.deepcopy(NEW), "ports/fnv-nx/loader", [], roots, PIN, REPO)
check("a profile that is not the root's target is red", any("the admit.json target" in x for x in errs), errs[:3])

sys.exit(1 if fails else 0)
