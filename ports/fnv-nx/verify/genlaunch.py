#!/usr/bin/env python3
"""The launcher of every generated stage (recipe.Job.generate_one): argv = OUTPUTS TOOL ARGS...; OUTPUTS is the newline-joined list of the declared outputs
of the step (a directory output is written `dir:<path>`). It creates the parent directory of every declared output under /out (the one place that does: a generator never makes directories for its
declared outputs), then runs TOOL, a module name `fnvnx.<path>` (the one module path; /imp is the PYTHONPATH), as `python -m` does: runpy.run_module
with run_name `__main__`. The launcher's own directory is not on sys.path, so a bare sibling import has no path."""
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


def require_mounted_module(name):
    """NAME, a module `fnvnx.<path>` whose file is mounted under /imp (the one module root of a stage), else exit: the guard of the launcher's one dynamic load."""
    path = require_mounted("/imp/" + "/".join(name.split(".")) + ".py")
    if not os.path.isfile(path):
        raise SystemExit("genlaunch: %s is not mounted at %s" % (name, path))
    return name


for o in sys.argv[1].split("\n"):
    if o.startswith("dir:"):   # a declared directory output: the directory itself
        os.makedirs("/out/" + o[4:], exist_ok=True)
        continue
    os.makedirs(os.path.dirname("/out/" + o), exist_ok=True)
tool = require_mounted_module(sys.argv[2])
sys.path.pop(0)   # /launch: the launcher's directory is no module path
sys.argv = [tool] + sys.argv[3:]
runpy.run_module(tool, run_name="__main__", alter_sys=True)
