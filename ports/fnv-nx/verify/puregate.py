#!/usr/bin/env python3 -I
"""puregate: static gate for rule R12-T (a build stage's output is a function of its declared inputs).

    python3 -I puregate.py check PATH... [--json OUT.json]      exit 0 clean, 2 violations, 1 usage error

PATH: .py file or directory (sorted walk). Non-.py files are ignored, except shell files (suffix .sh/.bash or a shell
shebang): one `shell-unanalysable` violation each. Output `file:line: RULE symbol`, at most 200 lines (JSON: all).
SOUND for what it accepts: code it cannot analyse is a violation (`dynamic`, `syntax`), never a pass. Names resolve
through import bindings (import m [as a], from m import x [as y], at any depth) with Python scoping (function, lambda,
class, comprehension, global/nonlocal); a name bound by an import and by anything else in one scope is `dynamic`.
Policy (POLICY below, one class rule per row):
  clock       time.* except time.sleep; datetime.now/today/utcnow, date.today; os.path.get{m,a,c}time; stat.ST_*TIME;
              os.utime unless times is a tuple literal or ns= is given; attributes st_{m,a,c}time[_ns], st_birthtime
              on ANY expression.
  inode       attributes st_ino, st_dev, st_nlink; os.path.samefile/samestat/sameopenfile.
  entropy     random, secrets, tempfile; uuid.uuid1/uuid4; os.urandom/getrandom; builtins id, hash.
  unsorted-listing  os.listdir/scandir, glob.glob/iglob, methods iterdir/glob/rglob: only as first positional
              argument of sorted(). os.walk/fwalk: only `for R, D, F in os.walk(..)` (topdown) whose first statement
              is D.sort() and F used only as sorted(F) or after a second statement F.sort(); D, F never rebound.
  unsorted-set  set/frozenset literal, set comprehension or set()/frozenset() call directly iterated by for/comprehension.
  host        os.cpu_count/sched_getaffinity/uname/getpid/getppid/getlogin/getuid/times/getloadavg/statvfs...,
              multiprocessing.cpu_count, platform/socket/getpass/pwd/grp, threading.get_ident/get_native_id,
              resource.getrusage, shutil.disk_usage, string constants under /proc or /sys (str/bytes, f-strings too).
  completion-order  concurrent.futures.as_completed/wait, method imap_unordered, queue/multiprocessing (Simple)Queue.
  dynamic     eval exec compile __import__ __builtins__ globals locals, vars() without argument, importlib.*,
              builtins.*, sys.modules, getattr/setattr/delattr with a non-constant name or a module as first argument,
              `from m import *`, an unparsable or too deeply nested file (`syntax`), and any escape of a module/class that carries rules (time, os, os.path, datetime,
              datetime.datetime, glob, uuid, ...) other than as the base of an attribute access.
NOT covered: sets held in variables (PYTHONHASHSEED=0 plus the cold-build check); other modules the file imports (the
gate is per file); stdlib calls that read clocks/entropy internally (zipfile, tarfile, shutil.copy2, logging,
subprocess children); stat results used by index or unpacking; undeclared file/env/network reads; strings built at
run time; reflection via other helpers (operator.attrgetter, ctypes); list(set(..)); float non-associativity.
"""
import ast
import hashlib
import json
import os
import sys

# (rule, exact qualified names, qualified-name prefixes)
POLICY = [
    ("clock", "datetime.datetime.now datetime.datetime.today datetime.datetime.utcnow datetime.date.today "
     "os.path.getmtime os.path.getatime os.path.getctime os.utime stat.ST_MTIME stat.ST_ATIME stat.ST_CTIME", "time."),
    ("inode", "os.path.samefile os.path.samestat os.path.sameopenfile", ""),
    ("entropy", "uuid.uuid1 uuid.uuid4 os.urandom os.getrandom", "random. secrets. tempfile."),
    ("unsorted-listing", "os.listdir os.scandir glob.glob glob.iglob os.walk os.fwalk", ""),
    ("host", "os.cpu_count os.process_cpu_count multiprocessing.cpu_count os.sched_getaffinity os.uname os.getpid "
     "os.getppid os.getlogin os.getuid os.geteuid os.getgid os.times os.getloadavg os.statvfs threading.get_ident "
     "threading.get_native_id resource.getrusage shutil.disk_usage", "platform. socket. getpass. pwd. grp."),
    ("completion-order", "concurrent.futures.as_completed concurrent.futures.wait queue.Queue queue.SimpleQueue "
     "multiprocessing.Queue multiprocessing.SimpleQueue", ""),
    ("dynamic", "sys.modules", "importlib. builtins."),
]
ATTR = {"imap_unordered": "completion-order", "iterdir": "unsorted-listing", "glob": "unsorted-listing",
        "rglob": "unsorted-listing", "st_ino": "inode", "st_dev": "inode", "st_nlink": "inode"}
