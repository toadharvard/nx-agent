#!/usr/bin/env python3
"""Tests for verify/targets.py: the declared-target records, their citations and what is generated from them. Plain script."""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import targets  # noqa: E402
import recipe  # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails.append(name)
        print("   ", detail)


t = targets.load()
repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
check("the record is green", targets.check(t, repo=repo) == [], targets.check(t, repo=repo))
check("the generated prefix", targets.semantic_prefix(t) == ["-mabi=ilp32", "-march=armv8-a+crc+crypto", "-mtune=cortex-a57", "-fPIC", "-ftls-model=local-exec"],
      targets.semantic_prefix(t))
check("recipe.SEMANTICS_FLAGS starts with it", list(recipe.SEMANTICS_FLAGS[:5]) == targets.semantic_prefix(t))
u = copy.deepcopy(t)
del u["guest_platform"]["cite"]["triplet"]
check("a field without a citation is red", any("triplet has no citation" in b for b in targets.check(u)), targets.check(u))
u = copy.deepcopy(t)
u["console_cpu"]["features"].append("sve")
check("a feature without a citation is red", any("feature sve" in b for b in targets.check(u)), targets.check(u))
u = copy.deepcopy(t)
u["console_cpu"]["cite"]["crypto"].append({"assumption": "ASM-999"})
check("an unknown assumption id is red", any("ASM-999" in b for b in targets.check(u)), targets.check(u))
u = copy.deepcopy(t)
u["references"]["x"] = "no/such/file.json"
check("a reference to a missing file is red", any("no/such" in b for b in targets.check(u, repo=repo)), targets.check(u, repo=repo))
import tempfile  # noqa: E402
with tempfile.TemporaryDirectory() as d:
    os.makedirs(d + "/nx")
    open(d + "/nx/Makefile", "w").write("\n" * 26 + "ARCH := -march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIC -ftls-model=local-exec\n")
    only = {k: v for k, v in t["externals"].items()}
    bad = targets.check(t, ext={"libnx": d})
    check("an external citation resolves file, line and quote (libnx Makefile:27 present; svc.h absent is red)", any("svc.h" in b for b in bad) and not any("Makefile" in b for b in bad), bad)
    open(d + "/nx/Makefile", "w").write("\n" * 26 + "ARCH := -march=armv8-a -mtune=cortex-a53\n")
    check("a quote that is not on the cited line is red", any("Makefile:27 does not hold" in b for b in targets.check(t, ext={"libnx": d})), targets.check(t, ext={"libnx": d}))
sys.exit(1 if fails else (print("all passed") or 0))
