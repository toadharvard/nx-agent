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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import recipe  # noqa: E402

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


# the mounts of a python step (recipe.python_mounts): the runtime brings paths the host resolves (/lib/x86_64-linux-gnu, /usr/local/bin) that nest with the layout of the mounted
# debs; the sandbox refuses nested paths (sandbox.check_doc). (a) a layout link or directory that a runtime path lies under or over yields; (b) a declared input or scratch that clashes is red
from fnvnx.verify import sandbox


def doc_of(ins, links, scratch=()):
    return {"format": "fnvnx-stage/1", "stage": "s", "argv": ["/x"], "cwd": "/work", "env": {}, "inputs": sorted(ins, key=lambda i: i["at"]), "outputs": ["/out"], "links": links, "scratch": list(scratch)}


T0 = "0" * 64
layout = [{"at": "/usr", "tree": T0}, {"at": "/etc", "tree": T0}]
links = [{"at": "/lib", "to": "usr/lib"}, {"at": "/bin", "to": "usr/bin"}]
rt = [{"at": "/lib/x86_64-linux-gnu", "tree": T0}, {"at": "/usr/local/bin", "tree": T0}, {"at": "/opt/py", "tree": T0}]
own = [{"at": "/in/1", "tree": T0}]
ins_, lk_, er_ = recipe.python_mounts(layout, links, own, ["/scratch"], rt)
check("a nested runtime path under a usr-merge link and under a layout directory mounts green (the layout entries yield)",
      not er_ and [l["at"] for l in lk_] == ["/bin"] and sorted(i["at"] for i in ins_) == ["/etc", "/in/1", "/lib/x86_64-linux-gnu", "/opt/py", "/usr/local/bin"], (ins_, lk_, er_))
try:
    sandbox.check_doc(doc_of(ins_, lk_, ["/scratch"]))
    ok_doc = True
except sandbox.SbxError as e:
    ok_doc = repr(e)
check("the mounts are accepted by the sandbox's own path rule", ok_doc is True, ok_doc)
try:
    sandbox.check_doc(doc_of(layout + own + rt, links))
    raw = None
except sandbox.SbxError as e:
    raw = str(e)
check("the layout and the runtime together, as they were, are refused (paths nest)", raw is not None and "nest" in raw, raw)
ins2, lk2, er2 = recipe.python_mounts(layout, links, [{"at": "/usr/local/bin/x", "tree": T0}], ["/opt/py/tmp"], rt)
check("a declared input at a path that clashes with a runtime path is red, never dropped",
      any(e == "step input /usr/local/bin/x overlaps the python runtime path /usr/local/bin" for e in er2), er2)
check("a scratch directory that clashes with a runtime path is red", any(e == "step input /opt/py/tmp overlaps the python runtime path /opt/py" for e in er2), er2)
check("the step's own input is never among the dropped entries", any(i["at"] == "/usr/local/bin/x" for i in ins2), ins2)
check("no clash, no change: layout kept whole, runtime appended", recipe.python_mounts(layout, links, own, [], [{"at": "/opt/py", "tree": T0}]) == (layout + own + [{"at": "/opt/py", "tree": T0}], links, []))

with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    repo = make(td, "g", GOOD)
    errs = recipe.check(json.load(open(os.path.join(repo, R, "build.json"))), R, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    check("the recipe with a python tool step is valid", not errs, errs)
    j1, root1 = produced(repo, td, "1")
    check("a step importing two pinned modules and writing under scratch is green", not j1.red and open(os.path.join(root1, "o.txt")).read() != "", j1.red)
    # a consumer's `uses` of a root whose output only a run step makes: Job.produce runs the producer's run stages too (it once ran only the generated ones,
    # so the output was absent: "[Errno 2] ... root/sdm.txt" through imptest/lifted -> lift/sdmrows -> lift/sdm)
    try:
        ut = j1.use_tree(tempfile.mkdtemp(prefix="rp-use", dir=td), {"root": R, "output": "o"})
    except (OSError, recipe.BuildRed) as e:
        ut = repr(e)
    check("a `uses` of a run-only producer's output is the tree its own build makes", ut == recipe.cas.ingest(os.path.join(root1, "o.txt"), j1.store), ut)
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
