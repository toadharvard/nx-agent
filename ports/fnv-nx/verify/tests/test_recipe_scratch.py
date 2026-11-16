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


def mentions_ws(v):
    return any(isinstance(x, ast.Name) and x.id == "ws" for x in ast.walk(v))


def tainted(v, taint):
    """V is a path under ws that is not the shared view or a private scratch: os.path.join(ws, X) (X not "root"), a join from a tainted name, a string built from ws
    (ws + "/x", "%s/x" % ws, f"{ws}/x", "...".format(ws)), or a name assigned one of those."""
    if isinstance(v, ast.Name):
        return v.id in taint
    if is_join(v):
        a0, a1 = v.args[0], v.args[1]
        if isinstance(a0, ast.Name) and a0.id == "ws":
            return not (isinstance(a1, ast.Constant) and a1.value in VIEW)
        return tainted(a0, taint)
    if isinstance(v, (ast.BinOp, ast.JoinedStr)) or (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "format"):
        return mentions_ws(v) and not isinstance(v, ast.Name)
    return False


def judge(src):
    """[(function, line)]: creating calls whose destination is a path under ws that is not private, directly or through a local helper that passes a parameter to a creating
    call (a creator function: its call sites are judged like the creating calls)."""
    fns = [f for f in ast.walk(ast.parse(src)) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
    creators = {}   # function name -> indexes of the parameters that reach a creating call's destination
    for _ in range(4):
        for fn in fns:
            params = [a.arg for a in fn.args.args]
            for n in ast.walk(fn):
                if not isinstance(n, ast.Call):
                    continue
                name = dotted(n.func)
                dest = None
                if name in CREATE and len(n.args) > CREATE[name][0]:
                    if not any(k.arg == CREATE[name][1] and isinstance(k.value, ast.Constant) and k.value.value for k in n.keywords):
                        dest = [n.args[CREATE[name][0]]]
                elif name and name.split(".")[-1] in creators:
                    dest = [n.args[i] for i in creators[name.split(".")[-1]] if i < len(n.args)]
                for d in dest or []:
                    for x in ast.walk(d):
                        if isinstance(x, ast.Name) and x.id in params and x.id != "ws":
                            creators.setdefault(fn.name, set()).add(params.index(x.id) - (1 if params and params[0] == "self" else 0))
    out = []
    for fn in fns:
        if "ws" not in {a.arg for a in fn.args.args}:
            continue
        taint = set()
        for _ in range(3):   # a name assigned from a tainted one
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and tainted(n.value, taint):
                    taint.add(n.targets[0].id)
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            name = dotted(n.func)
            if name in CREATE:
                i, kw = CREATE[name]
                if len(n.args) > i and tainted(n.args[i], taint) and not any(k.arg == kw and isinstance(k.value, ast.Constant) and k.value.value for k in n.keywords):
                    out.append((fn.name, n.lineno))
            elif name and name.split(".")[-1] in creators and name.split(".")[-1] != fn.name:
                if any(i < len(n.args) and tainted(n.args[i], taint) for i in creators[name.split(".")[-1]]):
                    out.append((fn.name, n.lineno))
    return out


r = judge(open(os.path.join(VERIFY, "recipe.py")).read())
check("recipe.py: no created path under ws is named by a constant (findings: %s)" % r, r == [])
check("selftest: ws/syslib made with makedirs is a finding", judge("def link(self, ws):\n    yd = os.path.join(ws, 'syslib')\n    os.makedirs(yd)\n") == [("link", 3)])
check("selftest: the merge1 shape (a counter format, materialize) is a finding", judge("def merge_inputs(base, extra, store, ws):\n    d = os.path.join(ws, 'merge%d' % 1)\n    cas.materialize(t, store, d)\n") == [("merge_inputs", 3)])
check("selftest: a subpath of a tainted name is a finding", judge("def f(ws):\n    d = os.path.join(ws, 'x')\n    s = os.path.join(d, 'y')\n    shutil.copytree(a, s)\n") == [("f", 4)])
check("selftest: exist_ok / dirs_exist_ok lift it", judge("def f(ws):\n    d = os.path.join(ws, 'x')\n    os.makedirs(d, exist_ok=True)\n    shutil.copytree(a, d, dirs_exist_ok=True)\n") == [])
check("selftest: a private mkdtemp and ws/root are no finding", judge("def f(ws):\n    pv = tempfile.mkdtemp(dir=ws)\n    os.makedirs(os.path.join(pv, 'syslib'))\n    os.makedirs(os.path.join(ws, 'root'))\n    cas.materialize(t, s, pv)\n") == [])
check("selftest: a string built from ws is a path under ws", judge("def f(ws):\n    d = ws + '/x'\n    os.mkdir(d)\n    e = '%s/y' % ws\n    os.mkdir(e)\n    g = f'{ws}/z'\n    os.mkdir(g)\n") == [("f", 3), ("f", 5), ("f", 7)])
check("selftest: a helper that creates its parameter makes its call sites findings", judge("def mk(p):\n    os.mkdir(p)\n\n\ndef f(ws):\n    mk(os.path.join(ws, 'x'))\n") == [("f", 6)])
check("selftest: a helper called with a private scratch is no finding", judge("def mk(p):\n    os.mkdir(p)\n\n\ndef f(ws):\n    pv = tempfile.mkdtemp(dir=ws)\n    mk(os.path.join(pv, 'x'))\n") == [])
check("selftest: a helper whose creation is exist_ok (build's out) is no creator", judge("def b(out):\n    os.makedirs(out, exist_ok=True)\n\n\ndef f(ws):\n    b(os.path.join(ws, 'x'))\n") == [])
sys.exit(1 if fails else 0)
