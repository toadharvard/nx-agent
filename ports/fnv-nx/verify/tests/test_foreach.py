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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import recipe  # noqa: E402

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
TABLE = "#shape\tclass\nadd\tint\nmul\tint\nvaddps\tsimd\n"


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
           ("a header only", dict(table="#shape\tclass\n"), "no row"),
           ("a short row", dict(table="#shape\tclass\nadd\n"), "fields"),
           ("a duplicate key", dict(table="#shape\tclass\nadd\tint\nadd\tsimd\n"), "twice"),
           ("a key that is no plain name", dict(table="#shape\tclass\na b\tint\n"), "not a plain name"),
           ("a duplicate column", dict(table="#shape\tshape\nadd\tint\n"), "unique column"),
           ("a header without #", dict(table="shape\tclass\nadd\tint\n"), "not a header"),
           ("a # line after the header", dict(table="#shape\tclass\nadd\tint\n# comment\nmul\tint\n"), "no comments"),
           ("a key column that is not in the header", dict(table=TABLE, step={"foreach": {"table": R + "/rows.tsv", "key": "nope"}}), "key column"),
           ("an unknown {column}", dict(table=TABLE, step={"args": ["{nope}"]}), "no column"),
           ("a lone brace", dict(table=TABLE, step={"args": ["a{b"]}), "lone brace"),
           ("an output that does not name the row", dict(table=TABLE, step={"outputs": ["proof/all.txt"]}), "also an output"))
    for what, kw, want in bad:
        r, rc = make(td, "bad%d" % len(os.listdir(td)), **kw)
        e = recipe.check(rc, R, recipe.tracked(r), recipe.load_admit(r), None, r)
        check("%s is red at check time" % what, any(want in x for x in e), e)
# {range: N}: rows i = 0..N-1, n = N; a consumer's `rows: all` input is one stage ref per row in row order, and @rows its mounts
sh = {"name": "shard", "foreach": {"range": 3}, "exec": "/usr/bin/sh", "args": ["/in/1", "{i}", "{n}"], "inputs": [R + "/p.sh"], "debs": ["toybb"], "outputs": ["part/{i}.txt"]}
cons = {"name": "sum", "exec": "/usr/bin/sh", "args": ["/in/1", "@rows"], "inputs": [R + "/p.sh", {"stage": R + ":run:shard", "path": "part/{i}.txt", "rows": "all"}], "debs": ["toybb"], "outputs": ["sum.txt"]}
rr = {"format": "fnvnx-recipe/1", "run": [sh, cons]}
ex = recipe.expand_foreach(rr, None)["run"]
check("range: three steps shard.0..2 with i and n in args and the row in the output", [g["name"] for g in ex[:3]] == ["shard.0", "shard.1", "shard.2"] and ex[2]["args"] == ["/in/1", "2", "3"] and ex[1]["outputs"] == ["part/1.txt"], ex[:3])
check("rows: all is one stage ref per row in row order, @rows the mounts", ex[3]["inputs"][1:] == [{"stage": R + ":run:shard.%d" % k, "path": "part/%d.txt" % k} for k in range(3)] and ex[3]["args"] == ["/in/1", "/in/2", "/in/3", "/in/4"], ex[3])
for what, mut, want in (("a range of 0", lambda: dict(rr, run=[dict(sh, foreach={"range": 0}), cons]), "range must be"),
                        ("a boolean range", lambda: dict(rr, run=[dict(sh, foreach={"range": True}), cons]), "range must be"),
                        ("a rows input naming no foreach step", lambda: dict(rr, run=[sh, dict(cons, inputs=[R + "/p.sh", {"stage": R + ":run:nope", "path": "part/{i}.txt", "rows": "all"}])]), "names no foreach step"),
                        ("a rows path that is no output as written", lambda: dict(rr, run=[sh, dict(cons, inputs=[R + "/p.sh", {"stage": R + ":run:shard", "path": "part/0.txt", "rows": "all"}])]), "no output of foreach step"),
                        ("@rows with no rows input", lambda: dict(rr, run=[sh, dict(cons, inputs=[R + "/p.sh"])]), "@rows arg needs")):
    try:
        recipe.expand_foreach(mut(), None)
        msg = ""
    except ValueError as e:
        msg = str(e)
    check("%s is red" % what, want in msg, msg)
# named rows inputs: two groups, `as`, and @rows:NAME in row order
sh2 = dict(sh, name="shard2", foreach={"range": 2}, outputs=["q/{i}.txt"])
two = dict(cons, inputs=[R + "/p.sh", {"stage": R + ":run:shard", "path": "part/{i}.txt", "rows": "all", "as": "a"}, {"stage": R + ":run:shard2", "path": "q/{i}.txt", "rows": "all", "as": "b"}],
           args=["/in/1", "@rows:b", "@rows:a"])
