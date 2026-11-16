#!/usr/bin/env python3
"""Tests for verify/targets.py: the declared-target records, their citations and what is generated from them. Plain script."""
import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): `verify` is imported by its qualified name
from fnvnx.verify import targets  # noqa: E402
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
check("the generated prefix: platform, CPU, code model, then the flags of the data-model rows",
      targets.semantic_prefix(t) == ["-mabi=ilp32", "-march=armv8-a+crc+crypto", "-mtune=cortex-a57", "-fPIC", "-ftls-model=local-exec", "-fsigned-char", "-fshort-wchar"],
      targets.semantic_prefix(t))
check("recipe.SEMANTICS_FLAGS starts with it", list(recipe.SEMANTICS_FLAGS[:7]) == targets.semantic_prefix(t))
check("the data-model flags are the table's `flag` rows, in row order", targets.datamodel_flags(t) == ["-fsigned-char", "-fshort-wchar"], targets.datamodel_flags(t))
u = copy.deepcopy(t)
del u["guest_platform"]["cite"]["triplet"]
check("a field without a citation is red", any("triplet has no citation" in b for b in targets.check(u)), targets.check(u))
u = copy.deepcopy(t)
u["console_cpu"]["features"].append("sve")
check("a feature without a citation is red", any("feature sve" in b for b in targets.check(u)), targets.check(u))
u = copy.deepcopy(t)
u["console_cpu"]["cite"]["crypto"].append({"assumption": "ASM-" + "999"})   # built at run time: no literal id for the registry scan
BAD = "ASM-" + "999"
check("an unknown assumption id is red", any(BAD in b for b in targets.check(u)), targets.check(u))
u = copy.deepcopy(t)
u["references"]["x"] = "no/such/file.json"
check("a reference to a missing file is red", any("no/such" in b for b in targets.check(u, repo=repo)), targets.check(u, repo=repo))
import tempfile  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "..", "..", "contract", "target"))   # gen_header imports its sibling fpcr by its plain name, as the stage runs it
import gen_header as targets_header  # noqa: E402
check("fpcr: DN=0 FZ=0 RMode=RN, AHP=0: the derived integer is 0", targets.fpcr_value(t) == 0 and "FNVNX_FPCR 0x0ull" in targets_header.render(t), targets_header.render(t))
u = copy.deepcopy(t)
u["console_cpu"]["fpcr"]["RMode"] = 3
check("fpcr: the row changes the integer and the generated header (RMode at bit 22)", targets.fpcr_value(u) == 3 << 22 and "FNVNX_FPCR 0xc00000ull" in targets_header.render(u), targets_header.render(u))
u = copy.deepcopy(t)
u["console_cpu"]["fpcr"]["FZ"] = 2
check("fpcr: a value outside the field's width is red, and no header is written", any("fpcr.FZ" in b for b in targets.check(u)), targets.check(u))
try:
    targets_header.render(u)
    check("fpcr: render refuses a bad row", False)
except ValueError:
    check("fpcr: render refuses a bad row", True)
u = copy.deepcopy(t)
u["console_cpu"]["fpcr"]["IOE"] = 1
check("fpcr: an unknown field is red", any("exactly the fields" in b for b in targets.check(u)), targets.check(u))
u = copy.deepcopy(t)
del u["console_cpu"]["cite"]["fpcr"]
check("fpcr: the row needs its citation", any("fpcr has no citation" in b for b in targets.check(u)), targets.check(u))
with tempfile.TemporaryDirectory() as d:
    os.makedirs(d + "/nx")
    open(d + "/nx/Makefile", "w").write("\n" * 26 + "ARCH := -march=armv8-a+crc+crypto -mtune=cortex-a57 -mtp=soft -fPIC -ftls-model=local-exec\n")
    only = {k: v for k, v in t["externals"].items()}
    bad = targets.check(t, ext={"libnx": d})
    check("an external citation resolves file, line and quote (libnx Makefile:27 present; svc.h absent is red)", any("svc.h" in b for b in bad) and not any("Makefile" in b for b in bad), bad)
    open(d + "/nx/Makefile", "w").write("\n" * 26 + "ARCH := -march=armv8-a -mtune=cortex-a53\n")
    check("a quote that is not on the cited line is red", any("Makefile:27 does not hold" in b for b in targets.check(t, ext={"libnx": d})), targets.check(t, ext={"libnx": d}))
sys.exit(1 if fails else (print("all passed") or 0))
