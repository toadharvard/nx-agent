#!/usr/bin/env python3
"""Driver of the python steps of the lift/proof/shapeprove root (stdlib only; the step's interpreter is the declared python3 deb, so z3, capstone and keystone come from pinned wheels).

    python3 -I stage.py --closure DIR [--wheel WHEEL]... [--exec-copy SRC:DST]... --script FILE -- ARGS...

--closure DIR   the prover closure (stage `closure`): DIR/proof and DIR/fnvlift, put first on sys.path
--wheel WHEEL   a wheel file, extracted to /scratch/site (a wheel is a zip; its extension modules cannot load from it) and put on sys.path
--exec-copy S:D copy file S to D and make it executable (a mounted input has no execute bit; the isla binary is run from its copy)
--script FILE   the tool script that runs, as __main__, with sys.argv = [FILE] + ARGS (it is a tracked file of the root, not part of the prover's identity)."""
import argparse
import os
import runpy
import shutil
import sys
import zipfile


def main(argv):
    ap = argparse.ArgumentParser(); ap.add_argument("--closure", required=True); ap.add_argument("--wheel", action="append", default=[]); ap.add_argument("--exec-copy", action="append", default=[]); ap.add_argument("--script", required=True)
    a, rest = ap.parse_known_args(argv)
    if rest[:1] == ["--"]: rest = rest[1:]
    site = "/scratch/site"
    os.makedirs(site, exist_ok=True)
    for w in a.wheel:
        with zipfile.ZipFile(w) as z: z.extractall(site)
    for sd in a.exec_copy:
        src, dst = sd.split(":", 1)
        os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copyfile(src, dst); os.chmod(dst, 0o755)
    sys.path[0:0] = [os.path.abspath(a.closure), site]
    sys.argv = [a.script] + rest
    runpy.run_path(a.script, run_name="__main__")      # a tool script ends with sys.exit(main()): its status is the step's
    return 0


if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
