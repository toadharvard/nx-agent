#!/usr/bin/env python3 -I
"""puregate: DEFAULT-DENY static gate for rule R12-T (a build stage's output is a function of its declared inputs).

    python3 -I puregate.py check PATH... [--json OUT.json]      exit 0 clean, 2 violations, 1 usage error
    python3 -I puregate.py audit-stdlib                          check the PURE table (A-stdlib): a report, not a gate

PATH: .py file or directory (sorted walk); shell files (suffix .sh/.bash or shell shebang): `shell-unanalysable`. Output
`file:line: RULE symbol`, at most 200 lines (JSON: all). Policy tables: puregate_policy.py (data only; rule classes there).
"Accepted" means: no Python code in the checked files can read an undeclared channel (clock, entropy, host identity,
directory/set order, file times/inodes), UNDER THE ASSUMPTIONS below. Anything not analysable is a violation, never a pass.
Names resolve through import bindings with Python scoping; a name bound by an import and by anything else in one scope is
`dynamic`; an escape of a module/class that carries rules (other than as an attribute base) is `dynamic`.
Rules: the class rules of POLICY/ATTR (clock inode entropy unsorted-listing host completion-order dynamic) plus
  import-unlisted  every import must be (a) PURE stdlib, (b) RESTRICTED stdlib (only allow-listed members, else the rule
                   of the member or `member-unlisted`), (c) PROJECT, (d) EXTERNAL (A-ext), or a banned module whose uses
                   are ruled. Project rule: `import a.b` / `from a.b import x` resolves a/b.py or a/b/__init__.py below
                   the importing file's directory or a directory PATH; `from .m import x` below the importing file's
                   directory (one level up per extra dot); `x` must be a submodule or (module a real file) a name in it.
                   The file must be among the checked files (a namespace directory counts only for submodules); a
                   checked file with the name of a listed module is ambiguous. Project modules are checked in the same
                   run, reported under their own path. Import roots (`--import-roots DIR...`, table IMPORT_ROOTS of the
                   policy): a module found there is allowed iff its file has no violation itself (its imports included);
                   else `import-unlisted impure:MOD -> FILE: n violations (first ...)` at the importer; the file is not an item.
  stat-result      os.stat/lstat/fstat or a .stat()/.lstat() method call: only as the value of `.st_size`/`.st_mode`.
  unsorted-set     set literal/comprehension/set()/frozenset() (also |,&,-,^ and union..copy of those) directly iterated,
                   unpacked, starred, or passed to list tuple enumerate iter map zip filter reversed, .join .extend
                   .fromkeys, or .pop().
  clock (archives) gzip/zipfile/tarfile only for reading; writing needs gzip mtime=<not None>, else `clock`; a mode that is
                   not a constant string, or a call with *args/**kw, is `dynamic`.
  dynamic          also vars() dir() with any argument and attributes __dict__ __getattribute__ __subclasses__ __globals__
                   __code__ __self__ __closure__ f_globals f_back ...; sorted/min/max key=id|hash is `entropy` (id, hash).
Counter set_iterations_unproven (not a violation): for/comprehension iterations over a bare Name or Attribute, i.e. sets
held in variables whose origin the gate cannot see (an upper bound: every such iteration is counted). Floats: out of
scope (IEEE operations are deterministic).
ASSUMPTIONS (named; what "accepted" does NOT prove):
  A-stdlib   modules in the PURE table and the allowed members of RESTRICTED ones do not read clock/entropy/host identity
             (checked by `audit-stdlib`, which runs these rules over their sources; C modules are listed unaudited).
  A-ext      the EXTERNAL modules (summary line: `external modules assumed pure`) are pure.
  A-hashseed PYTHONHASHSEED=0 and str/bytes/int/tuple-of-those set elements: set iteration order is a function of the
             insertion sequence (sets held in variables; see the counter).
  A-path     at run time the project imports resolve to the files this gate resolved (no other sys.path content).
  A-child    subprocess children, the files read and the environment are declared inputs (governed by the stage document).
  A-text     strings built at run time are not file names under the host-identity trees.
"""
import ast
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import puregate_policy as P

PROC = tuple("/" + x for x in ("proc", "sys"))
WALK = ("os.walk", "os.fwalk")
NF = object()  # name bound in no scope: a builtin (or undefined)
CONT = {"sys", "importlib", "builtins"} | set(P.RESTRICTED)  # modules/classes whose attributes carry rules: no escape
for _r, _ex, _pre in P.POLICY:
    CONT.update(q.rsplit(".", i)[0] for q in _ex.split() for i in range(1, q.count(".") + 1))
    CONT.update(p.rstrip(".") for p in _pre.split())

