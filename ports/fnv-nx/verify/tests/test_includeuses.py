#!/usr/bin/env python3
"""Selftest of `include_uses` and directory outputs (RECIPE.md "Outputs and uses"). Pure: recipes are written into a temp repo."""
import json, os, sys, tempfile, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import recipe
recipe.read_recipe = lambda repo, path: json.load(open(os.path.join(repo, path)))   # the synthetic recipes have no profile: the plain reader

fails = []
def check(what, got, want=True):
    if got != want:
        fails.append("%s: got %r want %r" % (what, got, want))

check("dir_output: a path with generated outputs below it", recipe.dir_output("gen", ["gen/a.h", "x.c"]), True)
check("dir_output: a file is not a directory output", recipe.dir_output("gen/a.h", ["gen/a.h"]), False)
check("dir_output: no output below", recipe.dir_output("gen", ["x.c"]), False)
with tempfile.TemporaryDirectory() as repo:
    def w(p, o):
        os.makedirs(os.path.dirname(os.path.join(repo, p)), exist_ok=True)
        json.dump(o, open(os.path.join(repo, p), "w"))
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    w("ports/fnv-nx/p/build.json", {"outputs": {"GEN": "gen", "F": "gen/a.h"}, "generated": [{"tool": "t.py", "args": [], "inputs": [], "outputs": ["gen/a.h", "gen/b.h"]}]})
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    roots = {"ports/fnv-nx/p": {"recipe": "ports/fnv-nx/p/build.json", "class": "product"}, "ports/fnv-nx/c": {"recipe": "ports/fnv-nx/c/build.json", "class": "product"}}
    ie = lambda rec: recipe.include_uses_errors(rec, roots, repo)
    U = {"root": "ports/fnv-nx/p", "output": "GEN"}
    check("a directory output in uses and include_uses is valid", ie({"uses": [U], "include_uses": [U]}), [])
    check("no include_uses: no error", ie({"uses": [U]}), [])
    check("include_uses not in uses is red", len(ie({"include_uses": [U]})) == 1, True)
    F = {"root": "ports/fnv-nx/p", "output": "F"}
    check("a file output is no include directory", len(ie({"uses": [F], "include_uses": [F]})) == 1, True)
    check("a duplicate is red", len(ie({"uses": [U], "include_uses": [U, U]})) == 1, True)
    check("a bad shape is red", len(ie({"uses": [U], "include_uses": ["p"]})) == 1, True)
    open(os.path.join(repo, "ports/fnv-nx/p", "gen_dummy"), "w").close()
    os.makedirs(os.path.join(repo, "ports/fnv-nx/p", "gen")); open(os.path.join(repo, "ports/fnv-nx/p", "gen", "tracked.h"), "w").close()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    check("a tracked file inside the directory output is red", len(ie({"uses": [U], "include_uses": [U]})) == 1, True)
# the include carry: a generated directory of P's include is not carried to a consumer (no tracked file: nothing to mount)
with tempfile.TemporaryDirectory() as repo:
    P, C = "ports/fnv-nx/p", "ports/fnv-nx/c"
    os.makedirs(os.path.join(repo, P)); os.makedirs(os.path.join(repo, C))
    json.dump({"include": [P + "/inc", P + "/gen/inc"]}, open(os.path.join(repo, P, "build.json"), "w"))
    roots = {P: {"recipe": P + "/build.json", "class": "product", "target": "guest"}, C: {"class": "product", "target": "guest"}}
    files = [P + "/inc/a.h", P + "/x.c"]
# unitstage: an include of a mounted `include_uses` directory is accepted, one that no input names is refused
import argparse, unitstage
with tempfile.TemporaryDirectory() as d:
    os.makedirs(d + "/src"); os.makedirs(d + "/store")
    json.dump({"exes": {"g++": 1}, "inputs": []}, open(d + "/rt.json", "w")); json.dump({"argv": []}, open(d + "/fl.json", "w"))
    mk = lambda **kw: unitstage.Build(argparse.Namespace(store=d + "/store", cold=False, cxx="g++", toolrt=d + "/rt.json", flags_file=d + "/fl.json", src_root=d + "/src@/src", include=["/uses/r/GEN"], **kw))
    check("a mounted use directory is an include", mk(mounted=["/uses/r/GEN"]).iargs, ["-I/uses/r/GEN"])
    try:
        mk()
        check("an include that no input mounts is refused", False)
    except unitstage.UnitError:
        pass
if fails:
    print("\n".join("FAIL " + f for f in fails)); sys.exit(1)
print("includeuses: ok")
