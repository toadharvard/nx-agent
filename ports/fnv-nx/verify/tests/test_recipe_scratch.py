#!/usr/bin/env python3
"""Rule of the stage scratch (the defect: link() made ws/syslib twice in one run, EEXIST): a method of Job that can run more than once in one
workspace never names a directory of it by a constant. Every `os.path.join(ws, <str constant>)` in those methods is a finding; their directories come
from tempfile.mkdtemp(dir=ws). ast over recipe.py. Plain script; exit 0 on pass."""
import ast
import os
import sys

VERIFY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPEATED = {"link", "host_startfiles", "ldscript_probe"}
fails = 0


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    fails += 0 if ok else 1


def fixed(fn):
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "join" and len(n.args) >= 2 \
                and isinstance(n.args[0], ast.Name) and n.args[0].id == "ws" and isinstance(n.args[1], ast.Constant) and isinstance(n.args[1].value, str) \
                and n.args[1].value != "root":
            out.append(n.lineno)
    return out


def judge(src):
    tree = ast.parse(src)
    return {f.name: fixed(f) for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name in REPEATED}


r = judge(open(os.path.join(VERIFY, "recipe.py")).read())
check("the repeated methods of recipe.py are found", {"link", "host_startfiles"} <= set(r))
check("recipe.py: no constant-named directory of ws in a repeated method", all(not v for v in r.values()))
bad = judge("def link(self, ws):\n    yd = os.path.join(ws, 'syslib')\n")
check("selftest: ws/syslib in link is a finding", bad["link"] == [2])
ok = judge("def link(self, ws):\n    pv = tempfile.mkdtemp(dir=ws)\n    yd = os.path.join(pv, 'syslib')\n    r = os.path.join(ws, 'root')\n")
check("selftest: a private scratch and ws/root are not findings", ok["link"] == [])
sys.exit(1 if fails else 0)