def classify(q):
    for rule, ex, pre in P.POLICY:
        if q in ex.split() or any(q.startswith(p) for p in pre.split()): return None if q == "time.sleep" else rule
    return None

def allowed(q):
    """None if q is not below a RESTRICTED module, else whether the member path is allow-listed (or leads to one)."""
    k = max((m for m in P.RESTRICTED if q == m or q.startswith(m + ".")), key=len, default=None)
    if k is None or q == k: return None if k is None else True
    rel, al = q[len(k) + 1:], P.RESTRICTED[k].split()
    ps = rel.split(".")
    return "*" in al or any(".".join(ps[:i]) in al for i in range(1, len(ps) + 1)) or any(
        a.endswith("*") and ps[0].startswith(a[:-1]) or a.startswith(rel + ".") for a in al)

def norm(p): return os.path.normpath(os.path.abspath(p))

def locate(files, base, parts):
    """File of `files` realising module `parts` below directory `base`, "" for a namespace directory, else None."""
    d = os.path.join(base, *parts)
    for c in ((d + ".py",) if parts else ()) + (os.path.join(d, "__init__.py"),):
        if c in files: return c
    return "" if any(f.startswith(d + os.sep) for f in files) else None

def listed(mod, names=()):
    ok = lambda m: any(m == k or m.startswith(k + ".") for k in P.LISTED)
    return ok(mod) or (bool(names) and all(ok(mod + "." + x) for x in names))

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
    def __init__(self, final, src, proj):
        self.final, self.out, self.src, self.proj = final, [], src, proj  # pass 1 binds names; pass 2 checks uses
        self.unproven, self.ext = 0, set()
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
    def is_set(self, x, sc):
        if isinstance(x, (ast.Set, ast.SetComp)): return True
        if isinstance(x, ast.Call):
            f = x.func
            if isinstance(f, ast.Name): return f.id in ("set", "frozenset") and self.look(f.id, sc) is NF
            return isinstance(f, ast.Attribute) and f.attr in P.SETOPS and self.is_set(f.value, sc)
        return isinstance(x, ast.BinOp) and isinstance(x.op, (ast.BitOr, ast.BitAnd, ast.Sub, ast.BitXor)) and (
            self.is_set(x.left, sc) or self.is_set(x.right, sc))
    def bad_imports(self, n):
        """Symbols of the imports of an Import/ImportFrom node that are outside the allow-list."""
        files, roots, *imp = self.proj
        imp = imp[0] if imp else None
        lvl, here, out = getattr(n, "level", 0), os.path.dirname(self.src), []
        ents = [(a.name, []) for a in n.names] if isinstance(n, ast.Import) else [
            (n.module or "", [a.name for a in n.names if a.name != "*"])]
        for mod, names in ents:
            parts = mod.split(".") if mod else []
            bases = [here] if lvl else [here] + list(roots)
            for _ in range(lvl - 1): bases[0] = os.path.dirname(bases[0])
            hit, b = None, None
            for b in bases:
                hit = locate(files, b, parts) if parts or lvl else None
                if hit is not None: break
            if hit is None:
                if lvl or not listed(mod, names): out.append("." * lvl + mod)
                elif parts[0] in P.EXTERNAL: self.ext.add(parts[0])
            elif not lvl and listed(parts[0]): out.append("ambiguous:" + mod)
            else: out += [mod + "." + x for x in names if not hit and locate(files, b, parts + [x]) is None]
            if hit and imp and hit in imp.files:  # a module of an import root: allowed iff that file is itself accepted
                why = imp.verdict(hit)
                if why: out.append("impure:%s -> %s" % (mod, why))
        return out
    def leaf(self, n, sc):
        if not self.final: return self.binder(n, sc)
        if isinstance(n, (ast.Name, ast.Attribute)): self.use(n, sc)
        elif isinstance(n, ast.Call): self.call(n, sc)
        elif isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)):
            v = n.value if isinstance(n.value, str) else n.value.decode("latin-1")
            if v in PROC or v.startswith(tuple(x + "/" for x in PROC)): self.add(n, "host", v[:40])
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for m in self.bad_imports(n): self.add(n, "import-unlisted", m)
            if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names): self.add(n, "dynamic", "import*:" + str(n.module))
        elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
            it = n.iter
            if self.is_set(it, sc): self.add(it, "unsorted-set", "set")
            elif isinstance(it, (ast.Name, ast.Attribute)): self.unproven += 1
            if isinstance(n, ast.For) and isinstance(it, ast.Call) and self.res(it.func, sc) in WALK:
                it._ok = self.walk_ok(n, it, sc)
        elif isinstance(n, (ast.Starred, ast.YieldFrom)) and self.is_set(n.value, sc): self.add(n, "unsorted-set", "*set")
        elif isinstance(n, ast.Assign) and isinstance(n.targets[0], (ast.Tuple, ast.List)) and self.is_set(n.value, sc):
            self.add(n, "unsorted-set", "unpack")
    def binder(self, n, sc):
        if isinstance(n, ast.NamedExpr): n.target._w = True
        if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            while getattr(n, "_w", False) and sc.k == "k": sc = sc.p
            self.bind(sc, n.id, None, n)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): self.bind(sc, n.name, None, n)
        elif isinstance(n, ast.Import):
            for a in n.names:
                q = a.name if a.asname else a.name.split(".")[0]
                self.bind(sc, a.asname or q, P.CANON.get(q, q), n)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                m = P.CANON.get(n.module, n.module)
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
                elif n.id in P.BAD_BUILTIN or (n.id in P.CALL_BUILTIN and not (isinstance(p, ast.Call) and p.func is n)):
                    self.add(n, "dynamic", n.id)
                return
        q = self.res(n, sc)
        rule = classify(q) if q else None
        sym = q
        if rule is None and q in P.STATQ: rule = "stat-result"
        if rule is None and isinstance(n, ast.Attribute) and n.attr in P.ATTR: rule, sym = P.ATTR[n.attr], "." + n.attr
        calls = isinstance(p, ast.Call) and p.func is n
        if rule:
            if sym in WALK: ok = calls and getattr(p, "_ok", False)
            elif sym == "os.utime": ok = calls and self.utime_ok(p)
            elif rule == "stat-result": ok = calls and self.stat_ok(p)
            elif rule == "unsorted-listing":
                ok = calls and sorted_call(p._p) and p._p.args[0] is p and self.look("sorted", sc) is NF
            elif q and isinstance(p, ast.Attribute) and p.value is n and classify(q + "." + p.attr) == rule:
                return  # the enclosing attribute reports it
            else: ok = False
            if not ok: self.add(n, rule, sym)
        elif q in CONT and not calls and not (isinstance(p, ast.Attribute) and p.value is n): self.add(n, "dynamic", q)
        elif q in P.MODECALLS: self.modecall(n, q)
        elif q and allowed(q) is False and not (isinstance(n, ast.Attribute) and allowed(self.res(n.value, sc)) is False):
            self.add(n, "member-unlisted", q)
    @staticmethod
    def utime_ok(c):
        kw = {k.arg: k.value for k in c.keywords}
        t, ns = (c.args[1] if len(c.args) > 1 else kw.get("times")), kw.get("ns")
        if None in kw or any(isinstance(a, ast.Starred) for a in c.args): return False
        return isinstance(t, (ast.Tuple, ast.List)) if t is not None else ns is not None and not (
            isinstance(ns, ast.Constant) and ns.value is None)
    @staticmethod
    def stat_ok(c):
        a = c._p
        return isinstance(a, ast.Attribute) and a.value is c and (a.attr in P.ST_OK or P.ATTR.get(a.attr, "stat-result") != "stat-result")
    def modecall(self, n, q):
        c = n._p
        if not (isinstance(c, ast.Call) and c.func is n): return self.add(n, "dynamic", q + ":not-called")
        a, kw = c.args, {k.arg: k.value for k in c.keywords}
        if None in kw or any(isinstance(x, ast.Starred) for x in a): return self.add(n, "dynamic", q + ":*")
        m, gz = (a[1] if len(a) > 1 else kw.get("mode")), q in ("gzip.GzipFile", "gzip.compress")
        if q != "gzip.compress" and (m is None and q == "gzip.GzipFile" and ("fileobj" in kw or len(a) > 3) or m is not None
                                     and not (isinstance(m, ast.Constant) and isinstance(m.value, str))):
            return self.add(n, "dynamic", q + ":mode")  # mode unknown or taken from a file object
        mt = kw.get("mtime", a[4] if q == "gzip.GzipFile" and len(a) > 4 else None)
        if (q == "gzip.compress" or m is not None and not m.value.startswith("r")) and not (
                gz and mt is not None and not (isinstance(mt, ast.Constant) and mt.value is None)):
            self.add(n, "clock", q + ":write")
    def call(self, n, sc):
        f, a = n.func, n.args
        if isinstance(f, ast.Name) and f.id in P.ORDERED and self.look(f.id, sc) is NF and any(self.is_set(x, sc) for x in a):
            self.add(n, "unsorted-set", f.id + "(set)")
        elif isinstance(f, ast.Attribute) and (f.attr in P.ORDER_ATTRS and any(self.is_set(x, sc) for x in a) or (
                f.attr == "pop" and not a and self.is_set(f.value, sc))):
            self.add(n, "unsorted-set", "." + f.attr)
        if not (isinstance(f, ast.Name) and f.id in P.CALL_BUILTIN and self.look(f.id, sc) is NF): return
        if f.id in ("vars", "dir"): return self.add(n, "dynamic", f.id + "()")
        mod = self.res(a[0], sc) if a else None  # a module binding as first argument (its use is reported itself)
        good = (len(a) >= 2 and not any(isinstance(x, ast.Starred) for x in a) and all(k.arg for k in n.keywords)
                and isinstance(a[1], ast.Constant) and isinstance(a[1].value, str) and mod is None)
        if not good and mod not in CONT: self.add(n, "dynamic", f.id + "(non-constant)")
        elif good and a[1].value in P.ATTR: self.add(n, P.ATTR[a[1].value], f.id + ":" + a[1].value)
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

