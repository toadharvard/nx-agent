#!/usr/bin/env python3
"""Generate step of lift/rcpcert: the unit list, the form `sources` of verify/RECIPE.md takes.
usage: gen_sources.py OUT
Writes one `<root>:<path>` line per unit besides the root's own rcpcert.c: the reference table lift/rt/rcptab.c, the one source of fnvl_rcp32 / fnvl_rsqrt32 (rule 2: no copy)."""
import sys
with open(sys.argv[1], "w") as o:
    o.write("ports/fnv-nx/lift/rt:rcptab.c\n")
