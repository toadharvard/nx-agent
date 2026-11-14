#!/usr/bin/env python3
"""Generate step of render/shaders/translator: the unit list of the mojoshader external.
usage: gen_sources.py EXTERNAL_DIR OUT
Writes one `mojoshader:<path>` line per C file of the external tree (sorted), the form `sources` of verify/RECIPE.md takes.
The external is the pinned tree tools/mojoshader-glsldk of fnv-nx-build, which holds exactly the files the translator's ABI reaches
(render/shaders/translator/PATCHES.md), so every C file of it is a unit; there is no selection here."""
import os, sys
ext, out = sys.argv[1], sys.argv[2]
rows = sorted(os.path.relpath(os.path.join(d, f), ext).replace(os.sep, "/") for d, _, fs in os.walk(ext) for f in fs if f.endswith(".c"))
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w") as o:
    for r in rows:
        o.write("mojoshader:%s\n" % r)
