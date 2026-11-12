#!/usr/bin/env python3
"""importclosure.py - the transitive import closure of a python tool, computed statically, and the cache key built on it (rule: a python tool stage's
key covers every file the tool can load, never only its entry file). stdlib only; python3 -I.

  import_closure(entry, repo, tracked, isolated=True, pinned=None) -> (files, findings)
    ENTRY: repo-relative .py file. TRACKED: set of repo-relative files of the admitted roots (the only project files an import may resolve to).
    FILES: sorted repo-relative .py files of the closure (ENTRY included). FINDINGS: sorted [(kind, file, line, detail)], each a red:
      sys-path-dynamic  a change of sys.path that is not insert/append/extend of a CONSTANT path: constant = str literals, __file__, os.sep/pardir,
                        os.path.join/dirname/abspath/realpath/normpath/basename of constants, `+` of constants, and a module-level NAME assigned exactly
                        once to a constant (abspath/realpath need an absolute argument: relative ones depend on the working directory); site.addsitedir.
      dynamic-import    __import__ / importlib.import_module with a non-literal name; any other use of importlib, runpy, imp; `import *` from a project module.
      unresolved        an import that is not stdlib, not an env-pinned distribution and resolves to no TRACKED file.
      ambiguous         one module name resolving to two different tracked files over the import path (no order is assumed).
      shadows           a tracked file that has the name of a stdlib or env-pinned top-level module.
      parse             a closure file that does not parse.
    The import path: the directories of the constant sys.path additions found in ANY closure file (a union, to a fixpoint) and, when ISOLATED is False,
    the script's directory (`python3 -I` puts no script directory on sys.path, so the gate runner passes True). Every Import/ImportFrom anywhere in a
    file (also inside a function or a try) is followed; a relative import resolves from the importing file's directory.
    STDLIB = sys.stdlib_module_names; PINNED = the top-level import names of the distributions pinned in env.json python.modules (pinned_imports).
  closure_key(files, repo, env_id, data=()) -> sha256 hex: the content of every file of the closure, the stage's declared data inputs DATA
    [(name, content hash)], the interpreter (sys.version) and the env id.
  check_modules(modules, files, repo, pinned_files) -> [(kind, path)]: runtime verification. MODULES = the file of every module in sys.modules at exit
    (closure_run.py records it). A module file outside closure ∪ stdlib ∪ env-pinned site-packages is `outside-closure`: the static closure was
    incomplete in that run. It checks; it never selects.
Not covered (stated): programs the tool starts as subprocesses (their files are declared inputs of the stage), files read as data, C extension modules of
pinned distributions (the env id covers them), modules loaded in a forked worker only."""
import ast
import hashlib
import json
import os
import posixpath
import sys
import sysconfig

STDLIB = frozenset(sys.stdlib_module_names)
PATH_CALLS = {"insert", "append", "extend"}
OS_FUNCS = {"join", "dirname", "abspath", "realpath", "normpath", "basename"}
DYN_MODS = ("importlib", "runpy", "imp")
IMPORTLIB_DYNAMIC = {"util", "machinery", "reload", "invalidate_caches", "__import__"}   # importlib members that load code from a path or a loader; metadata, resources, abc read data


class NotConst(Exception):
    pass


def pinned_imports(env_json):
    """{top-level import name} of the distributions pinned in ENV_JSON (path to env.json) python.modules, read from each INSTALLED pinned dist's
    top_level.txt (else the first path components of its RECORD): the installed set is what envcheck.py proves equal to env.json."""
    import importlib.metadata as md
    with open(env_json) as f:
        mods = json.load(f)["python"]["modules"]
    out = set()
    for name in sorted(mods):
        d = md.distribution(name)
        top = d.read_text("top_level.txt")
        names = top.split() if top else {p.parts[0] for p in (d.files or []) if p.suffix == ".py" or len(p.parts) > 1}
        out |= {n for n in names if n.isidentifier() and not n.endswith("dist-info")}
    return frozenset(out)


def pinned_files(env_json):
    """Absolute paths of every installed file of the pinned distributions (for the runtime check)."""
    import importlib.metadata as md
    with open(env_json) as f:
        mods = json.load(f)["python"]["modules"]
    return frozenset(os.path.realpath(str(d.locate_file(p))) for n in sorted(mods) for d in [md.distribution(n)] for p in (d.files or []))


