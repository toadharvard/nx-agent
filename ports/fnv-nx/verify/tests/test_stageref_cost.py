#!/usr/bin/env python3
"""The cost of a stage ref (recipe.stage_output): the requested step and the steps its inputs come from run, no other; a second request for the same step
in one process is answered from the per-process memo; the artifact root of an artifact step is built once per Job. Counted by recipe.Job.stats (kept by
recipe.py itself, nothing is stubbed).
  - step_closure: three steps, the third independent: the closure of the second is {first, second} in recipe order (pure, runs anywhere);
  - two roots (A: s1, s2 <- s1, s3 independent; B: one step reading A's s2): resolving B's output evaluates s1, s2 and b1, not s3; resolving it again evaluates none.
Run: python3 -I <this file>. The second part needs root and the declared environment (the pinned python runtime); run on a farm."""
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
A, B = T + "tools/a", T + "tools/b"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def step(name, inputs, out):
    return {"name": name, "exec": {"python": True}, "args": [], "inputs": inputs, "debs": [], "outputs": [out]}


s1 = step("s1", ["ports/fnv-nx/tools/a/p.py"], "o1")
s2 = step("s2", ["ports/fnv-nx/tools/a/p.py", {"stage": A + ":run:s1", "path": "o1"}], "o2")
s3 = step("s3", ["ports/fnv-nx/tools/a/p.py"], "o3")
j = object.__new__(recipe.Job)
j.root = A
check("step_closure: the second step needs the first and not the third", [g["name"] for g in j.step_closure([s1, s2, s3], "s2")] == ["s1", "s2"], [g["name"] for g in j.step_closure([s1, s2, s3], "s2")])
check("step_closure: an independent step is its own closure", [g["name"] for g in j.step_closure([s1, s2, s3], "s3")] == ["s3"])

if os.geteuid() != 0 or not os.path.exists(os.path.join(VERIFY, "env.json")):
    print("SKIP the run part: needs root and the declared environment")
    sys.exit(1 if fails else 0)


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


recipe.deb_pins = lambda: {"base-files": {"version": "1", "sha256": "1" * 64}}
recipe.Job._deb_tree = lambda self, tmp, name, pin: os.makedirs(os.path.join(tmp, "deb-" + name))
recipe.env_blocked = lambda repo: None
td = tempfile.mkdtemp(prefix="sref-")
repo = os.path.join(td, "r")
os.makedirs(repo)
git(repo, "init", "-q")
w(repo, A + "/p.py", "import sys\nsrc = open(sys.argv[2]).read() if len(sys.argv) > 2 else ''\nopen('/out/' + sys.argv[1], 'w').write(src + sys.argv[1])\n")
w(repo, B + "/p.py", "import sys\nopen('/out/ob', 'w').write(open(sys.argv[1]).read())\n")
for i in (s1, s2, s3):   # script argv: the output name, then the input file when the step has a stage input
    i["args"] = ["/in/1"] + [i["outputs"][0]] + (["/in/2"] if len(i["inputs"]) > 1 else [])
w(repo, A + "/build.json", json.dumps({"format": "fnvnx-recipe/1", "run": [s1, s2, s3], "outputs": {"o": "o2"}}))
sb = step("b1", [B + "/p.py", {"stage": A + ":run:s2", "path": "o2"}], "ob")
sb["args"] = ["/in/1", "/in/2"]
w(repo, B + "/build.json", json.dumps({"format": "fnvnx-recipe/1", "run": [sb], "outputs": {"o": "ob"}}))
w(repo, T + "verify/env.json", open(os.path.join(VERIFY, "env.json")).read())
w(repo, T + "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
    {"path": A, "class": "product", "target": "host", "recipe": A + "/build.json"}, {"path": B, "class": "product", "target": "host", "recipe": B + "/build.json"}, {"path": T + "verify", "class": "tool"}]}))
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "toy")
cache = os.path.join(td, "cache")
os.makedirs(cache + "/store")
before = recipe.Job.stats["run_one"]
ref = {"stage": B + ":run:b1", "path": "ob"}
host, _ = recipe.resolve_input(A, ref, repo, cache)
n1 = recipe.Job.stats["run_one"] - before
check("a stage ref evaluates the requested step and its producers (b1 <- s2 <- s1), not s3", n1 == 3 and open(host).read() == "o1o2", (n1, open(host).read()))
recipe.resolve_input(A, ref, repo, cache)
check("the same ref again in one process evaluates nothing (memo)", recipe.Job.stats["run_one"] - before == n1, recipe.Job.stats)
sys.exit(1 if fails else 0)