ATTR.update({a: "clock" for a in "st_mtime st_atime st_ctime st_mtime_ns st_atime_ns st_ctime_ns st_birthtime".split()})
WALK = ("os.walk", "os.fwalk")
CANON = {"posixpath": "os.path", "genericpath": "os.path", "ntpath": "os.path"}
BAD_BUILTIN = ("eval", "exec", "compile", "__import__", "globals", "locals", "__builtins__")
CALL_BUILTIN = ("getattr", "setattr", "delattr", "vars")
PROC = tuple("/" + x for x in ("proc", "sys"))
NF = object()  # name bound in no scope: a builtin (or undefined)
CONT = {"sys", "importlib", "builtins"}  # modules/classes whose attributes carry rules: they must not escape
for _r, _ex, _pre in POLICY:
    CONT.update(q.rsplit(".", i)[0] for q in _ex.split() for i in range(1, q.count(".") + 1))
    CONT.update(p.rstrip(".") for p in _pre.split())

def classify(q):
    for rule, ex, pre in POLICY:
        if q in ex.split() or any(q.startswith(p) for p in pre.split()): return None if q == "time.sleep" else rule
    return None

class Scope:
    def __init__(self, parent, kind): self.p, self.k, self.b, self.g, self.nl, self.c = parent, kind, {}, [], [], []

def split(n):
    """(children evaluated in the enclosing scope, children of the new scope, kind, parameter names) or None."""
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = n.args
        ps = a.posonlyargs + a.args + a.kwonlyargs + [x for x in (a.vararg, a.kwarg) if x]
        outer = a.defaults + [d for d in a.kw_defaults if d] + [p.annotation for p in ps if p.annotation]
        if isinstance(n, ast.Lambda): return outer, [n.body], "f", [p.arg for p in ps]
        return outer + n.decorator_list + ([n.returns] if n.returns else []), n.body, "f", [p.arg for p in ps]
    if isinstance(n, ast.ClassDef): return n.decorator_list + n.bases + n.keywords, n.body, "c", []
    if isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        g, elt = n.generators, ([n.key, n.value] if isinstance(n, ast.DictComp) else [n.elt])
        return [g[0].iter], [g[0].target] + g[0].ifs + g[1:] + elt, "k", []
    return None

def is_sort(st, name):
    v = st.value if isinstance(st, ast.Expr) else None
    return (isinstance(v, ast.Call) and not v.args and not v.keywords and isinstance(v.func, ast.Attribute)
            and v.func.attr == "sort" and isinstance(v.func.value, ast.Name) and v.func.value.id == name)

def sorted_call(c):
    return isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "sorted" and bool(c.args)

