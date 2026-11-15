#!/usr/bin/env python3
"""Gate "abspath" (fnvnx-gate/1, owner order of 2026-11-15 21:00Z "every generator is a recipe stage; no path outside the key"): no absolute host
path and no environment default can reach an input of a product stage or a gate stage.
    python3 -I gate_abspath.py        cwd = checkout root, env GATE_OUT, optional GATE_COMMIT; writes GATE_OUT/abspath.json
Universe (from the stage graph, by rule, no list of files): the Python files of
  (a) the tools the recipe runs as stages: `generated[].tool` of every recipe of every admitted root (admit.json), and the stage launchers of
      recipe.py itself (GEN_LAUNCH, LD_TOOLS files, unitstage.py);
  (b) the gate scripts of gates.d (every `.py` of an argv);
  (c) the transitive closure of (a) and (b) inside the tracked tree: an `import` / `from .. import` statement (ast) resolves to the tracked
      file of that name (the importer's directory first; with none there, every tracked file of that module path: a superset), and a string constant
      naming a tracked `.py` file (relative to the file's directory or to the checkout root) is a launch edge (gate_contract.py runs bodysel.py).
      A dynamic import that cannot be resolved (importlib.import_module / __import__ / spec_from_file_location / runpy with a non-constant
      argument) is red `abspath:dynamic-import:FILE:LINE`, unless its target expression is the result of require_mounted(...) or require_under(...)
      of recipe.py (a call, or a name all of whose assignments are such calls): those guards raise outside the stage's mounts or the given tree.
A file outside the closure is not judged. Findings are read from the ast, never from the text:
  abspath:literal:FILE:LINE:VALUE   a str constant that is an absolute path ("/" then a name) and is used as a path: it is, or is inside an argument
                                    of, open, os.*, os.path.*, pathlib.Path, subprocess.*, shutil.*, glob.*, io/tarfile/zipfile open, sys.path.*, or
                                    is assigned (directly, through names, tuples or a for target) to a name that reaches one of those argument
                                    positions in its function or module. A path under a mount prefix of the sandbox (recipe.MOUNT_PREFIXES, the one
                                    constant, followed by "/" or the end) is a path inside the stage's own mount namespace and is green.
  abspath:envdefault:FILE:LINE      os.environ.get(k, default) / os.getenv(k, default) with a default other than None, os.environ.setdefault, or an
                                    os.environ[k] read inside a try block that has a handler (a fallback). A missing input must be an argument error.
  abspath:escape:FILE:LINE:PATH     a file read (open, Path.read_*, os.listdir, glob, np.load, shutil.copy*) in a stage tool whose path is built from
                                    __file__, __path__ or sys.argv[0] (through names, os.path.dirname/join/abspath, pathlib `/` and .parent) and
                                    resolves outside the tree the stage mounts for that tool (the owner tree of the file: recipe.tree_of, all of its
                                    files are mounted at /tool): any `..` that leaves it. Inside the tree it is a mounted file (green); through
                                    require_mounted/require_under it is guarded (green).
  abspath:home:FILE:LINE            `~` paths, os.path.expanduser, Path.home().
Report: the global count of findings per file with the commit; red iff there is any finding or any dynamic import.
Proof: a stage opens only what its key mounts; a literal absolute path outside the mount prefixes, an environment default, or a home path names
something outside every key, and the closure is every Python file a stage or a gate can execute that is in the repository, so a path outside the key
cannot enter through a tracked Python file. Assumptions: ASM-001 (the compiler), ASM-004 (the console), as for every gate. Limits, stated: a path built
at run time from non-constant parts (a join of arguments) is not a literal and is not seen (an argument is the rule: it is the caller's key); a script
launched by a name that is not a string constant is not in the closure."""
import ast
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import recipe  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
GATES_D = TREE + "/verify/gates.d"
MOUNTS = recipe.MOUNT_PREFIXES
ABS = re.compile(r"/[A-Za-z0-9_.][^\s]*\Z")
PYREF = re.compile(r"[\w./+-]+\.py\Z")
SINK_PREFIX = ("os.", "shutil.", "subprocess.", "glob.", "pathlib.", "sys.path.")
SINKS = {"open", "io.open", "tarfile.open", "zipfile.ZipFile", "pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "fileinput.input",
         "runpy.run_path", "importlib.util.spec_from_file_location"}
