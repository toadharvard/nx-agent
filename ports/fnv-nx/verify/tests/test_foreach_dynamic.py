#!/usr/bin/env python3
"""A build-time foreach (RECIPE.md "foreach"): the table is an earlier step's output, the rows are made when it exists.
  - one root: step `mk` writes rows.tsv, step `prove` has foreach {table: {stage: mk, path: rows.tsv}, key, into: proof} and writes proof/<row>.txt per row: the root is green and proof/ holds one file per row;
  - one more row in the table: `mk` (whose script holds the table) and the new row execute; the old rows are hits: a row's key is a function of its own row, tool and inputs;
  - an empty table is red; an output outside `into` is red.
Run: python3 -I <this file>. Needs root and the declared environment (the pinned python runtime); run on a farm."""
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
R = T + "tools/d"


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
recipe.Job._deb_tree = lambda self, tmp, name, pin: os.makedirs(os.path.join(tmp, "deb-" + name))
recipe.env_blocked = lambda repo: None


def make(td, name, table, into_out="proof/{shape}.txt"):
    repo = os.path.join(td, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    w(repo, R + "/mk.py", "import sys\nopen('/out/mk.tsv', 'w').write(%r)\n" % table)
    w(repo, R + "/row.py", "import sys, os\nos.makedirs('/out/proof', exist_ok=True)\nopen('/out/proof/' + sys.argv[1] + '.txt', 'w').write(sys.argv[1] + sys.argv[2])\n")
    mk = {"name": "mk", "exec": {"python": True}, "args": ["/in/1"], "inputs": [R + "/mk.py"], "debs": [], "outputs": ["mk.tsv"]}
    dyn = {"name": "prove", "foreach": {"table": {"stage": R + ":run:mk", "path": "mk.tsv"}, "key": "shape", "into": "proof"}, "exec": {"python": True},
           "args": ["/in/1", "{shape}", "{class}"], "inputs": [R + "/row.py"], "debs": [], "outputs": [into_out]}
    w(repo, R + "/build.json", json.dumps({"format": "fnvnx-recipe/1", "run": [mk, dyn], "outputs": {"o": "proof"}}))
    w(repo, T + "verify/env.json", open(os.path.join(VERIFY, "env.json")).read())
    w(repo, T + "verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [{"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}, {"path": T + "verify", "class": "tool"}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo


def build(repo, store, tag):
    out = tempfile.mkdtemp(prefix="o-" + tag, dir=os.path.dirname(store))
    return recipe.build(repo, R, store, out), out


if os.geteuid() != 0:
    print("SKIP: needs root and the declared environment")
    sys.exit(0)
td = tempfile.mkdtemp(prefix="fed-")
store = os.path.join(td, "store")
os.makedirs(store)
r1, o1 = build(make(td, "a", "shape\tclass\nadd\tint\nmul\tint\n"), store, "1")
check("a table made at build time gives one step per row, the directory holds one file per row", r1["status"] == "green", r1.get("failure"))
r2, _ = build(make(td, "b", "shape\tclass\nadd\tint\nmul\tint\nsub\tint\n"), store, "2")
check("one more row executes the table step (its script holds the table) and exactly one row step; the two old rows are hits", r2["status"] == "green" and r2["stages"]["run"] == 2 and r2["stages"]["hit"] >= 2, r2["stages"])
r3, _ = build(make(td, "c", "shape\tclass\n"), store, "3")
check("an empty table is red", r3["status"] == "red", r3["status"])
try:
    build(make(td, "d", "shape\tclass\nadd\tint\n", "other/{shape}.txt"), store, "4")
    msg = ""
except (recipe.RecipeError, ValueError) as e:
    msg = str(e.errors if hasattr(e, "errors") else e)
check("an output outside `into` is red at check", "lie under" in msg, msg)
sys.exit(1 if fails else 0)
