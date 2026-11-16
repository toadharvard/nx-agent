#!/usr/bin/env python3
"""recipe.resolved / recipe.entered: a root is resolved once per process however many roots reach it, and a root reached again from its own resolution is a cycle.
  - a diamond (two consumers resolve the same output of one root): the producer's make runs once, both get the same value; another output or kind is its own resolution;
  - a failure is remembered: the second consumer of a red root gets the same red without a second make;
  - a remembered value whose files are gone (valid) is made again;
  - a cycle A -> B -> A (the make of A resolves B, the make of B resolves A) is red with the full path "cycle: A -> B -> A"; entering a root twice by hand is the same red; the stack is
    empty afterwards, so a later diamond is no cycle;
  - a cxx_origin stage that the root's recipe does not contain is red from `resolved` (origin_stage_errors) and from `check` (lifted_stage): one function.
No function of recipe is replaced: the makes are the callers' own closures, as in a real resolution; the stage-level count (stage starts of a diamond of generated stages) needs the declared
environment and is the farm's verdict run of the product roots.
Run: python3 -I <this file>."""
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
A, B, C = T + "a", T + "b", T + "c"


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:400]))
    fails += 0 if ok else 1


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"] + list(a), capture_output=True, text=True, check=True).stdout


def red_of(f):
    try:
        f()
    except recipe.BuildRed as r:
        return r.args[0]
    return None