GUARDS = {"require_mounted", "require_under"}   # recipe.py: the guards a launcher loads a tool through (the target of a dynamic load must be their result)
DYN = {"importlib.import_module", "__import__", "importlib.util.spec_from_file_location", "runpy.run_path", "runpy.run_module",
       "importlib.__import__"}


def in_mount(v):
    return any(v == m or v.startswith(m + "/") for m in MOUNTS)


def dotted(n):
    parts = []
    while isinstance(n, ast.Attribute):
        parts.append(n.attr)
        n = n.value
    if isinstance(n, ast.Name):
        parts.append(n.id)
        return ".".join(reversed(parts))
    return None


class Module:
    """One parsed file: its import aliases, scopes (name -> constants and aliased names), sink calls."""

    def __init__(self, path, src):
        self.path, self.tree = path, ast.parse(src, path)
        self.alias = {}   # local name -> dotted origin
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    self.alias[(a.asname or a.name).split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                for a in n.names:
                    self.alias[a.asname or a.name] = n.module + "." + a.name
        self.frag = set()   # Constants that continue a string (right operand of +, later piece of an f-string): a suffix, not a path of its own
        for n in ast.walk(self.tree):
            if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add) and isinstance(n.right, ast.Constant):
                self.frag.add(id(n.right))
            elif isinstance(n, ast.JoinedStr):
                self.frag.update(id(v) for v in n.values[1:] if isinstance(v, ast.Constant))
        self.docs = set()   # id of the Constant nodes that are docstrings
        for n in ast.walk(self.tree):
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                    and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str):
                self.docs.add(id(n.body[0].value))

    def full(self, node):
        d = dotted(node)
        if d is None:
            return None
        h, _, t = d.partition(".")
        return (self.alias.get(h, h) + ("." + t if t else ""))

    def sink(self, call):
        f = self.full(call.func)
        return f is not None and (f in SINKS or f.startswith(SINK_PREFIX))