def _assigned_once(tree):
    """{NAME: value node} of the module-level simple assignments of NAME that occur exactly once in the whole module (any scope)."""
    count, val = {}, {}
    for n in ast.walk(tree):
        tg = []
        if isinstance(n, ast.Assign):
            tg = n.targets
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            tg = [n.target]
        elif isinstance(n, (ast.For, ast.comprehension)):
            tg = [n.target]
        elif isinstance(n, ast.NamedExpr):
            tg = [n.target]
        for t in tg:
            for x in ast.walk(t):
                if isinstance(x, ast.Name):
                    count[x.id] = count.get(x.id, 0) + 1
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and count.get(n.targets[0].id) == 1:
            val[n.targets[0].id] = n.value
    return val


def _const(node, ctx, depth=0):
    """Value (str) of the constant expression NODE; ctx = (absolute file, {name: node}). Raises NotConst."""
    fpath, names, lookup = ctx
    if depth > 20:
        raise NotConst
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        if node.id == "__file__":
            return fpath
        if node.id in names:
            return _const(names[node.id], ctx, depth + 1)
        if lookup:
            return lookup(node.id, depth + 1)
        raise NotConst
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
        if node.attr in ("sep", "pardir", "curdir"):
            return {"sep": "/", "pardir": "..", "curdir": "."}[node.attr]
        raise NotConst
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _const(node.left, ctx, depth + 1) + _const(node.right, ctx, depth + 1)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in OS_FUNCS and not node.keywords
            and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "path"
            and isinstance(node.func.value.value, ast.Name) and node.func.value.value.id == "os"):
        args = [_const(a, ctx, depth + 1) for a in node.args]
        f = node.func.attr
        if f == "join" and args:
            return posixpath.join(*args)
        if len(args) != 1:
            raise NotConst
        a = args[0]
        if f == "dirname":
            return posixpath.dirname(a)
        if f == "basename":
            return posixpath.basename(a)
        if f == "normpath":
            return posixpath.normpath(a)
        if not a.startswith("/"):
            raise NotConst   # abspath/realpath of a relative path depends on the working directory
        return posixpath.normpath(a)
    raise NotConst


def _is_sys_path(n):
    return isinstance(n, ast.Attribute) and n.attr == "path" and isinstance(n.value, ast.Name) and n.value.id == "sys"


class _Scan(ast.NodeVisitor):
    """The imports, path additions and findings of one file."""

    def __init__(self, rel, abspath, tree):
        self.rel, self.abs, self.names = rel, abspath, _assigned_once(tree)
        self.imports, self.paths, self.finds = [], [], []   # imports: (level, module parts, [names], line); paths: (expr node, line, what)
        self.bound = {}   # local name -> (level, module parts, original name) of `from M import name [as local]`
        self.alias = {}   # local name -> module of DYN_MODS it is bound to
        self.visit(tree)

    def bad(self, kind, node, detail):
        self.finds.append((kind, self.rel, getattr(node, "lineno", 0), detail))

    def visit_Import(self, n):
        for a in n.names:
            self.imports.append((0, a.name.split("."), [], n.lineno))
            if a.name.split(".")[0] in DYN_MODS:
                if a.name.split(".")[0] == "importlib" and len(a.name.split(".")) > 1 and a.name.split(".")[1] in IMPORTLIB_DYNAMIC:
                    self.bad("dynamic-import", n, "import %s" % a.name)
                self.alias[a.asname or a.name.split(".")[0]] = a.name.split(".")[0]

    def visit_ImportFrom(self, n):
        mod = n.module.split(".") if n.module else []
        if any(a.name == "*" for a in n.names):
            self.imports.append((n.level, mod, ["*"], n.lineno))
        else:
            self.imports.append((n.level, mod, [a.name for a in n.names], n.lineno))
        for a in n.names:
            if a.name != "*":
                self.bound[a.asname or a.name] = (n.level, mod, a.name)
        if n.level == 0 and mod and mod[0] in DYN_MODS and (mod[0] != "importlib" or (len(mod) > 1 and mod[1] in IMPORTLIB_DYNAMIC)
                                                            or (len(mod) == 1 and any(a.name in IMPORTLIB_DYNAMIC for a in n.names))):
            self.bad("dynamic-import", n, "from %s import ...: use of %s" % (n.module, mod[0]))

    def visit_Attribute(self, n):
        if isinstance(n.value, ast.Name) and self.alias.get(n.value.id) in DYN_MODS:
            m = self.alias[n.value.id]
            if m != "importlib" or n.attr in IMPORTLIB_DYNAMIC:
                self.bad("dynamic-import", n, "%s.%s" % (m, n.attr))
        self.generic_visit(n)

    def visit_Call(self, n):
        f = n.func
        lit = lambda a: isinstance(a, ast.Constant) and isinstance(a.value, str)  # noqa: E731
        if isinstance(f, ast.Name) and f.id == "__import__":
            if n.args and lit(n.args[0]) and len(n.args) == 1 and not n.keywords:
                self.imports.append((0, n.args[0].value.split("."), [], n.lineno))
            else:
                self.bad("dynamic-import", n, "__import__ with a non-literal name")
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and self.alias.get(f.value.id) == "importlib" and f.attr == "import_module":
            if n.args and lit(n.args[0]) and len(n.args) == 1 and not n.keywords and not n.args[0].value.startswith("."):
                self.imports.append((0, n.args[0].value.split("."), [], n.lineno))
            else:
                self.bad("dynamic-import", n, "importlib.import_module with a non-literal or relative name")
        elif isinstance(f, ast.Attribute) and _is_sys_path(f.value):
            if f.attr in PATH_CALLS:
                args = n.args[-1:] if f.attr in ("insert", "append") else n.args[:1]
                if len(args) != 1 or n.keywords or (f.attr == "extend" and not isinstance(args[0], (ast.List, ast.Tuple))):
                    self.bad("sys-path-dynamic", n, "sys.path.%s with a non-constant path" % f.attr)
                else:
                    for e in (args[0].elts if f.attr == "extend" else [args[0]]):
                        self.paths.append((e, n.lineno, f.attr))
            else:
                self.bad("sys-path-dynamic", n, "sys.path.%s" % f.attr)
        elif isinstance(f, ast.Attribute) and f.attr == "addsitedir":
            self.bad("sys-path-dynamic", n, "addsitedir")
        self.generic_visit(n)

    def _store(self, targets, node):
        for t in targets:
            for x in ast.walk(t):
                if _is_sys_path(x) or (isinstance(x, ast.Subscript) and _is_sys_path(x.value)):
                    self.bad("sys-path-dynamic", node, "assignment to sys.path")

    def visit_Assign(self, n):
        self._store(n.targets, n)
        self.generic_visit(n)

    def visit_AugAssign(self, n):
        self._store([n.target], n)
        self.generic_visit(n)

    def visit_Delete(self, n):
        self._store(n.targets, n)
        self.generic_visit(n)


