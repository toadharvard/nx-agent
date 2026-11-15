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
# INCLUDE outputs of tracked files (a header-only root) and the header-home rule
with tempfile.TemporaryDirectory() as repo:
    def w(p, o):
        os.makedirs(os.path.dirname(os.path.join(repo, p)), exist_ok=True)
        json.dump(o, open(os.path.join(repo, p), "w")) if isinstance(o, dict) else open(os.path.join(repo, p), "w").write(o)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    T = "ports/fnv-nx/"
    w(T + "h/build.json", {"outputs": {"INCLUDE": "include"}}); w(T + "h/include/x.h", "int x;\n")
    w(T + "u/build.json", {"outputs": {"INCLUDE": "include"}}); w(T + "u/include/y.h", "int y;\n")
    w(T + "m/build.json", {"outputs": {"MIX": "inc"}, "generated": [{"tool": "t.py", "args": [], "inputs": [], "outputs": ["inc/g.h"]}]}); w(T + "m/inc/t.h", "int t;\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    files = recipe.tracked(repo)
    roots = {T + n: {"recipe": T + n + "/build.json", "class": "product", "target": "guest"} for n in "huc m".replace(" ", "")}
    H = {"root": T + "h", "output": "INCLUDE"}
    ue = lambda rec, root: recipe.uses_errors(rec, root, roots, repo, files)
    check("outputs: a directory of tracked files is a valid output", ue({"outputs": {"INCLUDE": "include"}}, T + "h"), [])
    check("outputs: a directory of both generated and tracked files is red", len(ue(json.load(open(os.path.join(repo, T, "m/build.json"))), T + "m")) == 1, True)
    check("include_uses accepts a directory output of tracked files", recipe.include_uses_errors({"uses": [H], "include_uses": [H]}, roots, repo), [])
    rec = {"include": [T + "h/include"], "uses": [H]}
    check("include_home: an include in a directory output the root uses", recipe.include_home(T + "h/include", rec, roots, repo, files), True)
    check("include_home: a file below it", recipe.include_home(T + "h/include/sub", rec, roots, repo, files), True)
    check("include_home: an admitted root that is not used is no home", recipe.include_home(T + "u/include", rec, roots, repo, files), False)
    check("include_home: no uses, no home", recipe.include_home(T + "h/include", {"include": [T + "h/include"]}, roots, repo, files), False)
    check("output_kind: srcdir", recipe.output_kind("include", T + "h", [], files), "srcdir")
    check("output_kind: nothing there", recipe.output_kind("nope", T + "h", [], files), None)
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