def consts(node, mod):
    """The str Constants of a subtree that are absolute-path shaped or home-shaped, as (node, value)."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in mod.docs and id(n) not in mod.frag and (ABS.match(n.value) or n.value == "~" or n.value.startswith("~/")):
            out.append((n, n.value))
    return out


def names_of(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def scopes(tree):
    """[(scope node, parent scope index or None)]; the module is index 0."""
    out = [(tree, None)]

    def go(node, idx):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                out.append((ch, idx))
                go(ch, len(out) - 1)
            else:
                go(ch, idx)
    go(tree, 0)
    return out


def own_nodes(scope):
    """The nodes of a scope without the bodies of nested functions."""
    todo = list(ast.iter_child_nodes(scope))
    while todo:
        n = todo.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            todo.extend(ast.iter_child_nodes(n))


def catches_keyerror(h):
    """True iff the handler H catches a missing environment key (a fallback): bare, KeyError, LookupError, Exception or BaseException."""
    t = h.type
    if t is None:
        return True
    ts = t.elts if isinstance(t, ast.Tuple) else [t]
    return any((dotted(x) or "").split(".")[-1] in ("KeyError", "LookupError", "Exception", "BaseException") for x in ts)


def is_guard(node):
    """A call of the one guard of a dynamic load: require_mounted(...) or require_under(...) (recipe.py), plain or as an attribute."""
    return isinstance(node, ast.Call) and (dotted(node.func) or "").split(".")[-1] in GUARDS


def guarded(a, idx, flow, sc, depth=0):
    """True iff the target expression A is the result of a guard call: the call itself, or a name every assignment of which (in its scope chain) is one."""
    if a is None:
        return False
    if is_guard(a):
        return True
    if isinstance(a, ast.Name) and depth < 4:
        while idx is not None:
            vals = flow[idx].get(a.id)
            if vals:
                return all(guarded(v, idx, flow, sc, depth + 1) for v in vals)
            idx = sc[idx][1]
    return False


READ_FUNCS = {"open", "io.open", "fileinput.input", "numpy.load", "numpy.loadtxt", "numpy.genfromtxt", "numpy.fromfile", "os.listdir", "os.scandir",
              "os.walk", "glob.glob", "glob.iglob", "shutil.copy", "shutil.copyfile", "shutil.copy2", "shutil.copytree"}
READ_METHODS = {"read_text", "read_bytes", "open", "glob", "rglob", "iterdir"}
NORM = {"os.path.abspath", "os.path.realpath", "os.path.normpath", "os.path.expanduser", "posixpath.normpath", "pathlib.Path", "pathlib.PurePath",
        "pathlib.PosixPath", "pathlib.PurePosixPath"}


def segs_join(a, b):
    """Join of two segment lists as os.path.join does: an absolute right part resets the base (and is no longer derived from __file__)."""
    return b if b and b[0] == "" else a + b


def pathval(node, idx, flow, sc, selfpath, mod, depth=0):
    """(segments, derived) of the path expression NODE, or None when it is not a statically known path expression. Segments are repo-relative
    names; "?" is a part that is not a constant. DERIVED: the value is built from __file__, __path__ or sys.argv[0] (the file's own location)."""
    if depth > 8:
        return None
    me = selfpath.split("/")
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return me, True
        if node.id == "__path__":
            return me[:-1], True
        i = idx
        while i is not None:
            vals = flow[i].get(node.id)
            if vals:
                alts = [v for w in vals for v in alternatives(w) if mentions_self(v, i, flow, sc)]   # the others are arguments: the caller's key
                got = [pathval(v, i, flow, sc, selfpath, mod, depth + 1) for v in alts]
                if not got or not all(g is not None for g in got):
                    return None
                return next((g for g in got if g[1] and "?" in g[0]), got[0])   # every assignment is an alternative: the undecidable one decides
            i = sc[i][1]
        return None
    if isinstance(node, ast.Subscript):
        if mod.full(node.value) == "sys.argv" and isinstance(node.slice, ast.Constant) and node.slice.value == 0:
            return me, True
        if isinstance(node.value, ast.Attribute) and node.value.attr == "parents" and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, int):
            b = pathval(node.value.value, idx, flow, sc, selfpath, mod, depth + 1)
            return (b[0][:-(node.slice.value + 1)] if b else None, b[1]) if b else None
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("/"), False
    if isinstance(node, ast.JoinedStr):
        parts = [pathval(v.value, idx, flow, sc, selfpath, mod, depth + 1) if isinstance(v, ast.FormattedValue) else (v.value.split("/"), False) for v in node.values]
        segs, der = [], False
        for p in parts:
            if p is None:
                segs += ["?"]
            else:
                segs = segs + p[0] if not segs else segs[:-1] + [segs[-1] + p[0][0]] + p[0][1:]
                der = der or p[1]
        return segs, der
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        a, b = (pathval(x, idx, flow, sc, selfpath, mod, depth + 1) for x in (node.left, node.right))
        if a is None and b is None:
            return None
        a, b = a or (["?"], False), b or (["?"], False)
        if isinstance(node.op, ast.Add):
            return a[0][:-1] + [a[0][-1] + b[0][0]] + b[0][1:], a[1] or b[1]
        return segs_join(a[0], b[0]), (a[1] or b[1]) and not (b[0] and b[0][0] == "")
    if isinstance(node, ast.Call):
        fn = mod.full(node.func) or ""
        if fn in ("os.path.dirname", "posixpath.dirname") and node.args:
            b = pathval(node.args[0], idx, flow, sc, selfpath, mod, depth + 1)
            return (b[0][:-1], b[1]) if b else None
        if fn in NORM and node.args:
            return pathval(node.args[0], idx, flow, sc, selfpath, mod, depth + 1)
        if fn in ("os.path.join", "posixpath.join"):
            vals = [pathval(a, idx, flow, sc, selfpath, mod, depth + 1) or (["?"], False) for a in node.args]
            if not vals:
                return None
            acc, der = vals[0]
            for v, _ in [(x[0], x[1]) for x in vals[1:]]:
                acc = segs_join(acc, v)
            return acc, any(x[1] for x in vals) and acc[:1] != [""]
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("resolve", "absolute", "expanduser"):
            return pathval(node.func.value, idx, flow, sc, selfpath, mod, depth + 1)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath":
            b = pathval(node.func.value, idx, flow, sc, selfpath, mod, depth + 1)
            if b is None:
                return None
            for a in node.args:
                v = pathval(a, idx, flow, sc, selfpath, mod, depth + 1) or (["?"], False)
                b = (segs_join(b[0], v[0]), b[1])
            return b
        return None
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        b = pathval(node.value, idx, flow, sc, selfpath, mod, depth + 1)
        return (b[0][:-1], b[1]) if b else None
    return None