class Analyzer:
    def __init__(self, final):
        self.final, self.out = final, []  # pass 1 (final=False) binds names; pass 2 checks uses
    def add(self, n, rule, sym):
        self.out.append((getattr(n, "lineno", 1), rule, sym))
    def go(self, n, sc):
        sp = split(n)
        if sp is None:
            self.leaf(n, sc)
            for c in ast.iter_child_nodes(n): self.go(c, sc)
            return
        outer, inner, kind, params = sp
        self.leaf(n.generators[0] if kind == "k" else n, sc)
        for c in outer: self.go(c, sc)
        if self.final: ns = n._sc
        else:
            ns = n._sc = Scope(sc, kind)
            for p in params: self.bind(ns, p, None, n)
        for c in inner: self.go(c, ns)
    def bind(self, sc, name, val, n):
        if name in sc.nl:  # assignment to an enclosing function's variable: only an import there is a problem
            if isinstance(self.look(name, sc.p), str): self.add(n, "dynamic", "rebind:" + name)
            return
        while name in sc.g and sc.p: sc = sc.p
        v = sc.b.setdefault(name, [])
        if val not in v: v.append(val)
        if len(v) > 1 and name not in sc.c:
            sc.c.append(name)
            self.add(n, "dynamic", "rebind:" + name)
    def look(self, name, sc):
        t, first = sc, True
        while t:
            while name in t.g and t.p: t = t.p
            if (t.k != "c" or first) and name in t.b: return t.b[name][0] if len(t.b[name]) == 1 else None
            t, first = t.p, False
        return NF
    def res(self, n, sc):
        """Dotted qualified name of a Name/Attribute chain rooted in an import binding, else None."""
        if isinstance(n, ast.Name):
            r = self.look(n.id, sc)
            return r if isinstance(r, str) else None
        b = self.res(n.value, sc) if isinstance(n, ast.Attribute) else None
        return b + "." + n.attr if b else None
    def leaf(self, n, sc):
        if not self.final: return self.binder(n, sc)
        if isinstance(n, (ast.Name, ast.Attribute)): self.use(n, sc)
        elif isinstance(n, ast.Call): self.call(n, sc)
        elif isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)):
            v = n.value if isinstance(n.value, str) else n.value.decode("latin-1")
            if v in PROC or v.startswith(tuple(x + "/" for x in PROC)): self.add(n, "host", v[:40])
        elif isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names):
            self.add(n, "dynamic", "import*:" + str(n.module))
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            it = n.iter
            if isinstance(it, (ast.Set, ast.SetComp)) or (isinstance(it, ast.Call) and isinstance(it.func, ast.Name)
                                                          and it.func.id in ("set", "frozenset") and self.look(it.func.id, sc) is NF):
                self.add(it, "unsorted-set", "set")
            if isinstance(n, ast.For) and isinstance(it, ast.Call) and self.res(it.func, sc) in WALK:
                it._ok = self.walk_ok(n, it, sc)
    def binder(self, n, sc):
        if isinstance(n, ast.NamedExpr): n.target._w = True
        if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            while getattr(n, "_w", False) and sc.k == "k": sc = sc.p
            self.bind(sc, n.id, None, n)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): self.bind(sc, n.name, None, n)
        elif isinstance(n, ast.Import):
            for a in n.names:
                q = a.name if a.asname else a.name.split(".")[0]
                self.bind(sc, a.asname or q, CANON.get(q, q), n)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                m = CANON.get(n.module, n.module)
                self.bind(sc, a.asname or a.name, None if n.level or not m else m + "." + a.name, n)
        elif isinstance(n, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and n.name: self.bind(sc, n.name, None, n)
        elif isinstance(n, ast.MatchMapping) and n.rest: self.bind(sc, n.rest, None, n)
        elif isinstance(n, (ast.Global, ast.Nonlocal)): (sc.g if isinstance(n, ast.Global) else sc.nl).extend(n.names)
    def use(self, n, sc):
        p = n._p
        if isinstance(n, ast.Name):
            if not isinstance(n.ctx, ast.Load): return
            if self.look(n.id, sc) is NF:
                if n.id in ("id", "hash"): self.add(n, "entropy", n.id)
                elif n.id in BAD_BUILTIN or (n.id in CALL_BUILTIN and not (isinstance(p, ast.Call) and p.func is n)):
                    self.add(n, "dynamic", n.id)
                return
        q = self.res(n, sc)
        rule = classify(q) if q else None
        sym = q
        if rule is None and isinstance(n, ast.Attribute) and n.attr in ATTR: rule, sym = ATTR[n.attr], "." + n.attr
        calls = isinstance(p, ast.Call) and p.func is n
        if rule:
            if sym in WALK: ok = calls and getattr(p, "_ok", False)
            elif sym == "os.utime": ok = calls and self.utime_ok(p)
            elif rule == "unsorted-listing":
                ok = calls and sorted_call(p._p) and p._p.args[0] is p and self.look("sorted", sc) is NF
            elif q and isinstance(p, ast.Attribute) and p.value is n and classify(q + "." + p.attr) == rule:
                return  # the enclosing attribute reports it
            else: ok = False
            if not ok: self.add(n, rule, sym)
        elif q in CONT and not (isinstance(p, ast.Attribute) and p.value is n): self.add(n, "dynamic", q)
    @staticmethod
    def utime_ok(c):
        kw = {k.arg: k.value for k in c.keywords}
        t, ns = (c.args[1] if len(c.args) > 1 else kw.get("times")), kw.get("ns")
        if None in kw or any(isinstance(a, ast.Starred) for a in c.args): return False
        return isinstance(t, (ast.Tuple, ast.List)) if t is not None else ns is not None and not (
            isinstance(ns, ast.Constant) and ns.value is None)
    def call(self, n, sc):
        f, a = n.func, n.args
        if not (isinstance(f, ast.Name) and f.id in CALL_BUILTIN and self.look(f.id, sc) is NF): return
        if f.id == "vars":
            if not a and not n.keywords: self.add(n, "dynamic", "vars()")
            return
        mod = self.res(a[0], sc) if a else None  # a module binding as first argument (its use is reported itself)
        good = (len(a) >= 2 and not any(isinstance(x, ast.Starred) for x in a) and all(k.arg for k in n.keywords)
                and isinstance(a[1], ast.Constant) and isinstance(a[1].value, str) and mod is None)
        if not good and mod not in CONT: self.add(n, "dynamic", f.id + "(non-constant)")
        elif good and a[1].value in ATTR: self.add(n, ATTR[a[1].value], f.id + ":" + a[1].value)
    def walk_ok(self, f, c, sc):
        t, b, kws = f.target, f.body, {k.arg: k.value for k in c.keywords}
        td = kws.pop("topdown", None)
        if not (isinstance(t, ast.Tuple) and len(t.elts) >= 3 and all(isinstance(e, ast.Name) for e in t.elts[:3])): return False
        d, fl = t.elts[1].id, t.elts[2].id
        if (d == fl or len(c.args) != 1 or isinstance(c.args[0], ast.Starred) or not (b and is_sort(b[0], d))
                or any(k not in ("onerror", "followlinks") for k in kws)
                or (td is not None and not (isinstance(td, ast.Constant) and td.value is True))): return False
        pre = len(b) > 1 and is_sort(b[1], fl)
        for x in [y for st in b for y in ast.walk(st)]:
            if isinstance(x, ast.Name) and x.id in (d, fl):
                if not isinstance(x.ctx, ast.Load) or (x.id == fl and not pre and not (
                        sorted_call(x._p) and x._p.args[0] is x and self.look("sorted", sc) is NF)): return False
        return True

def check_source(text, name):
    """Return the sorted unique [(line, rule, symbol)] violations of one Python source text."""
    try:
        tree = ast.parse(text, name)
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n): c._p = n
        root, out = Scope(None, "m"), []
        for final in (False, True):
            a = Analyzer(final)
            for st in tree.body: a.go(st, root)
            out += a.out
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        return [(getattr(e, "lineno", None) or 1, "syntax", type(e).__name__)]
    return sorted(set(out))

