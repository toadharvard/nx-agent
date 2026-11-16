#!/usr/bin/env python3
"""The launcher of every generated stage (recipe.Job.generate_one): argv = OUTPUTS TOOL ARGS...; OUTPUTS is the newline-joined list of the declared outputs
of the step (a directory output is written `dir:<path>`). It creates the parent directory of every declared output under /out (the one place that does: a generator never makes directories for its
declared outputs), then runs TOOL as a script (its own directory first on sys.path, argv[0] the tool)."""
import json
import os
import runpy
import sys


def require_mounted(path):
    """PATH when it is under a mount prefix of the stage (recipe.MOUNT_PREFIXES, mounted as /launch/mounts.json), else exit."""
    with open("/launch/mounts.json") as f:
        mounts = json.load(f)
    if not any(path == m or path.startswith(m + "/") for m in mounts):
        raise SystemExit("genlaunch: %s is not under a mount prefix of the stage" % path)
    return path


for o in sys.argv[1].split("\n"):
    if o.startswith("dir:"):   # a declared directory output: the directory itself
        os.makedirs("/out/" + o[4:], exist_ok=True)
        continue
    os.makedirs(os.path.dirname("/out/" + o), exist_ok=True)
tool = require_mounted(sys.argv[2])
sys.path[0] = os.path.dirname(tool)
sys.argv = sys.argv[2:]
runpy.run_path(tool, run_name="__main__")