def leaves(segs, owner):
    """True iff the segment list leaves the tree OWNER (a repo path): a `..` above the repo root, or a normalized path outside OWNER (a "?" part inside the
    owner prefix is unknown, so not equal)."""
    out = []
    for g in segs:
        if g in ("", "."):
            continue
        if g == "..":
            if out and out[-1] != "..":
                out.pop()
            else:
                out.append("..")
        else:
            out.append(g)
    o = owner.split("/")
    return out[:len(o)] != o, "/".join(out)


def alternatives(node):
    """The expressions a conditional or `or`/`and` chain can evaluate to."""
    if isinstance(node, ast.IfExp):
        return alternatives(node.body) + alternatives(node.orelse)
    if isinstance(node, ast.BoolOp):
        return [a for v in node.values for a in alternatives(v)]
    return [node]


def mentions_self(node, idx, flow, sc, depth=0):
    """True iff the expression NODE (or a name it is assigned from) mentions __file__, __path__ or sys.argv[0]: a path of the file's own location."""
    for m in ast.walk(node):
        if isinstance(m, ast.Name):
            if m.id in ("__file__", "__path__"):
                return True
            if depth < 6:
                i = idx
                while i is not None:
                    vals = flow[i].get(m.id)
                    if vals:
                        if any(mentions_self(v, i, flow, sc, depth + 1) for v in vals):
                            return True
                        break
                    i = sc[i][1]
        elif isinstance(m, ast.Subscript) and dotted(m.value) == "sys.argv" and isinstance(m.slice, ast.Constant) and m.slice.value == 0:
            return True
    return False


def read_target(n, mod):
    """The path expression a file-read call reads: the first argument of open-like functions, the object of a path method; else None."""
    fn = mod.full(n.func) or ""
    if fn in READ_FUNCS and n.args:
        return n.args[0]
    if isinstance(n.func, ast.Attribute) and n.func.attr in READ_METHODS and fn not in READ_FUNCS:
        return n.func.value
    return None


