#!/usr/bin/env python3
"""Selftest of recipe.resolve_input on the path "." (the whole tree of the base is inside it) and on paths that leave it. Synthetic git repo, no private input."""
import os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import recipe

fails = []
with tempfile.TemporaryDirectory() as d:
    repo, cache = d + "/repo", d + "/cache"
    os.makedirs(repo + "/r/sub"); os.makedirs(cache)
    open(repo + "/r/a.txt", "w").write("a"); open(repo + "/r/sub/b.txt", "w").write("b")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    host, ident = recipe.resolve_input("r", ".", repo, cache)
    if not (os.path.isfile(os.path.join(host, "r", "a.txt")) or os.path.isfile(os.path.join(host, "a.txt"))):
        fails.append("'.' does not resolve to the whole tracked tree: %r" % host)
    for p in ("..", "sub/../..", "/etc", "", "../r"):
        try:
            recipe.resolve_input("r", p, repo, cache)
            fails.append("path %r must be red" % p)
        except ValueError:
            pass
if fails:
    print("\n".join("FAIL " + f for f in fails)); sys.exit(1)
print("resolve_dot: ok")