def main():
    with tempfile.TemporaryDirectory(dir=os.path.expanduser("~")) as td:
        repo = os.path.join(td, "repo")
        os.makedirs(os.path.join(repo, T + "verify"))
        git(repo, "init", "-q")
        rows = []
        for r in (A, B, C):
            os.makedirs(os.path.join(repo, r))
            gen = [{"tool": T + "x.py", "args": [], "inputs": [], "outputs": ["o.txt"]}]
            open(os.path.join(repo, r, "build.json"), "w").write(json.dumps({"format": "fnvnx-recipe/1", "generated": gen, "outputs": {"o": "o.txt"}}))
            rows.append({"path": r, "class": "product", "target": "host", "recipe": r + "/build.json"})
        rows[2]["cxx_origin"] = {"lifted_by": T + "x.py", "stage": "gen:%s:1" % C}   # step 1 does not exist: the recipe has one generated step
        open(os.path.join(repo, T + "x.py"), "w").write("")
        open(os.path.join(repo, T + "verify/admit.json"), "w").write(json.dumps({"format": "fnvnx-admit/1", "roots": rows}))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "toy")
        store = os.path.join(td, "store")
        j = recipe.Job(repo, A, store, 2, recipe.ext_of_store(store))
        recipe.RESOLVE_MEMO.clear()

        calls = []

        def make(tag, v):
            def f():
                calls.append(tag)
                return v
            return f

        v1 = recipe.resolved(j, "uses", B, "o", make("b", {"tree": "t1"}))
        v2 = recipe.resolved(j, "uses", B, "o", make("b-again", {"tree": "t2"}))
        check("a diamond: the producer is made once and both consumers get its one value", calls == ["b"] and v1 is v2 and v2 == {"tree": "t1"}, (calls, v1, v2))
        recipe.resolved(j, "uses", B, "o2", make("b-o2", 1))
        recipe.resolved(j, "pp", B, "o", make("b-pp", 1))
        check("another output or another kind of the same root is its own resolution", calls == ["b", "b-o2", "b-pp"], calls)

        n = []

        def red():
            n.append(1)
            raise recipe.BuildRed({"stage": "gen:b:0", "exit": 1, "stderr": ["boom"]})

        r1, r2 = red_of(lambda: recipe.resolved(j, "uses", B, "bad", red)), red_of(lambda: recipe.resolved(j, "uses", B, "bad", red))
        check("a red root is red for every consumer and is made once", n == [1] and r1 == r2 and r1["stage"] == "gen:b:0" and r1 is not r2, (n, r1, r2))

        gone = {"p": os.path.join(td, "gone-file")}
        open(gone["p"], "w").write("x")
        mk = []
        recipe.resolved(j, "objects", B, "", lambda: mk.append(1) or dict(gone), valid=lambda v: os.path.exists(v["p"]), enter=False)
        recipe.resolved(j, "objects", B, "", lambda: mk.append(2) or dict(gone), valid=lambda v: os.path.exists(v["p"]), enter=False)
        os.remove(gone["p"])
        recipe.resolved(j, "objects", B, "", lambda: mk.append(3) or dict(gone), valid=lambda v: os.path.exists(v["p"]), enter=False)
        check("a remembered value whose files are gone is made again, one that is there is not", mk == [1, 3], mk)

        order = []

        def make_a():
            order.append("a")
            return recipe.resolved(j, "uses", B, "cyc", make_b)

        def make_b():
            order.append("b")
            return recipe.resolved(j, "uses", A, "cyc", make_a)

        c = red_of(lambda: recipe.resolved(j, "uses", A, "cyc", make_a))
        check("a cycle A -> B -> A is red with its full path, each make ran once", c is not None and c["stderr"] == ["cycle: %s -> %s -> %s" % (A, B, A)] and order == ["a", "b"], (c, order))
        check("the resolution stack is empty afterwards", not getattr(recipe._RESOLVING, "stack", None), getattr(recipe._RESOLVING, "stack", None))
        c = red_of(lambda: [recipe.entered(repo, A).__enter__(), recipe.entered(repo, A).__enter__()])
        recipe._RESOLVING.stack = []
        check("entering a root twice is the same red", c is not None and c["stderr"] == ["cycle: %s -> %s" % (A, A)], c)
        d1 = recipe.resolved(j, "uses", A, "dia", make("a-dia", 1))
        check("after a cycle a later resolution is no cycle", d1 == 1)


        # reachability of the root graph: one walk, memoised, cycle-safe
        def toy(name, uses):
            rp = os.path.join(td, name)
            os.makedirs(os.path.join(rp, T + "verify"))
            git(rp, "init", "-q")
            rws = []
            for r, us in uses.items():
                os.makedirs(os.path.join(rp, r))
                open(os.path.join(rp, r, "build.json"), "w").write(json.dumps({"format": "fnvnx-recipe/1", "uses": [{"root": u, "output": "o"} for u in us], "outputs": {"o": "o.txt"}}))
                rws.append({"path": r, "class": "product", "target": "host", "recipe": r + "/build.json"})
            open(os.path.join(rp, T + "verify/admit.json"), "w").write(json.dumps({"format": "fnvnx-admit/1", "roots": rws}))
            git(rp, "add", "-A")
            git(rp, "commit", "-q", "-m", "toy")
            return rp
        D = T + "d"
        rp = toy("reach", {A: [B, C], B: [D], C: [D], D: []})
        rr = recipe.load_admit(rp)
        check("root_reach of a diamond A -> B, C -> D is {B, C, D}", recipe.root_reach(A, rr, rp) == {B, C, D}, recipe.root_reach(A, rr, rp))
        check("depends_on: A reaches D, D does not reach A, a root reaches itself", recipe.depends_on(A, D, rr, rp) and not recipe.depends_on(D, A, rr, rp) and recipe.depends_on(B, B, rr, rp))
        again = recipe.root_reach(A, rr, rp)
        check("the second ask is the memoised set", again is recipe.root_reach(A, rr, rp))
        rp = toy("reachcyc", {A: [B], B: [A]})
        rr = recipe.load_admit(rp)
        check("a use cycle terminates: each reaches the other", recipe.depends_on(A, B, rr, rp) and recipe.depends_on(B, A, rr, rp))


        # an include_of input is itself the edge: a cycle through it is red like a use cycle
        def toy_inc(name, edges):
            rp = os.path.join(td, name)
            os.makedirs(os.path.join(rp, T + "verify"))
            git(rp, "init", "-q")
            rws = []
            for r, tgt in edges.items():
                os.makedirs(os.path.join(rp, r, "inc"))
                open(os.path.join(rp, r, "inc", "x.h"), "w").write("\n")
                gen = [{"tool": T + "x.py", "args": [], "inputs": [{"include_of": t} for t in tgt], "outputs": ["o.txt"]}] if tgt else []
                open(os.path.join(rp, r, "build.json"), "w").write(json.dumps({"format": "fnvnx-recipe/1", "include": [r + "/inc"], "generated": gen, "outputs": {}}))
                rws.append({"path": r, "class": "product", "target": "host", "recipe": r + "/build.json"})
            open(os.path.join(rp, T + "verify/admit.json"), "w").write(json.dumps({"format": "fnvnx-admit/1", "roots": rws}))
            git(rp, "add", "-A")
            git(rp, "commit", "-q", "-m", "toy")
            return rp
        rp = toy_inc("inc-a", {A: [B], B: []})
        rr = recipe.load_admit(rp)
        check("an include_of with no Python import and no `uses` is an edge: A reaches B", recipe.root_reach(A, rr, rp) == {B} and recipe.link_roots_cycle(A, rr, rp) is None, recipe.root_reach(A, rr, rp))
        rp = toy_inc("inc-b", {A: [B], B: [A]})
        rr = recipe.load_admit(rp)
        check("a cycle through include_of is red with its path", recipe.link_roots_cycle(A, rr, rp) == [A, B, A], recipe.link_roots_cycle(A, rr, rp))

        # objects_of: a diamond of two generate steps that read the same roots resolves each root once
        rp = toy("objs", {A: [], B: [], C: []})
        rr = recipe.load_admit(rp)
        jo = recipe.Job(rp, A, os.path.join(td, "store-o"), 2, recipe.ext_of_store(os.path.join(td, "store-o")))
        g = {"objects_of": {"target": "host"}, "tool": "x", "args": [], "inputs": [], "outputs": []}
        before = len([k for k in recipe.RESOLVE_MEMO if k[4] == "objects"])
        r1 = red_of(lambda: jo.objects_input(td, tempfile.mkdtemp(dir=td), g))
        mid = len([k for k in recipe.RESOLVE_MEMO if k[4] == "objects"])
        r2 = red_of(lambda: jo.objects_input(td, tempfile.mkdtemp(dir=td), g))
        after = len([k for k in recipe.RESOLVE_MEMO if k[4] == "objects"])
        check("objects_of: the second generate step that selects the same roots makes no new resolution", mid > before and after == mid and r1 == r2, (before, mid, after, r1, r2))
        check("objects_of: the selection is a rule over the admitted set (B and C, never A itself)", recipe.objects_of_roots({"generated": [g]}, A, rr, rp) == [B, C])

        roots = recipe.load_admit(repo)
        rec = recipe.load_recipe(repo, roots, C)
        errs = recipe.origin_stage_errors(rec, C, roots)
        check("a cxx_origin stage the recipe does not contain is a problem of the one rule", len(errs) == 1 and "gen:%s:I" % C in errs[0], errs)
        o = red_of(lambda: recipe.resolved(j, "uses", C, "o", make("c", 1)))
        check("resolving that root is red before anything is made, with the same message", o is not None and o["stderr"] == errs and "c" not in calls, (o, calls))
        check("a root without a stage origin has no problem", recipe.origin_stage_errors(rec, A, roots) == [])
        ok_rec = dict(rec, generated=rec["generated"] * 2)
        check("with the step present it is green", recipe.origin_stage_errors(ok_rec, C, roots) == [])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