def judge_file(path, src, owner=None):
    """(findings [(code, line, detail)], dynamic [(line)]) of one file. OWNER: the admitted root tree the stage mounts this file's tool from (None: the
    file is not a stage tool, so the escape rule does not apply)."""
    mod = Module(path, src)
    sc = scopes(mod.tree)
    f, seen = [], set()

    def add(code, line, detail=""):
        k = (code, line, detail)
        if k not in seen:
            seen.add(k)
            f.append(k)
    # def-use: per scope, name -> (constants, names it is built from); fixpoint along the scope chain
    flow = []
    for scope, _ in sc:
        d = {}
        for n in own_nodes(scope):
            tg = None
            if isinstance(n, ast.Assign):
                tg, val = n.targets, n.value
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                tg, val = [n.target], n.value
            elif isinstance(n, (ast.For, ast.AsyncFor)):
                tg, val = [n.target], n.iter
            elif isinstance(n, ast.NamedExpr):
                tg, val = [n.target], n.value
            if tg:
                for t in tg:
                    for nm in ({x.id for x in ast.walk(t) if isinstance(x, ast.Name)}):
                        d.setdefault(nm, []).append(val)
        flow.append(d)

    def reach(idx, nm, mod, guard):
        """The path-shaped constants that can flow into name NM seen from scope IDX."""
        out = []
        while idx is not None:
            for val in flow[idx].get(nm, []):
                if id(val) in guard:
                    continue
                guard.add(id(val))
                out += consts(val, mod)
                for m2 in names_of(val):
                    out += reach(idx, m2, mod, guard)
            idx = sc[idx][1]
        return out
    for idx, (scope, _) in enumerate(sc):
        for n in own_nodes(scope):
            if isinstance(n, ast.Call) and mod.sink(n):
                args = list(n.args) + [k.value for k in n.keywords]
                hit = []
                for a in args:
                    hit += consts(a, mod)
                    for nm in names_of(a):
                        hit += reach(idx, nm, mod, set())
                for c, v in hit:
                    if v == "~" or v.startswith("~/"):
                        add("home", c.lineno, v)
                    elif not in_mount(v):
                        add("literal", c.lineno, v)
    for n in ast.walk(mod.tree):
        if isinstance(n, ast.Call):
            fn = mod.full(n.func)
            if fn in ("os.path.expanduser", "posixpath.expanduser") or (fn and fn.endswith("Path.home")):
                add("home", n.lineno)
            two = (fn in ("os.environ.get", "os.getenv") and (len(n.args) >= 2 or any(k.arg == "default" for k in n.keywords)))
            if two:
                d = n.args[1] if len(n.args) >= 2 else [k.value for k in n.keywords if k.arg == "default"][0]
                if not (isinstance(d, ast.Constant) and d.value is None):
                    add("envdefault", n.lineno)
            if fn == "os.environ.setdefault":
                add("envdefault", n.lineno)
        if isinstance(n, ast.Try) and any(catches_keyerror(h) for h in n.handlers):
            for b in n.body:
                for m in ast.walk(b):
                    if isinstance(m, ast.Subscript) and isinstance(m.ctx, ast.Load) and mod.full(m.value) == "os.environ":
                        add("envdefault", m.lineno)
    if owner:
        for idx, (scope, _) in enumerate(sc):
            for n in own_nodes(scope):
                if not isinstance(n, ast.Call):
                    continue
                t0 = read_target(n, mod)
                if t0 is None:
                    continue
                for t in alternatives(t0):   # `a if c else b`, `a or b`: every branch is a path the read can take
                    if guarded(t, idx, flow, sc):
                        continue
                    v = pathval(t, idx, flow, sc, path, mod)
                    if v is not None and v[1]:
                        out, res = leaves(v[0], owner)
                        if out or "?" in v[0]:   # a path the gate cannot decide (a non-constant part) is never green
                            add("escape", n.lineno, res)
                    elif v is None and mentions_self(t, idx, flow, sc):
                        add("escape", n.lineno, "undecided")
    dyn = []
    for idx, (scope, _) in enumerate(sc):
        for n in own_nodes(scope):
            if isinstance(n, ast.Call) and mod.full(n.func) in DYN:
                fn = mod.full(n.func)
                i = 1 if fn.endswith("spec_from_file_location") else 0
                a = n.args[i] if len(n.args) > i else None
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)) and not guarded(a, idx, flow, sc):
                    dyn.append(n.lineno)
    return f, dyn


