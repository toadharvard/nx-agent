#!/usr/bin/python3.12
"""mkunits.py BUILD SRC LOG OUT - the units of GMP's library from the compile list ccrec.py recorded.
One unit per recorded compile, OUT/u/<object path>.c: the defines the Makefile gave that compile (-DNAME[=VALUE], as given, common ones first),
then the source text (the srcdir's file, or the build directory's own generated file), with a #line naming it. The headers of both trees go to OUT/inc/build and
OUT/inc/src with their layout (the recipe's include order is build, src, as `-I. -I../srcdir` was). A source that includes another C source is an error: the unit
rule would compile that file twice.
Every compile of the log must have the common flags of the first one (the -DOPERATION_x flags aside), else the tool stops."""
import json
import os
import re
import shutil
import sys

build, src, log, out = [os.path.realpath(p) for p in sys.argv[1:5]]
rows = [json.loads(l) for l in open(log)]
if not rows:
    sys.exit("mkunits: empty compile list")
KEEP_FIRST = lambda fl: [f for f in fl if not f.startswith("-DOPERATION_")]
common = KEEP_FIRST(rows[0]["flags"])
# the common flags are defines (written into every unit below), the two include dirs (the recipe's `include`: build, src) and the optimisation and debug flags
# the recipe's own flags carry (-O2 -g); anything else would be lost, so it is an error
ok = [f for f in common if f.startswith("-D") or f in ("-I.", "-g", "-O2") or (f.startswith("-I") and os.path.realpath(os.path.join(rows[0]["cwd"], f[2:])) == src)]
if ok != common:
    sys.exit("mkunits: flags the unit files cannot carry: %s" % [f for f in common if f not in ok])
units, seen = {}, {}
for r in rows:
    if KEEP_FIRST(r["flags"]) != common:
        sys.exit("mkunits: %s has flags %s, not the common %s" % (r["out"], KEEP_FIRST(r["flags"]), common))
    key = (r["out"], r["src"], tuple(f for f in r["flags"] if f.startswith("-DOPERATION_")))
    if key in seen:
        continue
    seen[key] = 1
    if r["out"] in units:
        sys.exit("mkunits: two different compiles write %s" % r["out"])
    units[r["out"]] = r
INC_C = re.compile(rb'^\s*#\s*include\s*[<"][^>"]*\.(?:c|S|asm)[>"]', re.M)
for o, r in sorted(units.items()):
    path = os.path.realpath(os.path.join(r["cwd"], r["src"]))
    text = open(path, "rb").read()
    if INC_C.search(text):
        sys.exit("mkunits: %s includes a C source" % path)
    where = ("src/" + os.path.relpath(path, src)) if path.startswith(src + "/") else ("build/" + os.path.relpath(path, build))
    defs = ["#define %s" % f[2:].replace("=", " ", 1) for f in r["flags"] if f.startswith("-D")]
    name = os.path.splitext(o)[0] + ".c"
    dest = os.path.join(out, "u", name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(("\n".join(defs + ['#line 1 "%s"' % where]) + "\n").encode() + text)
for tag, root in (("build", build), ("src", src)):
    for d, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.endswith(".h"):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    dst = os.path.join(out, "inc", tag, os.path.relpath(p, root))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(p, dst)
print("mkunits: %d units" % len(units))