NOPROJ = (frozenset(), ())

class ImportRoots:
    """Files below the import roots (policy table IMPORT_ROOTS, each an admitted tool root): they resolve like project files but are
    not items of the run. An import that resolves into one is allowed iff that file is itself accepted (no violation of its own,
    which includes its own imports: transitive); else the importer gets `import-unlisted impure:MOD -> FILE: n violations (first)`.
    A cycle is judged by the violations of its members. proj: the project context the files are analysed in (set by main)."""
    def __init__(self, files): self.files, self.memo, self.proj = frozenset(files), {}, None
    def verdict(self, f):
        if f not in self.memo:
            self.memo[f] = None
            r = analyse(f, self.proj)
            v = r[0] if r else []
            self.memo[f] = ("%s: %d violations (first: line %d %s %s)" % (os.path.relpath(f), len(v), v[0][0], v[0][1], v[0][2])) if v else None
        return self.memo[f]

def scan(text, name, proj=NOPROJ):
    """([(line, rule, symbol)] sorted unique, set_iterations_unproven, external modules imported) of one Python source."""
    try:
        tree = ast.parse(text, name)
        for n in ast.walk(tree):
            for c in ast.iter_child_nodes(n): c._p = n
        root, out = Scope(None, "m"), []
        for final in (False, True):
            a = Analyzer(final, norm(name), proj)
            for st in tree.body: a.go(st, root)
            out += a.out
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        return [(getattr(e, "lineno", None) or 1, "syntax", type(e).__name__)], 0, []
    return sorted(set(out)), a.unproven, sorted(a.ext)

