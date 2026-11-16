#!/usr/bin/env python3
"""Rule of the stage scratch (the defects: link() made ws/syslib twice in one run, EEXIST; merge_inputs made ws/merge1 twice, "dest exists"): a path under a stage workspace `ws`
that is created by an operation that fails when the destination exists (os.mkdir, os.makedirs without exist_ok=True, shutil.copytree without dirs_exist_ok, os.symlink,
os.rename, shutil.move, cas.materialize) is a private tempfile.mkdtemp(dir=ws) / mkstemp, never a name built from ws and a constant (or a format of constants and a counter that restarts
per call). ast over ALL of recipe.py, every function, no list of methods: a name is tainted when it is assigned os.path.join(ws, X) with X not "root" (the one shared view) or joined
from a tainted name; a creating call whose destination is tainted is a finding. Plain script; exit 0 on pass."""
import ast
import os
import sys

VERIFY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = 0
VIEW = {"root"}   # ws/root: the stage's one shared view, written by the steps of one build on purpose
# creating calls: (dotted name, index of the destination argument, keyword that lifts the "must not exist" failure)
CREATE = {"os.mkdir": (0, None), "os.makedirs": (0, "exist_ok"), "shutil.copytree": (1, "dirs_exist_ok"), "os.symlink": (1, None), "os.rename": (1, None),
          "shutil.move": (1, None), "cas.materialize": (2, None)}


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    fails += 0 if ok else 1


def dotted(n):
    if isinstance(n, ast.Attribute):
        d = dotted(n.value)
        return d + "." + n.attr if d else None
    return n.id if isinstance(n, ast.Name) else None


def is_join(n):
    return isinstance(n, ast.Call) and dotted(n.func) in ("os.path.join", "posixpath.join") and len(n.args) >= 2


def judge(src):
    """[(function, line)] of the creating calls whose destination is a constant-named path under ws."""
    out = []
    for fn in [f for f in ast.walk(ast.parse(src)) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if "ws" not in {a.arg for a in fn.args.args}:
            continue
        taint = set()
        for _ in range(3):   # a name assigned from a tainted one
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    if tainted(n.value, taint):
                        taint.add(n.targets[0].id)
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and dotted(n.func) in CREATE:
                i, kw = CREATE[dotted(n.func)]
                if len(n.args) > i and tainted(n.args[i], taint) and not any(k.arg == kw and isinstance(k.value, ast.Constant) and k.value.value for k in n.keywords):
                    out.append((fn.name, n.lineno))
    return out


def tainted(v, taint):
    if isinstance(v, ast.Name):
        return v.id in taint
    if is_join(v):
        a0, a1 = v.args[0], v.args[1]
        if isinstance(a0, ast.Name) and a0.id == "ws":
            return not (isinstance(a1, ast.Constant) and a1.value in VIEW)
        return tainted(a0, taint)
    return False


r = judge(open(os.path.join(VERIFY, "recipe.py")).read())
check("recipe.py: no created path under ws is named by a constant (findings: %s)" % r, r == [])
check("selftest: ws/syslib made with makedirs is a finding", judge("def link(self, ws):\n    yd = os.path.join(ws, 'syslib')\n    os.makedirs(yd)\n") == [("link", 3)])
check("selftest: the merge1 shape (a counter format, materialize) is a finding", judge("def merge_inputs(base, extra, store, ws):\n    d = os.path.join(ws, 'merge%d' % 1)\n    cas.materialize(t, store, d)\n") == [("merge_inputs", 3)])
check("selftest: a subpath of a tainted name is a finding", judge("def f(ws):\n    d = os.path.join(ws, 'x')\n    s = os.path.join(d, 'y')\n    shutil.copytree(a, s)\n") == [("f", 4)])
check("selftest: exist_ok / dirs_exist_ok lift it", judge("def f(ws):\n    d = os.path.join(ws, 'x')\n    os.makedirs(d, exist_ok=True)\n    shutil.copytree(a, d, dirs_exist_ok=True)\n") == [])
check("selftest: a private mkdtemp and ws/root are no finding", judge("def f(ws):\n    pv = tempfile.mkdtemp(dir=ws)\n    os.makedirs(os.path.join(pv, 'syslib'))\n    os.makedirs(os.path.join(ws, 'root'))\n    cas.materialize(t, s, pv)\n") == [])
sys.exit(1 if fails else 0)
