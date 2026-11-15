#!/usr/bin/env python3
"""The launcher of every generated stage (recipe.Job.generate_one): argv = OUTPUTS TOOL ARGS...; OUTPUTS is the newline-joined list of the declared outputs
of the step. It creates the parent directory of every declared output under /out (the one place that does: a generator never makes directories for its
declared outputs), then runs TOOL as a script (its own directory first on sys.path, argv[0] the tool)."""
import os
import runpy
import sys

for o in sys.argv[1].split("\n"):
    os.makedirs(os.path.dirname("/out/" + o), exist_ok=True)
tool = sys.argv[2]
sys.path[0] = os.path.dirname(tool)
sys.argv = sys.argv[2:]
runpy.run_path(tool, run_name="__main__")