def imports_of(path, src):
    """Module names imported by a file: [(dotted name, level)], plus the `.py` string constants."""
    tree = ast.parse(src, path)
    mods, pys = [], []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [(a.name, 0) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            mods.append((base, n.level))
            mods += [((base + "." if base else "") + a.name, n.level) for a in n.names]
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and PYREF.match(n.value):
            pys.append(n.value)
    return mods, pys


def resolve(path, files, mods, pys):
    """Tracked files that the imports and `.py` constants of PATH name."""
    here = os.path.dirname(path)
    out = set()
    for name, level in mods:
        if not name:
            continue
        rel = name.replace(".", "/")
        base = here
        for _ in range(max(level - 1, 0)):
            base = os.path.dirname(base)
        local = [c for c in (base + "/" + rel + ".py", base + "/" + rel + "/__init__.py") if c in files]
        if local:
            out.update(local)
            continue
        if level:
            continue
        for c in files:   # every tracked file of that module path (superset); a stdlib or third-party name matches nothing
            if c.endswith("/" + rel + ".py") or c.endswith("/" + rel + "/__init__.py"):
                out.add(c)
    for p in pys:
        for c in (here + "/" + p, p, TREE + "/" + p):
            c = os.path.normpath(c)
            if c in files:
                out.add(c)
    return out


def closure(files, seeds):
    """Python files reachable from SEEDS (tracked paths -> source in FILES)."""
    seen, todo = set(), [s for s in sorted(seeds) if s in files]
    while todo:
        p = todo.pop()
        if p in seen:
            continue
        seen.add(p)
        mods, pys = imports_of(p, files[p])
        todo += sorted(resolve(p, files, mods, pys) - seen)
    return seen


def judge(files, seeds, commit="", owner=None, tool_seeds=None):
    """(result dict, per-file count) over the closure of SEEDS in FILES {path: source}."""
    cl = closure(files, seeds)
    tools = closure(files, tool_seeds) if tool_seeds is not None else cl   # the escape rule judges stage tools only: a gate runs from the checkout root, not under /tool
    per, rows, bad = {}, [], []
    for p in sorted(cl):
        f, dyn = judge_file(p, files[p], owner(p) if owner and p in tools else None)
        n = len(f) + len(dyn)
        if n:
            per[p] = n
        for code, line, d in sorted(f):
            rows.append("abspath:%s:%s:%d%s" % (code, p, line, ":" + d if d else ""))
        for line in dyn:
            rows.append("abspath:dynamic-import:%s:%d" % (p, line))
    return {"closure": len(cl), "per_file": per, "findings": rows}


def seeds_of(files_all, git_show):
    """Seeds (a) and (b) from the tree: tools of the recipes of admitted roots, recipe's own stage launchers, gate scripts."""
    seeds = set()
    admit = json.loads(git_show(ADMIT))
    for r in admit.get("roots", []):
        rec = r.get("recipe")
        for rp in ([rec] if isinstance(rec, str) else list(rec.values()) if isinstance(rec, dict) else []):
            try:
                doc = json.loads(git_show(rp))
            except (ValueError, OSError):
                continue
            for k in ("generated",):
                for g in doc.get(k) or []:
                    if isinstance(g, dict) and isinstance(g.get("tool"), str):
                        seeds.add(g["tool"])
    rel = lambda p: os.path.relpath(p, os.getcwd()) if os.path.isabs(p) else p
    seeds.add(rel(recipe.GEN_LAUNCH))
    seeds.add(HERE_REL + "/unitstage.py")
    for p in (recipe.LD_TOOLS[0] + "/ldcheck.py", recipe.LD_TOOLS[0] + "/gateids.py", recipe.LD_TOOLS[1] + "/gatefile.py"):
        seeds.add(p)
    tools = set(seeds)
    for p in sorted(files_all):
        if p.startswith(GATES_D + "/") and p.endswith(".json"):
            for a in json.loads(git_show(p)).get("argv", []):
                if isinstance(a, str) and a.endswith(".py"):
                    seeds.add(a)
    return seeds, tools


HERE_REL = TREE + "/verify"


def read_tree(commit):
    """{path: source} of the tracked .py files below TREE, and a reader of any tracked file, at COMMIT (the working tree when COMMIT is empty)."""
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    names = sorted(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)

    def show(p):
        with open(p, "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    return names, show


def main():
    out = os.environ["GATE_OUT"]
    commit = os.environ.get("GATE_COMMIT")
    if commit is None:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    names, show = read_tree(commit)
    files = {p: show(p) for p in names if p.endswith(".py")}
    seeds, tool_seeds = seeds_of(set(names), show)
    missing = sorted(s for s in seeds if s not in files)
    roots = {r["path"]: r for r in json.loads(show(ADMIT)).get("roots", []) if isinstance(r, dict) and "path" in r}
    res = judge(files, seeds, commit, lambda p: recipe.tree_of(roots, p), tool_seeds)
    rows = res["findings"] + ["abspath:missing-tool:%s" % s for s in missing]
    status = "green" if not rows else "red"
    for p, n in sorted(res["per_file"].items()):
        print("  %s %d" % (p, n))
    for r in rows[:60]:
        print("  " + r[:200])
    detail = "%d findings in %d of %d closure files at %s" % (len(rows), len(res["per_file"]), res["closure"], commit[:12])
    doc = {"format": "fnvnx-gate/1", "name": "abspath", "status": status, "universe": res["closure"], "certified": res["closure"] - len(res["per_file"]),
           "uncertified": sorted(res["per_file"])[:50], "detail": detail, "reasons": {p: str(n) for p, n in sorted(res["per_file"].items())[:50]},
           "errors": rows[:50], "findings": len(rows), "per_file": res["per_file"], "commit": commit}
    with open(os.path.join(out, "abspath.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("abspath: %s %s" % (status, detail))
    return 0 if status == "green" else 1


if __name__ == "__main__":
    sys.exit(main())
