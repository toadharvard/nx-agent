#!/usr/bin/env python3
# reads: ports/fnv-nx/verify
"""Tests for verify/gate_pure.py cross-root imports (admit.json `imports`) and gate_admit.imports_errors. Cases, each on a toy tree
run as the gate runs (cwd = checkout root, env GATE_OUT): (1) a root that imports a module of another root WITHOUT declaring it is red
(import-unlisted) even though the module exists; (2) the same import declared in `imports` is green; (3) a declared import does not
make the imported root's sibling directories importable, and the importing root's own files are the universe (the imported root is
judged in its own run); (4) a declared-import cycle, an unknown root, a self import and a repeat are errors of the table, and the
gate is red; (6) one root of several `trees` is one puregate run and the table rules on trees. (5) with no admit root inside a DIR the DIR alone is the unit (the old behaviour). Plain script."""
import json
import os
import subprocess
import sys
import tempfile
import atexit  # noqa: E402
import shutil  # noqa: E402
tempfile.tempdir = tempfile.mkdtemp(prefix="scratch-")  # every scratch dir of this test lives here and is removed at exit
atexit.register(shutil.rmtree, tempfile.tempdir, True)

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))   # ports (`fnvnx` is ports/fnvnx -> fnv-nx): the siblings are imported by their qualified name
from fnvnx.verify import gate_admit  # noqa: E402

FAILED = []
GATE = os.path.join(VERIFY, "gate_pure.py")
T = "ports/fnv-nx"


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, (" -- " + str(detail)[:500]) if (detail and not ok) else ""), flush=True)
    if not ok:
        FAILED.append(name)


def tree(files, roots):
    d = tempfile.mkdtemp(prefix="gp-")
    for p, t in files.items():
        os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
        open(os.path.join(d, p), "w").write(t)
    os.makedirs(os.path.join(d, T, "verify"), exist_ok=True)
    json.dump({"format": "fnvnx-admit/1", "roots": roots}, open(os.path.join(d, T, "verify/admit.json"), "w"))
    return d


def run(d):
    out = os.path.join(d, "out")
    os.makedirs(out, exist_ok=True)
    p = subprocess.run([sys.executable, "-I", GATE], cwd=d, env={"GATE_OUT": out, "PATH": os.environ["PATH"]}, capture_output=True, text=True)
    return p.returncode, json.load(open(os.path.join(out, "pure.json"))), p.stderr


def root(p, entry=()):
    return {"path": T + "/" + p, "class": "tool", "entry": [T + "/" + e for e in entry]}


