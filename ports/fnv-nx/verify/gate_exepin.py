#!/usr/bin/env python3
"""Gate "exepin" (fnvnx-gate/1): the player's exe is read in exactly one place, and only after its fingerprint matched.
    python3 -I gate_exepin.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/exepin.json

Rule (contract/exepin.py is the one place; its function `load_exe` checks size, SHA-256 and the PE header fields of the one
supported build before it returns a byte). Universe: every tracked .py file below ports/fnv-nx outside a `tests` directory; an item is
its repo-relative path. An item is certified iff no exe path flows to a file-reading call except through `load_exe`.
Flow, computed from the syntax trees of all items together (call structure, not names of variables):
  sources  an attribute `.exe` read in a file that declares the option `--exe` (add_argument('--exe', ...) is how a tool receives the
           exe path), and a string constant that is `FalloutNV.exe` or ends in `/FalloutNV.exe` used as an expression (not a docstring);
  spread   an expression is tainted if a sub-expression is; assignment taints the target name of the function; a tainted argument
           taints the parameter of every def of the callee's name (class call -> `__init__`; methods without self); a tainted
           `return` taints the call; fixpoint over all items;
  sinks    open, io.open, os.open, Path / pathlib.Path (any construction), mmap.mmap, shutil.*, numpy fromfile/memmap, os.system /
           os.popen / os.exec* / os.spawn*, subprocess.* - a call of these with a tainted argument is a violation, except that
           a tainted element that directly follows the literal '--exe' in the call (the exe forwarded to another entry point,
           which is itself an item of this gate) is allowed;
  cut      a call of `load_exe` (exepin.load_exe): its result is bytes, the taint stops, and its body is not an item of the flow
           (exepin.py is the one item allowed to open its own parameter: `load_exe` is the function named in the gate).
A tool that gets the exe path any other way (not `--exe`, not the file name) is outside the rule: that is the stated assumption, and the
sources are the only two ways the tools of this tree name the exe.
Result: universe = items, certified = items without a violation, uncertified = the first 50 `path:line what`. Green iff all certified.
"""
import ast
import collections
import json
import os
import subprocess
import sys

TREE = "ports/fnv-nx"
SINK_NAMES = {"open", "Path", "PurePath", "system", "popen", "fromfile", "memmap", "mmap"}
SINK_PREFIX = ("exec", "spawn")
SINK_MODULES = {"subprocess", "shutil"}
CUT = "load_exe"
EXE_NAME = "FalloutNV.exe"


def callee(node):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id, None
    if isinstance(f, ast.Attribute):
        base = f.value.id if isinstance(f.value, ast.Name) else None
        return f.attr, base
    return None, None


def is_sink(node):
    name, base = callee(node)
    if name is None:
        return False
    return name in SINK_NAMES or name.startswith(SINK_PREFIX) or base in SINK_MODULES


