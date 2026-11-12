#!/usr/bin/env python3
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

HERE = os.path.dirname(os.path.abspath(__file__))
VERIFY = os.path.dirname(HERE)
sys.path.insert(0, VERIFY)
import gate_admit  # noqa: E402

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


def run(d, *dirs):
    out = os.path.join(d, "out")
    os.makedirs(out, exist_ok=True)
    p = subprocess.run([sys.executable, "-I", GATE] + list(dirs), cwd=d, env={"GATE_OUT": out, "PATH": os.environ["PATH"]},
                       capture_output=True, text=True)
    return p.returncode, json.load(open(os.path.join(out, "pure.json"))), p.stderr


def root(p, imports=None):
    r = {"path": T + "/" + p, "class": "tool", "gates": ["pure", "selftests"]}
    if imports is not None:
        r["imports"] = [T + "/" + i for i in imports]
    return r


def main():
    gen = {T + "/lift/gen/g.py": "import fnvlift\nfrom fnvlift import m\nx = m.f()\n"}
    lib = {T + "/lift/fnvlift/__init__.py": "", T + "/lift/fnvlift/m.py": "def f():\n    return 1\n"}
    other = {T + "/lift/other/o.py": "y = 2\n"}
    files = dict(gen, **lib)
    d = tree(files, [root("lift/gen"), root("lift/fnvlift")])
    rc, res, err = run(d, T + "/lift")
    check("an undeclared import of another root's module is red", rc == 2 and res["status"] == "red" and T + "/lift/gen/g.py" in res["uncertified"], (res, err))
    d = tree(files, [root("lift/gen", ["lift/fnvlift"]), root("lift/fnvlift")])
    rc, res, err = run(d, T + "/lift")
    check("the same import declared in admit.json imports is green; both roots are in the universe", rc == 0 and res["status"] == "green" and res["universe"] == 3, (res, err))
    f3 = dict(files, **other)
    f3[T + "/lift/gen/g.py"] = "import o\n"
    d = tree(f3, [root("lift/gen", ["lift/fnvlift"]), root("lift/fnvlift"), root("lift/other")])
    rc, res, err = run(d, T + "/lift")
    check("a declared import does not open the other roots: `import o` (root lift/other, not declared) is red", rc == 2 and T + "/lift/gen/g.py" in res["uncertified"], (res, err))
    # (4) table errors
    rs = [root("a", ["b"]), root("b", ["a"])]
    e = gate_admit.imports_errors(rs)
    check("a cycle of declared imports is an error", any("cycle" in x for x in e), e)
    check("a 3-cycle is an error and an acyclic chain is not", any("cycle" in x for x in gate_admit.imports_errors([root("a", ["b"]), root("b", ["c"]), root("c", ["a"])]))
          and gate_admit.imports_errors([root("a", ["b"]), root("b", ["c"]), root("c", [])]) == [])
    check("an unknown root, a self import and a repeat are errors", len(gate_admit.imports_errors([root("a", ["zz"]), root("b", ["b"]), root("c", ["a", "a"])])) == 3)
    check("imports that is not a list of strings is an error", len(gate_admit.imports_errors([{"path": "p", "imports": "q"}, {"path": "r", "imports": [1]}])) == 2)
    d = tree(files, [root("lift/gen", ["lift/fnvlift"]), root("lift/fnvlift", ["lift/gen"])])
    rc, res, err = run(d, T + "/lift")
    check("the gate is red on a cycle of declared imports", rc == 2 and res["status"] == "red" and "cycle" in res["detail"], (res, err))
    check("judge reports the cycle as a root error of the table", any("cycle" in x for x in gate_admit.judge([], {"format": "fnvnx-admit/1", "roots": rs}, ["pure"], lambda p: True)[1]))
    # (5) no admit root inside DIR: the DIR alone is the unit
    d = tree({T + "/rules/r.py": "import q\n", T + "/rules/q.py": "z = 1\n"}, [root("contract")])
    rc, res, err = run(d, T + "/rules")
    check("a DIR with no admitted root inside is one unit and resolves its own files", rc == 0 and res["universe"] == 2, (res, err))
    # (6) one root of two trees: mutual imports are green with no file moved; the table rules on `trees`
    two = {T + "/contract/a.py": "import b\n", T + "/link/b.py": "import a\n"}
    tr = {"path": T + "/contract", "trees": [T + "/contract", T + "/link"], "class": "tool", "gates": ["pure", "selftests"]}
    d = tree(two, [tr])
    rc, res, err = run(d, T + "/contract", T + "/link")
    check("two trees of one root that import each other are green (one puregate run)", rc == 0 and res["universe"] == 2, (res, err))
    d = tree(two, [root("contract"), root("link")])
    rc, res, err = run(d, T + "/contract", T + "/link")
    check("the same two trees as two roots without declared imports are red", rc == 2 and len(res["uncertified"]) == 2, (res, err))
    adm = lambda rs: gate_admit.judge([T + "/contract/a.py", T + "/link/b.py"], {"format": "fnvnx-admit/1", "roots": rs}, ["pure", "selftests"], lambda p: True)
    check("a file of the second tree is certified by the root", adm([tr]) == ({}, []), adm([tr]))
    check("trees overlapping another root are an error", any("overlap" in x for x in adm([tr, root("link/sub")])[1]))
    check("trees whose first is not path, or a single tree, are errors", adm([dict(tr, trees=[T + "/link", T + "/contract"])])[1] != [] and adm([dict(tr, trees=[T + "/contract"])])[1] != [])
    return 1 if FAILED else 0


if __name__ == "__main__":
    rc = main()
    print("%d FAILED" % len(FAILED) if FAILED else "all passed")
    sys.exit(rc)