def check_source(text, name, proj=NOPROJ):
    """Return the sorted unique [(line, rule, symbol)] violations of one Python source text."""
    return scan(text, name, proj)[0]

def is_shell(path, head):
    if path.endswith((".sh", ".bash")): return True
    tok = head[2:].split(b"\n")[0].decode("latin-1").split() if head.startswith(b"#!") else []
    if tok and os.path.basename(tok[0]) == "env": tok = [t for t in tok[1:] if not t.startswith("-") and "=" not in t]
    return bool(tok) and os.path.basename(tok[0]) in ("sh", "bash", "dash", "zsh", "ksh", "ash", "csh", "tcsh", "fish")

def collect(p, acc):
    if os.path.isdir(p) and not os.path.islink(p):
        for e in sorted(os.listdir(p)): collect(os.path.join(p, e), acc)
    elif p not in acc: acc.append(p)

def analyse(path, proj=NOPROJ):
    """(violations of one file as [(line, rule, symbol)], unproven count, external modules), or None if not subject to the gate."""
    try:
        with open(path, "rb") as fh: data = fh.read()
        if path.endswith(".py"): return scan(data.decode("utf-8"), path, proj)
        return ([(1, "shell-unanalysable", "shell")], 0, []) if is_shell(path, data[:256]) else None
    except (OSError, UnicodeDecodeError) as e:
        return ([(1, "syntax", type(e).__name__)], 0, []) if path.endswith(".py") else None

