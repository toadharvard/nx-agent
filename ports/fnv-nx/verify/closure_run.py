#!/usr/bin/env python3
"""closure_run.py - run a python tool as `python3 -I closure_run.py MODULES.json TOOL.py [ARGS...]` exactly as `python3 -I TOOL.py ARGS` would, and write
the file of every module in sys.modules at exit (also after SystemExit or an exception) to MODULES.json, for importclosure.check_modules. Modules present
before the tool starts (the interpreter's own start-up: site, .pth files of the installed environment, this runner) are not the tool's imports and are not
recorded: they are a function of the interpreter and the env id, which are in the key."""
import json
import runpy
import sys


def main():
    out, tool, sys.argv = sys.argv[1], sys.argv[2], sys.argv[2:]
    before = set(sys.modules)
    code = 0
    try:
        runpy.run_path(tool, run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        mods = sorted({getattr(m, "__file__", None) for n, m in list(sys.modules.items()) if n not in before and n != "__main__" and getattr(m, "__file__", None)})
        with open(out, "w") as f:
            json.dump(mods, f)
    return code


if __name__ == "__main__":
    sys.exit(main())
