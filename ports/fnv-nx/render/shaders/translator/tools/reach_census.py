#!/usr/bin/env python3
"""Reachability census of the translator root (measurement tool, not a build).
usage: reach_census.py OBJDIR LD NM OUT.tsv
Roots: the functions declared in include/fnvnx_shaders.h that some object defines. Graph: the relocation graph of the recipe's
-ffunction-sections/-fdata-sections objects, i.e. `ld --gc-sections --print-gc-sections` with -u per root and no entry.
Writes OUT.tsv: object<TAB>section<TAB>reached|collected. Prints the totals."""
import os, re, subprocess, sys
objdir, ld, nm, out = sys.argv[1:5]
here = os.path.dirname(os.path.abspath(__file__))
hdr = open(os.path.join(here, "..", "include", "fnvnx_shaders.h")).read()
hdr = re.sub(r"/\*.*?\*/", "", hdr, flags=re.S)
decl = set(re.findall(r"\b(fnvnx_[a-z0-9_]+)\s*\(", hdr))
objs = sorted(os.path.join(d, f) for d, _, fs in os.walk(objdir) for f in fs if f.endswith(".o"))
defd = set()
for o in objs:
    for l in subprocess.check_output([nm, "--defined-only", o], text=True).splitlines():
        p = l.split()
        if len(p) == 3 and p[1] in "TtWw": defd.add(p[2])
roots = sorted(decl & defd)
cmd = [ld, "-m", "aarch64linux32", "--gc-sections", "--print-gc-sections", "--entry=0", "--unresolved-symbols=ignore-all",
       "-o", os.devnull] + sum((["-u", r] for r in roots), []) + objs
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode: sys.exit("ld failed: " + r.stderr[:2000])
coll = set()
for l in r.stderr.splitlines() + r.stdout.splitlines():
    m = re.match(r"ld[^:]*: removing unused section '(.+)' in file '(.+)'", l)
    if m: coll.add((m.group(2), m.group(1)))
rows = []
for o in objs:
    for l in subprocess.check_output([nm.replace("-nm", "-objdump"), "-h", o], text=True).splitlines():
        m = re.match(r"\s*\d+\s+(\.(?:text|data|rodata|bss)\S*)\s", l)
        if m: rows.append((o, m.group(1), "collected" if (o, m.group(1)) in coll else "reached"))
with open(out, "w") as f:
    for o, s, st in rows: f.write("%s\t%s\t%s\n" % (os.path.relpath(o, objdir), s, st))
print("roots", roots)
print("sections", len(rows), "collected", sum(1 for x in rows if x[2] == "collected"))
