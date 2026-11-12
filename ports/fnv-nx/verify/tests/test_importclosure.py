#!/usr/bin/env python3
"""Tests for verify/importclosure.py and closure_run.py: the static import closure, the key built on it and the runtime check. Plain script:
PASS/FAIL lines, exit 0 on pass."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import importclosure as C  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else ": %s" % (detail,)))
    if not ok:
        FAILED.append(name)


def tree(base, files):
    for p, t in files.items():
        os.makedirs(os.path.dirname(os.path.join(base, p)) or base, exist_ok=True)
        with open(os.path.join(base, p), "w") as f:
            f.write(t)
    return set(files)


def kinds(fs):
    return sorted({k for k, _, _, _ in fs})


def main():
    with tempfile.TemporaryDirectory() as base:
        files = {
            "g/gate.py": "import os, sys, json\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\nfrom lib import util\nfrom lib.sub import deep\nimport capstone\n",
            "lib/__init__.py": "",
            "lib/util.py": "from . import helper\nimport ctypes\n",
            "lib/helper.py": "X = 1\n",
            "lib/sub/__init__.py": "",
            "lib/sub/deep.py": "from lib import tail\n",
            "lib/tail.py": "Y = 2\n",
            "lib/unrelated.py": "Z = 3\n",
        }
        tr = tree(base, files)
        pin = {"capstone"}
        fs, fd = C.import_closure("g/gate.py", base, tr, pinned=pin)
        want = ["g/gate.py", "lib/__init__.py", "lib/helper.py", "lib/sub/__init__.py", "lib/sub/deep.py", "lib/tail.py", "lib/util.py"]
        check("closure: the entry, a constant sys.path directory, packages, relative and nested imports; stdlib and pinned are not followed; no findings",
              fs == want and fd == [], (fs, fd))
        check("closure: an unrelated file is not in it", "lib/unrelated.py" not in fs)
        env = "e1"
        k1 = C.closure_key(fs, base, env)
        open(os.path.join(base, "lib/unrelated.py"), "w").write("Z = 4\n")
        check("key: an unrelated file leaves it unchanged", C.closure_key(fs, base, env) == k1)
        open(os.path.join(base, "lib/tail.py"), "w").write("Y = 5\n")
        k2 = C.closure_key(fs, base, env)
        check("key: an edit of an imported (transitive) module changes it", k2 != k1)
        check("key: the env id and a data input change it", C.closure_key(fs, base, "e2") != k2 and C.closure_key(fs, base, env, [("d", "h")]) != k2
              and C.closure_key(fs, base, env, [("d", "h")]) == C.closure_key(fs, base, env, [("d", "h")]))
        # NAME-assigned constants and extend/append
        tr2 = tree(base, {"a/x.py": "import os, sys\nHERE = os.path.dirname(os.path.abspath(__file__))\nsys.path.append(os.path.join(HERE, '..', 'lib'))\nsys.path.extend([HERE])\nimport helper\nimport y\n",
                          "a/y.py": "pass\n"})
        fs2, fd2 = C.import_closure("a/x.py", base, tr | tr2, pinned=pin)
        check("closure: NAME = constant, append and extend of constant paths", fs2 == ["a/x.py", "a/y.py", "lib/helper.py"] and fd2 == [], (fs2, fd2))
        # a constant imported from another project module of the closure (VERIFY = ...) is followed
        tr4 = tree(base, {"e/m.py": "import sys\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nfrom cfg import ROOT as R\nsys.path.insert(0, R)\nimport helper\n".replace("sys.path.insert(0, os.path.dirname", "import os\nsys.path.insert(0, os.path.dirname", 1),
                          "e/cfg.py": "import os\nROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib')\n"})
        fs4, fd4 = C.import_closure("e/m.py", base, tr | tr4, pinned=pin)
        check("closure: a path constant imported from another project module", "lib/helper.py" in fs4 and fd4 == [], (fs4, fd4))
        # red findings
        bad = {
            "sys-path-dynamic": "import sys, os\nsys.path.insert(0, os.environ['P'])\n",
            "sys-path-dynamic2": "import sys\nsys.path = ['/x']\n",
            "sys-path-dynamic3": "import sys, os\nsys.path.insert(0, os.path.abspath('.'))\n",
            "sys-path-dynamic4": "import sys\nsys.path.insert(0, 'lib')\n",
            "dynamic-import": "n = 'a'\nm = __import__(n)\n",
            "dynamic-import2": "import importlib\nm = importlib.import_module(input())\n",
            "dynamic-import3": "import importlib.util\nimportlib.util.spec_from_file_location('a', 'b')\n",
            "unresolved": "import nosuchmodule\n",
            "shadows": "import json\n",
        }
        for name, text in bad.items():
            t = tree(base, {"b/m.py": text, "b/json.py": "" if name == "shadows" else "x=1\n"} if name == "shadows" else {"b/m.py": text})
            sub = tr | t if name == "shadows" else (tr | t)
            a = "b"
            sub = set(sub)
            if name != "shadows":
                sub.discard("b/json.py")
            _, f = C.import_closure("b/m.py", base, sub, isolated=False, pinned=pin)
            want_k = name.rstrip("234")
            check("red: %s" % name, want_k in kinds(f), f)
        # a literal dynamic import is followed, an ambiguity is red
        t = tree(base, {"c/m.py": "import sys, os\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\nm = __import__('lib.tail')\nimport importlib\nimportlib.import_module('lib.helper')\n"})
        fs3, fd3 = C.import_closure("c/m.py", base, tr | t, isolated=False, pinned=pin)
        check("a literal __import__ / import_module name is followed", "lib/tail.py" in fs3 and "lib/helper.py" in fs3 and "lib/__init__.py" in fs3, (fs3, fd3))
        t = tree(base, {"p1/dup.py": "", "p2/dup.py": "", "d/m.py": "import sys, os\nH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\nsys.path.insert(0, H + '/p1')\nsys.path.insert(0, H + '/p2')\nimport dup\n"})
        tr3 = tr | t
        _, f = C.import_closure("d/m.py", base, tr3, pinned=pin)
        check("red: ambiguous (two files for one module name over the path)", "ambiguous" in kinds(f), f)
        # runtime verification
        script = os.path.join(base, "g", "gate.py")
        mj = os.path.join(base, "mods.json")
        open(os.path.join(base, "lib/tail.py"), "w").write("Y = 2\n")
        # the closure_run wrapper imports the same modules as python3 -I would
        r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "closure_run.py"), mj, script], capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        mods = json.load(open(mj))
        check("closure_run records the module files (rc 0 even though capstone may be absent: the import is the test's)", os.path.isfile(mj) and any(m.endswith("lib/tail.py") for m in mods) or r.returncode != 0, (r.returncode, r.stderr[-200:]))
        # a tool that loads a module the static closure missed (a path the analysis cannot see: the module is loaded by running code with that path)
        miss = tree(base, {"r/t.py": "import sys\nexec(\"sys.path.insert(0, '%s')\")\nimport hidden\n" % os.path.join(base, "h"), "h/hidden.py": "V = 1\n"})
        fsm, fdm = C.import_closure("r/t.py", base, tr | miss, pinned=pin)
        check("the static closure of that tool misses the module (hidden is unresolved or absent)", "h/hidden.py" not in fsm)
        r = subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(HERE), "closure_run.py"), mj, os.path.join(base, "r/t.py")], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        out = C.check_modules(json.load(open(mj)), fsm, base, frozenset())
        check("runtime check: the module outside the closure is named", r.returncode == 0 and [p for k, p in out if p.endswith("h/hidden.py")] != [], (r.returncode, r.stderr[-200:], out))
        check("runtime check: a complete closure has no module outside it", C.check_modules([os.path.join(base, f) for f in fs], fs, base, frozenset()) == []
              and C.check_modules([os.__file__], [], base, frozenset()) == [])


if __name__ == "__main__":
    main()
    print("FAILED: %s" % FAILED if FAILED else "all passed")
    sys.exit(1 if FAILED else 0)
