#!/usr/bin/env python3 -I
"""Gate: declared font files == the Wine files of the platform's static face set (0 extra, 0 missing).
Usage: fontgate.py <faces.txt (platform_faces.py stdout)> <wine_fontmap.tsv> <declared-dir>
mapped set = union of the files of every face in faces.txt; a face absent from the map, or mapped `none`, is listed (no Wine file) and
not a file. Exit 1 on any extra/missing file or any face missing from the map. Prints sha256 of every declared file.
"""
import hashlib, os, sys
faces = [l.rstrip("\n") for l in open(sys.argv[1]) if l.strip()]
mp = {}
for l in open(sys.argv[2]):
    if l.startswith("#") or not l.strip():
        continue
    f, v = l.rstrip("\n").split("\t")
    mp[f] = [] if v == "none" else v.split(",")
unmapped = [f for f in faces if f not in mp]
want = {x for f in faces if f in mp for x in mp[f]}
have = set(os.listdir(sys.argv[3]))
extra, missing = sorted(have - want), sorted(want - have)
nofile = sorted(f for f in faces if f in mp and not mp[f])
print("faces %d; mapped files %d; declared files %d; extra %d; missing %d; faces without a Wine file %d; unmapped faces %d" % (
    len(faces), len(want), len(have), len(extra), len(missing), len(nofile), len(unmapped)), file=sys.stderr)
for n in nofile: print("no Wine file:", n, file=sys.stderr)
for n in extra: print("extra:", n, file=sys.stderr)
for n in missing: print("missing:", n, file=sys.stderr)
for n in sorted(have & want):
    print(hashlib.sha256(open(os.path.join(sys.argv[3], n), "rb").read()).hexdigest(), n)
sys.exit(1 if extra or missing or unmapped else 0)
