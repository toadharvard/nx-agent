"""Path-flow analysis of Python tool files (ast, no run): the one implementation of the rules about which paths a stage tool reads. Used by gate_abspath
(abspath:literal / envdefault / escape / home) and by recipe.check (gen:file-escape, the same escape rule at `recipe check` time), so the rule exists once.
Imports nothing of the recipe: recipe.py imports this file."""
import ast
import re

ABS = re.compile(r"/[A-Za-z0-9_.][^\s]*\Z")
SINK_PREFIX = ("os.", "shutil.", "subprocess.", "glob.", "pathlib.", "sys.path.")
SINKS = {"open", "io.open", "tarfile.open", "zipfile.ZipFile", "pathlib.Path", "pathlib.PurePath", "pathlib.PosixPath", "fileinput.input",
         "runpy.run_path", "importlib.util.spec_from_file_location"}
GUARDS = {"require_mounted", "require_mounted_module"}   # recipe.py: the guards a launcher loads a tool through (the target of a dynamic load must be their result)


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
    """A call of the one guard of a dynamic load: require_mounted(...) (recipe.py), plain or as an attribute."""
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


def flow_of(sc):
    """Per scope, name -> the expressions assigned to it (the def-use map the path rules read)."""
    # def-use: per scope, name -> (constants, names it is built from); fixpoint along the scope chain
    flow = []
    for scope, _ in sc:
        d = {}
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):   # a parameter default is an assignment of the parameter: every default form (positional, keyword-only, lambda)
            a = scope.args
            pos = a.posonlyargs + a.args
            for prm, dv in list(zip(pos[len(pos) - len(a.defaults):], a.defaults)) + [(k, v) for k, v in zip(a.kwonlyargs, a.kw_defaults) if v is not None]:
                if not (isinstance(dv, ast.Name) and dv.id == prm.arg):   # `x=x` reads the enclosing scope's x, which the chain walk below cannot tell from the parameter
                    d.setdefault(prm.arg, []).append(dv)
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
    return flow


def escapes(path, mod, sc, flow, owner, inside=None):
    """[(line, detail)] of the file reads in the stage tool PATH (module MOD, scopes SC, def-use FLOW) whose path is built from __file__, __path__ or sys.argv[0] and
    leaves OWNER, the repo tree the stage mounts the tool from, or cannot be decided ("undecided" / a "?" part). Inside OWNER it is a mounted file (green);
    through require_mounted it is guarded (green). INSIDE: a list that receives the resolved repo path of every read that stays inside OWNER (what a stage must mount for
    the module to run). The one implementation: gate_abspath (abspath:escape) and recipe.check (gen:file-escape)."""
    out_f = []
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
                        out_f.append((n.lineno, res))
                    elif inside is not None:
                        inside.append(res)
                elif v is None and mentions_self(t, idx, flow, sc):
                    out_f.append((n.lineno, "undecided"))
    return out_f


def escapes_of(path, src, owner, inside=None):
    """The escapes of the tool file PATH with source SRC (see escapes); INSIDE receives the in-tree reads."""
    mod = Module(path, src)
    sc = scopes(mod.tree)
    return escapes(path, mod, sc, flow_of(sc), owner, inside)
