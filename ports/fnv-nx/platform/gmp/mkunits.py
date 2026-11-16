#!/usr/bin/python3.12
"""mkunits.py BUILD SRC LOG OUT - the units of GMP's library from the compile list ccrec.py recorded.
One unit per recorded compile, OUT/u/<object path>.c: the defines the Makefile gave that compile (-DNAME[=VALUE], as given, common ones first),
then the source text (the srcdir's file, or the build directory's own generated file), with a #line naming it. The headers of both trees go to OUT/inc/build and
OUT/inc/src with their layout (the recipe's include order is build, src, as `-I. -I../srcdir` was).
Every compile of the log must have the common flags of the first one (the -DOPERATION_x flags aside), else the tool stops."""
import json
import os
import re
import shutil
import sys

build, src, log, out = [os.path.realpath(p) for p in sys.argv[1:5]]
supplied = sys.argv[5:]   # -I directories the recipe supplies itself (include_uses: another root's headers): their flag is dropped, the include path of the root has them
rows = [json.loads(l) for l in open(log)]
if not rows:
    sys.exit("mkunits: empty compile list")
for r in rows:   # the object's path in the build tree, whatever directory the sub-make compiled in
    r["out"] = os.path.relpath(os.path.realpath(os.path.join(r["cwd"], r["out"])), build)
# Per compile: the defines (-D) become text of the unit; the -I directories must each be one the unit view has (the build tree, the source tree, the compile's own directory, the
# directory of its source: the headers of the last two are written beside the unit below, the first two are the recipe's `include`, or a directory the recipe supplies); -g and -O2 are
# the recipe's own flags. Anything else would be lost, so it is an error.
real = lambda r, d: os.path.realpath(os.path.join(r["cwd"], d))
def own_dirs(r):   # the compile's own directory in the build tree and in the source tree, and the directory of its source file
    rel = os.path.relpath(os.path.realpath(r["cwd"]), build)
    return {os.path.realpath(r["cwd"]), os.path.realpath(os.path.join(src, rel)), os.path.dirname(real(r, r["src"]))}
def check_flags(r):
    for f in r["flags"]:
        if f.startswith("-D") or f in ("-g", "-O2"):
            continue
        if f == "-ffloat-store":
            continue   # GCC manual (Optimize Options): -ffloat-store only keeps values from living in wider registers; the guest is aarch64, whose FP registers hold exactly the declared type (FLT_EVAL_METHOD 0), so it changes no result
        if f.startswith("-W") and not f.startswith(("-Werror", "-Wl,", "-Wa,", "-Wp,")):
            continue   # a warning option only chooses which diagnostics are printed, never the object code (GCC manual, "Options to Request or Suppress Warnings")
        if f.startswith("-I") and (f[2:] in supplied or real(r, f[2:]) in own_dirs(r) | {build, src}):
            continue
        sys.exit("mkunits: %s: the flag %s cannot be carried by a unit" % (r["out"], f))
units, seen = {}, {}
for r in rows:
    check_flags(r)
    key = (r["out"], r["src"], tuple(f for f in r["flags"] if f.startswith("-D")))
    if key in seen:
        continue
    seen[key] = 1
    if r["out"] in units:
        sys.exit("mkunits: two different compiles write %s" % r["out"])
    units[r["out"]] = r
# A source that #includes a C source (mpn/generic/mul_fft.c under WANT_ADDSUB) is not examined: the unit's own preprocessing opens an included file only when its condition holds, and a
# C source there is no header of OUT/inc, so an active include of one is a red preprocessing stage, never a silent difference.
for o, r in sorted(units.items()):
    path = os.path.realpath(os.path.join(r["cwd"], r["src"]))
    text = open(path, "rb").read()
    where = ("src/" + os.path.relpath(path, src)) if path.startswith(src + "/") else ("build/" + os.path.relpath(path, build))
    defs = ["#define %s" % (f[2:].replace("=", " ", 1) if "=" in f else f[2:] + " 1") for f in r["flags"] if f.startswith("-D")]   # -DNAME is NAME defined as 1 (C99 6.10.2 as cc documents it)
    name = os.path.splitext(o)[0] + ".c"
    dest = os.path.join(out, "u", name)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(("\n".join(defs + ['#line 1 "%s"' % where]) + "\n").encode() + text)
# A quoted #include resolves first in the directory of the including file (mpf/fits_sint.c includes its sibling "fits_s.h"). The unit is not in that directory, so the headers of each
# unit's source directory are written beside the unit. Two source directories that map to one unit directory with different headers of one name are a stop: the unit could not tell them apart.
sib = {}
for o, r in sorted(units.items()):
    udir = os.path.join(out, "u", os.path.dirname(o))
    for sdir in sorted(d for d in own_dirs(r) if os.path.isdir(d)):
      for fn in sorted(os.listdir(sdir)):
        if fn.endswith(".h"):
            data = open(os.path.join(sdir, fn), "rb").read()
            key = (udir, fn)
            if key in sib and sib[key] != data:
                sys.exit("mkunits: %s/%s: two source directories give the unit directory different headers of this name" % (os.path.dirname(o), fn))
            if key not in sib:
                sib[key] = data
                os.makedirs(udir, exist_ok=True)
                open(os.path.join(udir, fn), "wb").write(data)
for tag, root in (("build", build), ("src", src)):
    for d, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if fn.endswith(".h"):
                p = os.path.join(d, fn)
                if os.path.exists(p):
                    dst = os.path.join(out, "inc", tag, os.path.relpath(p, root))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copyfile(p, dst)
print("mkunits: %d units, %d with per-file defines (wrapper units)" % (len(units), sum(1 for r in units.values() if any(f.startswith("-D") and f not in ("-DHAVE_CONFIG_H", "-D__GMP_WITHIN_GMP") for f in r["flags"]))))
