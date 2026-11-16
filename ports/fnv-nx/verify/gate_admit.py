#!/usr/bin/env python3
"""Gate "admit" (fnvnx-gate/1): nothing is in the trunk without the gates that close its classes of failure.
    python3 -I gate_admit.py        run in the checkout root (a git worktree), env GATE_OUT; writes GATE_OUT/admit.json

Rule. verify/admit.json ("fnvnx-admit/1") lists the roots of the port tree: {"path", "class", ["recipe"], ["trees"], ["entry"]} (the cross-root Python imports of a root are derived from its sources, derived_imports); the gate set of a root is derived: the gates of verify/gates.d whose `covers` holds the root class (gatesdir.root_class), so no table here restates it.
`entry` (class tool: required, may be empty): the stage entry points of the root, .py files inside its trees. gate_pure's universe is the
import closure of these and of the `generated` tools of the recipes; a script only a gate driver runs is not an entry point.
A row is one root with one tree, its "path"; a `trees` list is red (a second tree is a second root that the first declares in `imports`). Two roots never share a tree; a root may lie inside another (a header root of a product root's include directory, a generated-only product root inside
a tool root): a file belongs to the NEAREST root, the deepest one that contains it. The root is named by "path" in `imports`.
`imports` (a root's python cross-root imports): a list of paths of other admitted roots; "pure" (gate_pure.py) judges the root
with its own directory and the parent directory of each imported root as import bases, so a root's python may import another
root's modules only when it declares it. Each entry must be an admitted root other than the root itself, without repeats; the
declared imports form no cycle (an error of the table: every file is uncertified).
Universe: every file git tracks below ports/fnv-nx (`git ls-files -z`), an item is its repo-relative path.
A file is certified iff ALL hold:
  1. a root owns it: the nearest root containing it (a root is a directory prefix or one exact file path);
  2. the root's class is one of CLASSES (a row that lists "gates" is red: the set is derived from gates.d `covers`, never written on a row);
  3. a product root names a "recipe": a file inside the root that the build gates read (how its code is compiled in the
     declared environment); the file exists.
Classes:
  doc      text for people: no gate required. Nothing in a doc root is compiled or run by a build.
  tool     stage and verification tools: "selftests" required (their tests pass in the declared environment).
  product  code that is compiled into the shipped binary: the gates whose `covers` hold it, the static closure of the run-time
           failure classes of verify/VERDICT.md. A gate covers a class by its own gates.d file, so a root is judged by
           every gate that declares its class, and the verdict is red for a gate whose universe is 0 while a covered root exists.
  test     tests linked against the real files of the product roots: the gates that cover class test, a "recipe" like a product root and a "target".
  header   headers shared by include (a directory of .h/.hh/.hpp/.hxx/.inc/.inl files that product and test roots name in `include`): no recipe, no
           "target" (a root with no units has no target: both are derived from the recipes that include it), no translation unit, no build.json.
           Gates: the ones that cover class header (they read sources). A header root is a home for `include` of every product and test root of either target.
  ref      reference files that tests/oracle.json records point at (spec, contract, model): no gate named, no recipe, no units, no link,
           no outputs. Every file of a ref root is the `path` of a reference of some tests/oracle.json, and none is a build.json or a
           test (is_test_file): a product or test root is never a ref root. The gates that cover class ref (gates.d `covers`) are the
           ones that read any root: pure, assumptions (and the stub gate where it exists).
Argument: the verdict is green only if certified == universe, so a tracked file outside every root, a root with a
missing gate, or a product root without its gates makes every merge red. The table maps directories to gates; it
holds no exception for a file, a function or a finding.
Assumption: `git ls-files` lists the tree under test (the verdict runs gates in a clean worktree of the commit). (ASM-041)"""
import json
import re
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gatesdir  # noqa: E402

TREE = "ports/fnv-nx"
ADMIT = TREE + "/verify/admit.json"
CLASSES = ("doc", "tool", "product", "ref", "test", "header")
UNIT_EXTS = (".c", ".cc", ".cpp", ".cxx", ".s", ".S")   # a translation unit (the same set recipe.py compiles); a header root holds none
# The roots whose python reads the host by design (the sandbox, the recipe, the gate drivers, the farm scripts): gate_pure.py HOST-READER modules.
# Every other tool and product root is judged by "pure".
HOST_READERS = (TREE + "/verify", TREE + "/farm")


def trees(r):
    """The tree of an admit root, as a list of one: its path."""
    return [r.get("path", "")]