def is_shell(path, head):
    if path.endswith((".sh", ".bash")): return True
    tok = head[2:].split(b"\n")[0].decode("latin-1").split() if head.startswith(b"#!") else []
    if tok and os.path.basename(tok[0]) == "env": tok = [t for t in tok[1:] if not t.startswith("-") and "=" not in t]
    return bool(tok) and os.path.basename(tok[0]) in ("sh", "bash", "dash", "zsh", "ksh", "ash", "csh", "tcsh", "fish")

def collect(p, acc):
    if os.path.isdir(p) and not os.path.islink(p):
        for e in sorted(os.listdir(p)): collect(os.path.join(p, e), acc)
    elif p not in acc: acc.append(p)

def analyse(path):
    """Violations of one file as [(line, rule, symbol)], or None if the file is not subject to the gate."""
    try:
        with open(path, "rb") as fh: data = fh.read()
        if path.endswith(".py"): return check_source(data.decode("utf-8"), path)
        return [(1, "shell-unanalysable", "shell")] if is_shell(path, data[:256]) else None
    except (OSError, UnicodeDecodeError) as e:
        return [(1, "syntax", type(e).__name__)] if path.endswith(".py") else None

def main(argv):
    args, js = argv[1:], None
    if "--json" in args[:-1]:
        i = args.index("--json")
        js = args[i + 1]
        del args[i:i + 2]
    paths = args[1:]
    if args[:1] != ["check"] or not paths or any(not os.path.exists(p) for p in paths):
        sys.stderr.write("usage: puregate.py check PATH... [--json OUT.json] (PATHs must exist)\n")
        return 1
    files, seen, viol, by_rule, by_file = [], [], [], {}, {}
    for p in paths: collect(p, files)
    for f in files:
        r = analyse(f)
        if r is not None:
            seen.append(f)
            viol += [{"file": f, "line": ln, "rule": ru, "symbol": s} for ln, ru, s in r]
    for v in viol:
        for d, k in ((by_rule, v["rule"]), (by_file, v["file"])): d[k] = d.get(k, 0) + 1
    for v in viol[:200]: print("%s:%d: %s %s" % (v["file"], v["line"], v["rule"], v["symbol"]))
    if js:
        tool = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
        doc = {"schema": "fnvnx-puregate/1", "tool": tool, "files": seen, "violations": viol, "counts_by_rule": by_rule,
               "counts_by_file": by_file}
        with open(js, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    detail = ", ".join("%s: %d" % (r, by_rule[r]) for r in sorted(by_rule)) or "none"
    print("puregate: %d files, %d violations (%s)" % (len(seen), len(viol), detail))
    return 2 if viol else 0

if __name__ == "__main__": sys.exit(main(sys.argv))