def declares_exe_option(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and callee(n)[0] == "add_argument":
            for a in n.args:
                if isinstance(a, ast.Constant) and a.value == "--exe":
                    return True
    return False


def docstring_nodes(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
            out.add(id(n.value))
    return out


class Flow:
    def __init__(self, trees):
        self.trees = trees                      # path -> ast
        self.opt = {p: declares_exe_option(t) for p, t in trees.items()}
        self.doc = {p: docstring_nodes(t) for p, t in trees.items()}
        self.defs = collections.defaultdict(list)       # callee name -> [(path, FunctionDef, offset)]
        for p, t in trees.items():
            for n in ast.walk(t):
                if isinstance(n, ast.ClassDef):
                    for m in n.body:
                        if isinstance(m, ast.FunctionDef) and m.name == "__init__":
                            self.defs[n.name].append((p, m, 1))
                        elif isinstance(m, ast.FunctionDef):
                            self.defs[m.name].append((p, m, 1))
            for n in ast.walk(t):
                if isinstance(n, ast.FunctionDef) and not any(n is d for l in self.defs.values() for _, d, _ in l):
                    self.defs[n.name].append((p, n, 0))
        self.tp = collections.defaultdict(set)          # id(FunctionDef) -> tainted parameter names
        self.tret = set()                               # callee names whose return is tainted

    def resolve(self, p, node):
        """the tracked defs a call can reach: `mod.f` -> f of the file mod.py; `f` -> f of this file, else of any file;
        `obj.f` -> every method f (a superset)"""
        name, base = callee(node)
        ds = self.defs.get(name, [])
        stem = {os.path.splitext(os.path.basename(q))[0]: q for q in self.trees}
        if base in stem:
            return [d for d in ds if d[0] == stem[base]]
        if isinstance(node.func, ast.Name):
            same = [d for d in ds if d[0] == p]
            return same or ds
        return [d for d in ds if d[2]]

    def _t(self, p, e, tl):
        if isinstance(e, ast.Call):
            name, _ = callee(e)
            if name == CUT:
                return False
            if self.resolve(p, e):          # a tracked function: its result is tainted only if its `return` is
                return any(d.name in self.tret for _, d, _ in self.resolve(p, e)) or (
                    callee(e)[0] in self.tret)
            return any(self._t(p, c, tl) for c in ast.iter_child_nodes(e))
        if isinstance(e, ast.Attribute) and e.attr == "exe" and self.opt[p] and isinstance(e.ctx, ast.Load):
            return True
        if isinstance(e, ast.Constant):
            return (isinstance(e.value, str) and (e.value == EXE_NAME or e.value.endswith('/' + EXE_NAME)) and id(e) not in self.doc[p])
        if isinstance(e, ast.Name):
            return e.id in tl
        return any(self._t(p, c, tl) for c in ast.iter_child_nodes(e))

    def local(self, scope):
        """the nodes of one scope: its statements, class bodies and lambdas included, nested defs excluded"""
        out, st = [], list(scope.body)
        while st:
            n = st.pop()
            out.append(n)
            for c in ast.iter_child_nodes(n):
                if not isinstance(c, ast.FunctionDef):
                    st.append(c)
        return out

    def scopes(self):
        """(path, FunctionDef or None, scope node, parent's tainted names): outer scopes first"""
        res = []

        def walk(p, fn, scope, parent):
            tl = set(parent) | (set(self.tp[id(fn)]) if fn else set())
            nodes = self.local(scope)
            for _ in range(3):
                for n in nodes:
                    if isinstance(n, ast.Assign) and self._t(p, n.value, tl):
                        for tg in n.targets:
                            for x in ast.walk(tg):
                                if isinstance(x, ast.Name):
                                    tl.add(x.id)
            res.append((p, fn, nodes, tl))
            for d in [c for c in self.local_defs(scope)]:
                walk(p, d, d, tl)
        for p, t in self.trees.items():
            walk(p, None, t, set())
        return res

    def local_defs(self, scope):
        out, st = [], list(scope.body)
        while st:
            n = st.pop()
            if isinstance(n, ast.FunctionDef):
                out.append(n)
                continue
            st.extend(ast.iter_child_nodes(n))
        return out

    def run(self):
        """-> violations [(path, line, what)]"""
        for _ in range(50):
            changed = False
            for p, fn, nodes, tl in self.scopes():
                for n in nodes:
                    if isinstance(n, ast.Return) and n.value is not None and fn and self._t(p, n.value, tl):
                        if fn.name not in self.tret:
                            self.tret.add(fn.name)
                            changed = True
                    if isinstance(n, ast.Call):
                        name, _ = callee(n)
                        if name is None or name == CUT:
                            continue
                        for dp, d, off in self.resolve(p, n):
                            params = [a.arg for a in d.args.args][off:]
                            for i, a in enumerate(n.args):
                                if i < len(params) and self._t(p, a, tl) and params[i] not in self.tp[id(d)]:
                                    self.tp[id(d)].add(params[i])
                                    changed = True
                            for k in n.keywords:
                                if k.arg in params and self._t(p, k.value, tl) and k.arg not in self.tp[id(d)]:
                                    self.tp[id(d)].add(k.arg)
                                    changed = True
            if not changed:
                break
        out = []
        for p, fn, nodes, tl in self.scopes():
            if fn is not None and fn.name == CUT and p.endswith("contract/exepin.py"):
                continue
            for n in nodes:
                if isinstance(n, ast.Call) and is_sink(n) and callee(n)[0] != CUT:
                    flat = []
                    for a in list(n.args) + [k.value for k in n.keywords]:
                        flat += list(a.elts) if isinstance(a, (ast.List, ast.Tuple)) else [a]
                    fwd = {id(flat[i + 1]) for i, a in enumerate(flat[:-1]) if isinstance(a, ast.Constant) and a.value == "--exe"}
                    for a in flat:
                        if id(a) not in fwd and self._t(p, a, tl):
                            out.append((p, n.lineno, "exe path reaches %s() without load_exe" % callee(n)[0]))
                            break
        return sorted(set(out))


def judge(sources):
    """sources {path: text} -> (bad {path: 'line what'}, errors [str]); pure function of its argument"""
    trees, errors = {}, []
    for p, s in sources.items():
        try:
            trees[p] = ast.parse(s, filename=p)
        except SyntaxError as e:
            errors.append("%s: does not parse: %s" % (p, e))
    bad = {}
    for p, line, what in Flow(trees).run():
        bad.setdefault(p, "%d %s" % (line, what))
    for p in sources:
        if p not in trees:
            bad[p] = "does not parse"
    return bad, errors


def universe(files):
    return sorted(f for f in files if f.endswith(".py") and f.startswith(TREE + "/") and "/tests/" not in f)


def main():
    out = os.environ["GATE_OUT"]
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    files = universe(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    sources = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            sources[f] = fh.read()
    bad, errors = judge(sources)
    red = bool(bad or errors)
    uncert = ["%s:%s" % (p, bad[p]) for p in sorted(bad)][:50]
    detail = "; ".join(errors)[:400] if errors else (
        "exe path read only through exepin.load_exe" if not bad else "%d items read the exe without load_exe" % len(bad))
    res = {"format": "fnvnx-gate/1", "name": "exepin", "status": "red" if red else "green", "universe": len(files),
           "certified": len(files) - len(bad), "uncertified": uncert, "detail": detail}
    with open(os.path.join(out, "exepin.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    print("exepin: %s %d/%d %s" % (res["status"], res["certified"], len(files), detail))
    return 2 if red else 0


if __name__ == "__main__":
    sys.exit(main())
