"""What a certificate is bound to besides the shape, the Form and the IR: the obligations the prover checks, and the platform sources of the declared outputs (declared.json).
One module, no SMT solver: the prover (prove.py, shapeprove.py), the certificate builder (certs_build.py) and the gate (formgate.py) all import it, so none keeps a copy."""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIFT = os.path.dirname(HERE)
OBLIGATIONS = ("def0", "xstate")      # what a record certifies beyond the field equalities; certs_build refuses a record without it, formgate a row without it (stale-obligations)


def declared_table():
    with open(os.path.join(HERE, "declared.json")) as fh:
        return json.load(fh)


def platform_sha(fns, root=None):
    """sha256 over (relative path, content) of the declared.json source files of the functions `fns`: part of the certificate; the gate recomputes it from the tree it runs in"""
    root = root or LIFT
    rows = {u: r for r in declared_table()["rows"] for u in r["ufs"]}
    h = hashlib.sha256()
    for src in sorted({rows[f]["source"] for f in fns}):
        h.update(src.encode()); h.update(open(os.path.join(root, src), "rb").read())
    return h.hexdigest()


PROVER_ROOTS = ("proof/prove.py", "proof/model_sail.py", "proof/symform.py", "proof/certbasis.py")      # the modules that define what a proof compares; the prover identity is these and every module of this repository that they import, transitively, paths relative to lift/ (stdlib and pinned wheels stay outside).  A prover fix invalidates by rule, an unrelated edit does not.


def _module_file(m):
    """lift/-relative file of the repository module `m` (dotted), with the __init__.py of each package it passes through; [] when `m` is not of this repository"""
    p = m.split(".")
    out = []
    for i in range(1, len(p)):
        init = os.path.join(*p[:i], "__init__.py")
        if os.path.isfile(os.path.join(LIFT, init)): out.append(init)
    f = os.path.join(*p) + ".py"
    if os.path.isfile(os.path.join(LIFT, f)): return out + [f]
    return []


class DynamicLoad(Exception):
    """a closure file loads code other than by an import statement: the closure (the prover's identity) would not see it"""


def _imports(rel):
    """the repository files that the file `rel` imports (any depth: function-level imports count, relative imports resolve against the file's package as Python does), by its syntax tree.
    Raises DynamicLoad on any importlib / __import__ / exec use: only import statements form the closure."""
    import ast
    out = set()
    pkg = os.path.dirname(rel).replace(os.sep, ".")
    for n in ast.walk(ast.parse(open(os.path.join(LIFT, rel), "rb").read())):
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
            bad = [m for m in names if m.split(".")[0] == "importlib"]
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                up = pkg.split(".")
                up = up[:len(up) - (n.level - 1)]
                base = ".".join(up + ([n.module] if n.module else []))
            names = [base] + [base + "." + a.name for a in n.names]
            bad = [base] if base.split(".")[0] == "importlib" else []
        elif isinstance(n, ast.Name) and n.id in ("__import__", "exec"):
            raise DynamicLoad("%s:%d uses %s" % (rel, n.lineno, n.id))
        else:
            continue
        if bad: raise DynamicLoad("%s:%d imports %s" % (rel, n.lineno, bad[0]))
        for m in names: out.update(_module_file(m))
    return out


def prover_files():
    seen, todo = set(), list(PROVER_ROOTS)
    while todo:
        f = todo.pop()
        if f not in seen:
            seen.add(f); todo.extend(_imports(f))
    return sorted(seen)


# Data files of the closure's packages: everything that is not .py or documentation (*.md: under the repository convention no code reads them, so they are not program inputs), tests/ and caches left out.
# They decide verdicts (fnvlift/faults.json, proof/declared.json, the page tables ...), so the identity hashes them all.
# Left out, because they are already their own certificate component:
#   model_sha / closure_sha: the declared inputs of the recipe roots whose outputs those components are (COMPONENT_ROOTS, read from their recipes, the roots' own directories included);
#   x86sys_sha256: platform/declared/x86sys.json (outside these directories).
# A model input under proof/ that no component root declares stays hashed: it over-invalidates and is safe.
COMPONENT_ROOTS = ("proof/sailmodel", "proof/isla")      # lift/-relative


def component_inputs():
    """LIFT-relative paths of the declared repository inputs of COMPONENT_ROOTS (files and directories), read through recipe.load_recipe like formgate.ir_file does"""
    repo = os.path.normpath(os.path.join(LIFT, "..", "..", ".."))
    sys.path.insert(0, os.path.join(LIFT, "..", "verify"))
    try:
        import recipe
        roots = recipe.load_admit(repo)
        out = set()
        for r in COMPONENT_ROOTS:
            out.add(r)
            for st in recipe.load_recipe(repo, roots, recipe.TREE + "/lift/" + r).get("run", []):
                for i in st.get("inputs", []):
                    if isinstance(i, str) and i.startswith(recipe.TREE + "/lift/"): out.add(i[len(recipe.TREE + "/lift/"):])
        return out
    finally: sys.path.pop(0)


def prover_data_files():
    pk = sorted({os.path.dirname(f) for f in prover_files()})
    skip = component_inputs()
    out = []
    for d in pk:
        for root, dirs, files in os.walk(os.path.join(LIFT, d)):
            rel = os.path.relpath(root, LIFT)
            dirs[:] = sorted(x for x in dirs if x not in ("tests", "__pycache__") and os.path.join(rel, x) not in skip)
            for f in sorted(files):
                p = os.path.join(rel, f)
                if not f.endswith((".py", ".pyc", ".md")) and p not in skip: out.append(p)
    return out


def prover_sha():
    h = hashlib.sha256()
    for f in prover_files() + prover_data_files():
        h.update(f.encode() + b"\0" + open(os.path.join(LIFT, f), "rb").read() + b"\0")
    return h.hexdigest()