def audit():
    """A-stdlib self-check: the gate's own deny rules over the sources of every PURE-table module of this interpreter."""
    dirs = [d for d in sys.path if d and os.path.isabs(d) and os.path.isdir(d)]
    clean = found = cmod = 0
    for m in sorted(P.PURE):
        hit = next((x for d in dirs for x in (os.path.join(d, m + ".py"), os.path.join(d, m))
                    if os.path.isfile(x) or os.path.isfile(os.path.join(x, "__init__.py"))), None)
        if hit is None:
            print("%s: c-module, not audited" % m)
            cmod += 1
            continue
        files, cnt = [], {}
        collect(hit, files)
        for f in files:
            for _, ru, _s in (analyse(f) or ([],))[0]: cnt[ru] = cnt.get(ru, 0) + 1
        real = ", ".join("%s: %d" % (r, cnt[r]) for r in sorted(cnt) if r != "import-unlisted")
        print("%s: %s%s" % (m, real or "clean", " [imports outside the tables: %d]" % cnt["import-unlisted"] if "import-unlisted" in cnt else ""))
        clean, found = clean + (not real), found + bool(real)
    print("audit-stdlib: %d clean, %d with findings, %d c-modules, python %d.%d" % (clean, found, cmod, sys.version_info[0], sys.version_info[1]))
    return 0

def main(argv):
    args, js = argv[1:], None
    if args == ["audit-stdlib"]: return audit()
    if "--json" in args[:-1]:
        i = args.index("--json")
        js = args[i + 1]
        del args[i:i + 2]
    iroots = []
    if "--import-roots" in args:  # directories (table IMPORT_ROOTS) whose files are importable if accepted, not items
        i = args.index("--import-roots")
        j = next((k for k in range(i + 1, len(args)) if args[k].startswith("--")), len(args))
        iroots = args[i + 1:j]
        del args[i:j]
    paths = args[1:]
    if args[:1] != ["check"] or not paths or any(not os.path.exists(p) for p in paths + iroots):
        sys.stderr.write("usage: puregate.py check PATH... [--import-roots DIR...] [--json OUT.json] | audit-stdlib (PATHs must exist)\n")
        return 1
    files, seen, viol, by_rule, by_file, unproven, ext = [], [], [], {}, {}, {}, set()
    for p in paths: collect(p, files)
    ifiles = []
    for p in iroots: collect(p, ifiles)
    imp = ImportRoots(norm(f) for f in ifiles if f.endswith(".py") and f not in files)
    proj = (frozenset(norm(f) for f in files if f.endswith(".py")) | imp.files,
            tuple(norm(p) for p in paths if os.path.isdir(p)) + tuple(norm(p) for p in iroots if os.path.isdir(p)), imp)
    imp.proj = proj
    for f in files:
        r = analyse(f, proj)
        if r is not None:
            seen.append(f)
            viol += [{"file": f, "line": ln, "rule": ru, "symbol": s} for ln, ru, s in r[0]]
            unproven[f] = r[1]
            ext.update(r[2])
    for v in viol:
        for d, k in ((by_rule, v["rule"]), (by_file, v["file"])): d[k] = d.get(k, 0) + 1
    for v in viol[:200]: print("%s:%d: %s %s" % (v["file"], v["line"], v["rule"], v["symbol"]))
    total, ext = sum(unproven.values()), sorted(ext)
    if js:
        tool = hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest()
        doc = {"schema": "fnvnx-puregate/1", "tool": tool, "files": seen, "violations": viol, "counts_by_rule": by_rule,
               "counts_by_file": by_file, "set_iterations_unproven": total, "set_iterations_unproven_by_file": unproven,
               "external_modules_assumed_pure": ext}
        with open(js, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    detail = ", ".join("%s: %d" % (r, by_rule[r]) for r in sorted(by_rule)) or "none"
    print("puregate: %d files, %d violations (%s); set_iterations_unproven: %d; external modules assumed pure: %s"
          % (len(seen), len(viol), detail, total, ", ".join(ext) or "none"))
    return 2 if viol else 0

if __name__ == "__main__": sys.exit(main(sys.argv))