def import_roots(imports, tree):
    """{module prefix: root path} of the roots a root DECLARES in admit.json `imports` (IMPORTS: their repo paths; TREE: the project tree, ports/fnv-nx): the ONE rule of
    cross-root Python imports. A declared root R is importable under exactly one module path: its path relative to TREE with `/` replaced by `.` (render/total ->
    `render.total`, so `import render.total.x`). Every consumer calls this: recipe.foreign_imports, the /imp mount (R is mounted at /imp/<that relative path>, /imp is
    the PYTHONPATH), gate_pure (the --base of the run is TREE) and gate_abspath. The packages are namespace packages (PEP 420): no __init__.py is invented. A bare
    import of a module of another root (`import x` for render/total/x.py) is not a spelling of this rule: it is red, with the qualified name as the hint."""
    out = {}
    for r in imports:
        rel = os.path.relpath(os.path.normpath(r), tree)
        if rel.startswith(".."):
            raise ValueError("import root %s lies outside %s" % (r, tree))
        out[".".join(rel.split(os.sep))] = r
    return out


def import_namespaces(prefixes):
    """The dotted prefixes that are namespace packages of PREFIXES (`render` for `render.total`): importable, no file of their own."""
    return {".".join(p.split(".")[:k]) for p in prefixes for k in range(1, len(p.split(".")))}


def recipe_paths(r):
    """The recipe files of an admit entry as written: its `recipe` path, or the paths of a {target: path} recipe, and the `recipe` of each entry
    variant (a per-target recipe spelled as a variant)."""
    rc = r.get("recipe")
    out = list(rc.values()) if isinstance(rc, dict) else [rc] if rc else []
    vs = r.get("variants")
    return out + [v["recipe"] for v in vs.values() if isinstance(v, dict) and "recipe" in v] if isinstance(vs, dict) else out


_IMPORT_MEMO = {}   # (abspath, mtime_ns, size) -> ([dotted module names a file imports], [line of a dynamic import])


