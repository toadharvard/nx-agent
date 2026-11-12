#!/usr/bin/env python3
"""Class check of the FreeType entries of platform/build.json against FreeType's own build rules (the gen_units.py derivation).

    check_units.py TREE [BUILD_JSON]       TREE = the pinned mirror (the recipe's external `freetype`, fnv-nx-build mirror/freetype-src)

Runs gen_units.py on TREE with the tracked declarations and patches and requires, exit 1 listing each difference:
  (1) the generated stage's outputs in build.json are exactly the files the generator writes (so no output is missing and none is unwritten);
  (2) `sources` names the stage's units.list;
  (3) the unit set (units.list plus the generated units, <gen>/src/<dir>/<name>.c) equals units.txt, the committed record of the derivation;
  (4) the `include` list holds, in this order, the generated include directory, the external's include and one /ext/freetype/src/<dir> per unit directory of that set;
  (5) the stage's arguments name the tracked declarations and the patch directory."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
P = "ports/fnv-nx/platform/freetype/"


def main(a):
    tree = a[0]
    bj = json.load(open(a[1] if len(a) > 1 else os.path.join(HERE, "..", "build.json")))
    red = []
    stages = [g for g in bj["generated"] if g["tool"] == P + "gen_units.py"]
    if len(stages) != 1:
        print("check_units: %d gen_units.py stages" % len(stages))
        return 1
    g = stages[0]
    want_args = ["/ext/freetype", "/in/" + P + "modules.enable", "/in/" + P + "options.enable", "/in/" + P + "patches", "/out/freetype"]
    if g["args"] != want_args:
        red.append("args %r != %r" % (g["args"], want_args))
    if sorted(g["inputs"]) != sorted([P + "modules.enable", P + "options.enable", P + "patches"]):
        red.append("inputs %r" % g["inputs"])
    with tempfile.TemporaryDirectory() as out:
        subprocess.run([sys.executable, "-I", os.path.join(HERE, "gen_units.py"), tree, os.path.join(HERE, "modules.enable"),
                        os.path.join(HERE, "options.enable"), os.path.join(HERE, "patches"), out], check=True)
        made = sorted("freetype/" + os.path.relpath(os.path.join(d, f), out) for d, _, fs in os.walk(out) for f in fs)
        listed = [l.split(":", 1)[1] for l in open(os.path.join(out, "units.list")).read().split()]
    if sorted(g["outputs"]) != made:
        red.append("outputs differ: build.json only %s, generator only %s" % (sorted(set(g["outputs"]) - set(made)), sorted(set(made) - set(g["outputs"]))))
    if bj.get("sources") != "freetype/units.list":
        red.append("sources is %r, not freetype/units.list" % (bj.get("sources"),))
    gen_units = [m[len("freetype/gen/"):] for m in made if m.startswith("freetype/gen/src/") and m.endswith(".c")]
    allu = sorted(listed + gen_units)
    rec = open(os.path.join(HERE, "units.txt")).read().split()
    if allu != rec:
        red.append("unit set %s differs from units.txt %s" % (len(allu), len(rec)))
    dirs = sorted({u.rsplit("/", 1)[0] for u in allu})
    want_inc = ["ports/fnv-nx/platform/freetype/gen/include", "/ext/freetype/include"] + ["/ext/freetype/" + d for d in dirs]
    got_inc = [i for i in bj["include"] if "freetype" in i]
    if got_inc != want_inc:
        red.append("include differs: build.json only %s, derived only %s" % ([i for i in got_inc if i not in want_inc], [i for i in want_inc if i not in got_inc]))
    for r in red:
        print("check_units:", r)
    if not red:
        print("freetype units: %d (%d listed from the external, %d generated), outputs %d, include %d" % (len(allu), len(listed), len(gen_units), len(made), len(want_inc)))
    return 1 if red else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
