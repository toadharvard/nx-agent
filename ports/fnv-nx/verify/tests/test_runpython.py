#!/usr/bin/env python3
"""A `run` tool step with `exec: {"python": true}` (RECIPE.md "Tool steps"): the pinned python runtime of the generated stages (recipe.Job.pyrt: the interpreter and
env.json python.modules), through the one function, with the step's own inputs, scratch and stage refs.
  - a step that imports two pinned modules (z3, capstone) and writes under its scratch directory is green;
  - a step that imports a module that env.json does not pin is red (the stage cannot see it);
  - a changed module pin (its installed-files digest) re-keys the step;
  - a step that names debs for the python runtime is red at check time.
Run: python3 -I <this file>. Needs root and the declared environment (the pinned python modules installed); run on a farm."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import recipe  # noqa: E402

fails = 0
T = "ports/fnv-nx/"
R = T + "tools"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


recipe.deb_pins = lambda: {"base-files": {"version": "1", "sha256": "1" * 64}}


def toy_tree(self, tmp, name, pin):
    os.makedirs(os.path.join(tmp, "deb-" + name))   # the layout deb ships nothing here
    return None


recipe.Job._deb_tree = toy_tree
recipe.env_blocked = lambda repo: None

GOOD = "import z3, capstone, sys\nopen('/scratch/x','w').write(z3.get_version_string()+capstone.__version__)\nopen('/out/o.txt','w').write(open('/scratch/x').read())\n"
BAD = "import yaml\nopen('/out/o.txt','w').write('x')\n"


def make(td, name, script, debs=None):
    repo = os.path.join(td, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    w(repo, R + "/p.py", script)
    step = {"name": "py", "exec": {"python": True}, "args": ["/in/1"], "inputs": [R + "/p.py"], "debs": debs or [], "outputs": ["o.txt"], "scratch": ["/scratch"]}
    rec = {"format": "fnvnx-recipe/1", "run": [step], "outputs": {"o": "o.txt"}}
    w(repo, R + "/build.json", json.dumps(rec, indent=1))
    w(repo, "ports/fnv-nx/verify/env.json", open(os.path.join(VERIFY, "env.json")).read())
    w(repo, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}, {"path": T + "verify", "class": "tool"}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def produced(repo, td, tag):
    j = recipe.Job(repo, R, os.path.join(td, "store"), 2, recipe.ext_of_store(os.path.join(td, "store")))
    ws = tempfile.mkdtemp(prefix="rp-" + tag, dir=td)
    recipe._copy(repo, [f for f in j.files if recipe.under(f, R)], R, os.path.join(ws, "root"))
    j.externals(ws)
    j.run_all(ws)
    return j, os.path.join(ws, "root")


with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    repo = make(td, "g", GOOD)
    errs = recipe.check(json.load(open(os.path.join(repo, R, "build.json"))), R, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    check("the recipe with a python tool step is valid", not errs, errs)
    j1, root1 = produced(repo, td, "1")
    check("a step importing two pinned modules and writing under scratch is green", not j1.red and open(os.path.join(root1, "o.txt")).read() != "", j1.red)
    jb, _ = produced(make(td, "b", BAD), td, "2")
    check("a step importing an unpinned module is red", bool(jb.red), jb.red)
    orig = recipe.pinned_modules

    def other(repo_):
        ms = orig(repo_)
        return [(m, v, "f" * 64 if k == 0 else d, t, p) for k, (m, v, d, t, p) in enumerate(ms)]
    recipe.pinned_modules = other
    j2, _ = produced(repo, td, "3")
    recipe.pinned_modules = orig
    check("a changed module pin re-keys the step", bool(j2.keys - j1.keys), (len(j1.keys), len(j2.keys)))
    r4 = make(td, "d", GOOD, debs=["base-files"])
    j4 = recipe.Job(r4, R, os.path.join(td, "store"), 2, recipe.ext_of_store(os.path.join(td, "store")))
    e4 = recipe.check(j4.rec, R, j4.files, j4.roots, repo=r4)
    check("a python step with debs is red at check time", any("python" in e for e in e4), e4)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