def _resolve(parts, dirs, tracked):
    """Tracked files realising module PARTS below the directories DIRS (repo-relative): ([files of the module itself], [package __init__ files],
    namespace?) over every directory; the list of module files has one entry per distinct file."""
    mods, inits, ns = [], [], False
    for d in dirs:
        base = posixpath.normpath(posixpath.join(d, *parts)) if parts else posixpath.normpath(d)
        for c in ((base + ".py",) if parts else ()) + (posixpath.join(base, "__init__.py"),):
            if c in tracked and c not in mods:
                mods.append(c)
        if any(t.startswith(base + "/") for t in tracked):
            ns = True
        for i in range(1, len(parts) + 1):
            p = posixpath.normpath(posixpath.join(d, *parts[:i], "__init__.py"))
            if p in tracked and p not in inits:
                inits.append(p)
    return mods, inits, ns


def import_closure(entry, repo, tracked, isolated=True, pinned=None):
    pinned = frozenset(pinned or ())
    tracked = set(tracked)
    files, finds, scans = {}, [], {}
    todo = [entry]
    dirs = [] if isolated else [posixpath.dirname(entry)]
    seen_dirs = set(dirs)
    add_dirs = lambda ps: [d for d in ps]  # noqa: E731

    def scan(rel):
        if rel in scans:
            return
        try:
            with open(os.path.join(repo, rel), "rb") as f:
                tree = ast.parse(f.read(), rel)
        except (SyntaxError, ValueError, OSError) as e:
            finds.append(("parse", rel, getattr(e, "lineno", 0) or 0, str(e)[:100]))
            scans[rel] = None
            return
        scans[rel] = _Scan(rel, os.path.join(os.path.abspath(repo), rel), tree)

    def lookup_in(rel, name, depth):
        """Value of module-level NAME of the file REL (assigned once there to a constant, or itself imported from a project module)."""
        s = scans.get(rel)
        if s is None or depth > 20:
            raise NotConst
        if name in s.names:
            return _const(s.names[name], (s.abs, s.names, lambda n, d, rel=rel: lookup_in(rel, n, d)), depth)
        if name in s.bound:
            level, parts, orig = s.bound[name]
            search = [posixpath.dirname(rel)] if level == 0 else None
            if level:
                base = posixpath.dirname(rel)
                for _ in range(level - 1):
                    base = posixpath.dirname(base)
                search = [base]
            else:
                search = dirs
            mods, _, _ = _resolve(parts, search, tracked)
            if len(mods) != 1:
                raise NotConst
            scan(mods[0])
            return lookup_in(mods[0], orig, depth + 1)
        raise NotConst

    def eval_path(rel, s, node):
        return _const(node, (s.abs, s.names, lambda n, d: lookup_in(rel, n, d)))

    def rel_dir(p):
        ap, rp = os.path.abspath(p), os.path.abspath(repo)
        return posixpath.relpath(ap, rp) if ap == rp or ap.startswith(rp + "/") else None

    changed = True
    while changed:
        changed = False
        while todo:
            rel = todo.pop()
            if rel in files:
                continue
            files[rel] = None
            scan(rel)
            changed = True
        for rel, s in sorted((r, s) for r, s in scans.items() if s):
            for node, line, what in s.paths:
                try:
                    p = eval_path(rel, s, node)
                except NotConst:
                    continue   # reported once after the fixpoint
                if not p.startswith("/"):
                    continue
                d = rel_dir(p)
                if d is not None and d not in seen_dirs:
                    seen_dirs.add(d)
                    dirs.append(d)
                    changed = True
        for rel, s in sorted((r, s) for r, s in scans.items() if s):
            for level, parts, names, line in s.imports:
                if level:
                    base = posixpath.dirname(rel)
                    for _ in range(level - 1):
                        base = posixpath.dirname(base)
                    search = [base]
                else:
                    search = dirs
                    top = parts[0] if parts else ""
                targets = []   # (module parts, is name list for submodules)
                if parts:
                    targets.append(parts)
                cand = []
                mods, inits, ns = _resolve(parts, search, tracked) if (parts or level) else ([], [], True)
                if level == 0 and parts and not mods and not ns:
                    if parts[0] in STDLIB or parts[0] in pinned:
                        continue
                    finds.append(("unresolved", rel, line, ".".join(parts)))
                    continue
                if level == 0 and parts and (parts[0] in STDLIB or parts[0] in pinned) and (mods or ns):
                    finds.append(("shadows", rel, line, "%s is a tracked file and a %s module" % (".".join(parts), "stdlib" if parts[0] in STDLIB else "pinned")))
                if len(mods) > 1 and parts:
                    finds.append(("ambiguous", rel, line, "%s: %s" % (".".join(parts), ", ".join(mods))))
                cand += mods + inits
                for nm in names:
                    if nm == "*":
                        if mods or inits:
                            finds.append(("dynamic-import", rel, line, "import * from a project module"))
                        continue
                    sm, si, _ = _resolve(parts + [nm], search, tracked)
                    if len(sm) > 1:
                        finds.append(("ambiguous", rel, line, "%s.%s: %s" % (".".join(parts), nm, ", ".join(sm))))
                    cand += sm
                    if not sm and not mods and not inits:
                        pass   # a name of a namespace directory that is neither a module nor a file: nothing to follow
                for c in cand:
                    if c not in files and c not in todo:
                        todo.append(c)
                        changed = True
    for r, s in scans.items():
        if s:
            finds += s.finds
            for node, line, what in s.paths:
                try:
                    if not eval_path(r, s, node).startswith("/"):
                        raise NotConst   # a relative path depends on the working directory
                except NotConst:
                    finds.append(("sys-path-dynamic", r, line, "sys.path.%s with a non-constant path" % what))
    return sorted(files), sorted(set(finds))


def file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def closure_key(files, repo, env_id, data=()):
    doc = {"closure": [[f, file_sha(os.path.join(repo, f))] for f in sorted(files)], "data": sorted([n, h] for n, h in data),
           "interpreter": sys.version, "env": env_id}
    return hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()


def stdlib_roots():
    p = sysconfig.get_paths()
    return tuple(os.path.realpath(p[k]) + "/" for k in ("stdlib", "platstdlib") if k in p) + (os.path.realpath(os.path.join(p["stdlib"], "lib-dynload")) + "/",)


def check_modules(modules, files, repo, pinned_paths):
    """Runtime verification: MODULES = [file path of each module in sys.modules at exit]. -> sorted [("outside-closure", path)] for every module file
    that is not in the closure FILES (repo-relative), not under the stdlib directories and not a file of an env-pinned distribution (PINNED_PATHS)."""
    inside = {os.path.realpath(os.path.join(repo, f)) for f in files}
    roots, out = stdlib_roots(), []
    for m in sorted(set(modules)):
        r = os.path.realpath(m)
        if r in inside or r in pinned_paths or any(r.startswith(x) for x in roots):
            continue
        out.append(("outside-closure", m))
    return out