def file_imports(path):
    """([dotted names a tracked .py file imports at any depth], [lines of dynamic imports]): by `ast` over Import and ImportFrom (`from m import a` names m and m.a; a relative
    import names nothing: it stays inside its package), and over importlib.import_module / __import__ calls, whose literal argument is a name and whose non-literal one is a
    dynamic import. An unparsable file names nothing (the python gates judge it)."""
    import ast
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if key not in _IMPORT_MEMO:
        names, dyn = [], []
        try:
            with open(path, "rb") as f:
                tree = ast.parse(f.read())
        except (SyntaxError, ValueError):
            tree = None
        for n in ast.walk(tree) if tree is not None else ():
            if isinstance(n, ast.Import):
                names += [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
                names += [n.module] + [n.module + "." + a.name for a in n.names]
            elif isinstance(n, ast.Call):
                fn = n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id if isinstance(n.func, ast.Name) else None
                if fn in ("import_module", "__import__"):
                    a0 = n.args[0] if n.args else None
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        names.append(a0.value)
                    else:
                        dyn.append(n.lineno)
        _IMPORT_MEMO[key] = (names, dyn)
    return _IMPORT_MEMO[key]


def derived_imports(rows, files, repo="."):
    """({root path: sorted paths of the OTHER roots whose modules its tracked .py files import}, [errors]): the ONE rule of cross-root Python imports, derived from the
    sources, never declared. ROWS: the admit root dicts; FILES: the tracked paths. A file belongs to the nearest root (longest tree). An import resolves by its
    qualified module path (import_roots: the deepest root whose prefix it names); a standard-library, pinned or root-local module is no edge. A dynamic import
    (importlib.import_module / __import__ with a non-literal argument) is an error: its edge cannot be derived. A bare module of another root (`import x`) is no
    spelling of this rule (the stage tools' import closure makes it red, recipe.foreign_imports)."""
    paths = sorted({r.get("path", "") for r in rows})
    prefixes = {}
    for p in paths:
        rel = os.path.relpath(os.path.normpath(p), TREE)
        if not rel.startswith("..") and rel != ".":
            prefixes[".".join(rel.split(os.sep))] = p
    trees_of = sorted(((t, r["path"]) for r in rows for t in trees(r)), key=lambda x: -len(x[0]))
    edges = {p: set() for p in paths}
    errs = []
    for f in files:
        if not f.endswith(".py"):
            continue
        own = next((p for t, p in trees_of if f == t or f.startswith(t + "/")), None)
        if own is None:
            continue
        names, dyn = file_imports(os.path.join(repo, f))
        errs += ["%s:%d: a dynamic import (importlib.import_module or __import__ with a non-literal argument): its root edge cannot be derived" % (f, ln) for ln in dyn]
        for nm in names:
            hit = [pre for pre in prefixes if nm == pre or nm.startswith(pre + ".")]
            if hit:
                r = prefixes[max(hit, key=len)]
                if r != own:
                    edges[own].add(r)
    return {p: sorted(v) for p, v in edges.items()}, errs


def imports_errors(roots):
    """Errors of the `imports` field of ROOTS (admit root dicts): there is none, the imports of a root are derived (derived_imports)."""
    return ["root %s: admit.json `imports` is derived from the sources of the root (derived_imports): delete the field" % r.get("path")
            for r in roots if "imports" in r]


def is_test_file(f):
    b = f.rsplit("/", 1)[-1]
    return b == "build.json" or (b.startswith("test_") and b.endswith(".py")) or "/tests/" in f


def cross_root_bare(files, admit, read):
    """{file: reason} of the tracked .py files that import a module of ANOTHER admitted root by its bare name (RULE QUALIFIED-IMPORT, one module path per module): the
    module table is the top-level .py files of each admitted root, and a root's module is imported by its qualified path (`contract.store`, `lift.proof.formgate`: the root's
    path below TREE, `/` as `.`, import_roots) from every file of every root. A bare name that is a sibling in the file's own directory, or a top-level module of the file's own
    root, is the file's own (it resolves first); any other bare name of a module of a root is the finding. READ(path) -> text (a file that does not parse is not judged here)."""
    import ast
    rts = sorted({r["path"] for r in admit.get("roots", []) if isinstance(r.get("path"), str) and r.get("class") != "doc"}, key=len, reverse=True)
    root_of = lambda f: next((r for r in rts if f.startswith(r + "/")), None)   # noqa: E731
    mods = {}
    for f in files:
        r = root_of(f)
        if r and f.endswith(".py") and f.rsplit("/", 1)[0] == r:
            mods.setdefault(f.rsplit("/", 1)[1][:-3], []).append(f)
    std = set(sys.stdlib_module_names)
    bad = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        r = root_of(f)
        try:
            tree = ast.parse(read(f))
        except (OSError, SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module] if isinstance(n, ast.ImportFrom) and not n.level and n.module else []
            for full in names:
                m = full.split(".")[0]
                hits = mods.get(m)
                if m in std or not hits or any(x.rsplit("/", 1)[0] in (f.rsplit("/", 1)[0], r) for x in hits):
                    continue
                bad.setdefault(f, "imports %s bare: a module of %s (qualified: %s)" % (
                    m, ", ".join(sorted({root_of(x) for x in hits})), ", ".join(sorted(".".join(os.path.relpath(root_of(x), TREE).split(os.sep) + [m]) for x in hits))))
    return bad


def judge(files, admit, exists, ref_paths=frozenset(), read=None, repo=None):
    """-> (uncertified {path: reason}, root errors [str]); pure function of its arguments. REF_PATHS: the tracked paths that a tests/oracle.json
    reference (spec, contract, model) points at; the files of a root of class ref must all be among them."""
    roots, errors = [], []
    if admit.get("format") != "fnvnx-admit/1":
        errors.append("admit.json: format is not fnvnx-admit/1")
    for r in admit.get("roots", []):
        path, cls = r.get("path", ""), r.get("class")
        why = []
        if "gates" in r:
            why.append('a row lists "gates": the gate set is derived from gates.d `covers`; a row names root and class only')
        if "trees" in r:
            why.append('a row names one tree, its "path": a second tree is a second root, with the import declared in `imports`')
        if cls == "tool":
            en = r.get("entry")
            if not isinstance(en, list) or any(not isinstance(e, str) or not e.endswith(".py") or not exists(e)
                                               or not any(e.startswith(t + "/") for t in trees(r)) for e in en):
                why.append("entry is not a list of existing .py files inside the root's trees")
        for t in (trees(r) if not why else [path]):
            if not t or t != t.strip("/") or not (t == TREE or t.startswith(TREE + "/")):
                why.append("%s is not below %s" % (t, TREE))
        if cls not in CLASSES:
            why.append("class %r is not one of %s" % (cls, ", ".join(CLASSES)))
        else:
            if cls == "ref" and "recipe" in r:
                why.append("a ref root has no recipe (it has no units, no link and no outputs)")
            if cls == "header":
                why += ['a header root has no "%s" (it has no units, so no target, recipe, variants or link: they are derived from the roots that include it)' % k
                        for k in ("recipe", "target", "variants", "cxx_origin") if k in r]
                why += ["a header root holds the translation unit %s (a root with a unit is a product or test root)" % f for f in files
                        if any(f.startswith(t + "/") for t in trees(r)) and (f.endswith(UNIT_EXTS) or f.rsplit("/", 1)[-1] == "build.json")]
            if cls == "test" and r.get("target") not in ("guest", "host"):
                why.append('a test root declares "target": "guest" or "host"')
            if "variants" in r:   # the variants of a product root: {NAME: {"target": "guest"|"host"}}, addressed as root@NAME (recipe.entry_of); the recipe holds their flags
                vs = r["variants"]
                if cls != "product" or not (isinstance(vs, dict) and vs and all(isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", n) and isinstance(e, dict)
                                                                                   and set(e) <= {"target"} and e.get("target", "guest") in ("guest", "host") for n, e in vs.items())):
                    why.append('"variants" is allowed on a product root only: {NAME: {"target": "guest"|"host"}}')
            if cls in ("product", "test"):
                rc = r.get("recipe", "")
                if isinstance(rc, dict):   # one recipe per target of the same units (recipe.norm_entry): both targets, the entry's target is one
                    if not (set(rc) == {"guest", "host"} and r.get("target") in rc and len(set(rc.values())) == 2
                            and not set(r.get("variants") or {}) & set(rc)):
                        why.append('a recipe {"guest": PATH, "host": PATH} names both targets, two distinct files, the entry\'s "target" is one of them, '
                                   "and no variant of the entry has a target's name")
                for rec in recipe_paths(r) or [""]:
                    if not isinstance(rec, str) or not any(rec.startswith(t + "/") for t in trees(r)) or not exists(rec):
                        why.append("recipe %r is not an existing file inside the root" % (rec,))
        roots.append((trees(r) if not why else [path], why))
    errors += imports_errors(admit.get("roots", []))
    if repo is not None:
        errors += derived_imports(admit.get("roots", []), files, repo)[1]
    paths = [t for ts, _ in roots for t in ts]
    errors += ["trees %s is named twice" % t for t in sorted({t for t in paths if paths.count(t) > 1})]   # nested roots are fine: a file belongs to the nearest one
    cls_of = {r.get("path", ""): r.get("class") for r in admit.get("roots", [])}
    bad = {}
    for f in files:
        hit = [(len(p), ts[0], why) for ts, why in roots for p in ts if f == p or f.startswith(p + "/")]
        if hit:   # the nearest root owns the file: the deepest tree that contains it
            deep = max(h[0] for h in hit)
            hit = list({(h[1], tuple(h[2])): (h[1], h[2]) for h in hit if h[0] == deep}.values())
        if len(hit) != 1:
            bad[f] = "in %d roots" % len(hit)
        elif hit[0][1]:
            bad[f] = "root %s: %s" % (hit[0][0], "; ".join(hit[0][1]))
        elif cls_of[hit[0][0]] == "ref" and (is_test_file(f) or f not in ref_paths):
            bad[f] = "root %s is a ref root: %s" % (hit[0][0], "a build file or a test is not a reference" if is_test_file(f)
                                                   else "the file is not the path of a reference of any tests/oracle.json")
    errors += ["root %s: %s" % (ts[0], "; ".join(why)) for ts, why in roots if why]
    if read is not None:
        for f, why in cross_root_bare(files, admit, read).items():
            bad.setdefault(f, why)
    return bad, errors


def main():
    out = os.environ["GATE_OUT"]
    ls = subprocess.run(["git", "ls-files", "-z", "--", TREE], capture_output=True, check=True).stdout
    files = sorted(p for p in ls.decode("utf-8", "surrogateescape").split("\0") if p)
    with open(ADMIT) as f:
        admit = json.load(f)
    ref_paths = set()
    for f in files:
        if f.endswith("/tests/oracle.json"):
            try:
                with open(f) as fh:
                    for e in json.load(fh).get("tests", {}).values():
                        ref_paths.update(x["path"] for x in e.get("references", []) if isinstance(x, dict) and x.get("kind") in ("spec", "contract", "model") and isinstance(x.get("path"), str))
            except (OSError, ValueError, AttributeError):
                pass          # gate oracle judges a malformed declaration; here it names no reference
    def read(p):
        with open(p, encoding="utf-8") as fh:
            return fh.read()
    bad, errors = judge(files, admit, os.path.isfile, ref_paths, read, ".")
    red = bool(bad or errors)
    certified = 0 if errors else len(files) - len(bad)
    detail = "%d files, %d roots; %s" % (len(files), len(admit.get("roots", [])),
                                         "; ".join(sorted(errors))[:400] if errors else "every file under a gated root")
    res = {"format": "fnvnx-gate/1", "name": "admit", "status": "red" if red else "green", "universe": len(files),
           "certified": certified, "uncertified": sorted(bad)[:50], "detail": detail}
    with open(os.path.join(out, "admit.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
        f.write("\n")
    print("admit: %s %d/%d %s" % (res["status"], certified, len(files), detail))
    return 2 if red else 0


if __name__ == "__main__":
    sys.exit(main())
