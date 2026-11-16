#!/usr/bin/env python3
"""`foreach` steps (RECIPE.md "foreach"): a `run` step with `foreach: {table, key}` is expanded once (recipe.expand_foreach, called by read_recipe and check) into one
static step per row of a tracked TSV table, {COLUMN} replaced in args, outputs, dirs and env values; every other reader sees plain steps.
  - the recipe is valid and expands to one step per row, named NAME.<key>, with the row's fields in its args and outputs;
  - a changed row changes only that row's expanded step (the key of a step is a function of its own row, tool and inputs);
  - a table that is not tracked, an empty table, a short row, a duplicate key, a key that is no plain name, a bad header, an unknown {column}, a lone brace and an
    output that does not name the row are each red at check time; {{ and }} are literal braces.
Static: no sandbox. Run: python3 -I <this file>."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import recipe  # noqa: E402

fails = 0
T = "ports/fnv-nx/"
R = T + "tools"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:500]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def w(repo, rel, text):
    p = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


recipe.deb_pins = lambda: {"toybb": {"version": "1", "sha256": "0" * 64}, "base-files": {"version": "1", "sha256": "1" * 64}}
TABLE = "shape\tclass\nadd\tint\nmul\tint\nvaddps\tsimd\n"


def make(td, name, table, step=None, tracked_table=True):
    repo = os.path.join(td, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    w(repo, R + "/p.sh", "true\n")
    if tracked_table:
        w(repo, R + "/rows.tsv", table)
    st = {"name": "prove", "foreach": {"table": R + "/rows.tsv", "key": "shape"}, "exec": "/usr/bin/sh", "args": ["/in/1", "{shape}", "{class}"],
          "inputs": [R + "/p.sh"], "debs": ["toybb"], "outputs": ["proof/{shape}.txt"]}
    st.update(step or {})
    rec = {"format": "fnvnx-recipe/1", "run": [st], "outputs": {"o": "proof"}}
    w(repo, R + "/build.json", json.dumps(rec, indent=1))
    w(repo, "ports/fnv-nx/verify/env.json", open(os.path.join(os.path.dirname(HERE), "env.json")).read())
    w(repo, "ports/fnv-nx/verify/admit.json", json.dumps({"format": "fnvnx-admit/1", "roots": [
        {"path": R, "class": "product", "target": "host", "recipe": R + "/build.json"}, {"path": T + "verify", "class": "tool"}]}))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "toy")
    return repo, rec


with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
    repo, rec = make(td, "ok", TABLE)
    e = recipe.check(rec, R, recipe.tracked(repo), recipe.load_admit(repo), None, repo)
    check("a recipe with a foreach step is valid", not e, e)
    ex = recipe.read_recipe(repo, R + "/build.json")["run"]
    check("one static step per row, named NAME.<key>, the row's fields in args and outputs",
          [g["name"] for g in ex] == ["prove.add", "prove.mul", "prove.vaddps"] and ex[2]["args"] == ["/in/1", "vaddps", "simd"] and ex[1]["outputs"] == ["proof/mul.txt"] and not any("foreach" in g for g in ex), ex)
    repo2, _ = make(td, "chg", TABLE.replace("mul\tint", "mul\tsimd"))
    ex2 = recipe.read_recipe(repo2, R + "/build.json")["run"]
    check("a changed row changes only that row's step", [a == b for a, b in zip(ex, ex2)] == [True, False, True], ex2)
    ex3 = recipe.read_recipe(make(td, "lit", TABLE, {"args": ["{{x}}", "{shape}"]})[0], R + "/build.json")["run"]
    check("{{ and }} are literal braces", ex3[0]["args"] == ["{x}", "add"], ex3[0]["args"])
    bad = (("a table that is not tracked", dict(table=TABLE, tracked_table=False), "not a tracked file"),
           ("an empty table", dict(table=""), "empty"),
           ("a header only", dict(table="shape\tclass\n"), "no row"),
           ("a short row", dict(table="shape\tclass\nadd\n"), "fields"),
           ("a duplicate key", dict(table="shape\tclass\nadd\tint\nadd\tsimd\n"), "twice"),
           ("a key that is no plain name", dict(table="shape\tclass\na b\tint\n"), "not a plain name"),
           ("a duplicate column", dict(table="shape\tshape\nadd\tint\n"), "unique column"),
           ("a key column that is not in the header", dict(table=TABLE, step={"foreach": {"table": R + "/rows.tsv", "key": "nope"}}), "key column"),
           ("an unknown {column}", dict(table=TABLE, step={"args": ["{nope}"]}), "no column"),
           ("a lone brace", dict(table=TABLE, step={"args": ["a{b"]}), "lone brace"),
           ("an output that does not name the row", dict(table=TABLE, step={"outputs": ["proof/all.txt"]}), "also an output"))
    for what, kw, want in bad:
        r, rc = make(td, "bad%d" % len(os.listdir(td)), **kw)
        e = recipe.check(rc, R, recipe.tracked(r), recipe.load_admit(r), None, r)
        check("%s is red at check time" % what, any(want in x for x in e), e)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