ex2 = recipe.expand_foreach(dict(rr, run=[sh, sh2, two]), None)["run"][-1]
check("named rows inputs: @rows:NAME is that group's mounts in row order", ex2["args"] == ["/in/1", "/in/5", "/in/6", "/in/2", "/in/3", "/in/4"] and len(ex2["inputs"]) == 6, ex2)
for what, mut, want in (("two rows inputs without as", dict(two, inputs=[two["inputs"][0], dict(two["inputs"][1], **{}), two["inputs"][2]]), "each needs `as`"),
                        ("a duplicate name", dict(two, inputs=[two["inputs"][0], two["inputs"][1], dict(two["inputs"][2], **{"as": "a"})]), "twice"),
                        ("@rows:NAME with no such input", dict(two, args=["@rows:zz"]), "names no rows input"),
                        ("a bare @rows with two named inputs", dict(two, args=["@rows"]), "names no rows input")):
    if what.startswith("two rows"):
        mut = dict(mut, inputs=[mut["inputs"][0]] + [{k: v for k, v in x.items() if k != "as"} for x in mut["inputs"][1:]])
    try:
        recipe.expand_foreach(dict(rr, run=[sh, sh2, mut]), None)
        msg = ""
    except ValueError as e:
        msg = str(e)
    check("%s is red" % what, want in msg, msg)
# build-time table (a stage output): the step stays one marker step that declares the directory `into`; the row template is kept; the recipe is valid
with tempfile.TemporaryDirectory() as td2:
    gen = {"name": "mk", "exec": "/usr/bin/sh", "args": ["/in/1"], "inputs": [R + "/p.sh"], "debs": ["toybb"], "outputs": ["mk.tsv"]}
    dyn = {"name": "prove", "foreach": {"table": {"stage": R + ":run:mk", "path": "mk.tsv"}, "key": "shape", "into": "proof"}, "exec": "/usr/bin/sh", "args": ["/in/1", "{shape}"],
           "inputs": [R + "/p.sh"], "debs": ["toybb"], "outputs": ["proof/{shape}.txt"]}
    r, rc = make(td2, "dyn", TABLE)
    rc = dict(rc, run=[gen, dyn], outputs={"o": "proof"})
    ex = recipe.expand_foreach(rc, r)["run"]
    check("build-time foreach: one marker step that declares only the directory, the row template kept", len(ex) == 2 and ex[1]["dirs"] == ["proof"] and ex[1]["outputs"] == [] and ex[1]["foreach"]["row"]["outputs"] == ["proof/{shape}.txt"], ex[1])
    e = recipe.check(rc, R, recipe.tracked(r), recipe.load_admit(r), None, r)
    check("build-time foreach: the recipe is valid (a consumer reads the directory)", not [x for x in e if "foreach" in x or "run[1]" in x], e)
    for what, mut, want in (("an output outside into", dict(dyn, outputs=["other/{shape}.txt"]), "lie under"),
                            ("an output that does not name the row", dict(dyn, outputs=["proof/all.txt"]), "lie under"),
                            ("a table of another form", dict(dyn, foreach={"table": {"nope": 1}, "key": "shape", "into": "proof"}), "build-time foreach is"),
                            ("an undeclared external table", dict(dyn, foreach={"table": {"external": "x", "path": "t.tsv"}, "key": "shape", "into": "proof"}), "not declared"),
                            ("a dir outside into", dict(dyn, dirs=["other/{shape}"]), "lie under"),
                            ("a dir that does not name the row", dict(dyn, dirs=["proof/all"]), "lie under"),
                            ("no output and no dir", {k: v for k, v in dyn.items() if k != "outputs"}, "lie under")):
        try:
            recipe.expand_foreach(dict(rc, run=[gen, mut]), r)
            msg = ""
        except ValueError as ex2:
            msg = str(ex2)
        check("build-time foreach: %s is red" % what, want in msg, msg)
# a template of directories (`dirs`) keyed by the row like files: the marker still declares only `into`, the row template keeps both
    dd = dict(dyn, outputs=["proof/{shape}.txt"], dirs=["proof/{shape}.d"])
    ex = recipe.expand_foreach(dict(rc, run=[gen, dd]), r)["run"]
    check("build-time foreach: a dirs template is kept in the row, the marker declares only into", ex[1]["dirs"] == ["proof"] and ex[1]["outputs"] == []
          and ex[1]["foreach"]["row"]["dirs"] == ["proof/{shape}.d"] and ex[1]["foreach"]["row"]["outputs"] == ["proof/{shape}.txt"], ex[1])
    dd = {k: v for k, v in dd.items() if k != "outputs"}
    ex = recipe.expand_foreach(dict(rc, run=[gen, dd]), r)["run"]
    check("build-time foreach: a template of dirs alone is valid", ex[1]["dirs"] == ["proof"] and ex[1]["foreach"]["row"]["dirs"] == ["proof/{shape}.d"], ex[1])
    st = recipe.foreach_row_step(ex[1]["foreach"]["row"], {"shape": "add", "class": "int"}, "shape")
    check("build-time foreach: a row step's dirs name its row", st["dirs"] == ["proof/add.d"] and st["name"] == "prove.add", st)
print("fails: %d" % fails)
sys.exit(1 if fails else 0)
