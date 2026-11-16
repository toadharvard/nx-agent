#!/usr/bin/python3.12
"""ccrec.py - the compiler of GMP's `make` inside the generate stage (gmpgen.sh): it records every compile and runs none.
GMP's own Makefiles decide which sources are compiled and with which defines (mpn/*.c once per operation: -DOPERATION_x). The recipe compiles the units, so make runs
with this program as CC: a compile call (-c SOURCE -o OBJECT) appends one JSON line {cwd, src, flags, out} to $CCREC_LOG and writes the empty object and dependency
files the Makefile's rules move; any other call (a link, a preprocess) is an error, because nothing here may produce an object code of GMP."""
import json
import os
import sys

a = sys.argv[1:]
if "-c" not in a:
    sys.exit("ccrec: not a compile call: " + " ".join(a))
out, srcs, flags, dep, i = None, [], [], None, 0
while i < len(a):
    t = a[i]
    if t == "-o":
        out, i = a[i + 1], i + 2
    elif t in ("-MT", "-MQ", "-MF"):
        if t == "-MF":
            dep = a[i + 1]
        i += 2
    elif t in ("-MD", "-MP", "-MMD", "-c"):
        i += 1
    elif t.endswith((".c", ".S", ".s")) and not t.startswith("-"):
        srcs.append(t)
        i += 1
    else:
        flags.append(t)
        i += 1
if out is None or len(srcs) != 1:
    sys.exit("ccrec: expected one source and -o: " + " ".join(a))
if dep:
    open(dep, "w").close()
open(out, "w").close()
with open(os.environ["CCREC_LOG"], "a") as f:
    f.write(json.dumps({"cwd": os.getcwd(), "src": srcs[0], "flags": flags, "out": out}) + "\n")
