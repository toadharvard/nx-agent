#!/usr/bin/env python3
"""The cost of a stage ref (recipe.stage_output): the requested step and the steps its inputs come from run, no other; a second request for the same step
in one process is answered from the per-process memo; the artifact root of an artifact step is built once per Job. Counted by recipe.Job.stats (kept by
recipe.py itself, nothing is stubbed).
  - step_closure: three steps, the third independent: the closure of the second is {first, second} in recipe order (pure, runs anywhere);
(The run-count part of w28's original, which resolves a stage ref through the sandbox and counts Job.stats, needs this line's exec fixtures and a farm: not here.)
Run: python3 -I <this file>."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import recipe  # noqa: E402

fails = 0
T = "ports/fnv-nx/"
A, B = T + "tools/a", T + "tools/b"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def step(name, inputs, out):
    return {"name": name, "exec": {"python": True}, "args": [], "inputs": inputs, "debs": [], "outputs": [out]}


s1 = step("s1", ["ports/fnv-nx/tools/a/p.py"], "o1")
s2 = step("s2", ["ports/fnv-nx/tools/a/p.py", {"stage": A + ":run:s1", "path": "o1"}], "o2")
s3 = step("s3", ["ports/fnv-nx/tools/a/p.py"], "o3")
j = object.__new__(recipe.Job)
j.root = A
check("step_closure: the second step needs the first and not the third", [g["name"] for g in j.step_closure([s1, s2, s3], "s2")] == ["s1", "s2"], [g["name"] for g in j.step_closure([s1, s2, s3], "s2")])
check("step_closure: an independent step is its own closure", [g["name"] for g in j.step_closure([s1, s2, s3], "s3")] == ["s3"])

sys.exit(1 if fails else 0)