def main():
    G = T + "/lift/gen/g.py"
    gen = {G: "import fnvlift\nfrom fnvlift import m\nx = m.f()\n"}
    lib = {T + "/lift/fnvlift/__init__.py": "", T + "/lift/fnvlift/m.py": "def f():\n    return 1\n"}
    files = dict(gen, **lib)
    d = tree(files, [root("lift/gen", entry=["lift/gen/g.py"]), root("lift/fnvlift")])
    rc, res, err = run(d)
    check("a bare import of another root's module is red (the qualified name is the one spelling)", rc == 2 and res["status"] == "red" and G in res["uncertified"], (res, err))
    qfiles = dict(lib, **{G: "from fnvnx.lift.fnvlift import m\nx = m.f()\n"})
    d = tree(qfiles, [root("lift/gen", entry=["lift/gen/g.py"]), root("lift/fnvlift")])
    rc, res, err = run(d)
    check("the qualified import needs no declaration: the root edge is derived from the source; the closure holds g.py, m.py and __init__.py",
          rc == 0 and res["status"] == "green" and res["universe"] == 3, (res, err))
    f3 = dict(qfiles, **{T + "/lift/other/o.py": "y = 2\n"})
    f3[G] = "import o\n"
    d = tree(f3, [root("lift/gen", entry=["lift/gen/g.py"]), root("lift/fnvlift"), root("lift/other")])
    rc, res, err = run(d)
    check("a derived import does not open the other roots: a bare `import o` (root lift/other) is red", rc == 2 and G in res["uncertified"], (res, err))
    rows = [dict(root("lift/gen"), imports=[]), root("lift/fnvlift")]
    e = gate_admit.imports_errors(rows)
    check("an `imports` field in admit.json is an error (the imports are derived)", len(e) == 1 and "derived" in e[0], e)
    d = tree(qfiles, [dict(root("lift/gen", entry=["lift/gen/g.py"]), imports=[T + "/lift/fnvlift"]), root("lift/fnvlift")])
    rc, res, err = run(d)
    check("the gate is red on a leftover `imports` field", rc == 2 and res["status"] == "red" and "derived" in res["detail"], (res, err))
    dd = tree({T + "/lift/gen/g.py": "import importlib\nn = 'x'\nimportlib.import_module(n)\n__import__('os')\n"}, [root("lift/gen")])
    edges, derr = gate_admit.derived_imports([root("lift/gen")], [T + "/lift/gen/g.py"], dd)
    check("a dynamic import with a non-literal name is an error, a literal one is a name", len(derr) == 1 and ":3:" in derr[0], derr)
    mut = {T + "/a/x.py": "import q\nfrom fnvnx.lift.fnvlift import m\nimport os, json\n", T + "/lift/fnvlift/m.py": "", T + "/b/q.py": ""}
    dm = tree(mut, [root("a"), root("lift/fnvlift"), root("b")])
    edges, derr = gate_admit.derived_imports([root("a"), root("lift/fnvlift"), root("b")], list(mut), dm)
    check("the derived edge set: the qualified module is an edge; stdlib and a bare name are none", edges[T + "/a"] == [T + "/lift/fnvlift"] and not derr, (edges, derr))
    check("a derived edge set is cyclic when two roots import each other and that is no error (mounts are per file closure)",
          gate_admit.derived_imports([root("lift/gen"), root("lift/fnvlift")], [T + "/lift/gen/g.py", T + "/lift/fnvlift/m.py"],
                                     tree({T + "/lift/gen/g.py": "from fnvnx.lift.fnvlift import m\n", T + "/lift/fnvlift/m.py": "from lift.gen import g\n"}, []))[1] == [])
    # host readers and the universe
    verify = {T + "/verify/recipe.py": "import time\nt = time.time()\n"}
    stage = {T + "/lift/gen/g.py": "import recipe\n"}
    d = tree(dict(verify, **stage), [root("lift/gen", entry=["lift/gen/g.py"]), root("verify")])
    rc, res, err = run(d)
    check("a stage file importing verify/recipe without declaring it is red", rc == 2 and T + "/lift/gen/g.py" in res["uncertified"], (res, err))
    d = tree(dict(verify, **{T + "/lift/gen/g.py": "from verify import recipe\n"}), [root("lift/gen", entry=["lift/gen/g.py"]), dict(root("verify"), gates=["selftests"])])
    rc, res, err = run(d)
    check("a stage file importing verify/recipe by its qualified name is red too (host reader)", rc == 2 and T + "/lift/gen/g.py" in res["uncertified"], (res, err))
    drv = {T + "/lift/gen/g.py": "x = 1\n", T + "/lift/gen/gate_drv.py": "import recipe\n"}
    d = tree(dict(verify, **drv), [root("lift/gen", entry=["lift/gen/g.py"]), dict(root("verify"), gates=["selftests"])])
    rc, res, err = run(d)
    check("a gate driver in lift/ that imports recipe and is no entry point and imported by no stage is outside the universe: green",
          rc == 0 and res["universe"] == 1, (res, err))
    d = tree({T + "/lift/gen/h.py": "x = 1\n", T + "/lift/gen/sub/gate_drv.py": "import recipe\n", **verify},
             [root("lift/gen", entry=["lift/gen/h.py"]), dict(root("verify"), gates=["selftests"])])
    rc2, res2, err2 = run(d)
    check("a moved file keeps its classification: the same driver one directory deeper and the entry renamed is still outside and green",
          rc2 == 0 and res2["universe"] == 1, (res2, err2))
    # entries: recipe generators
    rec = {T + "/prod/build.json": json.dumps({"generated": [{"tool": T + "/lift/gen/g.py"}]}), T + "/lift/gen/g.py": "from fnvnx.lift import fnvlift\n"}
    pr = {"path": T + "/prod", "class": "product", "recipe": T + "/prod/build.json"}
    d = tree(dict(rec, **lib), [root("lift/gen"), root("lift/fnvlift"), pr])
    rc, res, err = run(d)
    check("a generator tool of a recipe is an entry point: its closure is the universe", rc == 0 and res["universe"] == 2, (res, err))
    d = tree(dict(rec, **{T + "/lift/gen/g.py": "from fnvnx.lift import fnvlift\n", T + "/lift/fnvlift/m.py": "import time\nt = time.time()\n", T + "/lift/fnvlift/__init__.py": "from . import m\n"}),
             [root("lift/gen"), root("lift/fnvlift"), pr])
    rc, res, err = run(d)
    check("a clock read in a file the generator reaches is red", rc == 2 and T + "/lift/fnvlift/m.py" in res["uncertified"], (res, err))
    # no directory name is special: only the import closure counts
    d = tree({T + "/lift/gen/g.py": "import tests.x\n", T + "/lift/gen/tests/x.py": "import time\nt = time.time()\n", T + "/lift/gen/tests/y.py": "z = 1\n",
              T + "/lift/gen/other/w.py": "import time\nt = time.time()\n"}, [root("lift/gen", entry=["lift/gen/g.py"])])
    rc, res, err = run(d)
    check("a file named tests/x.py that a stage imports is in the universe (and red for its clock read); not-imported files in tests/ or any directory are not",
          rc == 2 and T + "/lift/gen/tests/x.py" in res["uncertified"] and res["universe"] == 2, (res, err))
    d = tree({T + "/lift/gen/g.py": "import tests.x\n", T + "/lift/gen/tests/x.py": "z = 1\n", T + "/lift/gen/tests/y.py": "import time\nt = time.time()\n"},
             [root("lift/gen", entry=["lift/gen/g.py"])])
    rc, res, err = run(d)
    check("a clean tests/x.py imported by a stage is certified, a non-imported tests/y.py with a clock read is outside: green", rc == 0 and res["universe"] == 2, (res, err))
    # `entry` is required for a tool root
    adm = lambda rs, files=(): gate_admit.judge(list(files), {"format": "fnvnx-admit/1", "roots": rs}, lambda p: True)
    noent = {"path": T + "/x", "class": "tool"}
    check("entry is required for a tool root", any("entry" in x for x in adm([noent])[1]) and adm([dict(noent, entry=[])])[1] == [], adm([noent]))
    check("an entry outside the root's trees or not .py is an error", adm([dict(noent, entry=[T + "/y/a.py"])])[1] != [] and adm([dict(noent, entry=[T + "/x/a.sh"])])[1] != [])
    # one root of two trees
    two = {T + "/contract/a.py": "import b\n", T + "/link/b.py": "import a\n"}
    tr = {"path": T + "/contract", "trees": [T + "/contract", T + "/link"], "class": "tool", "entry": [T + "/contract/a.py"]}
    d = tree(two, [tr])
    rc, res, err = run(d)
    check("two trees of one root that import each other are green (one puregate run)", rc == 0 and res["universe"] == 2, (res, err))
    d = tree(two, [root("contract", entry=["contract/a.py"]), root("link")])
    rc, res, err = run(d)
    check("the same two trees as two roots without declared imports are red", rc == 2 and T + "/contract/a.py" in res["uncertified"], (res, err))
    adm2 = lambda rs: adm(rs, [T + "/contract/a.py", T + "/link/b.py"])
    check("a file of the second tree is certified by the root", adm2([tr]) == ({}, []), adm2([tr]))
    check("a root inside another root's trees is no error (the nearest root owns a file)", adm2([tr, root("link/sub", entry=["link/sub/s.py"])])[1] == [], adm2([tr, root("link/sub", entry=["link/sub/s.py"])]))
    check("trees whose first is not path, or a single tree, are errors", adm2([dict(tr, trees=[T + "/link", T + "/contract"])])[1] != [] and adm2([dict(tr, trees=[T + "/contract"])])[1] != [])
    return 1 if FAILED else 0


if __name__ == "__main__":
    rc = main()
    print("%d FAILED" % len(FAILED) if FAILED else "all passed")
    sys.exit(rc)
