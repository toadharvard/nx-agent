#!/usr/bin/env python3
"""The PP keying rule (recipe.Job.pp_subtrees / pp_uses): a PP stage's declared inputs are exactly what its sandbox can read. Pure: the inputs are computed from a
toy external and toy `uses` outputs in a temporary store; no stage runs.
  - an include inside an external mounts that subtree: a change outside it changes no PP input;
  - a change inside it changes the subtree's tree id;
  - an include equal to the external's root is the whole external (its tree id);
  - a flag path (-include FILE) mounts that file only;
  - an include_uses output is a PP input, any other `uses` output is not (unless a flag names it).
Run: python3 -I <this file>."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import cas  # noqa: E402
import recipe  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  " + repr(info)[:300]))
    fails += 0 if ok else 1


def put(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(text)


def job(store, ext, rec):
    j = object.__new__(recipe.Job)
    j.store, j.ext, j.rec = store, ext, rec
    return j


with tempfile.TemporaryDirectory() as td:
    store = os.path.join(td, "store")
    x = os.path.join(td, "x")
    for i in range(10):
        put(x, "u/%d.cpp" % i, "int f%d;\n" % i)
    put(x, "inc/a.h", "#define A 1\n")
    put(x, "inc/sub/b.h", "#define B 1\n")
    put(x, "other/c.h", "#define C 1\n")
    put(x, "flag.h", "#define F 1\n")
    ext = lambda: {"x": {"at": "/ext/x", "host": x, "tree": cas.ingest(x, store)}, "cc": {"at": "/ext/cc", "host": os.path.join(td, "cc"), "tree": "cc-tree"}}   # noqa: E731

    def subs(tokens, skip=("cc",)):
        return job(store, ext(), {}).pp_subtrees(tokens, skip)
    base = subs(["/ext/x/inc"])
    check("an include inside an external mounts that subtree at its own path", [i["at"] for i in base] == ["/ext/x/inc"], base)
    put(x, "u/3.cpp", "int changed;\n")
    put(x, "other/c.h", "#define C 2\n")
    check("a change outside the include subtree (a unit, another directory) changes no PP input", subs(["/ext/x/inc"]) == base)
    put(x, "inc/sub/b.h", "#define B 2\n")
    check("a change inside the subtree changes its tree id", subs(["/ext/x/inc"]) != base)
    whole = subs(["/ext/x"])
    check("an include equal to the external's root is the whole external", whole == [{"at": "/ext/x", "tree": ext()["x"]["tree"]}], whole)
    check("a path nested in another named path folds into it", [i["at"] for i in subs(["/ext/x/inc", "/ext/x/inc/sub"])] == ["/ext/x/inc"])
    fl = subs(["-include", "/ext/x/flag.h"])
    check("a flag path mounts that file only", [i["at"] for i in fl] == ["/ext/x/flag.h"], fl)
    check("the skipped (compiler) external is not taken here, and a name that is no path adds nothing", subs(["/ext/cc/bin", "-O2", "/ext/x/nope"]) == [])
    # uses
    rec = {"uses": [{"root": "r", "output": "INC"}, {"root": "r", "output": "TSV"}], "include_uses": [{"root": "r", "output": "INC"}]}
    j = job(store, ext(), rec)
    j.use_inputs_cached = [{"at": "/uses/r/INC", "tree": "t1"}, {"at": "/uses/r/TSV", "tree": "t2"}]
    check("an include_uses output is a PP input, another `uses` output is not", [i["at"] for i in j.pp_uses(["-O2"])] == ["/uses/r/INC"], j.pp_uses(["-O2"]))
    check("a `uses` output that a flag names is a PP input too", [i["at"] for i in j.pp_uses(["-include", "/uses/r/TSV/x.h"])] == ["/uses/r/INC", "/uses/r/TSV"])
print("FAILED %d" % fails if fails else "all pass")
sys.exit(1 if fails else 0)
